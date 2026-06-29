#!/bin/bash
# Daily job: scrape, regenerate the feed, and publish it to GitHub Pages.
# Pushing requires git to be set up with a remote and non-interactive auth
# (SSH key or a stored credential helper). See README.
set -euo pipefail

cd /Users/jamesjarvis/development/train-disruption-tracker

./.venv/bin/python -m disruption.main

git add docs/disruptions.ics
if ! git diff --cached --quiet; then
    git commit -m "chore: update disruption feed ($(date +%F))"
    git push
    echo "Published updated feed."
else
    echo "Feed unchanged; nothing to publish."
fi
