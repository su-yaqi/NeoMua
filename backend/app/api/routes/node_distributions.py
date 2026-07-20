"""Platform-operated, signed Runtime Node distribution releases."""

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import SessionDep, get_current_active_superuser
from app.api.routes.runtime_artifacts import artifact_storage
from app.core.config import settings
from app.runtime.artifacts.signing import configured_artifact_signer
from app.runtime.enrollment import EnrollmentTokenInvalid, read_enrollment_token
from app.runtime.models import (
    NodeBootstrapSession,
    NodeDistributionRelease,
    RuntimeAdapterRelease,
    RuntimeEngineType,
    RuntimeNodeMode,
)

platform_router = APIRouter(
    prefix="/runtime-node-distributions",
    tags=["runtime-node-distributions"],
    dependencies=[Depends(get_current_active_superuser)],
)
node_router = APIRouter(prefix="/node/bootstrap", tags=["node-bootstrap"])


class NodeDistributionPublic(BaseModel):
    id: uuid.UUID
    release_key: str
    channel: str
    management_mode: RuntimeNodeMode
    os_name: str
    architecture: str
    manifest: dict[str, Any]
    manifest_digest: str
    signature: str
    signing_public_key: str
    active: bool


class RuntimeAdapterReleaseCreate(BaseModel):
    adapter_id: str = Field(min_length=1, max_length=128)
    engine_type: RuntimeEngineType
    version: str = Field(min_length=1, max_length=64)
    discovery_contract: dict[str, Any]
    execution_contract: dict[str, Any]


class RuntimeAdapterReleasePublic(RuntimeAdapterReleaseCreate):
    id: uuid.UUID
    release_digest: str
    signature: str
    signing_public_key: str
    active: bool


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _canonical_architecture(value: str) -> str:
    normalized = value.lower()
    aliases = {"x86_64": "amd64", "aarch64": "arm64"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"amd64", "arm64"}:
        raise HTTPException(422, "unsupported distribution architecture")
    return normalized


def _safe_archive_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise HTTPException(422, "distribution archive contains an unsafe path")
    return path


def _validate_adapter_contract(body: RuntimeAdapterReleaseCreate) -> None:
    expected_engine = {
        "neomua.claude-code": RuntimeEngineType.CLAUDE_CODE,
        "neomua.codex": RuntimeEngineType.CODEX,
    }.get(body.adapter_id)
    if expected_engine is None or body.engine_type != expected_engine:
        raise HTTPException(422, "unsupported built-in adapter identity")
    allowed_discovery = {
        "candidate_paths",
        "version_args",
        "version_regex",
        "identity_regex",
        "timeout_seconds",
        "max_output_bytes",
        "login_probe_args",
        "login_success_regex",
        "model_probe_args",
        "model_regex",
        "min_version",
        "max_version_exclusive",
        "capabilities",
    }
    if set(body.discovery_contract) != allowed_discovery:
        raise HTTPException(422, "adapter discovery contract fields are incomplete")
    candidates = body.discovery_contract["candidate_paths"]
    if (
        not isinstance(candidates, list)
        or not candidates
        or len(candidates) > 32
        or len(candidates) != len(set(candidates))
        or any(
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or any(character in path for character in "*?[]{}\x00")
            for path in candidates
        )
    ):
        raise HTTPException(
            422, "adapter candidate paths must be finite absolute paths"
        )
    for field in ("version_args", "login_probe_args", "model_probe_args"):
        value = body.discovery_contract[field]
        if (
            not isinstance(value, list)
            or len(value) > 16
            or any(
                not isinstance(argument, str) or not argument or "\x00" in argument
                for argument in value
            )
        ):
            raise HTTPException(422, f"adapter {field} is invalid")
    compiled_patterns: dict[str, re.Pattern[str]] = {}
    for field in (
        "version_regex",
        "identity_regex",
        "login_success_regex",
        "model_regex",
    ):
        value = body.discovery_contract[field]
        if not isinstance(value, str) or not value or len(value) > 512:
            raise HTTPException(422, f"adapter {field} is invalid")
        try:
            compiled_patterns[field] = re.compile(value)
        except re.error as exc:
            raise HTTPException(422, f"adapter {field} is invalid") from exc
    if compiled_patterns["version_regex"].groups != 1:
        raise HTTPException(422, "adapter version_regex requires one capture group")
    if compiled_patterns["model_regex"].groups != 1:
        raise HTTPException(422, "adapter model_regex requires one capture group")
    timeout = body.discovery_contract["timeout_seconds"]
    output_limit = body.discovery_contract["max_output_bytes"]
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= 30
    ):
        raise HTTPException(422, "adapter probe timeout is invalid")
    if (
        not isinstance(output_limit, int)
        or isinstance(output_limit, bool)
        or not 1024 <= output_limit <= 1024 * 1024
    ):
        raise HTTPException(422, "adapter probe output limit is invalid")
    if not isinstance(body.discovery_contract["capabilities"], dict):
        raise HTTPException(422, "adapter capabilities are invalid")
    try:
        minimum = Version(str(body.discovery_contract["min_version"]))
        maximum = Version(str(body.discovery_contract["max_version_exclusive"]))
    except (InvalidVersion, TypeError) as exc:
        raise HTTPException(422, "adapter version range is invalid") from exc
    if minimum >= maximum:
        raise HTTPException(422, "adapter version range is empty")
    allowed_execution = {
        "runner",
        "supported_permission_modes",
        "supports_tool_filters",
        "supports_mcp_injection",
        "supports_per_tool_approval",
    }
    if set(body.execution_contract) != allowed_execution:
        raise HTTPException(422, "adapter execution contract fields are incomplete")
    expected_runner = {
        RuntimeEngineType.CLAUDE_CODE: "claude_code_jsonl_v1",
        RuntimeEngineType.CODEX: "codex_exec_jsonl_v1",
    }[body.engine_type]
    if body.execution_contract["runner"] != expected_runner:
        raise HTTPException(422, "adapter runner does not match engine")
    modes = body.execution_contract["supported_permission_modes"]
    if (
        not isinstance(modes, list)
        or not modes
        or any(mode not in {"default", "acceptEdits", "plan"} for mode in modes)
    ):
        raise HTTPException(422, "adapter permission modes are invalid")
    if len(modes) != len(set(modes)):
        raise HTTPException(422, "adapter permission modes contain duplicates")
    for field in (
        "supports_tool_filters",
        "supports_mcp_injection",
        "supports_per_tool_approval",
    ):
        if not isinstance(body.execution_contract[field], bool):
            raise HTTPException(422, f"adapter {field} must be boolean")


@platform_router.post(
    "/adapters", response_model=RuntimeAdapterReleasePublic, status_code=201
)
def publish_adapter_release(
    body: RuntimeAdapterReleaseCreate, session: SessionDep
) -> RuntimeAdapterReleasePublic:
    _validate_adapter_contract(body)
    payload = body.model_dump(mode="json")
    release_digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    existing = session.exec(
        select(RuntimeAdapterRelease).where(
            RuntimeAdapterRelease.adapter_id == body.adapter_id,
            RuntimeAdapterRelease.version == body.version,
        )
    ).first()
    if existing is not None:
        if existing.release_digest != release_digest:
            raise HTTPException(409, "adapter release identity is immutable")
        return RuntimeAdapterReleasePublic.model_validate(
            existing, from_attributes=True
        )
    signer = configured_artifact_signer()
    active = session.exec(
        select(RuntimeAdapterRelease).where(
            RuntimeAdapterRelease.adapter_id == body.adapter_id,
            col(RuntimeAdapterRelease.active).is_(True),
        )
    ).all()
    for previous in active:
        previous.active = False
        session.add(previous)
    record = RuntimeAdapterRelease(
        **payload,
        release_digest=release_digest,
        signature=signer.sign(_canonical_bytes(payload)),
        signing_public_key=signer.public_key(),
        active=True,
    )
    session.add(record)
    session.commit()
    return RuntimeAdapterReleasePublic.model_validate(record, from_attributes=True)


@platform_router.get("/adapters", response_model=list[RuntimeAdapterReleasePublic])
def list_adapter_releases(session: SessionDep) -> list[RuntimeAdapterReleasePublic]:
    records = session.exec(
        select(RuntimeAdapterRelease).order_by(
            col(RuntimeAdapterRelease.created_at).desc()
        )
    ).all()
    return [
        RuntimeAdapterReleasePublic.model_validate(record, from_attributes=True)
        for record in records
    ]


def _inspect_distribution_archive(
    archive_path: Path, archive_digest: str, archive_size: int
) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise HTTPException(422, "distribution must be a valid ZIP archive") from exc
    files: list[dict[str, Any]] = []
    release_metadata: dict[str, Any] | None = None
    seen_paths: set[str] = set()
    expanded_size = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = _safe_archive_path(info.filename)
            normalized_path = path.as_posix()
            if normalized_path in seen_paths:
                raise HTTPException(422, "distribution archive has duplicate paths")
            seen_paths.add(normalized_path)
            mode = (info.external_attr >> 16) & 0o177777
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise HTTPException(422, "distribution archive symlinks are forbidden")
            digest = hashlib.sha256()
            size = 0
            content = bytearray() if normalized_path == "release.json" else None
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    expanded_size += len(chunk)
                    if expanded_size > settings.ARTIFACT_MAX_ARCHIVE_BYTES:
                        raise HTTPException(
                            413, "distribution expanded size exceeds limit"
                        )
                    digest.update(chunk)
                    if content is not None:
                        content.extend(chunk)
            files.append(
                {
                    "path": normalized_path,
                    "sha256": digest.hexdigest(),
                    "size": size,
                    "executable": bool(mode & 0o111),
                }
            )
            if content is not None:
                try:
                    parsed = json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HTTPException(422, "release.json is invalid") from exc
                if not isinstance(parsed, dict):
                    raise HTTPException(422, "release.json must be an object")
                release_metadata = parsed
    if release_metadata is None:
        raise HTTPException(422, "distribution archive requires release.json")
    required_strings = {
        "release_key",
        "channel",
        "management_mode",
        "os_name",
        "architecture",
        "node_manager_version",
        "service_manager",
        "entrypoint",
        "logical_installation_ref",
    }
    if any(
        not isinstance(release_metadata.get(field), str) or not release_metadata[field]
        for field in required_strings
    ):
        raise HTTPException(422, "release.json is missing required string fields")
    if release_metadata.get("schema_version") != "1.0":
        raise HTTPException(422, "unsupported distribution schema version")
    if release_metadata["os_name"].lower() != "linux":
        raise HTTPException(422, "only Linux distributions are supported")
    if release_metadata["service_manager"] != "systemd":
        raise HTTPException(422, "only systemd distributions are supported")
    try:
        mode = RuntimeNodeMode(release_metadata["management_mode"])
    except ValueError as exc:
        raise HTTPException(422, "invalid distribution management mode") from exc
    if mode == RuntimeNodeMode.LEGACY_UNCLASSIFIED:
        raise HTTPException(422, "legacy distributions cannot be published")
    entrypoint = _safe_archive_path(release_metadata["entrypoint"]).as_posix()
    entrypoint_file = next((item for item in files if item["path"] == entrypoint), None)
    if entrypoint_file is None or not entrypoint_file["executable"]:
        raise HTTPException(422, "distribution entrypoint is missing or not executable")
    components = release_metadata.get("components")
    if not isinstance(components, list) or any(
        not isinstance(component, dict)
        or not isinstance(component.get("name"), str)
        or not isinstance(component.get("version"), str)
        for component in components
    ):
        raise HTTPException(422, "distribution components are invalid")
    component_names = {component["name"] for component in components}
    required_components = (
        {"neomua-node-manager", "claude-agent-sdk", "runtime-adapter", "systemd-unit"}
        if mode == RuntimeNodeMode.SERVICE
        else {"neomua-node-manager", "adapter-registry", "systemd-unit"}
    )
    if not required_components.issubset(component_names):
        raise HTTPException(422, "distribution components are incomplete")
    return {
        **release_metadata,
        "architecture": _canonical_architecture(release_metadata["architecture"]),
        "entrypoint": entrypoint,
        "archive_sha256": archive_digest,
        "archive_size": archive_size,
        "files": sorted(files, key=lambda item: item["path"]),
    }


def _public(record: NodeDistributionRelease) -> NodeDistributionPublic:
    return NodeDistributionPublic(
        id=record.id,
        release_key=record.release_key,
        channel=record.channel,
        management_mode=record.management_mode,
        os_name=record.os_name,
        architecture=record.architecture,
        manifest=record.manifest,
        manifest_digest=record.manifest_digest,
        signature=record.signature,
        signing_public_key=record.signing_public_key,
        active=record.active,
    )


@platform_router.post("", response_model=NodeDistributionPublic, status_code=201)
async def publish_distribution(
    session: SessionDep, file: UploadFile = File()
) -> NodeDistributionPublic:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="neomua-node-distribution-", suffix=".zip"
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.ARTIFACT_MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "distribution archive exceeds limit")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        archive_digest = digest.hexdigest()
        manifest = _inspect_distribution_archive(temporary, archive_digest, size)
        mode = RuntimeNodeMode(manifest["management_mode"])
        if mode == RuntimeNodeMode.CLIENT:
            raw_adapter_ids = manifest.get("adapter_release_ids")
            if not isinstance(raw_adapter_ids, list) or len(raw_adapter_ids) != 2:
                raise HTTPException(
                    422, "client distribution requires exact Adapter release IDs"
                )
            try:
                adapter_ids = [uuid.UUID(str(value)) for value in raw_adapter_ids]
            except ValueError as exc:
                raise HTTPException(
                    422, "client Adapter release ID is invalid"
                ) from exc
            adapters = session.exec(
                select(RuntimeAdapterRelease).where(
                    col(RuntimeAdapterRelease.id).in_(adapter_ids),
                    col(RuntimeAdapterRelease.active).is_(True),
                )
            ).all()
            if len(adapters) != 2 or {adapter.engine_type for adapter in adapters} != {
                RuntimeEngineType.CLAUDE_CODE,
                RuntimeEngineType.CODEX,
            }:
                raise HTTPException(409, "client Adapter release set is unavailable")
            releases = [
                RuntimeAdapterReleasePublic.model_validate(
                    adapter, from_attributes=True
                ).model_dump(mode="json")
                for adapter in sorted(adapters, key=lambda item: item.adapter_id)
            ]
            registry = {"schema_version": "1.0", "releases": releases}
            manifest["adapter_registry"] = registry
            manifest["adapter_registry_digest"] = hashlib.sha256(
                _canonical_bytes(registry)
            ).hexdigest()
        elif manifest.get("adapter_release_ids") is not None:
            raise HTTPException(422, "service distribution cannot contain Adapters")
        existing = session.exec(
            select(NodeDistributionRelease).where(
                NodeDistributionRelease.release_key == manifest["release_key"]
            )
        ).first()
        if existing is not None:
            if existing.manifest.get("archive_sha256") == archive_digest:
                return _public(existing)
            raise HTTPException(409, "distribution release key is immutable")
        release_id = uuid.uuid4()
        manifest["release_id"] = str(release_id)
        manifest["download_path"] = f"/api/v1/node/bootstrap/distributions/{release_id}"
        payload = _canonical_bytes(manifest)
        signer = configured_artifact_signer()
        storage_key = f"node-distributions/sha256/{archive_digest}"
        artifact_storage().put_once(storage_key, temporary)
        active = session.exec(
            select(NodeDistributionRelease).where(
                NodeDistributionRelease.channel == manifest["channel"],
                NodeDistributionRelease.management_mode == mode,
                NodeDistributionRelease.os_name == "linux",
                NodeDistributionRelease.architecture == manifest["architecture"],
                col(NodeDistributionRelease.active).is_(True),
            )
        ).all()
        for previous in active:
            previous.active = False
            session.add(previous)
        record = NodeDistributionRelease(
            id=release_id,
            release_key=manifest["release_key"],
            channel=manifest["channel"],
            management_mode=mode,
            os_name="linux",
            architecture=manifest["architecture"],
            manifest=manifest,
            manifest_digest=hashlib.sha256(payload).hexdigest(),
            signature=signer.sign(payload),
            signing_public_key=signer.public_key(),
            active=True,
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "distribution release already exists") from exc
        return _public(record)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


@platform_router.get("", response_model=list[NodeDistributionPublic])
def list_distributions(session: SessionDep) -> list[NodeDistributionPublic]:
    records = session.exec(
        select(NodeDistributionRelease).order_by(
            col(NodeDistributionRelease.created_at).desc()
        )
    ).all()
    return [_public(record) for record in records]


@node_router.get("/distributions/{release_id}", response_model=None)
def download_distribution(
    release_id: uuid.UUID,
    session: SessionDep,
    x_bootstrap_token: Annotated[str, Header(alias="X-Bootstrap-Token")],
) -> StreamingResponse:
    try:
        token = read_enrollment_token(session, x_bootstrap_token)
    except EnrollmentTokenInvalid as exc:
        raise HTTPException(403, str(exc)) from exc
    bootstrap = session.exec(
        select(NodeBootstrapSession).where(
            NodeBootstrapSession.enrollment_token_id == token.id
        )
    ).first()
    release = session.get(NodeDistributionRelease, release_id)
    if (
        bootstrap is None
        or release is None
        or bootstrap.distribution_release_id != release.id
        or token.consumed_at is not None
        or token.revoked_at is not None
    ):
        session.rollback()
        raise HTTPException(404, "bootstrap distribution is unavailable")
    storage_key = f"node-distributions/sha256/{release.manifest['archive_sha256']}"
    source = artifact_storage().open(storage_key)

    def stream() -> Iterator[bytes]:
        try:
            while chunk := source.read(1024 * 1024):
                yield chunk
        finally:
            source.close()

    return StreamingResponse(stream(), media_type="application/zip")
