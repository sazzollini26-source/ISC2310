"""Normalised data types shared by every provider.

Providers speak wildly different dialects; everything is converted into these
dataclasses at the edge so the router, cache and UI only ever see one shape.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _utc(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


@dataclass(slots=True)
class Quote:
    """A point-in-time price for one security."""

    symbol: str
    price: float
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    currency: str = "USD"
    venue: str | None = None
    source: str = "unknown"
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.ts = _utc(self.ts)
        # Providers are inconsistent about which of the three they send, so
        # derive whatever is missing rather than showing blanks in the grid.
        if self.change is None and self.prev_close not in (None, 0):
            self.change = self.price - self.prev_close
        if self.change_pct is None and self.prev_close not in (None, 0):
            self.change_pct = (self.price - self.prev_close) / self.prev_close * 100.0
        if self.prev_close is None and self.change is not None:
            self.prev_close = self.price - self.change

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.ts).total_seconds()

    def is_stale(self, max_age: float) -> bool:
        return self.age_seconds > max_age


@dataclass(slots=True)
class Bar:
    """One OHLCV candle."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        self.ts = _utc(self.ts)


@dataclass(slots=True)
class Series:
    """An ordered set of bars for one symbol at one interval."""

    symbol: str
    interval: str
    bars: list[Bar] = field(default_factory=list)
    source: str = "unknown"

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.bars.sort(key=lambda b: b.ts)

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    def pct_change(self) -> float | None:
        if len(self.bars) < 2 or self.bars[0].close == 0:
            return None
        return (self.bars[-1].close - self.bars[0].close) / self.bars[0].close * 100.0

    def returns(self) -> list[float]:
        out: list[float] = []
        for prev, cur in zip(self.bars, self.bars[1:]):
            if prev.close:
                out.append((cur.close - prev.close) / prev.close)
        return out

    def realised_vol(self, periods_per_year: int = 252) -> float | None:
        """Annualised stdev of simple returns. None when there isn't enough data."""
        rets = self.returns()
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(periods_per_year) * 100.0

    def max_drawdown(self) -> float | None:
        """Worst peak-to-trough decline over the window, as a negative percent."""
        if not self.bars:
            return None
        peak = self.bars[0].close
        worst = 0.0
        for bar in self.bars:
            peak = max(peak, bar.close)
            if peak:
                worst = min(worst, (bar.close - peak) / peak)
        return worst * 100.0


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    published: datetime
    publisher: str = ""
    summary: str = ""
    symbols: list[str] = field(default_factory=list)
    source: str = "unknown"

    def __post_init__(self) -> None:
        self.published = _utc(self.published)
        self.symbols = [s.upper().strip() for s in self.symbols if s and s.strip()]


@dataclass(slots=True)
class Filing:
    """An SEC EDGAR submission."""

    symbol: str
    form: str
    filed: datetime
    title: str
    url: str
    accession: str = ""
    source: str = "edgar"

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.filed = _utc(self.filed)


@dataclass(slots=True)
class Profile:
    symbol: str
    name: str = ""
    exchange: str = ""
    industry: str = ""
    sector: str = ""
    country: str = ""
    currency: str = "USD"
    market_cap: float | None = None
    website: str = ""
    description: str = ""
    source: str = "unknown"

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()


def to_dict(obj: Any) -> dict:
    """asdict() that renders datetimes as ISO strings, for JSON/CSV export."""
    def _conv(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [_conv(v) for v in value]
        if isinstance(value, dict):
            return {k: _conv(v) for k, v in value.items()}
        return value

    return {k: _conv(v) for k, v in asdict(obj).items()}
