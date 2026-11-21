"""Asynchronous rate limiting without network dependencies."""

from asyncio import Lock, get_running_loop, sleep
from time import monotonic_ns

cimport cython
from cpython.long cimport PyLong_CheckExact
from libc.stdint cimport int64_t, uint64_t, UINT64_MAX
from picop.cytime cimport PyTime_t, time_monotonic_raw

__all__ = ["TokenBucket"]

cdef object _ORIGINAL_MONOTONIC_NS = monotonic_ns
cdef object _NS_PER_SECOND = 1_000_000_000

cdef inline int64_t _positive_int(object value) except -1:
    cdef int64_t result
    if not PyLong_CheckExact(value):
        return 0
    try:
        result = <int64_t>value
    except OverflowError:
        return 0
    return result if result > 0 else 0


cdef inline uint64_t _check_setting(object name, object value) except? 0:
    cdef int64_t result = _positive_int(value)
    if result:
        return <uint64_t>result
    if not PyLong_CheckExact(value):
        raise TypeError(f"{name} must be an int")
    raise ValueError(f"{name} must be between 1 and 2**63 - 1")


cdef class TokenBucket:
    """An initially full, FIFO token bucket for one asyncio event loop.

    Settings are exact ints in ``1..2**63 - 1``. Refill is continuous at
    ``refill_amount`` tokens per ``refill_period_ns``. The first valid
    acquire binds the bucket to its running loop.
    """
    cdef uint64_t _capacity, _period, _refill
    cdef uint64_t _fixed_capacity, _fixed_credit, _max_elapsed
    cdef bint _fixed, _native_initialized
    cdef PyTime_t _native_updated
    cdef object _clock, _python_updated
    cdef object _wide_capacity, _wide_credit
    cdef object _lock, _loop
    cdef Py_ssize_t _waiting

    def __init__(self, capacity: int, *, refill_amount: int = 1,
                 refill_period_ns: int = 1_000_000_000) -> None:
        cdef uint64_t size = _check_setting("capacity", capacity)
        cdef uint64_t refill = _check_setting("refill_amount", refill_amount)
        cdef uint64_t period = _check_setting("refill_period_ns", refill_period_ns)
        self._capacity = size
        self._refill = refill
        self._period = period
        self._fixed = self._capacity <= UINT64_MAX // self._period
        if self._fixed:
            self._fixed_capacity = self._capacity * self._period
            self._fixed_credit = self._fixed_capacity
            self._max_elapsed = UINT64_MAX // self._refill
            self._wide_capacity = self._wide_credit = None
        else:
            self._wide_capacity = capacity * refill_period_ns
            self._wide_credit = self._wide_capacity
        # Snapshot the private test clock at construction. Production does
        # not call Python or box a timestamp in the fixed-width refill path.
        self._clock = monotonic_ns
        if self._fixed and self._clock is _ORIGINAL_MONOTONIC_NS:
            self._clock = None
        self._python_updated = None
        self._native_initialized = False
        self._lock = None
        self._loop = None
        self._waiting = 0

    cdef uint64_t _elapsed_ns(self) except? <uint64_t>-1:
        cdef PyTime_t now
        cdef uint64_t elapsed = 0
        cdef object wide_elapsed
        if self._clock is not None:
            wide_elapsed = self._python_elapsed_ns()
            return UINT64_MAX if wide_elapsed > UINT64_MAX else <uint64_t>wide_elapsed
        time_monotonic_raw(&now)
        if self._native_initialized and now > self._native_updated:
            elapsed = <uint64_t>now - <uint64_t>self._native_updated
        self._native_updated = now
        self._native_initialized = True
        return elapsed

    cdef object _python_elapsed_ns(self):
        cdef object now = self._clock()
        cdef object previous = self._python_updated
        self._python_updated = now
        return now - previous if previous is not None and now > previous else 0

    @cython.cdivision(True)
    cdef uint64_t _fixed_step(self, uint64_t charge, uint64_t elapsed) noexcept:
        cdef uint64_t credit = self._fixed_credit
        cdef uint64_t deficit, refill
        # Guard the multiply and add separately; credit cannot exceed capacity.
        if elapsed > self._max_elapsed:
            credit = self._fixed_capacity
        else:
            refill = elapsed * self._refill
            if refill >= self._fixed_capacity - credit:
                credit = self._fixed_capacity
            else:
                credit += refill
        if credit >= charge:
            self._fixed_credit = credit - charge
            return 0
        self._fixed_credit = credit
        deficit = charge - credit
        return deficit // self._refill + (deficit % self._refill != 0)

    cdef object _wide_step(self, object charge):
        cdef object elapsed = self._python_elapsed_ns()
        cdef object credit = self._wide_credit
        cdef object deficit
        if elapsed:
            credit = min(self._wide_capacity, credit + elapsed * self._refill)
        if credit >= charge:
            self._wide_credit = credit - charge
            return 0
        self._wide_credit = credit
        deficit = charge - credit
        return (deficit + self._refill - 1) // self._refill

    cdef bint _try_take(self, uint64_t cost) except -1:
        if self._fixed:
            return self._fixed_step(cost * self._period, self._elapsed_ns()) == 0
        return self._wide_step((<object>cost) * self._period) == 0

    cdef object _step(self, uint64_t cost):
        if self._fixed:
            return self._fixed_step(cost * self._period, self._elapsed_ns())
        return self._wide_step((<object>cost) * self._period)

    async def acquire(self, cost: int = 1) -> None:
        """Consume ``cost`` tokens, waiting without debiting on cancellation."""
        cdef object loop, delay_ns, lock
        cdef uint64_t tokens = <uint64_t>_positive_int(cost)
        if not tokens or tokens > self._capacity:
            if not PyLong_CheckExact(cost):
                raise TypeError("cost must be an int")
            raise ValueError("cost must be between 1 and capacity")
        loop = get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("TokenBucket cannot be used across event loops")
        # Include queued callers until they return, even during lock handoff.
        if self._waiting == 0 and self._try_take(tokens):
            return
        self._waiting += 1
        try:
            if self._lock is None:
                self._lock = Lock()
            lock = self._lock
            async with lock:
                while True:
                    delay_ns = self._step(tokens)
                    if not delay_ns:
                        return
                    await sleep(delay_ns / _NS_PER_SECOND)
        finally:
            self._waiting -= 1
