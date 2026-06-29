"""Summary-logic tests (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime, time

from disruption.analyze import summarise
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
