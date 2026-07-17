"""Business logic for Agent drafts and Harness Profiles.

CAS revision control, draft validation (model/harness/version/security),
and profile reference checks. All security validation delegates to catalog.py
so the schema-layer invariants are enforced for every write path.
"""

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.agent_management.catalog import (
    Diagnostic,
    evaluate_version_constraint,
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
from app.models import LlmModelDefinition, LlmProviderConfig, LlmProviderModel
from app.runtime.models import RuntimeNode, RuntimeProfile, RuntimeType


class DraftConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(
            f"draft revision conflict; current revision is {current_revision}"
        )


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
    harness_version: str | None = None
    compatible: bool | None = None
    reason: str | None = None


class ValidationResult(BaseModel):
    validated_revision: int | None
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

# Sentinel for "field not supplied" in save_draft. Distinct from None, which
# means "explicitly clear the field to null". Callers that want to leave a
# field unchanged pass _UNSET (the default); callers that want to clear it
# pass None explicitly.
_UNSET = object()


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


def copy_agent(
    session: Session,
    source: AgentDefinition,
    *,
    slug: str,
    name: str,
    user_id: uuid.UUID,
) -> AgentDefinition:
    source_draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == source.id)
    ).one()
    copied = AgentDefinition(
        namespace_id=source.namespace_id,
        slug=slug,
        name=name,
        description=source.description,
        status=AgentStatus.ACTIVE,
        created_by=user_id,
    )
    copied_draft = AgentDraft(
        agent_id=copied.id,
        revision=1,
        harness_profile_id=None,
        provider_config_id=None,
        model_id=None,
        preferred_model_definition_id=source_draft.preferred_model_definition_id,
        execution_policy=deepcopy(source_draft.execution_policy),
        system_prompt=source_draft.system_prompt,
        config={},
        validated_revision=None,
        validation_result=None,
    )
    session.add(copied)
    session.add(copied_draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise _slug_conflict_or_reraise(exc) from exc
    return copied


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
    harness_profile_id: uuid.UUID | None | object = _UNSET,
    provider_config_id: uuid.UUID | None | object = _UNSET,
    model_id: str | None | object = _UNSET,
    preferred_model_definition_id: uuid.UUID | None | object = _UNSET,
    execution_policy: dict[str, Any] | None | object = _UNSET,
    system_prompt: str | None | object = _UNSET,
    config: dict[str, Any] | None | object = _UNSET,
) -> AgentDraft:
    """CAS-save a draft. Locks the row; bumps revision on success.

    Any field change invalidates validated_revision (set to None) because the
    old validation no longer applies to the new content.

    Each optional field defaults to _UNSET ("not supplied, leave unchanged").
    Passing ``None`` explicitly clears the field back to null, which is how the
    frontend clears optional fields (it sends the full intended state on every
    save). Callers must use ``_UNSET`` to omit a field.
    """
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    if draft is None:
        raise ValueError("draft not found")
    if draft.revision != expected_revision:
        raise DraftConflict(draft.revision)

    changed = False
    if (
        harness_profile_id is not _UNSET
        and harness_profile_id != draft.harness_profile_id
    ):
        draft.harness_profile_id = cast(uuid.UUID | None, harness_profile_id)
        changed = True
    if (
        provider_config_id is not _UNSET
        and provider_config_id != draft.provider_config_id
    ):
        draft.provider_config_id = cast(uuid.UUID | None, provider_config_id)
        changed = True
    if model_id is not _UNSET and model_id != draft.model_id:
        draft.model_id = cast(str | None, model_id)
        changed = True
    if (
        preferred_model_definition_id is not _UNSET
        and preferred_model_definition_id != draft.preferred_model_definition_id
    ):
        draft.preferred_model_definition_id = cast(
            uuid.UUID | None, preferred_model_definition_id
        )
        changed = True
    if execution_policy is not _UNSET and execution_policy != draft.execution_policy:
        draft.execution_policy = cast(dict[str, Any] | None, execution_policy) or {}
        changed = True
    if system_prompt is not _UNSET and system_prompt != draft.system_prompt:
        draft.system_prompt = cast(str | None, system_prompt) or ""
        changed = True
    if config is not _UNSET and config != draft.config:
        draft.config = cast(dict[str, Any] | None, config) or {}
        changed = True

    if changed:
        draft.revision += 1
        # Invalidate any prior validation — it was for older content.
        # Keep validation_result so draft_validation_status can report
        # "stale" (was validated, now edited) rather than "unvalidated".
        draft.validated_revision = None
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
    """True if any agent draft references this profile, including archived agents."""
    stmt = select(AgentDraft).where(AgentDraft.harness_profile_id == profile_id)
    return session.exec(stmt).first() is not None


def _validate_model(
    session: Session, namespace_id: uuid.UUID, draft: AgentDraft
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if draft.preferred_model_definition_id is not None:
        definition = session.get(
            LlmModelDefinition, draft.preferred_model_definition_id
        )
        if (
            definition is None
            or definition.namespace_id != namespace_id
            or not definition.enabled
        ):
            diags.append(
                Diagnostic(
                    code="preferred_model_invalid",
                    field="preferred_model_definition_id",
                    message="preferred model is missing, disabled, or cross-namespace",
                )
            )
        return diags
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


def build_target_compatibility(
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
        harness_version: str | None = None
        capability_inventory: dict[str, Any] = rt.harness_capabilities
        if rt.runtime_type == RuntimeType.NODE:
            node = session.exec(
                select(RuntimeNode).where(
                    RuntimeNode.runtime_profile_id == rt.id,
                    col(RuntimeNode.revoked_at).is_(None),
                )
            ).first()
            if node:
                capability_inventory = node.harness_capabilities

        claude_capability = capability_inventory.get("claude_code", {})
        if isinstance(claude_capability, dict):
            cli_version = claude_capability.get("cli_version")
            sdk_version = claude_capability.get("sdk_version")
            harness_version = claude_capability.get("harness_version")

        compatible: bool | None
        reason: str | None
        if cli_version is None or sdk_version is None or harness_version is None:
            compatible = None
            reason = "unknown"
        else:
            checks_ok = evaluate_version_constraint(
                cli_version, profile.cli_version_constraint
            ) and evaluate_version_constraint(
                sdk_version, profile.sdk_version_constraint
            )
            if checks_ok:
                compatible = True
                reason = None
            else:
                compatible = False
                reason = "version_constraint_violated"
        results.append(
            TargetCompatibility(
                runtime_profile_id=rt.id,
                runtime_type=rt.runtime_type.value,
                cli_version=cli_version,
                sdk_version=sdk_version,
                harness_version=harness_version,
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
    At least one compatible target is required; unknown/incompatible targets
    remain blocked individually without invalidating compatible targets.
    """
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    assert draft is not None  # created with agent

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    errors.extend(_validate_model(session, namespace_id, draft))
    profile: HarnessProfile | None = None
    if draft.preferred_model_definition_id is None:
        harness_errors, profile = _validate_harness(session, namespace_id, draft)
        errors.extend(harness_errors)

    # Config security validation (schema-layer invariants).
    if draft.preferred_model_definition_id is None:
        errors.extend(validate_config(draft.config, is_profile=False))
    else:
        allowed_policy_keys = {
            "permission_mode",
            "timeout_seconds",
            "required_capabilities",
            "tool_approval",
            "network_policy",
            "project_context_required",
            "delegation_required",
        }
        for key in draft.execution_policy:
            if key not in allowed_policy_keys:
                errors.append(
                    Diagnostic(
                        code="execution_policy_field_unknown",
                        field=f"execution_policy.{key}",
                        message="execution policy contains an unsupported field",
                    )
                )
        permission_mode = draft.execution_policy.get("permission_mode", "default")
        if permission_mode not in {"default", "acceptEdits", "plan", "dontAsk"}:
            errors.append(
                Diagnostic(
                    code="execution_permission_mode_invalid",
                    field="execution_policy.permission_mode",
                    message="execution permission mode is unsupported",
                )
            )
        timeout = draft.execution_policy.get("timeout_seconds", 3600)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            errors.append(
                Diagnostic(
                    code="execution_timeout_invalid",
                    field="execution_policy.timeout_seconds",
                    message="execution timeout must be a positive integer",
                )
            )
        for boolean_key in {
            "project_context_required",
            "delegation_required",
            "tool_approval",
        }:
            value = draft.execution_policy.get(boolean_key)
            if value is not None and not isinstance(value, bool):
                errors.append(
                    Diagnostic(
                        code="execution_policy_type_invalid",
                        field=f"execution_policy.{boolean_key}",
                        message="execution policy field must be boolean",
                    )
                )
        network_policy = draft.execution_policy.get("network_policy", "unrestricted")
        if network_policy != "unrestricted":
            errors.append(
                Diagnostic(
                    code="execution_network_policy_unsupported",
                    field="execution_policy.network_policy",
                    message="current Runtime adapters cannot prove restricted network execution",
                )
            )
        required = draft.execution_policy.get("required_capabilities", {})
        if not isinstance(required, dict) or any(
            not isinstance(key, str) or not isinstance(value, bool)
            for key, value in required.items()
        ):
            errors.append(
                Diagnostic(
                    code="required_capabilities_invalid",
                    field="execution_policy.required_capabilities",
                    message="required_capabilities must map capability names to booleans",
                )
            )

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

    # Capability dependencies are validated by the same deterministic Resolver
    # used to build an immutable Release. This prevents a draft from being
    # marked validated while Plugin/Skill/MCP/Tool expansion would fail later.
    if not errors:
        from app.agent_management.capabilities import (
            ClaudeCodeHarnessAdapter,
            ResolutionError,
            resolve_agent_spec,
        )

        try:
            resolved_spec, _dependency_lock, _components = resolve_agent_spec(
                session, agent
            )
            if resolved_spec.schema_version != "2.0":
                errors.extend(ClaudeCodeHarnessAdapter().validate_spec(resolved_spec))
        except ResolutionError as exc:
            errors.extend(exc.diagnostics)

    target_compat = build_target_compatibility(session, namespace_id, profile)
    # Draft validation succeeds when at least one target is compatible. Unknown
    # and incompatible targets remain explicitly unavailable for target-specific
    # publication/activation, but do not invalidate other compatible targets.
    if profile is not None and not any(tc.compatible is True for tc in target_compat):
        errors.append(
            Diagnostic(
                code="no_compatible_runtime_target",
                field="target_compatibility",
                message="no runtime target has reported compatible Claude CLI, SDK, and harness versions",
            )
        )

    status = "validated" if not errors else "error"

    draft.validated_revision = draft.revision if not errors else None
    draft.validation_result = {
        "status": status,
        "errors": [e.model_dump() for e in errors],
        "warnings": [w.model_dump() for w in warnings],
        "target_compatibility": [
            item.model_dump(mode="json") for item in target_compat
        ],
    }
    draft.updated_at = datetime.now(timezone.utc)
    session.add(draft)
    session.flush()

    return ValidationResult(
        validated_revision=draft.validated_revision,
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
        return (
            ValidationStatus.STALE
            if draft.validation_result.get("status") == "validated"
            else ValidationStatus.ERROR
        )
    if draft.validated_revision != draft.revision:
        return ValidationStatus.STALE
    status = draft.validation_result.get("status", ValidationStatus.UNVALIDATED)
    return status if isinstance(status, str) else ValidationStatus.UNVALIDATED
