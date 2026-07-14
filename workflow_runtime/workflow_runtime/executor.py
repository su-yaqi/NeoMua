import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from workflow_runtime.package import package_content_digest
from workflow_runtime.sdk import NODE_HANDLERS, VALIDATORS, WorkflowRunContext

MAX_SPEC_FILES = 100
MAX_SPEC_BYTES = 1024 * 1024


def _workflow_apps_root() -> Path:
    configured = os.environ.get("NEOMUA_WORKFLOW_APPS_ROOT")
    candidates = [
        Path(configured) if configured else None,
        Path("/app/workflow_apps"),
        Path(__file__).resolve().parents[2] / "workflow_apps",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    raise RuntimeError("Bundled Workflow directory is unavailable in this Runtime")


def _load_module(path: Path) -> ModuleType:
    # Use the same stable module identity as the platform Package validator.
    # This keeps in-process tests idempotent while Runtime workers still load
    # exactly the bundled files in their own process.
    name = "neomua_workflow_" + "_".join(path.with_suffix("").parts[-5:])
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load bundled Workflow module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_bundled_code() -> None:
    root = _workflow_apps_root()
    for pattern in ("*/backend/nodes/*.py", "*/backend/validators/*.py"):
        for path in sorted(root.glob(pattern)):
            if path.name != "__init__.py":
                _load_module(path)


def _package_root(slug: str) -> Path:
    for manifest_path in sorted(_workflow_apps_root().glob("*/manifest.json")):
        raw = json.loads(manifest_path.read_text())
        if raw.get("slug") == slug:
            return manifest_path.parent
    raise ValueError(f"Workflow Package is not bundled: {slug}")


def _context(raw: dict[str, Any]) -> WorkflowRunContext:
    return WorkflowRunContext(
        workflow_instance_id=str(raw["workflow_instance_id"]),
        node_key=str(raw["node_key"]),
        runtime_id=str(raw["runtime_id"]),
        project_id=(str(raw["project_id"]) if raw.get("project_id") else None),
        input=dict(raw["input"]),
        previous_output=(
            dict(raw["previous_output"])
            if isinstance(raw.get("previous_output"), dict)
            else None
        ),
        change_summary=dict(raw.get("change_summary", {})),
        idempotency_key=str(raw["idempotency_key"]),
    )


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _normalized_remote(value: str) -> str:
    return value.rstrip("/").removesuffix(".git")


def _probe_repository(payload: dict[str, Any]) -> dict[str, Any]:
    raw_workspace = Path(str(payload["workspace_path"]))
    if not raw_workspace.is_absolute():
        raise ValueError("workspace_path must be absolute")
    workspace = raw_workspace.resolve(strict=True)
    roots = [
        Path(str(value)).resolve(strict=True) for value in payload["allowed_roots"]
    ]
    if not roots or not any(_inside(workspace, root) for root in roots):
        raise ValueError("workspace is outside Runtime allowed roots")
    if not (workspace / ".git").exists():
        _git(workspace, "rev-parse", "--git-dir")
    origin = _git(workspace, "remote", "get-url", "origin")
    expected_remote = str(payload["remote_url"])
    if _normalized_remote(origin) != _normalized_remote(expected_remote):
        raise ValueError("workspace origin does not match the configured repository")
    commit = _git(workspace, "rev-parse", "HEAD")
    branch = payload.get("default_branch")
    if branch:
        _git(workspace, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")

    total_bytes = 0
    total_files = 0
    spec_results: list[dict[str, Any]] = []
    for item in payload.get("spec_locations", []):
        pure = PurePosixPath(str(item["path"]))
        if pure.is_absolute() or ".." in pure.parts or str(pure) in {"", "."}:
            raise ValueError("Spec path is not a normalized repository-relative path")
        candidate = workspace.joinpath(*pure.parts).resolve(strict=True)
        if not _inside(candidate, workspace):
            raise ValueError("Spec path escapes the repository through a symlink")
        expected_type = str(item["location_type"])
        if expected_type == "file" and not candidate.is_file():
            raise ValueError(f"Spec file does not exist: {pure}")
        if expected_type == "directory" and not candidate.is_dir():
            raise ValueError(f"Spec directory does not exist: {pure}")
        paths = [candidate] if candidate.is_file() else sorted(candidate.rglob("*"))
        files: list[dict[str, Any]] = []
        for path in paths:
            if not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if not _inside(resolved, workspace):
                raise ValueError(
                    "Spec content escapes the repository through a symlink"
                )
            data = resolved.read_bytes()
            total_files += 1
            total_bytes += len(data)
            if total_files > MAX_SPEC_FILES or total_bytes > MAX_SPEC_BYTES:
                raise ValueError("Spec content exceeds the Runtime snapshot limit")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"Spec content is not UTF-8 text: {resolved}") from exc
            files.append(
                {
                    "path": resolved.relative_to(workspace).as_posix(),
                    "content_digest": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "content": content,
                }
            )
        spec_results.append(
            {
                "spec_location_id": str(item["id"]),
                "path": str(pure),
                "location_type": expected_type,
                "files": files,
                "content_digest": hashlib.sha256(
                    "".join(file["content_digest"] for file in files).encode()
                ).hexdigest(),
            }
        )
    return {
        "workspace_ref": str(payload["workspace_ref"]),
        "workspace_path": str(workspace),
        "remote_url": origin,
        "commit": commit,
        "spec_locations": spec_results,
    }


def execute_runtime_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "repository_probe":
        return _probe_repository(payload)
    package_root = _package_root(str(payload["package_slug"]))
    manifest = json.loads((package_root / "manifest.json").read_text())
    local_digest = package_content_digest(package_root, manifest)
    if local_digest != payload.get("package_digest"):
        raise ValueError("Workflow Package digest does not match the Runtime bundle")
    _load_bundled_code()
    context = _context(dict(payload["context"]))
    key = str(payload["component_key"])
    if kind == "workflow_handler":
        handler = NODE_HANDLERS.get(key)
        if handler is None:
            raise ValueError(f"Workflow handler is not bundled: {key}")
        return {"output": handler(context)}
    if kind == "workflow_validator":
        validator = VALIDATORS.get(key)
        if validator is None:
            raise ValueError(f"Workflow validator is not bundled: {key}")
        passed, details = validator(context)
        return {"passed": bool(passed), "details": details}
    raise ValueError(f"Unsupported Runtime job kind: {kind}")
