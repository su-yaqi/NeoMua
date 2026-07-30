import fcntl
import os
import shutil
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from node_runtime.artifacts.state import ArtifactState
from node_runtime.artifacts.validator import (
    validate_archive,
    validate_directory,
    validate_instruction,
)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ArtifactInstaller:
    def __init__(self, roots: dict[str, Path], node_id: str, namespace_id: str) -> None:
        self.roots = {key: value.resolve() for key, value in roots.items()}
        self.node_id = node_id
        self.namespace_id = namespace_id

    def recover(self) -> None:
        for root in self.roots.values():
            if not root.exists():
                continue
            with self._lock(root):
                for path in root.glob(".staging-*"):
                    if path.is_dir():
                        shutil.rmtree(path)
                for path in root.glob(".current-*"):
                    path.unlink(missing_ok=True)
                for path in root.glob(".state-*"):
                    path.unlink(missing_ok=True)
                state = ArtifactState(root)
                current_link = root / "current"
                if not current_link.is_symlink():
                    if current_link.exists():
                        state.write(None, None, degraded=True)
                    continue
                target = os.readlink(current_link)
                target_path = (root / target).resolve()
                versions = (root / "versions").resolve()
                if versions not in target_path.parents or not target_path.is_dir():
                    state.write(None, None, degraded=True)
                    continue
                current = target_path.name
                try:
                    persisted = state.read()
                except (OSError, ValueError):
                    persisted = {}
                persisted_current = persisted.get("current")
                previous = persisted.get("previous")
                if (
                    persisted_current
                    and persisted_current != current
                    and (versions / str(persisted_current)).is_dir()
                ):
                    previous = persisted_current
                if (
                    not previous
                    or previous == current
                    or not (versions / str(previous)).is_dir()
                ):
                    previous = None
                state.write(current, previous)

    @contextmanager
    def _lock(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(root / ".install.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def apply_archive(self, command: dict, archive_path: Path) -> dict:
        manifest, deployment = validate_instruction(
            command, node_id=self.node_id, namespace_id=self.namespace_id
        )
        root = self.roots.get(manifest.logical_target)
        if root is None:
            raise ValueError("logical target has no local allowlisted root")
        with self._lock(root):
            versions = root / "versions"
            versions.mkdir(exist_ok=True)
            validate_archive(archive_path, manifest, command["content_sha256"])
            staging = root / f".staging-{deployment.deployment_id}-{uuid.uuid4().hex}"
            staging.mkdir(mode=0o700)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for item in manifest.files:
                        destination = staging / item.path
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with (
                            archive.open(item.path) as source,
                            destination.open("xb") as target,
                        ):
                            shutil.copyfileobj(source, target, length=1024 * 1024)
                            target.flush()
                            os.fsync(target.fileno())
                version_path = versions / manifest.artifact_id
                if version_path.exists():
                    validate_directory(version_path, manifest)
                    shutil.rmtree(staging)
                else:
                    os.replace(staging, version_path)
                    _fsync_directory(versions)
                state = ArtifactState(root)
                current_link = root / "current"
                old = None
                if current_link.is_symlink():
                    old = Path(os.readlink(current_link)).name
                temporary_link = root / f".current-{uuid.uuid4().hex}"
                os.symlink(Path("versions") / manifest.artifact_id, temporary_link)
                os.replace(temporary_link, current_link)
                _fsync_directory(root)
                state.write(manifest.artifact_id, old)
                return {
                    "deployment_id": deployment.deployment_id,
                    "artifact_id": manifest.artifact_id,
                    "logical_target": manifest.logical_target,
                    "previous_artifact_id": old,
                }
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
