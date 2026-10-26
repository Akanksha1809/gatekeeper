"""Structured JSON logging configuration.

No third-party JSON-logging dependency is used -- stdlib `logging` plus a
small formatter is enough and keeps the dependency surface small.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

_REDACT_KEYS = {"authorization", "x-api-key", "api_key", "apikey", "password", "secret"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k.lower() in _REDACT_KEYS:
                    continue
                payload[k] = v
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers unless we're debugging.
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def log_with_fields(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"fields": fields})
