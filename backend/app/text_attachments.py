import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.api.routes.runtime_artifacts import artifact_storage
from app.core.config import settings

TEXT_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
TEXT_ATTACHMENT_TYPES = {
    "text/plain": {".txt"},
    "text/markdown": {".md", ".markdown"},
    "application/json": {".json"},
}


async def store_text_attachment(
    file: UploadFile, *, storage_prefix: str
) -> dict[str, Any]:
    filename = file.filename or ""
    if (
        not filename
        or len(filename) > 255
        or filename != Path(filename).name
        or "/" in filename
        or "\\" in filename
    ):
        raise HTTPException(422, "Attachment filename must be a plain basename")
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    if (
        content_type not in TEXT_ATTACHMENT_TYPES
        or suffix not in TEXT_ATTACHMENT_TYPES[content_type]
    ):
        raise HTTPException(
            415,
            {
                "code": "attachment_type_not_allowed",
                "allowed": {
                    value: sorted(extensions)
                    for value, extensions in TEXT_ATTACHMENT_TYPES.items()
                },
            },
        )
    temp_root = Path(settings.ARTIFACT_TEMP_DIR or tempfile.gettempdir())
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix="neomua-attachment-", dir=temp_root)
    temporary = Path(name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > TEXT_ATTACHMENT_MAX_BYTES:
                    raise HTTPException(413, "Attachment exceeds the 5 MiB limit")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        raw = temporary.read_bytes()
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Attachment must be valid UTF-8 text") from exc
        if "\x00" in decoded or any(
            ord(char) < 32 and char not in {"\t", "\n", "\r"} for char in decoded
        ):
            raise HTTPException(422, "Attachment contains unsafe control bytes")
        if content_type == "application/json":
            try:
                json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise HTTPException(422, "JSON attachment is invalid") from exc
        content_digest = digest.hexdigest()
        storage_ref = f"{storage_prefix}/sha256/{content_digest[:2]}/{content_digest}"
        artifact_storage().put_once(storage_ref, temporary)
        return {
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "content_digest": content_digest,
            "storage_ref": storage_ref,
            "scan_details": {
                "scanner": "strict-text-v1",
                "checks": [
                    "size",
                    "mime_extension_allowlist",
                    "utf8",
                    "control_bytes",
                    *(["json_parse"] if content_type == "application/json" else []),
                ],
            },
        }
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
