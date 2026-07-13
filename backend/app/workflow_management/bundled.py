import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def workflow_apps_root() -> Path:
    return Path(__file__).resolve().parents[3] / "workflow_apps"


def bundled_package_root(slug: str) -> Path:
    for manifest_path in sorted(workflow_apps_root().glob("*/manifest.json")):
        raw = json.loads(manifest_path.read_text())
        if raw.get("slug") == slug:
            return manifest_path.parent
    raise RuntimeError(f"Bundled Workflow Package is missing: {slug}")


def _load_module(path: Path) -> ModuleType:
    module_name = "neomua_workflow_" + "_".join(path.with_suffix("").parts[-5:])
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Workflow module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_bundled_workflow_code() -> None:
    root = workflow_apps_root()
    if not root.exists():
        raise RuntimeError(f"Bundled Workflow directory is missing: {root}")
    for pattern in ("*/backend/nodes/*.py", "*/backend/validators/*.py"):
        for path in sorted(root.glob(pattern)):
            if path.name != "__init__.py":
                _load_module(path)


def bundled_manifests() -> list[dict[str, Any]]:
    load_bundled_workflow_code()
    return [
        json.loads(path.read_text())
        for path in sorted(workflow_apps_root().glob("*/manifest.json"))
    ]
