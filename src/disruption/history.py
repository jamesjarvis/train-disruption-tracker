"""Persist computed day reports so the feed can keep ~2 months of history.

The .ics is rebuilt from scratch each run and Realtime Trains only serves recent dates,
so past days must be remembered between runs. This is a tiny JSON store keyed by ISO
date, kept locally (git-ignored). The published .ics remains the durable public record.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from .models import DayReport, TrainOption


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


def report_to_dict(r: DayReport) -> dict:
    return {
        "date": r.date.isoformat(),
        "am_total": r.am_total,
        "am_disrupted": r.am_disrupted,
        "pm_total": r.pm_total,
        "pm_disrupted": r.pm_disrupted,
        "am_disrupted_trains": [_train_to_dict(t) for t in r.am_disrupted_trains],
        "pm_disrupted_trains": [_train_to_dict(t) for t in r.pm_disrupted_trains],
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


def prune(store: dict[date, DayReport], today: date, keep_days: int) -> None:
    """Drop days older than ``today - keep_days`` (in place)."""
    cutoff = today - timedelta(days=keep_days)
    for d in [d for d in store if d < cutoff]:
        del store[d]
