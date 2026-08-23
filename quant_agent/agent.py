"""Agent orchestration: evaluate -> score -> risk-check -> execute -> journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .broker import BrokerAdapter, Fill, build_broker
from .config import AgentConfig
from .data import MarketDataProvider, build_provider
from .journal import JournalEntry, TradeJournal
from .risk import PortfolioState, RiskDecision, RiskManager
from .sentiment import SentimentScore, score_series


@dataclass
class RunReport:
    """Outcome of one full watchlist pass."""

    ts: datetime
    mode: str
    data_source: str
    equity: float
    market_open: bool
    entries: list[JournalEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def fills(self) -> list[Fill]:
        return [e.fill for e in self.entries if e.fill]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.action] = counts.get(entry.action, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "nothing"
        return (
            f"{len(self.entries)} symbols evaluated ({breakdown}) "
            f"on {self.data_source} data in {self.mode} mode."
        )


class TradingAgent:
    """Runs the daily watchlist evaluation end to end."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        provider: MarketDataProvider | None = None,
        broker: BrokerAdapter | None = None,
        journal: TradeJournal | None = None,
    ):
        self.config = config or AgentConfig()
        self.provider = provider or build_provider(
            self.config.provider, self.config.data_dir
        )
        self.broker = broker or build_broker(
            self.config.mode,
            self.config.equity,
            state_path=f"{self.config.data_dir}/paper_account.json",
        )
        self.journal = journal or TradeJournal(self.config.journal_path)
        self.risk = RiskManager(self.config.limits)

    def run(self) -> RunReport:
        """Evaluate every watchlist symbol and route approved orders."""
        now = datetime.now(timezone.utc)
        market_open = self.broker.is_market_open(now)

        report = RunReport(
            ts=now,
            mode=self.config.mode,
            data_source=self.provider.name,
            equity=self.broker.get_equity(),
            market_open=market_open,
        )

        if self.provider.name == "synthetic":
            report.warnings.append(
                "Data source is SYNTHETIC (simulated prices, not a market feed). "
                "Results are illustrative only."
            )
        if not market_open:
            report.warnings.append(
                f"US equity market is closed at {now:%Y-%m-%d %H:%M UTC}; "
                "orders are not routed."
            )
        if self.config.dry_run:
            report.warnings.append("Dry run: risk checks only, no orders submitted.")

        for symbol in self.config.watchlist:
            report.entries.append(self._evaluate_symbol(symbol, market_open))

        return report

    # ----------------------------------------------------------------- private

    def _evaluate_symbol(self, symbol: str, market_open: bool) -> JournalEntry:
        now = datetime.now(timezone.utc)

        try:
            series = self.provider.fetch(symbol, lookback=250)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not halt the run
            return self._error_entry(symbol, f"Data fetch failed: {exc}", now)

        score = score_series(series, self.config.limits.min_conviction)

        portfolio = PortfolioState(
            equity=self.broker.get_equity(),
            open_positions=self.broker.get_positions(),
        )
        decision = self.risk.evaluate(
            score,
            portfolio,
            bar_count=len(series.bars),
            data_age_minutes=series.age_minutes(now),
            is_synthetic=series.is_synthetic,
            live_mode=self.broker.is_live,
        )

        action, fill, notes = self._route(decision, score, market_open)

        entry = JournalEntry(
            ts=now,
            symbol=symbol,
            action=action,
            score=score,
            decision=decision,
            fill=fill,
            data_source=series.source,
            notes=notes,
        )
        self.journal.record(entry)
        return entry

    def _route(
        self, decision: RiskDecision, score: SentimentScore, market_open: bool
    ) -> tuple[str, Fill | None, str]:
        """Decide whether the approved order actually reaches the broker."""
        if not decision.approved:
            action = "hold" if score.direction == "hold" else "rejected"
            return action, None, decision.reason

        if self.config.dry_run:
            return "skipped", None, "Dry run - order approved but not submitted."

        if self.config.require_market_open and not market_open:
            return "skipped", None, "Market closed - approved order not submitted."

        fill = self.broker.submit_order(decision.order)  # type: ignore[arg-type]
        return fill.status, fill, fill.detail

    def _error_entry(self, symbol: str, message: str, now: datetime) -> JournalEntry:
        from .sentiment import IndicatorSnapshot

        empty_snapshot = IndicatorSnapshot(
            price=0.0, sma20=None, sma50=None, rsi14=None, macd_line=None,
            macd_signal=None, macd_hist=None, atr14=None, atr_pct=None,
            bb_lower=None, bb_upper=None, volatility=None, roc10=None,
            volume=0.0, avg_volume20=None,
        )
        score = SentimentScore(
            symbol=symbol,
            composite=0.0,
            label="NEUTRAL",
            components={},
            snapshot=empty_snapshot,
            rationale=[message],
            insufficient_data=True,
        )
        decision = RiskDecision(
            symbol=symbol, approved=False, checks=[], reason=message
        )
        entry = JournalEntry(
            ts=now, symbol=symbol, action="error", score=score,
            decision=decision, fill=None, data_source="unavailable", notes=message,
        )
        self.journal.record(entry)
        return entry
