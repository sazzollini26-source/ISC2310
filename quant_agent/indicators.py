"""Technical indicators computed on plain Python sequences.

Every function returns ``None`` when there is not enough history rather than
raising or padding, so callers can treat "insufficient data" as a first-class
state instead of trading on a half-formed signal.
"""

from __future__ import annotations

import math
from typing import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` observations."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float | None:
    """Exponential moving average, seeded with an SMA of the first window."""
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = value * k + out * (1.0 - k)
    return out


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    """EMA at every point from index ``period - 1`` onwards."""
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    out = [sum(values[:period]) / period]
    for value in values[period:]:
        out.append(value * k + out[-1] * (1.0 - k))
    return out


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    """Wilder's Relative Strength Index, 0-100."""
    if period <= 0 or len(values) < period + 1:
        return None

    # Seed with the first ``period`` changes.
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = sum(d for d in deltas[:period] if d > 0) / period
    losses = sum(-d for d in deltas[:period] if d < 0) / period

    # Wilder smoothing over the remainder.
    for delta in deltas[period:]:
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        gains = (gains * (period - 1) + gain) / period
        losses = (losses * (period - 1) + loss) / period

    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float] | None:
    """MACD line, signal line, and histogram."""
    if len(values) < slow + signal:
        return None

    fast_series = _ema_series(values, fast)
    slow_series = _ema_series(values, slow)
    # Align: the fast EMA starts earlier, so trim its head.
    offset = len(fast_series) - len(slow_series)
    macd_series = [f - s for f, s in zip(fast_series[offset:], slow_series)]

    signal_series = _ema_series(macd_series, signal)
    if not signal_series:
        return None

    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    return macd_line, signal_line, macd_line - signal_line


def true_range(high: float, low: float, prev_close: float) -> float:
    """Single-bar true range."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Average True Range using Wilder smoothing."""
    n = len(closes)
    if not (len(highs) == len(lows) == n) or n < period + 1:
        return None

    ranges = [true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, n)]
    value = sum(ranges[:period]) / period
    for tr in ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def bollinger(
    values: Sequence[float], period: int = 20, num_std: float = 2.0
) -> tuple[float, float, float] | None:
    """Bollinger bands as ``(lower, middle, upper)``."""
    middle = sma(values, period)
    if middle is None:
        return None
    window = values[-period:]
    variance = sum((v - middle) ** 2 for v in window) / period
    std = math.sqrt(variance)
    return middle - num_std * std, middle, middle + num_std * std


def realized_volatility(values: Sequence[float], period: int = 20) -> float | None:
    """Annualised volatility from daily log returns."""
    if len(values) < period + 1:
        return None
    window = values[-(period + 1) :]
    returns = [
        math.log(window[i] / window[i - 1])
        for i in range(1, len(window))
        if window[i - 1] > 0 and window[i] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def rate_of_change(values: Sequence[float], period: int = 10) -> float | None:
    """Percentage change over ``period`` bars."""
    if len(values) < period + 1 or values[-(period + 1)] == 0:
        return None
    return (values[-1] - values[-(period + 1)]) / values[-(period + 1)]
