__all__ = ["TokenBucket"]

class TokenBucket:
    def __init__(
        self,
        capacity: int,
        *,
        refill_amount: int = 1,
        refill_period_ns: int = 1_000_000_000,
    ) -> None: ...
    async def acquire(self, cost: int = 1) -> None: ...
