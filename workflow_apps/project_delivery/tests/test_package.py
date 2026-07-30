import json
from pathlib import Path

from workflow_runtime.executor import execute_runtime_job
from workflow_runtime.package import package_content_digest

from app.workflow_management.package import validate_package


def test_project_delivery_package_is_valid() -> None:
    manifest_path = Path(__file__).parents[1] / "manifest.json"
    manifest = validate_package(json.loads(manifest_path.read_text()))
    assert manifest.entry_nodes == ["clarify"]
    assert manifest.exit_nodes == ["prepare"]
    assert package_content_digest(
        manifest_path.parent, manifest.model_dump(mode="json", exclude_none=True)
    )


def test_project_delivery_validator_rejects_incomplete_clarification() -> None:
    manifest_path = Path(__file__).parents[1] / "manifest.json"
    raw = json.loads(manifest_path.read_text())
    result = execute_runtime_job(
        "workflow_validator",
        {
            "package_slug": raw["slug"],
            "package_digest": package_content_digest(manifest_path.parent, raw),
            "component_key": "project_delivery.has_clarification",
            "context": {
                "workflow_instance_id": "workflow-1",
                "node_key": "prepare",
                "runtime_id": "runtime-1",
                "project_id": "project-1",
                "input": {"upstream": {}},
                "previous_output": None,
                "change_summary": {},
                "idempotency_key": "validator-failure",
            },
        },
    )
    assert result["passed"] is False
    assert result["details"]["code"] == "clarification_incomplete"


def test_project_delivery_handler_uses_upstream_revision() -> None:
    manifest_path = Path(__file__).parents[1] / "manifest.json"
    raw = json.loads(manifest_path.read_text())
    result = execute_runtime_job(
        "workflow_handler",
        {
            "package_slug": raw["slug"],
            "package_digest": package_content_digest(manifest_path.parent, raw),
            "component_key": "project_delivery.prepare",
            "context": {
                "workflow_instance_id": "workflow-1",
                "node_key": "prepare",
                "runtime_id": "runtime-1",
                "project_id": "project-1",
                "input": {
                    "upstream": {
                        "clarify": {
                            "output": {
                                "goals": ["g1", "g2"],
                                "acceptance_criteria": ["a1"],
                            }
                        }
                    },
                    "upstream_revisions": {"clarify": 4},
                },
                "previous_output": {"source_revision": 3},
                "change_summary": {"new_input_digest": "new"},
                "idempotency_key": "handler-rerun",
            },
        },
    )
    assert result["output"] == {
        "summary": "目标 2 项，验收标准 1 项。",
        "source_revision": 4,
    }
