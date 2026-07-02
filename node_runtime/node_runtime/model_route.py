import json
import os
import secrets
from pathlib import Path

from pydantic import BaseModel


class DirectRoute(BaseModel):
    runtime_id: str
    base_url: str
    model_id: str
    api_key: str
    verified: bool = False
    fingerprint: str | None = None


class ModelRouteStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, route: DirectRoute) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        values = self._read()
        values[route.runtime_id] = route.model_dump()
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(values, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def set_revision(self, revision: int) -> None:
        values = self._read()
        values["__config_revision"] = {"value": revision}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(values, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def revision(self) -> int:
        return int(self._read().get("__config_revision", {}).get("value", 0))

    def get(self, runtime_id: str) -> DirectRoute | None:
        value = self._read().get(runtime_id)
        return DirectRoute.model_validate(value) if value else None


def build_route_env(
    route: dict, snapshot: dict, store: ModelRouteStore
) -> dict[str, str]:
    if route.get("mode") == "platform_gateway":
        if not all(route.get(key) for key in ("base_url", "api_key", "runtime_id")):
            raise ValueError("gateway route is incomplete")
        return {
            "ANTHROPIC_BASE_URL": str(route["base_url"]),
            "ANTHROPIC_API_KEY": str(route["api_key"]),
        }
    if route.get("mode") != "direct_anthropic":
        raise ValueError("unsupported model route")
    runtime_id = str(snapshot.get("runtime_profile_id", ""))
    direct = store.get(runtime_id)
    if (
        direct is None
        or not direct.verified
        or direct.base_url != snapshot.get("base_url")
        or direct.model_id != snapshot.get("model_id")
    ):
        raise ValueError("exact direct Anthropic endpoint and model are not verified")
    return {
        "ANTHROPIC_BASE_URL": direct.base_url,
        "ANTHROPIC_API_KEY": direct.api_key,
    }
