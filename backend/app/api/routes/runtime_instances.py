"""v0.9 Runtime instance, configuration, capability, and model catalog APIs."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.llm_provider_service import open_secret_payload, sanitize_error_text
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
    model_catalog_fingerprint,
    validate_model_binding_route,
)
from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind
from app.runtime.models import (
    RuntimeCapabilityReport,
    RuntimeConfigurationRevision,
    RuntimeConfigurationStatus,
    RuntimeEngineType,
    RuntimeInstance,
    RuntimeInstanceStatus,
    RuntimeLocationType,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
    RuntimeModelValidationAttempt,
    RuntimeNode,
)
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
    discovered_models: list[dict[str, Any]] = []


class RuntimeDiscoveryReport(StrictBody):
    node_id: uuid.UUID
    generation: int = Field(ge=1)
    installations: list[DiscoveryInstallation]


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
    return {
        "id": runtime.id,
        "namespace_id": runtime.namespace_id,
        "runtime_node_id": runtime.runtime_node_id,
        "location_type": runtime.location_type.value,
        "name": runtime.name,
        "installation_key": runtime.installation_key,
        "engine_type": runtime.engine_type.value,
        "engine_version": runtime.engine_version,
        "adapter_version": runtime.adapter_version,
        "status": runtime.status.value,
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
    statement = select(RuntimeInstance).where(
        RuntimeInstance.namespace_id == namespace_id,
        RuntimeInstance.enabled.is_(True),
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
    if runtime.runtime_node_id != node_id or runtime.location_type != RuntimeLocationType.NODE:
        raise HTTPException(404, "Runtime does not belong to this Node")
    if runtime.status == RuntimeInstanceStatus.INCOMPATIBLE:
        raise HTTPException(409, "Incompatible Runtime cannot be enabled")
    if runtime.desired_configuration_revision_id is None:
        raise HTTPException(409, "Runtime configuration must be created before enablement")
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
    body: PlatformRuntimeCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    existing = session.exec(
        select(RuntimeInstance).where(
            RuntimeInstance.namespace_id == namespace_id,
            RuntimeInstance.location_type == RuntimeLocationType.PLATFORM,
            RuntimeInstance.installation_key == body.installation_key,
        )
    ).first()
    if existing:
        expected = existing.executable_fingerprint
        fingerprint = canonical_digest(
            {"idempotency_key": idempotency_key, "body": body.model_dump(mode="json")}
        )
        if expected != fingerprint:
            raise HTTPException(409, "Platform Runtime installation_key already exists")
        return _runtime_public(session, existing)
    fingerprint = canonical_digest(
        {"idempotency_key": idempotency_key, "body": body.model_dump(mode="json")}
    )
    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        location_type=RuntimeLocationType.PLATFORM,
        name=body.name,
        installation_key=body.installation_key,
        engine_type=body.engine_type,
        engine_version=body.engine_version,
        adapter_version=body.adapter_version,
        executable_fingerprint=fingerprint,
        status=RuntimeInstanceStatus.DISCOVERED,
        enabled=True,
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(runtime)
    session.flush()
    configuration = _configuration_from_input(
        runtime.id, 1, body.configuration, current_user.id
    )
    session.add(configuration)
    session.flush()
    runtime.desired_configuration_revision_id = configuration.id
    session.add(runtime)
    session.commit()
    return _runtime_public(session, runtime)


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
    latest = session.exec(
        select(RuntimeConfigurationRevision)
        .where(RuntimeConfigurationRevision.runtime_instance_id == runtime.id)
        .order_by(col(RuntimeConfigurationRevision.revision).desc())
    ).first()
    actual = latest.revision if latest else 0
    if body.expected_revision != actual:
        raise HTTPException(409, {"expected_revision": body.expected_revision, "actual_revision": actual})
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
    return {"id": row.id, "revision": row.revision, "configuration_digest": row.configuration_digest, "status": row.status.value}


@router.post("/runtimes/{runtime_id}/configuration/apply", status_code=202)
def apply_runtime_configuration(
    runtime_id: uuid.UUID,
    body: ConfigurationApplyInput,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    runtime = _runtime_or_404(session, runtime_id, namespace_id)
    configuration = session.get(
        RuntimeConfigurationRevision, body.configuration_revision_id
    )
    if (
        configuration is None
        or configuration.runtime_instance_id != runtime.id
        or runtime.desired_configuration_revision_id != configuration.id
    ):
        raise HTTPException(409, "Only the current desired configuration can be applied")
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
        .order_by(col(LlmModelDefinition.provider_family), col(LlmModelDefinition.model_key))
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


def _validate_provider_model_call(
    provider: LlmProviderConfig, model_id: str
) -> dict[str, Any]:
    try:
        kind = gateway_provider_kind(provider.provider_slug)
        secrets = open_secret_payload(provider.secret_ciphertext)
        api_key = secrets.get("api_key") or secrets.get("api_token") or secrets.get(
            "token"
        )
        if not api_key:
            raise RuntimeCatalogError(
                "provider_credential_missing", "Provider credential is missing"
            )
        base_url = provider.base_url.rstrip("/")
        if kind == "anthropic":
            url = (
                f"{base_url}/messages"
                if base_url.endswith("/v1")
                else f"{base_url}/v1/messages"
            )
            response = httpx.post(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model_id,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
                timeout=30,
            )
        else:
            url = (
                f"{base_url}/chat/completions"
                if base_url.endswith("/v1")
                else f"{base_url}/v1/chat/completions"
            )
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
                timeout=30,
            )
        response.raise_for_status()
        return {
            "provider_kind": kind,
            "status_code": response.status_code,
            "model_id": model_id,
        }
    except UnsupportedGatewayProvider as exc:
        raise RuntimeCatalogError(
            "provider_adapter_unsupported", str(exc)
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeCatalogError(
            "provider_model_validation_failed",
            sanitize_error_text(str(exc)),
        ) from exc


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
    definition = session.get(LlmModelDefinition, body.model_definition_id)
    if definition is None or definition.namespace_id != namespace_id or not definition.enabled:
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
    if row is None or row.runtime_instance_id != runtime_id or row.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime model binding not found")
    last_attempt = session.exec(
        select(RuntimeModelValidationAttempt)
        .where(RuntimeModelValidationAttempt.runtime_model_binding_id == row.id)
        .order_by(col(RuntimeModelValidationAttempt.attempt_no).desc())
    ).first()
    attempt_no = (last_attempt.attempt_no if last_attempt else 0) + 1
    try:
        _, capability, _ = current_runtime_evidence(
            session, runtime, include_catalog=False
        )
        validate_model_binding_route(session, row)
        if row.provider_config_id:
            provider = session.get(LlmProviderConfig, row.provider_config_id)
            if provider is None or provider.validation_status != ProviderValidationStatus.SUCCESS:
                raise RuntimeCatalogError(
                    "provider_not_validated",
                    "Provider Config must pass connection validation before binding validation",
                )
            evidence = _validate_provider_model_call(provider, row.engine_model_id)
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
    if row is None or row.runtime_instance_id != runtime_id or row.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime model binding not found")
    row.status = RuntimeModelBindingStatus.DISABLED
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return _binding_public(session, row)


@internal_router.post("/discovery")
def report_runtime_discovery(
    body: RuntimeDiscoveryReport,
    session: SessionDep,
) -> dict[str, Any]:
    node = session.get(RuntimeNode, body.node_id)
    if node is None or node.revoked_at is not None:
        raise HTTPException(404, "Active Runtime Node not found")
    report_digest = canonical_digest(body.model_dump(mode="json", exclude={"node_id"}))
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
        if runtime is None:
            runtime = RuntimeInstance(
                namespace_id=node.namespace_id,
                runtime_node_id=node.id,
                location_type=RuntimeLocationType.NODE,
                name=item.name,
                installation_key=item.installation_key,
                engine_type=item.engine_type,
                engine_version=item.engine_version,
                adapter_version=item.adapter_version,
                executable_fingerprint=item.executable_fingerprint,
                status=RuntimeInstanceStatus.DISCOVERED,
                enabled=False,
            )
            session.add(runtime)
            session.flush()
        elif runtime.engine_type != item.engine_type or (
            runtime.executable_fingerprint
            and runtime.executable_fingerprint != item.executable_fingerprint
        ):
            runtime.status = RuntimeInstanceStatus.INCOMPATIBLE
            session.add(runtime)
            continue
        runtime.name = item.name
        runtime.engine_version = item.engine_version
        runtime.adapter_version = item.adapter_version
        runtime.last_seen_at = datetime.now(timezone.utc)
        if runtime.enabled:
            runtime.status = RuntimeInstanceStatus.AVAILABLE
        session.add(runtime)
        configuration_digest = "0" * 64
        if runtime.applied_configuration_revision_id:
            configuration = session.get(
                RuntimeConfigurationRevision, runtime.applied_configuration_revision_id
            )
            if configuration:
                configuration_digest = configuration.configuration_digest
        generation = len(
            session.exec(
                select(RuntimeCapabilityReport).where(
                    RuntimeCapabilityReport.runtime_instance_id == runtime.id
                )
            ).all()
        ) + 1
        report = RuntimeCapabilityReport(
            runtime_instance_id=runtime.id,
            generation=generation,
            engine_version=item.engine_version,
            adapter_version=item.adapter_version,
            configuration_digest=configuration_digest,
            capabilities=item.capabilities,
            discovered_models=item.discovered_models,
            capability_fingerprint=canonical_digest(
                {
                    "engine_type": item.engine_type.value,
                    "engine_version": item.engine_version,
                    "adapter_version": item.adapter_version,
                    "configuration_digest": configuration_digest,
                    "capabilities": item.capabilities,
                    "discovered_models": item.discovered_models,
                }
            ),
        )
        session.add(report)
        session.flush()
        runtime.current_capability_report_id = report.id
        session.add(runtime)
        accepted += 1
    known = session.exec(
        select(RuntimeInstance).where(RuntimeInstance.runtime_node_id == node.id)
    ).all()
    for runtime in known:
        if runtime.installation_key not in seen_keys:
            runtime.status = RuntimeInstanceStatus.UNAVAILABLE
            session.add(runtime)
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
            col(RuntimeConfigurationRevision.runtime_instance_id)
            == RuntimeInstance.id,
        )
        .where(
            RuntimeConfigurationRevision.status
            == RuntimeConfigurationStatus.DESIRED,
            RuntimeInstance.location_type == RuntimeLocationType.PLATFORM,
            RuntimeInstance.enabled.is_(True),
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
        .where(
            RuntimeConfigurationRevision.id == body.configuration_revision_id
        )
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
    generation = len(
        session.exec(
            select(RuntimeCapabilityReport).where(
                RuntimeCapabilityReport.runtime_instance_id == runtime.id
            )
        ).all()
    ) + 1
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
    runtime.status = RuntimeInstanceStatus.AVAILABLE
    runtime.last_seen_at = now
    runtime.updated_at = now
    session.add(configuration)
    session.add(runtime)
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
            RuntimeModelValidationAttempt.runtime_model_binding_id == body.runtime_model_binding_id,
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
        _, capability, _ = current_runtime_evidence(
            session, runtime, include_catalog=False
        )
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
            RuntimeInstance.enabled.is_(True),
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
