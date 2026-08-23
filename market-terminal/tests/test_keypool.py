"""Key rotation, budgets and benching."""
from __future__ import annotations

import pytest

from mkt.config import PROVIDER_SPECS
from mkt.keypool import KeyPool, KeyRegistry, PoolExhausted, TokenBucket
from tests.vendorsim import make_env


def test_twenty_keys_load_from_env():
    env = {
        "MKT_FINNHUB_KEYS": ",".join(f"fh{i}" for i in range(1, 9)),
        "MKT_ALPHAVANTAGE_KEYS": ",".join(f"av{i}" for i in range(1, 6)),
        "MKT_POLYGON_KEY_1": "pg1", "MKT_POLYGON_KEY_2": "pg2", "MKT_POLYGON_KEY_3": "pg3",
        "TWELVEDATA_API_KEY": "td1",
        "MKT_FMP_KEYS": "fmp1,fmp2",
        "MKT_TIINGO_KEY": "tg1",
    }
    registry = KeyRegistry.from_env(env)
    assert registry.total_keys() == 20
    assert registry.get("finnhub").size == 8
    assert registry.get("polygon").size == 3


def test_all_three_env_spellings_are_accepted():
    registry = KeyRegistry.from_env({
        "MKT_FINNHUB_KEYS": "a,b",
        "MKT_FINNHUB_KEY_3": "c",
        "FINNHUB_API_KEY": "d",
    })
    values = {k.value for k in registry.get("finnhub").keys}
    assert values == {"a", "b", "c", "d"}


def test_duplicates_and_placeholders_are_ignored():
    registry = KeyRegistry.from_env({"MKT_FINNHUB_KEYS": "a,a,b,your_key_here,"})
    assert {k.value for k in registry.get("finnhub").keys} == {"a", "b"}


async def test_rotation_is_round_robin():
    registry = KeyRegistry.from_env(make_env(5))
    pool = registry.get("finnhub")
    used = [(await pool.acquire()).label for _ in range(5)]
    assert len(set(used)) == 5, "each key should be used once before any repeat"
    # Sixth acquire wraps to the least-recently-used key.
    assert (await pool.acquire()).label == used[0]


async def test_pool_exhausts_at_the_published_limit():
    """alphavantage is 5 calls/min per key; 4 keys means 20 calls, then stop."""
    registry = KeyRegistry.from_env({"MKT_ALPHAVANTAGE_KEYS": "a,b,c,d"})
    pool = registry.get("alphavantage")
    for _ in range(20):
        await pool.acquire()
    with pytest.raises(PoolExhausted) as excinfo:
        await pool.acquire()
    assert excinfo.value.retry_in > 0


async def test_daily_cap_is_enforced_separately_from_rate():
    spec = PROVIDER_SPECS["alphavantage"]
    assert spec.daily_cap == 25
    pool = KeyPool(spec, ["only"])
    key = pool.keys[0]
    key.calls_today = 25
    assert key.daily_exhausted
    assert not key.available()
    with pytest.raises(PoolExhausted):
        await pool.acquire()


async def test_rate_limited_key_is_benched_and_pool_routes_around_it():
    registry = KeyRegistry.from_env({"MKT_FINNHUB_KEYS": "a,b"})
    pool = registry.get("finnhub")
    first = await pool.acquire()
    first.report_rate_limited(retry_after=30)
    assert not first.available()
    # The other key still serves.
    for _ in range(5):
        assert (await pool.acquire()).value != first.value


def test_fatal_auth_failure_benches_for_an_hour():
    pool = KeyPool(PROVIDER_SPECS["finnhub"], ["bad"])
    pool.keys[0].report_failure(fatal=True)
    assert pool.keys[0].cooldown_remaining > 3000


def test_backoff_grows_with_repeated_throttling():
    pool = KeyPool(PROVIDER_SPECS["finnhub"], ["k"])
    key = pool.keys[0]
    waits = []
    for _ in range(3):
        key.report_rate_limited()
        waits.append(key.cooldown_remaining)
        key.cooldown_until = 0.0
    assert waits[0] < waits[1] < waits[2]


def test_keys_are_never_printed_in_full():
    pool = KeyPool(PROVIDER_SPECS["finnhub"], ["supersecretvalue123"])
    key = pool.keys[0]
    assert "supersecretvalue123" not in key.masked
    assert "supersecretvalue123" not in str(key.stats())
    assert "supersecretvalue123" not in str(pool.health())


async def test_keyless_pool_never_exhausts():
    registry = KeyRegistry.from_env({})
    pool = registry.get("yahoo")
    assert pool.keyless and pool.usable
    for _ in range(50):
        assert await pool.acquire() is None


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(capacity=4, per=1.0)
    for _ in range(4):
        assert bucket.try_take()
    assert not bucket.try_take()
    assert 0 < bucket.wait_time() <= 0.3
    bucket._last -= 1.0  # simulate a second passing
    assert bucket.try_take()


def test_registry_orders_providers_by_priority():
    registry = KeyRegistry.from_env(make_env(2))
    names = registry.usable_providers("quote")
    priorities = [PROVIDER_SPECS[n].priority for n in names]
    assert priorities == sorted(priorities)
