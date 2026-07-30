import pytest

from node_runtime.artifacts.validator import (
    ArtifactValidationError,
    validate_instruction,
)
from .test_installer import NODE_ID, command_for


def test_tampered_manifest_is_rejected(tmp_path) -> None:
    command, _ = command_for(tmp_path, "safe", "artifact-1")
    command["manifest"]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ArtifactValidationError):
        validate_instruction(command, node_id=NODE_ID, namespace_id="ns")


def test_cross_node_deployment_is_rejected(tmp_path) -> None:
    command, _ = command_for(tmp_path, "safe", "artifact-1")
    with pytest.raises(ArtifactValidationError, match="node scope"):
        validate_instruction(
            command,
            node_id="00000000-0000-0000-0000-000000000099",
            namespace_id="ns",
        )


def test_cross_namespace_deployment_is_rejected(tmp_path) -> None:
    command, _ = command_for(tmp_path, "safe", "artifact-1")
    with pytest.raises(ArtifactValidationError, match="namespace scope"):
        validate_instruction(command, node_id=NODE_ID, namespace_id="other")
