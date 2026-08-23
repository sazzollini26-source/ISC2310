"""Newswire RSS.

RSS feeds are published to be fetched by clients, which makes this the one
scraping lane that is unambiguously fair game. Still rate-limited per host and
still robots-checked, because a feed being public is not a licence to hammer it.
"""
from __future__ import annotations

import asyncio
import re
from xml.etree import ElementTree

from ..models import NewsItem
from ..providers.base import Provider, parse_date

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(_TAG.sub("", text).split())


def parse_feed(xml_text: str, fallback_publisher: str = "") -> list[NewsItem]:
    """Parse RSS 2.0 or Atom into NewsItems. Returns [] on malformed XML."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError:
        return []

    atom = "{http://www.w3.org/2005/Atom}"
    items: list[NewsItem] = []

    # RSS 2.0
    for node in root.iter("item"):
        title = _clean(node.findtext("title"))
        if not title:
            continue
        source_node = node.find("source")
        items.append(NewsItem(
            title=title,
            url=(node.findtext("link") or "").strip(),
            published=parse_date(node.findtext("pubDate") or node.findtext("date") or ""),
            publisher=_clean(source_node.text if source_node is not None else None)
            or fallback_publisher,
            summary=_clean(node.findtext("description"))[:400],
        ))

    # Atom
    for node in root.iter(f"{atom}entry"):
        title = _clean(node.findtext(f"{atom}title"))
        if not title:
            continue
        link = ""
        for link_node in node.findall(f"{atom}link"):
            if link_node.get("rel") in (None, "alternate"):
                link = link_node.get("href", "")
                break
        items.append(NewsItem(
            title=title, url=link,
            published=parse_date(
                node.findtext(f"{atom}updated") or node.findtext(f"{atom}published") or ""
            ),
            publisher=fallback_publisher,
            summary=_clean(node.findtext(f"{atom}summary"))[:400],
        ))
    return items


class RssScraper(Provider):
    """Fans out across several public feeds and merges them newest-first."""

    name = "rss"

    # Per-symbol feed template plus general market feeds.
    SYMBOL_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    MARKET_FEEDS: tuple[tuple[str, str], ...] = (
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
        ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    )

    async def _fetch_feed(self, url: str, publisher: str) -> list[NewsItem]:
        try:
            response = await self.http.get(url, ttl=300.0, check_robots=True, retries=1)
        except Exception:
            # One dead feed must not take down the whole news panel.
            return []
        items = parse_feed(response.text, publisher)
        for item in items:
            item.source = self.name
        return items

    async def news(self, symbol: str | None = None, limit: int = 20) -> list[NewsItem]:
        if symbol:
            feeds = [(symbol.upper(), self.SYMBOL_FEED.format(symbol=symbol.upper()))]
        else:
            feeds = list(self.MARKET_FEEDS)

        batches = await asyncio.gather(
            *(self._fetch_feed(url, publisher) for publisher, url in feeds)
        )
        merged: list[NewsItem] = []
        seen: set[str] = set()
        for batch in batches:
            for item in batch:
                fingerprint = item.url or item.title
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                if symbol:
                    item.symbols = [symbol.upper()]
                merged.append(item)
        merged.sort(key=lambda n: n.published, reverse=True)
        return merged[:limit]
