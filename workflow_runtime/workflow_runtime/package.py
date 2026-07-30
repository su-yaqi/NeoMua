import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def directory_digest(root: Path) -> str:
    if not root.is_dir():
        raise ValueError(f"Package directory is missing: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def package_content_digest(root: Path, canonical_manifest: dict[str, Any]) -> str:
    if not root.is_dir():
        raise ValueError(f"Workflow Package directory is missing: {root}")
    digest = hashlib.sha256()
    digest.update(b"manifest\0")
    digest.update(canonical_json(canonical_manifest))
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.name == "manifest.json"
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
        ):
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
