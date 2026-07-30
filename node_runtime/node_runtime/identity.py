import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel


class DeviceIdentity(BaseModel):
    node_id: str
    namespace_id: str | None = None
    private_key: str
    credential: str
    previous_credential_id: str | None = None


@dataclass(frozen=True)
class DeviceKeypair:
    private_key: str
    public_key: str


def generate_keypair() -> DeviceKeypair:
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return DeviceKeypair(
        private_key=base64.b64encode(private_bytes).decode(),
        public_key=base64.b64encode(public_bytes).decode(),
    )


def sign_device_payload(private_key: str, payload: str) -> str:
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key))
    return base64.b64encode(private.sign(payload.encode())).decode()


class IdentityStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, identity: DeviceIdentity) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(identity.model_dump(), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load(self) -> DeviceIdentity:
        return DeviceIdentity.model_validate_json(self.path.read_text(encoding="utf-8"))
