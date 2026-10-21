"""Headless CLI paths, wired to the vendor simulator."""
from __future__ import annotations

import httpx
import pytest

from mkt import cli
from mkt.http import HttpClient
from mkt.keypool import KeyRegistry
from tests.vendorsim import make_env


@pytest.fixture(autouse=True)
def wire_cli(monkeypatch, sim, settings):
    """Point the CLI's HTTP client and key registry at the simulator."""
    def fake_client(s=None, **kwargs):
        client = HttpClient(settings, transport=httpx.MockTransport(sim.handler))
        client.limiter.default_interval = 0.0
        return client

    # Capture the real builders *before* patching, or the replacements recurse.
    real_registry_from_env = KeyRegistry.from_env

    monkeypatch.setattr(cli, "HttpClient", fake_client)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: {})
    monkeypatch.setattr(
        cli, "KeyRegistry",
        type("StubRegistry", (), {
            "from_env": staticmethod(lambda env=None: real_registry_from_env(make_env(3))),
        }),
    )
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: settings))


def test_quote_table(capsys):
    assert cli.main(["quote", "AAPL", "MSFT"]) == 0
    out = capsys.readouterr().out
    assert "AAPL" in out and "232.15" in out
    assert "MSFT" in out and "418.90" in out
    assert "SOURCE" in out


def test_quote_json(capsys):
    import json
    assert cli.main(["quote", "AAPL", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["AAPL"]["price"] == pytest.approx(232.15, abs=0.01)
    assert "source" in payload["AAPL"]


def test_history_renders_a_chart(capsys):
    assert cli.main(["history", "AAPL", "--width", "60", "--height", "10"]) == 0
    out = capsys.readouterr().out
    assert "█" in out or "│" in out
    assert "AAPL" in out
    assert "[" not in out, "markup must be stripped for plain stdout"


def test_news(capsys):
    assert cli.main(["news", "AAPL", "--limit", "5"]) == 0
    assert "http" in capsys.readouterr().out


def test_filings(capsys):
    assert cli.main(["filings", "AAPL", "--forms", "10-K", "10-Q"]) == 0
    out = capsys.readouterr().out
    assert "10-Q" in out and "sec.gov" in out


def test_search(capsys):
    assert cli.main(["search", "apple"]) == 0
    assert "AAPL" in capsys.readouterr().out


def test_keys_never_prints_a_full_key(capsys):
    assert cli.main(["keys"]) == 0
    out = capsys.readouterr().out
    assert "finnhub" in out and "ready" in out
    assert "finnhub_key_01" not in out, "keys must be masked"
    assert "..." in out


def test_doctor_probes_every_source(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "PROVIDER" in out
    assert "sources responded" in out
    assert out.count("OK") >= 10, out


def test_doctor_reports_failures(capsys, sim):
    sim.set_fail("finnhub.io", "500")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_doctor_distinguishes_empty_from_ok(capsys, sim):
    """A scraper that swallows its own feed errors returns [] — that is
    reachable-but-useless, not success."""
    for host in ("feeds.finance.yahoo.com", "www.cnbc.com",
                 "feeds.content.dowjones.io", "www.federalreserve.gov",
                 "www.sec.gov"):
        sim.set_fail(host, "500")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    rss_line = next(line for line in out.splitlines() if line.startswith("rss"))
    assert "EMPTY" in rss_line, rss_line


def test_status_json(capsys):
    import json
    assert cli.main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "providers" in payload and "keys" in payload
    assert "finnhub_key_01" not in capsys.readouterr().out


def test_quote_failure_exit_code(capsys, sim):
    for host in ("finnhub.io", "query1.finance.yahoo.com", "stooq.com",
                 "api.coingecko.com", "api.twelvedata.com", "financialmodelingprep.com",
                 "www.alphavantage.co", "api.tiingo.com", "api.polygon.io",
                 "api.marketstack.com"):
        sim.set_fail(host, "500")
    assert cli.main(["quote", "AAPL"]) == 1
