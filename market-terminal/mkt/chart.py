"""Terminal chart rendering.

Pure string/markup functions — no Textual imports — so the drawing logic is
unit-testable without spinning up an app.
"""
from __future__ import annotations

from .models import Series

BLOCKS = "▁▂▃▄▅▆▇█"
UP = "green"
DOWN = "red"
FLAT = "grey62"


def sparkline(values: list[float], width: int = 40) -> str:
    """Compress a series into one row of block characters."""
    if not values:
        return ""
    points = _resample(values, width)
    low, high = min(points), max(points)
    span = high - low
    if span <= 0:
        return BLOCKS[len(BLOCKS) // 2] * len(points)
    return "".join(
        BLOCKS[min(len(BLOCKS) - 1, int((v - low) / span * (len(BLOCKS) - 1) + 0.5))]
        for v in points
    )


def _resample(values: list[float], width: int) -> list[float]:
    """Bucket-average down to ``width`` points (or pass through if shorter)."""
    if width <= 0 or len(values) <= width:
        return list(values)
    bucket = len(values) / width
    out: list[float] = []
    for i in range(width):
        start = int(i * bucket)
        end = max(start + 1, int((i + 1) * bucket))
        chunk = values[start:end]
        out.append(sum(chunk) / len(chunk))
    return out


def fmt_price(value: float | None) -> str:
    if value is None:
        return "--"
    magnitude = abs(value)
    if magnitude >= 10000:
        return f"{value:,.0f}"
    if magnitude >= 100:
        return f"{value:,.2f}"
    if magnitude >= 1:
        return f"{value:,.2f}"
    return f"{value:,.4f}"


def fmt_volume(value: float | None) -> str:
    if value is None:
        return "--"
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"{value / limit:.2f}{suffix}"
    return f"{value:,.0f}"


def candles(series: Series, width: int = 80, height: int = 18) -> list[str]:
    """Render OHLC candles as Rich markup rows, with a price axis.

    Each column is one bar: ``█`` spans open-to-close, ``│`` the high-low wick.
    """
    if not series.bars:
        return ["[grey62]no data[/]"]

    axis_width = 10
    plot_width = max(10, width - axis_width)
    bars = series.bars[-plot_width:]

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    top, bottom = max(highs), min(lows)
    if top == bottom:
        top, bottom = top + 0.5, bottom - 0.5
    span = top - bottom
    height = max(4, height)

    def row_price(row: int) -> tuple[float, float]:
        """Price band covered by a given row (row 0 is the top)."""
        upper = top - (row / height) * span
        lower = top - ((row + 1) / height) * span
        return lower, upper

    rows: list[str] = []
    for row in range(height):
        lower, upper = row_price(row)
        cells: list[str] = []
        for bar in bars:
            rising = bar.close >= bar.open
            colour = UP if rising else DOWN
            body_top = max(bar.open, bar.close)
            body_bottom = min(bar.open, bar.close)
            if body_bottom <= upper and body_top >= lower:
                cells.append(f"[{colour}]█[/]")
            elif bar.low <= upper and bar.high >= lower:
                cells.append(f"[{colour}]│[/]")
            else:
                cells.append(" ")
        label = f"{fmt_price(upper):>{axis_width - 2}} "
        rows.append(f"[grey50]{label}[/]│{''.join(cells)}")

    # x-axis: first, middle and last dates under the plot
    rows.append(f"[grey50]{'':>{axis_width - 1}}[/]└{'─' * len(bars)}")
    if len(bars) >= 2:
        left = bars[0].ts.strftime("%d %b")
        right = bars[-1].ts.strftime("%d %b")
        middle = bars[len(bars) // 2].ts.strftime("%d %b")
        gap = max(1, len(bars) - len(left) - len(right) - len(middle))
        line = f"{left}{' ' * (gap // 2)}{middle}{' ' * (gap - gap // 2)}{right}"
        rows.append(f"[grey50]{'':>{axis_width}}{line[:len(bars)]}[/]")
    return rows


def summary_line(series: Series) -> str:
    """One-line stat strip under a chart."""
    change = series.pct_change()
    vol = series.realised_vol()
    drawdown = series.max_drawdown()
    colour = UP if (change or 0) >= 0 else DOWN
    parts = [
        f"[bold]{series.symbol}[/] [grey62]{series.interval}[/]",
        f"[{colour}]{change:+.2f}%[/]" if change is not None else "[grey62]--[/]",
        f"[grey62]vol[/] {vol:.1f}%" if vol is not None else "",
        f"[grey62]mdd[/] {drawdown:.1f}%" if drawdown is not None else "",
        f"[grey62]n={len(series)}  src={series.source}[/]",
    ]
    return "  ".join(p for p in parts if p)
