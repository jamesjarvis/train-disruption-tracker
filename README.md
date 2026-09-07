# Train Disruption Tracker

Publishes a daily disruption ratio for the Bexley ↔ London commute as an iCalendar feed,
served from GitHub Pages. Subscribe once in any calendar app; days with disrupted trains
show up as all-day events, clean days show nothing.

## Subscribe

The feed lives at:

```
https://jamesjarvis.github.io/train-disruption-tracker/disruptions.ics
```

- Apple Calendar (iPhone/Mac): open the [webcal link](webcal://jamesjarvis.github.io/train-disruption-tracker/disruptions.ics)
  and confirm, or File → New Calendar Subscription → paste the URL. Leave "ignore alerts"
  unticked to get a notification at 20:00 the evening before a disrupted day.
- [Google Calendar](https://calendar.google.com/calendar/r?cid=webcal://jamesjarvis.github.io/train-disruption-tracker/disruptions.ics),
  or Other calendars → From URL. Google strips alerts from subscribed feeds, so you get
  the events but no evening-before notification.
- Anything else: add a calendar subscription pointing at the URL above.

Events look like `Bexley trains AM 100% / PM 100% disrupted`, with a description listing
the affected trains grouped by reason (`Cancelled: 07:28, 07:58` / `Delayed 7 min: 08:14`)
for each peak window. UIDs are stable per day, so events update in place.

## What it measures

Two peak windows, both via the London Bridge hub (every peak train calls there, whether
it terminates at Charing Cross or Cannon Street):

- AM peak: Bexley → London, 07:00–10:00
- PM peak: London → Bexley, 17:00–22:00

A train is disrupted if it is cancelled, departs more than 5 minutes late, or its planned
itinerary uses a rail-replacement bus.

Two sources feed that:

- `src/disruption/rtt.py` — the Realtime Trains API (`api.rtt.io`). Free JSON, HTTP basic
  auth, booked vs realtime departure times and cancellation status. Covers yesterday,
  today and tomorrow, and is the source of the cancelled / late signal.
- `src/disruption/scraper.py` — the National Rail journey planner
  (`ojp.nationalrail.co.uk/service/timesandfares`). Server-rendered HTML, future-dated,
  no signup. Covers tomorrow to 20 days out, and is the source of planned engineering
  works and replacement buses.

The feed keeps a rolling 60 days of past days alongside the look-ahead, so it also serves
as a record of how the line actually ran. Past days carry no alarm; future days alarm at
20:00 the evening before.

## How it works

```
main.py → history.load()
        → analyze.build_actual_report(D-1..D)   via rtt.fetch_trains      (actual)
        → analyze.build_merged_report(D+1)       rtt + scraper, merged     (both)
        → analyze.build_day_report(D+2..+20)     via scraper.fetch_trains  (planned)
        → history.upsert / prune (keep 60 days) / save
        → ics_writer.write_calendar(reports)     rebuilt from scratch each run
```

History lives in a git-ignored `state/history.json`; the published `.ics` is the durable
record.

Both sources guard against silent breakage. If neither parses any trains for a day, they
log a warning and keep the last stored value rather than writing a falsely-clean feed.
The scraper adds a content-vs-parse backstop: if a planner page clearly describes a
replacement bus (`sprite-bus`, "replacement bus", "made by bus") yet nothing parses as
disrupted, it raises instead of reporting a clean day, so a markup change that moves the
bus markers can't hide disruption.

The planner renders each journey as a `tr.mtx` summary row followed by detail rows
(`tr.changes`, `tr.status`) carrying the disruption markers, so the scraper parses each
journey as that whole group rather than the summary row alone. A journey is disrupted
when the group contains a `.disruptiondesc` block (the planner only emits one when
something is wrong), and the reason is that block's short `<h4 class="title">` label,
not its verbose body text.

If a live-window day (today or tomorrow) can't be refreshed from any source — RTT dark
and the planner failing — the feed emits `[!] Bexley trains: data unavailable — check
live times` for that day rather than serving the last stored value. It clears on the next
good run.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest        # no network
```

Realtime Trains credentials come from `RTT_USERNAME` / `RTT_PASSWORD`, or a git-ignored
`secrets/rtt.json` (`{"username": "...", "password": "..."}`), which is what the deployed
job uses. Sign up free at <https://api.rtt.io/>. Without credentials the run still works,
but only reports planner disruption — no actual cancellations or delays.

```bash
./.venv/bin/python -m disruption.main                      # writes docs/disruptions.ics
./.venv/bin/python -m disruption.main --dry-run            # print the .ics, write nothing
./.venv/bin/python -m disruption.main --horizon 14         # look further ahead
./.venv/bin/python -m disruption.main --output /tmp/x.ics  # custom path
```

`run.sh` wraps that: regenerate the feed, then commit and push `docs/disruptions.ics` so
Pages serves the update. It needs a git remote and non-interactive auth (SSH key or a
stored credential helper).

To publish your own fork, push it to GitHub and set Settings → Pages → Source to the
`main` branch, `/docs` folder. The feed is then at
`https://<you>.github.io/<repo>/disruptions.ics`.

Stations, peak windows, horizon, calendar name and politeness delays live in
`src/disruption/config.py`.

## Deployment (Raspberry Pi, systemd)

The job runs on the Pi (`ssh pi@pi`) rather than a laptop, so the feed refreshes whether
or not a Mac is awake. Everything it needs is under `/srv/disruption`.

| Path | What it is |
|---|---|
| `/srv/disruption/repo` | Shallow clone of this repo, branch `main` |
| `/srv/disruption/repo/state/history.json` | Rolling 60-day history. Not in git |
| `/srv/disruption/ssh/id_ed25519` | GitHub deploy key for this repo only. `pi:pi`, mode 600 |
| `/srv/disruption/.gitconfig` | Commit identity for the automated pushes |
| `/etc/train-disruption.env` | Realtime Trains credentials. `root:root`, mode 600 |

`train-disruption.timer` fires `train-disruption.service` at 06:00, 08:00 … 20:00 local
time, with `Persistent=true` so a run missed while the Pi was off happens at next boot,
and a randomised delay of up to 3 minutes so the planner is not hit on the exact hour.
The service is `Type=oneshot`, runs as `pi`, and is sandboxed with `ProtectSystem=strict`
and `ProtectHome=true`, leaving `/srv/disruption` as its only writable path.

Pushing uses a deploy key passed through `GIT_SSH_COMMAND` in the unit rather than
`~/.ssh/config`, so no other git operation on the Pi is affected by it.

### Installing from scratch

```bash
sudo install -d -o pi -g pi -m 700 /srv/disruption/ssh
sudo -u pi ssh-keygen -t ed25519 -f /srv/disruption/ssh/id_ed25519 -N '' \
    -C 'train-disruption-tracker@pi'
cat /srv/disruption/ssh/id_ed25519.pub
```

Add that public key to the repo's Settings → Deploy keys with *Allow write access*, then
write `/etc/train-disruption.env`:

```
RTT_USERNAME=...
RTT_PASSWORD=...
```

Then run the installer, which is idempotent:

```bash
git clone git@github.com:jamesjarvis/train-disruption-tracker.git /tmp/tdt
sudo /tmp/tdt/deploy/install-pi.sh
```

It clones to `/srv/disruption/repo`, builds the venv, installs both units and enables the
timer. After that the repo is self-hosting:
`sudo /srv/disruption/repo/deploy/install-pi.sh`.

Seed the history by copying `state/history.json` onto the Pi; without it the rolling
window rebuilds from the published `.ics` plus new runs.

```bash
systemctl list-timers train-disruption.timer   # when it next runs
systemctl status train-disruption.service      # last run
journalctl -u train-disruption -n 50           # run history
sudo systemctl start train-disruption.service  # run now
```

## Limitations

- If National Rail changes the planner's HTML the scraper raises, that day's planner
  refresh is skipped and logged, and the last stored value is kept.
- The planner is future-dated and can't see same-day cancellations or delays; that's what
  RTT is for, but RTT only covers recent dates. So tomorrow shows pre-cancellations and
  planned engineering only, not predicted delays.
- `delay_minutes` is the departure delay at the origin, not arrival lateness at the
  destination.
- History is machine-local, so moving the job to a fresh machine rebuilds the rolling
  window from the published `.ics` plus new runs.
