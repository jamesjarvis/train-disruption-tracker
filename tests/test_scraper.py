"""Parser tests against saved planner HTML — no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from disruption import config
from disruption.scraper import ScrapeError, _get, _parse_page, _page_signals_bus

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


def test_engineering_day_current_markup_all_flagged():
    """Current (2026) NR markup: bus markers live in the detail rows that FOLLOW
    each tr.mtx summary row, not inside it. Every journey on this all-bus page must
    still be flagged as a replacement bus."""
    rows = _parse_page(_load("eng_bus_am_bxy_lbg_2026.html"), D)
    assert rows, "expected to parse some trains"
    assert all(disrupted for _dep, disrupted, _reason in rows), (
        "every journey on an all-bus day must be flagged disrupted"
    )
    assert all("bus" in (reason or "").lower() for _dep, _d, reason in rows)


def test_page_signals_bus_detects_current_markup():
    """The backstop signal: a page whose HTML clearly mentions a replacement bus
    must be recognised even if no individual row parses as disrupted."""
    assert _page_signals_bus(_load("eng_bus_am_bxy_lbg_2026.html"))
    assert _page_signals_bus(_load("eng_am_bxy_lbg_0700.html"))
    assert not _page_signals_bus(_load("normal_am_bxy_lbg_0700.html"))


def test_parse_page_raises_when_bus_signalled_but_nothing_flagged():
    """Defense in depth: if the page shouts 'replacement bus' but our per-row
    parsing flags nothing, that is a parser/markup mismatch. Raise rather than
    silently report a clean day (which is exactly the failure that shipped)."""

    def _blind_row(_row, _d):
        # Simulate a future markup change that hides the per-row bus marker.
        return None

    with pytest.raises(ScrapeError):
        _parse_page(_load("eng_bus_am_bxy_lbg_2026.html"), D, _row_parser=_blind_row)


def test_cancelled_journey_flagged_with_clean_reason():
    """A cancelled planner journey must be flagged, and its reason must be the clean
    short label ("Cancelled") — not the run-on desc text with the 'Find alternative
    trains' call-to-action and embedded newlines that leaked into the feed."""
    rows = _parse_page(_load("cancelled_planner_journey.html"), D)
    cancelled = [(dep, reason) for dep, dis, reason in rows if dis]
    assert len(cancelled) == 1, "the 08:28 cancellation must be detected"
    dep, reason = cancelled[0]
    assert dep.strftime("%H:%M") == "08:28"
    assert reason == "Cancelled"
    # No raw-desc pollution.
    assert "find alternative" not in reason.lower()
    assert "\n" not in reason and "\t" not in reason


def test_second_clean_journey_not_flagged():
    rows = _parse_page(_load("cancelled_planner_journey.html"), D)
    clean = [dep for dep, dis, _r in rows if not dis]
    assert [d.strftime("%H:%M") for d in clean] == ["08:58"]


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
