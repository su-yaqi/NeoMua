import json
from pathlib import Path

from app.workflow_management.package import validate_package


def test_project_delivery_package_is_valid() -> None:
    manifest_path = Path(__file__).parents[1] / "manifest.json"
    manifest = validate_package(json.loads(manifest_path.read_text()))
    assert manifest.entry_nodes == ["clarify"]
    assert manifest.exit_nodes == ["prepare"]
