import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class NodeConfig(BaseModel):
    platform_url: HttpUrl
    node_name: str
    management_mode: Literal["service", "client", "legacy_unclassified"] = (
        "legacy_unclassified"
    )
    artifact_roots: dict[str, str] = Field(default_factory=dict)
    distribution_release_id: str | None = None
    bootstrap_session_id: str | None = None
    distribution_manifest_digest: str | None = None
    logical_installation_ref: str | None = None
    distribution_entrypoint: str | None = None
    distribution_components: list[dict[str, str]] = Field(default_factory=list)
    installation_receipt_id: str | None = None
    adapter_registry: dict[str, object] | None = None
    adapter_registry_digest: str | None = None
    distribution_signing_public_key: str | None = None


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, config: NodeConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config.model_dump(mode="json"), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load(self) -> NodeConfig:
        return NodeConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
