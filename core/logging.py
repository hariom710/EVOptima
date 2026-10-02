"""Structured (JSON-lines) log formatting for production.

Every record is emitted as a single JSON object per line, which is what log
shippers (Loki, CloudWatch, ELK, journald) expect. Using JSON rather than a
free-text format means request id, logger name and traceback survive as
queryable fields instead of being concatenated into an unreadable string.

Usage (``config/settings/prod.py``)::

    LOGGING = {
        "formatters": {"json": {"()": "core.logging.JsonFormatter"}},
        ...
    }

Rendering a traceback is deliberately deferred: ``format()``,
``formatException()`` and ``formatStack()`` each return a *string*, so log
formatting never has to touch the exception object. When
``include_extra=True`` any non-standard ``LogRecord`` attribute (``request_id``,
``extra={...}`` passed by the caller) is folded into the payload under
``"extra"``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

#: ``LogRecord`` attributes that belong to the formatter itself rather than to
#: the caller-supplied payload. Anything outside this set is treated as extra.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render each log record as one compact JSON object."""

    def __init__(self, include_extra: bool = True, **kwargs) -> None:
        # ``fmt``/``datefmt`` are accepted so the setting can be written the
        # usual way, but they are unused — the layout is fixed JSON.
        kwargs.pop("fmt", None)
        kwargs.pop("datefmt", None)
        kwargs.pop("style", None)
        super().__init__(**kwargs)
        self.include_extra = include_extra

    # -- helpers -----------------------------------------------------------
    def _payload(self, record: logging.LogRecord) -> dict:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        if self.include_extra:
            extra = {
                k: v
                for k, v in record.__dict__.items()
                if k not in _RESERVED and not k.startswith("_")
            }
            if extra:
                payload["extra"] = extra
        return payload

    # -- logging.Formatter API --------------------------------------------
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            self._payload(record),
            default=self._default,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def _default(obj):
        # Fall back to str for anything JSON cannot represent (datetime,
        # Decimal, UUID, model instances, sets, ...). A log line must never
        # raise.
        return str(obj)
