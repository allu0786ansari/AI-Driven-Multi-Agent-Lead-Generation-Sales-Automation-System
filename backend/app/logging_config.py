"""Structured JSON logging with a per-request / per-event trace ID and PII masking.

Every log line carries `trace_id`, so one lead's journey can be followed across
the webhook, the worker and the agents. Phone numbers and email addresses are
masked before anything is written (spec Section 11).
"""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import re
import sys
import uuid
from datetime import UTC, datetime

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Phone numbers / WhatsApp IDs: 10-15 digits, optional leading +, optional single
# spaces between digit groups ("+91 98765 43210"). Must stand alone, so digits inside
# hex IDs, UUIDs and timestamps ("2026-10-14 14:04:19") are left untouched.
_PHONE_RE = re.compile(r"(?<![\w+])\+?\d(?: ?\d){9,14}(?!\w)")


def mask_pii(text: str) -> str:
    """Mask emails and phone-like digit runs, keeping the last 2 digits for debugging."""
    text = _EMAIL_RE.sub(r"\1***@\2", text)

    def _mask_phone(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"***{digits[-2:]}"

    return _PHONE_RE.sub(_mask_phone, text)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


class JsonFormatter(logging.Formatter):
    """One JSON object per line; extra fields passed via `extra={...}` are included."""

    _RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "msg": mask_pii(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = mask_pii(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exc"] = mask_pii(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """Human-readable lines. Only the message is masked, never the timestamp or trace ID."""

    def format(self, record: logging.LogRecord) -> str:
        record = copy.copy(record)
        record.msg = mask_pii(record.getMessage())
        record.args = None
        record.trace_id = trace_id_var.get()
        if record.exc_info:
            record.exc_text = mask_pii(self.formatException(record.exc_info))
        return super().format(record)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(PlainFormatter("%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Uvicorn's access log would print raw query strings; keep it but quieter.
    logging.getLogger("uvicorn.access").setLevel("WARNING")
