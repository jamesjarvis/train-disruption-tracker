"""Tests for the iCalendar writer (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from disruption.ics_writer import _escape, _fold, _group_by_reason, build_calendar
from disruption.models import DayReport, TrainOption

NOW = datetime(2026, 6, 29, 6, 0, 0, tzinfo=timezone.utc)


def _train(d: date, hh: int, mm: int, reason: str | None) -> TrainOption:
    return TrainOption(datetime.combine(d, time(hh, mm)), True, reason)


def _affected() -> DayReport:
    d = date(2026, 7, 5)
    return DayReport(d, am_total=6, am_disrupted=3, pm_total=10, pm_disrupted=0,
                     am_disrupted_trains=[_train(d, 7, 28, "Rail replacement bus"),
                                          _train(d, 7, 58, "Rail replacement bus"),
                                          _train(d, 8, 14, "Rail replacement bus")])


def _clean() -> DayReport:
    return DayReport(date(2026, 7, 6), am_total=20, am_disrupted=0,
                     pm_total=22, pm_disrupted=0)


def test_only_affected_days_become_events():
    ics = build_calendar([_affected(), _clean()], now=NOW)
    assert ics.count("BEGIN:VEVENT") == 1
    assert "SUMMARY:Bexley trains AM 50% / PM 0% disrupted" in ics


def test_description_lists_trains_grouped_by_reason():
    # Unfold (drop CRLF+space continuations) so folded long lines read contiguously.
    ics = build_calendar([_affected()], now=NOW).replace("\r\n ", "")
    assert "London-bound AM - 3/6 disrupted (50%)" in ics
    # Three trains share one reason => one grouped line with all three times.
    assert "Rail replacement bus: 07:28\\, 07:58\\, 08:14" in ics
    assert "Bexley-bound PM - 0/10 disrupted (0%)" in ics


def test_group_by_reason_collapses_and_defaults():
    d = date(2026, 7, 5)
    grouped = _group_by_reason([
        _train(d, 7, 28, "Bus"), _train(d, 7, 58, "Bus"), _train(d, 8, 0, None),
    ])
    assert grouped == [("Bus", ["07:28", "07:58"]), ("Disrupted", ["08:00"])]


def test_affected_event_has_valarm_clean_does_not():
    affected = build_calendar([_affected()], now=NOW)
    assert "BEGIN:VALARM" in affected
    assert "TRIGGER;RELATED=START:-PT4H" in affected
    assert "BEGIN:VALARM" not in build_calendar([_clean()], now=NOW)


def test_all_day_event_uses_date_values_and_stable_uid():
    ics = build_calendar([_affected()], now=NOW)
    assert "DTSTART;VALUE=DATE:20260705" in ics
    assert "DTEND;VALUE=DATE:20260706" in ics  # exclusive end => single day
    assert "UID:2026-07-05@bexside-trains" in ics


def test_wellformed_envelope_and_crlf():
    ics = build_calendar([_affected()], now=NOW)
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "\r\n" in ics and "VERSION:2.0" in ics


def test_empty_when_no_disruption():
    ics = build_calendar([_clean()], now=NOW)
    assert "BEGIN:VEVENT" not in ics
    assert "BEGIN:VCALENDAR" in ics  # still a valid empty calendar


def test_text_escaping_of_commas_and_semicolons():
    # Escaping happens before line folding, so assert on _escape directly.
    assert _escape("Bus; replacement, all day") == "Bus\\; replacement\\, all day"
    assert _escape("a\\b\nc") == "a\\\\b\\nc"


def test_long_line_is_folded_to_75_octets():
    folded = _fold("X" * 200)
    for ln in folded.split("\r\n"):
        assert len(ln.encode("utf-8")) <= 75


def test_folding_never_splits_a_multibyte_char():
    # En-dashes (3 bytes) packed so a naive octet split would land mid-char.
    line = "DESCRIPTION:" + "Dartford–Gravesend " * 8
    folded = _fold(line)
    # Must still be decodable and round-trip to the original once unfolded.
    assert folded.encode("utf-8").decode("utf-8") == folded
    unfolded = folded.replace("\r\n ", "")
    assert unfolded == line
    for ln in folded.split("\r\n"):
        assert len(ln.encode("utf-8")) <= 75


def test_feed_is_ascii_only_endash_transliterated():
    # GitHub Pages serves the feed without a charset, so it must be pure ASCII.
    d = date(2026, 7, 5)
    rep = DayReport(d, am_total=6, am_disrupted=1, pm_total=10, pm_disrupted=0,
                    am_disrupted_trains=[
                        _train(d, 7, 28,
                               "Rail replacement bus between Dartford–Gravesend all day")])
    ics = build_calendar([rep], now=NOW)
    ics.encode("ascii")  # raises if any non-ASCII byte slipped into the feed
    assert "Dartford-Gravesend" in ics.replace("\r\n ", "")  # en-dash -> hyphen


def test_calendar_name_present_as_both_properties():
    ics = build_calendar([_affected()], now=NOW)
    assert "NAME:Bexley Trains Disruptions" in ics
    assert "X-WR-CALNAME:Bexley Trains Disruptions" in ics
