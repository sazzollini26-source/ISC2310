"""Every provider must produce a correctly normalised model from a realistic
vendor payload."""
from __future__ import annotations

import pytest

from mkt.models import Quote, Series
from mkt.providers import build_providers
from mkt.providers.base import SymbolNotFound

QUOTE_PROVIDERS = [
    "stooq", "yahoo", "alphavantage", "finnhub", "twelvedata",
    "polygon", "fmp", "tiingo", "marketstack",
]
HISTORY_PROVIDERS = [
    "stooq", "yahoo", "alphavantage", "twelvedata", "polygon",
    "fmp", "tiingo", "marketstack", "fred",
]
NEWS_PROVIDERS = ["finnhub", "polygon", "fmp", "tiingo", "newsapi", "rss"]
PROFILE_PROVIDERS = ["yahoo", "alphavantage", "finnhub", "fmp"]


@pytest.fixture
def providers(http, settings, keys):
    return build_providers(http, settings, keys)


def test_all_fourteen_sources_are_built(providers):
    assert len(providers) == 14, sorted(providers)


@pytest.mark.parametrize("name", QUOTE_PROVIDERS)
async def test_quote_shape(providers, name):
    quote = await providers[name].quote("AAPL")
    assert isinstance(quote, Quote)
    assert quote.symbol == "AAPL"
    assert quote.price == pytest.approx(232.15, abs=0.01)
    assert quote.source == name
    assert quote.ts.tzinfo is not None


@pytest.mark.parametrize("name", HISTORY_PROVIDERS)
async def test_history_shape(providers, name):
    symbol = "DGS10" if name == "fred" else "AAPL"
    series = await providers[name].history(symbol, "1d", 30)
    assert isinstance(series, Series)
    assert len(series) >= 15
    assert series.bars == sorted(series.bars, key=lambda b: b.ts), "bars must be chronological"
    assert all(bar.close > 0 for bar in series.bars)
    assert series.source == name


@pytest.mark.parametrize("name", NEWS_PROVIDERS)
async def test_news_shape(providers, name):
    items = await providers[name].news("AAPL", limit=10)
    assert items, f"{name} returned no news"
    first = items[0]
    assert first.title and first.published.tzinfo is not None
    assert "<" not in first.summary, "HTML must be stripped from summaries"


@pytest.mark.parametrize("name", PROFILE_PROVIDERS)
async def test_profile_shape(providers, name):
    profile = await providers[name].profile("AAPL")
    assert profile.symbol == "AAPL"
    assert profile.name


async def test_yahoo_skips_null_bars(providers):
    """Yahoo pads holidays with nulls; those must be dropped, not zeroed."""
    series = await providers["yahoo"].history("AAPL", "1d", 30)
    assert len(series) == 20, "one null bar should have been dropped from 21"
    assert all(bar.close is not None and bar.close > 0 for bar in series.bars)


async def test_fred_skips_missing_observations(providers):
    """FRED writes '.' for missing periods."""
    series = await providers["fred"].history("DGS10", "1d", 30)
    assert len(series) == 21
    assert all(bar.close > 0 for bar in series.bars)


async def test_edgar_resolves_cik_and_filters_forms(providers):
    filings = await providers["edgar"].filings("AAPL", forms=("10-Q", "10-K"), limit=10)
    assert [f.form for f in filings] == ["10-Q", "10-K"]
    assert filings[0].url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert filings[0].accession == "0000320193-26-000081"


async def test_edgar_unknown_ticker(providers):
    with pytest.raises(SymbolNotFound):
        await providers["edgar"].filings("NOTATICKER")


async def test_derived_fields_fill_in(providers):
    """Stooq sends no change/prev_close; the model derives what it can."""
    quote = await providers["stooq"].quote("AAPL")
    assert quote.change is None or isinstance(quote.change, float)
    finnhub = await providers["finnhub"].quote("AAPL")
    assert finnhub.change_pct == pytest.approx(0.8033, abs=0.01)
    assert finnhub.prev_close == pytest.approx(230.30, abs=0.01)


async def test_search(providers):
    for name in ("yahoo", "alphavantage", "finnhub", "twelvedata"):
        results = await providers[name].search("apple", limit=5)
        assert results and results[0]["symbol"] == "AAPL"
