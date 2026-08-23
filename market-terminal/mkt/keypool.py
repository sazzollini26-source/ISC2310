"""Multi-key rotation with per-key rate budgets.

The whole point of holding twenty API keys is that no single one of them is
allowed to be the bottleneck. Each key gets its own token bucket sized to the
vendor's published limit; the pool hands out whichever eligible key has been
idle longest, and a key that gets 429'd is benched until its cooldown expires.
When every key in a pool is benched the pool reports itself exhausted and the
router moves to the next provider entirely.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

from .config import PROVIDER_SPECS, ProviderSpec


class TokenBucket:
    """Classic token bucket. ``capacity`` tokens refill over ``per`` seconds."""

    __slots__ = ("capacity", "per", "_tokens", "_last")

    def __init__(self, capacity: int, per: float) -> None:
        self.capacity = max(1, capacity)
        self.per = max(0.001, per)
        self._tokens = float(self.capacity)
        self._last = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * (self.capacity / self.per))
        self._last = now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def try_take(self, n: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def wait_time(self, n: float = 1.0) -> float:
        """Seconds until ``n`` tokens are available (0 when ready now)."""
        self._refill()
        if self._tokens >= n:
            return 0.0
        deficit = n - self._tokens
        return deficit * (self.per / self.capacity)


@dataclass(slots=True)
class ApiKey:
    """One credential plus its health and usage accounting."""

    value: str
    provider: str
    label: str
    bucket: TokenBucket
    daily_cap: int = 0
    calls_today: int = 0
    day_stamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    cooldown_until: float = 0.0
    last_used: float = 0.0
    successes: int = 0
    failures: int = 0
    rate_limit_hits: int = 0
    consecutive_failures: int = 0

    @property
    def masked(self) -> str:
        """Never print a key in full — logs and the UI show this instead."""
        if len(self.value) <= 8:
            return f"{self.value[:2]}***"
        return f"{self.value[:4]}...{self.value[-4:]}"

    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self.day_stamp:
            self.day_stamp = today
            self.calls_today = 0

    @property
    def cooling(self) -> bool:
        return time.monotonic() < self.cooldown_until

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, self.cooldown_until - time.monotonic())

    @property
    def daily_exhausted(self) -> bool:
        self._roll_day()
        return self.daily_cap > 0 and self.calls_today >= self.daily_cap

    def available(self) -> bool:
        return not self.cooling and not self.daily_exhausted and self.bucket.tokens >= 1.0

    def wait_time(self) -> float:
        if self.daily_exhausted:
            return float("inf")
        return max(self.cooldown_remaining, self.bucket.wait_time())

    def take(self) -> None:
        self._roll_day()
        self.bucket.try_take()
        self.calls_today += 1
        self.last_used = time.monotonic()

    def report_success(self) -> None:
        self.successes += 1
        self.consecutive_failures = 0

    def report_rate_limited(self, retry_after: float | None = None) -> None:
        self.rate_limit_hits += 1
        self.consecutive_failures += 1
        # Trust Retry-After when the server sends it, else back off
        # exponentially so a repeatedly-throttled key drops out of rotation.
        wait = retry_after if retry_after else min(300.0, 30.0 * (2 ** min(self.consecutive_failures - 1, 3)))
        self.cooldown_until = time.monotonic() + wait

    def report_failure(self, *, fatal: bool = False) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        if fatal:
            # 401/403 means the key is bad, not busy. Bench it for an hour
            # rather than burning the retry budget on it.
            self.cooldown_until = time.monotonic() + 3600.0
        elif self.consecutive_failures >= 3:
            self.cooldown_until = time.monotonic() + min(120.0, 10.0 * self.consecutive_failures)

    def stats(self) -> dict:
        return {
            "label": self.label,
            "masked": self.masked,
            "successes": self.successes,
            "failures": self.failures,
            "rate_limit_hits": self.rate_limit_hits,
            "calls_today": self.calls_today,
            "daily_cap": self.daily_cap,
            "tokens": round(self.bucket.tokens, 2),
            "cooling_for": round(self.cooldown_remaining, 1),
            "available": self.available(),
        }


class PoolExhausted(RuntimeError):
    """Raised when every key in a pool is throttled, benched or spent."""

    def __init__(self, provider: str, retry_in: float) -> None:
        self.provider = provider
        self.retry_in = retry_in
        super().__init__(
            f"{provider}: all keys exhausted, next available in {retry_in:.1f}s"
        )


class KeyPool:
    """The rotating set of keys for a single provider."""

    def __init__(self, spec: ProviderSpec, keys: list[str] | None = None) -> None:
        self.spec = spec
        self.name = spec.name
        self._lock = asyncio.Lock()
        self.keys: list[ApiKey] = []
        for i, raw in enumerate(keys or [], start=1):
            self.add(raw, label=f"{spec.name}#{i}")

    def add(self, value: str, label: str | None = None) -> ApiKey:
        calls, per = self.spec.rate
        key = ApiKey(
            value=value,
            provider=self.name,
            label=label or f"{self.name}#{len(self.keys) + 1}",
            bucket=TokenBucket(calls, per),
            daily_cap=self.spec.daily_cap,
        )
        self.keys.append(key)
        return key

    @property
    def keyless(self) -> bool:
        return self.spec.keyless

    @property
    def size(self) -> int:
        return len(self.keys)

    @property
    def usable(self) -> bool:
        """True when this provider could serve a request now or soon."""
        return self.keyless or bool(self.keys)

    def available_count(self) -> int:
        return sum(1 for k in self.keys if k.available())

    def next_available_in(self) -> float:
        if self.keyless:
            return 0.0
        if not self.keys:
            return float("inf")
        return min(k.wait_time() for k in self.keys)

    async def acquire(self, *, wait: bool = False, timeout: float = 10.0) -> ApiKey | None:
        """Check out a key.

        Returns ``None`` for keyless providers (nothing to check out). Picks the
        eligible key that has been idle longest so load spreads evenly instead
        of hammering key #1 until it throttles.
        """
        if self.keyless:
            return None
        if not self.keys:
            raise PoolExhausted(self.name, float("inf"))

        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                ready = [k for k in self.keys if k.available()]
                if ready:
                    ready.sort(key=lambda k: k.last_used)
                    chosen = ready[0]
                    chosen.take()
                    return chosen
                retry_in = self.next_available_in()

            if not wait or retry_in == float("inf"):
                raise PoolExhausted(self.name, retry_in)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PoolExhausted(self.name, retry_in)
            await asyncio.sleep(min(retry_in + 0.01, remaining, 1.0))

    def health(self) -> dict:
        return {
            "provider": self.name,
            "keyless": self.keyless,
            "keys": self.size,
            "available": self.available_count(),
            "next_available_in": round(self.next_available_in(), 1)
            if self.next_available_in() != float("inf") else None,
            "detail": [k.stats() for k in self.keys],
        }


class KeyRegistry:
    """All pools, built from the environment."""

    def __init__(self, pools: dict[str, KeyPool] | None = None) -> None:
        self.pools: dict[str, KeyPool] = pools or {}

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "KeyRegistry":
        env = dict(os.environ if env is None else env)
        pools: dict[str, KeyPool] = {}
        for name, spec in PROVIDER_SPECS.items():
            keys = _collect_keys(spec, env)
            pools[name] = KeyPool(spec, keys)
        return cls(pools)

    def get(self, provider: str) -> KeyPool | None:
        return self.pools.get(provider)

    def usable_providers(self, capability: str | None = None) -> list[str]:
        out = []
        for name, pool in self.pools.items():
            if not pool.usable:
                continue
            if capability and capability not in pool.spec.capabilities:
                continue
            out.append(name)
        out.sort(key=lambda n: self.pools[n].spec.priority)
        return out

    def total_keys(self) -> int:
        return sum(p.size for p in self.pools.values())

    def health(self) -> list[dict]:
        return [p.health() for p in self.pools.values() if p.usable]

    def summary(self) -> str:
        keyed = [p for p in self.pools.values() if p.size]
        keyless = [p for p in self.pools.values() if p.keyless]
        return (
            f"{self.total_keys()} key(s) across {len(keyed)} keyed provider(s); "
            f"{len(keyless)} keyless source(s)"
        )


def _collect_keys(spec: ProviderSpec, env: dict[str, str]) -> list[str]:
    """Gather every key for one provider from the environment.

    Three accepted spellings, so a big key set stays readable in .env::

        MKT_FINNHUB_KEYS=k1,k2,k3      # comma-separated list
        MKT_FINNHUB_KEY=k1             # single
        MKT_FINNHUB_KEY_1=k1           # numbered, any count
        FINNHUB_API_KEY=k1             # bare vendor-style name
    """
    found: list[str] = []
    seen: set[str] = set()

    def push(raw: str) -> None:
        for part in raw.split(","):
            value = part.strip()
            if value and value not in seen and not value.lower().startswith("your_"):
                seen.add(value)
                found.append(value)

    for stem in spec.env:
        for prefix in ("MKT_", ""):
            for suffix in ("_KEYS", "_KEY", "_API_KEY", "_TOKEN"):
                value = env.get(f"{prefix}{stem}{suffix}")
                if value:
                    push(value)
            # Numbered variants: <STEM>_KEY_1 .. _KEY_50
            for i in range(1, 51):
                value = env.get(f"{prefix}{stem}_KEY_{i}")
                if value:
                    push(value)
    return found
