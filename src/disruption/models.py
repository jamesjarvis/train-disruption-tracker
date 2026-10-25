"""Core data structures shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class TrainOption:
    """One train option, from the journey planner (planned) or Realtime Trains (actual).

    ``disrupted`` is the rollup: a replacement-bus/flagged itinerary (planner), a
    cancellation, or a departure more than 5 minutes late (Realtime Trains).
    """

    departure: datetime  # scheduled departure from the origin station
    disrupted: bool  # bus/flag OR cancelled OR delay_minutes > 5
    reason: str | None = None  # short human description, e.g. "Cancelled", "Delayed 7 min"
    cancelled: bool = False  # actual cancellation (Realtime Trains)
    delay_minutes: int | None = None  # departure delay at origin; None if unknown

    def __hash__(self) -> int:  # dedupe by departure time across stepped requests
        return hash(self.departure)


@dataclass
class DayReport:
    """AM/PM disruption summary for a single date."""

    date: date
    am_total: int = 0
    am_disrupted: int = 0
    pm_total: int = 0
    pm_disrupted: int = 0
    # The actual disrupted trains, so the calendar can list times + reasons.
    am_disrupted_trains: list[TrainOption] = field(default_factory=list)
    pm_disrupted_trains: list[TrainOption] = field(default_factory=list)
    trains: list[Journey] = field(default_factory=list)

    @property
    def affected(self) -> bool:
        return self.am_disrupted > 0 or self.pm_disrupted > 0

    @staticmethod
    def _pct(disrupted: int, total: int) -> int:
        return round(100 * disrupted / total) if total else 0

    @property
    def am_pct(self) -> int:
        return self._pct(self.am_disrupted, self.am_total)

    @property
    def pm_pct(self) -> int:
        return self._pct(self.pm_disrupted, self.pm_total)


@dataclass(frozen=True)
class Call:
    booked_dep: datetime | None
    actual_dep: datetime | None
    booked_arr: datetime | None
    actual_arr: datetime | None
    cancelled: bool = False


@dataclass
class Journey:
    uid: str
    run_date: date
    window: str
    cancelled: bool
    reason: str | None
    calls: dict[str, Call]

    @property
    def end_crs(self) -> str:
        if self.window != "am":
            return "BXY"
        for crs in ("CST", "CHX"):
            if crs in self.calls:
                return crs
        return "LBG"

    @property
    def final(self) -> bool:
        end = self.calls.get(self.end_crs)
        return self.cancelled or (end is not None and end.actual_arr is not None)
