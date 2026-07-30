import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from node_runtime.connection import handshake_headers, reconnect_delay
from node_runtime.identity import DeviceIdentity, generate_keypair
from node_runtime.secrets import node_secret_fingerprints


def test_handshake_is_signed_by_persisted_device_key() -> None:
    keypair = generate_keypair()
    identity = DeviceIdentity(
        node_id="node-1", private_key=keypair.private_key, credential="credential"
    )
    headers = handshake_headers(identity, timestamp=1_700_000_000)
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(keypair.public_key))
    public.verify(
        base64.b64decode(headers["X-Node-Signature"]),
        f"neomua-ws-v1:1700000000:{headers['X-Node-Nonce']}".encode(),
    )
    assert headers["Authorization"] == "Bearer credential"


def test_reconnect_delay_is_exponential_and_capped() -> None:
    assert reconnect_delay(0, jitter=0) == 1
    assert reconnect_delay(4, jitter=0) == 16
    assert reconnect_delay(20, jitter=0) == 60
    assert reconnect_delay(20, jitter=1) <= 60


def test_node_secret_index_reports_only_refs_and_fingerprints(tmp_path) -> None:
    index = tmp_path / "node-secret-refs.json"
    index.write_text(
        json.dumps(
            {
                "github": {
                    "credential_id": "node-secret:github",
                    "fingerprint": "a" * 64,
                    "updated_at": "2026-07-13T00:00:00+00:00",
                }
            }
        ),
        "utf-8",
    )
    assert node_secret_fingerprints(index) == {"github": "a" * 64}
    assert "credential_id" not in json.dumps(node_secret_fingerprints(index))


def test_damaged_node_secret_index_stops_reporting(tmp_path) -> None:
    index = tmp_path / "node-secret-refs.json"
    index.write_text('{"github":{"fingerprint":"invalid"}}', "utf-8")
    try:
        node_secret_fingerprints(index)
    except RuntimeError as exc:
        assert "damaged" in str(exc)
    else:
        raise AssertionError("damaged secret index was accepted")
