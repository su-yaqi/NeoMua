import json
import os
import secrets
from pathlib import Path


class ArtifactState:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def path(self) -> Path:
        return self.root / "state.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {"current": None, "previous": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(
        self, current: str | None, previous: str | None, *, degraded: bool = False
    ) -> None:
        temporary = self.root / f".state-{secrets.token_hex(8)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "current": current,
                    "previous": previous,
                    "degraded": degraded,
                },
                handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
