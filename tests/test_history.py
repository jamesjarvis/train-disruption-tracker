"""History store tests (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime, time

from disruption import history
from disruption.models import DayReport, TrainOption


def _report(d: date, *, disrupted: int = 1) -> DayReport:
    trains = [TrainOption(datetime.combine(d, time(7, 28)), True, "Cancelled",
                          cancelled=True, delay_minutes=None)]
    return DayReport(d, am_total=6, am_disrupted=disrupted, pm_total=10, pm_disrupted=0,
                     am_disrupted_trains=trains if disrupted else [])


def test_round_trip_preserves_fields(tmp_path):
    path = tmp_path / "history.json"
    d = date(2026, 6, 28)
    store = {d: _report(d)}
    history.save(store, path)
    loaded = history.load(path)
    assert loaded.keys() == store.keys()
    t = loaded[d].am_disrupted_trains[0]
    assert t.cancelled is True and t.reason == "Cancelled" and t.disrupted is True
    assert t.departure == datetime(2026, 6, 28, 7, 28)


def test_load_missing_file_is_empty(tmp_path):
    assert history.load(tmp_path / "nope.json") == {}


def test_upsert_overwrites_same_date():
    d = date(2026, 6, 28)
    store = {d: _report(d, disrupted=1)}
    history.upsert(store, _report(d, disrupted=3))
    assert len(store) == 1 and store[d].am_disrupted == 3


def test_prune_drops_old_keeps_boundary():
    today = date(2026, 6, 29)
    store = {
        date(2026, 4, 1): _report(date(2026, 4, 1)),   # well older than 60d -> dropped
        date(2026, 4, 30): _report(date(2026, 4, 30)),  # exactly 60d before -> kept
        today: _report(today),
    }
    history.prune(store, today, keep_days=60)
    assert date(2026, 4, 1) not in store
    assert date(2026, 4, 30) in store and today in store
