"""Bounded asynchronous binary stream writer."""

import os
import threading

from .queues import ByteRing


class BackgroundWriter:
    """Write immutable byte records to a caller-owned binary stream in order."""

    _OPEN = 0
    _CLOSING = 1
    _CLOSED = 2
    _FAILED = 3

    def __init__(self, stream, *, capacity: int = 1024, max_item_size: int = 1048576):
        self._ring = ByteRing(capacity, max_item_size)
        self._pid = os.getpid()
        self._stream = stream
        self._max_item_size = max_item_size
        self._condition = threading.Condition()
        self._state = self._OPEN
        self._failure = None
        self._accepted = 0
        self._completed = 0
        self._inflight = False
        self._flushes = []
        self._worker = threading.Thread(
            target=self._run, name="picoke-background-writer", daemon=True
        )
        self._worker.start()

    def _check_process(self):
        if os.getpid() != self._pid:
            raise RuntimeError("background writer cannot be used after fork")

    def _check_worker(self):
        if threading.current_thread() is self._worker:
            raise RuntimeError("background writer cannot be called from its worker")

    def _raise_failure(self):
        if self._failure is not None:
            raise RuntimeError("background writer failed") from self._failure

    def write(self, data: bytes, *, block: bool = True) -> bool:
        self._check_process()
        if type(data) is not bytes:
            raise TypeError("item must be exact bytes")
        if len(data) > self._max_item_size:
            raise ValueError("item exceeds max_item_size")
        if type(block) is not bool:
            raise TypeError("block must be exact bool")
        self._check_worker()
        with self._condition:
            while True:
                self._raise_failure()
                if self._state != self._OPEN:
                    raise ValueError("background writer is closed")
                if self._ring.try_push(data):
                    self._accepted += 1
                    self._condition.notify_all()
                    return True
                if not block:
                    return False
                self._condition.wait()

    def flush(self) -> None:
        self._check_process()
        self._check_worker()
        request = [False]
        with self._condition:
            self._raise_failure()
            if self._state != self._OPEN:
                raise ValueError("background writer is closed")
            target = self._accepted
            self._flushes.append((target, request))
            self._condition.notify_all()
            while not request[0]:
                self._raise_failure()
                self._condition.wait()

    def close(self) -> None:
        self._check_process()
        self._check_worker()
        with self._condition:
            if self._failure is None and self._state == self._OPEN:
                self._state = self._CLOSING
                self._flushes.append((self._accepted, [False]))
                self._condition.notify_all()
        self._worker.join()
        with self._condition:
            self._raise_failure()
            if self._state != self._CLOSED:
                raise RuntimeError("background writer failed")

    def __enter__(self):
        self._check_process()
        with self._condition:
            self._raise_failure()
            if self._state != self._OPEN:
                raise ValueError("background writer is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def _write_item(self, data):
        if not data:
            return
        offset = 0
        while offset < len(data):
            chunk = data if offset == 0 else memoryview(data)[offset:]
            written = self._stream.write(chunk)
            remaining = len(data) - offset
            if type(written) is not int or written <= 0 or written > remaining:
                raise OSError("stream.write() returned an invalid byte count")
            offset += written

    def _run(self):
        try:
            while True:
                item = None
                flush_now = False
                with self._condition:
                    while True:
                        if any(target <= self._completed for target, _ in self._flushes):
                            flush_now = True
                            break
                        item = self._ring.try_pop()
                        if item is not None:
                            self._inflight = True
                            self._condition.notify_all()
                            break
                        if self._state in (self._CLOSING, self._CLOSED) and self._completed == self._accepted:
                            break
                        self._condition.wait()
                if flush_now:
                    self._stream.flush()
                    with self._condition:
                        remaining = []
                        for target, request in self._flushes:
                            if target <= self._completed:
                                request[0] = True
                            else:
                                remaining.append((target, request))
                        self._flushes = remaining
                        if self._state == self._CLOSING and self._completed == self._accepted and not self._flushes:
                            self._state = self._CLOSED
                            self._condition.notify_all()
                            return
                        self._condition.notify_all()
                    continue
                if item is not None:
                    self._write_item(item)
                    with self._condition:
                        self._completed += 1
                        self._inflight = False
                        self._condition.notify_all()
                    continue
                with self._condition:
                    if self._state == self._CLOSING and self._completed == self._accepted:
                        self._state = self._CLOSED
                        self._condition.notify_all()
                        return
        except BaseException as exc:
            with self._condition:
                self._failure = exc
                self._state = self._FAILED
                while self._ring.try_pop() is not None:
                    pass
                self._flushes.clear()
                self._condition.notify_all()
