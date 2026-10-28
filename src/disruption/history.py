"""Persist every computed day report, forever, in a SQLite database.

The .ics is rebuilt from scratch each run and Realtime Trains only serves recent dates,
so past days must be remembered between runs. Other apps (status page, MCP server) read
the same file, so the schema is the contract: ``journeys`` and ``calls`` hold every
train, and the ``legs`` view reduces them to one row per origin/destination pair.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config
from .models import Call, DayReport, Journey, TrainOption

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    date TEXT PRIMARY KEY,
    am_total INTEGER NOT NULL, am_disrupted INTEGER NOT NULL,
    pm_total INTEGER NOT NULL, pm_disrupted INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS day_disruptions (
    date TEXT NOT NULL REFERENCES days(date),
    window TEXT NOT NULL,
    departure TEXT NOT NULL,
    disrupted INTEGER NOT NULL,
    reason TEXT,
    cancelled INTEGER NOT NULL,
    delay_minutes INTEGER
);
CREATE TABLE IF NOT EXISTS journeys (
    uid TEXT PRIMARY KEY,
    run_date TEXT NOT NULL REFERENCES days(date),
    window TEXT NOT NULL,
    cancelled INTEGER NOT NULL,
    reason TEXT,
    final INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    uid TEXT NOT NULL REFERENCES journeys(uid),
    seq INTEGER NOT NULL,
    station TEXT NOT NULL,
    booked_dep TEXT, actual_dep TEXT, booked_arr TEXT, actual_arr TEXT,
    cancelled INTEGER NOT NULL,
    PRIMARY KEY (uid, seq)
);
CREATE VIEW IF NOT EXISTS legs AS
WITH am AS (
    SELECT j.uid, j.run_date, j.window, j.cancelled AS journey_cancelled, j.reason,
           o.station AS from_station, d.station AS to_station, o.seq AS from_seq, d.seq AS to_seq
    FROM journeys j
    JOIN calls o ON o.uid = j.uid AND o.station = 'BXY'
        AND o.seq = (SELECT MIN(seq) FROM calls WHERE uid = j.uid AND station = 'BXY')
    JOIN calls d ON d.uid = j.uid AND d.station IN ('LBG', 'CST', 'CHX')
        AND d.seq = (SELECT MIN(seq) FROM calls c WHERE c.uid = j.uid AND c.station = d.station AND c.seq > o.seq)
    WHERE j.window = 'am'
), pm AS (
    SELECT j.uid, j.run_date, j.window, j.cancelled AS journey_cancelled, j.reason,
           o.station AS from_station, d.station AS to_station, o.seq AS from_seq, d.seq AS to_seq
    FROM journeys j
    JOIN calls d ON d.uid = j.uid AND d.station = 'BXY'
        AND d.seq = (SELECT MAX(seq) FROM calls WHERE uid = j.uid AND station = 'BXY')
    JOIN calls o ON o.uid = j.uid AND o.station IN ('LBG', 'CST', 'CHX')
        AND o.seq = (SELECT MAX(seq) FROM calls c WHERE c.uid = j.uid AND c.station = o.station AND c.seq < d.seq)
    WHERE j.window <> 'am'
), pairs AS (
    SELECT * FROM am UNION ALL SELECT * FROM pm
), timed AS (
    SELECT p.uid, p.run_date, p.window, p.from_station, p.to_station, p.reason,
           p.journey_cancelled, o.cancelled AS from_cancelled, d.cancelled AS to_cancelled,
           o.booked_dep, o.actual_dep, d.booked_arr, d.actual_arr,
           CASE WHEN d.actual_arr IS NOT NULL AND d.booked_arr IS NOT NULL
                THEN CAST(ROUND((julianday(d.actual_arr) - julianday(d.booked_arr)) * 1440) AS INTEGER)
           END AS delay_minutes
    FROM pairs p
    JOIN calls o ON o.uid = p.uid AND o.seq = p.from_seq
    JOIN calls d ON d.uid = p.uid AND d.seq = p.to_seq
)
SELECT uid, run_date, window, from_station, to_station, booked_dep, actual_dep, booked_arr, actual_arr,
       delay_minutes, reason,
       CASE
           WHEN journey_cancelled OR from_cancelled THEN 'cancelled'
           WHEN to_cancelled THEN 'terminated_early'
           WHEN delay_minutes IS NULL THEN 'pending'
           WHEN delay_minutes > 5 THEN 'late'
           ELSE 'ran'
       END AS status
FROM timed;
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.TRAINS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _train_from_dict(d: dict) -> TrainOption:
    return TrainOption(
        departure=datetime.fromisoformat(d["departure"]),
        disrupted=d["disrupted"],
        reason=d.get("reason"),
        cancelled=d.get("cancelled", False),
        delay_minutes=d.get("delay_minutes"),
    )


def _call_from_dict(d: dict) -> Call:
    return Call(
        booked_dep=_time(d.get("booked_dep")),
        actual_dep=_time(d.get("actual_dep")),
        booked_arr=_time(d.get("booked_arr")),
        actual_arr=_time(d.get("actual_arr")),
        cancelled=d.get("cancelled", False),
    )


def journey_from_dict(d: dict) -> Journey:
    return Journey(
        uid=d["uid"],
        run_date=date.fromisoformat(d["run_date"]),
        window=d["window"],
        cancelled=d["cancelled"],
        reason=d.get("reason"),
        calls={crs: _call_from_dict(c) for crs, c in d["calls"].items()},
    )


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


def import_json(path: Path) -> dict[date, DayReport]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    return {date.fromisoformat(k): report_from_dict(v) for k, v in raw.items()}


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def load(path: Path | None = None) -> dict[date, DayReport]:
    conn = connect(path)
    store: dict[date, DayReport] = {}
    for row in conn.execute("SELECT date, am_total, am_disrupted, pm_total, pm_disrupted FROM days"):
        d = date.fromisoformat(row[0])
        store[d] = DayReport(d, am_total=row[1], am_disrupted=row[2], pm_total=row[3], pm_disrupted=row[4])
    for day, window, departure, disrupted, reason, cancelled, delay in conn.execute(
        "SELECT date, window, departure, disrupted, reason, cancelled, delay_minutes "
        "FROM day_disruptions ORDER BY departure"
    ):
        train = TrainOption(datetime.fromisoformat(departure), bool(disrupted), reason, bool(cancelled), delay)
        report = store[date.fromisoformat(day)]
        (report.am_disrupted_trains if window == "am" else report.pm_disrupted_trains).append(train)
    journeys = {}
    for uid, run_date, window, cancelled, reason in conn.execute(
        "SELECT uid, run_date, window, cancelled, reason FROM journeys ORDER BY run_date, uid"
    ):
        journey = Journey(uid, date.fromisoformat(run_date), window, bool(cancelled), reason, {})
        journeys[uid] = journey
        store[journey.run_date].trains.append(journey)
    for uid, station, booked_dep, actual_dep, booked_arr, actual_arr, cancelled in conn.execute(
        "SELECT uid, station, booked_dep, actual_dep, booked_arr, actual_arr, cancelled FROM calls ORDER BY uid, seq"
    ):
        journeys[uid].calls[station] = Call(
            _time(booked_dep), _time(actual_dep), _time(booked_arr), _time(actual_arr), bool(cancelled)
        )
    conn.close()
    return store


def save(store: dict[date, DayReport], path: Path | None = None) -> None:
    conn = connect(path)
    with conn:
        for table in ("calls", "journeys", "day_disruptions", "days"):
            conn.execute(f"DELETE FROM {table}")
        for d, r in sorted(store.items()):
            conn.execute(
                "INSERT INTO days VALUES (?, ?, ?, ?, ?)",
                (d.isoformat(), r.am_total, r.am_disrupted, r.pm_total, r.pm_disrupted),
            )
            for window, trains in (("am", r.am_disrupted_trains), ("pm", r.pm_disrupted_trains)):
                for t in trains:
                    conn.execute(
                        "INSERT INTO day_disruptions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (d.isoformat(), window, t.departure.isoformat(), t.disrupted, t.reason,
                         t.cancelled, t.delay_minutes),
                    )
            for j in r.trains:
                conn.execute(
                    "INSERT INTO journeys VALUES (?, ?, ?, ?, ?, ?)",
                    (j.uid, j.run_date.isoformat(), j.window, j.cancelled, j.reason, j.final),
                )
                for seq, (station, c) in enumerate(j.calls.items()):
                    conn.execute(
                        "INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (j.uid, seq, station, _iso(c.booked_dep), _iso(c.actual_dep),
                         _iso(c.booked_arr), _iso(c.actual_arr), c.cancelled),
                    )
    conn.close()


def upsert(store: dict[date, DayReport], report: DayReport) -> None:
    store[report.date] = report
