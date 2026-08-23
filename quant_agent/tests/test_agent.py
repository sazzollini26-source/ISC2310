"""Broker, journal, and end-to-end agent behaviour."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from quant_agent.agent import TradingAgent
from quant_agent.broker import LiveBroker, PaperBroker, build_broker
from quant_agent.config import AgentConfig, RiskLimits
from quant_agent.data import SyntheticProvider
from quant_agent.journal import TradeJournal
from quant_agent.risk import ProposedOrder


def make_order(symbol="TEST", side="buy", qty=10, price=100.0):
    return ProposedOrder(
        symbol=symbol, side=side, quantity=qty, entry_price=price,
        stop_price=price * 0.95, stop_pct=0.05,
        notional=qty * price, risk_amount=qty * price * 0.05,
    )


class TestLiveBrokerGuard(unittest.TestCase):
    """Live routing must fail loudly rather than degrade to paper."""

    def test_constructing_live_broker_raises(self):
        with self.assertRaises(NotImplementedError):
            LiveBroker()

    def test_build_broker_live_raises(self):
        with self.assertRaises(NotImplementedError):
            build_broker("live", 100_000.0)

    def test_error_message_explains_why(self):
        with self.assertRaises(NotImplementedError) as ctx:
            LiveBroker()
        self.assertIn("will not route real capital", str(ctx.exception))

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_broker("wildcat", 100_000.0)


class TestPaperBroker(unittest.TestCase):
    def test_buy_updates_position_and_cash(self):
        broker = PaperBroker(starting_equity=100_000.0)
        fill = broker.submit_order(make_order(qty=10, price=100.0))
        self.assertEqual(fill.status, "filled")
        self.assertEqual(broker.get_positions()["TEST"], 1_000.0)
        self.assertAlmostEqual(broker.cash, 99_000.0)
        self.assertAlmostEqual(broker.get_equity(), 100_000.0)

    def test_sell_records_negative_notional(self):
        broker = PaperBroker(starting_equity=100_000.0)
        broker.submit_order(make_order(side="sell", qty=5, price=200.0))
        self.assertEqual(broker.get_positions()["TEST"], -1_000.0)

    def test_state_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "acct.json")
            first = PaperBroker(starting_equity=50_000.0, state_path=path)
            first.submit_order(make_order(qty=2, price=100.0))
            second = PaperBroker(starting_equity=50_000.0, state_path=path)
            self.assertEqual(second.get_positions()["TEST"], 200.0)
            self.assertAlmostEqual(second.cash, 49_800.0)

    def test_market_hours(self):
        broker = PaperBroker()
        # Sunday - closed.
        self.assertFalse(
            broker.is_market_open(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
        )
        # Monday 15:00 UTC - open.
        self.assertTrue(
            broker.is_market_open(datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc))
        )
        # Monday 02:00 UTC - closed.
        self.assertFalse(
            broker.is_market_open(datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc))
        )


class TestAgentRun(unittest.TestCase):
    def _config(self, tmp, **kwargs):
        base = dict(
            watchlist=("TSLA", "NVDA", "AMD", "META", "AAPL"),
            equity=100_000.0,
            provider="synthetic",
            data_dir=tmp,
            journal_path=os.path.join(tmp, "trades.jsonl"),
            mode="paper",
            require_market_open=False,
        )
        base.update(kwargs)
        return AgentConfig(**base)

    def test_full_watchlist_is_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = TradingAgent(self._config(tmp)).run()
            self.assertEqual(len(report.entries), 5)
            self.assertEqual(
                [e.symbol for e in report.entries],
                ["TSLA", "NVDA", "AMD", "META", "AAPL"],
            )

    def test_synthetic_data_raises_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = TradingAgent(self._config(tmp)).run()
            self.assertTrue(any("SYNTHETIC" in w for w in report.warnings))

    def test_every_fill_respects_the_five_percent_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = TradingAgent(self._config(tmp)).run()
            for entry in report.entries:
                if entry.decision.order:
                    self.assertLessEqual(entry.decision.order.stop_pct, 0.05 + 1e-9)

    def test_dry_run_submits_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = TradingAgent(self._config(tmp, dry_run=True))
            report = agent.run()
            self.assertEqual(report.fills, [])
            self.assertEqual(agent.broker.get_positions(), {})

    def test_market_closed_blocks_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = TradingAgent(self._config(tmp, require_market_open=True))
            # Force the closed-market path regardless of when tests run.
            agent.broker.is_market_open = lambda now=None: False
            report = agent.run()
            self.assertEqual(report.fills, [])
            self.assertTrue(
                all(e.action != "filled" for e in report.entries)
            )

    def test_journal_is_written_for_every_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            TradingAgent(config).run()
            with open(config.journal_path) as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(len(rows), 5)
            for row in rows:
                self.assertIn("sentiment", row)
                self.assertIn("risk", row)
                self.assertIn("checks", row["risk"])
                self.assertTrue(row["sentiment"]["rationale"])

    def test_data_errors_do_not_halt_the_run(self):
        class BrokenProvider(SyntheticProvider):
            name = "synthetic"

            def fetch(self, symbol, lookback=250):
                if symbol == "AMD":
                    raise RuntimeError("feed timeout")
                return super().fetch(symbol, lookback)

        with tempfile.TemporaryDirectory() as tmp:
            agent = TradingAgent(self._config(tmp), provider=BrokenProvider())
            report = agent.run()
            self.assertEqual(len(report.entries), 5)
            amd = next(e for e in report.entries if e.symbol == "AMD")
            self.assertEqual(amd.action, "error")
            self.assertIn("feed timeout", amd.notes)

    def test_positions_are_capped_by_open_position_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            config.limits = RiskLimits(max_open_positions=1, min_conviction=0.0)
            agent = TradingAgent(config)
            agent.run()
            self.assertLessEqual(len(agent.broker.get_positions()), 1)


class TestJournalRendering(unittest.TestCase):
    def test_markdown_includes_symbols_and_rationale(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider="synthetic", data_dir=tmp,
                journal_path=os.path.join(tmp, "j.jsonl"),
                require_market_open=False, dry_run=True,
            )
            agent = TradingAgent(config)
            agent.run()
            markdown = agent.journal.render_markdown(header="test run")
            self.assertIn("Trade Journal", markdown)
            self.assertIn("Rationale", markdown)
            for symbol in ("TSLA", "NVDA", "AMD", "META", "AAPL"):
                self.assertIn(symbol, markdown)

    def test_empty_journal_still_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TradeJournal(os.path.join(tmp, "empty.jsonl"))
            self.assertIn("Trade Journal", journal.render_markdown())


if __name__ == "__main__":
    unittest.main()
