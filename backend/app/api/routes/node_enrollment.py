import base64
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.llm_provider_service import mask_secret_value, seal_secret_payload
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.enrollment import (
    EnrollmentTokenInvalid,
    consume_enrollment_token,
    create_enrollment_token,
    issue_node_credential,
)
from app.runtime.connections import node_is_online
from app.runtime.models import (
    NodeCredential,
    NodeEnrollmentToken,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
    RuntimeType,
)

admin_router = APIRouter(prefix="/runtimes/nodes", tags=["runtime-nodes"])
node_router = APIRouter(prefix="/node", tags=["node-enrollment"])


class EnrollmentTokenCreated(BaseModel):
    id: uuid.UUID
    token: str
    expires_at: datetime


class EnrollmentTokenPublic(BaseModel):
    id: uuid.UUID
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


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
    public_key: str


class NodeEnrollResult(BaseModel):
    node_id: uuid.UUID
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
    online: bool
    last_seen_at: datetime | None
    revoked_at: datetime | None
    runtime_profile_id: uuid.UUID | None


class NodesPublic(BaseModel):
    data: list[NodePublic]
    count: int


class NodeRuntimeUpsert(BaseModel):
    route_mode: RuntimeRouteMode
    model_id: str = Field(min_length=1, max_length=255)
    provider_config_id: uuid.UUID | None = None
    base_url: str | None = None
    permission_mode: str = "default"
    secret_inputs: dict[str, str] | None = None


class NodeRuntimePublic(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    route_mode: RuntimeRouteMode
    model_id: str
    provider_config_id: uuid.UUID | None
    base_url: str | None
    permission_mode: str
    secret_masked: str | None


def _public_token(record: NodeEnrollmentToken) -> EnrollmentTokenPublic:
    return EnrollmentTokenPublic(
        id=record.id,
        expires_at=record.expires_at,
        consumed_at=record.consumed_at,
        revoked_at=record.revoked_at,
    )


def _public_node(node: RuntimeNode) -> NodePublic:
    return NodePublic(
        id=node.id,
        name=node.name,
        hostname=node.hostname,
        os_name=node.os_name,
        architecture=node.architecture,
        agent_version=node.agent_version,
        sdk_version=node.sdk_version,
        online=node_is_online(node),
        last_seen_at=node.last_seen_at,
        revoked_at=node.revoked_at,
        runtime_profile_id=node.runtime_profile_id,
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
        id=record.id, token=raw, expires_at=record.expires_at
    )


@admin_router.get("/enrollment-tokens", response_model=EnrollmentTokensPublic)
def list_tokens(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> EnrollmentTokensPublic:
    records = session.exec(
        select(NodeEnrollmentToken)
        .where(NodeEnrollmentToken.namespace_id == namespace_id)
        .order_by(NodeEnrollmentToken.created_at.desc())
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
        .order_by(RuntimeNode.created_at.desc())
    ).all()
    return NodesPublic(data=[_public_node(node) for node in nodes], count=len(nodes))


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
    return _public_node(node)


@admin_router.put("/{node_id}/runtime", response_model=NodeRuntimePublic)
def configure_node_runtime(
    node_id: uuid.UUID,
    body: NodeRuntimeUpsert,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> NodeRuntimePublic:
    node = session.get(RuntimeNode, node_id)
    if node is None or node.namespace_id != namespace_id or node.revoked_at is not None:
        raise HTTPException(404, "Active node not found")
    if body.permission_mode == "bypassPermissions":
        raise HTTPException(400, "bypassPermissions is not allowed")
    existing_secret = None
    if node.runtime_profile_id:
        existing_secret = session.exec(
            select(RuntimeSecret).where(
                RuntimeSecret.runtime_profile_id == node.runtime_profile_id
            )
        ).first()
    if body.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        if not body.base_url or (not body.secret_inputs and existing_secret is None):
            raise HTTPException(
                400, "direct_anthropic requires an Anthropic-compatible URL and credential"
            )
    else:
        provider = session.get(LlmProviderConfig, body.provider_config_id)
        if provider is None or provider.namespace_id != namespace_id or not provider.enabled:
            raise HTTPException(404, "Enabled provider config not found")
        model = session.exec(
            select(LlmProviderModel).where(
                LlmProviderModel.provider_config_id == provider.id,
                LlmProviderModel.model_id == body.model_id,
                LlmProviderModel.is_enabled.is_(True),
            )
        ).first()
        if model is None:
            raise HTTPException(400, "Enabled model not found")
    runtime = session.get(RuntimeProfile, node.runtime_profile_id) if node.runtime_profile_id else None
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
    runtime.base_url = body.base_url
    runtime.permission_mode = body.permission_mode
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
        secret.secret_masked = mask_secret_value(next(iter(body.secret_inputs.values())))
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
        permission_mode=runtime.permission_mode,
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
            NodeCredential.revoked_at.is_(None),
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


@node_router.post("/enroll", response_model=NodeEnrollResult, status_code=201)
def enroll_node(body: NodeEnrollInput, session: SessionDep) -> NodeEnrollResult:
    key_bytes = _decode_public_key(body.public_key)
    fingerprint = hashlib.sha256(key_bytes).hexdigest()
    if session.exec(
        select(RuntimeNode).where(RuntimeNode.key_fingerprint == fingerprint)
    ).first():
        raise HTTPException(409, "Device key is already enrolled")
    try:
        enrollment = consume_enrollment_token(session, body.token)
    except EnrollmentTokenInvalid as exc:
        raise HTTPException(409, str(exc)) from exc
    node = RuntimeNode(
        namespace_id=enrollment.namespace_id,
        name=body.name,
        hostname=body.hostname,
        os_name=body.os_name,
        architecture=body.architecture,
        agent_version=body.agent_version,
        sdk_version=body.sdk_version,
        public_key=body.public_key,
        key_fingerprint=fingerprint,
    )
    session.add(node)
    session.flush()
    credential, token = issue_node_credential(session, node)
    session.commit()
    return NodeEnrollResult(
        node_id=node.id,
        credential=token,
        credential_expires_at=credential.expires_at,
    )
