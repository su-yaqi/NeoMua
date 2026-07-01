import stat

from node_runtime.identity import DeviceIdentity, IdentityStore, generate_keypair


def test_identity_file_is_owner_only(tmp_path) -> None:
    store = IdentityStore(tmp_path / "identity.json")
    identity = DeviceIdentity(
        node_id="node-1", private_key="secret", credential="credential"
    )
    store.save(identity)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load() == identity


def test_generated_ed25519_keypair_is_distinct() -> None:
    first = generate_keypair()
    second = generate_keypair()
    assert first.private_key != second.private_key
    assert first.public_key != second.public_key
