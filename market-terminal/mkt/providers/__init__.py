"""Provider registry: instantiate every source that has usable credentials."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Settings
from ..keypool import KeyRegistry
from .base import NotSupported, Provider, SymbolNotFound
from .keyed import (
    AlphaVantageProvider, FinnhubProvider, FmpProvider, FredProvider,
    MarketstackProvider, NewsApiProvider, PolygonProvider, TiingoProvider,
    TwelveDataProvider,
)
from .keyless import CoinGeckoProvider, StooqProvider, YahooProvider

if TYPE_CHECKING:
    from ..http import HttpClient

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "stooq": StooqProvider,
    "yahoo": YahooProvider,
    "coingecko": CoinGeckoProvider,
    "alphavantage": AlphaVantageProvider,
    "finnhub": FinnhubProvider,
    "twelvedata": TwelveDataProvider,
    "polygon": PolygonProvider,
    "fmp": FmpProvider,
    "tiingo": TiingoProvider,
    "marketstack": MarketstackProvider,
    "fred": FredProvider,
    "newsapi": NewsApiProvider,
}


def build_providers(
    http: "HttpClient", settings: Settings, keys: KeyRegistry
) -> dict[str, Provider]:
    """Instantiate every provider that can actually serve a request.

    A keyed provider with an empty pool is skipped entirely rather than
    constructed and failed later — that keeps the router's candidate list
    honest about what is really available.
    """
    from ..scrapers.edgar import EdgarScraper
    from ..scrapers.rss import RssScraper

    classes: dict[str, type[Provider]] = dict(PROVIDER_CLASSES)
    classes["edgar"] = EdgarScraper
    classes["rss"] = RssScraper

    built: dict[str, Provider] = {}
    for name, cls in classes.items():
        pool = keys.get(name)
        if pool is None or not pool.usable:
            continue
        built[name] = cls(http, settings, pool)
    return built


__all__ = [
    "Provider", "NotSupported", "SymbolNotFound",
    "PROVIDER_CLASSES", "build_providers",
]
