"""Capability routing with health-aware failover.

The router is what turns a pile of providers into one dependable data feed. For
each request it builds an ordered candidate list — cheapest and healthiest
first — walks it until something answers, and records the outcome so a flaky
source drifts down the order on its own.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .config import Settings
from .http import FetchError, HttpClient, RateLimited
from .keypool import KeyRegistry, PoolExhausted
from .models import Filing, NewsItem, Profile, Quote, Series
from .providers import Provider, build_providers
from .providers.base import NotSupported, SymbolNotFound


@dataclass(slots=True)
class ProviderHealth:
    """Rolling success record, used to reorder candidates.

    Two details matter here. The success rate is Laplace-smoothed, so a single
    transient failure nudges a provider down the order instead of launching it
    out of contention. And the counters decay toward neutral while a provider
    sits idle, so a source that got demoted is eventually re-probed rather than
    exiled forever by one bad minute.
    """

    calls: int = 0
    successes: int = 0
    failures: int = 0
    rate_limits: int = 0
    total_latency: float = 0.0
    last_error: str = ""
    penalty_until: float = 0.0
    last_activity: float = field(default_factory=time.monotonic)
    decay_after: float = 120.0

    def _decay(self) -> None:
        """Halve the record for each idle window, so old news stops counting."""
        elapsed = time.monotonic() - self.last_activity
        if elapsed < self.decay_after or self.calls == 0:
            return
        windows = int(elapsed // self.decay_after)
        factor = 2 ** min(windows, 8)
        self.calls //= factor
        self.successes //= factor
        self.failures //= factor
        self.total_latency /= factor
        self.last_activity = time.monotonic()

    @property
    def success_rate(self) -> float:
        """Smoothed: an untried provider reads as 0.5, not a perfect 1.0."""
        self._decay()
        return (self.successes + 1) / (self.calls + 2)

    @property
    def raw_success_rate(self) -> float:
        return self.successes / self.calls if self.calls else 1.0

    @property
    def avg_latency(self) -> float:
        return self.total_latency / self.successes if self.successes else 0.0

    @property
    def penalised(self) -> bool:
        return time.monotonic() < self.penalty_until

    def score(self, base_priority: int) -> float:
        """Lower sorts first. Priority leads; health adjusts within ~40 points."""
        score = float(base_priority)
        score += (1.0 - self.success_rate) * 40.0
        score += min(self.avg_latency, 5.0) * 2.0
        if self.penalised:
            score += 500.0
        return score


class NoProviderAvailable(RuntimeError):
    """Every candidate for this capability failed or was unavailable."""

    def __init__(self, capability: str, attempts: dict[str, str]) -> None:
        self.capability = capability
        self.attempts = attempts
        detail = "; ".join(f"{k}: {v}" for k, v in attempts.items()) or "no candidates"
        super().__init__(f"no provider could serve '{capability}' ({detail})")


@dataclass(slots=True)
class RouteResult:
    """What came back, and which source actually produced it."""

    value: object
    provider: str
    latency: float
    attempts: dict[str, str] = field(default_factory=dict)


class Router:
    def __init__(
        self,
        http: HttpClient | None = None,
        settings: Settings | None = None,
        keys: KeyRegistry | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.http = http or HttpClient(self.settings)
        self.keys = keys or KeyRegistry.from_env()
        self.providers: dict[str, Provider] = build_providers(self.http, self.settings, self.keys)
        self.health: dict[str, ProviderHealth] = {n: ProviderHealth() for n in self.providers}

    async def aclose(self) -> None:
        await self.http.aclose()

    # -- candidate selection ----------------------------------------------

    def candidates(self, capability: str, prefer: str | None = None) -> list[str]:
        names = [n for n, p in self.providers.items() if p.supports(capability)]
        names.sort(key=lambda n: self.health[n].score(self.providers[n].spec.priority))
        if prefer and prefer in names:
            names.remove(prefer)
            names.insert(0, prefer)
        return names

    def _record_success(self, name: str, latency: float) -> None:
        health = self.health[name]
        health._decay()
        health.calls += 1
        health.successes += 1
        health.total_latency += latency
        health.last_activity = time.monotonic()

    def _record_failure(self, name: str, error: Exception, *, rate_limited: bool = False) -> None:
        health = self.health[name]
        health._decay()
        health.calls += 1
        health.failures += 1
        health.last_activity = time.monotonic()
        health.last_error = f"{type(error).__name__}: {error}"[:200]
        if rate_limited:
            health.rate_limits += 1
            health.penalty_until = time.monotonic() + 60.0
        elif health.calls >= 3 and health.raw_success_rate < 0.34:
            # Consistently broken: rest it briefly so it stops costing latency.
            health.penalty_until = time.monotonic() + 30.0

    # -- the core walk -----------------------------------------------------

    async def _dispatch(
        self, capability: str, call, prefer: str | None = None, allow: list[str] | None = None
    ) -> RouteResult:
        attempts: dict[str, str] = {}
        names = self.candidates(capability, prefer)
        if allow:
            allowed = {a.lower() for a in allow}
            names = [n for n in names if n in allowed]

        for name in names:
            provider = self.providers[name]
            started = time.monotonic()
            try:
                value = await call(provider)
            except PoolExhausted as exc:
                attempts[name] = f"keys exhausted (retry in {exc.retry_in:.0f}s)"
                self._record_failure(name, exc, rate_limited=True)
            except RateLimited as exc:
                attempts[name] = "rate limited"
                # Bench the specific key that earned the 429, not the provider.
                pool = self.keys.get(name)
                if pool and pool.keys:
                    recent = max(pool.keys, key=lambda k: k.last_used)
                    recent.report_rate_limited(getattr(exc, "retry_after", None))
                self._record_failure(name, exc, rate_limited=True)
            except (SymbolNotFound, NotSupported) as exc:
                # Not a provider fault — this source simply lacks the symbol.
                attempts[name] = str(exc)[:120]
            except FetchError as exc:
                attempts[name] = f"http {exc.status or 'error'}"
                pool = self.keys.get(name)
                if pool and pool.keys and exc.status in (401, 403):
                    recent = max(pool.keys, key=lambda k: k.last_used)
                    recent.report_failure(fatal=True)
                self._record_failure(name, exc)
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                attempts[name] = f"{type(exc).__name__}: {exc}"[:120]
                self._record_failure(name, exc)
            else:
                latency = time.monotonic() - started
                self._record_success(name, latency)
                pool = self.keys.get(name)
                if pool and pool.keys:
                    max(pool.keys, key=lambda k: k.last_used).report_success()
                return RouteResult(value, name, latency, attempts)

        raise NoProviderAvailable(capability, attempts)

    # -- public API --------------------------------------------------------

    async def quote(self, symbol: str, *, prefer: str | None = None,
                    allow: list[str] | None = None) -> Quote:
        result = await self._dispatch(
            "quote", lambda p: p.quote(symbol), prefer=prefer, allow=allow)
        return result.value  # type: ignore[return-value]

    async def quotes(self, symbols: list[str], *, prefer: str | None = None
                     ) -> dict[str, Quote | Exception]:
        """Fan out across a watchlist concurrently; failures come back inline."""
        async def one(sym: str):
            try:
                return sym, await self.quote(sym, prefer=prefer)
            except Exception as exc:  # noqa: BLE001
                return sym, exc

        pairs = await asyncio.gather(*(one(s) for s in symbols))
        return dict(pairs)

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120,
                      *, prefer: str | None = None) -> Series:
        result = await self._dispatch(
            "history", lambda p: p.history(symbol, interval, lookback), prefer=prefer)
        return result.value  # type: ignore[return-value]

    async def news(self, symbol: str | None = None, limit: int = 20,
                   *, merge: bool = True) -> list[NewsItem]:
        """News merges across sources rather than stopping at the first hit —
        breadth is the point of a news panel."""
        if not merge:
            result = await self._dispatch("news", lambda p: p.news(symbol, limit))
            return result.value  # type: ignore[return-value]

        names = self.candidates("news")

        async def pull(name: str) -> list[NewsItem]:
            try:
                return await self.providers[name].news(symbol, limit)
            except Exception as exc:  # noqa: BLE001
                self._record_failure(name, exc)
                return []

        batches = await asyncio.gather(*(pull(n) for n in names))
        merged: list[NewsItem] = []
        seen: set[str] = set()
        for batch in batches:
            for item in batch:
                fingerprint = (item.url or item.title).strip().lower()
                if fingerprint and fingerprint not in seen:
                    seen.add(fingerprint)
                    merged.append(item)
        merged.sort(key=lambda n: n.published, reverse=True)
        return merged[:limit]

    async def profile(self, symbol: str, *, prefer: str | None = None) -> Profile:
        result = await self._dispatch("profile", lambda p: p.profile(symbol), prefer=prefer)
        return result.value  # type: ignore[return-value]

    async def filings(self, symbol: str, forms: tuple[str, ...] = (),
                      limit: int = 20) -> list[Filing]:
        result = await self._dispatch("filings", lambda p: p.filings(symbol, forms, limit))
        return result.value  # type: ignore[return-value]

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        result = await self._dispatch("search", lambda p: p.search(query, limit))
        return result.value  # type: ignore[return-value]

    # -- introspection -----------------------------------------------------

    def status(self) -> dict:
        return {
            "providers": {
                name: {
                    "priority": provider.spec.priority,
                    "capabilities": list(provider.capabilities),
                    "keys": self.keys.get(name).size if self.keys.get(name) else 0,
                    "keyless": provider.spec.keyless,
                    "calls": self.health[name].calls,
                    "success_rate": round(self.health[name].raw_success_rate * 100, 1),
                    "avg_latency_ms": round(self.health[name].avg_latency * 1000, 1),
                    "rate_limits": self.health[name].rate_limits,
                    "penalised": self.health[name].penalised,
                    "last_error": self.health[name].last_error,
                }
                for name, provider in self.providers.items()
            },
            "keys": self.keys.health(),
            "http": self.http.stats(),
        }
