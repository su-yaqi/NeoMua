import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from node_runtime.adapter_registry import (
    AdapterRegistryError,
    LocalInstallationRegistry,
    discover_client_installations,
    public_installation,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _executable(path: Path, identity: str) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  --version) echo "{identity} 1.2.3" ;;\n'
        '  auth) echo "logged-in" ;;\n'
        '  models) echo "model-a"; echo "model-b" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _signed_registry(paths: list[Path]) -> tuple[dict, str, str]:
    private = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode()
    payload = {
        "adapter_id": "neomua.claude-code",
        "engine_type": "claude_code",
        "version": "0.1.0",
        "discovery_contract": {
            "candidate_paths": [str(path) for path in paths],
            "version_args": ["--version"],
            "version_regex": r"Claude Code ([0-9.]+)",
            "identity_regex": r"Claude Code",
            "timeout_seconds": 2,
            "max_output_bytes": 4096,
            "login_probe_args": ["auth"],
            "login_success_regex": r"logged-in",
            "model_probe_args": ["models"],
            "model_regex": r"(model-[a-z]+)",
            "min_version": "1.0.0",
            "max_version_exclusive": "2.0.0",
            "capabilities": {"permission_modes": ["default", "plan"]},
        },
        "execution_contract": {
            "runner": "claude_code_jsonl_v1",
            "supported_permission_modes": ["default", "plan"],
            "supports_tool_filters": True,
            "supports_mcp_injection": True,
            "supports_per_tool_approval": False,
        },
    }
    payload_bytes = _canonical_bytes(payload)
    release = {
        "id": "00000000-0000-0000-0000-000000000001",
        **payload,
        "release_digest": hashlib.sha256(payload_bytes).hexdigest(),
        "signature": base64.b64encode(private.sign(payload_bytes)).decode(),
        "signing_public_key": public_key,
        "active": True,
    }
    registry = {"schema_version": "1.0", "releases": [release]}
    return registry, hashlib.sha256(_canonical_bytes(registry)).hexdigest(), public_key


def test_signed_registry_discovers_multiple_stable_private_installations(
    tmp_path: Path,
) -> None:
    first = tmp_path / "claude-one"
    second = tmp_path / "claude-two"
    _executable(first, "Claude Code")
    _executable(second, "Claude Code")
    registry, digest, public_key = _signed_registry([first, second])
    local_registry = LocalInstallationRegistry(tmp_path / "installations.json")

    discovered, observations = discover_client_installations(
        registry,
        registry_digest=digest,
        trusted_signing_public_key=public_key,
        local_registry=local_registry,
    )
    repeated, _ = discover_client_installations(
        registry,
        registry_digest=digest,
        trusted_signing_public_key=public_key,
        local_registry=local_registry,
    )

    assert len(discovered) == 2
    assert len({item["installation_key"] for item in discovered}) == 2
    assert [item["installation_key"] for item in repeated] == [
        item["installation_key"] for item in discovered
    ]
    assert {item["status"] for item in observations} == {"available"}
    public_payload = json.dumps(
        [public_installation(item) for item in discovered], sort_keys=True
    )
    assert str(tmp_path) not in public_payload
    assert "_execution_path" not in public_payload


def test_registry_rejects_tampered_adapter_signature(tmp_path: Path) -> None:
    executable = tmp_path / "claude"
    _executable(executable, "Claude Code")
    registry, digest, public_key = _signed_registry([executable])
    registry["releases"][0]["signature"] = base64.b64encode(b"invalid").decode()
    tampered_digest = hashlib.sha256(_canonical_bytes(registry)).hexdigest()

    with pytest.raises(AdapterRegistryError, match="signature is invalid"):
        discover_client_installations(
            registry,
            registry_digest=tampered_digest,
            trusted_signing_public_key=public_key,
            local_registry=LocalInstallationRegistry(tmp_path / "state.json"),
        )

    assert tampered_digest != digest
