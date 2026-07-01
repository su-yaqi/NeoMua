from app.runtime.artifacts.manifest import (
    ArtifactFile,
    ArtifactKind,
    ArtifactManifest,
    LogicalTarget,
)
from app.runtime.artifacts.signing import ArtifactSigner


def test_modified_manifest_fails_signature() -> None:
    signer = ArtifactSigner.generate()
    manifest = ArtifactManifest(
        artifact_id="artifact-1", version="1", kind=ArtifactKind.AGENT,
        logical_target=LogicalTarget.AGENTS,
        files=[ArtifactFile(path="agent/AGENT.md", sha256="a" * 64, size=12)],
    )
    signature = signer.sign(manifest.canonical_bytes())
    assert signer.verify(manifest.canonical_bytes(), signature)
    manifest.files[0].sha256 = "0" * 64
    assert not signer.verify(manifest.canonical_bytes(), signature)
