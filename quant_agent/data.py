"""Market data access.

Providers are pluggable so the scoring and risk stack can be exercised offline
and deterministically. ``SyntheticProvider`` is the default precisely because it
is *obviously* not real data - nothing downstream can mistake it for a live feed.
"""

from __future__ import annotations

import csv
import math
import os
import random
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Series:
    """An ordered price history for one symbol, oldest bar first."""

    symbol: str
    bars: tuple[Bar, ...]
    source: str

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    @property
    def highs(self) -> list[float]:
        return [b.high for b in self.bars]

    @property
    def lows(self) -> list[float]:
        return [b.low for b in self.bars]

    @property
    def volumes(self) -> list[float]:
        return [b.volume for b in self.bars]

    @property
    def last(self) -> Bar:
        return self.bars[-1]

    def age_minutes(self, now: datetime | None = None) -> float:
        """Minutes since the most recent bar closed."""
        now = now or datetime.now(timezone.utc)
        return (now - self.last.ts).total_seconds() / 60.0

    @property
    def is_synthetic(self) -> bool:
        return self.source == "synthetic"


class MarketDataProvider(ABC):
    """Interface every data source must satisfy."""

    name: str = "abstract"

    @abstractmethod
    def fetch(self, symbol: str, lookback: int = 250) -> Series:
        """Return up to ``lookback`` bars for ``symbol``, oldest first."""


class SyntheticProvider(MarketDataProvider):
    """Deterministic simulated price paths for offline testing and demos.

    This is NOT market data. It exists so the pipeline can be run and tested
    without a live feed. Any Series it produces is tagged ``source='synthetic'``
    and the agent refuses to route live orders on it.
    """

    name = "synthetic"

    def __init__(self, seed: int = 20260823, drift: float = 0.0003, vol: float = 0.022):
        self.seed = seed
        self.drift = drift
        self.vol = vol

    def fetch(self, symbol: str, lookback: int = 250) -> Series:
        # Seed per symbol so each name has a stable, distinct path.
        rng = random.Random(f"{self.seed}:{symbol}")
        # crc32, not hash(): the builtin is salted per process.
        price = 50.0 + (zlib.crc32(symbol.encode()) % 400)
        start = datetime.now(timezone.utc) - timedelta(days=lookback - 1)

        bars: list[Bar] = []
        for i in range(lookback):
            shock = rng.gauss(self.drift, self.vol)
            close = max(price * math.exp(shock), 0.01)
            high = max(price, close) * (1 + abs(rng.gauss(0, 0.004)))
            low = min(price, close) * (1 - abs(rng.gauss(0, 0.004)))
            bars.append(
                Bar(
                    ts=start + timedelta(days=i),
                    open=price,
                    high=high,
                    low=low,
                    close=close,
                    volume=float(rng.randint(5_000_000, 60_000_000)),
                )
            )
            price = close

        return Series(symbol=symbol, bars=tuple(bars), source=self.name)


class CsvProvider(MarketDataProvider):
    """Reads ``<data_dir>/<SYMBOL>.csv`` with headers date,open,high,low,close,volume."""

    name = "csv"

    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def fetch(self, symbol: str, lookback: int = 250) -> Series:
        path = os.path.join(self.data_dir, f"{symbol}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No CSV history for {symbol} at {path}")

        bars: list[Bar] = []
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                bars.append(
                    Bar(
                        ts=_parse_ts(row["date"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume") or 0.0),
                    )
                )

        bars.sort(key=lambda b: b.ts)
        return Series(symbol=symbol, bars=tuple(bars[-lookback:]), source=self.name)


class YFinanceProvider(MarketDataProvider):
    """Real historical data via the optional ``yfinance`` package.

    Imported lazily: the package is not a hard dependency, and its absence
    should surface as a clear configuration error rather than an ImportError
    at module load.
    """

    name = "yfinance"

    def fetch(self, symbol: str, lookback: int = 250) -> Series:
        try:
            import yfinance  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "provider='yfinance' requires the yfinance package "
                "(pip install yfinance). Install it or use provider='csv'."
            ) from exc

        frame = yfinance.Ticker(symbol).history(period=f"{max(lookback, 60)}d")
        if frame.empty:  # pragma: no cover - network dependent
            raise RuntimeError(f"yfinance returned no rows for {symbol}")

        bars = [
            Bar(
                ts=index.to_pydatetime().astimezone(timezone.utc),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
            )
            for index, row in frame.iterrows()
        ]
        return Series(symbol=symbol, bars=tuple(bars[-lookback:]), source=self.name)


def _parse_ts(raw: str) -> datetime:
    """Parse an ISO-ish date or datetime into an aware UTC datetime."""
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text[:10], "%Y-%m-%d")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_provider(name: str, data_dir: str = "data") -> MarketDataProvider:
    """Factory used by the CLI and agent."""
    providers: dict[str, callable] = {
        "synthetic": SyntheticProvider,
        "csv": lambda: CsvProvider(data_dir),
        "yfinance": YFinanceProvider,
    }
    if name not in providers:
        raise ValueError(f"Unknown provider '{name}'. Choose from {sorted(providers)}.")
    return providers[name]()
