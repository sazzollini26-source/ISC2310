"""Configuration: watchlist, risk limits, and runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict

# The daily watchlist this agent is responsible for.
WATCHLIST: tuple[str, ...] = ("TSLA", "NVDA", "AMD", "META", "AAPL")


@dataclass(frozen=True)
class RiskLimits:
    """Hard limits applied to every proposed order.

    The stop-loss ceiling is the headline control: no order may ever be sent
    with a stop further than ``max_stop_loss_pct`` away from the entry price.
    """

    # Layer 2 - per-trade loss containment.
    max_stop_loss_pct: float = 0.05      # 5% maximum stop distance. Hard ceiling.
    min_stop_loss_pct: float = 0.005     # Reject degenerate stops sitting on top of price.

    # Layer 3 - position sizing.
    risk_per_trade_pct: float = 0.01     # Capital at risk if the stop is hit.
    max_position_pct: float = 0.20       # Max notional in any single symbol.

    # Layer 4 - portfolio exposure.
    max_open_positions: int = 5
    max_gross_exposure_pct: float = 1.00  # No leverage.
    max_daily_loss_pct: float = 0.03      # Kill switch for the session.

    # Layer 5 - execution sanity.
    min_order_notional: float = 100.0
    max_order_notional: float = 50_000.0
    min_conviction: float = 0.25          # |sentiment| below this is a HOLD.

    # Data quality.
    min_bars: int = 60                    # Enough history for SMA50 + ATR14.
    max_data_age_minutes: int = 24 * 60   # Reject stale quotes.

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentConfig:
    """Top-level runtime configuration."""

    watchlist: tuple[str, ...] = WATCHLIST
    limits: RiskLimits = field(default_factory=RiskLimits)

    equity: float = 100_000.0
    provider: str = "synthetic"          # synthetic | csv | yfinance
    data_dir: str = "data"
    journal_path: str = "journal/trades.jsonl"

    # Execution posture. ``paper`` is the only mode with an implementation.
    mode: str = "paper"                  # paper | live
    dry_run: bool = False                # Score and risk-check, never submit.
    require_market_open: bool = True

    @classmethod
    def from_env(cls, **overrides) -> "AgentConfig":
        """Build config from environment variables, then apply overrides."""
        cfg = cls(
            equity=float(os.environ.get("QA_EQUITY", cls.equity)),
            provider=os.environ.get("QA_PROVIDER", cls.provider),
            data_dir=os.environ.get("QA_DATA_DIR", cls.data_dir),
            journal_path=os.environ.get("QA_JOURNAL", cls.journal_path),
            mode=os.environ.get("QA_MODE", cls.mode),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        return cfg
