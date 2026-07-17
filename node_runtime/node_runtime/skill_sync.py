"""Node-side Skill synchronization controller."""

import os
import tempfile
from pathlib import Path

import httpx

from node_runtime.protocol import Envelope, envelope
from runtime_worker.skill_store import SkillCacheMiss, SkillStore


class NodeSkillSyncController:
    def __init__(
        self,
        platform_url: str,
        node_id: str,
        root: Path,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.platform_url = platform_url.rstrip("/")
        self.node_id = node_id
        self.store = SkillStore(root)
        self.download_dir = root / "downloads"
        self.client = client
        self.pending: dict[str, dict] = {}

    async def handle(self, message: Envelope) -> list[Envelope]:
        if message.type == "skill_sync_requested":
            return [
                envelope(
                    "skill_sync_desired_request",
                    self.node_id,
                    {
                        "runtime_skill_state_id": message.payload[
                            "runtime_skill_state_id"
                        ],
                        "generation": message.payload["generation"],
                    },
                )
            ]
        if message.type == "skill_sync_commit":
            attempt_id = str(message.payload["attempt_id"])
            payload = self.pending.pop(attempt_id, None)
            if payload is None:
                return []
            try:
                result = self.store.activate(payload)
                body = {"status": "committed", **result}
            except Exception as exc:
                body = {
                    "status": "failed",
                    "content_sha256": payload["manifest"]["content_sha256"],
                    "error": {"code": "skill_commit_failed", "message": str(exc)},
                }
            return [
                envelope(
                    "skill_sync_result",
                    self.node_id,
                    {"attempt_id": attempt_id, **body},
                )
            ]
        if message.type != "skill_sync_desired":
            return []
        payload = message.payload
        self.download_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(
            prefix="skill-", suffix=".zip", dir=self.download_dir
        )
        archive = Path(name)
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=None)
        try:
            try:
                result = self.store.verify_cached(payload)
            except SkillCacheMiss:
                result = None
            if result is not None:
                os.close(descriptor)
            else:
                async with client.stream(
                    "GET",
                    f"{self.platform_url}{payload['download_path']}",
                    params={"token": payload["download_token"]},
                ) as response:
                    response.raise_for_status()
                    with os.fdopen(descriptor, "wb") as target:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                result = self.store.apply_archive(payload, archive)
            body = {"status": "verified", **result}
            self.pending[str(payload["attempt_id"])] = payload
        except Exception as exc:
            try:
                os.close(descriptor)
            except OSError:
                pass
            body = {
                "status": "failed",
                "content_sha256": payload.get("manifest", {}).get(
                    "content_sha256", "0" * 64
                ),
                "bytes_downloaded": archive.stat().st_size if archive.exists() else 0,
                "error": {"code": "skill_sync_failed", "message": str(exc)},
            }
        finally:
            archive.unlink(missing_ok=True)
            if owns_client:
                await client.aclose()
        return [
            envelope(
                "skill_sync_result",
                self.node_id,
                {"attempt_id": payload.get("attempt_id"), **body},
            )
        ]
