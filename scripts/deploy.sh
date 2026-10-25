#!/usr/bin/env bash
#
# deploy.sh -- put the latest origin/v2 on the droplet checkout, safely.
#
#   On the droplet (as root):   bash /home/recon/recon-v2/scripts/deploy.sh [--dry-run]
#   From the desktop:           bash scripts/deploy.sh --host root@<droplet> [--dry-run]
#                               (streams this script over ssh and runs it there)
#
# What it does, in order. It stops at the first problem and says why.
#   1. Refuses to run inside the cron window (default 04:30-05:30 KST; the brief starts at 05:00).
#   2. Refuses while a RECON run holds the cron lock (logs/.cron_run.lock).
#   3. Fetches origin/v2 and checks that the checkout can fast-forward (no local edits, no diverged commits).
#   4. Runs the offline tests on the NEW code in a temporary copy, as the recon user. The live checkout is not
#      touched until they pass.
#   5. git pull --ff-only origin v2 (with safe.directory, because root pulls a recon-owned checkout).
#   6. chown -R recon:recon on the checkout.
#   7. Writes deploy/last-deploy.json (deployed_sha, previous_sha, deployed_at). The orchestrator copies
#      deployed_sha into every run.json, so each brief says which code made it.
#
# --dry-run: read-only. Shows what would change (git ls-remote, no fetch, no tests, no writes).
#
# Settings (environment variables, all optional):
#   RECON_DEPLOY_HOST         ssh target when the checkout is not on this machine (same as --host)
#   RECON_DEPLOY_DIR          checkout                       (default /home/recon/recon-v2)
#   RECON_DEPLOY_BRANCH       branch                         (default v2)
#   RECON_DEPLOY_USER         owner of the checkout          (default recon)
#   RECON_DEPLOY_QUIET_START  start of the no-deploy window  (default 04:30, KST)
#   RECON_DEPLOY_QUIET_END    end of the no-deploy window    (default 05:30, KST)
#   RECON_DEPLOY_TZ           zone of that window            (default Asia/Seoul)
#   RECON_DEPLOY_TESTS        test command                   (default: unittest on tests/ and tests/collectors/)
#   RECON_DEPLOY_NOW          pretend clock HH:MM, for tests only
#
set -uo pipefail

DRY=0
HOST="${RECON_DEPLOY_HOST:-}"
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1; ARGS+=("$1") ;;
        --host) HOST="${2:-}"; shift ;;
        --host=*) HOST="${1#--host=}" ;;
        -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
    esac
    shift
done

DIR="${RECON_DEPLOY_DIR:-/home/recon/recon-v2}"
BRANCH="${RECON_DEPLOY_BRANCH:-v2}"
OWNER="${RECON_DEPLOY_USER:-recon}"
QUIET_START="${RECON_DEPLOY_QUIET_START:-04:30}"
QUIET_END="${RECON_DEPLOY_QUIET_END:-05:30}"
WINDOW_TZ="${RECON_DEPLOY_TZ:-Asia/Seoul}"
TESTS="${RECON_DEPLOY_TESTS:-python3 -m unittest discover -s tests && python3 -m unittest discover -s tests/collectors}"

say()  { echo "deploy: $*"; }
stop() { echo "deploy: STOPPED. $*" >&2; exit 1; }

# ── Desktop mode: the checkout is not here, so run this same script on the droplet over ssh ──
if [ ! -d "$DIR/.git" ]; then
    [ -n "$HOST" ] || stop "No checkout at $DIR on this machine. From the desktop, pass --host root@<droplet>."
    say "No checkout here; running on $HOST over ssh."
    pass=()
    for v in RECON_DEPLOY_DIR RECON_DEPLOY_BRANCH RECON_DEPLOY_USER RECON_DEPLOY_QUIET_START RECON_DEPLOY_QUIET_END RECON_DEPLOY_TZ; do
        [ -n "${!v:-}" ] && pass+=("$v=$(printf '%q' "${!v}")")
    done
    # shellcheck disable=SC2029
    exec ssh "$HOST" "${pass[*]:+env ${pass[*]} }bash -s -- ${ARGS[*]:-}" < "${BASH_SOURCE[0]}"
fi

# ── Who runs git: root needs safe.directory on the recon-owned checkout, then gives the files back ──
ME="$(id -un)"
if [ "$(id -u)" -eq 0 ]; then
    AS_OWNER=(runuser -u "$OWNER" --)
    [ "$OWNER" = "$ME" ] && AS_OWNER=()
elif [ "$ME" = "$OWNER" ]; then
    AS_OWNER=()
else
    [ "$DRY" = 1 ] || stop "Run this as root or as $OWNER (you are $ME)."
    AS_OWNER=()
fi
id "$OWNER" >/dev/null 2>&1 || stop "User $OWNER does not exist on this machine."
git_() { git -c "safe.directory=$DIR" -C "$DIR" "$@"; }

# ── 1. Cron window ──
to_min() { local h="${1%%:*}" m="${1##*:}"; echo $(( 10#$h * 60 + 10#$m )); }
in_window() {
    local now s e
    now=$(to_min "${RECON_DEPLOY_NOW:-$(TZ="$WINDOW_TZ" date +%H:%M)}")
    s=$(to_min "$QUIET_START"); e=$(to_min "$QUIET_END")
    if [ "$s" -le "$e" ]; then [ "$now" -ge "$s" ] && [ "$now" -lt "$e" ]
    else [ "$now" -ge "$s" ] || [ "$now" -lt "$e" ]; fi   # a window that crosses midnight
}
NOW_TXT="${RECON_DEPLOY_NOW:-$(TZ="$WINDOW_TZ" date +%H:%M)}"
in_window && stop "It is $NOW_TXT in $WINDOW_TZ, inside the cron window $QUIET_START-$QUIET_END. The daily brief may be running. Try again after $QUIET_END."
say "Time check OK: $NOW_TXT $WINDOW_TZ is outside the cron window $QUIET_START-$QUIET_END."

# ── 2. A run in progress ──
LOCK="$DIR/logs/.cron_run.lock"
if [ -f "$LOCK" ] && command -v flock >/dev/null; then
    flock -n "$LOCK" true || stop "A RECON run is in progress (it holds $LOCK). Wait for it to finish."
fi

# ── 3. What would change ──
OLD=$(git_ rev-parse HEAD) || stop "Could not read the current commit in $DIR."
if [ "$DRY" = 1 ]; then
    NEW=$(git_ ls-remote origin "refs/heads/$BRANCH" | cut -f1)
    [ -n "$NEW" ] || stop "Could not read origin/$BRANCH (network or credentials)."
    if [ "$OLD" = "$NEW" ]; then say "Dry run: already on origin/$BRANCH ${NEW:0:7}. Nothing to deploy."; exit 0; fi
    say "Dry run: would move $DIR from ${OLD:0:7} to ${NEW:0:7} (origin/$BRANCH)."
    [ -n "$(git_ status --porcelain --untracked-files=no)" ] && say "Dry run: WARNING, the checkout has local edits; a real deploy would stop."
    if git_ cat-file -e "$NEW^{commit}" 2>/dev/null; then
        git_ log --oneline "$OLD..$NEW" | head -n 20 | sed 's/^/    /'
    else
        say "Dry run: that commit is not fetched yet, so the list of changes is not shown."
    fi
    say "Dry run: would run the tests, pull, chown -R $OWNER, and write deploy/last-deploy.json. Nothing was changed."
    exit 0
fi

git_ fetch -q origin "$BRANCH" || stop "git fetch origin $BRANCH failed (network or credentials)."
NEW=$(git_ rev-parse "origin/$BRANCH") || stop "Could not read origin/$BRANCH after the fetch."
if [ "$OLD" = "$NEW" ]; then say "Already on origin/$BRANCH ${NEW:0:7}. Nothing to deploy."; exit 0; fi
[ -z "$(git_ status --porcelain --untracked-files=no)" ] || stop "The checkout has local edits to tracked files. Someone changed code on the droplet; look with: git -C $DIR status"
git_ merge-base --is-ancestor "$OLD" "$NEW" || stop "The checkout ${OLD:0:7} is not behind origin/$BRANCH ${NEW:0:7} (diverged). A fast-forward is impossible; sort it out by hand."
say "Will move from ${OLD:0:7} to ${NEW:0:7}:"
git_ log --oneline "$OLD..$NEW" | head -n 20 | sed 's/^/    /'

# ── 4. Offline tests on the new code, in a throwaway copy, as the owner ──
TMP=$(mktemp -d /tmp/recon-deploy.XXXXXX) || stop "Could not make a temporary folder."
trap 'rm -rf "$TMP"' EXIT
git_ archive "$NEW" | tar -x -C "$TMP" || stop "Could not unpack ${NEW:0:7} into $TMP."
[ "$(id -u)" -eq 0 ] && chown -R "$OWNER:$(id -gn "$OWNER")" "$TMP"
say "Running the offline tests on ${NEW:0:7} (about a minute)..."
TEST_LOG="$TMP/tests.log"
( cd "$TMP" && "${AS_OWNER[@]}" env -i PATH="/usr/local/bin:/usr/bin:/bin" HOME="$TMP" LANG=C.UTF-8 \
      bash -c "$TESTS" ) >"$TEST_LOG" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    tail -n 30 "$TEST_LOG" >&2
    stop "Tests failed on ${NEW:0:7} (exit $rc). Nothing was deployed; the droplet still runs ${OLD:0:7}."
fi
TEST_SUMMARY=$(grep -E '^Ran [0-9]+ tests' "$TEST_LOG" | awk '{n+=$2} END {print n+0}')
say "Tests passed ($TEST_SUMMARY tests)."

# The tests take a minute; check the window again before touching the live checkout.
NOW_TXT="${RECON_DEPLOY_NOW:-$(TZ="$WINDOW_TZ" date +%H:%M)}"
in_window && stop "The cron window $QUIET_START-$QUIET_END started while the tests ran. Nothing was deployed."

# ── 5. Pull ──
git_ pull -q --ff-only origin "$BRANCH" || stop "git pull --ff-only failed. The checkout may be half-updated; check: git -C $DIR status"
HEAD_NOW=$(git_ rev-parse HEAD)
[ "$HEAD_NOW" = "$NEW" ] || stop "After the pull HEAD is ${HEAD_NOW:0:7}, expected ${NEW:0:7}."

# ── 6. Give the files back to the owner ──
if [ "$(id -u)" -eq 0 ]; then
    chown -R "$OWNER:$(id -gn "$OWNER")" "$DIR" || stop "chown -R $OWNER on $DIR failed. The code is deployed but some files may be root-owned."
fi

# ── 7. Record what is deployed (read by recon/orchestrator.py into run.json) ──
mkdir -p "$DIR/deploy"
NOW_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
NOW_KST=$(TZ="$WINDOW_TZ" date +%Y-%m-%dT%H:%M:%S%:z)
cat > "$DIR/deploy/last-deploy.json.tmp" <<JSON
{"deployed_sha": "$NEW", "previous_sha": "$OLD", "branch": "$BRANCH", "deployed_at": "$NOW_UTC", "deployed_at_local": "$NOW_KST", "deployed_by": "$ME@$(hostname -s 2>/dev/null || echo unknown)", "tests_passed": $TEST_SUMMARY}
JSON
mv "$DIR/deploy/last-deploy.json.tmp" "$DIR/deploy/last-deploy.json"
[ "$(id -u)" -eq 0 ] && chown -R "$OWNER:$(id -gn "$OWNER")" "$DIR/deploy"

say "Done. $DIR now runs ${NEW:0:7} (was ${OLD:0:7}). The next 05:00 brief uses it."
say "Recorded in $DIR/deploy/last-deploy.json."
