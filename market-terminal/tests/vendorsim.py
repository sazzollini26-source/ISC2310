"""A fake internet for the test suite.

Emulates each vendor's real response shape so provider parsing is tested
against realistic payloads, and lets a test inject 429s, 500s, soft rate-limit
bodies and auth failures on demand — the conditions that actually exercise
rotation and failover.
"""
from __future__ import annotations

import time
from collections import defaultdict

import httpx

BASE_PRICE = {"AAPL": 232.15, "MSFT": 418.90, "NVDA": 121.44, "SPY": 561.02, "BTC": 64210.5}


def price_for(symbol: str) -> float:
    return BASE_PRICE.get(symbol.upper().replace(".US", ""), 100.0)


class VendorSim:
    """Routes MockTransport requests to per-vendor handlers."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = defaultdict(int)
        self.keys_seen: dict[str, list[str]] = defaultdict(list)
        # host -> mode in {"429","500","softlimit","401","empty"}
        self.fail: dict[str, str] = {}
        self.fail_times: dict[str, int] = defaultdict(int)

    # -- controls ----------------------------------------------------------

    def set_fail(self, host: str, mode: str, times: int = 10**9) -> None:
        self.fail[host] = mode
        self.fail_times[host] = times

    def clear(self, host: str | None = None) -> None:
        if host:
            self.fail.pop(host, None)
        else:
            self.fail.clear()

    def _maybe_fail(self, host: str) -> httpx.Response | None:
        mode = self.fail.get(host)
        if not mode or self.fail_times[host] <= 0:
            return None
        self.fail_times[host] -= 1
        if self.fail_times[host] <= 0:
            self.fail.pop(host, None)
        if mode == "429":
            return httpx.Response(429, headers={"Retry-After": "3"}, text="slow down")
        if mode == "500":
            return httpx.Response(500, text="server error")
        if mode == "401":
            return httpx.Response(401, text="invalid api key")
        if mode == "softlimit":
            # HTTP 200 that is really a refusal — the case that silently
            # burns a whole key pool if the client does not catch it.
            return httpx.Response(200, json={
                "Note": "Thank you for using Alpha Vantage! Our standard API call "
                        "frequency is 5 calls per minute and 25 calls per day."
            })
        if mode == "empty":
            return httpx.Response(200, json={})
        return None

    # -- entrypoint --------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        params = dict(request.url.params)
        self.calls[host] += 1

        # Record which credential was presented, so rotation is observable.
        for source in (
            params.get("apikey"), params.get("api_key"), params.get("apiKey"),
            params.get("access_key"), params.get("token"),
            request.headers.get("X-Finnhub-Token"),
            (request.headers.get("Authorization") or "").replace("Bearer ", "").replace("Token ", ""),
        ):
            if source:
                self.keys_seen[host].append(source)

        injected = self._maybe_fail(host)
        if injected is not None:
            return injected

        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nDisallow: /private")

        for prefix, method in (
            ("www.alphavantage.co", self._alphavantage),
            ("finnhub.io", self._finnhub),
            ("api.twelvedata.com", self._twelvedata),
            ("api.polygon.io", self._polygon),
            ("financialmodelingprep.com", self._fmp),
            ("api.tiingo.com", self._tiingo),
            ("api.marketstack.com", self._marketstack),
            ("api.stlouisfed.org", self._fred),
            ("newsapi.org", self._newsapi),
            ("stooq.com", self._stooq),
            ("query1.finance.yahoo.com", self._yahoo),
            ("query2.finance.yahoo.com", self._yahoo_search),
            ("api.coingecko.com", self._coingecko),
            ("www.sec.gov", self._sec_www),
            ("data.sec.gov", self._sec_data),
            ("feeds.finance.yahoo.com", self._rss),
            ("www.cnbc.com", self._rss),
            ("feeds.content.dowjones.io", self._rss),
            ("www.federalreserve.gov", self._rss),
        ):
            if host == prefix:
                return method(path, params, request)

        return httpx.Response(404, text=f"unmocked host {host}{path}")

    # -- vendor shapes -----------------------------------------------------

    def _alphavantage(self, path, params, request):
        function = params.get("function", "")
        symbol = params.get("symbol", "AAPL")
        price = price_for(symbol)
        if function == "GLOBAL_QUOTE":
            return httpx.Response(200, json={"Global Quote": {
                "01. symbol": symbol, "02. open": f"{price - 1:.4f}",
                "03. high": f"{price + 2:.4f}", "04. low": f"{price - 3:.4f}",
                "05. price": f"{price:.4f}", "06. volume": "51234567",
                "07. latest trading day": "2026-08-21",
                "08. previous close": f"{price - 1.5:.4f}",
                "09. change": "1.5000", "10. change percent": "0.6512%",
            }})
        if function == "TIME_SERIES_DAILY":
            series = {
                f"2026-08-{d:02d}": {
                    "1. open": f"{price + d - 20:.2f}", "2. high": f"{price + d - 18:.2f}",
                    "3. low": f"{price + d - 22:.2f}", "4. close": f"{price + d - 19:.2f}",
                    "5. volume": "40000000",
                } for d in range(1, 22)
            }
            return httpx.Response(200, json={"Time Series (Daily)": series})
        if function == "OVERVIEW":
            return httpx.Response(200, json={
                "Symbol": symbol, "Name": "Apple Inc", "Exchange": "NASDAQ",
                "Sector": "TECHNOLOGY", "Industry": "ELECTRONIC COMPUTERS",
                "Country": "USA", "Currency": "USD", "MarketCapitalization": "3510000000000",
                "Description": "Designs and sells consumer electronics.",
            })
        if function == "SYMBOL_SEARCH":
            return httpx.Response(200, json={"bestMatches": [
                {"1. symbol": "AAPL", "2. name": "Apple Inc", "3. type": "Equity",
                 "4. region": "United States"}]})
        return httpx.Response(200, json={})

    def _finnhub(self, path, params, request):
        symbol = params.get("symbol", "AAPL")
        price = price_for(symbol)
        if path.endswith("/quote"):
            return httpx.Response(200, json={
                "c": price, "d": 1.85, "dp": 0.8033, "h": price + 2.4,
                "l": price - 3.1, "o": price - 1.2, "pc": price - 1.85,
                "t": int(time.time()),
            })
        if path.endswith("/stock/profile2"):
            return httpx.Response(200, json={
                "name": "Apple Inc", "exchange": "NASDAQ NMS - GLOBAL MARKET",
                "finnhubIndustry": "Technology", "country": "US", "currency": "USD",
                "marketCapitalization": 3510000.0, "weburl": "https://www.apple.com/",
            })
        if "news" in path:
            return httpx.Response(200, json=[{
                "headline": "Apple beats on services revenue",
                "url": "https://news.test/a1", "datetime": int(time.time()) - 3600,
                "source": "Reuters", "summary": "Services grew 14% year over year.",
            }])
        if path.endswith("/search"):
            return httpx.Response(200, json={"result": [
                {"symbol": "AAPL", "description": "APPLE INC", "type": "Common Stock"}]})
        return httpx.Response(200, json={})

    def _twelvedata(self, path, params, request):
        symbol = params.get("symbol", "AAPL")
        price = price_for(symbol)
        if path.endswith("/quote"):
            return httpx.Response(200, json={
                "symbol": symbol, "open": f"{price-1:.2f}", "high": f"{price+2:.2f}",
                "low": f"{price-3:.2f}", "close": f"{price:.2f}", "volume": "48000000",
                "previous_close": f"{price-1.5:.2f}", "change": "1.50",
                "percent_change": "0.65", "currency": "USD", "exchange": "NASDAQ",
            })
        if path.endswith("/time_series"):
            return httpx.Response(200, json={"values": [
                {"datetime": f"2026-08-{d:02d}", "open": f"{price+d-20:.2f}",
                 "high": f"{price+d-18:.2f}", "low": f"{price+d-22:.2f}",
                 "close": f"{price+d-19:.2f}", "volume": "40000000"}
                for d in range(21, 0, -1)]})
        if path.endswith("/symbol_search"):
            return httpx.Response(200, json={"data": [
                {"symbol": "AAPL", "instrument_name": "Apple Inc",
                 "exchange": "NASDAQ", "instrument_type": "Common Stock"}]})
        return httpx.Response(200, json={})

    def _polygon(self, path, params, request):
        if "/prev" in path:
            symbol = path.split("/ticker/")[1].split("/")[0]
            price = price_for(symbol)
            return httpx.Response(200, json={"results": [{
                "o": price - 1.2, "h": price + 2.4, "l": price - 3.1,
                "c": price, "v": 48000000, "t": int(time.time() * 1000)}]})
        if "/range/" in path:
            symbol = path.split("/ticker/")[1].split("/")[0]
            price = price_for(symbol)
            now = int(time.time() * 1000)
            return httpx.Response(200, json={"results": [
                {"o": price+i-10, "h": price+i-8, "l": price+i-12, "c": price+i-9,
                 "v": 4e7, "t": now - (20 - i) * 86400000} for i in range(21)]})
        if "news" in path:
            return httpx.Response(200, json={"results": [{
                "title": "Polygon: chipmakers rally", "article_url": "https://news.test/p1",
                "published_utc": "2026-08-21T12:00:00Z", "publisher": {"name": "Polygon"},
                "description": "Sector up 3%.", "tickers": ["NVDA"]}]})
        return httpx.Response(200, json={})

    def _fmp(self, path, params, request):
        symbol = path.rstrip("/").split("/")[-1]
        price = price_for(symbol)
        if "/quote/" in path:
            return httpx.Response(200, json=[{
                "symbol": symbol, "price": price, "change": 1.85,
                "changesPercentage": 0.8033, "open": price - 1.2,
                "dayHigh": price + 2.4, "dayLow": price - 3.1,
                "previousClose": price - 1.85, "volume": 48000000,
                "exchange": "NASDAQ", "timestamp": int(time.time())}])
        if "historical-price-full" in path:
            return httpx.Response(200, json={"historical": [
                {"date": f"2026-08-{d:02d}", "open": price+d-20, "high": price+d-18,
                 "low": price+d-22, "close": price+d-19, "volume": 4e7}
                for d in range(21, 0, -1)]})
        if "/profile/" in path:
            return httpx.Response(200, json=[{
                "companyName": "Apple Inc", "exchangeShortName": "NASDAQ",
                "industry": "Consumer Electronics", "sector": "Technology",
                "country": "US", "currency": "USD", "mktCap": 3.51e12,
                "website": "https://apple.com", "description": "Consumer electronics."}])
        if "stock_news" in path:
            return httpx.Response(200, json=[{
                "title": "FMP: Apple expands India retail", "url": "https://news.test/f1",
                "publishedDate": "2026-08-21 09:30:00", "site": "fmp",
                "text": "Two new stores.", "symbol": "AAPL"}])
        return httpx.Response(200, json=[])

    def _tiingo(self, path, params, request):
        if path.startswith("/iex/"):
            symbol = path.split("/iex/")[1]
            price = price_for(symbol)
            return httpx.Response(200, json=[{
                "ticker": symbol, "last": price, "open": price - 1.2,
                "high": price + 2.4, "low": price - 3.1, "prevClose": price - 1.85,
                "volume": 48000000, "timestamp": "2026-08-21T20:00:00Z"}])
        if "/prices" in path:
            symbol = path.split("/daily/")[1].split("/")[0]
            price = price_for(symbol)
            return httpx.Response(200, json=[
                {"date": f"2026-08-{d:02d}T00:00:00Z", "open": price+d-20,
                 "high": price+d-18, "low": price+d-22, "close": price+d-19,
                 "volume": 4e7} for d in range(1, 22)])
        if "news" in path:
            return httpx.Response(200, json=[{
                "title": "Tiingo: rates steady", "url": "https://news.test/t1",
                "publishedDate": "2026-08-21T11:00:00Z", "source": "tiingo",
                "description": "Fed on hold.", "tickers": ["spy"]}])
        return httpx.Response(200, json=[])

    def _marketstack(self, path, params, request):
        symbol = (params.get("symbols") or "AAPL").split(",")[0]
        price = price_for(symbol)
        if "latest" in path:
            return httpx.Response(200, json={"data": [{
                "symbol": symbol, "open": price - 1.2, "high": price + 2.4,
                "low": price - 3.1, "close": price, "volume": 48000000,
                "exchange": "XNAS", "date": "2026-08-21T00:00:00+0000"}]})
        return httpx.Response(200, json={"data": [
            {"symbol": symbol, "open": price+d-20, "high": price+d-18,
             "low": price+d-22, "close": price+d-19, "volume": 4e7,
             "date": f"2026-08-{d:02d}T00:00:00+0000"} for d in range(21, 0, -1)]})

    def _fred(self, path, params, request):
        return httpx.Response(200, json={"observations": [
            {"date": f"2026-08-{d:02d}", "value": f"{4.20 + d * 0.01:.2f}"}
            for d in range(21, 0, -1)] + [{"date": "2026-07-31", "value": "."}]})

    def _newsapi(self, path, params, request):
        return httpx.Response(200, json={"status": "ok", "articles": [{
            "title": "NewsAPI: markets mixed", "url": "https://news.test/n1",
            "publishedAt": "2026-08-21T13:00:00Z", "source": {"name": "AP"},
            "description": "Indexes little changed."}]})

    def _stooq(self, path, params, request):
        symbol = params.get("s", "aapl.us")
        price = price_for(symbol)
        if path.startswith("/q/l"):
            return httpx.Response(200, text=(
                "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                f"{symbol.upper()},2026-08-21,22:00:07,{price-1.2},{price+2.4},"
                f"{price-3.1},{price},48000000\n"))
        return httpx.Response(200, text=(
            "Date,Open,High,Low,Close,Volume\n" + "".join(
                f"2026-08-{d:02d},{price+d-20},{price+d-18},{price+d-22},"
                f"{price+d-19},40000000\n" for d in range(1, 22))))

    def _yahoo(self, path, params, request):
        symbol = path.rstrip("/").split("/")[-1]
        price = price_for(symbol)
        now = int(time.time())
        count = 21
        stamps = [now - (count - i) * 86400 for i in range(count)]
        closes = [price + i - 19 for i in range(count)]
        closes[5] = None  # a holiday gap, which real payloads contain
        return httpx.Response(200, json={"chart": {"error": None, "result": [{
            "meta": {"currency": "USD", "symbol": symbol, "exchangeName": "NMS",
                     "regularMarketPrice": price, "chartPreviousClose": price - 1.85,
                     "regularMarketDayHigh": price + 2.4,
                     "regularMarketDayLow": price - 3.1,
                     "regularMarketVolume": 48000000,
                     "regularMarketTime": now, "longName": "Apple Inc",
                     "fullExchangeName": "NasdaqGS"},
            "timestamp": stamps,
            "indicators": {"quote": [{
                "open": [None if c is None else c - 1 for c in closes],
                "high": [None if c is None else c + 1 for c in closes],
                "low": [None if c is None else c - 2 for c in closes],
                "close": closes,
                "volume": [None if c is None else 4e7 for c in closes]}]},
        }]}})

    def _yahoo_search(self, path, params, request):
        return httpx.Response(200, json={"quotes": [
            {"symbol": "AAPL", "longname": "Apple Inc", "exchDisp": "NASDAQ",
             "quoteType": "EQUITY"}]})

    def _coingecko(self, path, params, request):
        if "simple/price" in path:
            coin = (params.get("ids") or "bitcoin").split(",")[0]
            return httpx.Response(200, json={coin: {
                "usd": 64210.5, "usd_24h_change": 2.4137, "usd_24h_vol": 2.1e10}})
        if "market_chart" in path:
            now_ms = int(time.time() * 1000)
            return httpx.Response(200, json={"prices": [
                [now_ms - (20 - i) * 86400000, 60000.0 + i * 200] for i in range(21)]})
        return httpx.Response(200, json={})

    def _sec_www(self, path, params, request):
        if "company_tickers" in path:
            return httpx.Response(200, json={
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}})
        if path.endswith(".rss") or "feed" in path:
            return self._rss(path, params, request)
        return httpx.Response(404)

    def _sec_data(self, path, params, request):
        return httpx.Response(200, json={"filings": {"recent": {
            "form": ["10-Q", "8-K", "4", "10-K"],
            "filingDate": ["2026-08-01", "2026-07-15", "2026-07-02", "2025-11-01"],
            "accessionNumber": ["0000320193-26-000081", "0000320193-26-000075",
                                "0000320193-26-000070", "0000320193-25-000110"],
            "primaryDocument": ["aapl-20260630.htm", "aapl-8k.htm", "form4.xml",
                                "aapl-20250928.htm"],
            "primaryDocDescription": ["10-Q", "8-K", "FORM 4", "10-K"],
        }}})

    def _rss(self, path, params, request):
        symbol = params.get("s", "MARKET")
        return httpx.Response(200, text=f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>{symbol}: shares climb on upgrade</title>
<link>https://news.test/rss1</link>
<pubDate>Fri, 21 Aug 2026 15:04:00 +0000</pubDate>
<description>&lt;p&gt;Analysts raise targets.&lt;/p&gt;</description></item>
<item><title>Market wrap: indexes edge higher</title>
<link>https://news.test/rss2</link>
<pubDate>Fri, 21 Aug 2026 14:00:00 +0000</pubDate>
<description>Breadth improved.</description></item>
</channel></rss>""")


def make_env(keys_per_provider: int = 3) -> dict[str, str]:
    """An environment holding a realistic multi-key spread."""
    def spread(stem: str, count: int) -> str:
        return ",".join(f"{stem.lower()}_key_{i:02d}" for i in range(1, count + 1))

    return {
        "MKT_FINNHUB_KEYS": spread("FINNHUB", keys_per_provider),
        "MKT_ALPHAVANTAGE_KEYS": spread("ALPHAVANTAGE", keys_per_provider),
        "MKT_TWELVEDATA_KEYS": spread("TWELVEDATA", keys_per_provider),
        "MKT_POLYGON_KEYS": spread("POLYGON", keys_per_provider),
        "MKT_FMP_KEYS": spread("FMP", keys_per_provider),
        "MKT_TIINGO_KEYS": spread("TIINGO", keys_per_provider),
        "MKT_MARKETSTACK_KEYS": spread("MARKETSTACK", keys_per_provider),
        "MKT_FRED_KEYS": spread("FRED", keys_per_provider),
        "MKT_NEWSAPI_KEYS": spread("NEWSAPI", keys_per_provider),
    }
