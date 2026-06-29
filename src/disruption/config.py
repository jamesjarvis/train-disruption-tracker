"""Static configuration for the disruption tracker.

Everything that might reasonably be tuned lives here so the rest of the code reads
cleanly.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

# --- Stations (CRS codes) -------------------------------------------------
# London Bridge is used as the hub: every peak Bexley<->London train calls there,
# regardless of whether it terminates at Charing Cross or Cannon Street. So a single
# BXY<->LBG query captures all London-bound / Bexley-bound trains in the window.
BEXLEY = "BXY"
LONDON_BRIDGE = "LBG"

# --- Peak windows ---------------------------------------------------------
AM_START = time(7, 0)
AM_END = time(10, 0)
PM_START = time(17, 0)
PM_END = time(22, 0)

# The journey planner returns ~6 trains per request; step the start time across the
# window in 30-minute hops and dedupe to cover the whole window.
WINDOW_STEP_MINUTES = 30

# --- Horizon --------------------------------------------------------------
# "At least three weeks ahead." 20 days comfortably includes the next three
# weekends, when engineering works almost always fall.
HORIZON_DAYS = 20

# --- Scraper politeness ----------------------------------------------------
OJP_BASE = "https://ojp.nationalrail.co.uk/service/timesandfares"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.0  # pause between planner requests
REQUEST_TIMEOUT_SECONDS = 30.0
REQUEST_RETRIES = 3  # attempts per request before giving up on a window
RETRY_BACKOFF_SECONDS = 2.0  # linear backoff: attempt N waits N * this

# --- iCalendar output -----------------------------------------------------
CALENDAR_NAME = "Bexley Trains Disruptions"
CALENDAR_TIME_ZONE = "Europe/London"
ICS_PRODID = "-//bexside//train-disruption-tracker//EN"
# Used to build stable per-day UIDs so re-publishing updates rather than duplicates.
ICS_UID_DOMAIN = "bexside-trains"
# Hint to calendar clients for how often to re-fetch the subscription.
ICS_REFRESH = "PT12H"

# --- Paths ----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# GitHub Pages serves the docs/ folder; the published feed lives here.
OUTPUT_ICS = PROJECT_ROOT / "docs" / "disruptions.ics"
