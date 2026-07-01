import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from node_runtime.connection import handshake_headers, reconnect_delay
from node_runtime.identity import DeviceIdentity, generate_keypair


def test_handshake_is_signed_by_persisted_device_key() -> None:
    keypair = generate_keypair()
    identity = DeviceIdentity(
        node_id="node-1", private_key=keypair.private_key, credential="credential"
    )
    headers = handshake_headers(identity, timestamp=1_700_000_000)
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(keypair.public_key))
    public.verify(
        base64.b64decode(headers["X-Node-Signature"]),
        b"neomua-ws-v1:1700000000",
    )
    assert headers["Authorization"] == "Bearer credential"


def test_reconnect_delay_is_exponential_and_capped() -> None:
    assert reconnect_delay(0, jitter=0) == 1
    assert reconnect_delay(4, jitter=0) == 16
    assert reconnect_delay(20, jitter=0) == 60
    assert reconnect_delay(20, jitter=1) <= 60
