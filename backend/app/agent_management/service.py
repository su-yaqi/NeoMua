"""Business logic for Agent drafts and Harness Profiles.

CAS revision control, draft validation (model/harness/version/security),
and profile reference checks. All security validation delegates to catalog.py
so the schema-layer invariants are enforced for every write path.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.agent_management.catalog import (
    Diagnostic,
    environment_catalog,
    validate_config,
    validate_harness_type,
    validate_version_constraint,
)
from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    AgentStatus,
    HarnessProfile,
)
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.models import RuntimeNode, RuntimeProfile, RuntimeType


class DraftConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"draft revision conflict; current revision is {current_revision}")


class ValidationStatus(str):
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    STALE = "stale"
    ERROR = "error"


class TargetCompatibility(BaseModel):
    runtime_profile_id: uuid.UUID
    runtime_type: str
    cli_version: str | None = None
    sdk_version: str | None = None
    compatible: bool | None = None
    reason: str | None = None


class ValidationResult(BaseModel):
    validated_revision: int
    status: str
    errors: list[Diagnostic]
    warnings: list[Diagnostic]
    target_compatibility: list[TargetCompatibility]


# Patterns that hint at secret leakage in system prompts (advisory only).
_SECRET_HINT_PATTERNS = (
    "sk-ant-",
    "AKIA",
    "-----BEGIN",
)


def create_agent(
    session: Session,
    namespace_id: uuid.UUID,
    *,
    slug: str,
    name: str,
    description: str | None,
    user_id: uuid.UUID,
) -> AgentDefinition:
    agent = AgentDefinition(
        namespace_id=namespace_id,
        slug=slug,
        name=name,
        description=description,
        status=AgentStatus.ACTIVE,
        created_by=user_id,
    )
    draft = AgentDraft(agent_id=agent.id, revision=1)
    session.add(agent)
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise _slug_conflict_or_reraise(exc) from exc
    return agent


def _slug_conflict_or_reraise(exc: IntegrityError) -> Exception:
    msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
    if "uq_agent_definition_namespace_slug" in msg or "slug" in msg:
        return ValueError("slug already exists in this namespace")
    return exc


def save_draft(
    session: Session,
    agent: AgentDefinition,
    *,
    expected_revision: int,
    harness_profile_id: uuid.UUID | None = None,
    provider_config_id: uuid.UUID | None = None,
    model_id: str | None = None,
    system_prompt: str | None = None,
    config: dict | None = None,
) -> AgentDraft:
    """CAS-save a draft. Locks the row; bumps revision on success.

    Any field change invalidates validated_revision (set to None) because the
    old validation no longer applies to the new content.
    """
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    if draft is None:
        raise ValueError("draft not found")
    if draft.revision != expected_revision:
        raise DraftConflict(draft.revision)

    changed = False
    if harness_profile_id is not None and harness_profile_id != draft.harness_profile_id:
        draft.harness_profile_id = harness_profile_id
        changed = True
    if provider_config_id is not None and provider_config_id != draft.provider_config_id:
        draft.provider_config_id = provider_config_id
        changed = True
    if model_id is not None and model_id != draft.model_id:
        draft.model_id = model_id
        changed = True
    if system_prompt is not None and system_prompt != draft.system_prompt:
        draft.system_prompt = system_prompt
        changed = True
    if config is not None and config != draft.config:
        draft.config = config
        changed = True

    if changed:
        draft.revision += 1
        # Invalidate any prior validation — it was for older content.
        draft.validated_revision = None
        draft.validation_result = None
        draft.updated_at = datetime.now(timezone.utc)
    session.add(draft)
    session.flush()
    return draft


def archive_agent(session: Session, agent: AgentDefinition) -> AgentDefinition:
    agent.status = AgentStatus.ARCHIVED
    agent.updated_at = datetime.now(timezone.utc)
    session.add(agent)
    session.flush()
    return agent


def is_profile_referenced(session: Session, profile_id: uuid.UUID) -> bool:
    """True if any non-archived agent draft references this profile."""
    stmt = (
        select(AgentDraft)
        .join(AgentDefinition, AgentDraft.agent_id == AgentDefinition.id)
        .where(
            AgentDraft.harness_profile_id == profile_id,
            AgentDefinition.status == AgentStatus.ACTIVE,
        )
    )
    return session.exec(stmt).first() is not None


def _validate_model(
    session: Session, namespace_id: uuid.UUID, draft: AgentDraft
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if draft.provider_config_id is None or not draft.model_id:
        diags.append(
            Diagnostic(
                code="model_not_selected",
                field="model_id",
                message="model and provider config are required",
            )
        )
        return diags
    config = session.get(LlmProviderConfig, draft.provider_config_id)
    if config is None or config.namespace_id != namespace_id:
        diags.append(
            Diagnostic(
                code="cross_namespace_model",
                field="provider_config_id",
                message="provider config does not belong to this namespace",
            )
        )
        return diags
    if not config.enabled:
        diags.append(
            Diagnostic(
                code="model_stale",
                field="provider_config_id",
                message="provider config is disabled",
            )
        )
    model = session.exec(
        select(LlmProviderModel).where(
            LlmProviderModel.provider_config_id == config.id,
            LlmProviderModel.model_id == draft.model_id,
        )
    ).first()
    if model is None:
        diags.append(
            Diagnostic(
                code="model_not_found",
                field="model_id",
                message=f"model '{draft.model_id}' not found in this provider config",
            )
        )
    elif not model.is_enabled:
        diags.append(
            Diagnostic(
                code="model_stale",
                field="model_id",
                message=f"model '{draft.model_id}' is disabled",
            )
        )
    return diags


def _validate_harness(
    session: Session, namespace_id: uuid.UUID, draft: AgentDraft
) -> tuple[list[Diagnostic], HarnessProfile | None]:
    diags: list[Diagnostic] = []
    if draft.harness_profile_id is None:
        diags.append(
            Diagnostic(
                code="harness_not_selected",
                field="harness_profile_id",
                message="harness profile is required",
            )
        )
        return diags, None
    profile = session.get(HarnessProfile, draft.harness_profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        diags.append(
            Diagnostic(
                code="cross_namespace_harness",
                field="harness_profile_id",
                message="harness profile does not belong to this namespace",
            )
        )
        return diags, None
    if profile.archived:
        diags.append(
            Diagnostic(
                code="harness_archived",
                field="harness_profile_id",
                message="harness profile is archived",
            )
        )
    diags.extend(validate_harness_type(profile.harness_type))
    return diags, profile


def _build_target_compatibility(
    session: Session, namespace_id: uuid.UUID, profile: HarnessProfile | None
) -> list[TargetCompatibility]:
    if profile is None:
        return []
    runtimes = session.exec(
        select(RuntimeProfile).where(RuntimeProfile.namespace_id == namespace_id)
    ).all()
    results: list[TargetCompatibility] = []
    for rt in runtimes:
        cli_version: str | None = None
        sdk_version: str | None = None
        if rt.runtime_type == RuntimeType.NODE:
            node = session.exec(
                select(RuntimeNode).where(
                    RuntimeNode.runtime_profile_id == rt.id,
                    RuntimeNode.revoked_at.is_(None),  # type: ignore[attr-defined]
                )
            ).first()
            if node:
                cli_version = node.agent_version
                sdk_version = node.sdk_version
        else:
            # Platform runtime: use runtime-worker reported versions from config.
            cli_version = rt.config.get("reported_cli_version")
            sdk_version = rt.config.get("reported_sdk_version")

        compatible: bool | None
        reason: str | None
        if cli_version is None and sdk_version is None:
            compatible = None
            reason = "unknown"
        else:
            compatible = True
            reason = None
        results.append(
            TargetCompatibility(
                runtime_profile_id=rt.id,
                runtime_type=rt.runtime_type.value,
                cli_version=cli_version,
                sdk_version=sdk_version,
                compatible=compatible,
                reason=reason,
            )
        )
    return results


def validate_draft(
    session: Session, agent: AgentDefinition, namespace_id: uuid.UUID
) -> ValidationResult:
    """Run full draft validation. Persists validated_revision + result on success.

    Returns the ValidationResult. Errors block a future publish; warnings do not.
    Target compatibility 'unknown' is treated as an error (blocks publish).
    """
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    assert draft is not None  # created with agent

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    errors.extend(_validate_model(session, namespace_id, draft))
    harness_errors, profile = _validate_harness(session, namespace_id, draft)
    errors.extend(harness_errors)

    # Config security validation (schema-layer invariants).
    errors.extend(validate_config(draft.config))

    if profile is not None:
        errors.extend(validate_config(profile.config, is_profile=True))
        vc_errors = validate_version_constraint(profile.cli_version_constraint)
        errors.extend(
            Diagnostic(
                code=d.code,
                field=f"cli_version_constraint.{d.field}",
                message=d.message,
            )
            for d in vc_errors
        )
        vc_errors = validate_version_constraint(profile.sdk_version_constraint)
        errors.extend(
            Diagnostic(
                code=d.code,
                field=f"sdk_version_constraint.{d.field}",
                message=d.message,
            )
            for d in vc_errors
        )

    # system_prompt checks
    if not draft.system_prompt.strip():
        errors.append(
            Diagnostic(
                code="system_prompt_empty",
                field="system_prompt",
                message="system prompt is required",
            )
        )
    for hint in _SECRET_HINT_PATTERNS:
        if hint in draft.system_prompt:
            warnings.append(
                Diagnostic(
                    code="potential_secret_in_prompt",
                    field="system_prompt",
                    message="system prompt may contain a secret; please verify",
                )
            )
            break

    target_compat = _build_target_compatibility(session, namespace_id, profile)
    # 'unknown' targets block publish.
    for tc in target_compat:
        if tc.compatible is None:
            errors.append(
                Diagnostic(
                    code="target_version_unknown",
                    field="target_compatibility",
                    message=f"runtime {tc.runtime_profile_id} has not reported "
                    "CLI/SDK versions; compatibility is unknown",
                )
            )

    status = "validated" if not errors else "error"

    draft.validated_revision = draft.revision if not errors else None
    draft.validation_result = {
        "status": status,
        "errors": [e.model_dump() for e in errors],
        "warnings": [w.model_dump() for w in warnings],
    }
    draft.updated_at = datetime.now(timezone.utc)
    session.add(draft)
    session.flush()

    return ValidationResult(
        validated_revision=draft.revision if not errors else draft.revision,
        status=status,
        errors=errors,
        warnings=warnings,
        target_compatibility=target_compat,
    )


def draft_validation_status(draft: AgentDraft) -> str:
    """Derive a display status from a draft's persisted validation state."""
    if draft.validation_result is None:
        return ValidationStatus.UNVALIDATED
    if draft.validated_revision is None:
        return ValidationStatus.STALE if draft.validation_result.get("status") == "validated" else ValidationStatus.ERROR
    if draft.validated_revision != draft.revision:
        return ValidationStatus.STALE
    return draft.validation_result.get("status", ValidationStatus.UNVALIDATED)
