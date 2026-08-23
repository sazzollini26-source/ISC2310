"""Market sentiment scoring.

Produces a single composite score in [-1, +1] per symbol from weighted
technical components, plus a human-readable rationale that is written verbatim
into the trade journal. Every component is bounded, so no single indicator can
dominate the composite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import indicators as ind
from .data import Series

# Component weights. These sum to 1.0 so the composite stays in [-1, +1].
WEIGHTS: dict[str, float] = {
    "trend": 0.35,      # SMA20 vs SMA50 separation
    "momentum": 0.25,   # RSI displacement from neutral
    "macd": 0.25,       # MACD histogram, price-normalised
    "volume": 0.15,     # Participation confirming the move
}


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Raw indicator readings captured at decision time."""

    price: float
    sma20: float | None
    sma50: float | None
    rsi14: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_hist: float | None
    atr14: float | None
    atr_pct: float | None
    bb_lower: float | None
    bb_upper: float | None
    volatility: float | None
    roc10: float | None
    volume: float
    avg_volume20: float | None

    def as_dict(self) -> dict:
        return {k: _round(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class SentimentScore:
    """Composite sentiment for one symbol."""

    symbol: str
    composite: float                 # [-1, +1]
    label: str                       # BULLISH | NEUTRAL | BEARISH
    components: dict[str, float]
    snapshot: IndicatorSnapshot
    rationale: list[str] = field(default_factory=list)
    insufficient_data: bool = False

    @property
    def direction(self) -> str:
        if self.label == "BULLISH":
            return "buy"
        if self.label == "BEARISH":
            return "sell"
        return "hold"

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "composite": round(self.composite, 4),
            "label": self.label,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "indicators": self.snapshot.as_dict(),
            "rationale": self.rationale,
            "insufficient_data": self.insufficient_data,
        }


def build_snapshot(series: Series) -> IndicatorSnapshot:
    """Compute every indicator the scorer needs from a price series."""
    closes, highs, lows, volumes = (
        series.closes,
        series.highs,
        series.lows,
        series.volumes,
    )
    price = closes[-1]

    macd_values = ind.macd(closes)
    bands = ind.bollinger(closes)
    atr14 = ind.atr(highs, lows, closes)

    return IndicatorSnapshot(
        price=price,
        sma20=ind.sma(closes, 20),
        sma50=ind.sma(closes, 50),
        rsi14=ind.rsi(closes, 14),
        macd_line=macd_values[0] if macd_values else None,
        macd_signal=macd_values[1] if macd_values else None,
        macd_hist=macd_values[2] if macd_values else None,
        atr14=atr14,
        atr_pct=(atr14 / price) if atr14 and price > 0 else None,
        bb_lower=bands[0] if bands else None,
        bb_upper=bands[2] if bands else None,
        volatility=ind.realized_volatility(closes),
        roc10=ind.rate_of_change(closes, 10),
        volume=volumes[-1],
        avg_volume20=ind.sma(volumes, 20),
    )


def score_series(series: Series, min_conviction: float = 0.25) -> SentimentScore:
    """Score one symbol's price history into a bounded sentiment reading."""
    snap = build_snapshot(series)
    components: dict[str, float] = {}
    rationale: list[str] = []

    required = (snap.sma20, snap.sma50, snap.rsi14, snap.macd_hist)
    if any(value is None for value in required):
        return SentimentScore(
            symbol=series.symbol,
            composite=0.0,
            label="NEUTRAL",
            components={},
            snapshot=snap,
            rationale=["Insufficient history to compute the full indicator set."],
            insufficient_data=True,
        )

    # --- Trend: how far the fast average sits above/below the slow average.
    separation = (snap.sma20 - snap.sma50) / snap.sma50 if snap.sma50 else 0.0
    components["trend"] = _bounded(separation / 0.05)
    rationale.append(
        f"Trend: SMA20 {snap.sma20:.2f} vs SMA50 {snap.sma50:.2f} "
        f"({separation:+.2%} separation)."
    )

    # --- Momentum: RSI displacement from the neutral 50 line.
    components["momentum"] = _bounded((snap.rsi14 - 50.0) / 25.0)
    rationale.append(f"Momentum: RSI(14) at {snap.rsi14:.1f}.")

    # --- MACD histogram, normalised by price so it is comparable across names.
    hist_pct = snap.macd_hist / snap.price if snap.price else 0.0
    components["macd"] = _bounded(hist_pct / 0.01)
    rationale.append(
        f"MACD: line {snap.macd_line:.3f} vs signal {snap.macd_signal:.3f} "
        f"(histogram {snap.macd_hist:+.3f})."
    )

    # --- Volume confirmation, signed by the direction of the latest move.
    if snap.avg_volume20 and snap.avg_volume20 > 0:
        relative = (snap.volume - snap.avg_volume20) / snap.avg_volume20
        sign = 1.0 if (snap.roc10 or 0.0) >= 0 else -1.0
        components["volume"] = _bounded(relative) * sign
        rationale.append(
            f"Volume: {snap.volume:,.0f} vs 20-day average "
            f"{snap.avg_volume20:,.0f} ({relative:+.1%})."
        )
    else:
        components["volume"] = 0.0

    composite = sum(WEIGHTS[name] * value for name, value in components.items())

    # --- Volatility dampener: a violent tape reduces conviction, never flips it.
    if snap.atr_pct:
        damp = 1.0 / (1.0 + max(0.0, snap.atr_pct - 0.03) * 20.0)
        if damp < 1.0:
            rationale.append(
                f"Conviction damped x{damp:.2f} on elevated ATR "
                f"({snap.atr_pct:.2%} of price)."
            )
        composite *= damp

    composite = _bounded(composite)
    label = (
        "BULLISH" if composite >= min_conviction
        else "BEARISH" if composite <= -min_conviction
        else "NEUTRAL"
    )
    rationale.append(f"Composite {composite:+.3f} -> {label}.")

    return SentimentScore(
        symbol=series.symbol,
        composite=composite,
        label=label,
        components=components,
        snapshot=snap,
        rationale=rationale,
    )


def _bounded(value: float) -> float:
    """Squash to [-1, 1] smoothly so outliers saturate instead of exploding."""
    return math.tanh(value)


def _round(value):
    return round(value, 6) if isinstance(value, float) else value
