#!/usr/bin/env bash
#
# phase_c_validate.sh -- the Phase C validation runs on the droplet, in the spec's order
# (docs/v2/phase-c-spec.md §0.1, §15, §17.4b), meant to run detached:
#
#   setsid nohup bash scripts/phase_c_validate.sh >/dev/null 2>&1 < /dev/null & echo $! > logs/phase_c_validate.pid
#
#   1. unit tests (Phase C and the Phase E collectors)          no calls
#   2. lens extras precondition on 09-11 and 10-04 (§0.1)       no calls
#   3. live schema smoke (§17.4b)                               6 FAST calls
#   4. spread probe (§15.0): 09-11-p1, 10-04-p1, -p2, -p3, -p3h ~33 calls, then spread_probe.py
#      -> GAP_MIN; stops here when the gate fails (the inputs get fixed before any debate runs)
#   5. replays (§15.2) with RECON_PAIR_GAP=GAP_MIN: 09-10-c1, 09-11-c1, 10-04-c1, the 10-04-c1s
#      stability rerun (to pairing) and the 09-11-c2 ANALYST triage; replay_report.py
#   6. one live collection into a tagged run folder (phase-e §4: the wired collectors), its view
#      and lens measurement                                    1 FAST call (BettaFish)
#
# Never runs between 04:30 and 06:30 KST (the 05:00 cron run): it stops before a step that would start
# in that window. Progress: logs/phase_c_validate.log; current step: logs/phase_c_validate.status.
# Steps can be skipped with SKIP="1 2 6" (step numbers).
#
set -uo pipefail
RECON_HOME="${RECON_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"; export RECON_HOME
cd "$RECON_HOME" || exit 1
for _f in "${RECON_ENV:-}" "$RECON_HOME/.recon.env" "$RECON_HOME/../.recon.env" "$HOME/.recon.env" /home/recon/.recon.env; do
    [ -n "$_f" ] && [ -f "$_f" ] && { set -a; source "$_f"; set +a; break; }
done
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"
export PYTHONIOENCODING=utf-8 RECON_CODEX_SLIM=1
mkdir -p logs briefs
LOG="$RECON_HOME/logs/phase_c_validate.log"
STATUS="$RECON_HOME/logs/phase_c_validate.status"
P="python3 recon/orchestrator.py"
SKIP="${SKIP:-}"

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
step() { echo "$*" > "$STATUS"; say "== $*"; }
skip() { case " $SKIP " in *" $1 "*) return 0 ;; esac; return 1; }
window_guard() {
    local hm; hm=$(TZ=Asia/Seoul date +%H%M)
    if [ "$hm" -ge 0430 ] && [ "$hm" -lt 0630 ]; then
        step "STOPPED: $1 would start inside 04:30-06:30 KST (the cron run); rerun with SKIP for the done steps"
        exit 4
    fi
}
copy_run() {   # copy_run SRC DST: a rerun on SRC's triage (--from-phase takes)
    rm -rf "briefs/$2"
    cp -a "briefs/$1" "briefs/$2"
    rm -f "briefs/$2/phases/started.txt" "briefs/$2/run.json"
}
replay() {     # replay DAY RUN_ID [extra args]: logs the exit code, never aborts the chain
    local day=$1 rid=$2; shift 2
    window_guard "replay $rid"
    say "run $rid: $P --replay briefs/$day --as-of $day --run-id $rid $*"
    $P --replay "briefs/$day" --as-of "$day" --run-id "$rid" --no-telegram "$@" >> "$LOG" 2>&1
    local rc=$?
    say "run $rid: exit $rc ($(wc -l < "briefs/$rid/phases/calls.jsonl" 2>/dev/null || echo 0) calls)"
    return $rc
}

say "phase_c_validate start at $(git log --oneline -1)"

if ! skip 1; then
    step "1 unit tests"
    python3 -m unittest discover -s tests >> "$LOG" 2>&1 || { step "FAILED: 1 Phase C unit tests"; exit 1; }
    python3 -m unittest discover -s tests/collectors >> "$LOG" 2>&1 || { step "FAILED: 1 collector tests"; exit 1; }
fi

if ! skip 2; then
    step "2 lens extras precondition (§0.1)"
    for d in 2026-09-11 2026-10-04; do
        out=$(python3 tests/lens_extras_probe.py "briefs/$d" --rebuild-view 2>&1); echo "$out" >> "$LOG"
        n=$(echo "$out" | sed -n 's/.*agents >= 2000 B: \([0-9]*\)\/9.*/\1/p')
        zero=$(echo "$out" | awk '$1 ~ /^[a-z_]+$/ && $2 == "0" {print $1}')
        if [ -z "$n" ] || [ "$n" -lt 7 ] || [ -n "$zero" ]; then
            step "FAILED: 2 lens extras on $d ($n/9 at 2 KB; zero: ${zero:-none})"; exit 1
        fi
    done
fi

if ! skip 3; then
    window_guard "schema smoke"
    step "3 live schema smoke (§17.4b, 6 FAST calls)"
    python3 scripts/schema_smoke.py >> "$LOG" 2>&1 || { step "FAILED: 3 schema smoke"; exit 1; }
fi

GAP_MIN="${RECON_PAIR_GAP:-}"
if ! skip 4; then
    step "4 spread probe (§15.0, ~33 calls)"
    RECON_STOP_AFTER=takes replay 2026-09-11 2026-09-11-p1
    RECON_STOP_AFTER=takes replay 2026-10-04 2026-10-04-p1
    copy_run 2026-10-04-p1 2026-10-04-p2
    RECON_STOP_AFTER=takes replay 2026-10-04 2026-10-04-p2 --from-phase takes
    SYNTH_MODEL=$(python3 -c "import sys; sys.path.insert(0, '.'); from recon import llm; print(llm.codex_model('synth')[0])")
    copy_run 2026-10-04-p1 2026-10-04-p3
    RECON_TAKE_AGENTS=skeptic,macro_strategist RECON_MODEL_ANALYST="$SYNTH_MODEL" RECON_STOP_AFTER=takes \
        replay 2026-10-04 2026-10-04-p3 --from-phase takes
    copy_run 2026-10-04-p1 2026-10-04-p3h
    RECON_TAKE_AGENTS=skeptic,macro_strategist RECON_EFFORT_ANALYST=high RECON_STOP_AFTER=takes \
        replay 2026-10-04 2026-10-04-p3h --from-phase takes
    out=$(python3 scripts/spread_probe.py --packages 2026-09-11-p1 2026-10-04-p1 --retest 2026-10-04-p1 2026-10-04-p2 \
          --lens 2026-10-04-p3 2026-10-04-p3h 2>&1); echo "$out" >> "$LOG"
    GAP_MIN=$(echo "$out" | sed -n 's/^GAP_MIN=//p')
    GATE=$(echo "$out" | sed -n 's/^GATE=//p')
    say "probe: GAP_MIN=$GAP_MIN GATE=$GATE"
    if [ "$GATE" != "pass" ]; then
        step "STOPPED: 4 spread probe gate failed (GAP_MIN $GAP_MIN); fix the inputs before the debate runs (§0.1)"
        exit 3
    fi
fi

if ! skip 5; then
    step "5 replays (§15.2, GAP_MIN ${GAP_MIN:-default})"
    [ -n "$GAP_MIN" ] && export RECON_PAIR_GAP="$GAP_MIN"
    replay 2026-09-10 2026-09-10-c1
    replay 2026-09-11 2026-09-11-c1
    replay 2026-10-04 2026-10-04-c1
    copy_run 2026-10-04-c1 2026-10-04-c1s
    RECON_STOP_AFTER=pairing replay 2026-10-04 2026-10-04-c1s --from-phase takes
    RECON_TRIAGE_TIER=analyst RECON_STOP_AFTER=triage replay 2026-09-11 2026-09-11-c2
    python3 scripts/replay_report.py 2026-09-10-c1 2026-09-11-c1 2026-10-04-c1 --old-dir briefs/_old_exports \
        --stability 2026-10-04-c1 2026-10-04-c1s --probe briefs/spread_probe.md >> "$LOG" 2>&1
fi

if ! skip 6; then
    window_guard "live collection"
    E1="$(TZ=Asia/Seoul date +%Y-%m-%d)-e1"
    step "6 live collection into briefs/$E1 (phase-e §4 wiring)"
    RECON_RUN_DIR="$RECON_HOME/briefs/$E1" RECON_LOG_FILE="$RECON_HOME/logs/$E1.log" \
        timeout 5400 /bin/bash scripts/collect_data.sh >> "$LOG" 2>&1
    say "collection exit $?; package $(wc -c < "briefs/$E1/00_data_package.md" 2>/dev/null || echo 0) B, raw $(wc -c < "briefs/$E1/00_raw_data.md" 2>/dev/null || echo 0) B"
    grep -E "^# SECTION|^# (Polymarket|Kalshi|Changelogs|ZDNet Korea) Intelligence|SOURCE STALE" "briefs/$E1/00_data_package.md" >> "$LOG" 2>&1
    grep -E "Polymarket|Kalshi|Changelogs|ZDNet" "logs/$E1.log" | tail -8 >> "$LOG" 2>&1
    python3 scripts/build_agent_package.py "briefs/$E1" >> "$LOG" 2>&1
    python3 tests/lens_extras_probe.py "briefs/$E1" >> "$LOG" 2>&1
fi

step "DONE ($(date '+%H:%M'))"
