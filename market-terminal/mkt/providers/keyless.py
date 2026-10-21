"""Sources that need no credentials. These are the floor the router falls back
to when every key in every pool is spent."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from ..models import Bar, Profile, Quote, Series
from .base import Provider, SymbolNotFound, epoch, num, parse_date


class StooqProvider(Provider):
    """Stooq's free CSV endpoints. Delayed, but reliable and unmetered."""

    name = "stooq"
    BASE = "https://stooq.com"

    def _symbol(self, symbol: str) -> str:
        # Stooq namespaces US tickers with a .us suffix.
        s = symbol.lower().strip()
        return s if "." in s or "^" in s else f"{s}.us"

    async def quote(self, symbol: str) -> Quote:
        base = self.settings.base_url(self.name, self.BASE)
        response = await self.http.get(
            f"{base}/q/l/",
            params={"s": self._symbol(symbol), "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            ttl=self.settings.cache_ttl,
        )
        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            raise SymbolNotFound(f"stooq: no data for {symbol}")
        row = rows[0]
        close = num(row.get("Close"))
        if close is None:
            raise SymbolNotFound(f"stooq: no price for {symbol}")
        stamp = f"{row.get('Date', '')} {row.get('Time', '') or '00:00:00'}".strip()
        return Quote(
            symbol=symbol,
            price=close,
            open=num(row.get("Open")),
            high=num(row.get("High")),
            low=num(row.get("Low")),
            volume=num(row.get("Volume")),
            source=self.name,
            ts=parse_date(stamp),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        code = {"1d": "d", "1wk": "w", "1mo": "m"}.get(interval, "d")
        base = self.settings.base_url(self.name, self.BASE)
        response = await self.http.get(
            f"{base}/q/d/l/",
            params={"s": self._symbol(symbol), "i": code},
            ttl=300.0,
        )
        bars: list[Bar] = []
        for row in csv.DictReader(io.StringIO(response.text)):
            close = num(row.get("Close"))
            if close is None:
                continue
            bars.append(Bar(
                ts=parse_date(row.get("Date", "")),
                open=num(row.get("Open")) or close,
                high=num(row.get("High")) or close,
                low=num(row.get("Low")) or close,
                close=close,
                volume=num(row.get("Volume")) or 0.0,
            ))
        if not bars:
            raise SymbolNotFound(f"stooq: no history for {symbol}")
        return Series(symbol, interval, bars[-lookback:], source=self.name)


class YahooProvider(Provider):
    """Yahoo's public chart JSON. Unofficial — rate limit politely."""

    name = "yahoo"
    BASE = "https://query1.finance.yahoo.com"
    SEARCH = "https://query2.finance.yahoo.com"

    RANGE_FOR = {
        "1m": "1d", "5m": "5d", "15m": "5d", "30m": "1mo",
        "1h": "3mo", "1d": "1y", "1wk": "5y", "1mo": "10y",
    }

    async def _chart(self, symbol: str, interval: str, rng: str, ttl: float) -> dict:
        base = self.settings.base_url(self.name, self.BASE)
        payload = await self.http.get_json(
            f"{base}/v8/finance/chart/{symbol.upper()}",
            params={"range": rng, "interval": interval},
            ttl=ttl,
        )
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            raise SymbolNotFound(f"yahoo: {chart['error'].get('description', symbol)}")
        results = chart.get("result") or []
        if not results:
            raise SymbolNotFound(f"yahoo: no result for {symbol}")
        return results[0]

    async def quote(self, symbol: str) -> Quote:
        result = await self._chart(symbol, "1d", "5d", self.settings.cache_ttl)
        meta = result.get("meta") or {}
        price = num(meta.get("regularMarketPrice"))
        if price is None:
            raise SymbolNotFound(f"yahoo: no price for {symbol}")
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=num(meta.get("chartPreviousClose")) or num(meta.get("previousClose")),
            high=num(meta.get("regularMarketDayHigh")),
            low=num(meta.get("regularMarketDayLow")),
            volume=num(meta.get("regularMarketVolume")),
            currency=meta.get("currency") or "USD",
            venue=meta.get("exchangeName") or meta.get("fullExchangeName"),
            source=self.name,
            ts=epoch(meta.get("regularMarketTime")) if meta.get("regularMarketTime")
            else datetime.now(timezone.utc),
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        rng = self.RANGE_FOR.get(interval, "1y")
        result = await self._chart(symbol, interval, rng, 300.0)
        stamps = result.get("timestamp") or []
        quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote_block.get("open") or []
        highs = quote_block.get("high") or []
        lows = quote_block.get("low") or []
        closes = quote_block.get("close") or []
        volumes = quote_block.get("volume") or []
        bars: list[Bar] = []
        for i, stamp in enumerate(stamps):
            close = num(closes[i]) if i < len(closes) else None
            if close is None:
                continue  # Yahoo pads holidays and halts with nulls.
            bars.append(Bar(
                ts=epoch(stamp),
                open=num(opens[i] if i < len(opens) else None) or close,
                high=num(highs[i] if i < len(highs) else None) or close,
                low=num(lows[i] if i < len(lows) else None) or close,
                close=close,
                volume=num(volumes[i] if i < len(volumes) else None) or 0.0,
            ))
        if not bars:
            raise SymbolNotFound(f"yahoo: no history for {symbol}")
        return Series(symbol, interval, bars[-lookback:], source=self.name)

    async def profile(self, symbol: str) -> Profile:
        result = await self._chart(symbol, "1d", "5d", 600.0)
        meta = result.get("meta") or {}
        return Profile(
            symbol=symbol,
            name=meta.get("longName") or meta.get("shortName") or symbol.upper(),
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            currency=meta.get("currency") or "USD",
            source=self.name,
        )

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        base = self.settings.base_url(f"{self.name}_search", self.SEARCH)
        payload = await self.http.get_json(
            f"{base}/v1/finance/search",
            params={"q": query, "quotesCount": limit, "newsCount": 0},
            ttl=600.0,
        )
        out = []
        for item in (payload or {}).get("quotes", [])[:limit]:
            out.append({
                "symbol": item.get("symbol", ""),
                "name": item.get("longname") or item.get("shortname") or "",
                "exchange": item.get("exchDisp") or item.get("exchange") or "",
                "type": item.get("quoteType") or "",
                "source": self.name,
            })
        return out


class CoinGeckoProvider(Provider):
    """Crypto spot prices, keyless."""

    name = "coingecko"
    BASE = "https://api.coingecko.com/api/v3"

    # Ticker shorthand to CoinGecko's internal ids.
    IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2",
        "DOT": "polkadot", "MATIC": "matic-network", "LINK": "chainlink",
        "LTC": "litecoin", "BCH": "bitcoin-cash", "USDT": "tether",
        "USDC": "usd-coin", "BNB": "binancecoin", "TRX": "tron",
    }

    def _coin_id(self, symbol: str) -> str:
        key = symbol.upper().replace("-USD", "").replace("USD", "").strip("-")
        if key in self.IDS:
            return self.IDS[key]
        if symbol.upper() in self.IDS:
            return self.IDS[symbol.upper()]
        return symbol.lower().replace("-usd", "")

    async def quote(self, symbol: str) -> Quote:
        coin = self._coin_id(symbol)
        base = self.settings.base_url(self.name, self.BASE)
        payload = await self.http.get_json(
            f"{base}/simple/price",
            params={
                "ids": coin, "vs_currencies": "usd",
                "include_24hr_change": "true", "include_24hr_vol": "true",
            },
            ttl=self.settings.cache_ttl,
        )
        block = (payload or {}).get(coin)
        if not block:
            raise SymbolNotFound(f"coingecko: unknown coin {symbol}")
        price = num(block.get("usd"))
        if price is None:
            raise SymbolNotFound(f"coingecko: no price for {symbol}")
        change_pct = num(block.get("usd_24h_change"))
        prev = price / (1 + change_pct / 100.0) if change_pct not in (None, -100.0) else None
        return Quote(
            symbol=symbol, price=price, change_pct=change_pct, prev_close=prev,
            volume=num(block.get("usd_24h_vol")), currency="USD",
            venue="CRYPTO", source=self.name,
        )

    async def history(self, symbol: str, interval: str = "1d", lookback: int = 120) -> Series:
        coin = self._coin_id(symbol)
        base = self.settings.base_url(self.name, self.BASE)
        payload = await self.http.get_json(
            f"{base}/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": max(1, min(lookback, 365))},
            ttl=300.0,
        )
        points = (payload or {}).get("prices") or []
        if not points:
            raise SymbolNotFound(f"coingecko: no history for {symbol}")
        # market_chart returns close-only points; synthesise flat OHLC.
        bars = [
            Bar(ts=epoch(ms), open=float(price), high=float(price),
                low=float(price), close=float(price))
            for ms, price in points if price is not None
        ]
        return Series(symbol, interval, bars[-lookback:], source=self.name)
