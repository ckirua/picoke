import binascii
import io
import logging
import struct

import pytest

msgpack = pytest.importorskip("msgpack")

from picoke.binary_logging import (
    BackgroundBinaryHandler,
    BinaryLogError,
    iter_binary_records,
)
from picoke.logging import BackgroundWriter

MAGIC = b"PKL\x01"


def frame(payload):
    return MAGIC + struct.pack(">I", len(payload)) + payload + struct.pack(">I", binascii.crc32(payload))

def pack(record):
    return msgpack.packb(record, use_bin_type=True)


def make_record(message="hello", **kwargs):
    record = logging.LogRecord(
        "binary.test", logging.INFO, "sample.py", 12, message, (),
        kwargs.pop("exc_info", None), sinfo=kwargs.pop("stack_info", None),
    )
    record.created = 1_700_000_000.25
    for name, value in kwargs.items():
        setattr(record, name, value)
    return record






class ReraiseBinaryHandler(BackgroundBinaryHandler):
    def handleError(self, record):
        raise


def emit_record(record, *, max_item_size=1 << 20):
    stream = io.BytesIO()
    writer = BackgroundWriter(stream, max_item_size=max_item_size)
    handler = ReraiseBinaryHandler(writer)
    try:
        handler.emit(record)
        handler.flush()
        return stream.getvalue()
    finally:
        handler.close()
        writer.close()


class ShortReader:
    def __init__(self, data, limit=1):
        self._stream = io.BytesIO(data)
        self.limit = limit
        self.closed = False

    def read(self, size=-1):
        if size < 0:
            size = self.limit
        return self._stream.read(min(size, self.limit))

    def close(self):
        self.closed = True
        self._stream.close()

def test_known_messagepack_frame_golden_and_independent_decode():
    # MessagePack {"a": 1} is fixed by the format, and this CRC/frame is a golden vector.
    payload = bytes.fromhex("81a16101")
    expected_frame = bytes.fromhex("504b4c010000000481a16101a7fa5365")
    assert frame(payload) == expected_frame

    expected = {
        "timestamp": "2023-11-14T22:13:20.250Z",
        "level": "INFO",
        "logger": "binary.test",
        "message": "golden",
    }
    raw = emit_record(make_record("golden"))
    encoded_payload = msgpack.packb(expected, use_bin_type=True)
    assert raw == frame(encoded_payload)
    size = int.from_bytes(raw[4:8], "big")
    assert len(raw) == size + 12
    assert int.from_bytes(raw[-4:], "big") == binascii.crc32(raw[8:-4])
    assert msgpack.unpackb(raw[8:-4], raw=False, strict_map_key=False) == expected

def test_reader_round_trip_unicode_nested_fields_and_stream_ownership():
    data = frame(pack({"timestamp": "雪☃", "fields": {"nested": [1, True, None]}}))
    stream = ShortReader(data)
    assert list(iter_binary_records(stream)) == [
        {"timestamp": "雪☃", "fields": {"nested": [1, True, None]}}
    ]
    assert not stream.closed


def test_appends_frames_across_writer_sessions(tmp_path):
    path = tmp_path / "events.pkl"
    for message in ("first", "second"):
        with path.open("ab") as stream:
            writer = BackgroundWriter(stream)
            handler = BackgroundBinaryHandler(writer)
            handler.emit(make_record(message))
            handler.close()
            writer.close()
    records = list(iter_binary_records(path.open("rb")))
    assert [record["message"] for record in records] == ["first", "second"]


def test_short_reads_and_clean_eof():
    first = frame(pack({"n": 1}))
    second = frame(pack({"n": 2}))
    reader = ShortReader(first + second, limit=2)
    assert list(iter_binary_records(reader)) == [{"n": 1}, {"n": 2}]
    assert list(iter_binary_records(io.BytesIO(b""))) == []


def test_fragmented_reads_request_only_remaining_frame_bytes():
    class TrackingReader(ShortReader):
        def __init__(self, data):
            super().__init__(data, limit=2)
            self.requests = []

        def read(self, size=-1):
            self.requests.append(size)
            return super().read(size)

    reader = TrackingReader(frame(pack({"n": 1})))
    assert list(iter_binary_records(reader)) == [{"n": 1}]
    assert reader.requests == [8, 6, 4, 2, 4, 2, 4, 2, 8]


def test_reader_accepts_short_reads_returning_bytes_subclasses():
    class DerivedBytes(bytes):
        def __new__(cls, value):
            instance = super().__new__(cls, value)
            instance.length_calls = 0
            return instance

        def __len__(self):
            self.length_calls += 1
            if self.length_calls > 3:
                raise RuntimeError("unexpected extra length lookup")
            return super().__len__()

    class DerivedReader(ShortReader):
        def read(self, size=-1):
            return DerivedBytes(super().read(size))

    reader = DerivedReader(frame(pack({"n": 1})), limit=2)
    assert list(iter_binary_records(reader)) == [{"n": 1}]


@pytest.mark.parametrize("cut", [1, 3, 4, 5, 7, 8, 9, 11])
def test_truncated_final_frame_is_strict_or_recovered(cut):
    raw = frame(pack({"ok": True}))
    truncated = raw[:-cut]
    with pytest.raises(BinaryLogError):
        list(iter_binary_records(io.BytesIO(truncated)))
    assert list(iter_binary_records(io.BytesIO(truncated), recover_truncated=True)) == []



@pytest.mark.parametrize("tail", [b"P", b"PK", b"PKL"])
def test_recovery_ignores_prefix_of_magic(tail):
    with pytest.raises(BinaryLogError):
        list(iter_binary_records(io.BytesIO(tail)))
    assert list(iter_binary_records(io.BytesIO(tail), recover_truncated=True)) == []


@pytest.mark.parametrize("tail", [b"X", b"PX", b"PKX", bytes.fromhex("504b4c02")])
def test_recovery_rejects_known_invalid_magic_prefix(tail):
    with pytest.raises(BinaryLogError):
        list(iter_binary_records(io.BytesIO(tail), recover_truncated=True))


@pytest.mark.parametrize("header", [MAGIC, MAGIC + b"\x00", MAGIC + b"\x00\x00", MAGIC + b"\x00\x00\x00"])
def test_recovery_ignores_a_valid_magic_with_truncated_length(header):
    with pytest.raises(BinaryLogError):
        list(iter_binary_records(io.BytesIO(header)))
    assert list(iter_binary_records(io.BytesIO(header), recover_truncated=True)) == []

def test_recovery_keeps_complete_records_and_ignores_only_incomplete_tail():
    good = frame(pack({"ok": 1}))
    assert list(iter_binary_records(io.BytesIO(good + frame(pack({"x": 2}))[:-2]), recover_truncated=True)) == [
        {"ok": 1}
    ]


def test_bad_magic_version_checksum_malformed_payload_and_non_map_rejected():
    good = frame(pack({"ok": True}))
    corruptions = [b"BAD!" + good[4:], good[:3] + b"\x02" + good[4:]]
    bad_crc = bytearray(good)
    bad_crc[-1] ^= 1
    corruptions.append(bytes(bad_crc))
    corruptions.extend((frame(b"\xc1"), frame(pack([1, 2]))))
    for data in corruptions:
        with pytest.raises(BinaryLogError):
            list(iter_binary_records(io.BytesIO(data), recover_truncated=True))


def test_reader_validates_all_top_level_map_keys():
    record = {f"field_{index}": index for index in range(128)}
    assert list(iter_binary_records(io.BytesIO(frame(pack(record))))) == [record]
    record[7] = "invalid key"
    with pytest.raises(BinaryLogError, match="record map keys must be strings"):
        list(iter_binary_records(io.BytesIO(frame(pack(record)))))




@pytest.mark.parametrize("record", [
    {"ok": True, "fields": []},
    {"ok": True, "fields": {1: "bad"}},
    {"ok": True, "fields": {"x": float("nan")}},
    {"ok": True, "fields": {"x": b"not JSON"}},
    {"ok": True, "fields": {"x": msgpack.ExtType(7, b"opaque")}},
])
def test_reader_rejects_invalid_fields_when_present(record):
    with pytest.raises(BinaryLogError):
        list(iter_binary_records(io.BytesIO(frame(pack(record)))))

def test_payload_size_limit_is_inclusive():
    payload = pack({"ok": True})
    assert list(iter_binary_records(io.BytesIO(frame(payload)), max_record_size=len(payload))) == [
        {"ok": True}
    ]


@pytest.mark.parametrize("recover", [1, None, "yes"])
def test_recovery_option_requires_exact_bool(recover):
    with pytest.raises(TypeError):
        list(iter_binary_records(io.BytesIO(), recover_truncated=recover))
def test_oversized_record_is_rejected_before_reading_payload():
    header = MAGIC + struct.pack(">I", 9)
    with pytest.raises(BinaryLogError):
        list(iter_binary_records(io.BytesIO(header + b"x" * 9), max_record_size=8))


@pytest.mark.parametrize("size", [1 << 31, (1 << 32) - 1])
def test_high_bit_payload_lengths_are_rejected_before_reading(size):
    header = MAGIC + struct.pack(">I", size)
    with pytest.raises(BinaryLogError, match="exceeds max_record_size"):
        list(iter_binary_records(io.BytesIO(header), recover_truncated=True))
    assert list(iter_binary_records(io.BytesIO(header), max_record_size=size,
                                    recover_truncated=True)) == []


@pytest.mark.parametrize("maximum", [-1, True, 1.5, "4"])
def test_reader_maximum_validation(maximum):
    with pytest.raises((TypeError, ValueError)):
        list(iter_binary_records(io.BytesIO(), max_record_size=maximum))


@pytest.mark.parametrize("fields", [None, (), {1: "bad"}, {"x": (1, 2)}, {"x": {False: 1}}, {"x": {1, 2}}])
def test_handler_rejects_invalid_field_shapes(fields):
    with pytest.raises((TypeError, ValueError)):
        emit_record(make_record(fields=fields))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_handler_rejects_nonfinite_field_values(value):
    with pytest.raises(ValueError):
        emit_record(make_record(fields={"value": value}))


@pytest.mark.parametrize(
    "value",
    [
        0.0,
        -0.0,
        float.fromhex("0x0.0000000000001p-1022"),
        float.fromhex("0x1.fffffffffffffp+1023"),
    ],
)
def test_extreme_finite_floats_round_trip_bit_exactly(value):
    raw = emit_record(make_record(fields={"values": [value]}))
    result = next(iter_binary_records(io.BytesIO(raw)))["fields"]["values"][0]
    assert struct.pack(">d", result) == struct.pack(">d", value)


def test_handler_rejects_cycles_but_allows_shared_acyclic_values():
    loop = []
    loop.append(loop)
    with pytest.raises(ValueError):
        emit_record(make_record(fields={"loop": loop}))
    shared = {"v": [1]}
    output = emit_record(make_record(fields={"left": shared, "right": shared}))
    parsed = list(iter_binary_records(io.BytesIO(output)))[0]
    assert parsed["fields"] == {"left": shared, "right": shared}


def test_custom_field_containers_are_materialized_once_with_their_values():
    from collections.abc import Mapping

    class DynamicMapping(Mapping):
        def __init__(self):
            self.lookups = []

        def __iter__(self):
            return iter(("numbers", "name"))

        def __len__(self):
            return 2

        def __getitem__(self, key):
            self.lookups.append(key)
            return {"numbers": DynamicList([1, 2]), "name": "snow"}[key]

    class DynamicList(list):
        def __iter__(self):
            yield 3
            yield 4

    fields = DynamicMapping()
    raw = emit_record(make_record(fields=fields))
    record = next(iter_binary_records(io.BytesIO(raw)))
    assert record["fields"] == {"numbers": [3, 4], "name": "snow"}
    assert fields.lookups == ["numbers", "name"]


def test_list_mutation_during_nested_mapping_iteration_is_preserved():
    from collections.abc import Mapping

    values = []

    class ExpandingMapping(Mapping):
        def __iter__(self):
            values.append(9)
            return iter(("item",))

        def __len__(self):
            return 1

        def __getitem__(self, key):
            if key != "item":
                raise KeyError(key)
            return 3

    values.extend([ExpandingMapping(), 1])
    raw = emit_record(make_record(fields={"values": values}))
    assert next(iter_binary_records(io.BytesIO(raw)))["fields"] == {
        "values": [{"item": 3}, 1, 9]
    }


def test_string_subclass_keys_preserve_hashing_behavior():
    class Key(str):
        calls = 0

        def __hash__(self):
            self.calls += 1
            if self.calls > 2:
                raise RuntimeError("unexpected extra hash")
            return super().__hash__()

    key = Key("city")
    raw = emit_record(make_record(fields={key: "雪"}))
    assert next(iter_binary_records(io.BytesIO(raw)))["fields"] == {"city": "雪"}


def test_dynamic_class_proxy_preserves_serialization_error():
    class PretendString:
        @property
        def __class__(self):
            return str

    value = PretendString()
    assert isinstance(value, str)
    with pytest.raises(TypeError) as expected:
        msgpack.packb(value, use_bin_type=True)
    with pytest.raises(TypeError) as actual:
        emit_record(make_record(fields={"value": value}))
    assert str(actual.value) == str(expected.value)

    with pytest.raises(TypeError) as expected_key:
        msgpack.packb({value: 1}, use_bin_type=True)
    with pytest.raises(TypeError) as actual_key:
        emit_record(make_record(fields={value: 1}))
    assert str(actual_key.value) == str(expected_key.value)


@pytest.mark.parametrize("value", [-(1 << 63), (1 << 64) - 1, -1, 0, 1])
def test_messagepack_integer_boundaries_round_trip(value):
    raw = emit_record(make_record(fields={"number": value}))
    assert next(iter_binary_records(io.BytesIO(raw)))["fields"] == {"number": value}


def test_structural_errors_precede_numeric_range_errors():
    with pytest.raises(TypeError, match="unsupported field value type"):
        emit_record(make_record(fields={"large": 1 << 64, "invalid": (1, 2)}))

    loop = []
    loop.append(loop)
    with pytest.raises(ValueError, match="circular reference"):
        emit_record(make_record(fields={"large": 1 << 64, "cycle": loop}))

    comparisons = []

    class ObservableInt(int):
        def __ge__(self, other):
            comparisons.append(other)
            return super().__ge__(other)

    with pytest.raises(TypeError, match="unsupported field value type"):
        emit_record(make_record(fields={"number": ObservableInt(1), "invalid": (1, 2)}))
    assert comparisons == []


def test_deeply_nested_fields_respect_python_recursion_limit():
    import sys

    value = []
    for _ in range(sys.getrecursionlimit() + 50):
        value = [value]
    with pytest.raises(RecursionError):
        emit_record(make_record(fields={"value": value}))


def test_deep_cycle_still_reports_circular_reference():
    root = []
    current = root
    for _ in range(80):
        child = []
        current.append(child)
        current = child
    current.append(root)
    with pytest.raises(ValueError, match="circular reference"):
        emit_record(make_record(fields={"value": root}))


def test_high_recursion_limit_does_not_overflow_native_stack():
    import subprocess
    import sys

    code = '''import logging, sys
from picoke.binary_logging import _encode_record
sys.setrecursionlimit(100000)
value = []
for _ in range(25000):
    value = [value]
record = logging.LogRecord("deep", logging.INFO, "", 1, "event", (), None)
record.fields = {"deep": value}
try:
    _encode_record(record, logging.Formatter())
except ValueError:
    pass
else:
    raise AssertionError("MessagePack should reject excessive nesting")
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.parametrize("value", [1 << 64, -(1 << 63) - 1])
def test_handler_rejects_integers_outside_messagepack_range(value):
    with pytest.raises((OverflowError, ValueError)):
        emit_record(make_record(fields={"value": value}))


def test_exception_and_stack_are_serialized_and_utf8_survives():
    try:
        raise LookupError("ошибка 雪")
    except LookupError:
        import sys

        info = sys.exc_info()
    raw = emit_record(make_record("café", fields={"city": "雪"}, exc_info=info, stack_info="stack 雪"))
    record = list(iter_binary_records(io.BytesIO(raw)))[0]
    assert record["message"] == "café"
    assert record["fields"] == {"city": "雪"}
    assert "ошибка 雪" in record["exception"]
    assert record["stack"] == "stack 雪"


def test_reset_formatter_preserves_exception_and_stack_formatting():
    stream = io.BytesIO()
    writer = BackgroundWriter(stream)
    handler = ReraiseBinaryHandler(writer)
    handler.setFormatter(None)
    try:
        raise LookupError("failure")
    except LookupError:
        import sys
        record = make_record(exc_info=sys.exc_info(), stack_info="stack text")
        handler.emit(record)
    handler.flush()
    handler.close()
    writer.close()
    result = list(iter_binary_records(io.BytesIO(stream.getvalue())))[0]
    assert "LookupError: failure" in result["exception"]
    assert result["stack"] == "stack text"


def test_binary_handler_routes_format_failures_to_handle_error():
    class BrokenHandler(BackgroundBinaryHandler):
        def handleError(self, record):
            self.seen = record

    stream = io.BytesIO()
    writer = BackgroundWriter(stream)
    handler = BrokenHandler(writer)
    handler.emit(make_record(fields={1: "bad"}))
    assert handler.seen.name == "binary.test"
    handler.close()
    writer.close()
    assert not stream.closed


def test_handler_write_failure_is_reported_on_flush_and_close_does_not_own_writer():
    class BrokenStream:
        def write(self, data):
            raise OSError("sink failed")

        def flush(self):
            pass

    writer = BackgroundWriter(BrokenStream())
    handler = BackgroundBinaryHandler(writer)
    handler.emit(make_record())
    with pytest.raises(RuntimeError, match="background writer failed"):
        handler.flush()
    with pytest.raises(RuntimeError, match="background writer failed"):
        writer.close()


def test_handler_close_does_not_flush_or_close_writer():
    stream = io.BytesIO()
    writer = BackgroundWriter(stream)
    handler = BackgroundBinaryHandler(writer)
    handler.close()
    assert writer.write(b"still open")
    writer.close()
    assert not stream.closed


def test_writer_lifecycle_context_and_repeat_close():
    stream = io.BytesIO()
    with BackgroundWriter(stream) as writer:
        assert writer.write(b"payload")
    writer.close()
    assert stream.getvalue() == b"payload"
    with pytest.raises(ValueError):
        writer.write(b"late")
    with pytest.raises(ValueError):
        writer.flush()


def test_reader_preserves_io_errors():
    class ReadError:
        def read(self, size=-1):
            raise OSError("read failed")

    with pytest.raises(OSError, match="read failed"):
        list(iter_binary_records(ReadError()))


@pytest.mark.parametrize(
    ("result", "error"),
    [(None, BlockingIOError), (bytearray(), TypeError), (b"x" * 9, ValueError)],
)
def test_reader_rejects_nonblocking_or_invalid_stream_reads(result, error):
    class InvalidRead:
        def read(self, size=-1):
            return result

    with pytest.raises(error):
        list(iter_binary_records(InvalidRead(), recover_truncated=True))
