#!/usr/bin/env bash
#
# cron_run.sh -- cron entry point for the daily brief.
#
# Root's crontab (times are KST, the droplet's zone):
#   0 5 * * * /bin/bash /home/recon/recon-v2/scripts/cron_run.sh
#
# cron runs lines with /bin/sh, which has no `source`; the v1 lines failed on that silently.
# This launcher loads the env file itself, takes a lock so two runs never overlap, logs to
# logs/cron.log, and sends ONE Telegram message when the run fails or no brief lands.
# Arguments go to run_recon.sh. --no-telegram (validation runs) also silences the alert.
#
set -uo pipefail

RECON_HOME="${RECON_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"; export RECON_HOME
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"
export HOME="${HOME:-/root}"
mkdir -p "$RECON_HOME/logs"
exec >>"$RECON_HOME/logs/cron.log" 2>&1

# Env file discovery: same order as run_recon.sh
for _f in "${RECON_ENV:-}" "$RECON_HOME/.recon.env" "$RECON_HOME/../.recon.env" "$HOME/.recon.env" /home/recon/.recon.env; do
    [ -n "$_f" ] && [ -f "$_f" ] && { set -a; source "$_f"; set +a; break; }
done

stamp() { date '+%Y-%m-%d %H:%M:%S'; }
quiet=0
for a in "$@"; do [ "$a" = "--no-telegram" ] && quiet=1; done

alert() {
    echo "[$(stamp)] ALERT: $1"
    if [ "$quiet" = 1 ]; then echo "[$(stamp)] (alert not sent: --no-telegram)"; return; fi
    if [ -z "${RECON_TELEGRAM_TOKEN:-}" ] || [ -z "${RECON_TELEGRAM_CHAT_ID:-}" ]; then
        echo "[$(stamp)] (alert not sent: Telegram not configured)"; return
    fi
    # The token stays in the environment, never on a command line.
    python3 - "$1" <<'PY'
import json, os, sys, urllib.request
data = json.dumps({"chat_id": os.environ["RECON_TELEGRAM_CHAT_ID"], "text": sys.argv[1][:3500]}).encode()
req = urllib.request.Request(
    "https://api.telegram.org/bot" + os.environ["RECON_TELEGRAM_TOKEN"] + "/sendMessage",
    data=data, headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=15)
except Exception as e:
    print(f"telegram alert failed: {type(e).__name__}")
PY
}

exec 9>"$RECON_HOME/logs/.cron_run.lock"
if ! flock -n 9; then
    echo "[$(stamp)] another RECON run holds the lock; not starting a second one"
    exit 0
fi

TODAY=$(date +%Y-%m-%d)
BRIEF="$RECON_HOME/briefs/$TODAY/07_daily_brief.md"
DAY_LOG="$RECON_HOME/logs/$TODAY.log"
started=$(date +%s)
echo "[$(stamp)] cron_run: start run_recon.sh $*"

timeout --kill-after=120 3h /bin/bash "$RECON_HOME/scripts/run_recon.sh" "$@"
rc=$?

landed=0
if [ -f "$BRIEF" ] && [ "$(stat -c %Y "$BRIEF")" -ge "$started" ]; then
    first=$(head -c 400 "$BRIEF")
    grep -q '^# RECON DAILY BRIEF' <<< "$first" && landed=1
fi

mins=$(( ($(date +%s) - started) / 60 ))
if [ "$rc" -ne 0 ] || [ "$landed" -ne 1 ]; then
    why="exit $rc"; [ "$rc" -eq 124 ] && why="timed out after 3 h"
    [ "$rc" -eq 0 ] && why="no brief was written"
    last=$(grep -v '^\s*$' "$DAY_LOG" 2>/dev/null | tail -n 4 | cut -c1-200)
    alert "RECON daily brief failed on $TODAY ($why, ${mins} min). Last log lines:
$last
Log: $DAY_LOG"
    exit 1
fi

echo "[$(stamp)] cron_run: brief landed in ${mins} min ($(wc -w < "$BRIEF") words)"
# Run record for the RUBRIC recon page (read-only on the run files; failure is not fatal)
python3 "$RECON_HOME/recon/export.py" >/dev/null 2>&1 || echo "[$(stamp)] export.py failed (non-fatal)"
exit 0
