import json
import os
from pathlib import Path

from pydantic import BaseModel, HttpUrl


class NodeConfig(BaseModel):
    platform_url: HttpUrl
    node_name: str


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, config: NodeConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        self.path.write_text(
            json.dumps(config.model_dump(mode="json")), encoding="utf-8"
        )
        os.chmod(self.path, 0o644)

    def load(self) -> NodeConfig:
        return NodeConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
