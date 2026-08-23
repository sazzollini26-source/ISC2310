"""Command line entry point: ``python -m quant_agent``."""

from __future__ import annotations

import argparse
import sys

from .agent import TradingAgent
from .config import WATCHLIST, AgentConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quant_agent",
        description="Evaluate a watchlist, apply layered risk controls, and "
                    "route approved orders to a broker adapter.",
    )
    parser.add_argument(
        "--symbols", nargs="+", default=list(WATCHLIST),
        help=f"Symbols to evaluate (default: {' '.join(WATCHLIST)}).",
    )
    parser.add_argument(
        "--provider", choices=["synthetic", "csv", "yfinance"], default="synthetic",
        help="Market data source. 'synthetic' is simulated, not real prices.",
    )
    parser.add_argument(
        "--mode", choices=["paper", "live"], default="paper",
        help="Execution venue. 'live' is not implemented and will refuse to run.",
    )
    parser.add_argument("--equity", type=float, default=100_000.0,
                        help="Account equity used for position sizing.")
    parser.add_argument("--data-dir", default="data", help="Directory for CSV data and paper state.")
    parser.add_argument("--journal", default="journal/trades.jsonl", help="JSONL journal path.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score and risk-check only; never submit orders.")
    parser.add_argument("--ignore-market-hours", action="store_true",
                        help="Route paper orders even when the market is closed.")
    parser.add_argument("--markdown", metavar="PATH",
                        help="Also write a Markdown summary of the run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = AgentConfig(
        watchlist=tuple(s.upper() for s in args.symbols),
        equity=args.equity,
        provider=args.provider,
        data_dir=args.data_dir,
        journal_path=args.journal,
        mode=args.mode,
        dry_run=args.dry_run,
        require_market_open=not args.ignore_market_hours,
    )

    try:
        agent = TradingAgent(config)
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = agent.run()

    for warning in report.warnings:
        print(f"WARNING: {warning}")
    print()
    print(agent.journal.render_markdown())
    print(report.summary())

    if args.markdown:
        with open(args.markdown, "w") as handle:
            handle.write(agent.journal.render_markdown())
        print(f"Markdown summary written to {args.markdown}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
