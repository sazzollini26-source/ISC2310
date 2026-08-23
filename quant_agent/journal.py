"""Trade journal.

Append-only JSONL is the system of record; the Markdown renderer is for humans.
Every entry carries the indicator snapshot, the sentiment rationale, and the
full risk audit trail, so any decision can be reconstructed after the fact -
including the decisions *not* to trade.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .broker import Fill
from .risk import RiskDecision
from .sentiment import SentimentScore


@dataclass
class JournalEntry:
    """One symbol's complete decision record for one run."""

    ts: datetime
    symbol: str
    action: str                  # filled | rejected | skipped | hold
    score: SentimentScore
    decision: RiskDecision
    fill: Fill | None
    data_source: str
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "action": self.action,
            "data_source": self.data_source,
            "sentiment": self.score.as_dict(),
            "risk": self.decision.as_dict(),
            "fill": self.fill.as_dict() if self.fill else None,
            "notes": self.notes,
        }


class TradeJournal:
    """Append-only journal writer."""

    def __init__(self, path: str = "journal/trades.jsonl"):
        self.path = path
        self.entries: list[JournalEntry] = []

    def record(self, entry: JournalEntry) -> None:
        self.entries.append(entry)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as handle:
            handle.write(json.dumps(entry.as_dict()) + "\n")

    def render_markdown(self, header: str = "") -> str:
        """Human-readable summary of everything recorded this run."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"# Trade Journal - {stamp}", ""]
        if header:
            lines += [header, ""]

        lines += [
            "| Symbol | Sentiment | Score | Action | Qty | Entry | Stop | Stop % | Risk $ |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|",
        ]
        for entry in self.entries:
            order = entry.decision.order
            lines.append(
                "| {sym} | {label} | {score:+.3f} | {action} | {qty} | {entry} | "
                "{stop} | {stop_pct} | {risk} |".format(
                    sym=entry.symbol,
                    label=entry.score.label,
                    score=entry.score.composite,
                    action=entry.action,
                    qty=order.quantity if order else "-",
                    entry=f"{order.entry_price:.2f}" if order else "-",
                    stop=f"{order.stop_price:.2f}" if order else "-",
                    stop_pct=f"{order.stop_pct:.2%}" if order else "-",
                    risk=f"{order.risk_amount:,.0f}" if order else "-",
                )
            )

        lines.append("")
        lines.append("## Rationale")
        for entry in self.entries:
            lines.append("")
            lines.append(f"### {entry.symbol} - {entry.action.upper()}")
            for reason in entry.score.rationale:
                lines.append(f"- {reason}")
            blocked = entry.decision.failures
            if blocked:
                lines.append("- **Blocked by:**")
                for check in blocked:
                    lines.append(f"  - L{check.layer} `{check.name}` - {check.detail}")
            if entry.fill:
                lines.append(f"- Fill: `{entry.fill.order_id}` {entry.fill.detail}")
            if entry.notes:
                lines.append(f"- Note: {entry.notes}")

        return "\n".join(lines) + "\n"
