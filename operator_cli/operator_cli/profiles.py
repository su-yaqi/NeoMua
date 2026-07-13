import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Profile:
    name: str
    api_url: str
    namespace_id: str | None
    output: str
    credential_id: str


class ProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(
            os.environ.get("NEOMUA_CONFIG_HOME", Path.home() / ".config" / "neomua")
        )
        self.path = self.root / "profiles.json"

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "current": None, "profiles": {}}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            value = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Profile file is damaged: {self.path}") from exc
        if value.get("schema_version") != SCHEMA_VERSION or not isinstance(
            value.get("profiles"), dict
        ):
            raise RuntimeError(
                "Profile schema is unsupported; explicit migration is required"
            )
        return cast(dict[str, Any], value)

    def save(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        descriptor, name = tempfile.mkstemp(
            prefix="profiles-", suffix=".json", dir=self.root
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(value, target, ensure_ascii=False, sort_keys=True, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def validate_url(api_url: str) -> str:
        parsed = urlparse(api_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API URL must be a canonical HTTPS origin")
        return api_url.rstrip("/")

    def put(self, profile: Profile, *, select: bool = True) -> None:
        value = self.load()
        normalized = Profile(
            **{**asdict(profile), "api_url": self.validate_url(profile.api_url)}
        )
        value["profiles"][profile.name] = asdict(normalized)
        if select:
            value["current"] = profile.name
        self.save(value)

    def get(self, name: str | None = None) -> Profile:
        value = self.load()
        selected = name or value.get("current")
        raw = value["profiles"].get(selected) if selected else None
        if raw is None:
            raise RuntimeError("No active NeoMua profile")
        return Profile(**raw)

    def delete(self, name: str) -> Profile:
        value = self.load()
        raw = value["profiles"].pop(name, None)
        if raw is None:
            raise RuntimeError(f"Profile not found: {name}")
        if value.get("current") == name:
            value["current"] = None
        self.save(value)
        return Profile(**raw)
