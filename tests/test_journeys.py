"""Journey collection tests (no network)."""

from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest

from disruption import journeys
from disruption.models import Call, Journey

D = date(2026, 10, 23)
NOW = datetime(2026, 10, 24, 6, 0)


def _arrived(uid: str, window: str = "am") -> Journey:
    t = datetime(2026, 10, 23, 8, 0)
    return Journey(uid, D, window, False, None, {"LBG": Call(None, None, t, t)})


def _pending(uid: str) -> Journey:
    t = datetime(2026, 10, 23, 8, 0)
    return Journey(uid, D, "am", False, None, {"LBG": Call(None, None, t, None)})


@pytest.fixture
def searched(monkeypatch):
    found = {"am": [], "pm": [], "late": []}

    def search(origin, dest, d, start, end, *, client):
        window = "am" if origin == "BXY" else ("late" if start.hour >= 22 else "pm")
        return found[window]

    monkeypatch.setattr(journeys.rtt, "search_services", search)
    return found


@pytest.fixture
def fetched(monkeypatch):
    calls: list[str] = []
    failing: set[str] = set()

    def fetch(uid, board, *, client):
        calls.append(uid)
        if uid in failing:
            raise httpx.HTTPError("boom")
        t = datetime(2026, 10, 23, 8, 0)
        return False, None, {board: Call(t, t, None, None), "LBG": Call(None, None, t, t)}

    monkeypatch.setattr(journeys.rtt, "fetch_service", fetch)
    return calls, failing


def _dep(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 23, h, m)


def test_collect_skips_final_journeys(searched, fetched):
    calls, _ = fetched
    searched["am"] = [("A", D, _dep(7)), ("B", D, _dep(7, 30)), ("C", D, _dep(8))]
    got, used = journeys.collect(D, [_arrived("A"), _pending("B")], client=None, now=NOW, limit=60)
    assert calls == ["B", "C"] and used == 2
    assert [j.uid for j in got] == ["A", "B", "C"]


def test_collect_keeps_previous_on_fetch_error(searched, fetched):
    _, failing = fetched
    failing.add("B")
    searched["am"] = [("B", D, _dep(7, 30))]
    got, _ = journeys.collect(D, [_pending("B")], client=None, now=NOW, limit=60)
    assert got == [_pending("B")]


def test_collect_waits_until_train_should_have_arrived(searched, fetched):
    calls, _ = fetched
    searched["pm"] = [("P", D, _dep(18))]
    got, used = journeys.collect(D, [], client=None, now=_dep(18, 30), limit=60)
    assert calls == [] and got == [] and used == 0


def test_collect_respects_limit(searched, fetched):
    calls, _ = fetched
    searched["am"] = [(u, D, _dep(7)) for u in "ABC"]
    _got, used = journeys.collect(D, [], client=None, now=NOW, limit=2)
    assert calls == ["A", "B"] and used == 2


def test_collect_orders_by_window_then_departure(searched, fetched):
    searched["late"] = [("L", D, _dep(22, 15))]
    searched["pm"] = [("P", D, _dep(18))]
    searched["am"] = [("A", D, _dep(7))]
    got, _ = journeys.collect(D, [], client=None, now=NOW, limit=60)
    assert [(j.uid, j.window) for j in got] == [("A", "am"), ("P", "pm"), ("L", "late")]
