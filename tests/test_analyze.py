"""Summary-logic tests (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from disruption.analyze import merge_trains, summarise
from disruption.models import TrainOption

D = date(2026, 6, 28)


def _train(hh: int, mm: int, disrupted: bool, reason: str | None = None) -> TrainOption:
    return TrainOption(datetime.combine(D, time(hh, mm)), disrupted, reason)


def test_ratios_and_affected():
    am = [_train(7, 0, True, "Bus service"), _train(7, 30, False),
          _train(8, 0, True, "Bus service"), _train(8, 30, False)]
    pm = [_train(17, 0, False), _train(17, 30, False)]
    rep = summarise(D, am, pm)
    assert rep.am_total == 4 and rep.am_disrupted == 2 and rep.am_pct == 50
    assert rep.pm_total == 2 and rep.pm_disrupted == 0 and rep.pm_pct == 0
    assert rep.affected is True
    assert len(rep.am_disrupted_trains) == 2
    assert [t.reason for t in rep.am_disrupted_trains] == ["Bus service", "Bus service"]
    assert rep.pm_disrupted_trains == []


def test_clean_day_not_affected():
    am = [_train(7, 0, False), _train(7, 30, False)]
    pm = [_train(17, 0, False)]
    rep = summarise(D, am, pm)
    assert rep.affected is False
    assert rep.am_pct == 0 and rep.pm_pct == 0
    assert rep.am_disrupted_trains == [] and rep.pm_disrupted_trains == []


def test_empty_window_pct_is_zero_not_error():
    rep = summarise(D, [], [])
    assert rep.am_pct == 0 and rep.pm_pct == 0


def test_merge_dedupes_by_departure_keeping_disrupted():
    # Same 07:28 train: planner says fine, actual says cancelled -> disrupted wins.
    actual = [_train(7, 28, True, "Cancelled"), _train(7, 58, True, "Delayed 7 min")]
    planner = [_train(7, 28, False), _train(8, 14, True, "Rail replacement bus")]
    merged = merge_trains(actual, planner)
    deps = [t.departure.strftime("%H:%M") for t in merged]
    assert deps == ["07:28", "07:58", "08:14"]  # union, sorted, no dup 07:28
    by_time = {t.departure.strftime("%H:%M"): t for t in merged}
    assert by_time["07:28"].disrupted and by_time["07:28"].reason == "Cancelled"


def test_merge_combined_report_counts_all_sources():
    actual = [_train(7, 28, True, "Cancelled"), _train(7, 58, False)]
    planner = [_train(7, 58, False), _train(8, 14, True, "Rail replacement bus")]
    rep = summarise(D, merge_trains(actual, planner), [])
    assert rep.am_total == 3 and rep.am_disrupted == 2


from disruption.analyze import apply_journeys, journey_outcome
from disruption.models import Call, DayReport, Journey


def _at(h: int, m: int) -> datetime:
    return datetime.combine(D, time(h, m))


def _am_journey(uid: str, *, dep=(8, 7), arr=(8, 45), arr_delay=0, dest_cancelled=False,
                reason=None, cancelled=False, dest_actual=True) -> Journey:
    dep_at = _at(*dep)
    arrival = _at(*arr)
    actual = arrival + timedelta(minutes=arr_delay)
    return Journey(uid, D, "am", cancelled, reason, {
        "BXY": Call(dep_at, dep_at, None, None),
        "LBG": Call(None, None, arrival, actual if dest_actual and not dest_cancelled else None, dest_cancelled),
    })


def test_late_arrival_is_disrupted_even_if_it_left_on_time():
    departure, option = journey_outcome(_am_journey("a", arr_delay=7))
    assert departure == _at(8, 7)
    assert option.disrupted and option.reason == "Arrived 7 min late" and option.delay_minutes == 7


def test_arrival_within_threshold_is_fine():
    assert journey_outcome(_am_journey("a", arr_delay=5)) == (_at(8, 7), None)


def test_terminated_early_is_disrupted_with_reason():
    _d, option = journey_outcome(_am_journey("a", dest_cancelled=True, reason="signalling system fault"))
    assert option.cancelled and option.reason == "Terminated early: signalling system fault"


def test_cancelled_journey_uses_its_reason():
    _d, option = journey_outcome(_am_journey("a", cancelled=True, reason="train fault"))
    assert option.cancelled and option.reason == "train fault"


def test_outcome_unknown_until_arrival_is_recorded():
    assert journey_outcome(_am_journey("a", dest_actual=False)) is None


def test_pm_journey_is_judged_at_bexley():
    dep, arr = _at(18, 0), _at(18, 30)
    journey = Journey("p", D, "pm", False, None, {
        "LBG": Call(dep, dep, None, None),
        "BXY": Call(None, None, arr, arr + timedelta(minutes=9)),
    })
    _d, option = journey_outcome(journey)
    assert option.reason == "Arrived 9 min late"


def test_apply_journeys_replaces_departure_delays_with_arrival_outcomes():
    report = DayReport(D, am_total=3, am_disrupted=1,
                       am_disrupted_trains=[_train(7, 7, True, "Delayed 8 min"), _train(9, 0, True, "Delayed 6 min")],
                       trains=[_am_journey("recovered", dep=(7, 7), arr=(7, 45), arr_delay=2),
                               _am_journey("late-arrival", dep=(8, 7), arr_delay=7)])
    apply_journeys(report)
    assert [(t.departure.strftime("%H:%M"), t.reason) for t in report.am_disrupted_trains] == [
        ("08:07", "Arrived 7 min late"), ("09:00", "Delayed 6 min")]
    assert report.am_disrupted == 2


def test_apply_journeys_is_idempotent():
    report = DayReport(D, am_total=1, trains=[_am_journey("late-arrival", arr_delay=7)])
    apply_journeys(report)
    first = list(report.am_disrupted_trains)
    apply_journeys(report)
    assert report.am_disrupted_trains == first and report.am_disrupted == 1


def test_apply_journeys_keeps_the_worst_of_trains_sharing_a_departure():
    report = DayReport(D, am_total=1, trains=[
        _am_journey("to-cst", dep=(8, 12), arr_delay=7),
        _am_journey("to-chx", dep=(8, 12), arr_delay=16),
    ])
    apply_journeys(report)
    assert [(t.reason, t.delay_minutes) for t in report.am_disrupted_trains] == [("Arrived 16 min late", 16)]
