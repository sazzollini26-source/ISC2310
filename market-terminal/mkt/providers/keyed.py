"""Credentialed providers.

Each spends one key per call from its pool. Note ``_soft_limit_check``: several
vendors answer HTTP 200 with a rate-limit message in the body instead of a
429, and if that isn't caught the key never gets benched and the router happily
keeps burning the whole pool on refusals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..http import RateLimited
from ..models import Bar, NewsItem, Profile, Quote, Series
from .base import Provider, SymbolNotFound, epoch, num, parse_date

_LIMIT_MARKERS = (
    "rate limit", "call frequency", "api call volume", "requests per",
    "quota", "usage limit", "too many requests", "exceeded",
    "premium endpoint", "upgrade your plan",
)


def _soft_limit_check(payload: Any, provider: str, url: str) -> None:
    """Raise RateLimited when a 200 response is really a refusal."""
    if not isinstance(payload, dict):
        return
    for field in ("Note", "Information", "message", "error", "detail", "Error Message"):
        value = payload.get(field)
        if isinstance(value, str) and any(m in value.lower() for m in _LIMIT_MARKERS):
            raise RateLimited(url, None)


class AlphaVantageProvider(Provider):
    name = "alphavantage"
    BASE = "https://www.alphavantage.co"

    async def _call(self, params: dict, ttl: float) -> dict:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}/query"
        payload = await self.http.get_json(
            url, params={**params, "apikey": key.value if key else ""}, ttl=ttl
        )
        _soft_limit_check(payload, self.name, url)
        return payload or {}

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call(
            {"function": "GLOBAL_QUOTE", "symbol": symbol.upper()}, self.settings.cache_ttl
        )
        block = payload.get("Global Quote") or {}
        price = num(block.get("05. price"))
        if price is None:
            raise SymbolNotFound(f"alphavantage: no quote for {symbol}")
        return Quote(
            symbol=symbol, price=price,
            open=num(block.get("02. open")), high=num(block.get("03. high")),
            low=num(block.get("04. low")), prev_close=num(block.get("08. previous close")),
            change=num(block.get("09. change")), change_pct=num(block.get("10. change percent")),
            volume=num(block.get("06. volume")), source=self.name,
            ts=parse_date(block.get("07. latest trading day", "")),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        function, series_key = {
            "1d": ("TIME_SERIES_DAILY", "Time Series (Daily)"),
            "1wk": ("TIME_SERIES_WEEKLY", "Weekly Time Series"),
            "1mo": ("TIME_SERIES_MONTHLY", "Monthly Time Series"),
        }.get(interval, ("TIME_SERIES_DAILY", "Time Series (Daily)"))
        params = {"function": function, "symbol": symbol.upper(),
                  "outputsize": "full" if lookback > 100 else "compact"}
        if interval in {"1m", "5m", "15m", "30m", "1h"}:
            params = {"function": "TIME_SERIES_INTRADAY", "symbol": symbol.upper(),
                      "interval": {"1h": "60min"}.get(interval, interval.replace("m", "min"))}
            series_key = f"Time Series ({params['interval']})"
        payload = await self._call(params, 300.0)
        block = payload.get(series_key) or {}
        if not block:
            raise SymbolNotFound(f"alphavantage: no history for {symbol}")
        bars = [
            Bar(ts=parse_date(day), open=num(v.get("1. open")) or 0.0,
                high=num(v.get("2. high")) or 0.0, low=num(v.get("3. low")) or 0.0,
                close=num(v.get("4. close")) or 0.0, volume=num(v.get("5. volume")) or 0.0)
            for day, v in block.items() if num(v.get("4. close")) is not None
        ]
        return Series(symbol, interval, sorted(bars, key=lambda b: b.ts)[-lookback:], source=self.name)

    async def profile(self, symbol: str) -> Profile:
        payload = await self._call({"function": "OVERVIEW", "symbol": symbol.upper()}, 3600.0)
        if not payload.get("Symbol"):
            raise SymbolNotFound(f"alphavantage: no profile for {symbol}")
        return Profile(
            symbol=symbol, name=payload.get("Name", ""), exchange=payload.get("Exchange", ""),
            industry=payload.get("Industry", ""), sector=payload.get("Sector", ""),
            country=payload.get("Country", ""), currency=payload.get("Currency", "USD"),
            market_cap=num(payload.get("MarketCapitalization")),
            description=payload.get("Description", ""), source=self.name,
        )

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        payload = await self._call({"function": "SYMBOL_SEARCH", "keywords": query}, 600.0)
        return [
            {"symbol": m.get("1. symbol", ""), "name": m.get("2. name", ""),
             "type": m.get("3. type", ""), "exchange": m.get("4. region", ""),
             "source": self.name}
            for m in (payload.get("bestMatches") or [])[:limit]
        ]


class FinnhubProvider(Provider):
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"

    async def _call(self, path: str, params: dict, ttl: float) -> Any:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}{path}"
        # Finnhub accepts the token as a header, which keeps it out of logs.
        payload = await self.http.get_json(
            url, params=params, ttl=ttl,
            headers={"X-Finnhub-Token": key.value} if key else None,
        )
        _soft_limit_check(payload, self.name, url)
        return payload

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call("/quote", {"symbol": symbol.upper()}, self.settings.cache_ttl)
        price = num((payload or {}).get("c"))
        if price is None or price == 0:
            raise SymbolNotFound(f"finnhub: no quote for {symbol}")
        return Quote(
            symbol=symbol, price=price, change=num(payload.get("d")),
            change_pct=num(payload.get("dp")), high=num(payload.get("h")),
            low=num(payload.get("l")), open=num(payload.get("o")),
            prev_close=num(payload.get("pc")), source=self.name,
            ts=epoch(payload.get("t")) if payload.get("t") else datetime.now(timezone.utc),
        )

    async def profile(self, symbol: str) -> Profile:
        payload = await self._call("/stock/profile2", {"symbol": symbol.upper()}, 3600.0)
        if not payload:
            raise SymbolNotFound(f"finnhub: no profile for {symbol}")
        cap = num(payload.get("marketCapitalization"))
        return Profile(
            symbol=symbol, name=payload.get("name", ""), exchange=payload.get("exchange", ""),
            industry=payload.get("finnhubIndustry", ""), country=payload.get("country", ""),
            currency=payload.get("currency", "USD"),
            market_cap=cap * 1e6 if cap else None,  # vendor reports millions
            website=payload.get("weburl", ""), source=self.name,
        )

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        if symbol:
            today = datetime.now(timezone.utc).date()
            payload = await self._call("/company-news", {
                "symbol": symbol.upper(),
                "from": (today - timedelta(days=14)).isoformat(),
                "to": today.isoformat(),
            }, 300.0)
        else:
            payload = await self._call("/news", {"category": "general"}, 300.0)
        items = payload if isinstance(payload, list) else []
        return [
            NewsItem(
                title=i.get("headline", ""), url=i.get("url", ""),
                published=epoch(i.get("datetime")), publisher=i.get("source", ""),
                summary=i.get("summary", ""), symbols=[symbol.upper()] if symbol else [],
                source=self.name,
            )
            for i in items[:limit] if i.get("headline")
        ]

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        payload = await self._call("/search", {"q": query}, 600.0)
        return [
            {"symbol": r.get("symbol", ""), "name": r.get("description", ""),
             "type": r.get("type", ""), "exchange": "", "source": self.name}
            for r in (payload or {}).get("result", [])[:limit]
        ]


class TwelveDataProvider(Provider):
    name = "twelvedata"
    BASE = "https://api.twelvedata.com"

    async def _call(self, path: str, params: dict, ttl: float) -> Any:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}{path}"
        payload = await self.http.get_json(
            url, params={**params, "apikey": key.value if key else ""}, ttl=ttl
        )
        if isinstance(payload, dict) and payload.get("status") == "error":
            _soft_limit_check({"message": payload.get("message", "")}, self.name, url)
            raise SymbolNotFound(f"twelvedata: {payload.get('message', 'error')}")
        return payload

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call("/quote", {"symbol": symbol.upper()}, self.settings.cache_ttl)
        price = num((payload or {}).get("close"))
        if price is None:
            raise SymbolNotFound(f"twelvedata: no quote for {symbol}")
        return Quote(
            symbol=symbol, price=price, open=num(payload.get("open")),
            high=num(payload.get("high")), low=num(payload.get("low")),
            prev_close=num(payload.get("previous_close")), change=num(payload.get("change")),
            change_pct=num(payload.get("percent_change")), volume=num(payload.get("volume")),
            currency=payload.get("currency", "USD"), venue=payload.get("exchange"),
            source=self.name,
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        mapped = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
                  "1h": "1h", "1d": "1day", "1wk": "1week", "1mo": "1month"}.get(interval, "1day")
        payload = await self._call("/time_series", {
            "symbol": symbol.upper(), "interval": mapped, "outputsize": max(1, min(lookback, 5000)),
        }, 300.0)
        values = (payload or {}).get("values") or []
        if not values:
            raise SymbolNotFound(f"twelvedata: no history for {symbol}")
        bars = [
            Bar(ts=parse_date(v.get("datetime", "")), open=num(v.get("open")) or 0.0,
                high=num(v.get("high")) or 0.0, low=num(v.get("low")) or 0.0,
                close=num(v.get("close")) or 0.0, volume=num(v.get("volume")) or 0.0)
            for v in values if num(v.get("close")) is not None
        ]
        return Series(symbol, interval, bars[-lookback:], source=self.name)

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        payload = await self._call("/symbol_search", {"symbol": query}, 600.0)
        return [
            {"symbol": d.get("symbol", ""), "name": d.get("instrument_name", ""),
             "exchange": d.get("exchange", ""), "type": d.get("instrument_type", ""),
             "source": self.name}
            for d in (payload or {}).get("data", [])[:limit]
        ]


class PolygonProvider(Provider):
    name = "polygon"
    BASE = "https://api.polygon.io"

    async def _call(self, path: str, params: dict, ttl: float) -> Any:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}{path}"
        payload = await self.http.get_json(
            url, params=params, ttl=ttl,
            headers={"Authorization": f"Bearer {key.value}"} if key else None,
        )
        _soft_limit_check(payload, self.name, url)
        return payload

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call(
            f"/v2/aggs/ticker/{symbol.upper()}/prev", {"adjusted": "true"}, self.settings.cache_ttl
        )
        results = (payload or {}).get("results") or []
        if not results:
            raise SymbolNotFound(f"polygon: no quote for {symbol}")
        bar = results[0]
        close = num(bar.get("c"))
        if close is None:
            raise SymbolNotFound(f"polygon: no close for {symbol}")
        return Quote(
            symbol=symbol, price=close, open=num(bar.get("o")), high=num(bar.get("h")),
            low=num(bar.get("l")), volume=num(bar.get("v")), source=self.name,
            ts=epoch(bar.get("t")),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        span, mult = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
                      "30m": ("minute", 30), "1h": ("hour", 1), "1d": ("day", 1),
                      "1wk": ("week", 1), "1mo": ("month", 1)}.get(interval, ("day", 1))
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=max(lookback * 2, 30))
        payload = await self._call(
            f"/v2/aggs/ticker/{symbol.upper()}/range/{mult}/{span}/{start}/{end}",
            {"adjusted": "true", "sort": "asc", "limit": 50000}, 300.0,
        )
        results = (payload or {}).get("results") or []
        if not results:
            raise SymbolNotFound(f"polygon: no history for {symbol}")
        bars = [
            Bar(ts=epoch(r.get("t")), open=num(r.get("o")) or 0.0, high=num(r.get("h")) or 0.0,
                low=num(r.get("l")) or 0.0, close=num(r.get("c")) or 0.0,
                volume=num(r.get("v")) or 0.0)
            for r in results if num(r.get("c")) is not None
        ]
        return Series(symbol, interval, bars[-lookback:], source=self.name)

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        params: dict = {"limit": min(limit, 50), "order": "desc"}
        if symbol:
            params["ticker"] = symbol.upper()
        payload = await self._call("/v2/reference/news", params, 300.0)
        return [
            NewsItem(
                title=r.get("title", ""), url=r.get("article_url", ""),
                published=parse_date(r.get("published_utc", "")),
                publisher=(r.get("publisher") or {}).get("name", ""),
                summary=r.get("description", ""), symbols=r.get("tickers", []) or [],
                source=self.name,
            )
            for r in (payload or {}).get("results", [])[:limit] if r.get("title")
        ]


class FmpProvider(Provider):
    name = "fmp"
    BASE = "https://financialmodelingprep.com"

    async def _call(self, path: str, params: dict, ttl: float) -> Any:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}{path}"
        payload = await self.http.get_json(
            url, params={**params, "apikey": key.value if key else ""}, ttl=ttl
        )
        _soft_limit_check(payload, self.name, url)
        return payload

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call(f"/api/v3/quote/{symbol.upper()}", {}, self.settings.cache_ttl)
        rows = payload if isinstance(payload, list) else []
        if not rows:
            raise SymbolNotFound(f"fmp: no quote for {symbol}")
        row = rows[0]
        price = num(row.get("price"))
        if price is None:
            raise SymbolNotFound(f"fmp: no price for {symbol}")
        return Quote(
            symbol=symbol, price=price, change=num(row.get("change")),
            change_pct=num(row.get("changesPercentage")), open=num(row.get("open")),
            high=num(row.get("dayHigh")), low=num(row.get("dayLow")),
            prev_close=num(row.get("previousClose")), volume=num(row.get("volume")),
            venue=row.get("exchange"), source=self.name,
            ts=epoch(row.get("timestamp")) if row.get("timestamp") else datetime.now(timezone.utc),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        if interval in {"1m", "5m", "15m", "30m", "1h"}:
            mapped = {"1h": "1hour"}.get(interval, interval.replace("m", "min"))
            payload = await self._call(
                f"/api/v3/historical-chart/{mapped}/{symbol.upper()}", {}, 300.0)
            rows = payload if isinstance(payload, list) else []
        else:
            payload = await self._call(
                f"/api/v3/historical-price-full/{symbol.upper()}",
                {"timeseries": max(1, lookback)}, 300.0)
            rows = (payload or {}).get("historical") or []
        if not rows:
            raise SymbolNotFound(f"fmp: no history for {symbol}")
        bars = [
            Bar(ts=parse_date(r.get("date", "")), open=num(r.get("open")) or 0.0,
                high=num(r.get("high")) or 0.0, low=num(r.get("low")) or 0.0,
                close=num(r.get("close")) or 0.0, volume=num(r.get("volume")) or 0.0)
            for r in rows if num(r.get("close")) is not None
        ]
        return Series(symbol, interval, sorted(bars, key=lambda b: b.ts)[-lookback:], source=self.name)

    async def profile(self, symbol: str) -> Profile:
        payload = await self._call(f"/api/v3/profile/{symbol.upper()}", {}, 3600.0)
        rows = payload if isinstance(payload, list) else []
        if not rows:
            raise SymbolNotFound(f"fmp: no profile for {symbol}")
        row = rows[0]
        return Profile(
            symbol=symbol, name=row.get("companyName", ""), exchange=row.get("exchangeShortName", ""),
            industry=row.get("industry", ""), sector=row.get("sector", ""),
            country=row.get("country", ""), currency=row.get("currency", "USD"),
            market_cap=num(row.get("mktCap")), website=row.get("website", ""),
            description=row.get("description", ""), source=self.name,
        )

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        params: dict = {"limit": min(limit, 50)}
        if symbol:
            params["tickers"] = symbol.upper()
        payload = await self._call("/api/v3/stock_news", params, 300.0)
        rows = payload if isinstance(payload, list) else []
        return [
            NewsItem(title=r.get("title", ""), url=r.get("url", ""),
                     published=parse_date(r.get("publishedDate", "")),
                     publisher=r.get("site", ""), summary=r.get("text", ""),
                     symbols=[r.get("symbol")] if r.get("symbol") else [], source=self.name)
            for r in rows[:limit] if r.get("title")
        ]


class TiingoProvider(Provider):
    name = "tiingo"
    BASE = "https://api.tiingo.com"

    async def _call(self, path: str, params: dict, ttl: float) -> Any:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}{path}"
        payload = await self.http.get_json(
            url, params=params, ttl=ttl,
            headers={"Authorization": f"Token {key.value}"} if key else None,
        )
        _soft_limit_check(payload, self.name, url)
        return payload

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call(f"/iex/{symbol.upper()}", {}, self.settings.cache_ttl)
        rows = payload if isinstance(payload, list) else []
        if not rows:
            raise SymbolNotFound(f"tiingo: no quote for {symbol}")
        row = rows[0]
        price = num(row.get("last")) or num(row.get("tngoLast")) or num(row.get("prevClose"))
        if price is None:
            raise SymbolNotFound(f"tiingo: no price for {symbol}")
        return Quote(
            symbol=symbol, price=price, open=num(row.get("open")), high=num(row.get("high")),
            low=num(row.get("low")), prev_close=num(row.get("prevClose")),
            volume=num(row.get("volume")), source=self.name,
            ts=parse_date(row.get("timestamp", "")),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        start = (datetime.now(timezone.utc).date() - timedelta(days=max(lookback * 2, 30))).isoformat()
        payload = await self._call(
            f"/tiingo/daily/{symbol.upper()}/prices", {"startDate": start}, 300.0)
        rows = payload if isinstance(payload, list) else []
        if not rows:
            raise SymbolNotFound(f"tiingo: no history for {symbol}")
        bars = [
            Bar(ts=parse_date(r.get("date", "")), open=num(r.get("open")) or 0.0,
                high=num(r.get("high")) or 0.0, low=num(r.get("low")) or 0.0,
                close=num(r.get("close")) or 0.0, volume=num(r.get("volume")) or 0.0)
            for r in rows if num(r.get("close")) is not None
        ]
        return Series(symbol, interval, bars[-lookback:], source=self.name)

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        params: dict = {"limit": min(limit, 100)}
        if symbol:
            params["tickers"] = symbol.upper()
        payload = await self._call("/tiingo/news", params, 300.0)
        rows = payload if isinstance(payload, list) else []
        return [
            NewsItem(title=r.get("title", ""), url=r.get("url", ""),
                     published=parse_date(r.get("publishedDate", "")),
                     publisher=r.get("source", ""), summary=r.get("description", ""),
                     symbols=[t.upper() for t in (r.get("tickers") or [])], source=self.name)
            for r in rows[:limit] if r.get("title")
        ]


class MarketstackProvider(Provider):
    name = "marketstack"
    BASE = "https://api.marketstack.com"

    async def _call(self, path: str, params: dict, ttl: float) -> Any:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}{path}"
        payload = await self.http.get_json(
            url, params={**params, "access_key": key.value if key else ""}, ttl=ttl)
        if isinstance(payload, dict) and payload.get("error"):
            message = (payload["error"] or {}).get("message", "")
            _soft_limit_check({"message": message}, self.name, url)
            raise SymbolNotFound(f"marketstack: {message}")
        return payload

    async def quote(self, symbol: str) -> Quote:
        payload = await self._call("/v1/eod/latest", {"symbols": symbol.upper()},
                                   self.settings.cache_ttl)
        rows = (payload or {}).get("data") or []
        if not rows:
            raise SymbolNotFound(f"marketstack: no quote for {symbol}")
        row = rows[0]
        close = num(row.get("close"))
        if close is None:
            raise SymbolNotFound(f"marketstack: no close for {symbol}")
        return Quote(
            symbol=symbol, price=close, open=num(row.get("open")), high=num(row.get("high")),
            low=num(row.get("low")), volume=num(row.get("volume")),
            venue=row.get("exchange"), source=self.name, ts=parse_date(row.get("date", "")),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        payload = await self._call(
            "/v1/eod", {"symbols": symbol.upper(), "limit": max(1, min(lookback, 1000))}, 300.0)
        rows = (payload or {}).get("data") or []
        if not rows:
            raise SymbolNotFound(f"marketstack: no history for {symbol}")
        bars = [
            Bar(ts=parse_date(r.get("date", "")), open=num(r.get("open")) or 0.0,
                high=num(r.get("high")) or 0.0, low=num(r.get("low")) or 0.0,
                close=num(r.get("close")) or 0.0, volume=num(r.get("volume")) or 0.0)
            for r in rows if num(r.get("close")) is not None
        ]
        return Series(symbol, interval, sorted(bars, key=lambda b: b.ts)[-lookback:], source=self.name)


class FredProvider(Provider):
    """Macro series: DGS10, CPIAUCSL, UNRATE, FEDFUNDS and friends."""

    name = "fred"
    BASE = "https://api.stlouisfed.org"

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}/fred/series/observations"
        payload = await self.http.get_json(url, params={
            "series_id": symbol.upper(), "api_key": key.value if key else "",
            "file_type": "json", "sort_order": "desc", "limit": max(1, lookback),
        }, ttl=3600.0)
        _soft_limit_check(payload, self.name, url)
        observations = (payload or {}).get("observations") or []
        bars = []
        for obs in observations:
            value = num(obs.get("value"))
            if value is None:  # FRED writes "." for missing periods
                continue
            stamp = parse_date(obs.get("date", ""))
            bars.append(Bar(ts=stamp, open=value, high=value, low=value, close=value))
        if not bars:
            raise SymbolNotFound(f"fred: no observations for {symbol}")
        return Series(symbol, interval, sorted(bars, key=lambda b: b.ts), source=self.name)


class NewsApiProvider(Provider):
    name = "newsapi"
    BASE = "https://newsapi.org"

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        key = await self.acquire()
        base = self.settings.base_url(self.name, self.BASE)
        url = f"{base}/v2/everything"
        query = f"{symbol} stock" if symbol else "stock market OR equities OR earnings"
        payload = await self.http.get_json(url, params={
            "q": query, "sortBy": "publishedAt", "language": "en",
            "pageSize": min(limit, 100), "apiKey": key.value if key else "",
        }, ttl=300.0)
        if isinstance(payload, dict) and payload.get("status") == "error":
            _soft_limit_check({"message": payload.get("message", "")}, self.name, url)
        return [
            NewsItem(title=a.get("title", ""), url=a.get("url", ""),
                     published=parse_date(a.get("publishedAt", "")),
                     publisher=(a.get("source") or {}).get("name", ""),
                     summary=a.get("description") or "",
                     symbols=[symbol.upper()] if symbol else [], source=self.name)
            for a in (payload or {}).get("articles", [])[:limit] if a.get("title")
        ]
