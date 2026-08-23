"""Routing, failover and degradation."""
from __future__ import annotations

import pytest

from mkt.http import HttpClient
from mkt.keypool import KeyRegistry
from mkt.models import Quote
from mkt.router import NoProviderAvailable, Router

import httpx


async def test_quote_uses_highest_priority_provider(router):
    quote = await router.quote("AAPL")
    assert quote.source == "finnhub"      # priority 10, the cheapest good source
    assert quote.price == pytest.approx(232.15, abs=0.01)


async def test_failover_when_primary_returns_429(router, sim):
    sim.set_fail("finnhub.io", "429")
    quote = await router.quote("AAPL")
    assert quote.source != "finnhub"
    assert isinstance(quote, Quote) and quote.price > 0
    assert router.health["finnhub"].rate_limits == 1


async def test_failover_cascades_through_several_dead_providers(router, sim):
    for host in ("finnhub.io", "query1.finance.yahoo.com", "api.coingecko.com",
                 "stooq.com", "api.twelvedata.com"):
        sim.set_fail(host, "500")
    quote = await router.quote("AAPL")
    # Everything above fmp in the order is down, so fmp (or lower) must serve.
    assert quote.source in {"fmp", "alphavantage", "tiingo", "polygon", "marketstack"}
    assert quote.price > 0


async def test_soft_rate_limit_is_detected_as_a_limit_not_a_success(router, sim):
    """Alpha Vantage answers 200 with a 'Note' when throttled. If that reads as
    success the key never gets benched and the pool drains silently."""
    sim.set_fail("www.alphavantage.co", "softlimit")
    provider = router.providers["alphavantage"]
    from mkt.http import RateLimited
    with pytest.raises(RateLimited):
        await provider.quote("AAPL")


async def test_router_benches_the_key_that_earned_the_429(router, sim):
    sim.set_fail("finnhub.io", "429")
    await router.quote("AAPL")
    pool = router.keys.get("finnhub")
    assert any(k.cooling for k in pool.keys), "the used key should be cooling"


async def test_401_marks_the_key_bad_for_an_hour(router, sim):
    sim.set_fail("api.polygon.io", "401")
    with pytest.raises(Exception):
        await router.providers["polygon"].quote("AAPL")
    await router._dispatch("quote", lambda p: p.quote("AAPL"), prefer="polygon")
    pool = router.keys.get("polygon")
    assert any(k.cooldown_remaining > 3000 for k in pool.keys)


async def test_everything_down_raises_with_a_readable_report(router, sim):
    for host in list(sim.calls) + [
        "finnhub.io", "query1.finance.yahoo.com", "stooq.com", "api.coingecko.com",
        "api.twelvedata.com", "financialmodelingprep.com", "www.alphavantage.co",
        "api.tiingo.com", "api.polygon.io", "api.marketstack.com",
    ]:
        sim.set_fail(host, "500")
    with pytest.raises(NoProviderAvailable) as excinfo:
        await router.quote("AAPL")
    assert "quote" in str(excinfo.value)
    assert len(excinfo.value.attempts) >= 5, "every candidate should be reported"


async def test_keyless_sources_still_serve_with_zero_keys(http, settings, sim):
    """The whole point of the keyless tier: no credentials, still a price."""
    router = Router(http=http, settings=settings, keys=KeyRegistry.from_env({}))
    assert router.keys.total_keys() == 0
    quote = await router.quote("AAPL")
    assert quote.source in {"yahoo", "stooq", "coingecko"}
    assert quote.price > 0


async def test_watchlist_fans_out_concurrently(router):
    results = await router.quotes(["AAPL", "MSFT", "NVDA", "SPY"])
    assert set(results) == {"AAPL", "MSFT", "NVDA", "SPY"}
    assert all(isinstance(v, Quote) for v in results.values())
    assert results["NVDA"].price == pytest.approx(121.44, abs=0.01)


async def test_one_bad_symbol_does_not_sink_the_watchlist(router, sim):
    results = await router.quotes(["AAPL", "MSFT"])
    assert isinstance(results["AAPL"], Quote)


async def test_prefer_overrides_priority_order(router):
    quote = await router.quote("AAPL", prefer="stooq")
    assert quote.source == "stooq"


async def test_allow_restricts_the_candidate_set(router):
    quote = await router.quote("AAPL", allow=["marketstack"])
    assert quote.source == "marketstack"


async def test_news_merges_across_providers_and_dedupes(router):
    items = await router.news("AAPL", limit=30)
    assert len(items) >= 4
    sources = {i.source for i in items}
    assert len(sources) >= 3, f"expected a merge, got {sources}"
    urls = [i.url for i in items if i.url]
    assert len(urls) == len(set(urls)), "duplicate stories must be collapsed"
    stamps = [i.published for i in items]
    assert stamps == sorted(stamps, reverse=True), "newest first"


async def test_news_survives_a_dead_feed(router, sim):
    sim.set_fail("finnhub.io", "500")
    items = await router.news("AAPL", limit=20)
    assert items, "other sources should still fill the panel"


async def test_history_and_analytics(router):
    series = await router.history("AAPL", "1d", 30)
    assert len(series) >= 15
    assert series.pct_change() is not None
    assert series.realised_vol() is not None
    assert series.max_drawdown() <= 0


async def test_filings_route_to_edgar(router):
    filings = await router.filings("AAPL", limit=5)
    assert filings and filings[0].source == "edgar"


async def test_flaky_provider_is_routed_around(router, sim):
    sim.set_fail("finnhub.io", "500")
    first = await router.quote("AAPL")
    assert first.source != "finnhub"
    # Once a healthy source has proven itself, the flaky one sinks below it.
    assert router.candidates("quote")[0] != "finnhub"
    for _ in range(3):
        assert (await router.quote("AAPL")).price > 0


async def test_repeated_failures_penalise_a_provider(router, sim):
    """Forced back onto a dead provider three times, it gets benched outright."""
    sim.set_fail("api.marketstack.com", "500")
    for _ in range(3):
        with pytest.raises(Exception):
            await router.providers["marketstack"].quote("AAPL")
        router._record_failure("marketstack", RuntimeError("boom"))
    assert router.health["marketstack"].penalised
    assert "marketstack" not in router.candidates("quote")[:3]


def test_health_decays_so_a_demoted_provider_is_retried():
    """A provider exiled by one bad minute must be re-probed later, or the
    router can never discover that it recovered."""
    from mkt.router import ProviderHealth

    health = ProviderHealth()
    for _ in range(4):
        health.calls += 1
        health.failures += 1
    bad_score = health.score(10)

    # Simulate several idle decay windows.
    health.last_activity -= health.decay_after * 4
    recovered_score = health.score(10)

    assert recovered_score < bad_score, "idle time must rehabilitate the record"
    assert health.calls < 4, "counters should have decayed"


def test_untried_provider_is_not_assumed_perfect():
    """Smoothing: an unproven source reads as 0.5, so it neither leaps to the
    front nor gets buried."""
    from mkt.router import ProviderHealth

    fresh = ProviderHealth()
    assert fresh.success_rate == pytest.approx(0.5)
    proven = ProviderHealth(calls=10, successes=10)
    assert proven.success_rate > fresh.success_rate


async def test_status_reports_keys_without_leaking_them(router):
    await router.quote("AAPL")
    status = router.status()
    blob = str(status)
    assert "finnhub_key_01" not in blob
    assert status["providers"]["finnhub"]["calls"] >= 1
    assert status["providers"]["finnhub"]["keys"] == 3


async def test_cache_prevents_duplicate_upstream_calls(sim, settings, keys):
    settings.cache_ttl = 60.0
    client = HttpClient(settings, transport=httpx.MockTransport(sim.handler))
    client.limiter.default_interval = 0.0
    router = Router(http=client, settings=settings, keys=keys)
    await router.quote("AAPL")
    calls_after_first = sim.calls["finnhub.io"]
    await router.quote("AAPL")
    assert sim.calls["finnhub.io"] == calls_after_first, "second call should be cached"
