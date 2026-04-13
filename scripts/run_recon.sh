#!/usr/bin/env bash
set -euo pipefail
SECONDS=0

RECON_HOME="/home/recon/recon"
TODAY=$(date +%Y-%m-%d)
RUN_DIR="$RECON_HOME/briefs/$TODAY"
LOG_FILE="$RECON_HOME/logs/${TODAY}.log"
PERSONAS="$RECON_HOME/personas"

source /home/recon/.recon.env
source "$RECON_HOME/scripts/ask_hermes.sh"

AGENTS=(trader narrator builder analyst skeptic regulator user_agent)
ALWAYS_ACTIVE=(skeptic regulator)
TENSIONS=("trader:narrator" "narrator:trader" "builder:user_agent" "user_agent:builder" "analyst:skeptic" "skeptic:analyst")

# Max parallel agent calls (keep under API rate limits / memory)
MAX_PARALLEL=3

mkdir -p "$RUN_DIR" "$(dirname "$LOG_FILE")"

log() { echo "[$(date +%H:%M:%S)] $1" | tee -a "$LOG_FILE"; }

send_telegram() {
    [ -z "${RECON_TELEGRAM_TOKEN:-}" ] && { log "Telegram not configured"; return; }
    local t="$1" i=0 len=${#t}
    while [ $i -lt $len ]; do
        curl -s -X POST "https://api.telegram.org/bot${RECON_TELEGRAM_TOKEN}/sendMessage" \
            -H "Content-Type: application/json" \
            -d "{\"chat_id\":\"${RECON_TELEGRAM_CHAT_ID}\",\"text\":$(echo "${t:$i:4000}" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')}" > /dev/null
        i=$((i+4000)); sleep 1
    done
}

# Helper: run up to MAX_PARALLEL background jobs, wait when full
throttle_wait() {
    while [ "$(jobs -rp | wc -l)" -ge "$MAX_PARALLEL" ]; do
        sleep 2
    done
}

log "=============================================="
log "RECON INTELLIGENCE CELL -- $TODAY"
log "=============================================="
send_telegram "🛰 RECON starting — $TODAY"

# ─── PHASE 0: DATA COLLECTION ──────────────────────────────
log "PHASE 0: Collecting real data..."
"$RECON_HOME/scripts/collect_data.sh"

[ ! -f "$RUN_DIR/00_data_package.md" ] && { log "FATAL: No data package"; exit 1; }
DATA=$(cat "$RUN_DIR/00_data_package.md")
log "Data package: $(echo "$DATA" | wc -c) bytes"

# ─── PHASE 0.5: RELEVANCE FILTER ───────────────────────────
log "PHASE 0.5: Filtering..."
FILTERED=$(ask_hermes "$PERSONAS/analyst.md" \
    "RELEVANCE FILTER MODE. You are receiving a pre-processed intelligence package. It has already been analyzed for sentiment (BettaFish) and geopolitical context (World Monitor).

PRESERVE the sentiment analysis and geopolitical sections intact — agents need this framing.
FILTER the on-chain data, news, and social sections: pass through items relevant to prediction markets, DeFi, crypto, LEVER Protocol (leveraged prediction market perpetuals on Base), and XMarket (UGC prediction market on BNB Chain). Score 3+/10 passes. Keep raw form. Drop irrelevant items.

INTELLIGENCE PACKAGE:
$(echo "$DATA" | head -c 60000)")
echo "$FILTERED" > "$RUN_DIR/01_filtered.md"
log "  Filtered: $(echo "$FILTERED" | wc -c) bytes"

# ─── PHASE 2: RELEVANCE CHECK (parallel) ───────────────────
log "PHASE 2: Agent activation (parallel)..."
declare -A active_agents
declare -A all_takes all_challenges all_responses all_votes

# Write filtered data to temp file so background jobs can read it
FILTERED_FILE="$RUN_DIR/01_filtered.md"

for agent in "${AGENTS[@]}"; do
    skip=false
    for aa in "${ALWAYS_ACTIVE[@]}"; do [[ "$agent" == "$aa" ]] && skip=true; done
    if $skip; then
        active_agents[$agent]=1; log "  $agent: ALWAYS ACTIVE"; continue
    fi

    # Run activation checks in parallel
    throttle_wait
    (
        check=$(ask_hermes "$PERSONAS/$agent.md" \
            "Quick check: review the sentiment summary and key signals below. Anything significant for your domain today? YES or NO, one sentence.
$(head -c 15000 "$FILTERED_FILE")")
        if echo "$check" | grep -qi "yes"; then
            echo "ACTIVE" > "$RUN_DIR/.activation_${agent}"
        else
            echo "SITTING_OUT" > "$RUN_DIR/.activation_${agent}"
        fi
    ) &
done
wait  # Wait for all activation checks

# Collect results
for agent in "${AGENTS[@]}"; do
    result_file="$RUN_DIR/.activation_${agent}"
    [ ! -f "$result_file" ] && continue
    if grep -q "ACTIVE" "$result_file" 2>/dev/null; then
        active_agents[$agent]=1; log "  $agent: ACTIVE"
    else
        log "  $agent: SITTING OUT"
    fi
    rm -f "$result_file"
done
log "  Active: ${!active_agents[*]}"
send_telegram "📡 ${#active_agents[@]} agents active: ${!active_agents[*]}"

# ─── PHASE 3: INDEPENDENT TAKES (parallel) ─────────────────
log "PHASE 3: Independent takes (parallel)..."
for agent in "${!active_agents[@]}"; do
    throttle_wait
    (
        extra=""
        if [[ "$agent" == "analyst" && -f "$RECON_HOME/config/analyst_model.md" ]]; then
            extra="YOUR CURRENT STRUCTURAL MODEL (update if warranted):
$(cat "$RECON_HOME/config/analyst_model.md")

"
        fi

        take=$(ask_hermes "$PERSONAS/$agent.md" \
            "${extra}Analyze today's intelligence package. It has been pre-processed through two systems before reaching you:
- SECTION 1 (SENTIMENT): BettaFish multi-agent sentiment analysis — overall mood, per-source breakdown, trending topics. Use this to calibrate your tone and flag divergences from your own read.
- SECTION 2 (GEOPOLITICAL): World Monitor intelligence — macro events, conflicts, regulatory signals. Consider how this backdrop affects your domain.
- SECTIONS 3-5: On-chain data, news headlines, and social discourse (Reddit/Twitter).

Ground your analysis in the sentiment and geopolitical context. Follow your output format. 200-400 words.

INTELLIGENCE PACKAGE:
$(head -c 50000 "$FILTERED_FILE")")
        echo "$take" > "$RUN_DIR/03_take_${agent}.md"
    ) &
    log "  $agent launched..."
done
wait  # Wait for all takes
log "  All takes complete"

# Load takes into associative array
for agent in "${!active_agents[@]}"; do
    [[ "$agent" == "regulator" ]] && continue
    if [ -f "$RUN_DIR/03_take_${agent}.md" ]; then
        all_takes[$agent]="$(cat "$RUN_DIR/03_take_${agent}.md")"
    fi
done

# ─── PHASE 4A: NATURAL TENSIONS (parallel pairs) ───────────
log "PHASE 4A: Tension challenges (parallel)..."
for agent in "${!all_takes[@]}"; do all_challenges[$agent]=""; done

for pair in "${TENSIONS[@]}"; do
    c="${pair%%:*}"; t="${pair##*:}"
    [[ -z "${all_takes[$c]:-}" || -z "${all_takes[$t]:-}" ]] && continue

    throttle_wait
    (
        ch=$(ask_hermes "$PERSONAS/$c.md" \
            "CHALLENGE ${t^^}'s analysis. What did they get wrong?

YOUR TAKE: ${all_takes[$c]}

${t^^}'S TAKE: ${all_takes[$t]}")
        echo "$ch" > "$RUN_DIR/04a_${c}_vs_${t}.md"
    ) &
    log "  $c -> $t launched..."
done
wait
log "  All challenges complete"

# Load challenges
for pair in "${TENSIONS[@]}"; do
    c="${pair%%:*}"; t="${pair##*:}"
    if [ -f "$RUN_DIR/04a_${c}_vs_${t}.md" ]; then
        all_challenges[$t]+="
--- Challenge from ${c^^} ---
$(cat "$RUN_DIR/04a_${c}_vs_${t}.md")
"
    fi
done

# ─── PHASE 4B: REGULATOR AUDIT ─────────────────────────────
# ─── PHASE 4C: WILDCARD ────────────────────────────────────
# Run these two in parallel since they're independent

log "PHASE 4B+4C: Regulator audit + Wildcard (parallel)..."
reg_audit="Regulator inactive."

# 4B: Regulator audit (background)
if [[ -n "${active_agents[regulator]:-}" ]]; then
    (
        audit_input=""
        for a in "${!all_takes[@]}"; do
            audit_input+="### ${a^^}:
${all_takes[$a]}

"
        done
        ask_hermes "$PERSONAS/regulator.md" "AUDIT all takes for regulatory risk:

$audit_input" > "$RUN_DIR/04b_audit.md"
    ) &
fi

# 4C: Wildcard (background)
if [ ${#all_takes[@]} -ge 4 ]; then
    (
        takes_summary=""
        for a in "${!all_takes[@]}"; do
            takes_summary+="- $a: $(echo "${all_takes[$a]}" | head -c 1000)...
"
        done
        wc_assign=$(ask_hermes "$PERSONAS/synthesizer.md" \
            "Pick ONE unexpected cross-examination between agents NOT in these pairs: trader-narrator, builder-user_agent, analyst-skeptic.

Agents:
$takes_summary

Reply EXACTLY: CHALLENGER: [name] TARGET: [name]" "claude-sonnet-4-20250514")

        wc_c=$(echo "$wc_assign" | grep -oi "challenger: *[a-z_]*" | sed 's/.*: *//' | tr '[:upper:]' '[:lower:]')
        wc_t=$(echo "$wc_assign" | grep -oi "target: *[a-z_]*" | sed 's/.*: *//' | tr '[:upper:]' '[:lower:]')

        if [[ -n "$wc_c" && -n "$wc_t" && -f "$RUN_DIR/03_take_${wc_c}.md" && -f "$RUN_DIR/03_take_${wc_t}.md" ]]; then
            wch=$(ask_hermes "$PERSONAS/$wc_c.md" \
                "WILDCARD: Challenge ${wc_t^^} from your unique perspective.

YOUR TAKE: $(cat "$RUN_DIR/03_take_${wc_c}.md")
${wc_t^^}'S TAKE: $(cat "$RUN_DIR/03_take_${wc_t}.md")")
            echo "$wch" > "$RUN_DIR/04c_wildcard_${wc_c}_vs_${wc_t}.md"
            echo "$wc_c:$wc_t" > "$RUN_DIR/.wildcard_pair"
        fi
    ) &
fi

wait
log "  Audit + wildcard complete"

# Load regulator audit
if [ -f "$RUN_DIR/04b_audit.md" ]; then
    reg_audit="$(cat "$RUN_DIR/04b_audit.md")"
fi

# Load wildcard challenge
if [ -f "$RUN_DIR/.wildcard_pair" ]; then
    wc_pair=$(cat "$RUN_DIR/.wildcard_pair")
    wc_c="${wc_pair%%:*}"; wc_t="${wc_pair##*:}"
    if [ -f "$RUN_DIR/04c_wildcard_${wc_c}_vs_${wc_t}.md" ]; then
        all_challenges[$wc_t]+="
--- Wildcard from ${wc_c^^} ---
$(cat "$RUN_DIR/04c_wildcard_${wc_c}_vs_${wc_t}.md")
"
        log "  Wildcard: $wc_c -> $wc_t"
    fi
    rm -f "$RUN_DIR/.wildcard_pair"
fi

# ─── PHASE 5: RESPONSES (parallel) ─────────────────────────
log "PHASE 5: Responses (parallel)..."
for agent in "${!all_challenges[@]}"; do
    [[ -z "${all_challenges[$agent]}" ]] && continue

    throttle_wait
    (
        resp=$(ask_hermes "$PERSONAS/$agent.md" \
            "DEFEND or CONCEDE. If conceding, tag: 'I am updating my position because [evidence].'

YOUR TAKE: ${all_takes[$agent]}

CHALLENGES: ${all_challenges[$agent]}")
        echo "$resp" > "$RUN_DIR/05_resp_${agent}.md"
    ) &
    log "  $agent launched..."
done
wait
log "  All responses complete"

# Load responses
for agent in "${!all_challenges[@]}"; do
    if [ -f "$RUN_DIR/05_resp_${agent}.md" ]; then
        all_responses[$agent]="$(cat "$RUN_DIR/05_resp_${agent}.md")"
    fi
done

# ─── PHASE 6: CONVERGENCE (parallel) ───────────────────────
log "PHASE 6: Votes (parallel)..."
ctx=""
for a in "${!all_takes[@]}"; do ctx+="### ${a^^}:
${all_takes[$a]}

"; done

for agent in "${!all_takes[@]}"; do
    throttle_wait
    (
        vote=$(ask_hermes "$PERSONAS/$agent.md" \
            "VOTE. Answer:
1. Most important thing to act on today
2. What the market is wrong about
3. Risk nobody is discussing

Debate summary: $ctx")
        echo "$vote" > "$RUN_DIR/06_vote_${agent}.md"
    ) &
    log "  $agent launched..."
done
wait
log "  All votes complete"

# Load votes
for agent in "${!all_takes[@]}"; do
    if [ -f "$RUN_DIR/06_vote_${agent}.md" ]; then
        all_votes[$agent]="$(cat "$RUN_DIR/06_vote_${agent}.md")"
    fi
done

send_telegram "🧠 Debate complete. Synthesizing brief..."

# ─── PHASE 7: SYNTHESIS (OPUS) ─────────────────────────────
log "PHASE 7: Synthesis (Opus 4.6)..."

record="# DEBATE RECORD -- $TODAY

## TAKES
"
for a in "${!all_takes[@]}"; do record+="### ${a^^}
${all_takes[$a]}

"; done

record+="## CHALLENGES & RESPONSES
"
for a in "${!all_challenges[@]}"; do
    [[ -z "${all_challenges[$a]}" ]] && continue
    record+="### To ${a^^}: ${all_challenges[$a]}
Response: ${all_responses[$a]:-none}

"
done

record+="## REGULATOR AUDIT
$reg_audit

## VOTES
"
for a in "${!all_votes[@]}"; do record+="### ${a^^}: ${all_votes[$a]}
"; done

echo "$record" > "$RUN_DIR/07_full_record.md"

brief=$(ask_hermes "$PERSONAS/synthesizer.md" \
    "Produce the RECON Daily Intelligence Brief. Under 1,500 words.

$record" "claude-opus-4-20250514")

echo "$brief" > "$RUN_DIR/07_daily_brief.md"
log "  BRIEF PRODUCED ($(echo "$brief" | wc -w) words)"

# ─── SAVE ANALYST MODEL UPDATE ──────────────────────────────
if [ -f "$RUN_DIR/03_take_analyst.md" ]; then
    log "  Updating analyst model..."
    model_update=$(ask_hermes "$PERSONAS/analyst.md" \
        "You just produced today's analysis. Extract ONLY the structural model updates from your take and format them as a changelog entry.

Output EXACTLY this format (nothing else):
### $TODAY
- [METRIC]: [old value] → [new value] (reason)
- Confidence: [level] (reason for any change)
- Thesis: [unchanged/revised] — [1-sentence summary if revised]

If nothing in your model changed, output:
### $TODAY
- No model update — thesis unchanged

YOUR TAKE:
$(cat "$RUN_DIR/03_take_analyst.md")" "claude-sonnet-4-20250514")

    # Append to model file, keeping it under 100 lines
    echo "" >> "$RECON_HOME/config/analyst_model.md"
    echo "$model_update" >> "$RECON_HOME/config/analyst_model.md"

    # Trim old entries if file gets too long (keep header + last 80 lines)
    model_lines=$(wc -l < "$RECON_HOME/config/analyst_model.md")
    if [ "$model_lines" -gt 100 ]; then
        head -20 "$RECON_HOME/config/analyst_model.md" > "$RECON_HOME/config/analyst_model.md.tmp"
        echo "" >> "$RECON_HOME/config/analyst_model.md.tmp"
        echo "### [older entries trimmed]" >> "$RECON_HOME/config/analyst_model.md.tmp"
        echo "" >> "$RECON_HOME/config/analyst_model.md.tmp"
        tail -60 "$RECON_HOME/config/analyst_model.md" >> "$RECON_HOME/config/analyst_model.md.tmp"
        mv "$RECON_HOME/config/analyst_model.md.tmp" "$RECON_HOME/config/analyst_model.md"
    fi
fi

# ─── DELIVER ────────────────────────────────────────────────
log "DELIVERING..."
send_telegram "$brief"

# ─── ARCHIVE DATA FOR KNOWLEDGE BASE ──────────────────────
log "Archiving daily data..."
ARCHIVE_DIR="$RECON_HOME/archive/$TODAY"
mkdir -p "$ARCHIVE_DIR"

# Archive raw data sources (snapshot for historical analysis)
for src in reddit twitter onchain news worldmonitor bettafish; do
    [ -f "$DATA_DIR/$src/latest.md" ] && cp "$DATA_DIR/$src/latest.md" "$ARCHIVE_DIR/${src}.md"
done

# Archive the brief and debate record
[ -f "$RUN_DIR/07_daily_brief.md" ] && cp "$RUN_DIR/07_daily_brief.md" "$ARCHIVE_DIR/brief.md"
[ -f "$RUN_DIR/07_full_record.md" ] && cp "$RUN_DIR/07_full_record.md" "$ARCHIVE_DIR/debate.md"

# Append to rolling knowledge index
echo "- [$TODAY](archive/$TODAY/brief.md) — $(head -c 200 "$RUN_DIR/07_daily_brief.md" 2>/dev/null | tr '\n' ' ' | head -c 150)" >> "$RECON_HOME/archive/INDEX.md" 2>/dev/null

log "  Archived to $ARCHIVE_DIR"

log "=============================================="
log "RECON COMPLETE in $((SECONDS/60))m $((SECONDS%60))s"
log "Brief: $RUN_DIR/07_daily_brief.md"
log "=============================================="
