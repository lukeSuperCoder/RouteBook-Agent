from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
routebook_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "routebook_id", default="-"
)
workflow_run_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workflow_run_id", default="-"
)
version_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("version_id", default="-")

SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "x-qw-api-key",
    "token",
    "secret",
    "password",
}
SENSITIVE_QUERY_PATTERN = re.compile(r"(?i)([?&](?:key|api_key|apikey|token)=)[^&\s]+")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
            "routebook_id": routebook_id_context.get(),
            "workflow_run_id": workflow_run_id_context.get(),
            "version_id": version_id_context.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
