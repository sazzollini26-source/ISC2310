"""Provider contract.

A provider is a thin adapter: take a normalised request, spend a key from its
pool, hit the vendor, return normalised models. All routing, retrying and
failover lives above this layer, so adding a fourteenth source is a small file.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..config import PROVIDER_SPECS, ProviderSpec, Settings
from ..keypool import ApiKey, KeyPool
from ..models import Filing, NewsItem, Profile, Quote, Series

if TYPE_CHECKING:
    from ..http import HttpClient


class NotSupported(NotImplementedError):
    """This provider does not offer that capability."""


class SymbolNotFound(LookupError):
    """The vendor answered, but knows nothing about this symbol."""


# Canonical intervals. Each provider maps these onto its own vocabulary.
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")


class Provider:
    name: str = "base"

    def __init__(self, http: "HttpClient", settings: Settings, pool: KeyPool | None = None) -> None:
        self.http = http
        self.settings = settings
        self.pool = pool

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDER_SPECS[self.name]

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.spec.capabilities

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    async def acquire(self) -> ApiKey | None:
        """Check a key out of this provider's pool (None when keyless)."""
        if self.pool is None:
            return None
        return await self.pool.acquire()

    # -- capability surface; providers override what they offer ------------

    async def quote(self, symbol: str) -> Quote:
        raise NotSupported(f"{self.name} does not provide quotes")

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        raise NotSupported(f"{self.name} does not provide history")

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        raise NotSupported(f"{self.name} does not provide news")

    async def profile(self, symbol: str) -> Profile:
        raise NotSupported(f"{self.name} does not provide profiles")

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        raise NotSupported(f"{self.name} does not provide search")

    async def filings(self, symbol: str, forms: tuple[str, ...] = (), limit: int = 20) -> list[Filing]:
        raise NotSupported(f"{self.name} does not provide filings")


# -- shared parsing helpers ------------------------------------------------


def num(value: object) -> float | None:
    """Best-effort float. Vendors send '', 'None', 'N/A' and nulls freely."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return None if result != result else result  # drop NaN
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def epoch(value: object) -> datetime:
    """Unix seconds (or milliseconds) to aware UTC datetime."""
    seconds = num(value) or 0.0
    if seconds > 1e11:  # milliseconds
        seconds /= 1000.0
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def parse_date(text: str) -> datetime:
    """Parse the handful of date shapes vendors actually emit."""
    raw = (text or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)
