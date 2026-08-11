"""Structured JSON logs for App / Bot / Jobs (never log secrets or OBO tokens)."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

_REDACT_KEYS = {
    "access_token",
    "token",
    "authorization",
    "password",
    "secret",
    "app_secret",
    "lark_app_secret",
    "client_secret",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _REDACT_KEYS or ("token" in kl and "conversation" not in kl):
                out[k] = "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 40 and obj.count(".") >= 2:
        # Likely JWT — never print
        if obj.startswith("eyJ"):
            return "***jwt***"
    return obj


def slog(
    event: str,
    *,
    level: str = "info",
    **fields: Any,
) -> None:
    """Emit one JSON log line to stdout (Databricks Apps / Jobs capture this)."""
    payload: dict[str, Any] = {
        "ts": _now_iso(),
        "level": level.lower(),
        "event": event,
        "service": os.environ.get("LARK_LOG_SERVICE", "lark-genie-bot"),
        "component": fields.pop("component", "bot"),
    }
    if fields:
        payload.update(_redact(fields))
    line = json.dumps(payload, ensure_ascii=False, default=str)
    stream = sys.stderr if level.lower() in {"error", "critical"} else sys.stdout
    print(line, file=stream, flush=True)


class JsonLogHandler(logging.Handler):
    """Bridge stdlib logging → slog (optional)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            slog(
                record.getMessage(),
                level=record.levelname.lower(),
                component=record.name,
                logger=record.name,
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


def elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
