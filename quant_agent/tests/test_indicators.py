"""Indicator correctness, including the insufficient-data contract."""

import unittest

from quant_agent import indicators as ind


class TestMovingAverages(unittest.TestCase):
    def test_sma_known_value(self):
        self.assertAlmostEqual(ind.sma([1, 2, 3, 4, 5], 5), 3.0)
        self.assertAlmostEqual(ind.sma([1, 2, 3, 4, 5], 2), 4.5)

    def test_ema_tracks_constant_series(self):
        self.assertAlmostEqual(ind.ema([10.0] * 30, 10), 10.0)

    def test_insufficient_data_returns_none(self):
        self.assertIsNone(ind.sma([1, 2], 5))
        self.assertIsNone(ind.ema([1, 2], 5))
        self.assertIsNone(ind.rsi([1, 2], 14))
        self.assertIsNone(ind.macd([1, 2, 3]))
        self.assertIsNone(ind.atr([1], [1], [1]))
        self.assertIsNone(ind.bollinger([1, 2], 20))


class TestRSI(unittest.TestCase):
    def test_monotonic_rise_saturates_high(self):
        values = list(range(1, 40))
        self.assertAlmostEqual(ind.rsi(values, 14), 100.0)

    def test_monotonic_fall_saturates_low(self):
        values = list(range(40, 1, -1))
        self.assertLess(ind.rsi(values, 14), 1.0)

    def test_bounded_range(self):
        values = [100 + (i % 7) - 3 for i in range(60)]
        result = ind.rsi(values, 14)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 100.0)


class TestMACD(unittest.TestCase):
    def test_histogram_is_line_minus_signal(self):
        values = [100 + i * 0.5 for i in range(80)]
        line, signal, hist = ind.macd(values)
        self.assertAlmostEqual(hist, line - signal, places=9)

    def test_uptrend_gives_positive_line(self):
        values = [100 * (1.01 ** i) for i in range(80)]
        line, _, _ = ind.macd(values)
        self.assertGreater(line, 0)


class TestATRAndBands(unittest.TestCase):
    def test_atr_of_constant_range(self):
        highs = [11.0] * 30
        lows = [9.0] * 30
        closes = [10.0] * 30
        self.assertAlmostEqual(ind.atr(highs, lows, closes, 14), 2.0)

    def test_atr_rejects_mismatched_lengths(self):
        self.assertIsNone(ind.atr([1, 2, 3], [1, 2], [1, 2, 3]))

    def test_bollinger_ordering(self):
        values = [100 + (i % 5) for i in range(40)]
        lower, middle, upper = ind.bollinger(values, 20)
        self.assertLess(lower, middle)
        self.assertLess(middle, upper)

    def test_zero_volatility_collapses_bands(self):
        lower, middle, upper = ind.bollinger([5.0] * 30, 20)
        self.assertAlmostEqual(lower, middle)
        self.assertAlmostEqual(upper, middle)


class TestDerived(unittest.TestCase):
    def test_rate_of_change(self):
        values = [100.0] * 10 + [110.0]
        self.assertAlmostEqual(ind.rate_of_change(values, 10), 0.10)

    def test_realized_volatility_is_zero_for_flat_series(self):
        self.assertAlmostEqual(ind.realized_volatility([50.0] * 40, 20), 0.0)

    def test_true_range_uses_gap(self):
        # Gap down: |low - prev_close| dominates the intraday range.
        self.assertAlmostEqual(ind.true_range(high=95, low=94, prev_close=100), 6.0)


if __name__ == "__main__":
    unittest.main()
