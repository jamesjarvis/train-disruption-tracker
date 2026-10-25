"""Realtime Trains parsing tests (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest

from disruption import config, rtt
from disruption.rtt import RttError, fetch_service, fetch_trains, search_services

D = date(2026, 10, 23)


def _temporal(booked: str | None, actual: str | None = None, *, cancelled: bool = False) -> dict:
    t: dict = {"isCancelled": cancelled}
    if booked:
        t["scheduleAdvertised"] = booked
    if actual:
        t["realtimeActual"] = actual
    return t


def _line_up(booked: str, actual: str | None = None, *, display: str = "CALL",
             cancelled: bool = False, reason: str | None = None, mode: str = "TRAIN",
             uid: str = "gb-nr:L01525:2026-10-23") -> dict:
    service = {
        "scheduleMetadata": {"uniqueIdentity": uid, "departureDate": uid[-10:], "modeType": mode},
        "temporalData": {"departure": _temporal(booked, actual, cancelled=cancelled),
                         "displayAs": display},
    }
    if reason:
        service["reasons"] = [{"type": "CANCEL", "shortText": reason}]
    return service


def _client(*payloads: dict | None, seen: list | None = None) -> httpx.Client:
    queue = list(payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        body = queue.pop(0)
        return httpx.Response(204) if body is None else httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=config.RTT_BASE)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(rtt._time, "sleep", lambda s: None)


def _fetch(*services: dict):
    client = _client({"services": list(services)})
    return fetch_trains("BXY", "LBG", D, datetime(2026, 1, 1, 7).time(),
                        datetime(2026, 1, 1, 10).time(), client=client)


def test_on_time_train_not_disrupted():
    [t] = _fetch(_line_up("2026-10-23T07:28:00+01:00", "2026-10-23T07:28:00+01:00"))
    assert t.departure == datetime(2026, 10, 23, 7, 28)
    assert not t.disrupted and t.delay_minutes == 0


def test_five_minutes_late_is_not_disrupted():
    [t] = _fetch(_line_up("2026-10-23T07:28:00+01:00", "2026-10-23T07:33:00+01:00"))
    assert not t.disrupted and t.delay_minutes == 5


def test_six_minutes_late_is_disrupted():
    [t] = _fetch(_line_up("2026-10-23T07:28:00+01:00", "2026-10-23T07:34:00+01:00"))
    assert t.disrupted and t.reason == "Delayed 6 min"


def test_cancelled_train_is_disrupted_with_reason():
    [t] = _fetch(_line_up("2026-10-23T07:28:00+01:00", display="CANCELLED", cancelled=True,
                          reason="signalling fault"))
    assert t.disrupted and t.cancelled and t.reason == "signalling fault"


def test_cancelled_default_reason_when_unlabelled():
    [t] = _fetch(_line_up("2026-10-23T07:28:00+01:00", display="CANCELLED"))
    assert t.cancelled and t.reason == "Cancelled"


def test_replacement_bus_is_disrupted():
    [t] = _fetch(_line_up("2026-10-23T07:28:00+01:00", mode="REPLACEMENT_BUS"))
    assert t.disrupted and t.reason == "Rail replacement bus"


def test_service_without_advertised_departure_is_skipped():
    service = _line_up("2026-10-23T07:28:00+01:00")
    del service["temporalData"]["departure"]["scheduleAdvertised"]
    assert _fetch(service, _line_up("2026-10-23T07:58:00+01:00"))[0].departure.minute == 58


def test_no_services_raises():
    client = _client(None)
    with pytest.raises(RttError):
        fetch_trains("BXY", "LBG", D, datetime(2026, 1, 1, 7).time(),
                     datetime(2026, 1, 1, 10).time(), client=client)


def test_search_queries_one_window_across_midnight():
    seen: list[httpx.Request] = []
    client = _client({"services": [_line_up("2026-10-24T00:12:00+01:00",
                                            uid="gb-nr:L09999:2026-10-23")]}, seen=seen)
    found = search_services("LBG", "BXY", D, config.LATE_START, config.LATE_END, client=client)
    assert found == [("gb-nr:L09999:2026-10-23", date(2026, 10, 23), datetime(2026, 10, 24, 0, 12))]
    params = seen[0].url.params
    assert seen[0].url.path == "/gb-nr/location"
    assert params["code"] == "LBG" and params["filterTo"] == "BXY"
    assert params["timeFrom"] == "2026-10-23T22:00:00" and params["timeTo"] == "2026-10-24T00:30:00"


def _location(crs: str, arr: tuple[str | None, str | None] = (None, None),
              dep: tuple[str | None, str | None] = (None, None), display: str = "CALL") -> dict:
    return {
        "location": {"shortCodes": [crs]},
        "temporalData": {"arrival": _temporal(*arr), "departure": _temporal(*dep),
                         "displayAs": display},
    }


def _service_payload(*locations: dict, reasons: list | None = None) -> dict:
    return {"service": {"locations": list(locations), "reasons": reasons or []}}


def test_fetch_service_extracts_journey_stations():
    client = _client(_service_payload(
        _location("DFD", dep=("2026-10-23T07:10:00+01:00", "2026-10-23T07:10:00+01:00")),
        _location("BXY", dep=("2026-10-23T07:28:00+01:00", "2026-10-23T07:29:00+01:00")),
        _location("LBG", arr=("2026-10-23T07:52:00+01:00", "2026-10-23T07:55:00+01:00"),
                  dep=("2026-10-23T07:53:00+01:00", "2026-10-23T07:56:00+01:00")),
        _location("CST", arr=("2026-10-23T07:58:00+01:00", "2026-10-23T08:03:00+01:00")),
    ))
    cancelled, reason, calls = fetch_service("gb-nr:L01525:2026-10-23", "BXY", client=client)
    assert set(calls) == {"BXY", "LBG", "CST"}
    assert calls["BXY"].booked_dep == datetime(2026, 10, 23, 7, 28)
    assert calls["CST"].actual_arr == datetime(2026, 10, 23, 8, 3)
    assert not cancelled and reason is None


def test_fetch_service_converts_utc_to_london_across_midnight():
    client = _client(_service_payload(
        _location("LBG", dep=("2026-10-23T22:52:00Z", "2026-10-23T22:55:00Z")),
        _location("BXY", arr=("2026-10-23T23:14:00Z", "2026-10-23T23:20:00Z")),
    ))
    _c, _r, calls = fetch_service("gb-nr:L09999:2026-10-23", "LBG", client=client)
    assert calls["BXY"].actual_arr == datetime(2026, 10, 24, 0, 20)


def test_fetch_service_cancelled_call_is_not_a_cancelled_journey():
    client = _client(_service_payload(
        _location("BXY", dep=("2026-10-23T07:28:00+01:00", "2026-10-23T07:28:00+01:00")),
        _location("LBG", arr=("2026-10-23T07:52:00+01:00", "2026-10-23T07:52:00+01:00"),
                  display="TERMINATES"),
        _location("CST", arr=("2026-10-23T07:58:00+01:00", None), display="CANCELLED"),
        reasons=[{"type": "CANCEL", "shortText": "train fault"}],
    ))
    cancelled, reason, calls = fetch_service("gb-nr:L01525:2026-10-23", "BXY", client=client)
    assert calls["CST"].cancelled and not calls["LBG"].cancelled
    assert not cancelled and reason == "train fault"


def test_open_client_without_token_is_none(monkeypatch):
    monkeypatch.setattr(config, "RTT_TOKEN", None)
    assert rtt.open_client() is None
