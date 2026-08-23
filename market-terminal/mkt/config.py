"""Configuration: .env loading, provider specs, and runtime settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Provider registry metadata
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Static facts about a data source.

    ``rate`` is (calls, per_seconds) enforced *per key*. ``daily_cap`` is the
    free-tier ceiling where the vendor publishes one; 0 means unmetered.
    ``env`` lists the environment variable stems checked for keys.
    """

    name: str
    keyless: bool = False
    env: tuple[str, ...] = ()
    rate: tuple[int, float] = (60, 60.0)
    daily_cap: int = 0
    capabilities: tuple[str, ...] = ("quote",)
    priority: int = 50  # lower is tried first
    notes: str = ""


# Free-tier limits as published by each vendor. Adjust if you hold a paid plan;
# the pool enforces whatever is written here.
PROVIDER_SPECS: dict[str, ProviderSpec] = {
    # ---- keyless sources: always available, used as the safety net ----
    "stooq": ProviderSpec(
        "stooq", keyless=True, rate=(20, 60.0),
        capabilities=("quote", "history"), priority=30,
        notes="Free CSV endpoint, delayed EOD/intraday.",
    ),
    "yahoo": ProviderSpec(
        "yahoo", keyless=True, rate=(30, 60.0),
        capabilities=("quote", "history", "search", "profile"), priority=20,
        notes="Public chart JSON. Unofficial; throttle politely.",
    ),
    "coingecko": ProviderSpec(
        "coingecko", keyless=True, rate=(10, 60.0),
        capabilities=("quote", "history"), priority=25,
        notes="Crypto. Free tier ~10-30 calls/min.",
    ),
    # ---- keyed sources ----
    "alphavantage": ProviderSpec(
        "alphavantage", env=("ALPHAVANTAGE", "ALPHA_VANTAGE"),
        rate=(5, 60.0), daily_cap=25,
        capabilities=("quote", "history", "search", "profile"), priority=40,
        notes="5 calls/min, 25/day per key. Rotation matters most here.",
    ),
    "finnhub": ProviderSpec(
        "finnhub", env=("FINNHUB",), rate=(60, 60.0),
        capabilities=("quote", "profile", "news", "search"), priority=10,
        notes="60 calls/min on the free tier.",
    ),
    "twelvedata": ProviderSpec(
        "twelvedata", env=("TWELVEDATA", "TWELVE_DATA"),
        rate=(8, 60.0), daily_cap=800,
        capabilities=("quote", "history", "search"), priority=35,
    ),
    "polygon": ProviderSpec(
        "polygon", env=("POLYGON",), rate=(5, 60.0),
        capabilities=("quote", "history", "news"), priority=45,
        notes="5 calls/min free; 15-minute delayed data.",
    ),
    "fmp": ProviderSpec(
        "fmp", env=("FMP", "FINANCIALMODELINGPREP"),
        rate=(10, 60.0), daily_cap=250,
        capabilities=("quote", "history", "profile", "news"), priority=38,
    ),
    "tiingo": ProviderSpec(
        "tiingo", env=("TIINGO",), rate=(50, 3600.0), daily_cap=500,
        capabilities=("quote", "history", "news"), priority=42,
        notes="Hourly rather than per-minute limiting.",
    ),
    "marketstack": ProviderSpec(
        "marketstack", env=("MARKETSTACK",), rate=(5, 60.0), daily_cap=100,
        capabilities=("quote", "history"), priority=60,
    ),
    "fred": ProviderSpec(
        "fred", env=("FRED",), rate=(120, 60.0),
        capabilities=("history",), priority=50,
        notes="Macro series (rates, CPI, unemployment).",
    ),
    "newsapi": ProviderSpec(
        "newsapi", env=("NEWSAPI", "NEWS_API"), rate=(100, 86400.0), daily_cap=100,
        capabilities=("news",), priority=55,
    ),
    # ---- scrapers ----
    "edgar": ProviderSpec(
        "edgar", keyless=True, rate=(8, 10.0),
        capabilities=("filings",), priority=15,
        notes="SEC requires a descriptive User-Agent; 10 req/s hard ceiling.",
    ),
    "rss": ProviderSpec(
        "rss", keyless=True, rate=(20, 60.0),
        capabilities=("news",), priority=18,
        notes="Public newswire RSS feeds.",
    ),
}


# --------------------------------------------------------------------------
# .env loading
# --------------------------------------------------------------------------


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Minimal .env reader — no dependency on python-dotenv.

    Supports ``KEY=value``, ``export KEY=value``, ``#`` comments and quoted
    values. Returns the mapping it applied.
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / ".env"
    path = Path(path)
    applied: dict[str, str] = {}
    if not path.exists():
        return applied
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
        applied[key] = value
    return applied


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    """Runtime knobs, all overridable from the environment."""

    watchlist: list[str] = field(default_factory=list)
    quote_refresh: float = 15.0
    news_refresh: float = 120.0
    stale_after: float = 90.0
    http_timeout: float = 12.0
    max_retries: int = 3
    cache_ttl: float = 20.0
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "market-terminal")
    user_agent: str = "market-terminal/1.0"
    contact_email: str = ""
    respect_robots: bool = True
    offline: bool = False
    base_urls: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        raw_watch = os.environ.get(
            "MKT_WATCHLIST", "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,SPY"
        )
        watch = [s.strip().upper() for s in raw_watch.split(",") if s.strip()]
        contact = os.environ.get("MKT_CONTACT_EMAIL", "")
        ua = os.environ.get("MKT_USER_AGENT", "")
        if not ua:
            # SEC and most publishers want a contactable UA string.
            ua = f"market-terminal/1.0 ({contact})" if contact else "market-terminal/1.0"
        cache_dir = os.environ.get("MKT_CACHE_DIR", "")
        settings = cls(
            watchlist=watch,
            quote_refresh=_env_float("MKT_QUOTE_REFRESH", 15.0),
            news_refresh=_env_float("MKT_NEWS_REFRESH", 120.0),
            stale_after=_env_float("MKT_STALE_AFTER", 90.0),
            http_timeout=_env_float("MKT_HTTP_TIMEOUT", 12.0),
            max_retries=_env_int("MKT_MAX_RETRIES", 3),
            cache_ttl=_env_float("MKT_CACHE_TTL", 20.0),
            cache_dir=Path(cache_dir) if cache_dir
            else Path.home() / ".cache" / "market-terminal",
            user_agent=ua,
            contact_email=contact,
            respect_robots=_env_bool("MKT_RESPECT_ROBOTS", True),
            offline=_env_bool("MKT_OFFLINE", False),
        )
        # MKT_BASE_URL_<PROVIDER> repoints one provider, which is how the test
        # suite aims the whole stack at a local fixture server.
        for env_key, value in os.environ.items():
            if env_key.startswith("MKT_BASE_URL_") and value:
                settings.base_urls[env_key[len("MKT_BASE_URL_"):].lower()] = value.rstrip("/")
        return settings

    def base_url(self, provider: str, default: str) -> str:
        return self.base_urls.get(provider.lower(), default)
