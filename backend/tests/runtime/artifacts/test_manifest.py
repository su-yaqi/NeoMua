import pytest

from app.runtime.artifacts.manifest import (
    ArtifactFile,
    ArtifactKind,
    ArtifactManifest,
    LogicalTarget,
    UnsafeArtifactPath,
    validate_relative_path,
)


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../escape", "skills/../../escape", "skills\\escape", "./x", "x//y"]
)
def test_rejects_unsafe_manifest_path(path: str) -> None:
    with pytest.raises(UnsafeArtifactPath):
        validate_relative_path(path)


def test_rejects_symlink_entry() -> None:
    with pytest.raises(ValueError, match="symlink"):
        ArtifactFile(path="skill/link", sha256="0" * 64, size=1, symlink=True)


def test_manifest_canonical_bytes_are_stable() -> None:
    manifest = ArtifactManifest(
        artifact_id="artifact-1", version="1.0.0", kind=ArtifactKind.SKILL,
        logical_target=LogicalTarget.SKILLS,
        files=[ArtifactFile(path="skill/SKILL.md", sha256="a" * 64, size=10)],
    )
    assert manifest.canonical_bytes() == manifest.canonical_bytes()
