import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.llm_provider_service import open_secret_payload, seal_secret_payload
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.artifacts.signing import configured_artifact_signer
from app.runtime.capabilities import HarnessCapabilities
from app.runtime.catalog import canonical_digest
from app.runtime.connections import node_is_online
from app.runtime.endpoints import EndpointValidationError, canonical_endpoint
from app.runtime.enrollment import (
    EnrollmentTokenInvalid,
    consume_enrollment_token,
    create_enrollment_token,
    issue_node_credential,
    read_enrollment_token,
)
from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind
from app.runtime.models import (
    BootstrapStatus,
    NodeBootstrapAttempt,
    NodeBootstrapSession,
    NodeCredential,
    NodeDistributionRelease,
    NodeEnrollmentToken,
    NodeInstallationReceipt,
    RuntimeNode,
    RuntimeNodeMode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
    RuntimeType,
)
from app.runtime.policy import PermissionMode

admin_router = APIRouter(prefix="/runtimes/nodes", tags=["runtime-nodes"])
node_router = APIRouter(prefix="/node", tags=["node-enrollment"])

NODE_MANAGER_VERSION = "0.1.0"


class EnrollmentTokenCreated(BaseModel):
    id: uuid.UUID
    token: str
    expires_at: datetime
    management_mode: RuntimeNodeMode


class EnrollmentTokenPublic(BaseModel):
    id: uuid.UUID
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    management_mode: RuntimeNodeMode


class EnrollmentTokensPublic(BaseModel):
    data: list[EnrollmentTokenPublic]
    count: int


class NodeEnrollInput(BaseModel):
    token: str = Field(min_length=16)
    name: str = Field(min_length=1, max_length=255)
    hostname: str = Field(min_length=1, max_length=255)
    os_name: str = Field(min_length=1, max_length=128)
    architecture: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=64)
    sdk_version: str | None = Field(default=None, max_length=64)
    harness_capabilities: HarnessCapabilities
    public_key: str
    management_mode: RuntimeNodeMode | None = None
    proof: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=128)
    distribution_manifest_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )


class NodeEnrollResult(BaseModel):
    node_id: uuid.UUID
    namespace_id: uuid.UUID
    credential: str
    credential_expires_at: datetime


class NodePublic(BaseModel):
    id: uuid.UUID
    name: str
    hostname: str
    os_name: str
    architecture: str
    agent_version: str
    sdk_version: str | None
    harness_capabilities: dict[str, Any]
    online: bool
    last_seen_at: datetime | None
    revoked_at: datetime | None
    runtime_profile_id: uuid.UUID | None
    management_mode: RuntimeNodeMode
    adapter_registry_digest: str | None
    current_installation_receipt_id: uuid.UUID | None
    installation_manifest_digest: str | None
    bootstrap_session_id: uuid.UUID | None
    bootstrap_status: BootstrapStatus | None
    discovery_generation: int
    discovery_requested_generation: int


class BootstrapSessionCreate(BaseModel):
    management_mode: RuntimeNodeMode
    release_channel: str = Field(default="stable", min_length=1, max_length=64)


class BootstrapSessionCreated(EnrollmentTokenCreated):
    bootstrap_session_id: uuid.UUID
    release_channel: str
    status: BootstrapStatus
    signing_public_key: str


class BootstrapSessionPublic(BaseModel):
    id: uuid.UUID
    management_mode: RuntimeNodeMode
    release_channel: str
    distribution_release_id: uuid.UUID | None
    status: BootstrapStatus
    node_id: uuid.UUID | None
    expires_at: datetime
    completed_at: datetime | None


class BootstrapPreflightInput(BaseModel):
    token: str = Field(min_length=16)
    management_mode: RuntimeNodeMode
    os_name: str = Field(min_length=1, max_length=128)
    architecture: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=64)
    public_key: str
    service_manager: str
    available_disk_bytes: int = Field(ge=0)
    state_directory_atomic_rename: bool
    platform_tls_verified: bool
    clock_skew_seconds: int


class BootstrapPreflightResult(BaseModel):
    bootstrap_session_id: uuid.UUID
    release_id: uuid.UUID
    manifest: dict[str, Any]
    manifest_digest: str
    signature: str
    signing_public_key: str


class InstallationReceiptInput(BaseModel):
    node_id: uuid.UUID
    bootstrap_session_id: uuid.UUID
    distribution_release_id: uuid.UUID
    manifest_digest: str = Field(min_length=64, max_length=64)
    components: list[dict[str, Any]]
    logical_installation_ref: str = Field(min_length=1, max_length=255)
    first_applied_at: datetime
    device_signature: str


class InstallationReceiptPublic(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    bootstrap_session_id: uuid.UUID | None
    distribution_release_id: uuid.UUID | None
    receipt_digest: str
    manifest_digest: str
    logical_installation_ref: str
    first_applied_at: datetime
    last_verified_at: datetime


class BootstrapStageInput(BaseModel):
    node_id: uuid.UUID
    bootstrap_session_id: uuid.UUID
    stage: BootstrapStatus
    error: dict[str, Any] | None = None
    device_signature: str


class NodesPublic(BaseModel):
    data: list[NodePublic]
    count: int


class NodeRuntimeUpsert(BaseModel):
    route_mode: RuntimeRouteMode
    model_id: str = Field(min_length=1, max_length=255)
    provider_config_id: uuid.UUID | None = None
    base_url: str | None = None
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    secret_inputs: dict[str, str] | None = None


class NodeRuntimePublic(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    route_mode: RuntimeRouteMode
    model_id: str
    provider_config_id: uuid.UUID | None
    base_url: str | None
    permission_mode: PermissionMode
    secret_masked: str | None


def _public_token(record: NodeEnrollmentToken) -> EnrollmentTokenPublic:
    return EnrollmentTokenPublic(
        id=record.id,
        expires_at=record.expires_at,
        consumed_at=record.consumed_at,
        revoked_at=record.revoked_at,
        management_mode=record.requested_management_mode,
    )


def _public_node(session: SessionDep, node: RuntimeNode) -> NodePublic:
    receipt = (
        session.get(NodeInstallationReceipt, node.current_installation_receipt_id)
        if node.current_installation_receipt_id is not None
        else None
    )
    bootstrap = (
        session.get(NodeBootstrapSession, receipt.bootstrap_session_id)
        if receipt is not None and receipt.bootstrap_session_id is not None
        else None
    )
    return NodePublic(
        id=node.id,
        name=node.name,
        hostname=node.hostname,
        os_name=node.os_name,
        architecture=node.architecture,
        agent_version=node.agent_version,
        sdk_version=node.sdk_version,
        harness_capabilities=node.harness_capabilities,
        online=node_is_online(node),
        last_seen_at=node.last_seen_at,
        revoked_at=node.revoked_at,
        runtime_profile_id=node.runtime_profile_id,
        management_mode=node.management_mode,
        adapter_registry_digest=node.adapter_registry_digest,
        current_installation_receipt_id=node.current_installation_receipt_id,
        installation_manifest_digest=receipt.manifest_digest if receipt else None,
        bootstrap_session_id=bootstrap.id if bootstrap else None,
        bootstrap_status=bootstrap.status if bootstrap else None,
        discovery_generation=node.discovery_generation,
        discovery_requested_generation=node.discovery_requested_generation,
    )


@admin_router.post(
    "/enrollment-tokens", response_model=EnrollmentTokenCreated, status_code=201
)
def create_token(
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> EnrollmentTokenCreated:
    raw, record = create_enrollment_token(session, namespace_id, current_user.id)
    return EnrollmentTokenCreated(
        id=record.id,
        token=raw,
        expires_at=record.expires_at,
        management_mode=record.requested_management_mode,
    )


@admin_router.post(
    "/bootstrap-sessions", response_model=BootstrapSessionCreated, status_code=201
)
def create_bootstrap_session(
    body: BootstrapSessionCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> BootstrapSessionCreated:
    if body.management_mode not in {RuntimeNodeMode.SERVICE, RuntimeNodeMode.CLIENT}:
        raise HTTPException(422, "bootstrap mode must be service or client")
    raw, record = create_enrollment_token(
        session,
        namespace_id,
        current_user.id,
        management_mode=body.management_mode,
        commit=False,
    )
    bootstrap = NodeBootstrapSession(
        namespace_id=namespace_id,
        enrollment_token_id=record.id,
        management_mode=body.management_mode,
        release_channel=body.release_channel,
        status=BootstrapStatus.WAITING_FOR_INSTALL,
        created_by=current_user.id,
        expires_at=record.expires_at,
    )
    session.add(bootstrap)
    session.commit()
    return BootstrapSessionCreated(
        id=record.id,
        token=raw,
        expires_at=record.expires_at,
        management_mode=record.requested_management_mode,
        bootstrap_session_id=bootstrap.id,
        release_channel=bootstrap.release_channel,
        status=bootstrap.status,
        signing_public_key=configured_artifact_signer().public_key(),
    )


@admin_router.get("/bootstrap-sessions", response_model=list[BootstrapSessionPublic])
def list_bootstrap_sessions(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> list[BootstrapSessionPublic]:
    records = session.exec(
        select(NodeBootstrapSession)
        .where(NodeBootstrapSession.namespace_id == namespace_id)
        .order_by(col(NodeBootstrapSession.created_at).desc())
    ).all()
    return [
        BootstrapSessionPublic(
            id=record.id,
            management_mode=record.management_mode,
            release_channel=record.release_channel,
            distribution_release_id=record.distribution_release_id,
            status=record.status,
            node_id=record.node_id,
            expires_at=record.expires_at,
            completed_at=record.completed_at,
        )
        for record in records
    ]


@admin_router.get("/enrollment-tokens", response_model=EnrollmentTokensPublic)
def list_tokens(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> EnrollmentTokensPublic:
    records = session.exec(
        select(NodeEnrollmentToken)
        .where(NodeEnrollmentToken.namespace_id == namespace_id)
        .order_by(col(NodeEnrollmentToken.created_at).desc())
    ).all()
    return EnrollmentTokensPublic(
        data=[_public_token(record) for record in records], count=len(records)
    )


@admin_router.get("", response_model=NodesPublic)
def list_nodes(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> NodesPublic:
    nodes = session.exec(
        select(RuntimeNode)
        .where(RuntimeNode.namespace_id == namespace_id)
        .order_by(col(RuntimeNode.created_at).desc())
    ).all()
    return NodesPublic(
        data=[_public_node(session, node) for node in nodes], count=len(nodes)
    )


@admin_router.get("/{node_id}", response_model=NodePublic)
def read_node(
    node_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> NodePublic:
    node = session.get(RuntimeNode, node_id)
    if node is None or node.namespace_id != namespace_id:
        raise HTTPException(404, "Node not found")
    return _public_node(session, node)


@admin_router.put("/{node_id}/runtime", response_model=NodeRuntimePublic)
def configure_node_runtime(
    node_id: uuid.UUID,
    body: NodeRuntimeUpsert,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> NodeRuntimePublic:
    raise HTTPException(410, "RuntimeProfile writes are read-only history in v0.9")
    node = session.get(RuntimeNode, node_id)
    if node is None or node.namespace_id != namespace_id or node.revoked_at is not None:
        raise HTTPException(404, "Active node not found")
    existing_secret = None
    runtime = (
        session.get(RuntimeProfile, node.runtime_profile_id)
        if node.runtime_profile_id
        else None
    )
    if node.runtime_profile_id:
        existing_secret = session.exec(
            select(RuntimeSecret).where(
                RuntimeSecret.runtime_profile_id == node.runtime_profile_id
            )
        ).first()
    canonical_base_url = None
    if body.base_url:
        try:
            canonical_base_url = canonical_endpoint(body.base_url)
        except EndpointValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
    endpoint_changed = bool(runtime and runtime.base_url != canonical_base_url)
    if body.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        if not canonical_base_url or (
            not body.secret_inputs and (existing_secret is None or endpoint_changed)
        ):
            raise HTTPException(
                400,
                "direct_anthropic requires an Anthropic-compatible URL and credential",
            )
    else:
        provider = session.get(LlmProviderConfig, body.provider_config_id)
        if (
            provider is None
            or provider.namespace_id != namespace_id
            or not provider.enabled
        ):
            raise HTTPException(404, "Enabled provider config not found")
        try:
            gateway_provider_kind(provider.provider_slug)
        except UnsupportedGatewayProvider as exc:
            raise HTTPException(422, str(exc)) from exc
        model = session.exec(
            select(LlmProviderModel).where(
                LlmProviderModel.provider_config_id == provider.id,
                LlmProviderModel.model_id == body.model_id,
                col(LlmProviderModel.is_enabled).is_(True),
            )
        ).first()
        if model is None:
            raise HTTPException(400, "Enabled model not found")
    if runtime is None:
        runtime = RuntimeProfile(
            namespace_id=namespace_id,
            runtime_type=RuntimeType.NODE,
            route_mode=body.route_mode,
            model_id=body.model_id,
        )
        session.add(runtime)
        session.flush()
        node.runtime_profile_id = runtime.id
    runtime.route_mode = body.route_mode
    runtime.model_id = body.model_id
    runtime.provider_config_id = body.provider_config_id
    runtime.base_url = canonical_base_url
    runtime.permission_mode = body.permission_mode.value
    runtime.config = {
        **runtime.config,
        "direct_compatibility_verified": False
        if body.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC
        else None,
    }
    if body.secret_inputs:
        secret = existing_secret or RuntimeSecret(
            namespace_id=namespace_id,
            runtime_profile_id=runtime.id,
            secret_ciphertext="",
        )
        secret.secret_ciphertext = seal_secret_payload(body.secret_inputs) or ""
        secret.secret_masked = "****"
        session.add(secret)
        existing_secret = secret
    node.config_revision += 1
    session.add(runtime)
    session.add(node)
    session.commit()
    return NodeRuntimePublic(
        id=runtime.id,
        node_id=node.id,
        route_mode=runtime.route_mode,
        model_id=runtime.model_id,
        provider_config_id=runtime.provider_config_id,
        base_url=runtime.base_url,
        permission_mode=PermissionMode(runtime.permission_mode),
        secret_masked=existing_secret.secret_masked if existing_secret else None,
    )


@admin_router.delete("/{node_id}/credential", status_code=204)
def revoke_node(
    node_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    node = session.get(RuntimeNode, node_id)
    if node is None or node.namespace_id != namespace_id:
        raise HTTPException(404, "Node not found")
    now = datetime.now(timezone.utc)
    node.revoked_at = now
    node.connection_id = None
    credentials = session.exec(
        select(NodeCredential).where(
            NodeCredential.node_id == node.id,
            col(NodeCredential.revoked_at).is_(None),
        )
    ).all()
    for credential in credentials:
        credential.revoked_at = now
        session.add(credential)
    session.add(node)
    session.commit()


@admin_router.delete("/enrollment-tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    record = session.get(NodeEnrollmentToken, token_id)
    if record is None or record.namespace_id != namespace_id:
        raise HTTPException(404, "Enrollment token not found")
    if record.consumed_at is not None:
        raise HTTPException(409, "Consumed enrollment token cannot be revoked")
    record.revoked_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()


def _decode_public_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise HTTPException(422, "public_key must be base64 encoded") from exc
    if len(decoded) != 32:
        raise HTTPException(422, "public_key must be a 32-byte Ed25519 key")
    return decoded


def _verify_device_proof(public_key: str, proof: str | None, payload: str) -> None:
    if proof is None:
        raise HTTPException(422, "device proof is required for bootstrap enrollment")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(_decode_public_key(public_key)).verify(
            base64.b64decode(proof, validate=True), payload.encode()
        )
    except (ValueError, InvalidSignature) as exc:
        raise HTTPException(422, "device proof is invalid") from exc


def _validate_mode_capabilities(capabilities: HarnessCapabilities) -> None:
    reported = capabilities.model_dump(exclude_none=True)
    if reported:
        raise HTTPException(
            422,
            "bootstrap enrollment cannot claim Runtime capabilities before first reconcile",
        )


def _canonical_architecture(value: str) -> str:
    normalized = value.lower()
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(normalized, normalized)


def _bootstrap_session_for_token(
    session: SessionDep, token_id: uuid.UUID
) -> NodeBootstrapSession:
    bootstrap = session.exec(
        select(NodeBootstrapSession).where(
            NodeBootstrapSession.enrollment_token_id == token_id
        )
    ).first()
    if bootstrap is None:
        raise HTTPException(409, "bootstrap session is missing")
    return bootstrap


def _append_bootstrap_attempt(
    session: SessionDep,
    bootstrap: NodeBootstrapSession,
    stage: BootstrapStatus,
    *,
    host_facts: dict[str, Any] | None = None,
    manifest_digest: str | None = None,
    error: dict[str, Any] | None = None,
) -> NodeBootstrapAttempt:
    attempt_no = (
        len(
            session.exec(
                select(NodeBootstrapAttempt).where(
                    NodeBootstrapAttempt.bootstrap_session_id == bootstrap.id
                )
            ).all()
        )
        + 1
    )
    attempt = NodeBootstrapAttempt(
        bootstrap_session_id=bootstrap.id,
        attempt_no=attempt_no,
        stage=stage,
        host_facts=host_facts or {},
        manifest_digest=manifest_digest,
        error=error,
    )
    session.add(attempt)
    return attempt


@node_router.post("/bootstrap/preflight", response_model=BootstrapPreflightResult)
def bootstrap_preflight(
    body: BootstrapPreflightInput, session: SessionDep
) -> BootstrapPreflightResult:
    key_bytes = _decode_public_key(body.public_key)
    fingerprint = hashlib.sha256(key_bytes).hexdigest()
    try:
        record = read_enrollment_token(session, body.token)
    except EnrollmentTokenInvalid as exc:
        raise HTTPException(409, str(exc)) from exc
    now = datetime.now(timezone.utc)
    if (
        record.consumed_at is not None
        or record.revoked_at is not None
        or record.expires_at <= now
        or record.requested_management_mode != body.management_mode
    ):
        session.rollback()
        raise HTTPException(409, "bootstrap token is invalid, expired, or mode-bound")
    bootstrap = _bootstrap_session_for_token(session, record.id)
    if bootstrap.status not in {
        BootstrapStatus.WAITING_FOR_INSTALL,
        BootstrapStatus.PREFLIGHTED,
        BootstrapStatus.FAILED,
    }:
        session.rollback()
        raise HTTPException(409, "bootstrap session cannot be preflighted")
    if body.os_name.lower() != "linux":
        session.rollback()
        raise HTTPException(422, "unsupported_host: only Linux/systemd is supported")
    architecture = _canonical_architecture(body.architecture)
    if architecture not in {"amd64", "arm64"}:
        session.rollback()
        raise HTTPException(422, "unsupported_host: architecture is not supported")
    if body.service_manager != "systemd":
        session.rollback()
        raise HTTPException(422, "unsupported_host: systemd is required")
    if body.available_disk_bytes < 2 * 1024 * 1024 * 1024:
        session.rollback()
        raise HTTPException(422, "unsupported_host: insufficient disk space")
    if not body.state_directory_atomic_rename:
        session.rollback()
        raise HTTPException(
            422, "unsupported_host: atomic state activation is required"
        )
    if not body.platform_tls_verified:
        session.rollback()
        raise HTTPException(422, "unsupported_host: platform TLS verification failed")
    if abs(body.clock_skew_seconds) > 300:
        session.rollback()
        raise HTTPException(422, "unsupported_host: clock skew exceeds five minutes")
    if body.agent_version != NODE_MANAGER_VERSION:
        session.rollback()
        raise HTTPException(409, "unsupported_node_manager_version")
    if (
        record.bound_public_key_fingerprint is not None
        and record.bound_public_key_fingerprint != fingerprint
    ):
        session.rollback()
        raise HTTPException(409, "bootstrap token is bound to another device key")
    release = session.exec(
        select(NodeDistributionRelease).where(
            NodeDistributionRelease.channel == bootstrap.release_channel,
            NodeDistributionRelease.management_mode == body.management_mode,
            NodeDistributionRelease.os_name == "linux",
            NodeDistributionRelease.architecture == architecture,
            col(NodeDistributionRelease.active).is_(True),
        )
    ).first()
    if release is None:
        session.rollback()
        raise HTTPException(503, "distribution_release_unavailable")
    if release.manifest.get("node_manager_version") != body.agent_version:
        session.rollback()
        raise HTTPException(409, "unsupported_node_manager_version")
    record.preflight_at = now
    record.bound_public_key_fingerprint = fingerprint
    record.distribution_manifest_digest = release.manifest_digest
    bootstrap.bound_public_key_fingerprint = fingerprint
    bootstrap.distribution_release_id = release.id
    bootstrap.status = BootstrapStatus.PREFLIGHTED
    _append_bootstrap_attempt(
        session,
        bootstrap,
        BootstrapStatus.PREFLIGHTED,
        host_facts={
            "os_name": "linux",
            "architecture": architecture,
            "service_manager": body.service_manager,
            "available_disk_bytes": body.available_disk_bytes,
            "state_directory_atomic_rename": body.state_directory_atomic_rename,
            "platform_tls_verified": body.platform_tls_verified,
            "clock_skew_seconds": body.clock_skew_seconds,
        },
        manifest_digest=release.manifest_digest,
    )
    session.add(record)
    session.add(bootstrap)
    session.commit()
    return BootstrapPreflightResult(
        bootstrap_session_id=bootstrap.id,
        release_id=release.id,
        manifest=release.manifest,
        manifest_digest=release.manifest_digest,
        signature=release.signature,
        signing_public_key=release.signing_public_key,
    )


@node_router.get("/bootstrap/time")
def bootstrap_server_time() -> dict[str, str]:
    return {"server_time": datetime.now(timezone.utc).isoformat()}


@node_router.post(
    "/bootstrap/receipt",
    response_model=InstallationReceiptPublic,
    status_code=201,
)
def record_installation_receipt(
    body: InstallationReceiptInput, session: SessionDep
) -> InstallationReceiptPublic:
    node = session.get(RuntimeNode, body.node_id)
    bootstrap = session.get(NodeBootstrapSession, body.bootstrap_session_id)
    release = session.get(NodeDistributionRelease, body.distribution_release_id)
    if (
        node is None
        or bootstrap is None
        or release is None
        or bootstrap.node_id != node.id
        or bootstrap.distribution_release_id != release.id
        or bootstrap.status not in {BootstrapStatus.ENROLLED, BootstrapStatus.STAGED}
    ):
        raise HTTPException(409, "installation receipt scope mismatch")
    if (
        body.manifest_digest != release.manifest_digest
        or body.logical_installation_ref
        != release.manifest.get("logical_installation_ref")
        or canonical_digest(body.components)
        != canonical_digest(release.manifest.get("components", []))
    ):
        raise HTTPException(409, "installation receipt manifest mismatch")
    receipt_payload = {
        "node_id": str(node.id),
        "bootstrap_session_id": str(bootstrap.id),
        "distribution_release_id": str(release.id),
        "manifest_digest": body.manifest_digest,
        "components": body.components,
        "logical_installation_ref": body.logical_installation_ref,
        "first_applied_at": body.first_applied_at.isoformat(),
    }
    receipt_digest = canonical_digest(receipt_payload)
    _verify_device_proof(
        node.public_key,
        body.device_signature,
        f"neomua-installation-receipt-v1:{receipt_digest}",
    )
    existing = session.exec(
        select(NodeInstallationReceipt).where(
            NodeInstallationReceipt.node_id == node.id,
            NodeInstallationReceipt.receipt_digest == receipt_digest,
        )
    ).first()
    now = datetime.now(timezone.utc)
    receipt = existing or NodeInstallationReceipt(
        node_id=node.id,
        bootstrap_session_id=bootstrap.id,
        distribution_release_id=release.id,
        receipt_digest=receipt_digest,
        manifest_digest=body.manifest_digest,
        components=body.components,
        logical_installation_ref=body.logical_installation_ref,
        device_signature=body.device_signature,
        first_applied_at=body.first_applied_at,
        last_verified_at=now,
    )
    receipt.last_verified_at = now
    session.add(receipt)
    session.flush()
    node.current_installation_receipt_id = receipt.id
    bootstrap.status = BootstrapStatus.STAGED
    _append_bootstrap_attempt(
        session,
        bootstrap,
        BootstrapStatus.STAGED,
        manifest_digest=body.manifest_digest,
    )
    session.add(node)
    session.add(bootstrap)
    session.commit()
    return InstallationReceiptPublic.model_validate(receipt, from_attributes=True)


@node_router.post("/bootstrap/stage")
def report_bootstrap_stage(
    body: BootstrapStageInput, session: SessionDep
) -> dict[str, str]:
    node = session.get(RuntimeNode, body.node_id)
    bootstrap = session.get(NodeBootstrapSession, body.bootstrap_session_id)
    if (
        node is None
        or bootstrap is None
        or bootstrap.node_id != node.id
        or node.current_installation_receipt_id is None
    ):
        raise HTTPException(409, "bootstrap stage scope mismatch")
    if body.stage not in {BootstrapStatus.SERVICE_ACTIVATED, BootstrapStatus.FAILED}:
        raise HTTPException(422, "unsupported bootstrap stage report")
    if body.stage == BootstrapStatus.FAILED and body.error is None:
        raise HTTPException(422, "failed bootstrap stage requires an error")
    receipt = session.get(NodeInstallationReceipt, node.current_installation_receipt_id)
    if receipt is None:
        raise HTTPException(409, "bootstrap installation receipt is missing")
    payload = {
        "node_id": str(node.id),
        "bootstrap_session_id": str(bootstrap.id),
        "stage": body.stage.value,
        "error": body.error,
    }
    _verify_device_proof(
        node.public_key,
        body.device_signature,
        f"neomua-bootstrap-stage-v1:{canonical_digest(payload)}",
    )
    bootstrap.status = body.stage
    _append_bootstrap_attempt(
        session,
        bootstrap,
        body.stage,
        manifest_digest=receipt.manifest_digest,
        error=body.error,
    )
    session.add(bootstrap)
    session.commit()
    return {"status": bootstrap.status.value}


@node_router.post("/enroll", response_model=NodeEnrollResult, status_code=201)
def enroll_node(body: NodeEnrollInput, session: SessionDep) -> NodeEnrollResult:
    key_bytes = _decode_public_key(body.public_key)
    fingerprint = hashlib.sha256(key_bytes).hexdigest()
    try:
        enrollment_record = read_enrollment_token(session, body.token)
    except EnrollmentTokenInvalid as exc:
        raise HTTPException(409, str(exc)) from exc
    management_mode = enrollment_record.requested_management_mode
    bootstrap: NodeBootstrapSession | None = None
    enrollment_request_digest: str | None = None
    if management_mode != RuntimeNodeMode.LEGACY_UNCLASSIFIED:
        bootstrap = _bootstrap_session_for_token(session, enrollment_record.id)
        if body.idempotency_key is None:
            session.rollback()
            raise HTTPException(422, "bootstrap enrollment requires idempotency_key")
        if body.distribution_manifest_digest is None:
            session.rollback()
            raise HTTPException(
                422, "bootstrap enrollment requires distribution_manifest_digest"
            )
        enrollment_request_digest = canonical_digest(
            {
                "name": body.name,
                "hostname": body.hostname,
                "os_name": body.os_name,
                "architecture": _canonical_architecture(body.architecture),
                "agent_version": body.agent_version,
                "sdk_version": body.sdk_version,
                "harness_capabilities": body.harness_capabilities.model_dump(
                    exclude_none=True
                ),
                "public_key_fingerprint": fingerprint,
                "management_mode": management_mode.value,
                "idempotency_key": body.idempotency_key,
                "distribution_manifest_digest": body.distribution_manifest_digest,
            }
        )
        if enrollment_record.consumed_at is not None:
            if (
                bootstrap.enrollment_idempotency_key != body.idempotency_key
                or bootstrap.enrollment_request_digest != enrollment_request_digest
                or bootstrap.enrollment_response_ciphertext is None
                or bootstrap.node_id is None
            ):
                session.rollback()
                raise HTTPException(409, "bootstrap enrollment replay conflict")
            restored = open_secret_payload(bootstrap.enrollment_response_ciphertext)
            session.rollback()
            return NodeEnrollResult(
                node_id=bootstrap.node_id,
                namespace_id=bootstrap.namespace_id,
                credential=restored["credential"],
                credential_expires_at=datetime.fromisoformat(
                    restored["credential_expires_at"]
                ),
            )
        if body.management_mode != management_mode:
            session.rollback()
            raise HTTPException(409, "bootstrap enrollment mode mismatch")
        if (
            enrollment_record.preflight_at is None
            or enrollment_record.bound_public_key_fingerprint != fingerprint
            or enrollment_record.distribution_manifest_digest is None
            or enrollment_record.distribution_manifest_digest
            != body.distribution_manifest_digest
            or bootstrap.distribution_release_id is None
        ):
            session.rollback()
            raise HTTPException(409, "bootstrap preflight is required")
        _verify_device_proof(
            body.public_key,
            body.proof,
            (
                f"neomua-enroll-v2:{body.token}:{management_mode.value}:"
                f"{body.distribution_manifest_digest}:{body.idempotency_key}"
            ),
        )
        _validate_mode_capabilities(body.harness_capabilities)
        session.rollback()
    existing = session.exec(
        select(RuntimeNode).where(RuntimeNode.key_fingerprint == fingerprint)
    ).first()
    if existing:
        raise HTTPException(409, "Device key is already enrolled")
    try:
        enrollment = consume_enrollment_token(session, body.token)
    except EnrollmentTokenInvalid as exc:
        raise HTTPException(409, str(exc)) from exc
    distribution_release = (
        session.get(NodeDistributionRelease, bootstrap.distribution_release_id)
        if management_mode == RuntimeNodeMode.CLIENT
        and bootstrap is not None
        and bootstrap.distribution_release_id is not None
        else None
    )
    node = RuntimeNode(
        namespace_id=enrollment.namespace_id,
        name=body.name,
        hostname=body.hostname,
        os_name=body.os_name,
        architecture=body.architecture,
        agent_version=body.agent_version,
        sdk_version=body.sdk_version,
        harness_capabilities=body.harness_capabilities.model_dump(exclude_none=True),
        public_key=body.public_key,
        key_fingerprint=fingerprint,
        management_mode=management_mode,
        adapter_registry_digest=distribution_release.manifest.get(
            "adapter_registry_digest"
        )
        if distribution_release is not None
        else None,
    )
    session.add(node)
    session.flush()
    credential, token = issue_node_credential(session, node)
    if bootstrap is not None:
        bootstrap.node_id = node.id
        bootstrap.status = BootstrapStatus.ENROLLED
        bootstrap.enrollment_idempotency_key = body.idempotency_key
        bootstrap.enrollment_request_digest = enrollment_request_digest
        bootstrap.enrollment_response_ciphertext = seal_secret_payload(
            {
                "credential": token,
                "credential_expires_at": credential.expires_at.isoformat(),
            }
        )
        _append_bootstrap_attempt(
            session,
            bootstrap,
            BootstrapStatus.STAGED,
            manifest_digest=body.distribution_manifest_digest,
        )
        _append_bootstrap_attempt(
            session,
            bootstrap,
            BootstrapStatus.ENROLLED,
            manifest_digest=body.distribution_manifest_digest,
        )
        session.add(bootstrap)
    session.commit()
    return NodeEnrollResult(
        node_id=node.id,
        namespace_id=node.namespace_id,
        credential=token,
        credential_expires_at=credential.expires_at,
    )


@node_router.post("/bootstrap/recover", response_model=NodeEnrollResult)
def recover_bootstrap_enrollment(
    body: NodeEnrollInput, session: SessionDep
) -> NodeEnrollResult:
    key_bytes = _decode_public_key(body.public_key)
    fingerprint = hashlib.sha256(key_bytes).hexdigest()
    try:
        record = read_enrollment_token(session, body.token)
    except EnrollmentTokenInvalid as exc:
        raise HTTPException(409, str(exc)) from exc
    now = datetime.now(timezone.utc)
    if (
        record.consumed_at is None
        or record.bound_public_key_fingerprint != fingerprint
        or record.requested_management_mode == RuntimeNodeMode.LEGACY_UNCLASSIFIED
        or record.expires_at + timedelta(minutes=10) <= now
    ):
        session.rollback()
        raise HTTPException(409, "bootstrap enrollment is not recoverable")
    bootstrap = _bootstrap_session_for_token(session, record.id)
    _verify_device_proof(
        body.public_key,
        body.proof,
        f"neomua-recover-v1:{body.token}:{record.requested_management_mode.value}",
    )
    node = session.exec(
        select(RuntimeNode).where(RuntimeNode.key_fingerprint == fingerprint)
    ).first()
    if node is None or node.management_mode != record.requested_management_mode:
        session.rollback()
        raise HTTPException(409, "bootstrap node identity is missing")
    if (
        body.idempotency_key != bootstrap.enrollment_idempotency_key
        or bootstrap.enrollment_response_ciphertext is None
    ):
        session.rollback()
        raise HTTPException(409, "bootstrap recovery idempotency mismatch")
    restored = open_secret_payload(bootstrap.enrollment_response_ciphertext)
    session.rollback()
    return NodeEnrollResult(
        node_id=node.id,
        namespace_id=node.namespace_id,
        credential=restored["credential"],
        credential_expires_at=datetime.fromisoformat(restored["credential_expires_at"]),
    )
