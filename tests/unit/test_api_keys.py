from gatekeeper.auth.api_keys import generate_api_key, hash_secret, parse_api_key, verify_secret


def test_generate_api_key_roundtrip():
    full_key, key_id, secret_hash = generate_api_key(prefix="gk")

    assert full_key.startswith("gk_")
    assert key_id in full_key

    parsed = parse_api_key(full_key)
    assert parsed is not None
    assert parsed.prefix == "gk"
    assert parsed.key_id == key_id
    assert verify_secret(parsed.secret, secret_hash)


def test_generate_api_key_is_unique_each_time():
    key1, id1, hash1 = generate_api_key()
    key2, id2, hash2 = generate_api_key()
    assert key1 != key2
    assert id1 != id2
    assert hash1 != hash2


def test_verify_secret_rejects_wrong_secret():
    _, _, secret_hash = generate_api_key()
    assert not verify_secret("totally-wrong-secret", secret_hash)


def test_parse_api_key_rejects_malformed_input():
    assert parse_api_key("not-a-valid-key") is None
    assert parse_api_key("") is None
    assert parse_api_key("gk_") is None  # way too short to contain key_id+secret
    assert parse_api_key("too_few") is None


def test_parse_api_key_handles_underscores_inside_key_id_or_secret():
    """token_urlsafe's alphabet includes "_", the same character used as a
    field separator -- parsing must not get confused when it shows up
    inside key_id or secret themselves (this was a real bug: a naive
    str.split("_") breaks as soon as either field contains one).
    """
    for _ in range(50):
        full_key, key_id, secret_hash = generate_api_key()
        parsed = parse_api_key(full_key)
        assert parsed is not None
        assert parsed.key_id == key_id
        assert verify_secret(parsed.secret, secret_hash)


def test_secret_never_stored_in_plaintext():
    full_key, key_id, secret_hash = generate_api_key()
    parsed = parse_api_key(full_key)
    assert parsed is not None
    assert parsed.secret not in secret_hash
    assert secret_hash != parsed.secret


def test_hash_secret_is_deterministic_for_same_input():
    assert hash_secret("same-secret") == hash_secret("same-secret")
    assert hash_secret("secret-a") != hash_secret("secret-b")
