"""Signed client Adapter Registry discovery with a private stable installation index."""

import base64
import hashlib
import json
import os
import re
import resource
import secrets
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version


class AdapterRegistryError(RuntimeError):
    pass


def _error_code(error: Exception) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(error).lower()).strip("_")
    return f"adapter_{value or 'probe_failed'}"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_release(release: dict[str, Any], trusted_public_key: str) -> None:
    payload = {
        key: release[key]
        for key in (
            "adapter_id",
            "engine_type",
            "version",
            "discovery_contract",
            "execution_contract",
        )
    }
    payload_bytes = _canonical_bytes(payload)
    if release.get("release_digest") != hashlib.sha256(payload_bytes).hexdigest():
        raise AdapterRegistryError("Adapter release digest mismatch")
    if release.get("signing_public_key") != trusted_public_key:
        raise AdapterRegistryError("Adapter signing key is not trusted")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        ).verify(
            base64.b64decode(str(release.get("signature")), validate=True),
            payload_bytes,
        )
    except (ValueError, InvalidSignature) as exc:
        raise AdapterRegistryError("Adapter release signature is invalid") from exc


def _probe(executable: Path, args: list[str], contract: dict[str, Any]) -> bytes:
    limit = int(contract["max_output_bytes"])

    def limit_output() -> None:
        # Probe contracts are Linux-only. Limit both stdout and stderr backing files.
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "LANG", "LC_ALL", "SSL_CERT_DIR", "SSL_CERT_FILE", "TMPDIR"}
    }
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                [str(executable), *args],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                env=environment,
                check=False,
                timeout=int(contract["timeout_seconds"]),
                preexec_fn=limit_output,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdapterRegistryError("Adapter probe failed") from exc
        size = output.tell()
        if size > limit:
            raise AdapterRegistryError("Adapter probe output exceeded limit")
        output.seek(0)
        value = output.read(limit + 1)
    if completed.returncode != 0:
        raise AdapterRegistryError("Adapter probe returned a failure status")
    return value


class LocalInstallationRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "1.0", "installations": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterRegistryError(
                "local installation registry is unreadable"
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != "1.0"
            or not isinstance(value.get("installations"), dict)
        ):
            raise AdapterRegistryError("local installation registry is invalid")
        return value

    def save(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(value, output, sort_keys=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)


def discover_client_installations(
    registry: dict[str, Any],
    *,
    registry_digest: str,
    trusted_signing_public_key: str,
    local_registry: LocalInstallationRegistry,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if _digest(registry) != registry_digest:
        raise AdapterRegistryError("Adapter Registry digest mismatch")
    releases = registry.get("releases")
    if registry.get("schema_version") != "1.0" or not isinstance(releases, list):
        raise AdapterRegistryError("Adapter Registry schema is invalid")
    state = local_registry.read()
    local_installations = state["installations"]
    discovered: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    seen_files: set[tuple[int, int, str]] = set()
    for release in releases:
        if not isinstance(release, dict):
            raise AdapterRegistryError("Adapter release entry is invalid")
        _verify_release(release, trusted_signing_public_key)
        contract = release["discovery_contract"]
        for candidate_value in contract["candidate_paths"]:
            candidate = Path(candidate_value)
            locator_key = _digest(
                {"adapter_id": release["adapter_id"], "candidate": candidate_value}
            )
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                observations.append(
                    {
                        "adapter_release_id": release["id"],
                        "candidate_ref": locator_key,
                        "status": "missing",
                        "error": {"code": "installation_missing"},
                    }
                )
                continue
            resolved = candidate.resolve(strict=True)
            file_stat = resolved.stat()
            file_identity = (
                file_stat.st_dev,
                file_stat.st_ino,
                str(release["adapter_id"]),
            )
            if file_identity in seen_files:
                continue
            seen_files.add(file_identity)
            try:
                fingerprint = _file_digest(resolved)
                version_output = _probe(
                    resolved, list(contract["version_args"]), contract
                ).decode(errors="replace")
                if re.search(contract["identity_regex"], version_output) is None:
                    raise AdapterRegistryError("executable identity probe failed")
                version_match = re.search(contract["version_regex"], version_output)
                if version_match is None:
                    raise AdapterRegistryError("executable version is unparseable")
                version = version_match.group(1)
                parsed_version = Version(version)
                if parsed_version < Version(
                    contract["min_version"]
                ) or parsed_version >= Version(contract["max_version_exclusive"]):
                    raise AdapterRegistryError("executable version is incompatible")
                login_output = _probe(
                    resolved, list(contract["login_probe_args"]), contract
                ).decode(errors="replace")
                login_ready = (
                    re.search(contract["login_success_regex"], login_output) is not None
                )
                model_output = _probe(
                    resolved, list(contract["model_probe_args"]), contract
                ).decode(errors="replace")
                model_ids = sorted(
                    {
                        match.group(1)
                        for match in re.finditer(contract["model_regex"], model_output)
                    }
                )[:100]
                if (
                    not login_ready
                    or not model_ids
                    or any(
                        not model_id.strip()
                        or len(model_id) > 128
                        or any(ord(character) < 32 for character in model_id)
                        for model_id in model_ids
                    )
                ):
                    raise AdapterRegistryError(
                        "native login or model probe is not ready"
                    )
                if _file_digest(resolved) != fingerprint:
                    raise AdapterRegistryError("executable changed during probe")
            except (AdapterRegistryError, InvalidVersion) as exc:
                observations.append(
                    {
                        "adapter_release_id": release["id"],
                        "candidate_ref": locator_key,
                        "status": "blocked",
                        "error": {"code": _error_code(exc)},
                    }
                )
                continue
            existing = local_installations.get(locator_key)
            installation_id = (
                str(existing["installation_id"])
                if isinstance(existing, dict)
                and existing.get("adapter_id") == release["adapter_id"]
                else str(uuid.uuid4())
            )
            execution_ref = f"client:{release['adapter_id']}:{installation_id}"
            local_installations[locator_key] = {
                "installation_id": installation_id,
                "adapter_id": release["adapter_id"],
                "adapter_release_id": release["id"],
                "candidate_ref": locator_key,
                "execution_ref": execution_ref,
                "execution_path": str(resolved),
                "executable_fingerprint": fingerprint,
                "engine_version": version,
            }
            capabilities = {
                **contract["capabilities"],
                **release["execution_contract"],
                "native_login_ready": True,
                "native_login_evidence_digest": hashlib.sha256(
                    login_output.encode()
                ).hexdigest(),
                "adapter_release_digest": release["release_digest"],
            }
            discovered.append(
                {
                    "installation_key": execution_ref,
                    "name": f"{release['engine_type']} {version}",
                    "engine_type": release["engine_type"],
                    "engine_version": version,
                    "adapter_version": release["version"],
                    "adapter_release_id": release["id"],
                    "adapter_release_digest": release["release_digest"],
                    "executable_fingerprint": fingerprint,
                    "capabilities": capabilities,
                    "discovered_models": [
                        {
                            "model_id": model_id,
                            "route_key": f"native:{installation_id}:{model_id}",
                            "login_evidence_digest": capabilities[
                                "native_login_evidence_digest"
                            ],
                        }
                        for model_id in model_ids
                    ],
                    "_execution_path": str(resolved),
                    "_revalidation_contract": {
                        "version_args": contract["version_args"],
                        "version_regex": contract["version_regex"],
                        "identity_regex": contract["identity_regex"],
                        "login_probe_args": contract["login_probe_args"],
                        "login_success_regex": contract["login_success_regex"],
                        "timeout_seconds": contract["timeout_seconds"],
                        "max_output_bytes": contract["max_output_bytes"],
                    },
                }
            )
            observations.append(
                {
                    "adapter_release_id": release["id"],
                    "candidate_ref": locator_key,
                    "installation_key": execution_ref,
                    "status": "available",
                }
            )
    state["registry_digest"] = registry_digest
    local_registry.save(state)
    return discovered, observations


def public_installation(installation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in installation.items() if not key.startswith("_")
    }
