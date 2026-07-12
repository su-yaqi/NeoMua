import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, select

from app import crud
from app.agent_management.catalog import (
    environment_catalog,
    validate_config,
    validate_harness_type,
    validate_version_constraint,
    HARNESS_CATALOG,
)
from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    AgentStatus,
    HarnessProfile,
)
from app.agent_management.schemas import (
    AgentCreate,
    AgentDraftPublic,
    AgentListItem,
    AgentPublic,
    AgentsPublic,
    AgentUpdate,
    DraftSave,
    EnvironmentCatalogPublic,
    HarnessCatalogPublic,
    HarnessProfileCreate,
    HarnessProfilePublic,
    HarnessProfilesPublic,
    HarnessProfileUpdate,
)
from app.agent_management.service import (
    DraftConflict,
    create_agent,
    draft_validation_status,
    is_profile_referenced,
    save_draft,
    validate_draft,
)
from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.models import NamespaceRole

router = APIRouter(tags=["agent-management"])


def _require_admin(
    session: SessionDep, current_user: CurrentUser, namespace_id: uuid.UUID
) -> None:
    if current_user.is_superuser:
        return
    role = crud.get_namespace_role(
        session=session, user_id=current_user.id, namespace_id=namespace_id
    )
    if role != NamespaceRole.ADMIN:
        raise HTTPException(403, "Namespace admin privilege required")


def _get_agent(
    session: SessionDep, agent_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentDefinition:
    agent = session.get(AgentDefinition, agent_id)
    if agent is None or agent.namespace_id != namespace_id:
        raise HTTPException(404, "Agent not found")
    return agent


def _get_draft(session: SessionDep, agent: AgentDefinition) -> AgentDraft:
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    ).first()
    if draft is None:
        raise HTTPException(404, "Draft not found")
    return draft


def _draft_public(draft: AgentDraft) -> AgentDraftPublic:
    return AgentDraftPublic(
        agent_id=draft.agent_id,
        revision=draft.revision,
        harness_profile_id=draft.harness_profile_id,
        provider_config_id=draft.provider_config_id,
        model_id=draft.model_id,
        system_prompt=draft.system_prompt,
        config=draft.config,
        validated_revision=draft.validated_revision,
        validation_result=draft.validation_result,
        validation_status=draft_validation_status(draft),
        updated_at=draft.updated_at,
    )


def _agent_public(agent: AgentDefinition) -> AgentPublic:
    return AgentPublic(
        id=agent.id,
        namespace_id=agent.namespace_id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        status=agent.status.value,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _agent_list_item(
    session: SessionDep, agent: AgentDefinition, draft: AgentDraft
) -> AgentListItem:
    harness_type: str | None = None
    if draft.harness_profile_id:
        profile = session.get(HarnessProfile, draft.harness_profile_id)
        if profile and profile.namespace_id == agent.namespace_id:
            harness_type = profile.harness_type
    return AgentListItem(
        id=agent.id,
        namespace_id=agent.namespace_id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        status=agent.status.value,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        draft_revision=draft.revision,
        validation_status=draft_validation_status(draft),
        harness_type=harness_type,
        model_id=draft.model_id,
    )


def _profile_public(
    session: SessionDep, profile: HarnessProfile
) -> HarnessProfilePublic:
    return HarnessProfilePublic(
        id=profile.id,
        namespace_id=profile.namespace_id,
        name=profile.name,
        harness_type=profile.harness_type,
        config_schema_version=profile.config_schema_version,
        cli_version_constraint=profile.cli_version_constraint,
        sdk_version_constraint=profile.sdk_version_constraint,
        config=profile.config,
        archived=profile.archived,
        referenced_by_agents=is_profile_referenced(session, profile.id),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# --- Agents ---


@router.get("/agents", response_model=AgentsPublic)
def list_agents(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> AgentsPublic:
    agents = session.exec(
        select(AgentDefinition)
        .where(AgentDefinition.namespace_id == namespace_id)
        .order_by(col(AgentDefinition.created_at).desc())
    ).all()
    items: list[AgentListItem] = []
    for agent in agents:
        draft = session.exec(
            select(AgentDraft).where(AgentDraft.agent_id == agent.id)
        ).first()
        if draft is None:
            continue
        items.append(_agent_list_item(session, agent, draft))
    return AgentsPublic(data=items, count=len(items))


@router.post("/agents", response_model=AgentPublic, status_code=201)
def create_agent_endpoint(
    body: AgentCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> AgentPublic:
    try:
        agent = create_agent(
            session,
            namespace_id,
            slug=body.slug,
            name=body.name,
            description=body.description,
            user_id=current_user.id,
        )
        session.commit()
        session.refresh(agent)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _agent_public(agent)


@router.get("/agents/{agent_id}", response_model=AgentPublic)
def read_agent(
    agent_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> AgentPublic:
    return _agent_public(_get_agent(session, agent_id, namespace_id))


@router.patch("/agents/{agent_id}", response_model=AgentPublic)
def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> AgentPublic:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    if agent.status == AgentStatus.ARCHIVED:
        raise HTTPException(409, "Archived agent cannot be modified")
    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.status is not None:
        if body.status not in {AgentStatus.ACTIVE.value, AgentStatus.ARCHIVED.value}:
            raise HTTPException(422, "status must be 'active' or 'archived'")
        agent.status = AgentStatus(body.status)
    from datetime import datetime, timezone

    agent.updated_at = datetime.now(timezone.utc)
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return _agent_public(agent)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(
    agent_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    # v0.5 stage 1: no releases yet, but enforce the rule preemptively.
    # (When releases exist in stage 6, check release reference here -> 409.)
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    ).first()
    if draft is not None:
        session.delete(draft)
    session.delete(agent)
    session.commit()


@router.get("/agents/{agent_id}/draft", response_model=AgentDraftPublic)
def read_draft(
    agent_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> AgentDraftPublic:
    agent = _get_agent(session, agent_id, namespace_id)
    return _draft_public(_get_draft(session, agent))


@router.put("/agents/{agent_id}/draft", response_model=AgentDraftPublic)
def save_draft_endpoint(
    agent_id: uuid.UUID,
    body: DraftSave,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> AgentDraftPublic:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    if agent.status == AgentStatus.ARCHIVED:
        raise HTTPException(409, "Archived agent cannot be edited")
    # Pre-validate config security before saving.
    if body.config is not None:
        diags = validate_config(body.config)
        if diags:
            raise HTTPException(422, {"errors": [d.model_dump() for d in diags]})
    try:
        draft = save_draft(
            session,
            agent,
            expected_revision=body.expected_revision,
            harness_profile_id=body.harness_profile_id,
            provider_config_id=body.provider_config_id,
            model_id=body.model_id,
            system_prompt=body.system_prompt,
            config=body.config,
        )
        session.commit()
        session.refresh(draft)
    except DraftConflict as exc:
        session.rollback()
        raise HTTPException(
            409,
            {
                "code": "draft_revision_conflict",
                "current_revision": exc.current_revision,
            },
        ) from exc
    return _draft_public(draft)


@router.post("/agents/{agent_id}/draft/validate")
def validate_draft_endpoint(
    agent_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    if agent.status == AgentStatus.ARCHIVED:
        raise HTTPException(409, "Archived agent cannot be validated")
    result = validate_draft(session, agent, namespace_id)
    session.commit()
    return result.model_dump(mode="json")


# --- Harness Profiles ---


@router.get("/harness-profiles", response_model=HarnessProfilesPublic)
def list_profiles(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> HarnessProfilesPublic:
    profiles = session.exec(
        select(HarnessProfile)
        .where(HarnessProfile.namespace_id == namespace_id)
        .order_by(col(HarnessProfile.created_at).desc())
    ).all()
    data = [_profile_public(session, p) for p in profiles]
    return HarnessProfilesPublic(data=data, count=len(data))


@router.post("/harness-profiles", response_model=HarnessProfilePublic, status_code=201)
def create_profile(
    body: HarnessProfileCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> HarnessProfilePublic:
    _require_admin(session, current_user, namespace_id)
    # Schema-layer security validation before persisting.
    errors: list = []
    errors.extend(validate_harness_type(body.harness_type))
    errors.extend(validate_config(body.config, is_profile=True))
    errors.extend(validate_version_constraint(body.cli_version_constraint))
    errors.extend(validate_version_constraint(body.sdk_version_constraint))
    if errors:
        raise HTTPException(422, {"errors": [e.model_dump() for e in errors]})
    profile = HarnessProfile(
        namespace_id=namespace_id,
        name=body.name,
        harness_type=body.harness_type,
        config_schema_version=body.config_schema_version,
        cli_version_constraint=body.cli_version_constraint,
        sdk_version_constraint=body.sdk_version_constraint,
        config=body.config,
        archived=False,
        created_by=current_user.id,
    )
    session.add(profile)
    try:
        session.commit()
        session.refresh(profile)
    except Exception as exc:  # unique constraint etc.
        session.rollback()
        msg = str(exc).lower()
        if "uq_harness_profile_namespace_name" in msg or "name" in msg:
            raise HTTPException(409, "Profile name already exists in this namespace") from exc
        raise
    return _profile_public(session, profile)


@router.get("/harness-profiles/{profile_id}", response_model=HarnessProfilePublic)
def read_profile(
    profile_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> HarnessProfilePublic:
    profile = session.get(HarnessProfile, profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        raise HTTPException(404, "Harness profile not found")
    return _profile_public(session, profile)


@router.patch("/harness-profiles/{profile_id}", response_model=HarnessProfilePublic)
def update_profile(
    profile_id: uuid.UUID,
    body: HarnessProfileUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> HarnessProfilePublic:
    profile = session.get(HarnessProfile, profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        raise HTTPException(404, "Harness profile not found")
    _require_admin(session, current_user, namespace_id)
    # Validate new config/values before saving.
    candidate_config = body.config if body.config is not None else profile.config
    errors: list = []
    errors.extend(validate_config(candidate_config, is_profile=True))
    if body.cli_version_constraint is not None:
        errors.extend(validate_version_constraint(body.cli_version_constraint))
    if body.sdk_version_constraint is not None:
        errors.extend(validate_version_constraint(body.sdk_version_constraint))
    if errors:
        raise HTTPException(422, {"errors": [e.model_dump() for e in errors]})
    if body.name is not None:
        profile.name = body.name
    if body.cli_version_constraint is not None:
        profile.cli_version_constraint = body.cli_version_constraint
    if body.sdk_version_constraint is not None:
        profile.sdk_version_constraint = body.sdk_version_constraint
    if body.config is not None:
        profile.config = body.config
    if body.archived is not None:
        profile.archived = body.archived
    from datetime import datetime, timezone

    profile.updated_at = datetime.now(timezone.utc)
    session.add(profile)
    try:
        session.commit()
        session.refresh(profile)
    except Exception as exc:
        session.rollback()
        raise HTTPException(409, "Profile name already exists in this namespace") from exc
    return _profile_public(session, profile)


@router.delete("/harness-profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    profile = session.get(HarnessProfile, profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        raise HTTPException(404, "Harness profile not found")
    _require_admin(session, current_user, namespace_id)
    if is_profile_referenced(session, profile.id):
        raise HTTPException(409, "Profile is referenced by agents; archive it instead")
    session.delete(profile)
    session.commit()


# --- Catalogs ---


@router.get("/harnesses/catalog", response_model=HarnessCatalogPublic)
def harness_catalog(
    _: CurrentUser,
    __: uuid.UUID = Depends(require_namespace_runtime_user),
) -> HarnessCatalogPublic:
    return HarnessCatalogPublic(harnesses=HARNESS_CATALOG)


@router.get("/harnesses/environment-catalog", response_model=EnvironmentCatalogPublic)
def env_catalog(
    _: CurrentUser,
    __: uuid.UUID = Depends(require_namespace_runtime_user),
) -> EnvironmentCatalogPublic:
    cat = environment_catalog()
    return EnvironmentCatalogPublic(**cat)
