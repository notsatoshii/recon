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
# Pipeline: the Python orchestrator (recon/orchestrator.py, Phase B) since 2026-10-04, after two
# live validation runs. If it fails and no brief landed, it is retried once with --resume (only the
# failed phase and the ones after it run again). The bash pipeline (run_recon.sh) runs on the same
# day's package only when the orchestrator produced no takes, and only after the orchestrator's memory
# snapshot is put back (orchestrator.py --restore-state), so bash never writes a second memory and state
# entry for the day. Tagged runs (--run-id) never fall back to bash: run_recon.sh writes briefs/<today>.
# RECON_PIPELINE=bash in the env file goes back to bash only.
# The Phase C debate (pairs, challenges, responses, crux check) is OFF here: the spread probe failed twice
# (phase-c-spec §0.1, model log) and no replay or c3/c4 run has passed (§15.5, §17.5). Triage, takes and the
# split sheet from the takes still run. The §18 cutover is RECON_DEBATE=1 below (or in the env file).
# A tagged run (--run-id, e.g. the §17.5 c3/c4 runs) leaves RECON_DEBATE unset so the orchestrator's tagged
# default (debate on) applies, and its brief is looked for in briefs/<run-id>/.
# Arguments go to the pipeline. --no-telegram (validation runs) also silences the alert.
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
RUN_ID=""
prev=""
RESUME_ARGS=()
skip_next=0
for a in "$@"; do
    [ "$a" = "--no-telegram" ] && quiet=1
    [ "$prev" = "--run-id" ] && RUN_ID="$a"
    case "$a" in --run-id=*) RUN_ID="${a#--run-id=}" ;; esac
    # the --resume retry drops --from-phase / --resume (mutually exclusive with it)
    if [ "$skip_next" = 1 ]; then skip_next=0
    elif [ "$a" = "--from-phase" ]; then skip_next=1
    elif [ "$a" != "--resume" ] && [ "${a#--from-phase=}" = "$a" ]; then RESUME_ARGS+=("$a")
    fi
    prev="$a"
done

# Debate gate (see above): the env file may set RECON_DEBATE=1 once the gate and the validation runs pass.
# A tagged run leaves it unset: an explicit 0 would override the orchestrator's tagged default (debate on).
if [ -n "$RUN_ID" ]; then
    unset RECON_DEBATE
else
    export RECON_DEBATE="${RECON_DEBATE:-0}"
fi

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
RUN="${RUN_ID:-$TODAY}"
BRIEF="$RECON_HOME/briefs/$RUN/07_daily_brief.md"
DAY_LOG="$RECON_HOME/logs/$RUN.log"
started=$(date +%s)
PIPELINE="${RECON_PIPELINE:-orchestrator}"
echo "[$(stamp)] cron_run: start $PIPELINE (run $RUN, debate ${RECON_DEBATE:-tagged default}) $*"

brief_landed() {
    [ -f "$BRIEF" ] && [ "$(stat -c %Y "$BRIEF")" -ge "$started" ] && head -c 400 "$BRIEF" | grep -q '^# RECON DAILY BRIEF'
}

if [ "$PIPELINE" = "bash" ]; then
    timeout --kill-after=120 3h /bin/bash "$RECON_HOME/scripts/run_recon.sh" "$@"; rc=$?
else
    timeout --kill-after=120 2h python3 "$RECON_HOME/recon/orchestrator.py" "$@"; rc=$?
    # First fallback: one --resume of the orchestrator, which redoes only the failed phase and those after it
    if ! brief_landed; then
        echo "[$(stamp)] orchestrator exit $rc, no brief: retrying once with --resume"
        timeout --kill-after=120 1h python3 "$RECON_HOME/recon/orchestrator.py" "${RESUME_ARGS[@]}" --resume; rc=$?
    fi
    # Last fallback: the bash pipeline, only when the orchestrator produced no takes (a v1 run is ~61 calls),
    # never for a tagged run, and only after the orchestrator's memory snapshot is put back
    if ! brief_landed; then
        if [ -n "$RUN_ID" ]; then
            echo "[$(stamp)] orchestrator --resume exit $rc, no brief for tagged run $RUN_ID: no bash fallback"
        elif [ -f "$RECON_HOME/briefs/$RUN/phases/takes.json" ]; then
            echo "[$(stamp)] orchestrator --resume exit $rc, no brief, but takes exist: no bash fallback (it would redo ~61 calls)"
        else
            echo "[$(stamp)] orchestrator --resume exit $rc, no takes: restoring memory, then run_recon.sh --skip-collect"
            python3 "$RECON_HOME/recon/orchestrator.py" "${RESUME_ARGS[@]}" --restore-state || echo "[$(stamp)] --restore-state failed (exit $?)"
            timeout --kill-after=120 2h /bin/bash "$RECON_HOME/scripts/run_recon.sh" --skip-collect "$@"; rc=$?
        fi
    fi
fi

landed=0
brief_landed && landed=1

mins=$(( ($(date +%s) - started) / 60 ))
if [ "$rc" -ne 0 ] || [ "$landed" -ne 1 ]; then
    why="exit $rc"; [ "$rc" -eq 124 ] && why="timed out"
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
