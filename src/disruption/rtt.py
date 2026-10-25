"""Fetch *actual* running data from the Realtime Trains API (data.rtt.io).

The journey planner (``scraper.py``) is future-dated and schedule-only, so it can't see
a train that actually got cancelled on the day or ran late. Realtime Trains gives booked
and actual times, cancellations and reasons for recent dates.

``fetch_trains`` mirrors ``scraper.fetch_trains`` (same signature, returns
``TrainOption``s) so the rest of the pipeline treats planner and actual data
identically. ``search_services`` and ``fetch_service`` feed the per-train journeys.

A train is flagged ``disrupted`` if it is cancelled, replaced by a bus, or departs
origin more than ``config.DELAY_THRESHOLD_MINUTES`` late.
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta

import httpx

from . import config
from .models import Call, TrainOption

_BUS_MODES = {"BUS", "REPLACEMENT_BUS"}
_last_request = 0.0


class RttError(RuntimeError):
    """Raised when Realtime Trains returns something we can't parse into trains."""


def open_client() -> httpx.Client | None:
    if not config.RTT_TOKEN:
        return None
    client = httpx.Client(
        base_url=config.RTT_BASE,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    client.headers["Authorization"] = f"Bearer {config.RTT_TOKEN}"
    try:
        resp = _get(client, "/api/get_access_token", {})
        client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return client


def _get(client: httpx.Client, path: str, params: dict) -> httpx.Response:
    global _last_request
    last_exc: Exception | None = None
    for attempt in range(1, config.REQUEST_RETRIES + 1):
        wait = config.RTT_REQUEST_DELAY_SECONDS - (_time.monotonic() - _last_request)
        if wait > 0:
            _time.sleep(wait)
        _last_request = _time.monotonic()
        try:
            resp = client.get(path, params=params)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < config.REQUEST_RETRIES:
                _time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc


def _local(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(config.LONDON).replace(tzinfo=None)
    return parsed.replace(second=0, microsecond=0)


def _window(d: date, start: time, end: time) -> tuple[datetime, datetime]:
    begin = datetime.combine(d, start)
    finish = datetime.combine(d, end)
    if finish <= begin:
        finish += timedelta(days=1)
    return begin, finish


def _search(origin: str, dest: str, d: date, start: time, end: time,
            client: httpx.Client) -> list[dict]:
    begin, finish = _window(d, start, end)
    resp = _get(client, "/gb-nr/location", {
        "code": origin,
        "filterTo": dest,
        "timeFrom": begin.isoformat(),
        "timeTo": finish.isoformat(),
    })
    if resp.status_code == 204 or not resp.content:
        return []
    return resp.json().get("services") or []


def _reason(reasons: list | None, kind: str) -> str | None:
    for r in reasons or []:
        if r.get("type") == kind and r.get("shortText"):
            return r["shortText"]
    return None


def _option(service: dict) -> TrainOption | None:
    temporal = service.get("temporalData") or {}
    dep = temporal.get("departure") or {}
    booked = _local(dep.get("scheduleAdvertised"))
    if booked is None:
        return None
    cancelled = bool(dep.get("isCancelled")) or temporal.get("displayAs") == "CANCELLED"
    actual = _local(dep.get("realtimeActual") or dep.get("realtimeForecast"))
    delay = int((actual - booked).total_seconds() // 60) if actual else None
    bus = (service.get("scheduleMetadata") or {}).get("modeType") in _BUS_MODES

    reason: str | None = None
    if cancelled:
        reason = _reason(service.get("reasons"), "CANCEL") or "Cancelled"
    elif bus:
        reason = "Rail replacement bus"
    elif delay is not None and delay > config.DELAY_THRESHOLD_MINUTES:
        reason = f"Delayed {delay} min"
    return TrainOption(booked, reason is not None, reason, cancelled, delay)


def fetch_trains(
    origin: str,
    dest: str,
    d: date,
    start: time,
    end: time,
    *,
    client: httpx.Client | None = None,
) -> list[TrainOption]:
    """Return *actual* train options departing ``origin`` towards ``dest`` in the window.

    Raises ``RttError`` if nothing comes back, so the caller can skip rather than
    publish a falsely-clean result.
    """
    owns_client = client is None
    client = client or open_client()
    if client is None:
        raise RttError("RTT_TOKEN not set; cannot fetch actual data.")
    try:
        services = _search(origin, dest, d, start, end, client)
    finally:
        if owns_client:
            client.close()
    options = {o.departure: o for o in map(_option, services) if o is not None}
    if not options:
        raise RttError(
            f"No Realtime Trains services for {origin}->{dest} on {d} "
            f"[{start:%H:%M}-{end:%H:%M}]."
        )
    return sorted(options.values(), key=lambda o: o.departure)


def search_services(origin: str, dest: str, d: date, start: time, end: time, *,
                    client: httpx.Client) -> list[tuple[str, date, datetime]]:
    found: dict[str, tuple[str, date, datetime]] = {}
    for service in _search(origin, dest, d, start, end, client):
        meta = service.get("scheduleMetadata") or {}
        booked = _local(((service.get("temporalData") or {}).get("departure") or {})
                        .get("scheduleAdvertised"))
        uid = meta.get("uniqueIdentity")
        if uid and booked:
            found[uid] = (uid, date.fromisoformat(meta["departureDate"]), booked)
    return sorted(found.values(), key=lambda s: s[2])


def _call(temporal: dict) -> Call:
    arr = temporal.get("arrival") or {}
    dep = temporal.get("departure") or {}
    return Call(
        booked_dep=_local(dep.get("scheduleAdvertised")),
        actual_dep=_local(dep.get("realtimeActual")),
        booked_arr=_local(arr.get("scheduleAdvertised")),
        actual_arr=_local(arr.get("realtimeActual")),
        cancelled=temporal.get("displayAs") == "CANCELLED"
        or bool(arr.get("isCancelled") or dep.get("isCancelled")),
    )


def fetch_service(unique_identity: str, board_crs: str, *,
                  client: httpx.Client) -> tuple[bool, str | None, dict[str, Call]]:
    resp = _get(client, "/gb-nr/service", {"uniqueIdentity": unique_identity})
    service = resp.json().get("service") or {}
    calls: dict[str, Call] = {}
    for loc in service.get("locations") or []:
        codes = (loc.get("location") or {}).get("shortCodes") or []
        crs = next((c for c in codes if c in config.JOURNEY_STATIONS), None)
        if crs and crs not in calls:
            calls[crs] = _call(loc.get("temporalData") or {})
    board = calls.get(board_crs)
    cancelled = board is None or board.cancelled
    reasons = service.get("reasons")
    reason = _reason(reasons, "CANCEL") or _reason(reasons, "DELAY")
    return cancelled, reason, calls
