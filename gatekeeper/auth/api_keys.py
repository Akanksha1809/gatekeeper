"""API key generation, hashing, and verification.

Format of an issued key:  gk_<key_id>_<secret>
  - "gk" (configurable prefix) marks it recognizable as a Gatekeeper key.
  - key_id (16 url-safe chars) is NOT secret: it's stored in plaintext and
    used purely as a lookup index so we don't need to scan/hash-compare
    every row in the table for every request.
  - secret (32 url-safe chars, ~192 bits) is the actual credential. Only
    its HMAC-SHA256 digest (keyed with a server-side secret) is persisted.

The full key is shown to the caller exactly once, at creation time, and is
unrecoverable afterwards -- the server only ever stores the digest.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets

_KEY_ID_BYTES = 12  # -> 16 base64url chars
_SECRET_BYTES = 24  # -> 32 base64url chars

# secrets.token_urlsafe's alphabet (base64url) includes "_", the same
# character used below as a field separator -- so key_id/secret CANNOT be
# split out by searching for "_" in the string; both fields have a fixed,
# deterministic length (unpadded base64url of a fixed byte count), so we
# parse by position instead.
_KEY_ID_LEN = math.ceil(_KEY_ID_BYTES * 4 / 3)
_SECRET_LEN = math.ceil(_SECRET_BYTES * 4 / 3)


def _server_hmac_key() -> bytes:
    # Separate from any single API key; used to keyed-hash secrets so a
    # leaked database dump alone isn't enough to forge valid keys (the
    # attacker would also need this server-side key, which is never persisted
    # alongside the hashes themselves).
    material = os.environ.get("GATEKEEPER_HMAC_SECRET", "insecure-default-change-me")
    return material.encode("utf-8")


def generate_api_key(prefix: str = "gk") -> tuple[str, str, str]:
    """Create a new API key.

    Returns (full_key, key_id, secret_hash) where `full_key` must be shown
    to the caller now and never again, and `secret_hash` is what gets
    persisted.
    """
    key_id = secrets.token_urlsafe(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    full_key = f"{prefix}_{key_id}_{secret}"
    secret_hash = hash_secret(secret)
    return full_key, key_id, secret_hash


def hash_secret(secret: str) -> str:
    return hmac.new(_server_hmac_key(), secret.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_secret(secret: str, expected_hash: str) -> bool:
    """Constant-time comparison of a presented secret against the stored hash."""
    candidate = hash_secret(secret)
    return hmac.compare_digest(candidate, expected_hash)


class ParsedApiKey:
    __slots__ = ("prefix", "key_id", "secret")

    def __init__(self, prefix: str, key_id: str, secret: str):
        self.prefix = prefix
        self.key_id = key_id
        self.secret = secret


def parse_api_key(raw: str) -> ParsedApiKey | None:
    """Split a presented key into (prefix, key_id, secret). None if malformed.

    key_id and secret have fixed lengths (see _KEY_ID_LEN/_SECRET_LEN above),
    so they're parsed by position/length rather than by searching for the
    "_" separators -- those characters can also legitimately appear inside
    either field's own base64url content.
    """
    tail_len = _KEY_ID_LEN + 1 + _SECRET_LEN  # key_id + "_" + secret
    if len(raw) < tail_len + 2:  # + at least 1 char of prefix + its "_" separator
        return None
    prefix = raw[: -tail_len - 1]
    rest = raw[-tail_len:]
    if not prefix or raw[len(prefix)] != "_":
        return None
    key_id = rest[:_KEY_ID_LEN]
    separator = rest[_KEY_ID_LEN]
    secret = rest[_KEY_ID_LEN + 1 :]
    if separator != "_":
        return None
    return ParsedApiKey(prefix=prefix, key_id=key_id, secret=secret)
