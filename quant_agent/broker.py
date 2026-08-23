"""Broker adapters.

``PaperBroker`` is a fully functional simulated account with persistent state.
``LiveBroker`` is deliberately inert: routing real capital requires credentials,
a venue-specific implementation, and a human decision, none of which this agent
manufactures for itself.
"""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time, timezone

from .risk import ProposedOrder


@dataclass(frozen=True)
class Fill:
    """The result of a submitted order."""

    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    stop_price: float
    status: str            # filled | rejected | skipped
    venue: str
    ts: datetime
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": round(self.price, 4),
            "stop_price": round(self.stop_price, 4),
            "status": self.status,
            "venue": self.venue,
            "ts": self.ts.isoformat(),
            "detail": self.detail,
        }


class BrokerAdapter(ABC):
    """Interface the agent routes through. Implementations must be swappable."""

    venue: str = "abstract"
    is_live: bool = False

    @abstractmethod
    def get_equity(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Symbol -> signed notional currently held."""

    @abstractmethod
    def submit_order(self, order: ProposedOrder) -> Fill: ...

    def is_market_open(self, now: datetime | None = None) -> bool:
        """Regular US equity session, 13:30-20:00 UTC on weekdays.

        Does not model market holidays or early closes; a live implementation
        should defer to the venue's own calendar endpoint instead.
        """
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if now.weekday() >= 5:
            return False
        return time(13, 30) <= now.time() <= time(20, 0)


@dataclass
class PaperBroker(BrokerAdapter):
    """Simulated account. Fills at the requested price, no slippage model."""

    venue: str = "paper"
    is_live: bool = False
    starting_equity: float = 100_000.0
    state_path: str | None = None
    positions: dict[str, float] = field(default_factory=dict)
    cash: float = field(init=False)

    def __post_init__(self) -> None:
        self.cash = self.starting_equity
        if self.state_path and os.path.exists(self.state_path):
            self._load()

    def get_equity(self) -> float:
        return self.cash + sum(self.positions.values())

    def get_positions(self) -> dict[str, float]:
        return dict(self.positions)

    def submit_order(self, order: ProposedOrder) -> Fill:
        notional = order.quantity * order.entry_price
        signed = notional if order.side == "buy" else -notional

        self.positions[order.symbol] = self.positions.get(order.symbol, 0.0) + signed
        self.cash -= signed
        self._save()

        return Fill(
            order_id=f"paper-{uuid.uuid4().hex[:12]}",
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.entry_price,
            stop_price=order.stop_price,
            status="filled",
            venue=self.venue,
            ts=datetime.now(timezone.utc),
            detail=(
                f"Simulated fill; protective stop at {order.stop_price:.2f} "
                f"({order.stop_pct:.2%} away)."
            ),
        )

    # ------------------------------------------------------------ persistence

    def _save(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w") as handle:
            json.dump({"cash": self.cash, "positions": self.positions}, handle, indent=2)

    def _load(self) -> None:
        with open(self.state_path) as handle:  # type: ignore[arg-type]
            state = json.load(handle)
        self.cash = state.get("cash", self.starting_equity)
        self.positions = state.get("positions", {})


class LiveBroker(BrokerAdapter):
    """Placeholder for a real venue connection.

    Intentionally not implemented. Wiring this up is a deliberate human act:
    it needs venue credentials, an order-routing implementation, and someone
    accountable for the capital. Constructing it raises rather than silently
    degrading to paper trading, so a misconfigured run fails loudly instead of
    pretending to be live.
    """

    venue = "live"
    is_live = True

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError(
            "Live order routing is not implemented. No broker credentials are "
            "configured for this agent and it will not route real capital. "
            "To go live: implement BrokerAdapter against your venue's API, "
            "supply credentials out of band, and enable it explicitly."
        )

    def get_equity(self) -> float:  # pragma: no cover - unreachable
        raise NotImplementedError

    def get_positions(self) -> dict[str, float]:  # pragma: no cover
        raise NotImplementedError

    def submit_order(self, order: ProposedOrder) -> Fill:  # pragma: no cover
        raise NotImplementedError


def build_broker(mode: str, equity: float, state_path: str | None = None) -> BrokerAdapter:
    """Factory used by the CLI and agent."""
    if mode == "paper":
        return PaperBroker(starting_equity=equity, state_path=state_path)
    if mode == "live":
        return LiveBroker()
    raise ValueError(f"Unknown mode '{mode}'. Choose 'paper' or 'live'.")
