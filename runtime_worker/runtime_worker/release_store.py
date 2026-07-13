import base64
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def execution_options(spec: dict[str, Any]) -> dict[str, Any]:
    allowed = [
        item["key"]
        for item in spec["tools"]
        if item["policy"] in {"allow", "require_approval"}
    ]
    disallowed = [
        item["key"]
        for item in spec["tools"]
        if item["policy"] in {"deny", "disabled", "forbidden"}
    ]
    return {
        "model": spec["model"]["model_id"],
        "system_prompt": spec["system_prompt"],
        "permission_mode": spec["policies"]["permission_mode"],
        "allowed_tools": sorted(allowed),
        "disallowed_tools": sorted(disallowed),
        "timeout_seconds": spec["policies"]["timeout_seconds"],
        "mcp_servers": [
            {
                "name": item["slug"],
                "transport": item["transport"],
                "config": item["config"],
            }
            for item in spec["mcp_servers"]
        ],
    }


class AgentReleaseStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _verify_signature(release: dict[str, Any]) -> None:
        try:
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(release["signing_public_key"])
            ).verify(
                base64.b64decode(release["signature"]),
                canonical_bytes(release["manifest"]),
            )
        except (KeyError, ValueError, InvalidSignature) as exc:
            raise ValueError("Agent Release signature is invalid") from exc

    def apply(self, payload: dict[str, Any]) -> dict[str, str]:
        release = payload["release"]
        spec = payload["resolved_spec"]
        materialization = payload["materialization"]
        self._verify_signature(release)
        if digest(release["manifest"]) != release["manifest_digest"]:
            raise ValueError("Agent Release manifest digest mismatch")
        if digest(spec) != release["resolved_spec_digest"]:
            raise ValueError("Resolved Agent Spec digest mismatch")
        if materialization["resolved_spec_digest"] != release["resolved_spec_digest"]:
            raise ValueError("Materialization refers to a different Spec")
        options = execution_options(spec)
        if digest(options) != materialization["options_digest"]:
            raise ValueError("Claude options digest mismatch")
        agent_root = self.root / "agents" / str(release["agent_id"])
        versions = agent_root / "versions"
        versions.mkdir(parents=True, exist_ok=True, mode=0o700)
        version = versions / str(release["id"])
        if not version.exists():
            staging = (
                agent_root / f".staging-{payload['deployment_id']}-{uuid.uuid4().hex}"
            )
            staging.mkdir(parents=True, mode=0o700)
            try:
                for name, value in (
                    ("resolved-spec.json", spec),
                    ("materialization.json", materialization),
                    ("options.json", options),
                    ("release.json", release),
                ):
                    path = staging / name
                    with path.open("x", encoding="utf-8") as target:
                        target.write(canonical_bytes(value).decode())
                        target.flush()
                        os.fsync(target.fileno())
                for skill in spec.get("skills", []):
                    slug = str(skill["slug"])
                    if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
                        raise ValueError("Skill slug is unsafe for materialization")
                    for item in skill.get("files", []):
                        relative = PurePosixPath(str(item["path"]))
                        if (
                            relative.is_absolute()
                            or ".." in relative.parts
                            or "\\" in str(item["path"])
                        ):
                            raise ValueError(
                                "Skill file path is unsafe for materialization"
                            )
                        content = base64.b64decode(
                            item["content_base64"], validate=True
                        )
                        if hashlib.sha256(content).hexdigest() != item["sha256"]:
                            raise ValueError(
                                "Skill file digest mismatch during materialization"
                            )
                        destination = (
                            staging
                            / ".claude"
                            / "skills"
                            / slug
                            / Path(*relative.parts)
                        )
                        destination.parent.mkdir(
                            parents=True, exist_ok=True, mode=0o700
                        )
                        with destination.open("xb") as target:
                            target.write(content)
                            target.flush()
                            os.fsync(target.fileno())
                os.replace(staging, version)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        current = agent_root / "current"
        old = Path(os.readlink(current)).name if current.is_symlink() else None
        temporary = agent_root / f".current-{uuid.uuid4().hex}"
        os.symlink(Path("versions") / version.name, temporary)
        os.replace(temporary, current)
        state = agent_root / "state.json"
        temp_state = agent_root / f".state-{uuid.uuid4().hex}"
        temp_state.write_bytes(
            canonical_bytes(
                {
                    "current": version.name,
                    "previous": old,
                    "resolved_spec_digest": release["resolved_spec_digest"],
                }
            )
        )
        os.chmod(temp_state, 0o600)
        os.replace(temp_state, state)
        return {
            "resolved_spec_digest": release["resolved_spec_digest"],
            "materialization_digest": materialization["resolved_spec_digest"],
        }

    def verify_installed(
        self, agent_id: str, release_id: str, resolved_spec_digest: str
    ) -> Path:
        version = self.root / "agents" / agent_id / "versions" / release_id
        spec_path = version / "resolved-spec.json"
        if not version.is_dir() or not spec_path.is_file():
            raise ValueError(
                "release_not_active: frozen Agent Release is not installed"
            )
        try:
            spec = json.loads(spec_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "release_not_active: installed Agent Release is damaged"
            ) from exc
        if digest(spec) != resolved_spec_digest:
            raise ValueError(
                "release_not_active: installed Agent Release digest mismatch"
            )
        return version
