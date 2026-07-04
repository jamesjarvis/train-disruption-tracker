"""Tests for the run orchestration in main.collect_reports (no network).

The analyze-layer fetchers are monkeypatched so we exercise the fallback/stale
bookkeeping without hitting Realtime Trains or the planner.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from disruption import config, history, main
from disruption.models import DayReport
from disruption.rtt import RttError
from disruption.scraper import ScrapeError


@pytest.fixture
def _isolate(monkeypatch):
    """No real history I/O, and pretend RTT credentials exist so the merged path runs."""
    monkeypatch.setattr(history, "load", lambda *a, **k: {})
    monkeypatch.setattr(history, "save", lambda *a, **k: None)
    monkeypatch.setattr(config, "RTT_USERNAME", "u")
    monkeypatch.setattr(config, "RTT_PASSWORD", "p")
    monkeypatch.setattr(main, "build_actual_report",
                        lambda d, **k: (_ for _ in ()).throw(RttError("no actual")))


def _clean(d: date) -> DayReport:
    return DayReport(d, am_total=6, am_disrupted=0, pm_total=6, pm_disrupted=0)


def test_live_day_that_fully_fails_is_reported_stale(_isolate, monkeypatch):
    today = date.today()
    # Every merged fetch fails, forcing the planner fallback...
    monkeypatch.setattr(main, "build_merged_report",
                        lambda d, **k: (_ for _ in ()).throw(RttError("dark")))
    # ...and the planner fallback also fails for TODAY only.
    def _planner(d, **k):
        if d == today:
            raise ScrapeError("planner down")
        return _clean(d)
    monkeypatch.setattr(main, "build_day_report", _planner)

    _reports, got_today, stale = main.collect_reports(horizon_days=1)

    assert got_today == today
    assert stale == [today]


def test_live_day_that_falls_back_to_planner_is_not_stale(_isolate, monkeypatch):
    today = date.today()
    monkeypatch.setattr(main, "build_merged_report",
                        lambda d, **k: (_ for _ in ()).throw(RttError("dark")))
    # Planner succeeds for every day => nothing is stale (this is today's real case:
    # RTT went dark but the planner still had the answer).
    monkeypatch.setattr(main, "build_day_report", lambda d, **k: _clean(d))

    _reports, _today, stale = main.collect_reports(horizon_days=1)

    assert stale == []


def test_advance_day_failure_is_not_reported_stale(_isolate, monkeypatch):
    """Only the live window (today/tomorrow) drives the stale warning; a far-future
    planner blip is expected churn, not something to alarm a subscriber about."""
    today = date.today()
    monkeypatch.setattr(main, "build_merged_report", lambda d, **k: _clean(d))
    def _planner(d, **k):
        if d == today + timedelta(days=5):
            raise ScrapeError("planner blip")
        return _clean(d)
    monkeypatch.setattr(main, "build_day_report", _planner)

    _reports, _today, stale = main.collect_reports(horizon_days=7)

    assert stale == []
