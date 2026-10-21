"""The terminal itself."""
from __future__ import annotations

from datetime import datetime, timezone

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Header, Input, Static

from ..chart import candles, fmt_price, fmt_volume, summary_line
from ..config import Settings
from ..models import Quote
from ..router import NoProviderAvailable, Router

HELP = """[bold]Commands[/]
  [cyan]AAPL[/]              load a security into the chart and news panes
  [cyan]/add TSLA[/]         add to the watchlist      [cyan]/rm TSLA[/]  remove
  [cyan]/chart 1d[/]         interval: 5m 15m 1h 1d 1wk 1mo
  [cyan]/news[/]             refresh the news feed for the active symbol
  [cyan]/filings[/]          recent SEC filings for the active symbol
  [cyan]/search apple[/]     symbol lookup across providers
  [cyan]/keys[/]             key-pool health          [cyan]/status[/]  provider health
  [cyan]/help[/]             this screen              [cyan]/quit[/]    exit
[grey62]Keys: r refresh · k keys · s status · / command · q quit[/]"""


class StatusBar(Static):
    """Bottom strip: key pools, provider health, cache."""

    def update_from(self, router: Router) -> None:
        pools = [p for p in router.keys.pools.values() if p.size]
        total = sum(p.size for p in pools)
        ready = sum(p.available_count() for p in pools)
        healthy = sum(
            1 for name in router.providers if not router.health[name].penalised
        )
        cache = router.http.stats()["cache"]
        key_colour = "green" if ready == total else ("yellow" if ready else "red")
        self.update(
            f"[{key_colour}]keys {ready}/{total} ready[/] [grey50]│[/] "
            f"[green]{healthy}/{len(router.providers)} sources up[/] [grey50]│[/] "
            f"[grey62]cache {cache['hit_rate']}% of {cache['hits'] + cache['misses']}[/] "
            f"[grey50]│[/] [grey62]{router.http.request_count} requests[/] [grey50]│[/] "
            f"[grey62]{datetime.now(timezone.utc):%H:%M:%S} UTC[/]"
            # Key hints live here now; a docked Footer fights this row for it.
            f"[grey50]   r[/][grey62]efresh[/] [grey50]k[/][grey62]eys[/] "
            f"[grey50]s[/][grey62]tatus[/] [grey50]?[/][grey62] help[/] "
            f"[grey50]q[/][grey62]uit[/]"
        )


class MarketTerminal(App):
    CSS = """
    Screen { layout: vertical; background: $surface; }
    #top { height: 1fr; }
    #watchlist-pane { width: 50%; border: round $primary; }
    #chart-pane { width: 1fr; border: round $primary; }
    #bottom { height: 40%; }
    #news-pane { width: 55%; border: round $primary; }
    #detail-pane { width: 1fr; border: round $primary; }
    DataTable { height: 1fr; }
    #chart { height: 1fr; padding: 0 1; }
    #chart-summary { height: 1; padding: 0 1; }
    #news { height: 1fr; padding: 0 1; }
    #detail { height: 1fr; padding: 0 1; }
    #chrome { dock: bottom; height: 2; background: $panel; }
    #command { height: 1; border: none; padding: 0 1; }
    StatusBar { height: 1; padding: 0 1; background: $panel; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("k", "show_keys", "Keys"),
        Binding("s", "show_status", "Status"),
        Binding("slash", "focus_command", "Command"),
        Binding("question_mark", "show_help", "Help"),
    ]

    def __init__(self, router: Router, settings: Settings) -> None:
        super().__init__()
        self.router = router
        self.settings = settings
        self.watchlist: list[str] = list(settings.watchlist)
        self.active: str = self.watchlist[0] if self.watchlist else "AAPL"
        self.interval: str = "1d"
        self.quotes: dict[str, Quote] = {}

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            with Vertical(id="watchlist-pane"):
                yield Static("[bold] WATCHLIST[/]")
                yield DataTable(id="watchlist", cursor_type="row", zebra_stripes=True)
            with Vertical(id="chart-pane"):
                yield Static("[bold] CHART[/]", id="chart-title")
                yield Static("", id="chart")
                yield Static("", id="chart-summary")
        with Horizontal(id="bottom"):
            with Vertical(id="news-pane"):
                yield Static("[bold] NEWS[/]", id="news-title")
                yield Static("", id="news")
            with Vertical(id="detail-pane"):
                yield Static("[bold] SECURITY[/]", id="detail-title")
                yield Static("", id="detail")
        # Input and status share one docked container; docking them
        # separately puts the status bar under the Footer, which then paints
        # over it.
        with Vertical(id="chrome"):
            yield Input(placeholder="Enter a ticker or /command  (? for help)", id="command")
            yield StatusBar(id="status")

    def on_mount(self) -> None:
        table = self.query_one("#watchlist", DataTable)
        # Explicit column keys so update_cell() can address cells by name.
        # Deliberately narrow: day range and venue live in the SECURITY pane,
        # so the watchlist stays a scanning surface that fits its column.
        for label, width in (("SYM", 5), ("LAST", 9), ("CHG", 7),
                             ("CHG%", 8), ("VOL", 7), ("SRC", 8)):
            table.add_column(label, key=label, width=width)
        for symbol in self.watchlist:
            table.add_row(symbol, "…", "", "", "", "", key=symbol)
        self.query_one("#detail", Static).update(HELP)
        self.set_interval(self.settings.quote_refresh, self.action_refresh)
        self.set_interval(1.0, self._tick_status)
        self._tick_status()
        self.action_refresh()
        self.load_security(self.active)

    def _tick_status(self) -> None:
        self.query_one(StatusBar).update_from(self.router)

    # -- data workers ------------------------------------------------------

    @work(exclusive=True, group="quotes")
    async def action_refresh(self) -> None:
        if not self.watchlist:
            return
        results = await self.router.quotes(self.watchlist)
        table = self.query_one("#watchlist", DataTable)
        for symbol, result in results.items():
            if isinstance(result, Exception):
                self._set_row(table, symbol, error=str(result))
                continue
            self.quotes[symbol] = result
            self._set_row(table, symbol, quote=result)
        self._tick_status()

    def _set_row(
        self, table: DataTable, symbol: str,
        quote: Quote | None = None, error: str = "",
    ) -> None:
        if symbol not in [str(k.value) for k in table.rows]:
            table.add_row(symbol, "…", "", "", "", "", key=symbol)
        if quote is None:
            table.update_cell(symbol, "LAST", "[red]err[/]")
            table.update_cell(symbol, "SRC", f"[red]{error[:12]}[/]")
            return
        colour = "green" if (quote.change or 0) >= 0 else "red"
        stale = quote.is_stale(self.settings.stale_after)
        price = fmt_price(quote.price)
        table.update_cell(symbol, "LAST",
                          f"[yellow]{price}[/]" if stale else f"[bold]{price}[/]")
        table.update_cell(symbol, "CHG",
                          f"[{colour}]{quote.change:+.2f}[/]" if quote.change is not None else "--")
        table.update_cell(symbol, "CHG%",
                          f"[{colour}]{quote.change_pct:+.2f}%[/]"
                          if quote.change_pct is not None else "--")
        table.update_cell(symbol, "VOL", fmt_volume(quote.volume))
        table.update_cell(symbol, "SRC", f"[grey62]{quote.source}[/]")

    @work(exclusive=True, group="chart")
    async def load_chart(self, symbol: str) -> None:
        chart = self.query_one("#chart", Static)
        self.query_one("#chart-title", Static).update(
            f"[bold] CHART[/] [cyan]{symbol}[/] [grey62]{self.interval}[/]")
        chart.update("[grey62]loading…[/]")
        try:
            series = await self.router.history(symbol, self.interval, 200)
        except NoProviderAvailable as exc:
            chart.update(f"[red]{exc}[/]")
            self.query_one("#chart-summary", Static).update("")
            return
        size = self.query_one("#chart-pane").size
        rows = candles(series, width=max(30, size.width - 4), height=max(6, size.height - 4))
        chart.update("\n".join(rows))
        self.query_one("#chart-summary", Static).update(summary_line(series))

    @work(exclusive=True, group="news")
    async def load_news(self, symbol: str | None) -> None:
        panel = self.query_one("#news", Static)
        self.query_one("#news-title", Static).update(
            f"[bold] NEWS[/] [cyan]{symbol or 'MARKET'}[/]")
        panel.update("[grey62]loading…[/]")
        try:
            items = await self.router.news(symbol, limit=14)
        except Exception as exc:  # noqa: BLE001
            panel.update(f"[red]{exc}[/]")
            return
        if not items:
            panel.update("[grey62]no stories[/]")
            return
        lines = []
        for item in items:
            age = (datetime.now(timezone.utc) - item.published).total_seconds() / 60
            stamp = f"{age:.0f}m" if age < 90 else f"{age / 60:.0f}h"
            lines.append(
                f"[grey50]{stamp:>4}[/] [white]{item.title[:78]}[/]\n"
                f"      [grey62]{item.publisher or item.source}[/]"
            )
        panel.update("\n".join(lines))

    @work(exclusive=True, group="detail")
    async def load_detail(self, symbol: str) -> None:
        panel = self.query_one("#detail", Static)
        self.query_one("#detail-title", Static).update(f"[bold] SECURITY[/] [cyan]{symbol}[/]")
        panel.update("[grey62]loading…[/]")
        blocks: list[str] = []
        quote = self.quotes.get(symbol)
        if quote is not None:
            colour = "green" if (quote.change or 0) >= 0 else "red"
            blocks.append(
                f"[bold]{fmt_price(quote.price)}[/] "
                f"[{colour}]{quote.change:+.2f} ({quote.change_pct:+.2f}%)[/]"
                if quote.change is not None and quote.change_pct is not None
                else f"[bold]{fmt_price(quote.price)}[/]"
            )
            blocks.append(
                f"[grey62]day range[/] {fmt_price(quote.low)} – {fmt_price(quote.high)}   "
                f"[grey62]open[/] {fmt_price(quote.open)}   "
                f"[grey62]prev[/] {fmt_price(quote.prev_close)}\n"
                f"[grey62]volume[/] {fmt_volume(quote.volume)}   "
                f"[grey62]venue[/] {quote.venue or '--'}   "
                f"[grey62]quote src[/] {quote.source}"
            )
        try:
            profile = await self.router.profile(symbol)
            blocks.append(
                f"[bold]{profile.name or symbol}[/]  [grey62]{profile.exchange}[/]\n"
                f"[grey62]sector[/] {profile.sector or '--'}   "
                f"[grey62]industry[/] {profile.industry or '--'}\n"
                f"[grey62]mkt cap[/] {fmt_volume(profile.market_cap)}   "
                f"[grey62]ccy[/] {profile.currency}   [grey62]src[/] {profile.source}"
            )
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"[grey62]profile unavailable: {str(exc)[:60]}[/]")
        try:
            filings = await self.router.filings(symbol, limit=6)
            if filings:
                rows = "\n".join(
                    f"  [yellow]{f.form:<6}[/] [grey62]{f.filed:%Y-%m-%d}[/]  {f.title[:40]}"
                    for f in filings
                )
                blocks.append(f"[bold]SEC FILINGS[/]\n{rows}")
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"[grey62]filings unavailable: {str(exc)[:60]}[/]")
        panel.update("\n\n".join(blocks))

    def load_security(self, symbol: str) -> None:
        self.active = symbol.upper()
        self.load_chart(self.active)
        self.load_news(self.active)
        self.load_detail(self.active)

    # -- interaction -------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.load_security(str(event.row_key.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        event.input.value = ""
        if raw:
            self.run_command(raw)

    def run_command(self, raw: str) -> None:
        """Dispatch one command line. Split out so tests can drive it directly."""
        text = raw.strip()
        # "AAPL <GO>" — the Bloomberg muscle memory works too.
        if text.upper().endswith("<GO>"):
            text = text[:-4].strip()
        if not text:
            return

        if not text.startswith("/"):
            self.load_security(text.split()[0])
            return

        parts = text[1:].split()
        command = parts[0].lower() if parts else ""
        args = parts[1:]
        detail = self.query_one("#detail", Static)

        if command in {"quit", "q", "exit"}:
            self.exit()
        elif command in {"help", "h", "?"}:
            detail.update(HELP)
        elif command == "add" and args:
            for symbol in (a.upper() for a in args):
                if symbol not in self.watchlist:
                    self.watchlist.append(symbol)
                    self.query_one("#watchlist", DataTable).add_row(
                        symbol, "…", "", "", "", "", key=symbol)
            self.action_refresh()
        elif command in {"rm", "remove", "del"} and args:
            table = self.query_one("#watchlist", DataTable)
            for symbol in (a.upper() for a in args):
                if symbol in self.watchlist:
                    self.watchlist.remove(symbol)
                    self.quotes.pop(symbol, None)
                    try:
                        table.remove_row(symbol)
                    except Exception:  # row already gone
                        pass
        elif command == "chart":
            if args:
                self.interval = args[0]
            self.load_chart(self.active)
        elif command == "news":
            self.load_news(args[0].upper() if args else self.active)
        elif command == "filings":
            self.load_detail(args[0].upper() if args else self.active)
        elif command == "search" and args:
            self.run_search(" ".join(args))
        elif command == "keys":
            self.action_show_keys()
        elif command == "status":
            self.action_show_status()
        elif command == "refresh":
            self.action_refresh()
        else:
            detail.update(f"[red]unknown command:[/] {text}\n\n{HELP}")

    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        panel = self.query_one("#detail", Static)
        panel.update(f"[grey62]searching {query}…[/]")
        try:
            results = await self.router.search(query, limit=12)
        except Exception as exc:  # noqa: BLE001
            panel.update(f"[red]{exc}[/]")
            return
        if not results:
            panel.update("[grey62]no matches[/]")
            return
        rows = "\n".join(
            f"  [cyan]{r['symbol']:<10}[/] {r['name'][:40]:<42} [grey62]{r['exchange']}[/]"
            for r in results
        )
        panel.update(f"[bold]SEARCH — {query}[/]\n{rows}")

    # -- actions -----------------------------------------------------------

    def action_focus_command(self) -> None:
        self.query_one("#command", Input).focus()

    def action_show_help(self) -> None:
        self.query_one("#detail", Static).update(HELP)

    def action_show_keys(self) -> None:
        lines = ["[bold]KEY POOLS[/]"]
        for pool in self.router.keys.pools.values():
            if not pool.usable:
                continue
            if pool.keyless:
                lines.append(f"  [green]{pool.name:<14}[/] [grey62]keyless[/]")
                continue
            ready = pool.available_count()
            colour = "green" if ready == pool.size else ("yellow" if ready else "red")
            lines.append(f"  [{colour}]{pool.name:<14}[/] {ready}/{pool.size} ready")
            for key in pool.keys:
                state = "ready" if key.available() else (
                    f"cooling {key.cooldown_remaining:.0f}s" if key.cooling else "spent")
                cap = f"/{key.daily_cap}" if key.daily_cap else ""
                lines.append(
                    f"      [grey62]{key.masked:<16}[/] {state:<14} "
                    f"[grey62]{key.calls_today}{cap} today  "
                    f"ok={key.successes} 429={key.rate_limit_hits}[/]"
                )
        self.query_one("#detail", Static).update("\n".join(lines))

    def action_show_status(self) -> None:
        lines = ["[bold]PROVIDER HEALTH[/]"]
        status = self.router.status()["providers"]
        for name in sorted(status, key=lambda n: status[n]["priority"]):
            info = status[name]
            colour = "red" if info["penalised"] else "green"
            lines.append(
                f"  [{colour}]{name:<14}[/] [grey62]p{info['priority']:<3}[/] "
                f"calls={info['calls']:<4} ok={info['success_rate']:.0f}% "
                f"{info['avg_latency_ms']:.0f}ms 429s={info['rate_limits']}"
            )
            if info["last_error"]:
                lines.append(f"      [grey50]{info['last_error'][:70]}[/]")
        self.query_one("#detail", Static).update("\n".join(lines))
