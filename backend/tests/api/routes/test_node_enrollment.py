import base64
import hashlib
import io
import json
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from app.runtime.catalog import canonical_digest
from app.runtime.models import RuntimeNode
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user

HARNESS_CAPABILITIES = {
    "claude_code": {
        "cli_version": "2.1.191",
        "sdk_version": "0.2.110",
        "harness_version": "0.1.0",
    }
}


def device_keypair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(public).decode()


def sign(private: Ed25519PrivateKey, payload: str) -> str:
    return base64.b64encode(private.sign(payload.encode())).decode()


def publish_distribution(
    client: TestClient,
    headers: dict[str, str],
    storage_root: Path,
    monkeypatch,
    *,
    mode: str,
    architecture: str,
) -> dict:
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(storage_root))
    components = (
        [
            {"name": "neomua-node-manager", "version": "0.1.0"},
            {"name": "claude-agent-sdk", "version": "0.2.110"},
            {"name": "runtime-adapter", "version": "0.1.0"},
            {"name": "systemd-unit", "version": "1.0.0"},
        ]
        if mode == "service"
        else [
            {"name": "neomua-node-manager", "version": "0.1.0"},
            {"name": "adapter-registry", "version": "1.0.0"},
            {"name": "systemd-unit", "version": "1.0.0"},
        ]
    )
    adapter_release_ids: list[str] = []
    if mode == "client":
        for adapter_id, engine_type, runner, candidate in (
            (
                "neomua.claude-code",
                "claude_code",
                "claude_code_jsonl_v1",
                "/usr/local/bin/claude",
            ),
            (
                "neomua.codex",
                "codex",
                "codex_exec_jsonl_v1",
                "/usr/local/bin/codex",
            ),
        ):
            adapter = client.post(
                f"{settings.API_V1_STR}/runtime-node-distributions/adapters",
                headers=headers,
                json={
                    "adapter_id": adapter_id,
                    "engine_type": engine_type,
                    "version": "1.0.0",
                    "discovery_contract": {
                        "candidate_paths": [
                            candidate,
                            f"/usr/bin/{Path(candidate).name}",
                        ],
                        "version_args": ["--version"],
                        "version_regex": r"([0-9]+(?:\.[0-9A-Za-z-]+)+)",
                        "identity_regex": "Claude|Codex|codex",
                        "timeout_seconds": 10,
                        "max_output_bytes": 65536,
                        "login_probe_args": ["auth", "status"],
                        "login_success_regex": "logged|authenticated",
                        "model_probe_args": ["models", "list"],
                        "model_regex": r"([A-Za-z0-9_.:-]+)",
                        "min_version": "0.1.0",
                        "max_version_exclusive": "1000.0.0",
                        "capabilities": {"permission_modes": ["default", "plan"]},
                    },
                    "execution_contract": {
                        "runner": runner,
                        "supported_permission_modes": ["default", "plan"],
                        "supports_tool_filters": engine_type == "claude_code",
                        "supports_mcp_injection": engine_type == "claude_code",
                        "supports_per_tool_approval": False,
                    },
                },
            )
            assert adapter.status_code == 201, adapter.text
            adapter_release_ids.append(adapter.json()["id"])
    metadata = {
        "schema_version": "1.0",
        "release_key": f"test-{mode}-{architecture}-{uuid.uuid4()}",
        "channel": "stable",
        "management_mode": mode,
        "os_name": "linux",
        "architecture": architecture,
        "node_manager_version": "0.1.0",
        "service_manager": "systemd",
        "entrypoint": "bin/neomua-node",
        "logical_installation_ref": (
            "service:claude-agent-sdk:stable"
            if mode == "service"
            else "client:node-manager:stable"
        ),
        "components": components,
    }
    if adapter_release_ids:
        metadata["adapter_release_ids"] = adapter_release_ids
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        release_info = zipfile.ZipInfo("release.json")
        release_info.external_attr = (stat.S_IFREG | 0o600) << 16
        output.writestr(
            release_info,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        )
        entrypoint = zipfile.ZipInfo("bin/neomua-node")
        entrypoint.external_attr = (stat.S_IFREG | 0o755) << 16
        output.writestr(entrypoint, b"#!/bin/sh\nexit 0\n")
    response = client.post(
        f"{settings.API_V1_STR}/runtime-node-distributions",
        headers=headers,
        files={"file": ("distribution.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_sees_enrollment_token_only_on_creation(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    created = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    )
    assert created.status_code == 201
    assert created.json()["token"].startswith("nmenr_")
    listed = client.get(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    )
    assert listed.status_code == 200
    assert "token" not in listed.json()["data"][0]
    assert "token_hash" not in listed.json()["data"][0]


def test_developer_cannot_create_enrollment_token(
    client: TestClient, db: Session
) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    response = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    )
    assert response.status_code == 403


def test_node_enrollment_consumes_token_once(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    token = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens",
        headers=namespace_headers(superuser_token_headers, namespace.id),
    ).json()["token"]
    payload = {
        "token": token,
        "name": "build-node",
        "hostname": "host-1",
        "os_name": "linux",
        "architecture": "arm64",
        "agent_version": "0.1.0",
        "sdk_version": "0.2.110",
        "harness_capabilities": HARNESS_CAPABILITIES,
        "public_key": base64.b64encode(b"a" * 32).decode(),
    }
    enrolled = client.post(f"{settings.API_V1_STR}/node/enroll", json=payload)
    assert enrolled.status_code == 201
    assert enrolled.json()["node_id"]
    assert enrolled.json()["credential"]
    reused = client.post(f"{settings.API_V1_STR}/node/enroll", json=payload)
    assert reused.status_code == 409


def test_admin_lists_and_configures_enrolled_node_runtime(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    token = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    ).json()["token"]
    enrolled = client.post(
        f"{settings.API_V1_STR}/node/enroll",
        json={
            "token": token,
            "name": "worker",
            "hostname": "host",
            "os_name": "linux",
            "architecture": "amd64",
            "agent_version": "0.1.0",
            "harness_capabilities": HARNESS_CAPABILITIES,
            "public_key": base64.b64encode(b"b" * 32).decode(),
        },
    ).json()
    listed = client.get(f"{settings.API_V1_STR}/runtimes/nodes", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == enrolled["node_id"]
    assert listed.json()["data"][0]["harness_capabilities"] == HARNESS_CAPABILITIES
    assert "public_key" not in listed.json()["data"][0]
    configured = client.put(
        f"{settings.API_V1_STR}/runtimes/nodes/{enrolled['node_id']}/runtime",
        headers=headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-node",
            "base_url": "https://anthropic-compatible.example",
            "secret_inputs": {"api_key": "node-secret"},
        },
    )
    assert configured.status_code == 410
    assert "read-only" in configured.text


def test_developer_can_list_but_cannot_configure_nodes(
    client: TestClient, db: Session
) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    assert (
        client.get(f"{settings.API_V1_STR}/runtimes/nodes", headers=headers).status_code
        == 200
    )
    response = client.put(
        f"{settings.API_V1_STR}/runtimes/nodes/{'0' * 8}-{'0' * 4}-{'0' * 4}-{'0' * 4}-{'0' * 12}/runtime",
        headers=headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "x",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "x"},
        },
    )
    assert response.status_code == 403


def test_service_bootstrap_is_mode_bound_signed_and_self_registering(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    release = publish_distribution(
        client,
        superuser_token_headers,
        tmp_path,
        monkeypatch,
        mode="service",
        architecture="amd64",
    )
    session_response = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/bootstrap-sessions",
        headers=headers,
        json={"management_mode": "service"},
    )
    assert session_response.status_code == 201
    token = session_response.json()["token"]
    private, public_key = device_keypair()
    preflight = client.post(
        f"{settings.API_V1_STR}/node/bootstrap/preflight",
        json={
            "token": token,
            "management_mode": "service",
            "os_name": "linux",
            "architecture": "amd64",
            "agent_version": "0.1.0",
            "public_key": public_key,
            "service_manager": "systemd",
            "available_disk_bytes": 3 * 1024 * 1024 * 1024,
            "state_directory_atomic_rename": True,
            "platform_tls_verified": True,
            "clock_skew_seconds": 0,
        },
    )
    assert preflight.status_code == 200, preflight.text
    signed = preflight.json()
    manifest_bytes = json.dumps(
        signed["manifest"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    Ed25519PublicKey.from_public_bytes(
        base64.b64decode(signed["signing_public_key"])
    ).verify(base64.b64decode(signed["signature"]), manifest_bytes)
    assert signed["release_id"] == release["id"]
    assert signed["manifest"]["archive_sha256"]
    downloaded = client.get(
        signed["manifest"]["download_path"],
        headers={"X-Bootstrap-Token": token},
    )
    assert downloaded.status_code == 200, downloaded.text
    assert (
        hashlib.sha256(downloaded.content).hexdigest()
        == signed["manifest"]["archive_sha256"]
    )
    idempotency_key = "service-bootstrap-idempotency-key"
    enrollment_proof = sign(
        private,
        (
            f"neomua-enroll-v2:{token}:service:"
            f"{signed['manifest_digest']}:{idempotency_key}"
        ),
    )
    enrollment_payload = {
        "token": token,
        "name": "service-node",
        "hostname": "service-host",
        "os_name": "linux",
        "architecture": "amd64",
        "agent_version": "0.1.0",
        "sdk_version": "0.2.110",
        "harness_capabilities": {},
        "public_key": public_key,
        "management_mode": "service",
        "proof": enrollment_proof,
        "idempotency_key": idempotency_key,
        "distribution_manifest_digest": signed["manifest_digest"],
    }
    enrolled = client.post(
        f"{settings.API_V1_STR}/node/enroll",
        json=enrollment_payload,
    )
    assert enrolled.status_code == 201, enrolled.text
    replayed = client.post(
        f"{settings.API_V1_STR}/node/enroll", json=enrollment_payload
    )
    assert replayed.status_code == 201, replayed.text
    assert replayed.json() == enrolled.json()
    applied_at = datetime.now(timezone.utc)
    receipt_payload = {
        "node_id": enrolled.json()["node_id"],
        "bootstrap_session_id": signed["bootstrap_session_id"],
        "distribution_release_id": signed["release_id"],
        "manifest_digest": signed["manifest_digest"],
        "components": signed["manifest"]["components"],
        "logical_installation_ref": signed["manifest"]["logical_installation_ref"],
        "first_applied_at": applied_at.isoformat(),
    }
    receipt_digest = canonical_digest(receipt_payload)
    receipt = client.post(
        f"{settings.API_V1_STR}/node/bootstrap/receipt",
        json={
            **receipt_payload,
            "device_signature": sign(
                private, f"neomua-installation-receipt-v1:{receipt_digest}"
            ),
        },
    )
    assert receipt.status_code == 201, receipt.text
    nodes = client.get(f"{settings.API_V1_STR}/runtimes/nodes", headers=headers)
    assert nodes.json()["data"][0]["management_mode"] == "service"
    sessions = client.get(
        f"{settings.API_V1_STR}/runtimes/nodes/bootstrap-sessions", headers=headers
    )
    assert sessions.json()[0]["status"] == "staged"


def test_client_bootstrap_rejects_managed_sdk_capability(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    publish_distribution(
        client,
        superuser_token_headers,
        tmp_path,
        monkeypatch,
        mode="client",
        architecture="arm64",
    )
    token = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/bootstrap-sessions",
        headers=headers,
        json={"management_mode": "client"},
    ).json()["token"]
    private, public_key = device_keypair()
    preflight = client.post(
        f"{settings.API_V1_STR}/node/bootstrap/preflight",
        json={
            "token": token,
            "management_mode": "client",
            "os_name": "linux",
            "architecture": "arm64",
            "agent_version": "0.1.0",
            "public_key": public_key,
            "service_manager": "systemd",
            "available_disk_bytes": 3 * 1024 * 1024 * 1024,
            "state_directory_atomic_rename": True,
            "platform_tls_verified": True,
            "clock_skew_seconds": 0,
        },
    )
    assert preflight.status_code == 200
    idempotency_key = "client-bootstrap-idempotency-key"
    manifest_digest = preflight.json()["manifest_digest"]
    proof = sign(
        private,
        f"neomua-enroll-v2:{token}:client:{manifest_digest}:{idempotency_key}",
    )
    enrolled = client.post(
        f"{settings.API_V1_STR}/node/enroll",
        json={
            "token": token,
            "name": "client-node",
            "hostname": "client-host",
            "os_name": "linux",
            "architecture": "arm64",
            "agent_version": "0.1.0",
            "harness_capabilities": {
                "claude_agent_sdk": {
                    "cli_version": "2.1.191",
                    "sdk_version": "0.2.110",
                }
            },
            "public_key": public_key,
            "management_mode": "client",
            "proof": proof,
            "idempotency_key": idempotency_key,
            "distribution_manifest_digest": manifest_digest,
        },
    )
    assert enrolled.status_code == 422
    assert "cannot claim Runtime capabilities" in enrolled.text
    accepted = client.post(
        f"{settings.API_V1_STR}/node/enroll",
        json={
            "token": token,
            "name": "client-node",
            "hostname": "client-host",
            "os_name": "linux",
            "architecture": "arm64",
            "agent_version": "0.1.0",
            "harness_capabilities": {},
            "public_key": public_key,
            "management_mode": "client",
            "proof": proof,
            "idempotency_key": idempotency_key,
            "distribution_manifest_digest": manifest_digest,
        },
    )
    assert accepted.status_code == 201, accepted.text
    node = db.get(RuntimeNode, accepted.json()["node_id"])
    assert node is not None
    assert node.adapter_registry_digest is not None
