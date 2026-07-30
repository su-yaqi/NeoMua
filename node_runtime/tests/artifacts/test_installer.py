import base64
import hashlib
import io
import zipfile
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from node_runtime.artifacts.installer import ArtifactInstaller
from node_runtime.artifacts.state import ArtifactState
from node_runtime.artifacts.validator import ArtifactValidationError
import pytest
from node_runtime.artifacts.protocol import ArtifactManifest, DeploymentManifest


NODE_ID = "00000000-0000-0000-0000-000000000001"


def command_for(tmp_path, content: str, artifact_id: str) -> tuple[dict, object]:
    archive_path = tmp_path / f"{artifact_id}.zip"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("demo/SKILL.md", content)
    archive_bytes = output.getvalue()
    archive_path.write_bytes(archive_bytes)
    file_bytes = content.encode()
    manifest = ArtifactManifest.model_validate(
        {
            "schema_version": "1",
            "artifact_id": artifact_id,
            "version": artifact_id,
            "kind": "skill",
            "logical_target": "skills",
            "files": [
                {
                    "path": "demo/SKILL.md",
                    "sha256": hashlib.sha256(file_bytes).hexdigest(),
                    "size": len(file_bytes),
                    "symlink": False,
                }
            ],
        }
    )
    deployment = DeploymentManifest.model_validate(
        {
            "schema_version": "1",
            "namespace_id": "ns",
            "node_id": NODE_ID,
            "release_id": "release",
            "deployment_id": f"deployment-{artifact_id}",
            "artifact_id": artifact_id,
            "logical_target": "skills",
            "artifact_manifest_sha256": hashlib.sha256(
                manifest.canonical_bytes()
            ).hexdigest(),
            "valid_until": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode()
    command = {
        "artifact_id": artifact_id,
        "deployment_id": deployment.deployment_id,
        "content_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "manifest": manifest.model_dump(mode="json"),
        "deployment_manifest": deployment.model_dump(mode="json"),
        "signature": base64.b64encode(
            private.sign(manifest.canonical_bytes())
        ).decode(),
        "deployment_signature": base64.b64encode(
            private.sign(deployment.canonical_bytes())
        ).decode(),
        "signing_public_key": public,
    }
    return command, archive_path


def test_atomic_switch_and_recovery_preserve_current(tmp_path) -> None:
    root = tmp_path / "skills"
    installer = ArtifactInstaller({"skills": root}, NODE_ID, "ns")
    first, first_zip = command_for(tmp_path, "v1", "artifact-v1")
    installer.apply_archive(first, first_zip)
    assert (root / "current" / "demo" / "SKILL.md").read_text() == "v1"
    abandoned = root / ".staging-power-loss"
    abandoned.mkdir()
    (abandoned / "partial").write_text("partial")
    installer.recover()
    assert not abandoned.exists()
    assert (root / "current" / "demo" / "SKILL.md").read_text() == "v1"
    second, second_zip = command_for(tmp_path, "v2", "artifact-v2")
    result = installer.apply_archive(second, second_zip)
    assert result["previous_artifact_id"] == "artifact-v1"
    assert (root / "current" / "demo" / "SKILL.md").read_text() == "v2"


def test_existing_tampered_version_is_not_reused(tmp_path) -> None:
    root = tmp_path / "skills"
    installer = ArtifactInstaller({"skills": root}, NODE_ID, "ns")
    command, archive = command_for(tmp_path, "safe", "artifact-v1")
    installer.apply_archive(command, archive)
    (root / "versions" / "artifact-v1" / "demo" / "SKILL.md").write_text("tampered")
    with pytest.raises(ArtifactValidationError, match="hash"):
        installer.apply_archive(command, archive)


def test_recovery_repairs_state_after_power_loss_post_symlink_switch(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "skills"
    installer = ArtifactInstaller({"skills": root}, NODE_ID, "ns")
    first, first_zip = command_for(tmp_path, "v1", "artifact-v1")
    installer.apply_archive(first, first_zip)
    second, second_zip = command_for(tmp_path, "v2", "artifact-v2")
    original_write = ArtifactState.write

    def fail_state_write(self, current, previous, *, degraded=False):
        raise OSError("simulated power loss before state persistence")

    monkeypatch.setattr(ArtifactState, "write", fail_state_write)
    with pytest.raises(OSError, match="simulated power loss"):
        installer.apply_archive(second, second_zip)
    assert (root / "current" / "demo" / "SKILL.md").read_text() == "v2"

    monkeypatch.setattr(ArtifactState, "write", original_write)
    installer.recover()
    recovered = ArtifactState(root).read()
    assert recovered["current"] == "artifact-v2"
    assert recovered["previous"] == "artifact-v1"
