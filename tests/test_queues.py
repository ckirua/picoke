"""Contract tests for the bounded byte ring."""

from __future__ import annotations

import queue
import sys
import threading
import time

import pytest

from picoke.queues import ByteRing


class DerivedInt(int):
    pass



class DerivedBytes(bytes):
    pass


@pytest.mark.parametrize("value", [True, False, 1.0, "1", DerivedInt(1)])
def test_constructor_requires_exact_builtin_ints(value: object) -> None:
    with pytest.raises(TypeError):
        ByteRing(value, 0)
    with pytest.raises(TypeError):
        ByteRing(1, value)


@pytest.mark.parametrize("capacity,max_item_size", [(0, 0), (-1, 0), (1, -1)])
def test_constructor_rejects_invalid_ranges(capacity: int, max_item_size: int) -> None:
    with pytest.raises(ValueError):
        ByteRing(capacity, max_item_size)


@pytest.mark.parametrize("capacity,max_item_size", [(sys.maxsize + 1, 0), (1, sys.maxsize + 1)])
def test_constructor_rejects_values_outside_py_ssize_t(
    capacity: int, max_item_size: int
) -> None:
    with pytest.raises(OverflowError):
        ByteRing(capacity, max_item_size)


@pytest.mark.parametrize("value", [bytearray(), memoryview(b"x"), "x", None, DerivedBytes(b"x")])
def test_push_requires_exact_bytes(value: object) -> None:
    ring = ByteRing(1, 1)
    with pytest.raises(TypeError):
        ring.try_push(value)


def test_oversized_push_is_rejected_even_when_full() -> None:
    ring = ByteRing(1, 1)
    assert ring.try_push(b"x") is True
    with pytest.raises(ValueError):
        ring.try_push(b"xx")
    assert ring.try_pop() == b"x"


def test_fifo_full_recovery_and_capacity_one() -> None:
    ring = ByteRing(2, 4)
    assert ring.try_push(b"a") is True
    assert ring.try_push(b"b") is True
    assert ring.try_push(b"c") is False
    assert ring.try_pop() == b"a"
    assert ring.try_push(b"c") is True
    assert [ring.try_pop(), ring.try_pop(), ring.try_pop()] == [b"b", b"c", None]

    single = ByteRing(1, 4)
    assert single.try_pop() is None
    assert single.try_push(b"one") is True
    assert single.try_push(b"two") is False
    assert single.try_pop() == b"one"
    assert single.try_push(b"two") is True
    assert single.try_pop() == b"two"


def test_wraparound_reuse_and_empty_payloads() -> None:
    ring = ByteRing(3, 4)
    for value in (b"a", b"b", b"c"):
        assert ring.try_push(value) is True
    assert ring.try_pop() == b"a"
    assert ring.try_pop() == b"b"
    assert ring.try_push(b"d") is True
    assert ring.try_push(b"e") is True
    assert ring.try_pop() == b"c"
    assert ring.try_pop() == b"d"
    assert ring.try_pop() == b"e"

    assert ring.try_push(b"") is True
    assert ring.try_pop() == b""
    assert ring.try_pop() is None


def test_zero_maximum_size_accepts_empty_bytes() -> None:
    ring = ByteRing(1, 0)
    assert ring.try_push(b"") is True
    assert ring.try_push(b"") is False
    assert ring.try_pop() == b""
    assert ring.try_pop() is None


def test_popped_payload_survives_ring_reuse() -> None:
    ring = ByteRing(1, 8)
    payload = bytes(bytearray(b"payload"))
    assert ring.try_push(payload) is True
    popped = ring.try_pop()
    assert popped == payload
    assert ring.try_push(b"reused") is True
    assert ring.try_pop() == b"reused"
    assert popped == b"payload"


def test_bounded_single_producer_single_consumer() -> None:
    count = 10_000
    ring = ByteRing(8, 8)
    errors: queue.Queue[BaseException] = queue.Queue()
    consumed: list[bytes] = []
    deadline = time.monotonic() + 10

    def producer() -> None:
        try:
            for number in range(count):
                item = number.to_bytes(8, "little")
                while not ring.try_push(item):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("producer deadline exceeded")
                    time.sleep(0)
        except BaseException as exc:
            errors.put(exc)

    def consumer() -> None:
        try:
            while len(consumed) < count:
                item = ring.try_pop()
                if item is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("consumer deadline exceeded")
                    time.sleep(0)
                    continue
                consumed.append(item)
        except BaseException as exc:
            errors.put(exc)

    consumer_thread = threading.Thread(target=consumer, daemon=True)
    producer_thread = threading.Thread(target=producer, daemon=True)
    consumer_thread.start()
    producer_thread.start()
    producer_thread.join(11)
    consumer_thread.join(11)
    if producer_thread.is_alive() or consumer_thread.is_alive():
        raise AssertionError("producer/consumer thread failed to join before deadline")
    if not errors.empty():
        raise errors.get()
    assert consumed == [number.to_bytes(8, "little") for number in range(count)]
