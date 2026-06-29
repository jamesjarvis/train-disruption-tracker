"""Parser tests against saved planner HTML — no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from disruption import config
from disruption.scraper import _get, _parse_page

FIXTURES = Path(__file__).parent / "fixtures"
D = date(2026, 6, 28)


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_engineering_day_all_trains_flagged_with_bus():
    rows = _parse_page(_load("eng_am_bxy_lbg_0700.html"), D)
    assert rows, "expected to parse some trains"
    # Every train on this engineering Sunday is a replacement bus.
    assert all(disrupted for _dep, disrupted, _reason in rows)
    assert any("bus" in (reason or "").lower() or reason == "Disrupted"
               for _dep, disrupted, reason in rows)


def test_normal_day_no_disruption():
    rows = _parse_page(_load("normal_am_bxy_lbg_0700.html"), D)
    assert rows, "expected to parse some trains"
    assert not any(disrupted for _dep, disrupted, _reason in rows)


def test_normal_day_returns_real_departure_times():
    rows = _parse_page(_load("normal_am_bxy_lbg_0700.html"), D)
    deps = sorted(dep.time().strftime("%H:%M") for dep, _d, _r in rows)
    assert deps[0] >= "06:00"
    assert len(set(deps)) == len(deps)  # no duplicate times within a page


class _FlakyClient:
    """Fails the first ``fail_times`` GETs, then returns a 200."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def get(self, url):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise httpx.ConnectError("transient")
        return httpx.Response(
            200, text="<html>ok</html>", request=httpx.Request("GET", url)
        )


def test_get_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)  # no sleep in tests
    client = _FlakyClient(fail_times=config.REQUEST_RETRIES - 1)
    resp = _get(client, "http://x")
    assert resp.status_code == 200
    assert client.calls == config.REQUEST_RETRIES


def test_get_reraises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)
    client = _FlakyClient(fail_times=config.REQUEST_RETRIES)
    with pytest.raises(httpx.ConnectError):
        _get(client, "http://x")
    assert client.calls == config.REQUEST_RETRIES
