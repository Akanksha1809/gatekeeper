"""Request correlation ID handling.

Request IDs are accepted from clients (X-Request-ID) but validated and
normalized first, to prevent header/log injection and to keep metric/log
cardinality bounded. If the incoming value doesn't look safe, a fresh one
is generated instead of trusting the client blindly.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def normalize_request_id(raw: str | None) -> str:
    """Return a safe request ID: the client's value if it matches our allowed
    charset/length, otherwise a freshly generated UUID4.
    """
    if raw and _REQUEST_ID_RE.match(raw):
        return raw
    return str(uuid.uuid4())


def set_request_id(request_id: str) -> None:
    _request_id_ctx.set(request_id)


def get_request_id() -> str:
    return _request_id_ctx.get()
