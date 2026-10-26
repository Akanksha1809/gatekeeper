from gatekeeper.request_context import get_request_id, normalize_request_id, set_request_id


def test_valid_client_request_id_is_preserved():
    assert normalize_request_id("abc-123_XYZ") == "abc-123_XYZ"


def test_missing_request_id_generates_a_uuid():
    generated = normalize_request_id(None)
    assert len(generated) == 36  # uuid4 string length
    assert generated != normalize_request_id(None)


def test_request_id_with_injection_characters_is_replaced():
    malicious = "abc\nSET x=1\r\nmalicious: header"
    normalized = normalize_request_id(malicious)
    assert "\n" not in normalized
    assert "\r" not in normalized
    assert normalized != malicious  # it was rejected and replaced


def test_oversized_request_id_is_replaced():
    too_long = "a" * 200
    normalized = normalize_request_id(too_long)
    assert normalized != too_long
    assert len(normalized) == 36


def test_set_and_get_request_id_roundtrip():
    set_request_id("my-request-id")
    assert get_request_id() == "my-request-id"
