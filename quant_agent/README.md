# Quantitative Trading Agent

A watchlist evaluation and paper-trading stack built on the Python standard
library only — no numpy, pandas, or broker SDK required to run or test it.

```
data -> indicators -> sentiment -> risk -> broker -> journal
```

## Scope and honest limits

Read this before using anything here.

| Capability | Status |
|---|---|
| Indicator + sentiment engine | Implemented and tested |
| Multi-layer risk controls (5% max stop) | Implemented and tested |
| Paper execution + persistent account | Implemented and tested |
| Trade journal (JSONL + Markdown) | Implemented and tested |
| Real-time market data | **Not connected.** Optional `yfinance` provider gives delayed *historical* bars |
| Live broker execution | **Not implemented.** `LiveBroker` refuses to construct |

The default data provider is `synthetic` — deterministic simulated price paths.
It is not market data, it is labelled as such at every layer, and the risk gate
refuses to let synthetic data back a live order. Any figure produced by a
synthetic run is illustrative only.

This is research and paper-trading tooling. It is not investment advice, it has
not been validated against live markets, and nothing here should route real
capital without a venue integration, a backtest, and a human accountable for it.

## Usage

```bash
# Score the default watchlist, risk-check only, submit nothing
python -m quant_agent --dry-run

# Paper trade against simulated data
python -m quant_agent --mode paper --ignore-market-hours

# Use your own OHLCV history: data/TSLA.csv with date,open,high,low,close,volume
python -m quant_agent --provider csv --data-dir data

# Write a Markdown session summary
python -m quant_agent --dry-run --markdown journal/today.md
```

`--mode live` exits with an error by design.

## The risk gauntlet

Every proposed order passes five ordered layers. One failure blocks the order,
and the full check list is journalled either way — including the reasons a trade
did *not* happen.

| Layer | Controls |
|---|---|
| 1. Data quality | Minimum history, staleness, price sanity, no synthetic data behind live orders |
| 2. Conviction + stop-loss | `\|sentiment\|` threshold, **hard 5% stop ceiling**, minimum stop distance |
| 3. Sizing | 1% risk-per-trade budget, 20% single-name concentration cap |
| 4. Portfolio | Max open positions, gross exposure ≤ 100% (no leverage), daily-loss kill switch, duplicate-position block |
| 5. Execution | Min/max order notional |

### The 5% stop-loss ceiling

Stops are volatility-aware — 2× ATR(14) — but the ceiling is absolute:

```python
stop_pct = min(2.0 * atr_pct, max_stop_loss_pct)   # never exceeds 0.05
```

When volatility argues for a wider stop, **the stop does not widen**. The
position is sized against 5% instead, so an unusually violent name yields a
smaller position rather than a larger loss. A stop that is degenerately tight
is rejected rather than silently nudged. `test_risk.py` pins this invariant
across a volatility sweep from 0.1% to 200%.

Note the interaction between limits: with a 1% risk budget and a stop capped at
5%, the 20% concentration cap is what binds under default settings. Both are
enforced; whichever is smaller wins.

## Sentiment scoring

A composite in `[-1, +1]` from four bounded, weighted components:

| Component | Weight | Signal |
|---|---|---|
| Trend | 0.35 | SMA20 vs SMA50 separation |
| Momentum | 0.25 | RSI(14) displacement from 50 |
| MACD | 0.25 | Histogram, normalised by price |
| Volume | 0.15 | Participation vs 20-day average, signed by ROC |

Each component is squashed with `tanh`, so an outlier saturates instead of
dominating. Elevated ATR then damps conviction toward zero — it can shrink a
signal but never flip its sign. `|composite| >= 0.25` is required to trade.

## Layout

| File | Purpose |
|---|---|
| `config.py` | Watchlist and every risk limit in one place |
| `indicators.py` | SMA, EMA, RSI, MACD, ATR, Bollinger, volatility, ROC |
| `data.py` | `Bar`/`Series` types; synthetic, CSV, and yfinance providers |
| `sentiment.py` | Indicator snapshot and composite scoring with rationale |
| `risk.py` | The five-layer gauntlet and position sizing |
| `broker.py` | `BrokerAdapter` interface, `PaperBroker`, inert `LiveBroker` |
| `journal.py` | Append-only JSONL record plus Markdown rendering |
| `agent.py` | Orchestration and the per-symbol run loop |
| `cli.py` | `python -m quant_agent` |

## Tests

```bash
python -m unittest discover -s quant_agent/tests -t .
```

55 tests, no third-party dependencies.

## Going live (deliberately manual)

1. Implement `BrokerAdapter` against your venue's API.
2. Supply credentials out of band — never commit them.
3. Connect a real-time data provider; the synthetic provider is blocked in live mode.
4. Backtest and paper trade first.
5. Enable it explicitly, with a human accountable for the capital.
