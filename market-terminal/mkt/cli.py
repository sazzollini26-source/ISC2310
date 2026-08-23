"""Command line entry point.

The TUI is the main event, but every capability is reachable headlessly too —
useful for cron jobs, piping into other tools, and diagnosing key problems.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .chart import fmt_price, fmt_volume, sparkline, summary_line
from .config import PROVIDER_SPECS, Settings, load_dotenv
from .http import HttpClient
from .keypool import KeyRegistry
from .models import to_dict
from .router import NoProviderAvailable, Router


def build_router(args: argparse.Namespace) -> tuple[Router, Settings]:
    load_dotenv()
    settings = Settings.from_env()
    if getattr(args, "symbols", None):
        settings.watchlist = [s.upper() for s in args.symbols]
    keys = KeyRegistry.from_env()
    http = HttpClient(settings)
    return Router(http=http, settings=settings, keys=keys), settings


async def cmd_quote(args, router: Router) -> int:
    results = await router.quotes(args.symbols)
    if args.json:
        payload = {
            s: (to_dict(q) if not isinstance(q, Exception) else {"error": str(q)})
            for s, q in results.items()
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{'SYM':<8}{'LAST':>12}{'CHG':>10}{'CHG%':>9}{'VOLUME':>12}  SOURCE")
    print("-" * 62)
    failures = 0
    for symbol in args.symbols:
        result = results.get(symbol.upper())
        if result is None or isinstance(result, Exception):
            failures += 1
            print(f"{symbol.upper():<8}{'error':>12}   {str(result)[:34]}")
            continue
        change = f"{result.change:+.2f}" if result.change is not None else "--"
        pct = f"{result.change_pct:+.2f}%" if result.change_pct is not None else "--"
        print(f"{result.symbol:<8}{fmt_price(result.price):>12}{change:>10}{pct:>9}"
              f"{fmt_volume(result.volume):>12}  {result.source}")
    return 1 if failures == len(args.symbols) else 0


async def cmd_history(args, router: Router) -> int:
    series = await router.history(args.symbol, args.interval, args.lookback)
    if args.json:
        print(json.dumps({"symbol": series.symbol, "interval": series.interval,
                          "source": series.source,
                          "bars": [to_dict(b) for b in series.bars]}, indent=2))
        return 0
    from .chart import candles
    print(_strip(summary_line(series)))
    for row in candles(series, width=args.width, height=args.height):
        print(_strip(row))
    print(_strip(sparkline(series.closes, args.width - 12)))
    return 0


async def cmd_news(args, router: Router) -> int:
    items = await router.news(args.symbol, limit=args.limit)
    if args.json:
        print(json.dumps([to_dict(i) for i in items], indent=2))
        return 0
    for item in items:
        print(f"[{item.published:%Y-%m-%d %H:%M}] {item.title}")
        print(f"    {item.publisher or item.source} — {item.url}")
    return 0 if items else 1


async def cmd_filings(args, router: Router) -> int:
    filings = await router.filings(args.symbol, tuple(args.forms or ()), args.limit)
    if args.json:
        print(json.dumps([to_dict(f) for f in filings], indent=2))
        return 0
    for filing in filings:
        print(f"{filing.form:<8}{filing.filed:%Y-%m-%d}  {filing.title[:44]}")
        print(f"        {filing.url}")
    return 0 if filings else 1


async def cmd_search(args, router: Router) -> int:
    results = await router.search(args.query, limit=args.limit)
    for row in results:
        print(f"{row['symbol']:<12}{row['name'][:44]:<46}{row['exchange']}")
    return 0 if results else 1


async def cmd_keys(args, router: Router) -> int:
    """Show the pools without ever printing a key in full."""
    print(router.keys.summary())
    print()
    for pool in router.keys.pools.values():
        if not pool.usable:
            continue
        if pool.keyless:
            print(f"{pool.name:<14} keyless")
            continue
        print(f"{pool.name:<14} {pool.available_count()}/{pool.size} ready   "
              f"limit {pool.spec.rate[0]}/{pool.spec.rate[1]:.0f}s"
              + (f", {pool.spec.daily_cap}/day" if pool.spec.daily_cap else ""))
        for key in pool.keys:
            state = "ready" if key.available() else (
                f"cooling {key.cooldown_remaining:.0f}s" if key.cooling else "spent")
            print(f"    {key.masked:<18}{state:<16}"
                  f"used {key.calls_today}"
                  + (f"/{key.daily_cap}" if key.daily_cap else "") +
                  f"  ok={key.successes} 429={key.rate_limit_hits}")
    return 0


async def cmd_doctor(args, router: Router) -> int:
    """Probe every configured source and report what actually works."""
    print(f"{'PROVIDER':<16}{'KEYS':<8}{'RESULT':<32}{'MS':>7}")
    print("-" * 64)
    ok = 0
    for name in sorted(router.providers, key=lambda n: PROVIDER_SPECS[n].priority):
        provider = router.providers[name]
        pool = router.keys.get(name)
        label = "keyless" if provider.spec.keyless else f"{pool.size}"
        import time
        started = time.monotonic()
        try:
            if provider.supports("quote"):
                result = await provider.quote(args.symbol)
            elif provider.supports("news"):
                result = await provider.news(args.symbol, limit=1)
            elif provider.supports("filings"):
                result = await provider.filings(args.symbol, limit=1)
            else:
                result = await provider.history(args.symbol, "1d", 5)
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.monotonic() - started) * 1000
            print(f"{name:<16}{label:<8}{('FAIL ' + str(exc))[:31]:<32}{elapsed:>7.0f}")
        else:
            elapsed = (time.monotonic() - started) * 1000
            # The scrapers swallow per-feed failures and return an empty list,
            # so "answered" is not the same as "returned anything".
            empty = result is None or (isinstance(result, (list, tuple)) and not result)
            if empty:
                print(f"{name:<16}{label:<8}{'EMPTY (reachable, no data)':<32}{elapsed:>7.0f}")
            else:
                ok += 1
                count = len(result) if isinstance(result, (list, tuple)) else 1
                print(f"{name:<16}{label:<8}{f'OK ({count} item(s))':<32}{elapsed:>7.0f}")
    print(f"\n{ok}/{len(router.providers)} sources responded")
    return 0 if ok else 1


async def cmd_status(args, router: Router) -> int:
    await router.quotes(router.settings.watchlist[:3])
    print(json.dumps(router.status(), indent=2, default=str))
    return 0


def cmd_tui(args) -> int:
    load_dotenv()
    settings = Settings.from_env()
    if args.symbols:
        settings.watchlist = [s.upper() for s in args.symbols]
    keys = KeyRegistry.from_env()
    http = HttpClient(settings)
    router = Router(http=http, settings=settings, keys=keys)
    from .ui.app import MarketTerminal
    MarketTerminal(router, settings).run()
    return 0


def _strip(markup: str) -> str:
    """Drop Rich markup for plain stdout.

    Uses Rich's own parser rather than a regex — hand-rolled patterns miss the
    bare ``[/]`` closing tag, which is most of what the chart emits.
    """
    from rich.text import Text
    try:
        return Text.from_markup(markup).plain
    except Exception:  # noqa: BLE001 - malformed markup should still print
        return markup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market-terminal",
        description="A multi-source market data terminal.",
    )
    sub = parser.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="launch the full-screen terminal (default)")
    tui.add_argument("symbols", nargs="*", help="override the watchlist")

    quote = sub.add_parser("quote", help="print quotes for one or more symbols")
    quote.add_argument("symbols", nargs="+")
    quote.add_argument("--json", action="store_true")

    history = sub.add_parser("history", help="print a chart")
    history.add_argument("symbol")
    history.add_argument("--interval", default="1d")
    history.add_argument("--lookback", type=int, default=120)
    history.add_argument("--width", type=int, default=90)
    history.add_argument("--height", type=int, default=18)
    history.add_argument("--json", action="store_true")

    news = sub.add_parser("news", help="merged news feed")
    news.add_argument("symbol", nargs="?")
    news.add_argument("--limit", type=int, default=15)
    news.add_argument("--json", action="store_true")

    filings = sub.add_parser("filings", help="SEC filings via EDGAR")
    filings.add_argument("symbol")
    filings.add_argument("--forms", nargs="*", default=[])
    filings.add_argument("--limit", type=int, default=15)
    filings.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="symbol lookup")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)

    sub.add_parser("keys", help="key-pool health (never prints a full key)")

    doctor = sub.add_parser("doctor", help="probe every source and report")
    doctor.add_argument("--symbol", default="AAPL")

    sub.add_parser("status", help="dump router/provider/key status as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "tui"):
        if args.command is None:
            args.symbols = []
        return cmd_tui(args)

    handlers = {
        "quote": cmd_quote, "history": cmd_history, "news": cmd_news,
        "filings": cmd_filings, "search": cmd_search, "keys": cmd_keys,
        "doctor": cmd_doctor, "status": cmd_status,
    }
    handler = handlers[args.command]

    async def run() -> int:
        router, _ = build_router(args)
        try:
            return await handler(args, router)
        except NoProviderAvailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        finally:
            await router.aclose()

    return asyncio.run(run())
