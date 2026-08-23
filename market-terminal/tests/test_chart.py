from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mkt.chart import candles, fmt_price, fmt_volume, sparkline, summary_line, _resample
from mkt.models import Bar, Series


def make_series(closes: list[float]) -> Series:
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    bars = [
        Bar(ts=base + timedelta(days=i), open=c - 1, high=c + 1, low=c - 2, close=c)
        for i, c in enumerate(closes)
    ]
    return Series("AAPL", "1d", bars, source="test")


def test_sparkline_maps_low_to_low_block():
    line = sparkline([1, 2, 3, 4, 5, 6, 7, 8], width=8)
    assert len(line) == 8
    assert line[0] == "▁" and line[-1] == "█"


def test_sparkline_handles_flat_and_empty():
    assert sparkline([]) == ""
    flat = sparkline([5, 5, 5, 5])
    assert len(set(flat)) == 1


def test_resample_compresses_to_width():
    assert len(_resample(list(range(100)), 10)) == 10
    assert _resample([1, 2, 3], 10) == [1, 2, 3]


def test_candles_have_expected_geometry():
    series = make_series([10, 12, 11, 15, 14, 18])
    rows = candles(series, width=40, height=8)
    assert len(rows) == 8 + 2, "plot rows plus axis and date line"
    assert "█" in "".join(rows)
    assert "└" in rows[-2]


def test_candles_colour_direction():
    up = make_series([10, 20])          # closes above opens
    rows = "".join(candles(up, width=30, height=6))
    assert "[green]" in rows
    down = Series("X", "1d", [Bar(ts=datetime(2026, 8, 1, tzinfo=timezone.utc),
                                  open=20, high=21, low=9, close=10)])
    assert "[red]" in "".join(candles(down, width=30, height=6))


def test_candles_on_empty_series():
    assert candles(Series("X", "1d", []), 40, 8) == ["[grey62]no data[/]"]


def test_formatters():
    assert fmt_price(1234.5) == "1,234.50"
    assert fmt_price(0.00123) == "0.0012"
    assert fmt_price(None) == "--"
    assert fmt_volume(1_500_000) == "1.50M"
    assert fmt_volume(2_300_000_000) == "2.30B"
    assert fmt_volume(None) == "--"


def test_summary_line_reports_stats():
    line = summary_line(make_series([100, 105, 103, 110]))
    assert "AAPL" in line and "%" in line and "src=test" in line
