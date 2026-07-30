"""v0.9 Runtime instance, configuration, capability, and model catalog APIs."""

import base64
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.models import (
    LlmModelDefinition,
    LlmProviderConfig,
    ProviderValidationStatus,
)
from app.runtime.catalog import (
    RuntimeCatalogError,
    canonical_digest,
    configuration_payload,
    current_runtime_evidence,
    model_binding_is_current,
    model_catalog_fingerprint,
    validate_model_binding_route,
)
from app.runtime.connections import node_is_online
from app.runtime.models import (
    BootstrapStatus,
    NodeBootstrapAttempt,
    NodeBootstrapSession,
    NodeInstallationReceipt,
    RuntimeAdapterRelease,
    RuntimeCapabilityReport,
    RuntimeConfigurationOrigin,
    RuntimeConfigurationRevision,
    RuntimeConfigurationStatus,
    RuntimeControlAction,
    RuntimeControlDecision,
    RuntimeDiscoveryObservation,
    RuntimeEngineType,
    RuntimeInstance,
    RuntimeInstanceStatus,
    RuntimeLocationType,
    RuntimeManagementType,
    RuntimeModelBinding,
    RuntimeModelBindingOrigin,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
    RuntimeModelValidationAttempt,
    RuntimeNode,
    RuntimeNodeMode,
)
from app.runtime.platform_builtin import (
    enqueue_platform_reconcile_job,
    ensure_builtin_platform_runtime,
    reconcile_platform_model_bindings,
)
from app.runtime.provider_validation import validate_provider_model_call
from app.runtime.security import require_internal_runtime

router = APIRouter(tags=["runtime-instances"])
internal_router = APIRouter(
    prefix="/internal/runtime",
    tags=["internal-runtime-instances"],
    dependencies=[Depends(require_internal_runtime)],
)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfigurationInput(StrictBody):
    expected_revision: int = Field(ge=0)
    executable: str = Field(min_length=1, max_length=1024)
    arguments: list[str] = []
    working_directory_policy: str = Field(default="workspace", max_length=64)
    environment_allowlist: list[str] = []
    security_policy: dict[str, Any] = {}
    resource_limits: dict[str, Any] = {}

    @model_validator(mode="after")
    def validate_command(self) -> "RuntimeConfigurationInput":
        if any("\x00" in item for item in [self.executable, *self.arguments]):
            raise ValueError("Runtime command cannot contain NUL")
        if self.working_directory_policy not in {"workspace", "project"}:
            raise ValueError("Unsupported working_directory_policy")
        if self.arguments:
            raise ValueError("adapter_contract_unsupported: configured base arguments")
        if len(set(self.environment_allowlist)) != len(self.environment_allowlist):
            raise ValueError("environment_allowlist contains duplicates")
        if any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
            for name in self.environment_allowlist
        ):
            raise ValueError("environment_allowlist contains an invalid name")
        allowed_security = {
            "allowed_working_roots",
            "network_policy",
            "permission_mode",
            "permission_modes",
        }
        unknown_security = set(self.security_policy) - allowed_security
        if unknown_security:
            raise ValueError(
                f"Unsupported Runtime security policy: {sorted(unknown_security)}"
            )
        if self.security_policy.get("network_policy", "unrestricted") != "unrestricted":
            raise ValueError("adapter_contract_unsupported: restricted network policy")
        modes = self.security_policy.get("permission_modes")
        if modes is None and self.security_policy.get("permission_mode") is not None:
            modes = [self.security_policy["permission_mode"]]
        if modes is not None and (
            not isinstance(modes, list)
            or not modes
            or any(
                mode not in {"default", "acceptEdits", "plan", "dontAsk"}
                for mode in modes
            )
        ):
            raise ValueError("security_policy.permission_modes is invalid")
        roots = self.security_policy.get("allowed_working_roots", [])
        if not isinstance(roots, list) or any(
            not isinstance(root, str)
            or not (
                (
                    PurePosixPath(root).is_absolute()
                    and ".." not in PurePosixPath(root).parts
                )
                or (
                    PureWindowsPath(root).is_absolute()
                    and ".." not in PureWindowsPath(root).parts
                )
            )
            for root in roots
        ):
            raise ValueError(
                "security_policy.allowed_working_roots must contain normalized absolute paths"
            )
        allowed_limits = {"max_timeout_seconds"}
        unknown_limits = set(self.resource_limits) - allowed_limits
        if unknown_limits:
            raise ValueError(
                f"adapter_contract_unsupported: resource limits {sorted(unknown_limits)}"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in self.resource_limits.values()
        ):
            raise ValueError("Runtime resource limits must be positive integers")
        return self


class PlatformRuntimeCreate(StrictBody):
    name: str = Field(min_length=1, max_length=255)
    engine_type: RuntimeEngineType
    installation_key: str = Field(min_length=1, max_length=255)
    engine_version: str | None = Field(default=None, max_length=64)
    adapter_version: str = Field(default="1.0.0", min_length=1, max_length=64)
    configuration: RuntimeConfigurationInput


class ModelDefinitionCreate(StrictBody):
    provider_family: str = Field(min_length=1, max_length=128)
    model_key: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    capability_tags: list[str] = []


class RuntimeModelBindingCreate(StrictBody):
    model_definition_id: uuid.UUID
    route_type: RuntimeModelRouteType
    route_key: str = Field(min_length=1, max_length=255)
    engine_model_id: str = Field(min_length=1, max_length=255)
    provider_config_id: uuid.UUID | None = None
    provider_model_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_route_fields(self) -> "RuntimeModelBindingCreate":
        if self.route_type == RuntimeModelRouteType.PROVIDER_CONFIG:
            if self.provider_config_id is None or self.provider_model_id is None:
                raise ValueError(
                    "provider_config route requires provider_config_id and provider_model_id"
                )
        elif self.provider_config_id is not None or self.provider_model_id is not None:
            raise ValueError("Native routes cannot include Provider Config fields")
        return self


class DiscoveryInstallation(StrictBody):
    installation_key: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    engine_type: RuntimeEngineType
    engine_version: str | None = Field(default=None, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    executable_fingerprint: str = Field(min_length=32, max_length=128)
    capabilities: dict[str, Any] = {}
    discovered_models: list["DiscoveredNativeModel"] = []
    installation_receipt_id: uuid.UUID | None = None
    distribution_manifest_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    adapter_release_id: uuid.UUID | None = None
    adapter_release_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )


class DiscoveredNativeModel(StrictBody):
    model_id: str = Field(min_length=1, max_length=255)
    route_key: str = Field(min_length=1, max_length=255)
    login_evidence_digest: str = Field(min_length=64, max_length=64)


class DiscoveryObservationInput(StrictBody):
    adapter_release_id: uuid.UUID
    candidate_ref: str = Field(min_length=64, max_length=64)
    installation_key: str | None = Field(default=None, max_length=255)
    status: str
    error: dict[str, Any] | None = None


class RuntimeDiscoveryReport(StrictBody):
    node_id: uuid.UUID
    generation: int = Field(ge=1)
    installations: list[DiscoveryInstallation]
    observations: list[DiscoveryObservationInput] = Field(default_factory=list)
    adapter_registry_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    device_signature: str | None = None


class ConfigurationApplyInput(StrictBody):
    configuration_revision_id: uuid.UUID


class ConfigurationClaimInput(StrictBody):
    worker_id: str = Field(min_length=1, max_length=255)


class CapabilityHeartbeatEvidence(StrictBody):
    runtime_instance_id: uuid.UUID
    engine_type: RuntimeEngineType
    engine_version: str | None = Field(default=None, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    configuration_digest: str = Field(min_length=64, max_length=64)
    capability_fingerprint: str = Field(min_length=64, max_length=64)


class CapabilityHeartbeatInput(StrictBody):
    worker_id: str = Field(min_length=1, max_length=255)
    evidence: list[CapabilityHeartbeatEvidence] = Field(max_length=1000)


class ConfigurationResultInput(StrictBody):
    worker_id: str = Field(min_length=1, max_length=255)
    runtime_instance_id: uuid.UUID
    configuration_revision_id: uuid.UUID
    configuration_digest: str = Field(min_length=64, max_length=64)
    status: str
    engine_version: str | None = Field(default=None, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    capabilities: dict[str, Any] = {}
    discovered_models: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None


class NativeModelValidationResult(StrictBody):
    worker_id: str = Field(min_length=1, max_length=255)
    runtime_model_binding_id: uuid.UUID
    attempt_no: int = Field(ge=1)
    status: str
    engine_model_id: str = Field(min_length=1, max_length=255)
    route_key: str = Field(min_length=1, max_length=255)
    evidence: dict[str, Any] = {}
    error: dict[str, Any] | None = None


def _runtime_or_404(
    session: SessionDep, runtime_id: uuid.UUID, namespace_id: uuid.UUID
) -> RuntimeInstance:
    runtime = session.get(RuntimeInstance, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime not found")
    return runtime


def _runtime_public(session: SessionDep, runtime: RuntimeInstance) -> dict[str, Any]:
    bindings = session.exec(
        select(RuntimeModelBinding).where(
            RuntimeModelBinding.runtime_instance_id == runtime.id
        )
    ).all()
    observation = (
        session.get(
            RuntimeDiscoveryObservation,
            runtime.current_discovery_observation_id,
        )
        if runtime.current_discovery_observation_id is not None
        else None
    )
    evidence_state = runtime.status.value
    if runtime.management_type == RuntimeManagementType.CLIENT_DISCOVERED:
        if observation is not None and observation.status != "available":
            evidence_state = (
                "installation_missing" if observation.status == "missing" else "blocked"
            )
        elif runtime.status == RuntimeInstanceStatus.AVAILABLE:
            evidence_state = "available"
        elif (
            runtime.applied_configuration_revision_id is not None
            and runtime.desired_configuration_revision_id
            != runtime.applied_configuration_revision_id
        ):
            evidence_state = "change_detected"
        elif any(
            binding.status == RuntimeModelBindingStatus.DECLARED for binding in bindings
        ):
            evidence_state = "validating"
    return {
        "id": runtime.id,
        "namespace_id": runtime.namespace_id,
        "runtime_node_id": runtime.runtime_node_id,
        "location_type": runtime.location_type.value,
        "management_type": runtime.management_type.value,
        "lifecycle_source_key": runtime.lifecycle_source_key,
        "name": runtime.name,
        "installation_key": runtime.installation_key,
        "engine_type": runtime.engine_type.value,
        "engine_version": runtime.engine_version,
        "adapter_version": runtime.adapter_version,
        "status": runtime.status.value,
        "evidence_state": evidence_state,
        "enabled": runtime.enabled,
        "desired_configuration_revision_id": runtime.desired_configuration_revision_id,
        "applied_configuration_revision_id": runtime.applied_configuration_revision_id,
        "current_capability_report_id": runtime.current_capability_report_id,
        "model_catalog_fingerprint": model_catalog_fingerprint(session, runtime.id),
        "available_model_count": sum(
            row.status == RuntimeModelBindingStatus.AVAILABLE for row in bindings
        ),
        "model_binding_count": len(bindings),
        "last_seen_at": runtime.last_seen_at,
        "created_at": runtime.created_at,
        "updated_at": runtime.updated_at,
    }


def _configuration_from_input(
    runtime_id: uuid.UUID,
    revision: int,
    body: RuntimeConfigurationInput,
    user_id: uuid.UUID | None,
) -> RuntimeConfigurationRevision:
    payload = body.model_dump(exclude={"expected_revision"})
    return RuntimeConfigurationRevision(
        runtime_instance_id=runtime_id,
        revision=revision,
        executable=body.executable,
        arguments=body.arguments,
        working_directory_policy=body.working_directory_policy,
        environment_allowlist=body.environment_allowlist,
        security_policy=body.security_policy,
        resource_limits=body.resource_limits,
        configuration_digest=canonical_digest(payload),
        status=RuntimeConfigurationStatus.DESIRED,
        created_by=user_id,
    )


@router.get("/runtimes")
def list_runtime_instances(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
    engine_type: RuntimeEngineType | None = None,
    status: RuntimeInstanceStatus | None = None,
) -> dict[str, Any]:
    ensure_builtin_platform_runtime(session, namespace_id)
    reconcile_platform_model_bindings(session, namespace_id)
    session.commit()
    statement = select(RuntimeInstance).where(
        RuntimeInstance.namespace_id == namespace_id
    )
    if engine_type is not None:
        statement = statement.where(RuntimeInstance.engine_type == engine_type)
    if status is not None:
        statement = statement.where(RuntimeInstance.status == status)
    rows = session.exec(statement.order_by(col(RuntimeInstance.name))).all()
    return {"data": [_runtime_public(session, row) for row in rows], "count": len(rows)}


@router.get("/runtime-catalog")
def runtime_catalog(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
    engine_type: RuntimeEngineType | None = None,
    model_definition_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    reconcile_platform_model_bindings(session, namespace_id)
    session.commit()
    statement = select(RuntimeInstance).where(
        RuntimeInstance.namespace_id == namespace_id,
        col(RuntimeInstance.enabled).is_(True),
        RuntimeInstance.status == RuntimeInstanceStatus.AVAILABLE,
    )
    if engine_type is not None:
        statement = statement.where(RuntimeInstance.engine_type == engine_type)
    runtimes = session.exec(statement.order_by(col(RuntimeInstance.name))).all()
    data: list[dict[str, Any]] = []
    for runtime in runtimes:
        binding_statement = select(RuntimeModelBinding).where(
            RuntimeModelBinding.runtime_instance_id == runtime.id,
            RuntimeModelBinding.status == RuntimeModelBindingStatus.AVAILABLE,
        )
        if model_definition_id is not None:
            binding_statement = binding_statement.where(
                RuntimeModelBinding.model_definition_id == model_definition_id
            )
        bindings = session.exec(
            binding_statement.order_by(col(RuntimeModelBinding.engine_model_id))
        ).all()
        if model_definition_id is not None and not bindings:
            continue
        data.append(
            {
                **_runtime_public(session, runtime),
                "model_bindings": [
                    _binding_public(session, binding) for binding in bindings
                ],
            }
        )
    return {"data": data, "count": len(data)}


@router.get("/runtime-nodes/{node_id}/runtimes")
def list_node_runtime_instances(
    node_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    node = session.get(RuntimeNode, node_id)
    if node is None or node.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime Node not found")
    rows = session.exec(
        select(RuntimeInstance)
        .where(RuntimeInstance.runtime_node_id == node.id)
        .order_by(col(RuntimeInstance.name))
    ).all()
    return {"data": [_runtime_public(session, row) for row in rows], "count": len(rows)}


@router.post("/runtime-nodes/{node_id}/runtimes/{runtime_id}/enable")
def enable_node_runtime_instance(
    node_id: uuid.UUID,
    runtime_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    if (
        runtime.runtime_node_id != node_id
        or runtime.location_type != RuntimeLocationType.NODE
    ):
        raise HTTPException(404, "Runtime does not belong to this Node")
    if runtime.status == RuntimeInstanceStatus.INCOMPATIBLE:
        raise HTTPException(409, "Incompatible Runtime cannot be enabled")
    if runtime.desired_configuration_revision_id is None:
        raise HTTPException(
            409, "Runtime configuration must be created before enablement"
        )
    runtime.enabled = True
    runtime.status = (
        RuntimeInstanceStatus.AVAILABLE
        if runtime.applied_configuration_revision_id is not None
        and runtime.current_capability_report_id is not None
        else RuntimeInstanceStatus.DISCOVERED
    )
    runtime.updated_at = datetime.now(timezone.utc)
    session.add(runtime)
    session.commit()
    return _runtime_public(session, runtime)


@router.post(
    "/runtimes/platform-instances",
    status_code=201,
    operation_id="runtime-instances-create_platform_runtime_instance_alias",
)
@router.post("/runtimes/platform", status_code=201)
def create_platform_runtime_instance(
    _body: PlatformRuntimeCreate,
    _session: SessionDep,
    _current_user: CurrentUser,
    _namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    raise HTTPException(410, "platform_runtime_system_managed")


@router.get("/runtimes/{runtime_id}")
def get_runtime_instance(
    runtime_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    configurations = session.exec(
        select(RuntimeConfigurationRevision)
        .where(RuntimeConfigurationRevision.runtime_instance_id == runtime.id)
        .order_by(col(RuntimeConfigurationRevision.revision).desc())
    ).all()
    reports = session.exec(
        select(RuntimeCapabilityReport)
        .where(RuntimeCapabilityReport.runtime_instance_id == runtime.id)
        .order_by(col(RuntimeCapabilityReport.generation).desc())
    ).all()
    return {
        **_runtime_public(session, runtime),
        "configurations": [
            {
                "id": row.id,
                "revision": row.revision,
                "origin": row.origin.value,
                "adapter_execution_ref": row.adapter_execution_ref,
                **configuration_payload(row),
                "configuration_digest": row.configuration_digest,
                "status": row.status.value,
                "error": row.error,
                "created_at": row.created_at,
                "applied_at": row.applied_at,
            }
            for row in configurations
        ],
        "capability_reports": [
            {
                "id": row.id,
                "generation": row.generation,
                "engine_version": row.engine_version,
                "adapter_version": row.adapter_version,
                "configuration_digest": row.configuration_digest,
                "capabilities": row.capabilities,
                "discovered_models": row.discovered_models,
                "capability_fingerprint": row.capability_fingerprint,
                "reported_at": row.reported_at,
            }
            for row in reports
        ],
    }


@router.put("/runtimes/{runtime_id}/configuration", status_code=201)
def create_runtime_configuration(
    runtime_id: uuid.UUID,
    body: RuntimeConfigurationInput,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = session.exec(
        select(RuntimeInstance)
        .where(
            RuntimeInstance.id == runtime_id,
            RuntimeInstance.namespace_id == namespace_id,
        )
        .with_for_update()
    ).first()
    if runtime is None:
        raise HTTPException(404, "Runtime not found")
    if runtime.management_type in {
        RuntimeManagementType.PLATFORM_BUILTIN,
        RuntimeManagementType.SERVICE_MANAGED,
        RuntimeManagementType.CLIENT_DISCOVERED,
    }:
        raise HTTPException(409, "runtime_configuration_system_managed")
    latest = session.exec(
        select(RuntimeConfigurationRevision)
        .where(RuntimeConfigurationRevision.runtime_instance_id == runtime.id)
        .order_by(col(RuntimeConfigurationRevision.revision).desc())
    ).first()
    actual = latest.revision if latest else 0
    if body.expected_revision != actual:
        raise HTTPException(
            409,
            {"expected_revision": body.expected_revision, "actual_revision": actual},
        )
    row = _configuration_from_input(runtime.id, actual + 1, body, current_user.id)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        current = session.exec(
            select(RuntimeConfigurationRevision)
            .where(RuntimeConfigurationRevision.runtime_instance_id == runtime_id)
            .order_by(col(RuntimeConfigurationRevision.revision).desc())
        ).first()
        raise HTTPException(
            409,
            {
                "expected_revision": body.expected_revision,
                "actual_revision": current.revision if current else 0,
            },
        ) from exc
    runtime.desired_configuration_revision_id = row.id
    runtime.updated_at = datetime.now(timezone.utc)
    session.add(runtime)
    session.commit()
    return {
        "id": row.id,
        "revision": row.revision,
        "configuration_digest": row.configuration_digest,
        "status": row.status.value,
    }


@router.post("/runtimes/{runtime_id}/configuration/apply", status_code=202)
def apply_runtime_configuration(
    runtime_id: uuid.UUID,
    body: ConfigurationApplyInput,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    if runtime.management_type in {
        RuntimeManagementType.PLATFORM_BUILTIN,
        RuntimeManagementType.SERVICE_MANAGED,
        RuntimeManagementType.CLIENT_DISCOVERED,
    }:
        raise HTTPException(409, "runtime_configuration_system_managed")
    configuration = session.get(
        RuntimeConfigurationRevision, body.configuration_revision_id
    )
    if (
        configuration is None
        or configuration.runtime_instance_id != runtime.id
        or runtime.desired_configuration_revision_id != configuration.id
    ):
        raise HTTPException(
            409, "Only the current desired configuration can be applied"
        )
    if configuration.status == RuntimeConfigurationStatus.FAILED:
        configuration.status = RuntimeConfigurationStatus.DESIRED
        configuration.error = None
        session.add(configuration)
        session.commit()
    return {
        "runtime_instance_id": runtime.id,
        "configuration_revision_id": configuration.id,
        "revision": configuration.revision,
        "configuration_digest": configuration.configuration_digest,
        "status": configuration.status.value,
    }


@router.get("/llm/model-definitions")
def list_model_definitions(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    rows = session.exec(
        select(LlmModelDefinition)
        .where(LlmModelDefinition.namespace_id == namespace_id)
        .order_by(
            col(LlmModelDefinition.provider_family), col(LlmModelDefinition.model_key)
        )
    ).all()
    return {"data": [row.model_dump() for row in rows], "count": len(rows)}


@router.post("/llm/model-definitions", status_code=201)
def create_model_definition(
    body: ModelDefinitionCreate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    family = body.provider_family.strip().lower()
    key = body.model_key.strip()
    existing = session.exec(
        select(LlmModelDefinition).where(
            LlmModelDefinition.namespace_id == namespace_id,
            LlmModelDefinition.provider_family == family,
            LlmModelDefinition.model_key == key,
        )
    ).first()
    if existing:
        return existing.model_dump()
    row = LlmModelDefinition(
        namespace_id=namespace_id,
        provider_family=family,
        model_key=key,
        display_name=body.display_name,
        capability_tags=body.capability_tags,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


def _binding_public(session: SessionDep, row: RuntimeModelBinding) -> dict[str, Any]:
    definition = session.get(LlmModelDefinition, row.model_definition_id)
    return {
        "id": row.id,
        "runtime_instance_id": row.runtime_instance_id,
        "origin": row.origin.value,
        "model_definition_id": row.model_definition_id,
        "model": definition.model_dump() if definition else None,
        "provider_config_id": row.provider_config_id,
        "provider_model_id": row.provider_model_id,
        "route_type": row.route_type.value,
        "route_key": row.route_key,
        "engine_model_id": row.engine_model_id,
        "status": row.status.value,
        "validation_fingerprint": row.validation_fingerprint,
        "validated_capability_fingerprint": row.validated_capability_fingerprint,
        "last_validated_at": row.last_validated_at,
        "validation_expires_at": row.validation_expires_at,
        "last_error": row.last_error,
    }


@router.get("/runtimes/{runtime_id}/model-bindings")
def list_runtime_model_bindings(
    runtime_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    rows = session.exec(
        select(RuntimeModelBinding)
        .where(RuntimeModelBinding.runtime_instance_id == runtime.id)
        .order_by(col(RuntimeModelBinding.engine_model_id))
    ).all()
    return {"data": [_binding_public(session, row) for row in rows], "count": len(rows)}


@router.post("/runtimes/{runtime_id}/model-bindings", status_code=201)
def create_runtime_model_binding(
    runtime_id: uuid.UUID,
    body: RuntimeModelBindingCreate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    if runtime.management_type == RuntimeManagementType.PLATFORM_BUILTIN:
        raise HTTPException(409, "platform_runtime_bindings_system_managed")
    definition = session.get(LlmModelDefinition, body.model_definition_id)
    if (
        definition is None
        or definition.namespace_id != namespace_id
        or not definition.enabled
    ):
        raise HTTPException(422, "Enabled model definition is required")
    row = RuntimeModelBinding(
        namespace_id=namespace_id,
        runtime_instance_id=runtime.id,
        model_definition_id=definition.id,
        provider_config_id=body.provider_config_id,
        provider_model_id=body.provider_model_id,
        route_type=body.route_type,
        route_key=body.route_key,
        engine_model_id=body.engine_model_id,
    )
    try:
        validate_model_binding_route(session, row)
    except RuntimeCatalogError as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message}) from exc
    session.add(row)
    session.commit()
    session.refresh(row)
    return _binding_public(session, row)


@router.post("/runtimes/{runtime_id}/model-bindings/{binding_id}/validate")
def validate_runtime_model_binding(
    runtime_id: uuid.UUID,
    binding_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    row = session.exec(
        select(RuntimeModelBinding)
        .where(RuntimeModelBinding.id == binding_id)
        .with_for_update()
    ).first()
    if (
        row is None
        or row.runtime_instance_id != runtime_id
        or row.namespace_id != namespace_id
    ):
        raise HTTPException(404, "Runtime model binding not found")
    last_attempt = session.exec(
        select(RuntimeModelValidationAttempt)
        .where(RuntimeModelValidationAttempt.runtime_model_binding_id == row.id)
        .order_by(col(RuntimeModelValidationAttempt.attempt_no).desc())
    ).first()
    attempt_no = (last_attempt.attempt_no if last_attempt else 0) + 1
    try:
        _configuration_evidence, capability, _catalog_digest = current_runtime_evidence(
            session, runtime, include_catalog=False
        )
        validate_model_binding_route(session, row)
        if row.provider_config_id:
            provider = session.get(LlmProviderConfig, row.provider_config_id)
            if (
                provider is None
                or provider.validation_status != ProviderValidationStatus.SUCCESS
            ):
                raise RuntimeCatalogError(
                    "provider_not_validated",
                    "Provider Config must pass connection validation before binding validation",
                )
            evidence = validate_provider_model_call(provider, row.engine_model_id)
        else:
            attempt = RuntimeModelValidationAttempt(
                runtime_model_binding_id=row.id,
                attempt_no=attempt_no,
                status="pending",
            )
            row.status = RuntimeModelBindingStatus.DECLARED
            row.last_error = None
            session.add(row)
            session.add(attempt)
            session.commit()
            return {
                **_binding_public(session, row),
                "validation_attempt_id": attempt.id,
                "validation_status": "pending",
            }
        evidence = {
            **evidence,
            "runtime_instance_id": str(runtime_id),
            "model_definition_id": str(row.model_definition_id),
            "engine_model_id": row.engine_model_id,
            "route_type": row.route_type.value,
            "route_key": row.route_key,
        }
        digest = canonical_digest(evidence)
        row.status = RuntimeModelBindingStatus.AVAILABLE
        row.validation_fingerprint = digest
        row.last_validated_at = datetime.now(timezone.utc)
        row.validation_expires_at = row.last_validated_at + timedelta(hours=24)
        row.validated_capability_fingerprint = capability.capability_fingerprint
        row.last_error = None
        attempt = RuntimeModelValidationAttempt(
            runtime_model_binding_id=row.id,
            attempt_no=attempt_no,
            status="succeeded",
            evidence_digest=digest,
            completed_at=datetime.now(timezone.utc),
        )
    except RuntimeCatalogError as exc:
        row.status = RuntimeModelBindingStatus.FAILED
        row.last_error = {"code": exc.code, "message": exc.message}
        attempt = RuntimeModelValidationAttempt(
            runtime_model_binding_id=row.id,
            attempt_no=attempt_no,
            status="failed",
            error=row.last_error,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.add(attempt)
        session.commit()
        raise HTTPException(422, row.last_error) from exc
    session.add(row)
    session.add(attempt)
    session.commit()
    return _binding_public(session, row)


@router.post("/runtimes/{runtime_id}/model-bindings/{binding_id}/disable")
def disable_runtime_model_binding(
    runtime_id: uuid.UUID,
    binding_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    _runtime_or_404(session, runtime_id, namespace_id)
    row = session.get(RuntimeModelBinding, binding_id)
    if (
        row is None
        or row.runtime_instance_id != runtime_id
        or row.namespace_id != namespace_id
    ):
        raise HTTPException(404, "Runtime model binding not found")
    row.status = RuntimeModelBindingStatus.DISABLED
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return _binding_public(session, row)


@router.post("/runtimes/{runtime_id}/pause")
def pause_managed_node_runtime(
    runtime_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    if runtime.management_type not in {
        RuntimeManagementType.CLIENT_DISCOVERED,
        RuntimeManagementType.SERVICE_MANAGED,
    }:
        raise HTTPException(409, "only managed Node Runtime scheduling can be paused")
    runtime.enabled = False
    runtime.updated_at = datetime.now(timezone.utc)
    session.add(runtime)
    session.add(
        RuntimeControlDecision(
            runtime_instance_id=runtime.id,
            action=RuntimeControlAction.PAUSE,
            actor_id=current_user.id,
            evidence={
                "capability_report_id": str(runtime.current_capability_report_id)
                if runtime.current_capability_report_id
                else None
            },
        )
    )
    session.commit()
    return _runtime_public(session, runtime)


@router.post("/runtimes/{runtime_id}/resume")
def resume_managed_node_runtime(
    runtime_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    if runtime.management_type not in {
        RuntimeManagementType.CLIENT_DISCOVERED,
        RuntimeManagementType.SERVICE_MANAGED,
    }:
        raise HTTPException(409, "only managed Node Runtime scheduling can be resumed")
    observation = (
        session.get(
            RuntimeDiscoveryObservation,
            runtime.current_discovery_observation_id,
        )
        if runtime.current_discovery_observation_id is not None
        else None
    )
    configuration = (
        session.get(
            RuntimeConfigurationRevision,
            runtime.applied_configuration_revision_id,
        )
        if runtime.applied_configuration_revision_id is not None
        else None
    )
    capability = (
        session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
        if runtime.current_capability_report_id is not None
        else None
    )
    node = (
        session.get(RuntimeNode, runtime.runtime_node_id)
        if runtime.runtime_node_id is not None
        else None
    )
    bindings = session.exec(
        select(RuntimeModelBinding).where(
            RuntimeModelBinding.runtime_instance_id == runtime.id,
            RuntimeModelBinding.status == RuntimeModelBindingStatus.AVAILABLE,
        )
    ).all()
    ready = bool(
        observation is not None
        and observation.status == "available"
        and configuration is not None
        and capability is not None
        and capability.configuration_digest == configuration.configuration_digest
        and capability.reported_at >= datetime.now(timezone.utc) - timedelta(minutes=5)
        and node is not None
        and node_is_online(node)
        and any(
            model_binding_is_current(
                session, binding, capability.capability_fingerprint
            )
            for binding in bindings
        )
    )
    if not ready:
        raise HTTPException(
            409, "managed Node Runtime evidence must be revalidated before resume"
        )
    assert observation is not None and capability is not None
    runtime.enabled = True
    runtime.status = RuntimeInstanceStatus.AVAILABLE
    runtime.updated_at = datetime.now(timezone.utc)
    session.add(runtime)
    session.add(
        RuntimeControlDecision(
            runtime_instance_id=runtime.id,
            action=RuntimeControlAction.RESUME,
            actor_id=current_user.id,
            evidence={
                "discovery_observation_id": str(observation.id),
                "capability_report_id": str(capability.id),
                "available_binding_ids": [str(binding.id) for binding in bindings],
            },
        )
    )
    session.commit()
    return _runtime_public(session, runtime)


@router.get("/runtime-nodes/{node_id}/discovery-observations")
def list_discovery_observations(
    node_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    node = session.get(RuntimeNode, node_id)
    if node is None or node.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime Node not found")
    observations = session.exec(
        select(RuntimeDiscoveryObservation)
        .where(RuntimeDiscoveryObservation.node_id == node.id)
        .order_by(
            col(RuntimeDiscoveryObservation.generation).desc(),
            col(RuntimeDiscoveryObservation.installation_key),
        )
    ).all()
    return {
        "data": [observation.model_dump() for observation in observations],
        "count": len(observations),
    }


@router.post("/runtime-nodes/{node_id}/discovery/refresh", status_code=202)
def request_client_discovery_refresh(
    node_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    node = session.exec(
        select(RuntimeNode).where(RuntimeNode.id == node_id).with_for_update()
    ).first()
    if node is None or node.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime Node not found")
    if node.management_mode != RuntimeNodeMode.CLIENT:
        raise HTTPException(409, "discovery refresh is only available for client Nodes")
    node.discovery_requested_generation = max(
        node.discovery_requested_generation,
        node.discovery_generation + 1,
    )
    session.add(node)
    session.commit()
    return {
        "status": "requested",
        "generation": node.discovery_requested_generation,
    }


@internal_router.post("/discovery")
def report_runtime_discovery(
    body: RuntimeDiscoveryReport,
    session: SessionDep,
) -> dict[str, Any]:
    node = session.get(RuntimeNode, body.node_id)
    if node is None or node.revoked_at is not None:
        raise HTTPException(404, "Active Runtime Node not found")
    if node.management_mode in {RuntimeNodeMode.SERVICE, RuntimeNodeMode.CLIENT}:
        if body.device_signature is None:
            raise HTTPException(422, "Runtime discovery device signature is required")
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        signed_payload = body.model_dump(
            mode="json",
            exclude={"node_id", "device_signature"},
            exclude_none=True,
        )
        discovery_digest = canonical_digest(signed_payload)
        try:
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(node.public_key, validate=True)
            ).verify(
                base64.b64decode(body.device_signature, validate=True),
                f"neomua-runtime-discovery-v1:{discovery_digest}".encode(),
            )
        except (ValueError, InvalidSignature) as exc:
            raise HTTPException(
                422, "Runtime discovery device signature is invalid"
            ) from exc
    if node.management_mode == RuntimeNodeMode.SERVICE:
        if (
            len(body.installations) != 1
            or body.installations[0].engine_type != RuntimeEngineType.CLAUDE_AGENT_SDK
        ):
            raise HTTPException(
                422,
                "service Node must report exactly one managed Claude Agent SDK Runtime",
            )
        receipt = (
            session.get(NodeInstallationReceipt, node.current_installation_receipt_id)
            if node.current_installation_receipt_id is not None
            else None
        )
        installation = body.installations[0]
        if (
            receipt is None
            or installation.installation_receipt_id != receipt.id
            or installation.distribution_manifest_digest != receipt.manifest_digest
            or installation.installation_key != receipt.logical_installation_ref
        ):
            raise HTTPException(409, "service installation receipt mismatch")
    if node.management_mode == RuntimeNodeMode.CLIENT and any(
        item.engine_type == RuntimeEngineType.CLAUDE_AGENT_SDK
        for item in body.installations
    ):
        raise HTTPException(422, "client Node cannot report a managed Claude Agent SDK")
    if node.management_mode == RuntimeNodeMode.CLIENT:
        if (
            node.adapter_registry_digest is None
            or body.adapter_registry_digest != node.adapter_registry_digest
        ):
            raise HTTPException(409, "client Adapter Registry digest mismatch")
        for item in body.installations:
            release = (
                session.get(RuntimeAdapterRelease, item.adapter_release_id)
                if item.adapter_release_id is not None
                else None
            )
            if (
                release is None
                or release.engine_type != item.engine_type
                or release.version != item.adapter_version
                or release.release_digest != item.adapter_release_digest
                or item.capabilities.get("adapter_release_digest")
                != release.release_digest
                or item.capabilities.get("native_login_ready") is not True
            ):
                raise HTTPException(409, "client Adapter release evidence mismatch")
            installation_parts = item.installation_key.split(":", 2)
            if len(installation_parts) != 3 or installation_parts[:2] != [
                "client",
                release.adapter_id,
            ]:
                raise HTTPException(422, "client installation key is invalid")
            try:
                installation_id = uuid.UUID(installation_parts[2])
            except ValueError as exc:
                raise HTTPException(422, "client installation UUID is invalid") from exc
            expected_route_prefix = f"native:{installation_id}:"
            if any(
                not model.route_key.startswith(expected_route_prefix)
                for model in item.discovered_models
            ):
                raise HTTPException(422, "client native model route is invalid")
    report_digest = canonical_digest(
        body.model_dump(
            mode="json",
            exclude={"node_id", "device_signature"},
            exclude_none=True,
        )
    )
    if body.generation < node.discovery_generation:
        raise HTTPException(409, "Stale discovery generation")
    if body.generation == node.discovery_generation:
        if node.discovery_digest != report_digest:
            raise HTTPException(409, "Discovery generation content conflict")
        return {"generation": body.generation, "accepted": 0, "idempotent": True}
    accepted = 0
    seen_keys: set[str] = set()
    for item in body.installations:
        if item.installation_key in seen_keys:
            raise HTTPException(422, "Duplicate installation_key in discovery report")
        seen_keys.add(item.installation_key)
        runtime = session.exec(
            select(RuntimeInstance).where(
                RuntimeInstance.namespace_id == node.namespace_id,
                RuntimeInstance.runtime_node_id == node.id,
                RuntimeInstance.installation_key == item.installation_key,
            )
        ).first()
        previous_capability = (
            session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
            if runtime is not None and runtime.current_capability_report_id is not None
            else None
        )
        evidence_changed = bool(
            runtime is not None
            and (
                runtime.engine_version != item.engine_version
                or runtime.adapter_version != item.adapter_version
                or runtime.executable_fingerprint != item.executable_fingerprint
                or (
                    previous_capability is not None
                    and previous_capability.adapter_release_id
                    != item.adapter_release_id
                )
            )
        )
        if runtime is None:
            management_type = {
                RuntimeNodeMode.SERVICE: RuntimeManagementType.SERVICE_MANAGED,
                RuntimeNodeMode.CLIENT: RuntimeManagementType.CLIENT_DISCOVERED,
            }.get(node.management_mode, RuntimeManagementType.LEGACY_MANUAL)
            runtime = RuntimeInstance(
                namespace_id=node.namespace_id,
                runtime_node_id=node.id,
                location_type=RuntimeLocationType.NODE,
                management_type=management_type,
                lifecycle_source_key=f"{node.id}:{item.installation_key}",
                name=item.name,
                installation_key=item.installation_key,
                engine_type=item.engine_type,
                engine_version=item.engine_version,
                adapter_version=item.adapter_version,
                executable_fingerprint=item.executable_fingerprint,
                status=RuntimeInstanceStatus.DISCOVERED,
                enabled=node.management_mode
                in {RuntimeNodeMode.SERVICE, RuntimeNodeMode.CLIENT},
            )
            session.add(runtime)
            session.flush()
            if management_type == RuntimeManagementType.CLIENT_DISCOVERED:
                session.add(
                    RuntimeControlDecision(
                        runtime_instance_id=runtime.id,
                        action=RuntimeControlAction.AUTO_ENABLE,
                        evidence={
                            "source": "client_bootstrap",
                            "discovery_generation": body.generation,
                        },
                    )
                )
            if management_type != RuntimeManagementType.LEGACY_MANUAL:
                executable = (
                    "codex" if item.engine_type == RuntimeEngineType.CODEX else "claude"
                )
                configuration_payload_value = {
                    "executable": executable,
                    "arguments": [],
                    "working_directory_policy": "workspace",
                    "environment_allowlist": [],
                    "security_policy": {
                        "permission_modes": list(
                            item.capabilities.get("permission_modes", ["default"])
                        )
                    },
                    "resource_limits": {"max_timeout_seconds": 3600},
                }
                configuration = RuntimeConfigurationRevision(
                    runtime_instance_id=runtime.id,
                    revision=1,
                    origin=(
                        RuntimeConfigurationOrigin.SERVICE_MANIFEST
                        if management_type == RuntimeManagementType.SERVICE_MANAGED
                        else RuntimeConfigurationOrigin.CLIENT_ADAPTER
                    ),
                    adapter_execution_ref=item.installation_key,
                    configuration_digest=canonical_digest(configuration_payload_value),
                    status=RuntimeConfigurationStatus.DESIRED,
                    created_by=None,
                    executable=executable,
                    arguments=[],
                    working_directory_policy="workspace",
                    environment_allowlist=[],
                    security_policy=configuration_payload_value["security_policy"],
                    resource_limits=configuration_payload_value["resource_limits"],
                )
                session.add(configuration)
                session.flush()
                runtime.desired_configuration_revision_id = configuration.id
                session.add(runtime)
        elif runtime.engine_type != item.engine_type:
            runtime.status = RuntimeInstanceStatus.INCOMPATIBLE
            session.add(runtime)
            continue
        elif evidence_changed:
            if runtime.management_type != RuntimeManagementType.CLIENT_DISCOVERED:
                runtime.status = RuntimeInstanceStatus.INCOMPATIBLE
                session.add(runtime)
                continue
            runtime.status = RuntimeInstanceStatus.UNAVAILABLE
            runtime.applied_configuration_revision_id = None
            runtime.current_capability_report_id = None
            runtime.executable_fingerprint = item.executable_fingerprint
            previous_revisions = session.exec(
                select(RuntimeConfigurationRevision).where(
                    RuntimeConfigurationRevision.runtime_instance_id == runtime.id
                )
            ).all()
            configuration_payload_value = {
                "executable": (
                    "codex" if item.engine_type == RuntimeEngineType.CODEX else "claude"
                ),
                "arguments": [],
                "working_directory_policy": "workspace",
                "environment_allowlist": [],
                "security_policy": {
                    "permission_modes": list(
                        item.capabilities.get("permission_modes", ["default"])
                    )
                },
                "resource_limits": {"max_timeout_seconds": 3600},
            }
            executable = (
                "codex" if item.engine_type == RuntimeEngineType.CODEX else "claude"
            )
            configuration = RuntimeConfigurationRevision(
                runtime_instance_id=runtime.id,
                revision=len(previous_revisions) + 1,
                origin=RuntimeConfigurationOrigin.CLIENT_ADAPTER,
                adapter_execution_ref=item.installation_key,
                configuration_digest=canonical_digest(configuration_payload_value),
                status=RuntimeConfigurationStatus.DESIRED,
                created_by=None,
                executable=executable,
                arguments=[],
                working_directory_policy="workspace",
                environment_allowlist=[],
                security_policy=configuration_payload_value["security_policy"],
                resource_limits=configuration_payload_value["resource_limits"],
            )
            session.add(configuration)
            session.flush()
            runtime.desired_configuration_revision_id = configuration.id
        runtime.name = item.name
        runtime.engine_version = item.engine_version
        runtime.adapter_version = item.adapter_version
        runtime.last_seen_at = datetime.now(timezone.utc)
        if (
            runtime.enabled
            and runtime.applied_configuration_revision_id is not None
            and runtime.current_capability_report_id is not None
        ):
            runtime.status = RuntimeInstanceStatus.AVAILABLE
        session.add(runtime)
        configuration_digest = "0" * 64
        if runtime.applied_configuration_revision_id:
            applied_configuration = session.get(
                RuntimeConfigurationRevision, runtime.applied_configuration_revision_id
            )
            if applied_configuration:
                configuration_digest = applied_configuration.configuration_digest
        generation = (
            len(
                session.exec(
                    select(RuntimeCapabilityReport).where(
                        RuntimeCapabilityReport.runtime_instance_id == runtime.id
                    )
                ).all()
            )
            + 1
        )
        report = RuntimeCapabilityReport(
            runtime_instance_id=runtime.id,
            generation=generation,
            engine_version=item.engine_version,
            adapter_version=item.adapter_version,
            adapter_release_id=item.adapter_release_id,
            configuration_digest=configuration_digest,
            capabilities=item.capabilities,
            discovered_models=[model.model_dump() for model in item.discovered_models],
            capability_fingerprint=canonical_digest(
                {
                    "engine_type": item.engine_type.value,
                    "engine_version": item.engine_version,
                    "adapter_version": item.adapter_version,
                    "configuration_digest": configuration_digest,
                    "capabilities": item.capabilities,
                    "discovered_models": [
                        model.model_dump() for model in item.discovered_models
                    ],
                }
            ),
        )
        session.add(report)
        session.flush()
        requires_client_revalidation = bool(
            runtime.management_type == RuntimeManagementType.CLIENT_DISCOVERED
            and (
                previous_capability is None
                or evidence_changed
                or previous_capability.capability_fingerprint
                != report.capability_fingerprint
            )
        )
        if requires_client_revalidation:
            runtime.status = RuntimeInstanceStatus.UNAVAILABLE
        runtime.current_capability_report_id = report.id
        observation = RuntimeDiscoveryObservation(
            node_id=node.id,
            runtime_instance_id=runtime.id,
            adapter_release_id=item.adapter_release_id,
            generation=body.generation,
            installation_key=item.installation_key,
            status="available",
            evidence={
                "engine_type": item.engine_type.value,
                "engine_version": item.engine_version,
                "adapter_version": item.adapter_version,
                "adapter_release_digest": item.adapter_release_digest,
                "executable_fingerprint": item.executable_fingerprint,
                "capability_digest": canonical_digest(item.capabilities),
                "model_digest": canonical_digest(
                    [model.model_dump() for model in item.discovered_models]
                ),
            },
            evidence_digest=canonical_digest(
                {
                    "installation_key": item.installation_key,
                    "executable_fingerprint": item.executable_fingerprint,
                    "adapter_release_digest": item.adapter_release_digest,
                    "capabilities": item.capabilities,
                    "models": [model.model_dump() for model in item.discovered_models],
                }
            ),
        )
        session.add(observation)
        session.flush()
        runtime.current_discovery_observation_id = observation.id
        session.add(runtime)
        if runtime.management_type == RuntimeManagementType.CLIENT_DISCOVERED:
            active_route_keys: set[str] = set()
            for native_model in item.discovered_models:
                active_route_keys.add(native_model.route_key)
                definition = session.exec(
                    select(LlmModelDefinition).where(
                        LlmModelDefinition.namespace_id == node.namespace_id,
                        LlmModelDefinition.provider_family == item.engine_type.value,
                        LlmModelDefinition.model_key == native_model.model_id,
                    )
                ).first()
                if definition is None:
                    definition = LlmModelDefinition(
                        namespace_id=node.namespace_id,
                        provider_family=item.engine_type.value,
                        model_key=native_model.model_id,
                        display_name=native_model.model_id,
                    )
                    session.add(definition)
                    session.flush()
                binding = session.exec(
                    select(RuntimeModelBinding).where(
                        RuntimeModelBinding.runtime_instance_id == runtime.id,
                        RuntimeModelBinding.route_type
                        == RuntimeModelRouteType.RUNTIME_NATIVE,
                        RuntimeModelBinding.route_key == native_model.route_key,
                    )
                ).first()
                if binding is None:
                    binding = RuntimeModelBinding(
                        namespace_id=node.namespace_id,
                        runtime_instance_id=runtime.id,
                        origin=RuntimeModelBindingOrigin.RUNTIME_NATIVE_DISCOVERED,
                        model_definition_id=definition.id,
                        route_type=RuntimeModelRouteType.RUNTIME_NATIVE,
                        route_key=native_model.route_key,
                        engine_model_id=native_model.model_id,
                        status=RuntimeModelBindingStatus.DECLARED,
                        last_error={"code": "native_model_validation_pending"},
                    )
                    session.add(binding)
                    session.flush()
                    session.add(
                        RuntimeModelValidationAttempt(
                            runtime_model_binding_id=binding.id,
                            attempt_no=1,
                            status="pending",
                        )
                    )
                elif (
                    binding.engine_model_id != native_model.model_id
                    or binding.model_definition_id != definition.id
                ):
                    binding.status = RuntimeModelBindingStatus.FAILED
                    binding.last_error = {"code": "native_model_identity_conflict"}
                    session.add(binding)
                elif requires_client_revalidation:
                    binding.status = RuntimeModelBindingStatus.DECLARED
                    binding.validation_fingerprint = None
                    binding.validated_capability_fingerprint = None
                    binding.last_validated_at = None
                    binding.validation_expires_at = None
                    binding.last_error = {"code": "native_model_revalidation_pending"}
                    attempts = session.exec(
                        select(RuntimeModelValidationAttempt).where(
                            RuntimeModelValidationAttempt.runtime_model_binding_id
                            == binding.id
                        )
                    ).all()
                    if not any(
                        attempt.status in {"pending", "running"} for attempt in attempts
                    ):
                        session.add(
                            RuntimeModelValidationAttempt(
                                runtime_model_binding_id=binding.id,
                                attempt_no=max(
                                    (attempt.attempt_no for attempt in attempts),
                                    default=0,
                                )
                                + 1,
                                status="pending",
                            )
                        )
                    session.add(binding)
            automatic_bindings = session.exec(
                select(RuntimeModelBinding).where(
                    RuntimeModelBinding.runtime_instance_id == runtime.id,
                    RuntimeModelBinding.origin
                    == RuntimeModelBindingOrigin.RUNTIME_NATIVE_DISCOVERED,
                )
            ).all()
            for binding in automatic_bindings:
                if binding.route_key not in active_route_keys:
                    binding.status = RuntimeModelBindingStatus.DISABLED
                    binding.last_error = {"code": "native_model_removed"}
                    session.add(binding)
        accepted += 1
    known = session.exec(
        select(RuntimeInstance).where(RuntimeInstance.runtime_node_id == node.id)
    ).all()
    for runtime in known:
        if runtime.installation_key not in seen_keys:
            runtime.status = RuntimeInstanceStatus.UNAVAILABLE
            missing = RuntimeDiscoveryObservation(
                node_id=node.id,
                runtime_instance_id=runtime.id,
                generation=body.generation,
                installation_key=runtime.installation_key,
                status="missing",
                evidence={"error": {"code": "installation_missing"}},
                evidence_digest=canonical_digest(
                    {
                        "installation_key": runtime.installation_key,
                        "status": "missing",
                    }
                ),
            )
            session.add(missing)
            session.flush()
            runtime.current_discovery_observation_id = missing.id
            session.add(runtime)
    for diagnostic in body.observations:
        if diagnostic.status == "available":
            continue
        if diagnostic.status not in {"missing", "blocked"}:
            raise HTTPException(422, "invalid discovery observation status")
        release = session.get(RuntimeAdapterRelease, diagnostic.adapter_release_id)
        if release is None:
            raise HTTPException(409, "discovery observation Adapter is unavailable")
        observation_key = (
            diagnostic.installation_key or f"candidate:{diagnostic.candidate_ref}"
        )
        session.add(
            RuntimeDiscoveryObservation(
                node_id=node.id,
                adapter_release_id=release.id,
                generation=body.generation,
                installation_key=observation_key,
                status=diagnostic.status,
                evidence={
                    "candidate_ref": diagnostic.candidate_ref,
                    "error": diagnostic.error,
                },
                evidence_digest=canonical_digest(diagnostic.model_dump(mode="json")),
            )
        )
    node.discovery_generation = body.generation
    node.discovery_digest = report_digest
    session.add(node)
    session.commit()
    return {"generation": body.generation, "accepted": accepted, "idempotent": False}


@internal_router.post("/configurations/claim")
def claim_runtime_configuration(
    body: ConfigurationClaimInput, session: SessionDep
) -> dict[str, Any]:
    configuration = session.exec(
        select(RuntimeConfigurationRevision)
        .join(
            RuntimeInstance,
            col(RuntimeConfigurationRevision.runtime_instance_id) == RuntimeInstance.id,
        )
        .where(
            RuntimeConfigurationRevision.status == RuntimeConfigurationStatus.DESIRED,
            RuntimeInstance.location_type == RuntimeLocationType.PLATFORM,
            col(RuntimeInstance.enabled).is_(True),
            RuntimeInstance.desired_configuration_revision_id
            == RuntimeConfigurationRevision.id,
        )
        .order_by(col(RuntimeConfigurationRevision.created_at))
        .with_for_update(skip_locked=True)
    ).first()
    if configuration is None:
        raise HTTPException(204)
    runtime = session.get(RuntimeInstance, configuration.runtime_instance_id)
    if runtime is None:
        raise HTTPException(409, "Runtime configuration target is missing")
    configuration.status = RuntimeConfigurationStatus.APPLYING
    configuration.error = {"apply_worker_id": body.worker_id}
    session.add(configuration)
    session.commit()
    return {
        "runtime_instance_id": runtime.id,
        "engine_type": runtime.engine_type.value,
        "engine_version": runtime.engine_version,
        "adapter_version": runtime.adapter_version,
        "configuration_revision_id": configuration.id,
        "revision": configuration.revision,
        "configuration_digest": configuration.configuration_digest,
        **configuration_payload(configuration),
    }


@internal_router.post("/capabilities/heartbeat")
def heartbeat_runtime_capabilities(
    body: CapabilityHeartbeatInput, session: SessionDep
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    accepted = 0
    for item in body.evidence:
        runtime = session.get(RuntimeInstance, item.runtime_instance_id)
        if (
            runtime is None
            or runtime.location_type != RuntimeLocationType.PLATFORM
            or runtime.engine_type != item.engine_type
            or runtime.applied_configuration_revision_id is None
            or runtime.current_capability_report_id is None
        ):
            continue
        configuration = session.get(
            RuntimeConfigurationRevision, runtime.applied_configuration_revision_id
        )
        capability = session.get(
            RuntimeCapabilityReport, runtime.current_capability_report_id
        )
        if (
            configuration is None
            or capability is None
            or capability.runtime_instance_id != runtime.id
            or configuration.configuration_digest != item.configuration_digest
            or capability.configuration_digest != item.configuration_digest
            or capability.capability_fingerprint != item.capability_fingerprint
            or capability.engine_version != item.engine_version
            or capability.adapter_version != item.adapter_version
        ):
            continue
        capability.reported_at = now
        runtime.last_seen_at = now
        if runtime.enabled:
            runtime.status = RuntimeInstanceStatus.AVAILABLE
        session.add(capability)
        session.add(runtime)
        accepted += 1
    session.commit()
    return {"accepted": accepted}


@internal_router.post("/configurations/result")
def report_runtime_configuration_result(
    body: ConfigurationResultInput, session: SessionDep
) -> dict[str, Any]:
    runtime = session.get(RuntimeInstance, body.runtime_instance_id)
    configuration = session.exec(
        select(RuntimeConfigurationRevision)
        .where(RuntimeConfigurationRevision.id == body.configuration_revision_id)
        .with_for_update()
    ).first()
    if (
        runtime is None
        or configuration is None
        or configuration.runtime_instance_id != runtime.id
        or runtime.desired_configuration_revision_id != configuration.id
        or configuration.status != RuntimeConfigurationStatus.APPLYING
        or configuration.error != {"apply_worker_id": body.worker_id}
        or configuration.configuration_digest != body.configuration_digest
    ):
        raise HTTPException(409, "Runtime configuration result scope mismatch")
    if body.status == "failed":
        if body.error is None:
            raise HTTPException(422, "Failed configuration result requires error")
        configuration.status = RuntimeConfigurationStatus.FAILED
        configuration.error = body.error
        session.add(configuration)
        session.commit()
        return {"status": configuration.status.value}
    if body.status != "applied" or body.error is not None:
        raise HTTPException(422, "Invalid Runtime configuration result")
    now = datetime.now(timezone.utc)
    service_bootstrap: NodeBootstrapSession | None = None
    service_receipt: NodeInstallationReceipt | None = None
    adapter_release_id: uuid.UUID | None = None
    if runtime.management_type == RuntimeManagementType.SERVICE_MANAGED:
        node = (
            session.get(RuntimeNode, runtime.runtime_node_id)
            if runtime.runtime_node_id is not None
            else None
        )
        service_receipt = (
            session.get(NodeInstallationReceipt, node.current_installation_receipt_id)
            if node is not None and node.current_installation_receipt_id is not None
            else None
        )
        component_versions = {
            item.get("name"): item.get("version")
            for item in (service_receipt.components if service_receipt else [])
            if isinstance(item, dict)
        }
        if (
            service_receipt is None
            or body.capabilities.get("distribution_manifest_digest")
            != service_receipt.manifest_digest
            or body.capabilities.get("sdk_version")
            != component_versions.get("claude-agent-sdk")
            or body.adapter_version != component_versions.get("runtime-adapter")
        ):
            configuration.status = RuntimeConfigurationStatus.FAILED
            configuration.error = {"code": "service_distribution_evidence_mismatch"}
            runtime.status = RuntimeInstanceStatus.INCOMPATIBLE
            session.add(configuration)
            session.add(runtime)
            session.commit()
            raise HTTPException(409, "service distribution evidence mismatch")
        service_bootstrap = (
            session.get(NodeBootstrapSession, service_receipt.bootstrap_session_id)
            if service_receipt.bootstrap_session_id is not None
            else None
        )
    elif runtime.management_type == RuntimeManagementType.CLIENT_DISCOVERED:
        discovered_capability = (
            session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
            if runtime.current_capability_report_id is not None
            else None
        )
        adapter_release = (
            session.get(RuntimeAdapterRelease, discovered_capability.adapter_release_id)
            if discovered_capability is not None
            and discovered_capability.adapter_release_id is not None
            else None
        )
        if (
            adapter_release is None
            or adapter_release.engine_type != runtime.engine_type
            or adapter_release.version != body.adapter_version
            or body.capabilities.get("adapter_release_digest")
            != adapter_release.release_digest
            or body.capabilities.get("native_login_ready") is not True
        ):
            configuration.status = RuntimeConfigurationStatus.FAILED
            configuration.error = {"code": "client_adapter_evidence_mismatch"}
            runtime.status = RuntimeInstanceStatus.INCOMPATIBLE
            session.add(configuration)
            session.add(runtime)
            session.commit()
            raise HTTPException(409, "client Adapter evidence mismatch")
        adapter_release_id = adapter_release.id
    generation = (
        len(
            session.exec(
                select(RuntimeCapabilityReport).where(
                    RuntimeCapabilityReport.runtime_instance_id == runtime.id
                )
            ).all()
        )
        + 1
    )
    capability_payload = {
        "engine_type": runtime.engine_type.value,
        "engine_version": body.engine_version,
        "adapter_version": body.adapter_version,
        "configuration_digest": configuration.configuration_digest,
        "capabilities": body.capabilities,
        "discovered_models": body.discovered_models,
    }
    report = RuntimeCapabilityReport(
        runtime_instance_id=runtime.id,
        generation=generation,
        engine_version=body.engine_version,
        adapter_version=body.adapter_version,
        adapter_release_id=adapter_release_id,
        configuration_digest=configuration.configuration_digest,
        capabilities=body.capabilities,
        discovered_models=body.discovered_models,
        capability_fingerprint=canonical_digest(capability_payload),
    )
    session.add(report)
    session.flush()
    configuration.status = RuntimeConfigurationStatus.APPLIED
    configuration.error = None
    configuration.applied_at = now
    runtime.applied_configuration_revision_id = configuration.id
    runtime.current_capability_report_id = report.id
    runtime.engine_version = body.engine_version
    runtime.adapter_version = body.adapter_version
    runtime.status = (
        RuntimeInstanceStatus.UNAVAILABLE
        if runtime.management_type == RuntimeManagementType.CLIENT_DISCOVERED
        else RuntimeInstanceStatus.AVAILABLE
    )
    runtime.last_seen_at = now
    runtime.updated_at = now
    session.add(configuration)
    session.add(runtime)
    if runtime.management_type == RuntimeManagementType.PLATFORM_BUILTIN:
        enqueue_platform_reconcile_job(
            session,
            runtime.namespace_id,
            trigger="platform_worker_evidence_updated",
        )
    elif (
        runtime.management_type == RuntimeManagementType.SERVICE_MANAGED
        and service_bootstrap is not None
        and service_receipt is not None
    ):
        service_bootstrap.status = BootstrapStatus.RECONCILED
        service_bootstrap.completed_at = now
        attempt_no = (
            len(
                session.exec(
                    select(NodeBootstrapAttempt).where(
                        NodeBootstrapAttempt.bootstrap_session_id
                        == service_bootstrap.id
                    )
                ).all()
            )
            + 1
        )
        session.add(
            NodeBootstrapAttempt(
                bootstrap_session_id=service_bootstrap.id,
                attempt_no=attempt_no,
                stage=BootstrapStatus.RECONCILED,
                manifest_digest=service_receipt.manifest_digest,
                host_facts={"runtime_instance_id": str(runtime.id)},
            )
        )
        session.add(service_bootstrap)
    reconcile_platform_model_bindings(session, runtime.namespace_id)
    session.commit()
    return {
        "status": configuration.status.value,
        "runtime_capability_report_id": report.id,
        "capability_fingerprint": report.capability_fingerprint,
    }


@internal_router.post("/model-validations/result")
def report_native_model_validation_result(
    body: NativeModelValidationResult, session: SessionDep
) -> dict[str, Any]:
    binding = session.get(RuntimeModelBinding, body.runtime_model_binding_id)
    attempt = session.exec(
        select(RuntimeModelValidationAttempt)
        .where(
            RuntimeModelValidationAttempt.runtime_model_binding_id
            == body.runtime_model_binding_id,
            RuntimeModelValidationAttempt.attempt_no == body.attempt_no,
        )
        .with_for_update()
    ).first()
    if (
        binding is None
        or attempt is None
        or attempt.status != "running"
        or attempt.error != {"worker_id": body.worker_id}
        or binding.route_type != RuntimeModelRouteType.RUNTIME_NATIVE
        or binding.engine_model_id != body.engine_model_id
        or binding.route_key != body.route_key
    ):
        raise HTTPException(409, "Native model validation scope mismatch")
    attempt.completed_at = datetime.now(timezone.utc)
    if body.status == "succeeded":
        if body.error is not None:
            raise HTTPException(422, "Successful validation cannot include error")
        runtime = session.get(RuntimeInstance, binding.runtime_instance_id)
        if runtime is None:
            raise HTTPException(409, "Runtime model validation target is missing")
        configuration = (
            session.get(
                RuntimeConfigurationRevision,
                runtime.applied_configuration_revision_id,
            )
            if runtime.applied_configuration_revision_id is not None
            else None
        )
        capability = (
            session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
            if runtime.current_capability_report_id is not None
            else None
        )
        if (
            configuration is None
            or capability is None
            or capability.configuration_digest != configuration.configuration_digest
            or capability.reported_at
            < datetime.now(timezone.utc) - timedelta(minutes=5)
        ):
            raise HTTPException(409, "Runtime evidence is not current")
        evidence = {
            "runtime_model_binding_id": str(binding.id),
            "engine_model_id": body.engine_model_id,
            "route_key": body.route_key,
            "evidence": body.evidence,
        }
        digest = canonical_digest(evidence)
        attempt.status = "succeeded"
        attempt.evidence_digest = digest
        binding.status = RuntimeModelBindingStatus.AVAILABLE
        binding.validation_fingerprint = digest
        binding.last_validated_at = attempt.completed_at
        binding.validation_expires_at = attempt.completed_at + timedelta(hours=24)
        binding.validated_capability_fingerprint = capability.capability_fingerprint
        binding.last_error = None
        runtime.status = RuntimeInstanceStatus.AVAILABLE
        runtime.updated_at = datetime.now(timezone.utc)
        session.add(runtime)
        if (
            runtime.management_type == RuntimeManagementType.CLIENT_DISCOVERED
            and runtime.runtime_node_id is not None
        ):
            node = session.get(RuntimeNode, runtime.runtime_node_id)
            receipt = (
                session.get(
                    NodeInstallationReceipt,
                    node.current_installation_receipt_id,
                )
                if node is not None
                and node.management_mode == RuntimeNodeMode.CLIENT
                and node.current_installation_receipt_id is not None
                else None
            )
            bootstrap = (
                session.get(NodeBootstrapSession, receipt.bootstrap_session_id)
                if receipt is not None and receipt.bootstrap_session_id is not None
                else None
            )
            if bootstrap is not None and bootstrap.status == BootstrapStatus.STAGED:
                assert receipt is not None
                bootstrap.status = BootstrapStatus.RECONCILED
                bootstrap.completed_at = attempt.completed_at
                attempt_no = (
                    len(
                        session.exec(
                            select(NodeBootstrapAttempt).where(
                                NodeBootstrapAttempt.bootstrap_session_id
                                == bootstrap.id
                            )
                        ).all()
                    )
                    + 1
                )
                session.add(
                    NodeBootstrapAttempt(
                        bootstrap_session_id=bootstrap.id,
                        attempt_no=attempt_no,
                        stage=BootstrapStatus.RECONCILED,
                        manifest_digest=receipt.manifest_digest,
                        host_facts={
                            "runtime_instance_id": str(runtime.id),
                            "runtime_model_binding_id": str(binding.id),
                        },
                    )
                )
                session.add(bootstrap)
    elif body.status == "failed" and body.error is not None:
        attempt.status = "failed"
        attempt.error = body.error
        binding.status = RuntimeModelBindingStatus.FAILED
        binding.last_error = body.error
    else:
        raise HTTPException(422, "Invalid native model validation result")
    binding.updated_at = datetime.now(timezone.utc)
    session.add(attempt)
    session.add(binding)
    session.commit()
    return _binding_public(session, binding)


@internal_router.post("/model-validations/claim")
def claim_native_model_validation(
    body: ConfigurationClaimInput, session: SessionDep
) -> dict[str, Any]:
    attempt = session.exec(
        select(RuntimeModelValidationAttempt)
        .join(
            RuntimeModelBinding,
            col(RuntimeModelValidationAttempt.runtime_model_binding_id)
            == RuntimeModelBinding.id,
        )
        .join(
            RuntimeInstance,
            col(RuntimeModelBinding.runtime_instance_id) == RuntimeInstance.id,
        )
        .where(
            RuntimeModelValidationAttempt.status == "pending",
            RuntimeModelBinding.route_type == RuntimeModelRouteType.RUNTIME_NATIVE,
            RuntimeInstance.location_type == RuntimeLocationType.PLATFORM,
            col(RuntimeInstance.enabled).is_(True),
        )
        .order_by(col(RuntimeModelValidationAttempt.started_at))
        .with_for_update(skip_locked=True)
    ).first()
    if attempt is None:
        raise HTTPException(204)
    binding = session.get(RuntimeModelBinding, attempt.runtime_model_binding_id)
    runtime = (
        session.get(RuntimeInstance, binding.runtime_instance_id) if binding else None
    )
    if binding is None or runtime is None:
        raise HTTPException(409, "Native model validation target is missing")
    attempt.status = "running"
    attempt.error = {"worker_id": body.worker_id}
    session.add(attempt)
    session.commit()
    return {
        "runtime_instance_id": runtime.id,
        "engine_type": runtime.engine_type.value,
        "engine_version": runtime.engine_version,
        "adapter_version": runtime.adapter_version,
        "runtime_model_binding_id": binding.id,
        "attempt_no": attempt.attempt_no,
        "engine_model_id": binding.engine_model_id,
        "route_key": binding.route_key,
    }
