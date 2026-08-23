"""Multi-layer pre-trade risk controls.

Every proposed order passes through an ordered gauntlet of checks. A single
failure blocks the order; the full check list is returned either way so the
journal records *why* something traded or did not.

Layer 1  Data quality     - enough clean, fresh history to justify a decision
Layer 2  Stop-loss        - hard 5% ceiling on stop distance (never relaxed)
Layer 3  Position sizing  - risk-per-trade budget and single-name concentration
Layer 4  Portfolio        - open positions, gross exposure, daily loss kill switch
Layer 5  Execution sanity - notional bounds, duplicate positions, live-mode guard
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import RiskLimits
from .sentiment import SentimentScore


@dataclass(frozen=True)
class RiskCheck:
    """The outcome of one named control."""

    layer: int
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {
            "layer": self.layer,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class ProposedOrder:
    """A sized, stop-protected order awaiting approval."""

    symbol: str
    side: str                # buy | sell
    quantity: int
    entry_price: float
    stop_price: float
    stop_pct: float
    notional: float
    risk_amount: float

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 4),
            "stop_price": round(self.stop_price, 4),
            "stop_pct": round(self.stop_pct, 5),
            "notional": round(self.notional, 2),
            "risk_amount": round(self.risk_amount, 2),
        }


@dataclass
class RiskDecision:
    """Aggregate verdict for one symbol."""

    symbol: str
    approved: bool
    checks: list[RiskCheck] = field(default_factory=list)
    order: ProposedOrder | None = None
    reason: str = ""

    @property
    def failures(self) -> list[RiskCheck]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "approved": self.approved,
            "reason": self.reason,
            "checks": [c.as_dict() for c in self.checks],
            "order": self.order.as_dict() if self.order else None,
        }


@dataclass
class PortfolioState:
    """Everything the portfolio-level checks need to know."""

    equity: float
    open_positions: dict[str, float] = field(default_factory=dict)  # symbol -> notional
    realized_pnl_today: float = 0.0

    @property
    def gross_exposure(self) -> float:
        return sum(abs(v) for v in self.open_positions.values())


class RiskManager:
    """Applies the layered controls to a scored symbol."""

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    # ------------------------------------------------------------------ public

    def evaluate(
        self,
        score: SentimentScore,
        portfolio: PortfolioState,
        *,
        bar_count: int,
        data_age_minutes: float,
        is_synthetic: bool = False,
        live_mode: bool = False,
    ) -> RiskDecision:
        """Run every layer and return the decision plus the full audit trail."""
        checks: list[RiskCheck] = []
        limits = self.limits

        # ---------------------------------------------------- Layer 1: data
        checks.append(
            RiskCheck(
                1,
                "sufficient_history",
                bar_count >= limits.min_bars and not score.insufficient_data,
                f"{bar_count} bars available, {limits.min_bars} required.",
            )
        )
        checks.append(
            RiskCheck(
                1,
                "data_freshness",
                data_age_minutes <= limits.max_data_age_minutes,
                f"Last bar is {data_age_minutes:.0f} min old, "
                f"limit {limits.max_data_age_minutes} min.",
            )
        )
        checks.append(
            RiskCheck(
                1,
                "price_sanity",
                score.snapshot.price > 0,
                f"Last price {score.snapshot.price:.2f}.",
            )
        )
        checks.append(
            RiskCheck(
                1,
                "live_data_required_for_live_orders",
                not (live_mode and is_synthetic),
                "Synthetic data may never back a live order."
                if is_synthetic
                else "Data source is a real feed.",
            )
        )

        # ------------------------------------------------ Layer 2: conviction
        conviction = abs(score.composite)
        directional = score.direction in ("buy", "sell")
        checks.append(
            RiskCheck(
                2,
                "conviction_threshold",
                directional and conviction >= limits.min_conviction,
                f"|composite| {conviction:.3f} vs threshold "
                f"{limits.min_conviction:.2f} ({score.label}).",
            )
        )

        if not _all_passed(checks):
            return self._reject(score.symbol, checks)

        # ------------------------------------------------- Layer 2: stop-loss
        entry = score.snapshot.price
        stop_pct = self._stop_distance_pct(score)
        stop_price = (
            entry * (1 - stop_pct) if score.direction == "buy" else entry * (1 + stop_pct)
        )

        checks.append(
            RiskCheck(
                2,
                "max_stop_loss_5pct",
                stop_pct <= limits.max_stop_loss_pct + 1e-9,
                f"Stop distance {stop_pct:.2%} vs hard ceiling "
                f"{limits.max_stop_loss_pct:.2%}.",
            )
        )
        checks.append(
            RiskCheck(
                2,
                "min_stop_distance",
                stop_pct >= limits.min_stop_loss_pct,
                f"Stop distance {stop_pct:.2%} vs floor "
                f"{limits.min_stop_loss_pct:.2%}.",
            )
        )

        if not _all_passed(checks):
            return self._reject(score.symbol, checks)

        # ---------------------------------------------------- Layer 3: sizing
        risk_budget = portfolio.equity * limits.risk_per_trade_pct
        per_share_risk = entry * stop_pct
        quantity = int(risk_budget // per_share_risk) if per_share_risk > 0 else 0

        # Concentration cap can only ever shrink the position.
        max_shares_by_position = int(
            (portfolio.equity * limits.max_position_pct) // entry
        )
        quantity = min(quantity, max_shares_by_position)

        notional = quantity * entry
        risk_amount = quantity * per_share_risk

        checks.append(
            RiskCheck(
                3,
                "positive_quantity",
                quantity > 0,
                f"Sized {quantity} shares from a "
                f"{limits.risk_per_trade_pct:.1%} (${risk_budget:,.0f}) risk budget.",
            )
        )
        checks.append(
            RiskCheck(
                3,
                "risk_per_trade",
                risk_amount <= risk_budget + 1e-6,
                f"Capital at risk ${risk_amount:,.2f} vs budget ${risk_budget:,.2f}.",
            )
        )
        checks.append(
            RiskCheck(
                3,
                "single_name_concentration",
                notional <= portfolio.equity * limits.max_position_pct + 1e-6,
                f"Position ${notional:,.0f} is "
                f"{_pct(notional, portfolio.equity)} of equity, cap "
                f"{limits.max_position_pct:.0%}.",
            )
        )

        if not _all_passed(checks):
            return self._reject(score.symbol, checks)

        # ------------------------------------------------- Layer 4: portfolio
        already_open = score.symbol in portfolio.open_positions
        projected_positions = len(portfolio.open_positions) + (0 if already_open else 1)
        projected_gross = portfolio.gross_exposure + notional
        daily_loss_limit = -abs(portfolio.equity * limits.max_daily_loss_pct)

        checks.append(
            RiskCheck(
                4,
                "max_open_positions",
                projected_positions <= limits.max_open_positions,
                f"{projected_positions} positions after fill, cap "
                f"{limits.max_open_positions}.",
            )
        )
        checks.append(
            RiskCheck(
                4,
                "gross_exposure",
                projected_gross <= portfolio.equity * limits.max_gross_exposure_pct + 1e-6,
                f"Gross exposure {_pct(projected_gross, portfolio.equity)} after fill, "
                f"cap {limits.max_gross_exposure_pct:.0%}.",
            )
        )
        checks.append(
            RiskCheck(
                4,
                "daily_loss_kill_switch",
                portfolio.realized_pnl_today > daily_loss_limit,
                f"Realised P&L today ${portfolio.realized_pnl_today:,.2f} vs "
                f"limit ${daily_loss_limit:,.2f}.",
            )
        )
        checks.append(
            RiskCheck(
                4,
                "no_duplicate_position",
                not already_open,
                f"{score.symbol} already held."
                if already_open
                else f"No existing position in {score.symbol}.",
            )
        )

        # ------------------------------------------------- Layer 5: execution
        checks.append(
            RiskCheck(
                5,
                "min_order_notional",
                notional >= limits.min_order_notional,
                f"Order ${notional:,.2f} vs minimum "
                f"${limits.min_order_notional:,.2f}.",
            )
        )
        checks.append(
            RiskCheck(
                5,
                "max_order_notional",
                notional <= limits.max_order_notional,
                f"Order ${notional:,.2f} vs maximum "
                f"${limits.max_order_notional:,.2f}.",
            )
        )

        if not _all_passed(checks):
            return self._reject(score.symbol, checks)

        order = ProposedOrder(
            symbol=score.symbol,
            side=score.direction,
            quantity=quantity,
            entry_price=entry,
            stop_price=stop_price,
            stop_pct=stop_pct,
            notional=notional,
            risk_amount=risk_amount,
        )
        return RiskDecision(
            symbol=score.symbol,
            approved=True,
            checks=checks,
            order=order,
            reason="All risk layers passed.",
        )

    # ----------------------------------------------------------------- private

    def _stop_distance_pct(self, score: SentimentScore) -> float:
        """Volatility-aware stop, hard-clamped to the 5% ceiling.

        A 2x ATR stop adapts to each name's tape, but the clamp is absolute:
        if volatility argues for a wider stop, the stop does not widen - the
        position is sized against 5% instead, and a genuinely too-tight stop
        is rejected upstream rather than silently nudged.
        """
        limits = self.limits
        atr_pct = score.snapshot.atr_pct
        if atr_pct and atr_pct > 0:
            return min(2.0 * atr_pct, limits.max_stop_loss_pct)
        return limits.max_stop_loss_pct

    def _reject(self, symbol: str, checks: list[RiskCheck]) -> RiskDecision:
        failed = [c for c in checks if not c.passed]
        reason = "; ".join(f"L{c.layer} {c.name}: {c.detail}" for c in failed)
        return RiskDecision(symbol=symbol, approved=False, checks=checks, reason=reason)


def _all_passed(checks: list[RiskCheck]) -> bool:
    return all(c.passed for c in checks)


def _pct(part: float, whole: float) -> str:
    return f"{(part / whole):.1%}" if whole else "n/a"
