"""Deterministic v0.9 Runtime, capability, and model catalog helpers."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, col, select

from app.models import LlmModelDefinition, LlmProviderConfig, LlmProviderModel
from app.runtime.models import (
    ModelSelectionMode,
    ModelSelectionSource,
    RuntimeCapabilityReport,
    RuntimeConfigurationRevision,
    RuntimeInstance,
    RuntimeInstanceStatus,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
)


class RuntimeCatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def configuration_payload(row: RuntimeConfigurationRevision) -> dict[str, Any]:
    return {
        "executable": row.executable,
        "arguments": row.arguments,
        "working_directory_policy": row.working_directory_policy,
        "environment_allowlist": row.environment_allowlist,
        "security_policy": row.security_policy,
        "resource_limits": row.resource_limits,
    }


def model_catalog_fingerprint(session: Session, runtime_id: uuid.UUID) -> str:
    rows = session.exec(
        select(RuntimeModelBinding)
        .where(
            RuntimeModelBinding.runtime_instance_id == runtime_id,
            RuntimeModelBinding.status == RuntimeModelBindingStatus.AVAILABLE,
        )
        .order_by(col(RuntimeModelBinding.id))
    ).all()
    return canonical_digest(
        [
            {
                "binding_id": str(row.id),
                "model_definition_id": str(row.model_definition_id),
                "route_type": row.route_type.value,
                "route_key": row.route_key,
                "engine_model_id": row.engine_model_id,
                "validation_fingerprint": row.validation_fingerprint,
            }
            for row in rows
        ]
    )


def available_model_bindings(
    session: Session, runtime_id: uuid.UUID
) -> list[RuntimeModelBinding]:
    return list(
        session.exec(
            select(RuntimeModelBinding)
            .where(
                RuntimeModelBinding.runtime_instance_id == runtime_id,
                RuntimeModelBinding.status == RuntimeModelBindingStatus.AVAILABLE,
            )
            .order_by(col(RuntimeModelBinding.engine_model_id))
        ).all()
    )


def validate_runtime_ready(_session: Session, runtime: RuntimeInstance) -> None:
    if not runtime.enabled or runtime.status != RuntimeInstanceStatus.AVAILABLE:
        raise RuntimeCatalogError(
            "runtime_unavailable", "Runtime is not enabled and available"
        )
    if runtime.applied_configuration_revision_id is None:
        raise RuntimeCatalogError(
            "runtime_configuration_not_applied",
            "Runtime has no applied configuration revision",
        )
    if runtime.current_capability_report_id is None:
        raise RuntimeCatalogError(
            "runtime_capability_missing", "Runtime has no current capability report"
        )


def resolve_model_binding(
    session: Session,
    *,
    runtime: RuntimeInstance,
    mode: ModelSelectionMode,
    exact_binding_id: uuid.UUID | None,
    preferred_model_definition_id: uuid.UUID | None,
) -> tuple[RuntimeModelBinding, ModelSelectionSource]:
    validate_runtime_ready(session, runtime)
    if mode == ModelSelectionMode.EXACT:
        if exact_binding_id is None:
            raise RuntimeCatalogError(
                "model_binding_required", "exact mode requires runtime_model_binding_id"
            )
        binding = session.get(RuntimeModelBinding, exact_binding_id)
        if (
            binding is None
            or binding.namespace_id != runtime.namespace_id
            or binding.runtime_instance_id != runtime.id
        ):
            raise RuntimeCatalogError(
                "model_binding_wrong_runtime",
                "Model binding does not belong to the selected Runtime",
            )
        if binding.status != RuntimeModelBindingStatus.AVAILABLE:
            raise RuntimeCatalogError(
                "model_binding_unavailable", "Model binding is not available"
            )
        source = (
            ModelSelectionSource.EXACT
            if preferred_model_definition_id in {None, binding.model_definition_id}
            else ModelSelectionSource.EXPLICIT_OVERRIDE
        )
        return binding, source

    if exact_binding_id is not None:
        raise RuntimeCatalogError(
            "model_binding_not_allowed",
            "agent_preference mode does not accept runtime_model_binding_id",
        )
    if preferred_model_definition_id is None:
        raise RuntimeCatalogError(
            "agent_preference_missing", "Agent Release has no model preference"
        )
    rows = session.exec(
        select(RuntimeModelBinding).where(
            RuntimeModelBinding.runtime_instance_id == runtime.id,
            RuntimeModelBinding.model_definition_id == preferred_model_definition_id,
            RuntimeModelBinding.status == RuntimeModelBindingStatus.AVAILABLE,
        )
    ).all()
    if not rows:
        raise RuntimeCatalogError(
            "agent_preference_unavailable",
            "Agent preferred model is unavailable on the selected Runtime",
        )
    if len(rows) > 1:
        raise RuntimeCatalogError(
            "model_binding_ambiguous",
            "Agent preferred model has multiple available routes; choose exact mode",
        )
    return rows[0], ModelSelectionSource.AGENT_PREFERENCE


def validate_model_binding_route(
    session: Session, binding: RuntimeModelBinding
) -> None:
    model_definition = session.get(LlmModelDefinition, binding.model_definition_id)
    if (
        model_definition is None
        or model_definition.namespace_id != binding.namespace_id
        or not model_definition.enabled
    ):
        raise RuntimeCatalogError(
            "model_definition_unavailable", "Stable model identity is unavailable"
        )
    if binding.provider_config_id is None:
        if binding.provider_model_id is not None:
            raise RuntimeCatalogError(
                "provider_route_incomplete",
                "Provider model cannot be set without Provider Config",
            )
        return
    provider = session.get(LlmProviderConfig, binding.provider_config_id)
    provider_model = session.get(LlmProviderModel, binding.provider_model_id)
    if (
        provider is None
        or provider.namespace_id != binding.namespace_id
        or not provider.enabled
    ):
        raise RuntimeCatalogError(
            "provider_config_unavailable", "Provider Config is unavailable"
        )
    if (
        provider_model is None
        or provider_model.provider_config_id != provider.id
        or provider_model.model_definition_id != binding.model_definition_id
        or not provider_model.is_enabled
    ):
        raise RuntimeCatalogError(
            "provider_model_unavailable",
            "Provider model is not enabled or does not match the stable identity",
        )


def current_runtime_evidence(
    session: Session, runtime: RuntimeInstance
) -> tuple[RuntimeConfigurationRevision, RuntimeCapabilityReport, str]:
    validate_runtime_ready(session, runtime)
    configuration = session.get(
        RuntimeConfigurationRevision, runtime.applied_configuration_revision_id
    )
    capability = session.get(
        RuntimeCapabilityReport, runtime.current_capability_report_id
    )
    if configuration is None or configuration.runtime_instance_id != runtime.id:
        raise RuntimeCatalogError(
            "runtime_configuration_missing", "Applied Runtime configuration is missing"
        )
    if capability is None or capability.runtime_instance_id != runtime.id:
        raise RuntimeCatalogError(
            "runtime_capability_missing", "Current Runtime capability report is missing"
        )
    return configuration, capability, model_catalog_fingerprint(session, runtime.id)
