#!/bin/bash
set -euo pipefail

REPO_URL="git@github.com:jamesjarvis/train-disruption-tracker.git"
BRANCH="main"
BASE="/srv/disruption"
REPO="$BASE/repo"
SVC_USER="pi"
ENV_FILE="/etc/train-disruption.env"
KEY="$BASE/ssh/id_ed25519"

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo." >&2
    exit 1
fi

GIT_SSH="ssh -i $KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$BASE/ssh/known_hosts"
as_svc() { sudo -u "$SVC_USER" env HOME="$BASE" GIT_SSH_COMMAND="$GIT_SSH" "$@"; }

install -d -o "$SVC_USER" -g "$SVC_USER" -m 755 "$BASE"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 700 "$BASE/ssh"

if [[ ! -f "$KEY" ]]; then
    echo "Missing deploy key at $KEY" >&2
    echo "Create one, then add the .pub to the repo's Settings -> Deploy keys with write access:" >&2
    echo "  sudo -u $SVC_USER ssh-keygen -t ed25519 -f $KEY -N '' -C 'train-disruption-tracker@pi'" >&2
    exit 1
fi
chown "$SVC_USER:$SVC_USER" "$KEY"
chmod 600 "$KEY"

ssh-keyscan -t ed25519 github.com > "$BASE/ssh/known_hosts" 2>/dev/null
chown "$SVC_USER:$SVC_USER" "$BASE/ssh/known_hosts"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE" >&2
    echo "Create it (root:root, mode 600) containing:" >&2
    echo "  RTT_USERNAME=..." >&2
    echo "  RTT_PASSWORD=..." >&2
    exit 1
fi
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"

if [[ -d "$REPO/.git" ]]; then
    as_svc git -C "$REPO" fetch --depth 1 origin "$BRANCH"
    as_svc git -C "$REPO" reset --hard "origin/$BRANCH"
else
    as_svc git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$REPO"
fi

as_svc git config --global user.name "Train Disruption Tracker"
as_svc git config --global user.email "hello@jamesjarvis.io"
as_svc git config --global --replace-all safe.directory "$REPO"

as_svc python3 -m venv "$REPO/.venv"
as_svc "$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
as_svc "$REPO/.venv/bin/python" -m pip install --quiet -e "$REPO"

install -d -o "$SVC_USER" -g "$SVC_USER" -m 755 "$REPO/state"

install -m 644 "$REPO/deploy/train-disruption.service" /etc/systemd/system/
install -m 644 "$REPO/deploy/train-disruption.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now train-disruption.timer

echo
echo "Installed. Next run:"
systemctl list-timers train-disruption.timer --no-pager
