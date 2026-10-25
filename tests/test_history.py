"""History store tests (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from disruption import history
from disruption.models import Call, DayReport, Journey, TrainOption


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


def _late_journey(d: date) -> Journey:
    return Journey(
        uid="L12345", run_date=d, window="late", cancelled=False, reason=None,
        calls={
            "LBG": Call(datetime.combine(d, time(23, 52)), datetime.combine(d, time(23, 55)), None, None),
            "BXY": Call(None, None, datetime.combine(d + timedelta(days=1), time(0, 14)),
                        datetime.combine(d + timedelta(days=1), time(0, 41))),
        },
    )


def test_journeys_round_trip():
    d = date(2026, 9, 7)
    r = DayReport(d, am_total=1, trains=[_late_journey(d)])
    assert history.report_from_dict(history.report_to_dict(r)) == r


def test_legacy_day_without_trains_loads():
    d = date(2026, 7, 9)
    raw = history.report_to_dict(_report(d))
    del raw["trains"]
    assert history.report_from_dict(raw).trains == []


def _am(calls: dict[str, Call], cancelled: bool = False) -> Journey:
    return Journey("A1", date(2026, 9, 7), "am", cancelled, None, calls)


def test_journey_final_rules():
    t = datetime(2026, 9, 7, 8, 0)
    assert _am({"BXY": Call(t, t, None, None), "CST": Call(None, None, t, t)}).final
    assert not _am({"LBG": Call(None, None, t, t), "CST": Call(None, None, t, None)}).final
    assert _am({"CST": Call(None, None, t, None)}, cancelled=True).final
    pm = Journey("P1", date(2026, 9, 7), "pm", False, None, {"BXY": Call(None, None, t, t)})
    assert pm.final
