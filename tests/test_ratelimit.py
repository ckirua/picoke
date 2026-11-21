"""Contract tests for the asynchronous token bucket."""

import asyncio
from time import monotonic_ns

import pytest

import picore.ratelimit as ratelimit


class FakeTime:
    def __init__(self, now: int = 0) -> None:
        self.now = now
        self.sleeps: list[float] = []
        self.gates: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    def clock(self) -> int:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        gate = asyncio.Event()
        self.gates.put_nowait(gate)
        await gate.wait()

    async def release(self, now: int) -> None:
        gate = await self.gates.get()
        self.now = now
        gate.set()


def patch_time(monkeypatch: pytest.MonkeyPatch, clock: FakeTime) -> None:
    monkeypatch.setattr(ratelimit, "monotonic_ns", clock.clock)
    monkeypatch.setattr(ratelimit, "sleep", clock.sleep)


class DerivedInt(int):
    pass


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("capacity", True),
        ("capacity", 1.0),
        ("capacity", "1"),
        ("capacity", DerivedInt(1)),
        ("refill_amount", False),
        ("refill_amount", 1.0),
        ("refill_period_ns", True),
        ("refill_period_ns", 1.0),
    ],
)
def test_constructor_rejects_non_exact_ints(setting: str, value: object) -> None:
    kwargs = {"capacity": 1} | {setting: value}
    with pytest.raises(TypeError, match=f"^{setting} must be an int$"):
        ratelimit.TokenBucket(**kwargs)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("capacity", 0),
        ("capacity", -1),
        ("capacity", 1 << 63),
        ("refill_amount", 0),
        ("refill_amount", 1 << 63),
        ("refill_period_ns", 0),
        ("refill_period_ns", 1 << 63),
    ],
)
def test_constructor_rejects_out_of_range_settings(setting: str, value: int) -> None:
    with pytest.raises(ValueError, match=f"^{setting} must be between 1 and 2\\*\\*63 - 1$"):
        ratelimit.TokenBucket(**({"capacity": 1} | {setting: value}))


@pytest.mark.parametrize("cost", [True, 1.0, "1", DerivedInt(1)])
def test_acquire_rejects_non_exact_int_cost(cost: object) -> None:
    bucket = ratelimit.TokenBucket(2)

    async def run() -> None:
        with pytest.raises(TypeError, match="^cost must be an int$"):
            await bucket.acquire(cost)  # type: ignore[arg-type]

    asyncio.run(run())
    asyncio.run(bucket.acquire())


@pytest.mark.parametrize("cost", [0, -1, 3, 1 << 63])
def test_acquire_rejects_invalid_cost_before_loop_binding(cost: int) -> None:
    bucket = ratelimit.TokenBucket(2)

    async def run() -> None:
        with pytest.raises(ValueError, match="^cost must be between 1 and capacity$"):
            await bucket.acquire(cost)

    asyncio.run(run())
    asyncio.run(bucket.acquire())


def test_constructor_validation_order() -> None:
    with pytest.raises(TypeError, match="^capacity must be an int$"):
        ratelimit.TokenBucket(True, refill_amount=0, refill_period_ns=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="^refill_amount must be between 1 and 2\\*\\*63 - 1$"):
        ratelimit.TokenBucket(1, refill_amount=0, refill_period_ns=0)


def test_refill_fractional_credit_and_integer_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(3, refill_amount=3, refill_period_ns=10)

    async def exercise() -> None:
        await bucket.acquire(3)
        two = asyncio.create_task(bucket.acquire(2))
        await asyncio.sleep(0)
        await clock.release(7)
        await asyncio.wait_for(two, 1)
        # At 7 ns one scaled credit unit remains; three more ns add nine,
        # reaching the exact ten-unit charge for the final token.
        one = asyncio.create_task(bucket.acquire(1))
        await asyncio.sleep(0)
        await clock.release(10)
        await asyncio.wait_for(one, 1)

    asyncio.run(asyncio.wait_for(exercise(), 2))
    assert clock.sleeps == [7 / 1_000_000_000, 3 / 1_000_000_000]


def test_elapsed_refill_is_capped_at_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(2, refill_period_ns=10)

    async def run() -> None:
        await bucket.acquire(2)
        clock.now = 10**30
        await bucket.acquire(2)
        third = asyncio.create_task(bucket.acquire())
        await clock.release(10**30 + 10)
        await third

    asyncio.run(asyncio.wait_for(run(), 2))
    assert clock.sleeps == [10 / 1_000_000_000]


def test_early_wakeup_rechecks_before_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(1, refill_period_ns=10)

    async def run() -> None:
        await bucket.acquire()
        waiting = asyncio.create_task(bucket.acquire())
        await asyncio.sleep(0)
        await clock.release(9)
        await asyncio.sleep(0)
        assert not waiting.done()
        assert len(clock.sleeps) == 2
        await clock.release(10)
        await asyncio.wait_for(waiting, 1)

    asyncio.run(asyncio.wait_for(run(), 2))


def test_real_clock_preserves_nanosecond_refill_units() -> None:
    period = 5_000_000
    bucket = ratelimit.TokenBucket(1, refill_period_ns=period)

    async def run() -> None:
        start = monotonic_ns()
        await bucket.acquire()
        await bucket.acquire()
        await bucket.acquire()
        assert monotonic_ns() - start >= 2 * period

    asyncio.run(asyncio.wait_for(run(), 2))


def test_bucket_cannot_cross_event_loops_after_completed_acquire() -> None:
    bucket = ratelimit.TokenBucket(1)
    asyncio.run(bucket.acquire())
    with pytest.raises(RuntimeError, match="^TokenBucket cannot be used across event loops$"):
        asyncio.run(bucket.acquire())


def test_fifo_weighted_waiters_and_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(2, refill_period_ns=10)

    async def run() -> None:
        await bucket.acquire(2)
        order: list[str] = []
        newcomer: asyncio.Task[None] | None = None

        async def sleeper(delay: float) -> None:
            nonlocal newcomer
            clock.sleeps.append(delay)
            gate = asyncio.Event()
            clock.gates.put_nowait(gate)
            await gate.wait()
            # Schedule the newcomer before the head releases the lock and
            # schedules the tail. Advance one token's worth *after* release.
            if newcomer is None:
                newcomer = asyncio.create_task(arrive())

        async def arrive() -> None:
            clock.now = 30
            await acquire("newcomer", 1)

        async def acquire(name: str, cost: int) -> None:
            await bucket.acquire(cost)
            order.append(name)

        monkeypatch.setattr(ratelimit, "sleep", sleeper)
        head = asyncio.create_task(acquire("head", 2))
        await asyncio.sleep(0)
        clock.now = 10
        tail = asyncio.create_task(acquire("tail", 1))
        await asyncio.sleep(0)
        assert not order
        await clock.release(20)
        await asyncio.wait_for(head, 1)
        assert newcomer is not None
        # The newcomer runs first, but the original tail gets the available token.
        await asyncio.wait_for(tail, 1)
        await clock.release(40)
        await asyncio.wait_for(newcomer, 1)
        assert order == ["head", "tail", "newcomer"]

    asyncio.run(asyncio.wait_for(run(), 2))


def test_cancelled_queued_middle_waiter_does_not_block_survivors(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(2, refill_period_ns=10)

    async def run() -> None:
        await bucket.acquire(2)
        order: list[str] = []

        async def acquire(name: str, cost: int) -> None:
            await bucket.acquire(cost)
            order.append(name)

        head = asyncio.create_task(acquire("head", 2))
        await asyncio.sleep(0)
        middle = asyncio.create_task(acquire("middle", 1))
        await asyncio.sleep(0)
        tail = asyncio.create_task(acquire("tail", 1))
        await asyncio.sleep(0)
        middle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await middle
        await clock.release(20)
        await asyncio.wait_for(head, 1)
        await clock.release(30)
        await asyncio.wait_for(tail, 1)
        assert order == ["head", "tail"]

    asyncio.run(asyncio.wait_for(run(), 2))


def test_cancelled_sleeping_head_releases_lock_without_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(2, refill_period_ns=10)

    async def run() -> None:
        await bucket.acquire(2)
        head = asyncio.create_task(bucket.acquire(2))
        await clock.gates.get()
        tail = asyncio.create_task(bucket.acquire(1))
        await asyncio.sleep(0)
        head.cancel()
        with pytest.raises(asyncio.CancelledError):
            await head
        await clock.release(10)
        await tail
        assert clock.sleeps == [20 / 1_000_000_000, 10 / 1_000_000_000]

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize("period_ns", [2, 3])
def test_refill_saturates_across_native_credit_boundary(
    monkeypatch: pytest.MonkeyPatch, period_ns: int
) -> None:
    maximum = (1 << 63) - 1
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(
        maximum, refill_amount=maximum, refill_period_ns=period_ns
    )

    async def run() -> None:
        # Adding a refill to near-full credit would overflow uint64 at period 2.
        await bucket.acquire()
        clock.now += 1
        await bucket.acquire(maximum)
        # Multiplying a refill over this interval would also overflow uint64.
        clock.now += period_ns + 1
        await bucket.acquire(maximum)

    asyncio.run(asyncio.wait_for(run(), 2))
    assert clock.sleeps == []


def test_large_scaled_credit_and_elapsed_refill_do_not_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    maximum = (1 << 63) - 1
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(
        maximum, refill_amount=maximum, refill_period_ns=maximum
    )

    async def run() -> None:
        await bucket.acquire(maximum)
        clock.now = 1 << 100
        await bucket.acquire(maximum)

    asyncio.run(asyncio.wait_for(run(), 2))
    assert clock.sleeps == []


@pytest.mark.parametrize(("period", "refill"), [(2, 1), (2, (1 << 63) - 1), ((1 << 63) - 1, 1)])
def test_large_deficit_is_converted_to_finite_sleep(
    monkeypatch: pytest.MonkeyPatch, period: int, refill: int
) -> None:
    maximum = (1 << 63) - 1
    clock = FakeTime()
    patch_time(monkeypatch, clock)
    bucket = ratelimit.TokenBucket(maximum, refill_period_ns=period, refill_amount=refill)
    wait_ns = -(-(maximum * period) // refill)

    async def run() -> None:
        await bucket.acquire(maximum)
        waiter = asyncio.create_task(bucket.acquire(maximum))
        await asyncio.sleep(0)
        delay = clock.sleeps[0]
        assert delay == wait_ns / 1_000_000_000
        assert delay < float("inf")
        await clock.release(wait_ns)
        await waiter

    asyncio.run(asyncio.wait_for(run(), 2))
