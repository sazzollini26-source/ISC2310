"""Async HTTP with retries, response caching and robots.txt enforcement."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from .config import Settings


class FetchError(RuntimeError):
    """Non-retryable failure, or retries exhausted."""

    def __init__(self, message: str, *, status: int | None = None, url: str = "") -> None:
        self.status = status
        self.url = url
        super().__init__(message)


class RateLimited(FetchError):
    def __init__(self, url: str, retry_after: float | None) -> None:
        self.retry_after = retry_after
        super().__init__(f"429 rate limited: {url}", status=429, url=url)


class RobotsDenied(FetchError):
    """The site's robots.txt disallows this path for our user-agent."""


@dataclass(slots=True)
class Response:
    status: int
    text: str
    headers: dict[str, str]
    url: str
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.text)


class _MemoryCache:
    """TTL cache keyed by method+url+params. Bounded so a long session can't
    grow without limit."""

    def __init__(self, max_entries: int = 512) -> None:
        self._data: dict[str, tuple[float, Response]] = {}
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(method: str, url: str, params: Mapping[str, Any] | None) -> str:
        blob = f"{method}|{url}|{sorted((params or {}).items())}"
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> Response | None:
        entry = self._data.get(key)
        if not entry:
            self.misses += 1
            return None
        expires, response = entry
        if time.monotonic() > expires:
            self._data.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        cached = Response(response.status, response.text, response.headers, response.url, True)
        return cached

    def put(self, key: str, response: Response, ttl: float) -> None:
        if ttl <= 0:
            return
        if len(self._data) >= self.max_entries:
            # Drop the entry closest to expiry; cheap approximation of LRU.
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.monotonic() + ttl, response)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total * 100, 1) if total else 0.0,
        }


class HostLimiter:
    """Per-host politeness gate, independent of any per-key budget.

    Two providers pointing at the same host still share one limiter, which is
    what keeps a burst of scraper calls from looking like an attack.
    """

    def __init__(self, default_interval: float = 0.2) -> None:
        self.default_interval = default_interval
        self._intervals: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def set_interval(self, host: str, interval: float) -> None:
        self._intervals[host] = interval

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        interval = self._intervals.get(host, self.default_interval)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host, 0.0)
            delta = time.monotonic() - last
            if delta < interval:
                await asyncio.sleep(interval - delta)
            self._last[host] = time.monotonic()


class RobotsCache:
    """Fetches and caches robots.txt per host."""

    def __init__(self, ttl: float = 3600.0) -> None:
        self._data: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self.ttl = ttl

    async def allowed(self, client: httpx.AsyncClient, url: str, user_agent: str) -> bool:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        entry = self._data.get(host)
        now = time.monotonic()
        if not entry or now > entry[0]:
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                resp = await client.get(f"{host}/robots.txt", timeout=6.0)
                if resp.status_code == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except Exception:
                # No robots.txt, or it failed to load. Convention is that an
                # unreachable robots.txt means "allowed"; we honour that but
                # still go through the host rate limiter.
                parser = None
            self._data[host] = (now + self.ttl, parser)
            entry = self._data[host]
        parser = entry[1]
        if parser is None:
            return True
        return parser.can_fetch(user_agent, url)


class HttpClient:
    """The single outbound HTTP path for the whole application."""

    RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.cache = _MemoryCache()
        self.limiter = HostLimiter()
        self.robots = RobotsCache()
        self._client = httpx.AsyncClient(
            timeout=self.settings.http_timeout,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": self.settings.user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        self.request_count = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        ttl: float | None = None,
        check_robots: bool = False,
        retries: int | None = None,
    ) -> Response:
        if self.settings.offline:
            raise FetchError("offline mode is enabled (MKT_OFFLINE=1)", url=url)

        cache_key = _MemoryCache.key("GET", url, params)
        effective_ttl = self.settings.cache_ttl if ttl is None else ttl
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if check_robots and self.settings.respect_robots:
            if not await self.robots.allowed(self._client, url, self.settings.user_agent):
                raise RobotsDenied(f"robots.txt disallows {url}", url=url)

        attempts = self.settings.max_retries if retries is None else retries
        last_error: Exception | None = None

        for attempt in range(attempts + 1):
            await self.limiter.wait(url)
            try:
                self.request_count += 1
                raw = await self._client.get(url, params=params, headers=dict(headers or {}))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise FetchError(f"transport failure: {exc}", url=url) from exc

            if raw.status_code == 429:
                retry_after = _parse_retry_after(raw.headers.get("Retry-After"))
                # Surfaced to the caller so the key that earned it gets benched.
                raise RateLimited(url, retry_after)

            if raw.status_code in self.RETRY_STATUSES and attempt < attempts:
                await asyncio.sleep(_backoff(attempt))
                continue

            if raw.status_code >= 400:
                raise FetchError(
                    f"HTTP {raw.status_code} for {url}: {raw.text[:200]}",
                    status=raw.status_code,
                    url=url,
                )

            response = Response(
                status=raw.status_code,
                text=raw.text,
                headers=dict(raw.headers),
                url=str(raw.url),
            )
            self.cache.put(cache_key, response, effective_ttl)
            return response

        raise FetchError(f"retries exhausted for {url}: {last_error}", url=url)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.get(url, **kwargs)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise FetchError(
                f"invalid JSON from {url}: {response.text[:200]}", url=url
            ) from exc

    def stats(self) -> dict:
        return {"requests": self.request_count, "cache": self.cache.stats()}


def _backoff(attempt: int, base: float = 0.4, cap: float = 8.0) -> float:
    """Exponential backoff with full jitter, so parallel retries decorrelate."""
    return random.uniform(0, min(cap, base * (2 ** attempt)))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
