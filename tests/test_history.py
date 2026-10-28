"""History store tests (pure, no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from disruption import history
from disruption.models import Call, DayReport, Journey, TrainOption


def _report(d: date, *, disrupted: int = 1) -> DayReport:
    trains = [TrainOption(datetime.combine(d, time(7, 28)), True, "Cancelled",
                          cancelled=True, delay_minutes=None)]
    return DayReport(d, am_total=6, am_disrupted=disrupted, pm_total=10, pm_disrupted=0,
                     am_disrupted_trains=trains if disrupted else [])


def test_round_trip_preserves_fields(tmp_path):
    path = tmp_path / "trains.db"
    d = date(2026, 6, 28)
    store = {d: _report(d)}
    history.save(store, path)
    loaded = history.load(path)
    assert loaded.keys() == store.keys()
    t = loaded[d].am_disrupted_trains[0]
    assert t.cancelled is True and t.reason == "Cancelled" and t.disrupted is True
    assert t.departure == datetime(2026, 6, 28, 7, 28)


def test_load_missing_database_is_empty(tmp_path):
    assert history.load(tmp_path / "nope.db") == {}


def test_upsert_overwrites_same_date():
    d = date(2026, 6, 28)
    store = {d: _report(d, disrupted=1)}
    history.upsert(store, _report(d, disrupted=3))
    assert len(store) == 1 and store[d].am_disrupted == 3


def _late_journey(d: date) -> Journey:
    return Journey(
        uid="L12345", run_date=d, window="late", cancelled=False, reason=None,
        calls={
            "LBG": Call(datetime.combine(d, time(23, 52)), datetime.combine(d, time(23, 55)), None, None),
            "BXY": Call(None, None, datetime.combine(d + timedelta(days=1), time(0, 14)),
                        datetime.combine(d + timedelta(days=1), time(0, 41))),
        },
    )


def test_journeys_round_trip(tmp_path):
    d = date(2026, 9, 7)
    store = {d: DayReport(d, am_total=1, trains=[_late_journey(d)])}
    history.save(store, tmp_path / "trains.db")
    assert history.load(tmp_path / "trains.db") == store


def test_save_replaces_previous_contents(tmp_path):
    path = tmp_path / "trains.db"
    d = date(2026, 9, 7)
    history.save({d: DayReport(d, trains=[_late_journey(d)])}, path)
    history.save({d: DayReport(d)}, path)
    assert history.load(path)[d].trains == []


def test_import_json_reads_legacy_store(tmp_path):
    d = date(2026, 7, 9)
    legacy = tmp_path / "history.json"
    legacy.write_text(json.dumps({d.isoformat(): {
        "date": d.isoformat(), "am_total": 6, "am_disrupted": 0, "pm_total": 10, "pm_disrupted": 0,
        "am_disrupted_trains": [], "pm_disrupted_trains": [],
    }}))
    assert history.import_json(legacy)[d].am_total == 6
    assert history.import_json(tmp_path / "nope.json") == {}


def _looping_journey(d: date, *, to_cancelled: bool = False) -> Journey:
    at = lambda h, m: datetime.combine(d, time(h, m))
    return Journey(
        uid="gb-nr:P88842", run_date=d, window="am", cancelled=False, reason="signalling system fault",
        calls={
            "LBG": Call(at(7, 7), at(7, 9), at(7, 6), at(7, 9)),
            "BXY": Call(at(8, 7), at(8, 11), at(8, 7), at(8, 10)),
            "CST": Call(None, None, at(8, 46), None if to_cancelled else at(8, 49), to_cancelled),
        },
    )


def _legs(tmp_path, journey: Journey) -> list[tuple]:
    path = tmp_path / "trains.db"
    history.save({journey.run_date: DayReport(journey.run_date, trains=[journey])}, path)
    conn = history.connect(path)
    return conn.execute(
        "SELECT from_station, to_station, delay_minutes, status FROM legs ORDER BY to_station"
    ).fetchall()


def test_legs_view_flags_terminated_early(tmp_path):
    legs = _legs(tmp_path, _looping_journey(date(2026, 10, 26), to_cancelled=True))
    assert ("BXY", "CST", None, "terminated_early") in legs


def test_legs_view_measures_arrival_delay(tmp_path):
    legs = _legs(tmp_path, _looping_journey(date(2026, 10, 26)))
    assert ("BXY", "CST", 3, "ran") in legs


def test_legs_view_uses_the_run_towards_the_destination_for_pm_trains(tmp_path):
    d = date(2026, 10, 26)
    at = lambda h, m: datetime.combine(d, time(h, m))
    journey = Journey("gb-nr:P1", d, "pm", False, None, {
        "CHX": Call(at(17, 50), at(17, 50), None, None),
        "LBG": Call(at(18, 0), at(18, 1), at(17, 59), at(18, 0)),
        "BXY": Call(None, None, at(18, 24), at(18, 40)),
    })
    legs = _legs(tmp_path, journey)
    assert ("LBG", "BXY", 16, "late") in legs and ("CHX", "BXY", 16, "late") in legs
