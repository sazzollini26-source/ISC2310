from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mkt.config import Settings           # noqa: E402
from mkt.http import HttpClient           # noqa: E402
from mkt.keypool import KeyRegistry       # noqa: E402
from mkt.router import Router             # noqa: E402
from tests.vendorsim import VendorSim, make_env  # noqa: E402


@pytest.fixture
def sim() -> VendorSim:
    return VendorSim()


@pytest.fixture
def settings() -> Settings:
    s = Settings.from_env()
    s.cache_ttl = 0.0        # caching off by default so call counts are exact
    s.max_retries = 1
    s.contact_email = "test@example.com"
    s.watchlist = ["AAPL", "MSFT", "NVDA"]
    return s


@pytest.fixture
def http(sim: VendorSim, settings: Settings) -> HttpClient:
    client = HttpClient(settings, transport=httpx.MockTransport(sim.handler))
    client.limiter.default_interval = 0.0   # no artificial delay in tests
    return client


@pytest.fixture
def keys() -> KeyRegistry:
    return KeyRegistry.from_env(make_env(3))


@pytest.fixture
def router(http: HttpClient, settings: Settings, keys: KeyRegistry) -> Router:
    return Router(http=http, settings=settings, keys=keys)
