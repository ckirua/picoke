# Frozen pre-Cython codec for benchmark comparisons; not a package implementation.
"""Append-only, checksummed MessagePack logging records.

Each frame contains ``PKL\x01``, a four-byte payload length, a MessagePack
mapping, and a four-byte CRC-32. Streams remain owned by their caller.
"""

from __future__ import annotations

import logging
import math
import struct
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import BinaryIO, Iterator

from ._background_writer import BackgroundWriter
from .logging import _json_fields

__all__ = ("BackgroundBinaryHandler", "BinaryLogError", "iter_binary_records")

_MAGIC = b"PKL\x01"
_HEADER_SIZE = 8
_CHECKSUM_SIZE = 4
_DEFAULT_MAX_RECORD_SIZE = 1_048_576


class BinaryLogError(ValueError):
    """A binary log frame is malformed or violates the record format."""


def _msgpack_module():
    try:
        import msgpack
    except ImportError as exc:
        raise RuntimeError("binary logging requires the 'msgpack' package") from exc
    return msgpack


def _validate_value(value: object) -> object:
    """Apply JSON field rules and MessagePack's portable integer/float limits."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 64):
            raise OverflowError("integer outside MessagePack 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    if isinstance(value, Mapping):
        # _json_fields owns cycle detection and key/type validation.
        materialized = _json_fields(value, set())
        return _validate_materialized(materialized)
    if isinstance(value, list):
        return _validate_materialized(_json_fields(value, set()))
    raise TypeError(f"unsupported field value type: {type(value).__name__}")


def _validate_materialized(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 64):
            raise OverflowError("integer outside MessagePack 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    if isinstance(value, dict):
        for item in value.values():
            _validate_materialized(item)
        return value
    if isinstance(value, list):
        for item in value:
            _validate_materialized(item)
        return value
    raise TypeError(f"unsupported field value type: {type(value).__name__}")


def _record_mapping(record: logging.LogRecord, formatter: logging.Formatter) -> dict[str, object]:
    timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
        timespec="milliseconds"
    )[:-6] + "Z"
    result: dict[str, object] = {
        "timestamp": timestamp,
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    if record.exc_info:
        result["exception"] = formatter.formatException(record.exc_info)
    if record.stack_info:
        result["stack"] = formatter.formatStack(record.stack_info)
    if hasattr(record, "fields"):
        fields = record.fields
        if not isinstance(fields, Mapping):
            raise TypeError("fields must be a mapping")
        result["fields"] = _validate_value(fields)
    return result


def _encode_record(record: logging.LogRecord, formatter: logging.Formatter) -> bytes:
    msgpack = _msgpack_module()
    payload = msgpack.packb(_record_mapping(record, formatter), use_bin_type=True)
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("MessagePack record exceeds frame length limit")
    return b"".join(
        (_MAGIC, struct.pack(">I", len(payload)), payload, struct.pack(">I", zlib.crc32(payload)))
    )


class BackgroundBinaryHandler(logging.Handler):
    """Format records as binary frames and enqueue them on a caller-owned writer.

    The writer is neither flushed nor closed by :meth:`close`. Failures in its
    background thread surface on a subsequent writer operation or ``flush``.
    An installed formatter supplies exception and stack hooks only; its
    ``format`` method does not replace the binary record schema.
    """

    def __init__(self, writer: BackgroundWriter) -> None:
        super().__init__()
        self.writer = writer
        # A standard Formatter supplies exception/stack formatting semantics;
        # records themselves are encoded directly as MessagePack, never JSON.
        self.setFormatter(logging.Formatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.writer.write(_encode_record(record, self.formatter or logging.Formatter()), block=True)
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        self.writer.flush()

    def close(self) -> None:
        super().close()


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    if not size:
        return b""
    chunk = stream.read(size)
    if chunk is None:
        raise BlockingIOError("binary log stream would block")
    if not isinstance(chunk, bytes):
        raise TypeError("binary log stream must return bytes")
    if not chunk:
        return b""
    if len(chunk) > size:
        raise ValueError("binary log stream returned more bytes than requested")
    if type(chunk) is bytes and len(chunk) == size:
        return chunk
    chunks = [chunk]
    remaining = size - len(chunk)
    while remaining:
        chunk = stream.read(remaining)
        if chunk is None:
            raise BlockingIOError("binary log stream would block")
        if not isinstance(chunk, bytes):
            raise TypeError("binary log stream must return bytes")
        if not chunk:
            break
        if len(chunk) > remaining:
            raise ValueError("binary log stream returned more bytes than requested")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _validate_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BinaryLogError("MessagePack record must be a map")
    for key in value:
        if not isinstance(key, str):
            raise BinaryLogError("record map keys must be strings")
    if "fields" in value:
        fields = value["fields"]
        try:
            _validate_value(fields)
        except (TypeError, ValueError, OverflowError) as exc:
            raise BinaryLogError("invalid JSON-compatible fields") from exc
        if not isinstance(fields, dict):
            raise BinaryLogError("record fields must be a map")
    return value


def iter_binary_records(
    stream: BinaryIO,
    *,
    max_record_size: int = _DEFAULT_MAX_RECORD_SIZE,
    recover_truncated: bool = False,
) -> Iterator[dict[str, object]]:
    """Yield validated records from a binary stream without closing it.

    ``recover_truncated`` ignores only an incomplete final frame; malformed
    complete frames, invalid checksums, and oversized records always fail.
    """
    if type(max_record_size) is not int or max_record_size < 0:
        raise ValueError("max_record_size must be a nonnegative exact int")
    if type(recover_truncated) is not bool:
        raise TypeError("recover_truncated must be bool")
    msgpack = _msgpack_module()
    while True:
        header = _read_exact(stream, _HEADER_SIZE)
        if not header:
            return
        header_size = len(header)
        if header[:min(header_size, len(_MAGIC))] != _MAGIC[:min(header_size, len(_MAGIC))]:
            raise BinaryLogError("invalid binary log magic or version")
        if header_size < _HEADER_SIZE:
            if recover_truncated:
                return
            raise BinaryLogError("truncated binary log header")
        if header[:4] != _MAGIC:
            raise BinaryLogError("invalid binary log magic or version")
        size = struct.unpack_from(">I", header, 4)[0]
        if size > max_record_size:
            raise BinaryLogError("binary log record exceeds max_record_size")
        payload = _read_exact(stream, size)
        if len(payload) < size:
            if recover_truncated:
                return
            raise BinaryLogError("truncated binary log payload")
        checksum = _read_exact(stream, _CHECKSUM_SIZE)
        if len(checksum) < _CHECKSUM_SIZE:
            if recover_truncated:
                return
            raise BinaryLogError("truncated binary log checksum")
        if struct.unpack_from(">I", checksum)[0] != zlib.crc32(payload):
            raise BinaryLogError("binary log checksum mismatch")
        try:
            unpacked = msgpack.unpackb(payload, raw=False, strict_map_key=False)
        except Exception as exc:
            raise BinaryLogError("malformed MessagePack record") from exc
        yield _validate_record(unpacked)
