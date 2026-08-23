"""Drive the real Textual app headlessly against the vendor simulator."""
from __future__ import annotations

import pytest
from textual.widgets import DataTable

from mkt.ui.app import MarketTerminal


@pytest.fixture
def app(router, settings):
    settings.quote_refresh = 3600.0   # no background refresh mid-test
    return MarketTerminal(router, settings)


async def settle(pilot, rounds: int = 12):
    """Let the workers finish; they are async and fire on mount."""
    for _ in range(rounds):
        await pilot.pause()


def text_of(app, selector) -> str:
    """Raw markup of a Static — asserting on this catches anything rendered,
    including text that styling would otherwise hide."""
    from textual.widgets import Static
    widget = app.query_one(selector, Static) if isinstance(selector, str) \
        else app.query_one(selector)
    return str(widget.content)


async def test_app_boots_and_fills_the_watchlist(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        table = app.query_one("#watchlist", DataTable)
        assert table.row_count == 3
        assert set(app.quotes) == {"AAPL", "MSFT", "NVDA"}
        assert app.quotes["AAPL"].price == pytest.approx(232.15, abs=0.01)


async def test_chart_news_and_detail_render(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        chart = text_of(app, "#chart")
        assert "█" in chart or "│" in chart, "chart should have drawn candles"
        summary = text_of(app, "#chart-summary")
        assert "AAPL" in summary
        news = text_of(app, "#news")
        assert "loading" not in news and len(news) > 20
        detail = text_of(app, "#detail")
        assert "SEC FILINGS" in detail or "Apple" in detail


async def test_add_and_remove_from_watchlist(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("/add TSLA")
        await settle(pilot)
        assert "TSLA" in app.watchlist
        assert app.query_one("#watchlist", DataTable).row_count == 4
        app.run_command("/rm TSLA")
        await settle(pilot)
        assert "TSLA" not in app.watchlist
        assert app.query_one("#watchlist", DataTable).row_count == 3


async def test_bare_ticker_loads_a_security(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("MSFT")
        await settle(pilot)
        assert app.active == "MSFT"
        assert "MSFT" in text_of(app, "#chart-title")


async def test_bloomberg_go_suffix_is_accepted(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("NVDA <GO>")
        await settle(pilot)
        assert app.active == "NVDA"


async def test_chart_interval_command(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("/chart 1wk")
        await settle(pilot)
        assert app.interval == "1wk"
        assert "1wk" in text_of(app, "#chart-title")


async def test_keys_screen_shows_pools_but_no_secrets(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("/keys")
        await pilot.pause()
        detail = text_of(app, "#detail")
        assert "KEY POOLS" in detail and "finnhub" in detail
        assert "finnhub_key_01" not in detail, "full keys must never be rendered"
        assert "..." in detail, "masked form expected"


async def test_status_screen_lists_providers(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("/status")
        await pilot.pause()
        detail = text_of(app, "#detail")
        assert "PROVIDER HEALTH" in detail
        assert "finnhub" in detail and "edgar" in detail


async def test_search_command(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("/search apple")
        await settle(pilot)
        detail = text_of(app, "#detail")
        assert "SEARCH" in detail and "AAPL" in detail


async def test_unknown_command_is_reported(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.run_command("/nonsense")
        await pilot.pause()
        assert "unknown command" in text_of(app, "#detail")


async def test_status_bar_reports_key_readiness(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        from mkt.ui.app import StatusBar
        bar = text_of(app, StatusBar)
        assert "keys" in bar and "sources up" in bar


async def test_ui_survives_a_dead_provider(app, sim):
    """A source going down must degrade the pane, not crash the app."""
    for host in ("finnhub.io", "query1.finance.yahoo.com", "stooq.com",
                 "api.coingecko.com", "api.twelvedata.com", "financialmodelingprep.com",
                 "www.alphavantage.co", "api.tiingo.com", "api.polygon.io",
                 "api.marketstack.com"):
        sim.set_fail(host, "500")
    async with app.run_test() as pilot:
        await settle(pilot)
        table = app.query_one("#watchlist", DataTable)
        assert table.row_count == 3, "the table should still be standing"
        assert app.is_running
