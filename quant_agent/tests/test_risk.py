"""Risk control tests. The 5% stop ceiling is the headline invariant."""

import unittest

from quant_agent.config import RiskLimits
from quant_agent.risk import PortfolioState, RiskManager
from quant_agent.sentiment import IndicatorSnapshot, SentimentScore


def make_score(
    symbol="TEST",
    composite=0.6,
    label="BULLISH",
    price=100.0,
    atr_pct=0.01,
) -> SentimentScore:
    """A score object with only the fields the risk layer reads."""
    snapshot = IndicatorSnapshot(
        price=price, sma20=price, sma50=price * 0.98, rsi14=60.0,
        macd_line=1.0, macd_signal=0.5, macd_hist=0.5,
        atr14=(atr_pct * price) if atr_pct else None, atr_pct=atr_pct,
        bb_lower=price * 0.95, bb_upper=price * 1.05,
        volatility=0.3, roc10=0.05, volume=1e6, avg_volume20=9e5,
    )
    return SentimentScore(
        symbol=symbol, composite=composite, label=label,
        components={"trend": 0.5}, snapshot=snapshot, rationale=[],
    )


def evaluate(manager, score, portfolio=None, **kwargs):
    defaults = dict(bar_count=250, data_age_minutes=5.0)
    defaults.update(kwargs)
    return manager.evaluate(
        score, portfolio or PortfolioState(equity=100_000.0), **defaults
    )


class TestStopLossCeiling(unittest.TestCase):
    """No approved order may ever carry a stop wider than 5%."""

    def setUp(self):
        self.manager = RiskManager(RiskLimits())

    def test_normal_volatility_uses_two_atr(self):
        decision = evaluate(self.manager, make_score(atr_pct=0.015))
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.order.stop_pct, 0.03)

    def test_extreme_volatility_is_clamped_to_five_percent(self):
        # 2 x ATR would want a 40% stop; the ceiling must win.
        decision = evaluate(self.manager, make_score(atr_pct=0.20))
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.order.stop_pct, 0.05)

    def test_missing_atr_falls_back_to_ceiling(self):
        decision = evaluate(self.manager, make_score(atr_pct=None))
        self.assertAlmostEqual(decision.order.stop_pct, 0.05)

    def test_ceiling_holds_across_a_wide_volatility_sweep(self):
        for atr_pct in (0.001, 0.01, 0.025, 0.05, 0.1, 0.5, 2.0):
            decision = evaluate(self.manager, make_score(atr_pct=atr_pct))
            if decision.order:
                self.assertLessEqual(
                    decision.order.stop_pct,
                    0.05 + 1e-9,
                    f"stop exceeded 5% at atr_pct={atr_pct}",
                )

    def test_stop_price_is_below_entry_for_buys(self):
        decision = evaluate(self.manager, make_score(atr_pct=0.02, price=200.0))
        order = decision.order
        self.assertLess(order.stop_price, order.entry_price)
        self.assertAlmostEqual(order.stop_price, 200.0 * (1 - 0.04))

    def test_stop_price_is_above_entry_for_sells(self):
        score = make_score(composite=-0.6, label="BEARISH", atr_pct=0.02, price=200.0)
        decision = evaluate(self.manager, score)
        self.assertEqual(decision.order.side, "sell")
        self.assertGreater(decision.order.stop_price, decision.order.entry_price)

    def test_degenerate_tight_stop_is_rejected(self):
        manager = RiskManager(RiskLimits(min_stop_loss_pct=0.01))
        decision = evaluate(manager, make_score(atr_pct=0.001))
        self.assertFalse(decision.approved)
        self.assertIn("min_stop_distance", decision.reason)


class TestPositionSizing(unittest.TestCase):
    def setUp(self):
        self.manager = RiskManager(RiskLimits())

    def test_risk_budget_sets_quantity_when_it_is_the_binding_constraint(self):
        # Concentration cap widened so the risk budget is what binds.
        manager = RiskManager(RiskLimits(max_position_pct=0.50))
        decision = evaluate(manager, make_score(atr_pct=0.015, price=100.0))
        # 1% of 100k = $1,000 budget; 3% stop on a $100 share = $3/share.
        self.assertEqual(decision.order.quantity, 333)
        self.assertLessEqual(decision.order.risk_amount, 1_000.0)

    def test_concentration_cap_binds_under_default_limits(self):
        # With a 1% risk budget and a <=5% stop, 20% single-name cap binds first.
        decision = evaluate(self.manager, make_score(atr_pct=0.015, price=100.0))
        self.assertEqual(decision.order.quantity, 200)
        self.assertAlmostEqual(decision.order.notional, 20_000.0)

    def test_risk_amount_never_exceeds_budget_across_a_sweep(self):
        for atr_pct in (0.002, 0.005, 0.01, 0.02, 0.04, 0.1):
            for price in (5.0, 50.0, 250.0, 1_000.0):
                decision = evaluate(
                    self.manager, make_score(atr_pct=atr_pct, price=price)
                )
                if decision.order:
                    self.assertLessEqual(
                        decision.order.risk_amount,
                        1_000.0 + 1e-6,
                        f"risk budget breached at atr={atr_pct} price={price}",
                    )

    def test_concentration_cap_shrinks_position(self):
        # A very tight stop would otherwise buy far more than 20% of equity.
        decision = evaluate(self.manager, make_score(atr_pct=0.003, price=100.0))
        self.assertLessEqual(decision.order.notional, 100_000.0 * 0.20 + 1e-6)

    def test_unaffordable_price_yields_no_quantity(self):
        decision = evaluate(self.manager, make_score(atr_pct=0.05, price=500_000.0))
        self.assertFalse(decision.approved)
        self.assertIn("positive_quantity", decision.reason)


class TestPortfolioLayer(unittest.TestCase):
    def setUp(self):
        self.manager = RiskManager(RiskLimits())

    def test_duplicate_position_blocked(self):
        portfolio = PortfolioState(equity=100_000.0, open_positions={"TEST": 5_000.0})
        decision = evaluate(self.manager, make_score(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("no_duplicate_position", decision.reason)

    def test_max_open_positions_blocked(self):
        portfolio = PortfolioState(
            equity=100_000.0,
            open_positions={s: 1_000.0 for s in ("A", "B", "C", "D", "E")},
        )
        decision = evaluate(self.manager, make_score(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("max_open_positions", decision.reason)

    def test_gross_exposure_blocked(self):
        portfolio = PortfolioState(equity=100_000.0, open_positions={"A": 99_000.0})
        decision = evaluate(self.manager, make_score(atr_pct=0.005), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("gross_exposure", decision.reason)

    def test_daily_loss_kill_switch(self):
        portfolio = PortfolioState(equity=100_000.0, realized_pnl_today=-3_500.0)
        decision = evaluate(self.manager, make_score(), portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("daily_loss_kill_switch", decision.reason)


class TestDataAndConvictionLayers(unittest.TestCase):
    def setUp(self):
        self.manager = RiskManager(RiskLimits())

    def test_neutral_sentiment_is_not_traded(self):
        score = make_score(composite=0.05, label="NEUTRAL")
        decision = evaluate(self.manager, score)
        self.assertFalse(decision.approved)
        self.assertIn("conviction_threshold", decision.reason)

    def test_short_history_blocked(self):
        decision = evaluate(self.manager, make_score(), bar_count=10)
        self.assertFalse(decision.approved)
        self.assertIn("sufficient_history", decision.reason)

    def test_stale_data_blocked(self):
        decision = evaluate(self.manager, make_score(), data_age_minutes=10_000)
        self.assertFalse(decision.approved)
        self.assertIn("data_freshness", decision.reason)

    def test_synthetic_data_blocked_in_live_mode(self):
        decision = evaluate(
            self.manager, make_score(), is_synthetic=True, live_mode=True
        )
        self.assertFalse(decision.approved)
        self.assertIn("live_data_required_for_live_orders", decision.reason)

    def test_synthetic_data_allowed_in_paper_mode(self):
        decision = evaluate(
            self.manager, make_score(), is_synthetic=True, live_mode=False
        )
        self.assertTrue(decision.approved)

    def test_audit_trail_is_recorded_on_rejection(self):
        decision = evaluate(self.manager, make_score(), bar_count=1)
        self.assertTrue(decision.checks)
        self.assertTrue(decision.failures)
        self.assertFalse(decision.as_dict()["approved"])


if __name__ == "__main__":
    unittest.main()
