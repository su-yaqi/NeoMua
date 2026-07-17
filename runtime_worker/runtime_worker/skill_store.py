"""Verified content-addressed Skill cache used independently from Agent Releases."""

import base64
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from runtime_worker.release_store import canonical_bytes, digest


class SkillCacheMiss(ValueError):
    pass


class SkillStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _verify_payload(payload: dict[str, Any]) -> dict[str, Any]:
        manifest = payload["manifest"]
        if digest(manifest) != payload["manifest_digest"]:
            raise ValueError("Skill manifest digest mismatch")
        try:
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(payload["signing_public_key"])
            ).verify(
                base64.b64decode(payload["signature"]),
                canonical_bytes(manifest),
            )
        except (KeyError, ValueError, InvalidSignature) as exc:
            raise ValueError("Skill bundle signature is invalid") from exc
        if manifest.get("schema_version") != "skill-bundle-v1":
            raise ValueError("Unsupported Skill bundle schema")
        return manifest

    def apply_archive(
        self, payload: dict[str, Any], archive_path: Path
    ) -> dict[str, Any]:
        manifest = self._verify_payload(payload)
        archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        if archive_digest != manifest["content_sha256"]:
            raise ValueError("Skill archive digest mismatch")
        versions = self.root / "versions"
        versions.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = versions / archive_digest
        if not destination.exists():
            staging = self.root / f".staging-{uuid.uuid4().hex}"
            staging.mkdir(parents=True, mode=0o700)
            expected = {str(item["path"]): item for item in manifest["files"]}
            seen: set[str] = set()
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        relative = PurePosixPath(info.filename)
                        mode = (info.external_attr >> 16) & 0o170000
                        if (
                            relative.is_absolute()
                            or ".." in relative.parts
                            or "\\" in info.filename
                            or mode == stat.S_IFLNK
                            or info.filename not in expected
                        ):
                            raise ValueError("Skill archive contains an unsafe file")
                        raw = archive.read(info)
                        declared = expected[info.filename]
                        if (
                            len(raw) != int(declared["size"])
                            or hashlib.sha256(raw).hexdigest() != declared["sha256"]
                        ):
                            raise ValueError("Skill file digest mismatch")
                        target = staging / Path(*relative.parts)
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with target.open("xb") as output:
                            output.write(raw)
                            output.flush()
                            os.fsync(output.fileno())
                        seen.add(info.filename)
                if seen != set(expected):
                    raise ValueError("Skill archive file set differs from manifest")
                metadata = staging / ".neomua-skill.json"
                metadata.write_bytes(canonical_bytes(manifest))
                os.chmod(metadata, 0o600)
                os.replace(staging, destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        self.verify_cached(payload)
        return {
            "content_sha256": archive_digest,
            "bytes_downloaded": archive_path.stat().st_size,
        }

    def _activate(
        self,
        manifest: dict[str, Any],
        destination: Path,
        generation: int,
        runtime_profile_id: str,
    ) -> None:
        identity_root = (
            self.root
            / "runtimes"
            / runtime_profile_id
            / "identities"
            / str(manifest["skill_id"])
        )
        identity_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = identity_root / f".current-{uuid.uuid4().hex}"
        os.symlink(destination, temporary)
        os.replace(temporary, identity_root / "current")
        state = identity_root / f".applied-{uuid.uuid4().hex}"
        state.write_bytes(
            canonical_bytes(
                {
                    "generation": generation,
                    "version_id": manifest["version_id"],
                    "content_sha256": manifest["content_sha256"],
                }
            )
        )
        os.chmod(state, 0o600)
        os.replace(state, identity_root / "applied.json")

    def verify_cached(self, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = self._verify_payload(payload)
        destination = self.root / "versions" / str(manifest["content_sha256"])
        if not destination.is_dir():
            raise SkillCacheMiss("Skill bundle is not cached")
        try:
            stored = json.loads((destination / ".neomua-skill.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("cached Skill metadata is damaged") from exc
        if stored != manifest:
            raise ValueError("cached Skill metadata differs from signed manifest")
        return {
            "content_sha256": str(manifest["content_sha256"]),
            "bytes_downloaded": 0,
        }

    def activate(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.verify_cached(payload)
        manifest = payload["manifest"]
        destination = self.root / "versions" / str(manifest["content_sha256"])
        self._activate(
            manifest,
            destination,
            int(payload["generation"]),
            str(payload["runtime_profile_id"]),
        )
        return result

    def resolve(
        self, runtime_profile_id: str, skill_id: str
    ) -> tuple[Path, dict[str, Any]]:
        identity_root = (
            self.root / "runtimes" / runtime_profile_id / "identities" / skill_id
        )
        try:
            state = json.loads((identity_root / "applied.json").read_text("utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("skill_not_ready") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("skill_cache_corrupt") from exc
        content_sha256 = state.get("content_sha256")
        if not isinstance(content_sha256, str):
            raise ValueError("skill_not_synchronized")
        resolved = self.root / "versions" / content_sha256
        if not resolved.is_dir():
            raise ValueError("skill_cache_corrupt")
        metadata_path = resolved / ".neomua-skill.json"
        try:
            metadata = json.loads(metadata_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("synchronized Skill metadata is damaged") from exc
        if str(metadata.get("skill_id")) != skill_id:
            raise ValueError("synchronized Skill identity mismatch")
        if (
            state.get("version_id") != metadata.get("version_id")
            or state.get("content_sha256") != metadata.get("content_sha256")
            or not isinstance(state.get("generation"), int)
        ):
            raise ValueError("synchronized Skill applied state is damaged")
        metadata["runtime_generation"] = state["generation"]
        return resolved, metadata

    def bind_task(
        self,
        task_id: str,
        runtime_profile_id: str,
        skills: list[dict[str, Any]],
    ) -> tuple[Path, list[dict[str, Any]]]:
        task_root = self.root / "task-bindings" / task_id
        if task_root.exists():
            shutil.rmtree(task_root)
        skills_root = task_root / ".claude" / "skills"
        skills_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        evidence = self.usage_evidence(runtime_profile_id, skills)
        sources = {
            str(item["skill_id"]): self.resolve(
                runtime_profile_id, str(item["skill_id"])
            )[0]
            for item in evidence
        }
        for item in skills:
            skill_id = str(item["id"])
            slug = str(item["slug"])
            if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
                raise ValueError("Skill slug is unsafe")
            os.symlink(sources[skill_id], skills_root / slug)
        return task_root, evidence

    def usage_evidence(
        self, runtime_profile_id: str, skills: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for item in skills:
            skill_id = str(item["id"])
            _, metadata = self.resolve(runtime_profile_id, skill_id)
            evidence.append(
                {
                    "skill_id": skill_id,
                    "version_id": str(metadata["version_id"]),
                    "version": str(metadata["version"]),
                    "content_sha256": str(metadata["content_sha256"]),
                    "runtime_generation": int(metadata["runtime_generation"]),
                }
            )
        return evidence

    def remove_task_binding(self, task_id: str) -> None:
        shutil.rmtree(self.root / "task-bindings" / task_id, ignore_errors=True)


def temporary_skill_archive(root: Path) -> tuple[int, Path]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix="skill-", suffix=".zip", dir=root)
    return descriptor, Path(name)
