"""Compact, structured JSON logging."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

__all__ = ("JSONFormatter",)


def _json_fields(value: object, active: set[int]) -> object:
    """Validate and materialize the supported JSON field values."""
    if value is None or isinstance(value, (str, int, float)):
        return value

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("fields contain a circular reference")
        active.add(identity)
        try:
            result = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("field mapping keys must be strings")
                result[key] = _json_fields(item, active)
            return result
        finally:
            active.remove(identity)

    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ValueError("fields contain a circular reference")
        active.add(identity)
        try:
            return [_json_fields(item, active) for item in value]
        finally:
            active.remove(identity)

    raise TypeError(f"unsupported field value type: {type(value).__name__}")


class JSONFormatter(logging.Formatter):
    """Format each record as one compact, ASCII-escaped JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        )[:-6] + "Z"
        result = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            result["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            result["stack"] = self.formatStack(record.stack_info)
        if hasattr(record, "fields"):
            fields = record.fields
            if not isinstance(fields, Mapping):
                raise TypeError("fields must be a mapping")
            result["fields"] = _json_fields(fields, set())

        return json.dumps(result, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
