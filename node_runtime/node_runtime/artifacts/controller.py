import os
import tempfile
from pathlib import Path

import httpx

from node_runtime.artifacts.installer import ArtifactInstaller
from node_runtime.protocol import Envelope, envelope


class ArtifactController:
    def __init__(
        self,
        platform_url: str,
        node_id: str,
        installer: ArtifactInstaller,
        download_dir: Path,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.platform_url = platform_url.rstrip("/")
        self.node_id = node_id
        self.installer = installer
        self.download_dir = download_dir
        self.client = client

    async def handle(self, message: Envelope) -> list[Envelope]:
        if message.type != "artifact_deploy":
            return []
        payload = message.payload
        self.download_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(
            prefix="artifact-", suffix=".zip", dir=self.download_dir
        )
        archive_path = Path(name)
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=None)
        try:
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
            result = self.installer.apply_archive(payload, archive_path)
            return [envelope("artifact_applied", self.node_id, result)]
        except Exception as exc:
            try:
                os.close(descriptor)
            except OSError:
                pass
            return [
                envelope(
                    "artifact_failed", self.node_id,
                    {
                        "deployment_id": payload.get("deployment_id"),
                        "code": "artifact_validation_or_apply_failed",
                        "message": str(exc),
                    },
                )
            ]
        finally:
            archive_path.unlink(missing_ok=True)
            if owns_client:
                await client.aclose()
