# Train Disruption Tracker

Scrapes the National Rail journey planner each morning and publishes a **daily
engineering-disruption ratio** for the Bexley ↔ London commute as an iCalendar feed
(published to GitHub Pages) your family can subscribe to — no accounts, no OAuth.

For each of the next ~10 days it measures:

- **AM peak** — Bexley → London (07:00–10:00), via the London Bridge hub
- **PM peak** — London → Bexley (17:00–22:00), via the London Bridge hub

A train counts as **disrupted** if its itinerary uses a rail-replacement bus or is
cancelled. On any day with non-zero disruption it creates one all-day calendar event,
e.g. `🚆 Bexley AM 100% / PM 100% disrupted`, with the details in the description.
Clean days get no event; if a previously-flagged day later clears, its event is deleted.

## How it works

```
main.py → analyze.build_day_report(day)
            → scraper.fetch_trains(BXY→LBG, AM)  ─┐ count disrupted / total
            → scraper.fetch_trains(LBG→BXY, PM)  ─┘
        → calendar_sync.reconcile(reports)  (idempotent create/update/delete)
```

Data source: `ojp.nationalrail.co.uk/service/timesandfares/...` — server-rendered HTML,
future-dated, free, no signup. All National-Rail-specific parsing is isolated in
`src/disruption/scraper.py`; if the site changes, that is the only file to fix. A
"no parseable trains" guard makes a silent breakage log a warning instead of writing a
falsely-clean feed.

## Setup

### 1. Install

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest        # 14 tests, no network
```

### 2. Generate the feed

```bash
./.venv/bin/python -m disruption.main           # writes docs/disruptions.ics
./.venv/bin/python -m disruption.main --dry-run # print the .ics, write nothing
```

### 3. Publish via GitHub Pages (no accounts, no auth)

```bash
git init && git add -A && git commit -m "Initial commit"
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

In the GitHub repo: **Settings → Pages → Source = `main` branch, `/docs` folder**.
The feed is then served at:

```
https://<you>.github.io/<repo>/disruptions.ics
```

### 4. Family subscribes

Share this URL (swap `https` for `webcal` so phones auto-add):

```
webcal://<you>.github.io/<repo>/disruptions.ics
```

- **Google Calendar:** Other calendars → **From URL** → paste the `https://...ics`.
- **Apple Calendar:** File → **New Calendar Subscription** → paste the `webcal://...`.

Clients re-fetch on their own schedule; events update in place (stable per-day UIDs).

## Usage

```bash
./.venv/bin/python -m disruption.main              # scrape + write the feed
./.venv/bin/python -m disruption.main --dry-run    # print the .ics, write nothing
./.venv/bin/python -m disruption.main --horizon 14 # look further ahead
./.venv/bin/python -m disruption.main --output /tmp/x.ics  # custom path
```

`run.sh` wraps this: it regenerates the feed, then commits and pushes
`docs/disruptions.ics` so GitHub Pages serves the update. It needs git configured with a
remote and non-interactive auth (SSH key, or a stored credential helper).

## Scheduling (launchd, daily 06:00)

The committed `com.bexside.disruption.plist.example` uses a `__PROJECT_DIR__`
placeholder. Generate the real (git-ignored) plist by substituting this repo's absolute
path, then load it:

```bash
sed "s#__PROJECT_DIR__#$(pwd)#g" com.bexside.disruption.plist.example \
    > com.bexside.disruption.plist
cp com.bexside.disruption.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bexside.disruption.plist
launchctl kickstart -k gui/$(id -u)/com.bexside.disruption   # run now to test
tail -f logs/run.log
```

If the Mac is asleep at 06:00, launchd runs the missed job on next wake. To remove:

```bash
launchctl bootout gui/$(id -u)/com.bexside.disruption
```

## Tuning

Stations, peak windows, horizon, calendar name, and politeness delays all live in
`src/disruption/config.py`.

## Limitations

- Scraping breaks if National Rail changes the planner's HTML — `scraper.py` raises and
  the day is skipped (logged), so a break is visible rather than silently "clean".
- "Cancelled with no alternative" trains simply don't appear in the planner; the primary
  signal is replacement-bus substitution, which does appear.
