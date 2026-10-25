"""Persist every computed day report, forever.

The .ics is rebuilt from scratch each run and Realtime Trains only serves recent dates,
so past days must be remembered between runs. This is a tiny JSON store keyed by ISO
date, kept locally (git-ignored). The published .ics remains the durable public record.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import config
from .models import Call, DayReport, Journey, TrainOption


def _train_to_dict(t: TrainOption) -> dict:
    return {
        "departure": t.departure.isoformat(),
        "disrupted": t.disrupted,
        "reason": t.reason,
        "cancelled": t.cancelled,
        "delay_minutes": t.delay_minutes,
    }


def _train_from_dict(d: dict) -> TrainOption:
    return TrainOption(
        departure=datetime.fromisoformat(d["departure"]),
        disrupted=d["disrupted"],
        reason=d.get("reason"),
        cancelled=d.get("cancelled", False),
        delay_minutes=d.get("delay_minutes"),
    )


def _time_to_str(t: datetime | None) -> str | None:
    return t.isoformat(timespec="minutes") if t else None


def _time_from_str(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _call_to_dict(c: Call) -> dict:
    return {
        "booked_dep": _time_to_str(c.booked_dep),
        "actual_dep": _time_to_str(c.actual_dep),
        "booked_arr": _time_to_str(c.booked_arr),
        "actual_arr": _time_to_str(c.actual_arr),
        "cancelled": c.cancelled,
    }


def _call_from_dict(d: dict) -> Call:
    return Call(
        booked_dep=_time_from_str(d.get("booked_dep")),
        actual_dep=_time_from_str(d.get("actual_dep")),
        booked_arr=_time_from_str(d.get("booked_arr")),
        actual_arr=_time_from_str(d.get("actual_arr")),
        cancelled=d.get("cancelled", False),
    )


def journey_to_dict(j: Journey) -> dict:
    return {
        "uid": j.uid,
        "run_date": j.run_date.isoformat(),
        "window": j.window,
        "cancelled": j.cancelled,
        "reason": j.reason,
        "calls": {crs: _call_to_dict(c) for crs, c in j.calls.items()},
    }


def journey_from_dict(d: dict) -> Journey:
    return Journey(
        uid=d["uid"],
        run_date=date.fromisoformat(d["run_date"]),
        window=d["window"],
        cancelled=d["cancelled"],
        reason=d.get("reason"),
        calls={crs: _call_from_dict(c) for crs, c in d["calls"].items()},
    )


def report_to_dict(r: DayReport) -> dict:
    return {
        "date": r.date.isoformat(),
        "am_total": r.am_total,
        "am_disrupted": r.am_disrupted,
        "pm_total": r.pm_total,
        "pm_disrupted": r.pm_disrupted,
        "am_disrupted_trains": [_train_to_dict(t) for t in r.am_disrupted_trains],
        "pm_disrupted_trains": [_train_to_dict(t) for t in r.pm_disrupted_trains],
        "trains": [journey_to_dict(j) for j in r.trains],
    }


def report_from_dict(d: dict) -> DayReport:
    return DayReport(
        date=date.fromisoformat(d["date"]),
        am_total=d["am_total"],
        am_disrupted=d["am_disrupted"],
        pm_total=d["pm_total"],
        pm_disrupted=d["pm_disrupted"],
        am_disrupted_trains=[_train_from_dict(t) for t in d["am_disrupted_trains"]],
        pm_disrupted_trains=[_train_from_dict(t) for t in d["pm_disrupted_trains"]],
        trains=[journey_from_dict(j) for j in d.get("trains", [])],
    )


def load(path: Path | None = None) -> dict[date, DayReport]:
    path = path or config.HISTORY_PATH
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    return {date.fromisoformat(k): report_from_dict(v) for k, v in raw.items()}


def save(store: dict[date, DayReport], path: Path | None = None) -> None:
    path = path or config.HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {d.isoformat(): report_to_dict(r) for d, r in sorted(store.items())}
    path.write_text(json.dumps(out, indent=2))


def upsert(store: dict[date, DayReport], report: DayReport) -> None:
    """Insert or overwrite the report for its date."""
    store[report.date] = report
