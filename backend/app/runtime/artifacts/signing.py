import base64
import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from app.core.config import settings


class ArtifactSigner:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self.private_key = private_key

    @classmethod
    def generate(cls) -> "ArtifactSigner":
        return cls(Ed25519PrivateKey.generate())

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self.private_key.sign(payload)).decode()

    def public_key(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw).decode()

    def verify(self, payload: bytes, signature: str) -> bool:
        return verify_signature(self.public_key(), payload, signature)


def verify_signature(public_key: str, payload: bytes, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            base64.b64decode(signature), payload
        )
        return True
    except (ValueError, InvalidSignature):
        return False


def configured_artifact_signer() -> ArtifactSigner:
    seed = hmac.new(
        settings.SECRET_KEY.encode(), b"neomua-artifact-signing-v1", hashlib.sha256
    ).digest()
    return ArtifactSigner(Ed25519PrivateKey.from_private_bytes(seed))
