# market-terminal

A Bloomberg-style market data terminal for the console. Watchlist, candle
charts, merged newswire, SEC filings and company fundamentals — pulled from 14
sources behind one routing layer that rotates API keys, respects rate limits,
and fails over when a source goes down.

```
╭────────────────────────────────────╮╭────────────────────────────────────╮
│ WATCHLIST                          ││ CHART AAPL 1d                      │
│ SYM    LAST      CHG     CHG%  SRC ││   234.15 │              │█         │
│ AAPL   232.15   +1.85  +0.80%  finn││   232.62 │            │███         │
│ MSFT   418.90   +1.85  +0.80%  finn││   231.08 │           │██│          │
╰────────────────────────────────────╯╰────────────────────────────────────╯
 keys 27/27 ready │ 14/14 sources up │ cache 38% of 214 │ 17:22:02 UTC
```

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # optional — it runs with no keys at all
python run.py                 # launch the terminal
```

No credentials? It still works. Stooq, Yahoo, CoinGecko, SEC EDGAR and the RSS
newswires need no keys, and the router treats them as the floor it falls back
to when everything else is throttled.

## Commands

Inside the terminal, type a ticker or a `/command`. `AAPL <GO>` works too.

| Command | Effect |
|---|---|
| `AAPL` | load a security into the chart, news and detail panes |
| `/add TSLA` · `/rm TSLA` | edit the watchlist |
| `/chart 1d` | interval: `5m` `15m` `1h` `1d` `1wk` `1mo` |
| `/news` · `/filings` | refresh those panes for the active symbol |
| `/search apple` | symbol lookup across providers |
| `/keys` | per-key health: budget used, cooldowns, 429 counts |
| `/status` | per-provider latency, success rate, last error |

Keys: `r` refresh · `k` keys · `s` status · `?` help · `q` quit

## Headless use

Every capability is reachable without the TUI, for cron jobs and pipelines:

```bash
python run.py quote AAPL MSFT NVDA
python run.py quote AAPL --json
python run.py history AAPL --interval 1d --lookback 200
python run.py news AAPL --limit 10
python run.py filings AAPL --forms 10-K 10-Q
python run.py keys              # pool health; never prints a full key
python run.py doctor            # probe every source, report what works
```

`doctor` is the first thing to run when something looks wrong — it tells you
which sources answered, which failed, and how slowly.

## How the key rotation works

This is the part that matters if you hold a lot of free-tier keys. Each key is
an independent bucket, not a shared pile:

- **Per-key token bucket** sized to the vendor's published limit. Alpha Vantage
  is 5/min *and* 25/day per key, so five keys is genuinely 25 calls/min and 125
  calls/day — the pool tracks both ceilings separately.
- **Least-recently-used selection.** Load spreads evenly instead of hammering
  key #1 until it throttles.
- **Cooldown on 429**, honouring `Retry-After` when the server sends one and
  backing off exponentially when it doesn't. A repeatedly-throttled key drops
  out of rotation on its own.
- **Fatal-auth detection.** A 401/403 means the key is *bad*, not busy, so it
  is benched for an hour rather than burning the retry budget.
- **Soft rate-limit detection.** Several vendors answer HTTP 200 with a
  refusal in the body. If that reads as success the key never gets benched and
  the whole pool drains silently, so those bodies are caught and treated as
  429s.
- **Pool exhaustion → provider failover.** When every key for a provider is
  spent, the router moves to the next provider entirely instead of waiting.

Add keys in any of these spellings (see `.env.example`):

```bash
MKT_FINNHUB_KEYS=k1,k2,k3     # list
MKT_FINNHUB_KEY_1=k1          # numbered, up to 50
FINNHUB_API_KEY=k1            # bare vendor name
```

Keys are masked (`fh_k...y_01`) everywhere they surface — logs, `/keys`,
`status` JSON. There is no code path that prints one in full.

## How routing works

Providers declare capabilities (`quote`, `history`, `news`, `profile`,
`search`, `filings`) and a base priority. For each request the router builds an
ordered candidate list and walks it until one answers.

Ordering is priority plus live health: a Laplace-smoothed success rate and
average latency. The smoothing matters — a raw rate would let one transient
failure exile a provider permanently, since a provider that never gets called
can never prove it recovered. Health also decays while a source sits idle, so
anything demoted gets re-probed later.

News is the exception: it *merges* across every available source and dedupes
by URL, because breadth is the point of a news panel.

## Sources

| Source | Keys | Provides |
|---|---|---|
| Yahoo Finance | keyless | quote, history, search, profile |
| Stooq | keyless | quote, history |
| CoinGecko | keyless | crypto quote, history |
| SEC EDGAR | keyless | filings |
| RSS newswires | keyless | news |
| Finnhub | yes | quote, profile, news, search |
| Alpha Vantage | yes | quote, history, search, profile |
| Twelve Data | yes | quote, history, search |
| Polygon | yes | quote, history, news |
| FMP | yes | quote, history, profile, news |
| Tiingo | yes | quote, history, news |
| Marketstack | yes | quote, history |
| FRED | yes | macro series |
| NewsAPI | yes | news |

## On scraping

The scraping lane deliberately points only at sources that permit it:

- **SEC EDGAR** publishes filings for exactly this purpose. Access is
  conditional on a descriptive `User-Agent` carrying a contact address and a
  10 req/s ceiling — both enforced in code. Set `MKT_CONTACT_EMAIL` or filings
  will fail.
- **RSS feeds** are published to be fetched by clients.

Both go through `robots.txt` checks and a per-host rate limiter. What this tool
does *not* do is scrape brokerages or terminal vendors: that needs credentialed
access, breaks their terms outright, and real-time market data carries
redistribution licensing that free-tier keys do not grant. If you need
authoritative real-time data, the vendor feeds above are the licensed path.

Free-tier data is typically delayed (Polygon is 15 minutes; Stooq and
Marketstack are end-of-day). The `SRC` column and `quote src` line always name
which source produced the number on screen, so you can tell what you're
looking at.

## Architecture

```
mkt/
  models.py       Quote · Bar · Series · NewsItem · Filing · Profile
  config.py       .env loading, provider specs, runtime settings
  keypool.py      token buckets, LRU rotation, cooldowns, pool exhaustion
  http.py         retries, backoff, response cache, robots.txt, host limiter
  router.py       capability routing, health scoring, failover, news merge
  chart.py        candle/sparkline rendering (pure, testable)
  cli.py          headless commands
  providers/
    base.py       Provider contract + shared parsers
    keyless.py    Stooq · Yahoo · CoinGecko
    keyed.py      the nine credentialed vendors
  scrapers/
    edgar.py      SEC filings
    rss.py        newswire feeds
  ui/app.py       the Textual terminal
```

## Tests

```bash
python -m pytest tests/ -q      # 101 tests
```

The suite runs against `tests/vendorsim.py`, a fake internet that emulates each
vendor's real response shape and can inject 429s, 500s, soft rate-limit bodies
and auth failures on demand. That's what makes rotation and failover actually
testable: `test_router.py` kills ten providers at once and asserts the terminal
still prints a price.

The UI tests drive the real Textual app headlessly — booting it, running
commands, and asserting on rendered output, including that no full API key ever
reaches the screen.
