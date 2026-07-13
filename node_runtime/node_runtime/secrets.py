import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

SERVICE = "io.neomua.operator-cli"


def node_secret_fingerprints(index_path: Path) -> dict[str, str]:
    if not index_path.exists():
        return {}
    try:
        raw = json.loads(index_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Node secret index is damaged: {index_path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"Node secret index is damaged: {index_path}")
    result: dict[str, str] = {}
    for ref, item in raw.items():
        fingerprint = item.get("fingerprint") if isinstance(item, dict) else None
        if (
            not isinstance(ref, str)
            or not ref
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        ):
            raise RuntimeError(f"Node secret index is damaged: {index_path}")
        result[ref] = fingerprint
    return result


def read_node_secret(ref: str) -> dict[str, str]:
    item_id = f"node-secret:{ref}"
    if platform.system() == "Darwin" and shutil.which("security"):
        command = [
            "security",
            "find-generic-password",
            "-s",
            SERVICE,
            "-a",
            item_id,
            "-w",
        ]
    elif platform.system() == "Linux" and shutil.which("secret-tool"):
        command = ["secret-tool", "lookup", "service", SERVICE, "account", item_id]
    else:
        raise RuntimeError("Node credential store is unavailable")
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Node secret_ref is unavailable: {ref}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Node MCP secret must be a JSON object of adapter fields"
        ) from exc
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload.items()
    ):
        raise RuntimeError("Node MCP secret must contain string keys and values")
    return payload
