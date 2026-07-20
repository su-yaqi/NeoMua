"""Verification and staging for immutable Runtime Node distributions."""

import base64
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class DistributionValidationError(RuntimeError):
    pass


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def verify_signed_manifest(
    signed: dict[str, Any], *, trusted_signing_public_key: str, mode: str
) -> dict[str, Any]:
    manifest = signed.get("manifest")
    if not isinstance(manifest, dict):
        raise DistributionValidationError("bootstrap manifest is missing")
    manifest_bytes = canonical_manifest_bytes(manifest)
    if hashlib.sha256(manifest_bytes).hexdigest() != signed.get("manifest_digest"):
        raise DistributionValidationError("bootstrap manifest digest mismatch")
    if signed.get("signing_public_key") != trusted_signing_public_key:
        raise DistributionValidationError("bootstrap signing key is not trusted")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_signing_public_key, validate=True)
        ).verify(
            base64.b64decode(str(signed.get("signature")), validate=True),
            manifest_bytes,
        )
    except (ValueError, InvalidSignature) as exc:
        raise DistributionValidationError(
            "bootstrap manifest signature is invalid"
        ) from exc
    if manifest.get("management_mode") != mode:
        raise DistributionValidationError("bootstrap manifest mode mismatch")
    return manifest


def _safe_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise DistributionValidationError("distribution contains an unsafe path")
    return path


def stage_distribution_archive(
    archive_path: Path, destination: Path, manifest: dict[str, Any]
) -> Path:
    expected_files_raw = manifest.get("files")
    if not isinstance(expected_files_raw, list):
        raise DistributionValidationError("distribution file manifest is invalid")
    expected: dict[str, dict[str, Any]] = {}
    for item in expected_files_raw:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise DistributionValidationError("distribution file entry is invalid")
        path = _safe_path(item["path"]).as_posix()
        if path in expected:
            raise DistributionValidationError("distribution file paths are duplicated")
        expected[path] = item
    destination.mkdir(parents=True, mode=0o700)
    seen: set[str] = set()
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise DistributionValidationError("distribution archive is invalid") from exc
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = _safe_path(info.filename)
            normalized = path.as_posix()
            if normalized in seen or normalized not in expected:
                raise DistributionValidationError(
                    "distribution archive does not match its file manifest"
                )
            seen.add(normalized)
            mode = (info.external_attr >> 16) & 0o177777
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise DistributionValidationError("distribution symlinks are forbidden")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
            digest = hashlib.sha256()
            size = 0
            try:
                with (
                    os.fdopen(descriptor, "wb") as output,
                    archive.open(info) as source,
                ):
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                target.unlink(missing_ok=True)
                raise
            expected_item = expected[normalized]
            if size != expected_item.get(
                "size"
            ) or digest.hexdigest() != expected_item.get("sha256"):
                target.unlink(missing_ok=True)
                raise DistributionValidationError(
                    "distribution file digest or size mismatch"
                )
            os.chmod(target, 0o700 if expected_item.get("executable") else 0o600)
    if seen != set(expected):
        raise DistributionValidationError("distribution archive is incomplete")
    entrypoint = destination.joinpath(*_safe_path(str(manifest["entrypoint"])).parts)
    if not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        raise DistributionValidationError("distribution entrypoint is unavailable")
    return entrypoint


def download_distribution(
    client: httpx.Client,
    *,
    platform_url: str,
    bootstrap_token: str,
    manifest: dict[str, Any],
    destination: Path,
) -> Path:
    release_id = str(manifest.get("release_id"))
    download_path = manifest.get("download_path")
    if download_path != f"/api/v1/node/bootstrap/distributions/{release_id}":
        raise DistributionValidationError("distribution download path is invalid")
    archive_path = destination.parent / f".{release_id}.download"
    digest = hashlib.sha256()
    size = 0
    try:
        with client.stream(
            "GET",
            f"{platform_url}{download_path}",
            headers={"X-Bootstrap-Token": bootstrap_token},
        ) as response:
            response.raise_for_status()
            descriptor = os.open(
                archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if size != manifest.get("archive_size") or digest.hexdigest() != manifest.get(
            "archive_sha256"
        ):
            raise DistributionValidationError(
                "distribution archive digest or size mismatch"
            )
        return stage_distribution_archive(archive_path, destination, manifest)
    finally:
        archive_path.unlink(missing_ok=True)
