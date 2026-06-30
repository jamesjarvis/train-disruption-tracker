"""Realtime Trains parsing tests (pure, no network)."""

from __future__ import annotations

from datetime import date

import pytest

from disruption.rtt import RttError, _parse_page, fetch_trains

D = date(2026, 6, 28)


def _service(booked: str, realtime: str | None = None, *, display: str = "CALL",
             cancel: str | None = None) -> dict:
    loc = {"gbttBookedDeparture": booked, "displayAs": display}
    if realtime is not None:
        loc["realtimeDeparture"] = realtime
    if cancel is not None:
        loc["cancelReasonCode"] = "VA"
        loc["cancelReasonShortText"] = cancel
    return {"locationDetail": loc}


def _payload(*services: dict) -> dict:
    return {"services": list(services)}


def test_on_time_train_not_disrupted():
    rows = _parse_page(_payload(_service("0728", "0728")), D)
    (_dep, disrupted, _reason, cancelled, delay), = rows
    assert disrupted is False and cancelled is False and delay == 0


def test_five_minutes_late_is_not_disrupted():
    rows = _parse_page(_payload(_service("0728", "0733")), D)
    (_dep, disrupted, _reason, _c, delay), = rows
    assert delay == 5 and disrupted is False  # ">5 min", so 5 is fine


def test_six_minutes_late_is_disrupted():
    rows = _parse_page(_payload(_service("0728", "0734")), D)
    (_dep, disrupted, reason, _c, delay), = rows
    assert delay == 6 and disrupted is True
    assert reason == "Delayed 6 min"


def test_cancelled_train_is_disrupted():
    rows = _parse_page(
        _payload(_service("0728", display="CANCELLED_CALL", cancel="Signalling")), D
    )
    (_dep, disrupted, reason, cancelled, _delay), = rows
    assert disrupted is True and cancelled is True and reason == "Signalling"


def test_cancelled_default_reason_when_unlabelled():
    rows = _parse_page(_payload(_service("0728", display="CANCELLED_CALL")), D)
    (_dep, disrupted, reason, cancelled, _delay), = rows
    assert cancelled is True and reason == "Cancelled"


def test_arrival_only_call_is_skipped():
    rows = _parse_page(_payload({"locationDetail": {"displayAs": "CALL"}}), D)
    assert rows == []


def test_departure_time_parsed_onto_date():
    rows = _parse_page(_payload(_service("0728", "0728")), D)
    (dep, *_rest), = rows
    assert dep.date() == D and dep.strftime("%H:%M") == "07:28"


def test_no_services_returns_empty():
    assert _parse_page({"services": None}, D) == []
    assert _parse_page({}, D) == []


def test_fetch_trains_without_credentials_raises(monkeypatch):
    from disruption import config
    monkeypatch.setattr(config, "RTT_USERNAME", None)
    monkeypatch.setattr(config, "RTT_PASSWORD", None)
    with pytest.raises(RttError):
        fetch_trains("BXY", "LBG", D, config.AM_START, config.AM_END)
