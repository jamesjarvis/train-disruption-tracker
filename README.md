# Train Disruption Tracker

Publishes a **daily disruption ratio** for the Bexley ↔ London commute as an iCalendar
feed (published to GitHub Pages) your family can subscribe to — no accounts, no OAuth.

## 📅 Subscribe

Subscribe once and any day with disrupted trains shows up in your calendar as an
all-day event (e.g. `Bexley trains AM 100% / PM 100% disrupted`). Clean days show
nothing. Your calendar app refreshes the feed automatically — nothing to install,
no account needed.

**On iPhone / Mac (Apple Calendar):**
tap **[📅 Subscribe](webcal://jamesjarvis.github.io/train-disruption-tracker/disruptions.ics)**
and confirm. (Or in Calendar: File → **New Calendar Subscription** → paste the link.)
Leave "ignore alerts" **unticked** and you'll also get a notification at **20:00 the
evening before** a disrupted day.

**Google Calendar:**
tap **[📅 Add to Google Calendar](https://calendar.google.com/calendar/r?cid=webcal://jamesjarvis.github.io/train-disruption-tracker/disruptions.ics)**
— or manually: Other calendars → **From URL** → paste the address below. Note: Google
ignores alerts embedded in subscribed feeds, so you'll see the events but get no
evening-before notification.

**Any other calendar app:** add a calendar subscription pointing at:

```
https://jamesjarvis.github.io/train-disruption-tracker/disruptions.ics
```

---

The feed combines two sources:

- **Realtime Trains** (actual) for **yesterday, today, tomorrow** — real cancellations
  and severe delays as they happen.
- **National Rail journey planner** (advance) for **tomorrow .. ~21 days ahead** —
  planned engineering works / rail-replacement buses.

For each day it measures:

- **AM peak** — Bexley → London (07:00–10:00), via the London Bridge hub
- **PM peak** — London → Bexley (17:00–22:00), via the London Bridge hub

A train counts as **disrupted** if it is **cancelled**, departs **more than 5 minutes
late** (actual data), or its planned itinerary uses a **rail-replacement bus**. On any
day with non-zero disruption it creates one all-day calendar event, e.g. `Bexley trains
AM 100% / PM 100% disrupted`. The description lists the affected trains grouped by reason
(e.g. `Cancelled: 07:28, 07:58` / `Delayed 7 min: 08:14`) for each peak window. Clean
days get no event.

The feed keeps a rolling **~2 months of past days** plus the look-ahead, so it doubles as
a record of how the line actually ran. Past days carry no alert; future days alert the
evening before.

Each affected event also carries an **alert set for 20:00 the evening before**, as
advance warning (Apple Calendar only — see the subscribe notes above).

## How it works

```
main.py → history.load()
        → analyze.build_actual_report(D-1..D)   via rtt.fetch_trains      (actual)
        → analyze.build_merged_report(D+1)       rtt + scraper, merged     (both)
        → analyze.build_day_report(D+2..+21)     via scraper.fetch_trains  (planned)
        → history.upsert / prune (keep 60 days) / save
        → ics_writer.write_calendar(reports)     rebuilt from scratch each run
```

Data sources, each isolated in one module so a site/API change only touches that file:

- **`src/disruption/rtt.py`** — Realtime Trains API (`api.rtt.io`): free JSON, HTTP basic
  auth, gives booked vs realtime departure times and cancellation status. Source of the
  cancelled / >5-min-late signal.
- **`src/disruption/scraper.py`** — `ojp.nationalrail.co.uk/service/timesandfares/...`:
  server-rendered HTML, future-dated, free, no signup. Source of advance engineering /
  replacement-bus disruption.

Both have a "no parseable trains" guard so a silent breakage logs a warning (and keeps
the last stored value for that day) instead of writing a falsely-clean feed. The scraper
also carries a **content-vs-parse backstop**: if a planner page clearly describes a
replacement bus (`sprite-bus` / "replacement bus" / "made by bus") yet no journey parses
as disrupted, it raises rather than report a false clean day — so a future markup change
that moves the bus markers can't silently hide disruption. History lives in a git-ignored
`state/history.json`; the published `.ics` is the durable record.

> The planner renders each journey as a `tr.mtx` summary row followed by detail rows
> (`tr.changes`, `tr.status`) that carry the bus/disruption markers, so `scraper.py`
> parses each journey as that whole group — not the summary row alone. A journey counts
> as disrupted when the group contains a `.disruptiondesc` block (the planner only emits
> one when something is wrong — bus, cancellation, or amendment), and the reason is that
> block's short `<h4 class="title">` label (e.g. "Cancelled"), not its verbose run-on
> body text.

Finally, if a **live-window day (today/tomorrow)** can't be refreshed from *any* source
this run (a total outage — RTT dark *and* the planner failing), the feed emits an
explicit **warning event** (`[!] Bexley trains: data unavailable — check live times`)
for that day instead of silently serving the last stored (often "clean") value. It
carries the same evening-before alarm and clears automatically on the next good run.

## Setup

### 1. Install

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest        # no network
```

### 2. Realtime Trains credentials

Sign up (free, personal use) at <https://api.rtt.io/> and note the issued **username**
and **password**. Provide them either as environment variables:

```bash
export RTT_USERNAME=... RTT_PASSWORD=...
```

…or in a git-ignored `secrets/rtt.json` (preferred for the launchd job):

```json
{ "username": "...", "password": "..." }
```

Without credentials the run still works but only emits planner (advance) disruption — no
actual cancellations/delays.

### 3. Generate the feed

```bash
./.venv/bin/python -m disruption.main           # writes docs/disruptions.ics
./.venv/bin/python -m disruption.main --dry-run # print the .ics, write nothing
```

### 4. Publish via GitHub Pages (no accounts, no auth)

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

### 5. Family subscribes

Share the links from the [📅 Subscribe](#-subscribe) section at the top (swap in your
own `<you>.github.io/<repo>` URL if you forked this). Clients re-fetch on their own
schedule; events update in place (stable per-day UIDs).

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

## Scheduling (launchd, every 2 hours 06:00–20:00)

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

If the Mac is asleep at a fire time, launchd runs the missed job on next wake. To remove:

```bash
launchctl bootout gui/$(id -u)/com.bexside.disruption
```

## Tuning

Stations, peak windows, horizon, calendar name, and politeness delays all live in
`src/disruption/config.py`.

## Limitations

- Scraping breaks if National Rail changes the planner's HTML — `scraper.py` raises and
  that day's planner refresh is skipped (logged); the last stored value is kept.
- The planner is future-dated, so it can't see same-day cancellations or delays — that's
  what the Realtime Trains feed is for, but RTT only covers recent dates. So **tomorrow**
  shows only pre-cancellations + planned engineering, not predicted delays.
- `delay_minutes` is the **departure** delay at the origin station, not arrival lateness
  at the destination.
- History is machine-local (`state/history.json`); if the job moves to a fresh machine,
  the rolling 2-month window rebuilds from whatever is already in the published `.ics`
  plus new runs.
