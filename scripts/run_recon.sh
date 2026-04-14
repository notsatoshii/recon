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

DATA_DIR="$RECON_HOME/data-sources"
AGENTS=(trader narrator builder analyst skeptic policy_analyst user_agent macro_strategist ai_engineer)
ALWAYS_ACTIVE=(skeptic)
TENSIONS=("trader:narrator" "narrator:trader" "builder:policy_analyst" "policy_analyst:builder" "analyst:skeptic" "skeptic:analyst" "macro_strategist:user_agent" "user_agent:macro_strategist" "ai_engineer:builder" "builder:ai_engineer")

# Max parallel agent calls (keep under API rate limits / memory)
MAX_PARALLEL=3

# Parse flags
SKIP_COLLECT=false
MODE="brief"  # brief (default), ai-digest, fundraising
for arg in "$@"; do
    case "$arg" in
        --skip-collect) SKIP_COLLECT=true ;;
        --mode) :;; # value handled below
        ai-digest|--mode=ai-digest) MODE="ai-digest" ;;
        fundraising|--mode=fundraising) MODE="fundraising" ;;
    esac
done
# Handle --mode value as next arg
for i in $(seq 1 $#); do
    if [ "${!i}" = "--mode" ]; then
        next=$((i+1))
        [ -n "${!next:-}" ] && MODE="${!next}"
    fi
done

mkdir -p "$RUN_DIR" "$(dirname "$LOG_FILE")"
# Clean old run artifacts from same day (allows re-runs)
rm -f "$RUN_DIR"/03_take_*.md "$RUN_DIR"/04a_*.md "$RUN_DIR"/04c_*.md "$RUN_DIR"/05_resp_*.md "$RUN_DIR"/05_5_*.md "$RUN_DIR"/06_vote_*.md "$RUN_DIR"/07_*.md "$RUN_DIR"/.activation_* 2>/dev/null

log() { echo "[$(date +%H:%M:%S)] $1" | tee -a "$LOG_FILE"; }

send_telegram() {
    [ -z "${RECON_TELEGRAM_TOKEN:-}" ] && { log "Telegram not configured"; return; }
    local text="${1:-}"
    [ -z "$text" ] && return

    # Convert markdown to Telegram HTML and split by section
    python3 -c "
import sys, re, json, urllib.request

text = sys.stdin.read().strip()

# Convert markdown to Telegram HTML
text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

# Strip markdown table separators and horizontal rules
text = re.sub(r'^\|[-| ]+\|$', '', text, flags=re.MULTILINE)
text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)

# Collapse excessive newlines
text = re.sub(r'\n{3,}', '\n\n', text)

# Split into chunks at section boundaries, respecting 4096 char limit
chunks = []
current = ''
for line in text.split('\n'):
    # Start new chunk at bold headers if current chunk is getting long
    if current and line.startswith('<b>') and len(current) > 3000:
        chunks.append(current.strip())
        current = ''
    current += line + '\n'
    if len(current) > 3800:
        chunks.append(current.strip())
        current = ''
if current.strip():
    chunks.append(current.strip())

# Send each chunk
token = '${RECON_TELEGRAM_TOKEN}'
chat_id = '${RECON_TELEGRAM_CHAT_ID}'
for chunk in chunks:
    data = json.dumps({
        'chat_id': chat_id,
        'text': chunk,
        'parse_mode': 'HTML'
    }).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=data,
        headers={'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        # Fallback: send without HTML if parsing fails
        data = json.dumps({
            'chat_id': chat_id,
            'text': chunk
        }).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except:
            pass
    import time; time.sleep(1)
" <<< "$text"
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
send_telegram "RECON starting — $TODAY"

# ─── PHASE -1: SCORE YESTERDAY'S PREDICTIONS ──────────────
log "PHASE -1: Scoring yesterday's predictions..."
source /home/recon/recon-venv/bin/activate 2>/dev/null || true
python3 "$RECON_HOME/scripts/score_yesterday.py" 2>&1 | while read line; do log "  $line"; done
sleep 3

# ─── PHASE 0: DATA COLLECTION ──────────────────────────────
if $SKIP_COLLECT; then
    log "PHASE 0: Skipping data collection (--skip-collect)"
    # Assemble from existing data sources if no package exists
    if [ ! -f "$RUN_DIR/00_data_package.md" ]; then
        log "  Assembling from existing data-sources..."
        DATA_DIR="$RECON_HOME/data-sources"
        echo "# RECON INTELLIGENCE PACKAGE -- $TODAY" > "$RUN_DIR/00_data_package.md"
        echo "## Assembled: $(date +'%H:%M:%S %Z')" >> "$RUN_DIR/00_data_package.md"
        echo "" >> "$RUN_DIR/00_data_package.md"
        for src in reddit twitter onchain news worldmonitor bettafish; do
            [ -f "$DATA_DIR/$src/latest.md" ] && { echo "---"; echo ""; cat "$DATA_DIR/$src/latest.md"; echo ""; } >> "$RUN_DIR/00_data_package.md"
        done
    fi
else
    log "PHASE 0: Collecting real data..."
    "$RECON_HOME/scripts/collect_data.sh"
fi

[ ! -f "$RUN_DIR/00_data_package.md" ] && { log "FATAL: No data package"; exit 1; }
DATA=$(cat "$RUN_DIR/00_data_package.md")
DATA_SIZE=$(echo "$DATA" | wc -c)
log "Data package: $DATA_SIZE bytes"

# Validate data quality — abort if package is suspiciously small
if [ "$DATA_SIZE" -lt 2000 ]; then
    log "WARNING: Data package is only $DATA_SIZE bytes — likely collection failure"
    send_telegram "WARNING: RECON data collection may have failed ($DATA_SIZE bytes). Proceeding with available data."
fi

# ─── PHASE 0.1: LOAD HISTORICAL CONTEXT ────────────────────
log "PHASE 0.1: Loading historical context..."
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d 2>/dev/null || echo "")
HIST_CONTEXT=""

# Try knowledge database first (richest context)
if [ -f "$RECON_HOME/config/knowledge.db" ]; then
    KB_CONTEXT=$(python3 "$RECON_HOME/scripts/knowledge_db.py" context --days 30 2>/dev/null)
    if [ -n "$KB_CONTEXT" ]; then
        HIST_CONTEXT="$KB_CONTEXT
"
        log "  Loaded 7-day context from knowledge database"
    fi
fi

# Yesterday's brief (if knowledge DB didn't have it)
if [ -z "$HIST_CONTEXT" ] && [ -n "$YESTERDAY" ] && [ -f "$RECON_HOME/archive/$YESTERDAY/brief.md" ]; then
    HIST_CONTEXT="## YESTERDAY'S BRIEF ($YESTERDAY)
$(head -c 3000 "$RECON_HOME/archive/$YESTERDAY/brief.md")
"
    log "  Loaded yesterday's brief ($YESTERDAY)"
fi

# Load sector context (landscape document all agents read)
SECTOR_CONTEXT=""
if [ -f "$RECON_HOME/config/sector_context.md" ]; then
    SECTOR_CONTEXT="$(cat "$RECON_HOME/config/sector_context.md")"
    log "  Loaded sector context ($(echo "$SECTOR_CONTEXT" | wc -c) bytes)"
fi

# Save historical context for agents to reference
echo "$HIST_CONTEXT" > "$RUN_DIR/00_historical_context.md"
log "  Historical context: $(echo "$HIST_CONTEXT" | wc -c) bytes"

# Load prediction scorecard if available
SCORECARD=""
if [ -f "$RUN_DIR/00_scorecard.md" ]; then
    SCORECARD="$(cat "$RUN_DIR/00_scorecard.md")"
    log "  Loaded prediction scorecard"
fi

# ─── PHASE 0.5: DATA PASSTHROUGH ──────────────────────────
# Data is already from curated sources (Reddit, Twitter, on-chain, news, World Monitor, BettaFish).
# No LLM filter needed — it was timing out on 60KB input and killing the pipeline.
# Agents receive the full package and decide what's relevant to their domain.
log "PHASE 0.5: Preparing data for agents..."
FILTERED="$DATA"
echo "$FILTERED" > "$RUN_DIR/01_filtered.md"
log "  Data package: $(echo "$FILTERED" | wc -c) bytes (passthrough, no filter)"

# ─── LIGHTWEIGHT MODE BRANCH ──────────────────────────────
# For ai-digest and fundraising modes, skip the full debate pipeline.
# Instead: filter relevant data -> single synthesis call -> deliver.
if [ "$MODE" != "brief" ]; then
    log "MODE: $MODE (lightweight pipeline)"
    FILTERED_FILE="$RUN_DIR/01_filtered.md"

    if [ "$MODE" = "ai-digest" ]; then
        SYNTH_PERSONA="$PERSONAS/synthesizer_ai.md"
        MODE_LABEL="AI DIGEST"
        # Focus on AI/tools data
        MODE_DATA="$(cat "$DATA_DIR/ai_tools/latest.md" 2>/dev/null)

$(grep -A 5000 "AI & TECH NEWS" "$DATA_DIR/news/latest.md" 2>/dev/null || echo "")

$(grep -A 5000 "AI x CRYPTO TOKENS" "$DATA_DIR/onchain/latest.md" 2>/dev/null || echo "")

$(grep -A 5000 "ai_crypto\|ai_tech" "$DATA_DIR/twitter/latest.md" 2>/dev/null || echo "")"
    elif [ "$MODE" = "fundraising" ]; then
        SYNTH_PERSONA="$PERSONAS/synthesizer_fundraising.md"
        MODE_LABEL="FUNDRAISING RADAR"
        # Focus on fundraising + VC data
        MODE_DATA="$(grep -A 5000 "RECENT FUNDRAISING ROUNDS" "$DATA_DIR/onchain/latest.md" 2>/dev/null || echo "")

$(grep -A 5000 "vc_institutional" "$DATA_DIR/twitter/latest.md" 2>/dev/null || echo "")

$(grep -A 5000 "PREDICTION MARKET PROTOCOLS" "$DATA_DIR/onchain/latest.md" 2>/dev/null || echo "")"
    fi

    log "  $MODE_LABEL data: $(echo "$MODE_DATA" | wc -c) bytes"

    # Single analysis pass: extract + filter relevant items
    sleep 3
    analysis=$(ask_hermes "$SYNTH_PERSONA" \
        "Produce the RECON $MODE_LABEL. Include source links for EVERY item.

TODAY: $TODAY

DATA:
$(echo "$MODE_DATA" | head -c 40000)" "claude-opus-4-20250514")

    echo "$analysis" > "$RUN_DIR/07_${MODE}_output.md"
    log "  $MODE_LABEL: $(echo "$analysis" | wc -w) words"

    # Deliver
    send_telegram "$analysis"
    log "$MODE_LABEL delivered to Telegram"

    # Archive
    ARCHIVE_DIR="$RECON_HOME/archive/$TODAY"
    mkdir -p "$ARCHIVE_DIR"
    cp "$RUN_DIR/07_${MODE}_output.md" "$ARCHIVE_DIR/${MODE}.md" 2>/dev/null

    log "=============================================="
    log "RECON $MODE_LABEL COMPLETE in $((SECONDS/60))m $((SECONDS%60))s"
    log "=============================================="
    exit 0
fi

# ─── PHASE 2: ALL AGENTS ACTIVE ───────────────────────────
# Every agent participates every day. No sit-outs — every perspective matters.
log "PHASE 2: All agents active..."
declare -A active_agents
declare -A all_takes all_challenges all_responses all_votes

FILTERED_FILE="$RUN_DIR/01_filtered.md"

for agent in "${AGENTS[@]}"; do
    active_agents[$agent]=1
done
log "  Active: ${!active_agents[*]} (all agents, no sit-outs)"
send_telegram "RECON: all ${#active_agents[@]} agents active"

# ─── PHASE 3: INDEPENDENT TAKES (parallel) ─────────────────
log "PHASE 3: Independent takes (parallel)..."
for agent in "${!active_agents[@]}"; do
    throttle_wait
    (
        sleep 3
        extra=""

        # Load agent's persistent memory (legacy format)
        memory_file="$RECON_HOME/config/agent_memory/${agent}.md"
        if [ -f "$memory_file" ]; then
            extra="YOUR RUNNING MEMORY (items you're tracking, prior predictions, recurring themes):
$(tail -40 "$memory_file")

"
        fi

        # Load agent's state file
        # Map user_agent persona to user state file
        state_name="$agent"
        [[ "$agent" == "user_agent" ]] && state_name="user"
        state_file="$RECON_HOME/config/agent_state/${state_name}_state.md"
        if [ -f "$state_file" ]; then
            extra+="YOUR STATE FROM PREVIOUS SESSIONS:
$(cat "$state_file")

"
        fi

        # Sector context for all agents
        sector_file="$RECON_HOME/config/sector_context.md"
        sector_ctx=""
        if [ -f "$sector_file" ]; then
            sector_ctx="SECTOR CONTEXT (crypto and macro landscape):
$(head -c 8000 "$sector_file")

"
        fi

        # Load historical context for continuity
        hist=""
        if [ -f "$RUN_DIR/00_historical_context.md" ]; then
            hist="
--- HISTORICAL CONTEXT (reference, not re-analyze) ---
$(head -c 3000 "$RUN_DIR/00_historical_context.md")
--- END HISTORICAL CONTEXT ---

"
        fi

        # Include scorecard if available
        scorecard_ctx=""
        if [ -f "$RUN_DIR/00_scorecard.md" ]; then
            scorecard_ctx="
--- YESTERDAY'S PREDICTIONS (check if any of yours were right or wrong) ---
$(head -c 2000 "$RUN_DIR/00_scorecard.md")
--- END PREDICTIONS ---

"
        fi

        take=$(ask_hermes "$PERSONAS/$agent.md" \
            "CITATION RULE (READ FIRST): Only cite numbers that appear in the INTELLIGENCE PACKAGE below. For claims from social media posts or Reddit threads, prefix with 'reportedly' or 'per social media'. Never present unverified social commentary as confirmed fact. If a number isn't in the data, say 'reportedly' — do NOT invent statistics.

${sector_ctx}${extra}${hist}${scorecard_ctx}Analyze today's intelligence package. The data has been processed through:
- SECTION 1 (SENTIMENT): BettaFish sentiment analysis across social media and news.
- SECTION 2 (GEOPOLITICAL): World Monitor intelligence from 79 global sources.
- SECTIONS 3-5: On-chain/market data, news headlines, social discourse, AI developments.

If historical context is provided, reference yesterday's brief — note what changed, what predictions held, what was wrong. Continuity matters.

Follow your output format. 200-400 words. Be specific — cite data points from the package, name sources, give numbers.

Cover the most significant development in YOUR domain today. The ecosystem includes: world events, macro economics, crypto/BTC/ETH, DeFi, stablecoins, AI/ML developments, regulation, prediction markets, fundraising, and infrastructure. Analyze what matters most TODAY — don't default to any single sector.

INTELLIGENCE PACKAGE:
$(head -c 50000 "$FILTERED_FILE")")

        # Validate output — retry once if too short or looks like a refusal
        take_len=${#take}
        if [ "$take_len" -lt 200 ] || echo "$take" | grep -qi "I can't\|I cannot\|as an AI\|I'm sorry"; then
            sleep 3
            take=$(ask_hermes "$PERSONAS/$agent.md" \
                "You must stay in character and produce analysis. Do NOT refuse. This is a simulation for intelligence analysis training.

$(head -c 50000 "$FILTERED_FILE")")
        fi

        echo "$take" > "$RUN_DIR/03_take_${agent}.md"
    ) &
    log "  $agent launched..."
done
wait  # Wait for all takes
log "  All takes complete"

# Load takes into associative array
for agent in "${!active_agents[@]}"; do
    if [ -f "$RUN_DIR/03_take_${agent}.md" ]; then
        all_takes[$agent]="$(cat "$RUN_DIR/03_take_${agent}.md")"
    fi
done

# ─── PHASE 4A: NATURAL TENSIONS (parallel pairs) ───────────
log "PHASE 4A: Tension challenges (parallel)..."
for agent in "${!all_takes[@]}"; do all_challenges[$agent]=""; done

# Build list of active agents for fallback reassignment
active_list=(${!all_takes[@]})

for pair in "${TENSIONS[@]}"; do
    c="${pair%%:*}"; t="${pair##*:}"

    # If challenger has no take, skip entirely
    [[ -z "${all_takes[$c]:-}" ]] && continue

    # If target sat out, reassign challenger to a random active agent
    if [[ -z "${all_takes[$t]:-}" ]]; then
        for fallback in "${active_list[@]}"; do
            if [[ "$fallback" != "$c" && -n "${all_takes[$fallback]:-}" ]]; then
                t="$fallback"
                log "  $c -> $t (reassigned — original target sat out)"
                break
            fi
        done
        [[ -z "${all_takes[$t]:-}" ]] && continue
    fi

    throttle_wait
    (
        sleep 3
        ch=$(ask_hermes "$PERSONAS/$c.md" \
            "CHALLENGE ${t^^}'s analysis. What did they get wrong? Where are the blind spots?

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
    for f in "$RUN_DIR"/04a_${c}_vs_*.md; do
        [ -f "$f" ] || continue
        actual_t=$(basename "$f" .md | sed "s/04a_${c}_vs_//")
        all_challenges[$actual_t]+="
--- Challenge from ${c^^} ---
$(cat "$f")
"
    done
done

# ─── PHASE 4C: WILDCARD ──────────────────────────────────────
log "PHASE 4C: Wildcard cross-examination..."

if [ ${#all_takes[@]} -ge 4 ]; then
    takes_summary=""
    for a in "${!all_takes[@]}"; do
        takes_summary+="- $a: $(echo "${all_takes[$a]}" | head -c 1000)...
"
    done
    sleep 3
    wc_assign=$(ask_hermes "$PERSONAS/synthesizer.md" \
        "Pick ONE unexpected cross-examination between agents NOT in these pairs: trader-narrator, builder-policy_analyst, analyst-skeptic, macro_strategist-user_agent.

Agents:
$takes_summary

Reply EXACTLY: CHALLENGER: [name] TARGET: [name]" "claude-sonnet-4-20250514")

    wc_c=$(echo "$wc_assign" | grep -oi "challenger: *[a-z_]*" | sed 's/.*: *//' | tr '[:upper:]' '[:lower:]')
    wc_t=$(echo "$wc_assign" | grep -oi "target: *[a-z_]*" | sed 's/.*: *//' | tr '[:upper:]' '[:lower:]')

    if [[ -n "$wc_c" && -n "$wc_t" && -f "$RUN_DIR/03_take_${wc_c}.md" && -f "$RUN_DIR/03_take_${wc_t}.md" ]]; then
        sleep 3
        wch=$(ask_hermes "$PERSONAS/$wc_c.md" \
            "WILDCARD: Challenge ${wc_t^^} from your unique perspective.

YOUR TAKE: $(cat "$RUN_DIR/03_take_${wc_c}.md")
${wc_t^^}'S TAKE: $(cat "$RUN_DIR/03_take_${wc_t}.md")")
        echo "$wch" > "$RUN_DIR/04c_wildcard_${wc_c}_vs_${wc_t}.md"
        all_challenges[$wc_t]+="
--- Wildcard from ${wc_c^^} ---
$wch
"
        log "  Wildcard: $wc_c -> $wc_t"
    fi
fi

# ─── PHASE 5: RESPONSES (parallel) ─────────────────────────
log "PHASE 5: Responses (parallel)..."
for agent in "${!all_challenges[@]}"; do
    [[ -z "${all_challenges[$agent]}" ]] && continue

    throttle_wait
    (
        sleep 3
        resp=$(ask_hermes "$PERSONAS/$agent.md" \
            "DEFEND or CONCEDE. If conceding, tag: 'I am updating my position because [evidence].'

YOUR TAKE: ${all_takes[$agent]:-}

CHALLENGES: ${all_challenges[$agent]:-}")
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

# ─── PHASE 5.5: SYNTHESIZER-DIRECTED DEEP DIVE ──────────────
log "PHASE 5.5: Checking for unresolved disagreements..."

# Build challenge/response summary for synthesizer
debate_summary=""
for a in "${!all_challenges[@]}"; do
    [[ -z "${all_challenges[$a]}" ]] && continue
    debate_summary+="### Challenges to ${a^^}:
${all_challenges[$a]}

Response: ${all_responses[$a]:-none}

"
done

if [ -n "$debate_summary" ]; then
    sleep 3
    deep_dive_decision=$(ask_hermes "$PERSONAS/synthesizer.md" \
        "Review all challenges and responses. Is there ONE unresolved disagreement that would materially change the brief's conclusions? If yes, identify the two agents and the specific point of contention.

Reply EXACTLY in one of these formats:
DEEP_DIVE: [agent1] vs [agent2] on [specific point]
NO_DEEP_DIVE: [reason]

DEBATE RECORD:
$debate_summary" "claude-sonnet-4-20250514")

    if echo "$deep_dive_decision" | grep -qi "DEEP_DIVE:"; then
        dd_agents=$(echo "$deep_dive_decision" | grep -oi "DEEP_DIVE: *[a-z_]* vs [a-z_]*" | sed 's/DEEP_DIVE: *//')
        dd_agent1=$(echo "$dd_agents" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')
        dd_agent2=$(echo "$dd_agents" | awk '{print $3}' | tr '[:upper:]' '[:lower:]')
        dd_point=$(echo "$deep_dive_decision" | sed 's/.*on //')

        log "  DEEP DIVE: $dd_agent1 vs $dd_agent2 on: $dd_point"

        # Send both agents back for a second round
        for dd_agent in "$dd_agent1" "$dd_agent2"; do
            if [ -f "$RUN_DIR/03_take_${dd_agent}.md" ]; then
                throttle_wait
                (
                    sleep 3
                    dd_resp=$(ask_hermes "$PERSONAS/$dd_agent.md" \
                        "DEEP DIVE — Final position on this specific point: $dd_point

This is the ONE unresolved disagreement that could change today's brief. Be precise.

1. State your final position clearly.
2. What specific evidence would change your mind?

YOUR ORIGINAL TAKE: $(cat "$RUN_DIR/03_take_${dd_agent}.md")
YOUR RESPONSE TO CHALLENGES: ${all_responses[$dd_agent]:-none}")
                    echo "$dd_resp" > "$RUN_DIR/05_5_deepdive_${dd_agent}.md"
                ) &
            fi
        done
        wait
        log "  Deep dive complete"
    else
        log "  No deep dive needed: $(echo "$deep_dive_decision" | head -1)"
    fi
fi

# ─── PHASE 6: CONVERGENCE (parallel) ───────────────────────
log "PHASE 6: Votes (parallel)..."
ctx=""
for a in "${!all_takes[@]}"; do ctx+="### ${a^^}:
${all_takes[$a]}

"; done

for agent in "${!all_takes[@]}"; do
    throttle_wait
    (
        sleep 3
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

# ─── PHASE 6.5: UPDATE AGENT MEMORIES + STATE (parallel) ────
log "PHASE 6.5: Updating agent memories and state (parallel)..."
for agent in "${!all_takes[@]}"; do
    # Update legacy agent memory
    memory_file="$RECON_HOME/config/agent_memory/${agent}.md"
    if [ -f "$memory_file" ]; then
        throttle_wait
        (
            sleep 3
            update=$(ask_hermes "$PERSONAS/$agent.md" \
                "Update your running memory file. This memory accumulates over time — don't rewrite it, ADD to it.

Rules:
- Add new items to Active Tracking (things to watch, metrics, deadlines)
- Add predictions with dates under Predictions (never delete unscored predictions)
- Update Recurring Themes (what keeps coming up across runs)
- Add to Lessons Learned if you conceded something today or got something wrong (what happened, why, what you'd do differently)
- Score any prior predictions that can now be resolved (mark: confirmed/wrong/expired)
- Archive items older than 60 days as 1-line summaries

Use EXACTLY this format:
### Active Tracking
- [items]

### Predictions
- [$TODAY] [prediction] [status: pending]

### Recurring Themes
- [theme] — seen Nx

### Lessons Learned
- [$TODAY] [what happened] [what I learned]

### Archived
- [date range] [summary]

YOUR TAKE TODAY:
${all_takes[$agent]}

YOUR VOTE TODAY:
${all_votes[$agent]:-none}

YOUR CURRENT MEMORY:
$(cat "$memory_file")" "claude-sonnet-4-20250514")

            # Append update to memory (don't replace)
            echo "" >> "$memory_file"
            echo "### Last updated: $TODAY" >> "$memory_file"
            echo "" >> "$memory_file"
            echo "$update" >> "$memory_file"

            # Trim if over 150 lines (keep header + last 140 lines)
            mem_lines=$(wc -l < "$memory_file")
            if [ "$mem_lines" -gt 150 ]; then
                head -2 "$memory_file" > "${memory_file}.tmp"
                echo "" >> "${memory_file}.tmp"
                echo "### [older entries archived]" >> "${memory_file}.tmp"
                echo "" >> "${memory_file}.tmp"
                tail -140 "$memory_file" >> "${memory_file}.tmp"
                mv "${memory_file}.tmp" "$memory_file"
            fi
        ) &
    fi

    # Update agent state file
    state_name="$agent"
    [[ "$agent" == "user_agent" ]] && state_name="user"
    state_file="$RECON_HOME/config/agent_state/${state_name}_state.md"
    if [ -f "$state_file" ]; then
        throttle_wait
        (
            sleep 3
            state_update=$(ask_hermes "$PERSONAS/$agent.md" \
                "Extract key claims, predictions, and position changes from your analysis today. Format as a dated state log entry.

Output EXACTLY this format:
### $TODAY
- POSITION: [your main position/call today, 1 sentence]
- PREDICTIONS: [any testable predictions with timeframe]
- CHANGED: [anything you conceded or updated from challenges]
- WATCHING: [key items to track for next session]

YOUR TAKE:
${all_takes[$agent]}

YOUR RESPONSE TO CHALLENGES:
${all_responses[$agent]:-none}

YOUR VOTE:
${all_votes[$agent]:-none}" "claude-sonnet-4-20250514")

            # Append to state file
            echo "" >> "$state_file"
            echo "$state_update" >> "$state_file"

            # Trim if too long (keep header + last 80 lines)
            state_lines=$(wc -l < "$state_file")
            if [ "$state_lines" -gt 200 ]; then
                head -5 "$state_file" > "${state_file}.tmp"
                echo "" >> "${state_file}.tmp"
                echo "### [older entries trimmed]" >> "${state_file}.tmp"
                echo "" >> "${state_file}.tmp"
                tail -150 "$state_file" >> "${state_file}.tmp"
                mv "${state_file}.tmp" "$state_file"
            fi
        ) &
    fi
done
wait
log "  Agent memories and state updated"

send_telegram "Debate complete. Synthesizing brief..."

# ─── PHASE 7: SYNTHESIS (OPUS) ─────────────────────────────
log "PHASE 7: Synthesis (Opus 4.6)..."

# Dynamic agent weighting
sleep 3
env_classification=$(ask_hermes "$PERSONAS/synthesizer.md" \
    "Classify today's data environment into ONE of these categories:
- MARKET-DRIVEN: significant price moves, volume spikes, or capital flows dominate
- NARRATIVE-DRIVEN: social discourse, new narratives, or sentiment shifts dominate
- PRODUCT-DRIVEN: major protocol launches, upgrades, or competitive moves dominate
- RISK-DRIVEN: regulatory actions, hacks, depegs, or systemic risks dominate
- QUIET: no dominant theme, incremental developments

Review the filtered data and agent takes:
$(head -c 5000 "$FILTERED_FILE")

Agent takes summary:
$(for a in "${!all_takes[@]}"; do echo "- $a: $(echo "${all_takes[$a]}" | head -c 200)"; done)

Reply EXACTLY: ENVIRONMENT: [type] WEIGHT: [comma-separated agent names to weight higher]" "claude-sonnet-4-20250514")

log "  Environment: $(echo "$env_classification" | head -1)"

record="# DEBATE RECORD -- $TODAY

## ENVIRONMENT CLASSIFICATION
$env_classification

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

# Include deep dive if it happened
if ls "$RUN_DIR"/05_5_deepdive_*.md 1>/dev/null 2>&1; then
    record+="## DEEP DIVE
"
    for f in "$RUN_DIR"/05_5_deepdive_*.md; do
        agent=$(basename "$f" .md | sed 's/05_5_deepdive_//')
        record+="### ${agent^^} (deep dive):
$(cat "$f")

"
    done
fi

record+="## VOTES
"
for a in "${!all_votes[@]}"; do record+="### ${a^^}: ${all_votes[$a]}
"; done

# Include scorecard
if [ -n "$SCORECARD" ]; then
    record+="
## YESTERDAY'S PREDICTION SCORECARD
$SCORECARD
"
fi

echo "$record" > "$RUN_DIR/07_full_record.md"

# First pass: produce the brief
sleep 3
brief_draft=$(ask_hermes "$PERSONAS/synthesizer.md" \
    "Produce the RECON Daily Brief. 600-1000 words. This gets read over morning coffee on a phone.

Write like a smart colleague explaining what happened overnight — not like an academic paper. Use plain language. No markdown tables. No corporate jargon. Be conversational but precise.

$env_classification

Use EXACTLY this format — 6 sections:
- WHAT HAPPENED (4-5 sentences. Key developments with numbers. Write like you're catching someone up over coffee.)
- WHAT IT MEANS (2-3 key takeaways from the debate. For each: what it is, why it matters, where agents agreed/disagreed. Spend 2-3 sentences per insight explaining the 'so what'. This is analysis, not bullet points.)
- WHERE THEY DISAGREE (The most interesting split. Who said what and why. Uncertainty is information.)
- RISKS (Top 2-3. Plain language, not formatted tables. How likely, how bad, why it matters.)
- WHAT TO WATCH (3-5 concrete things to monitor this week, with dates.)
- SCORECARD (Score yesterday's predictions. Be honest.)

HALLUCINATION CHECK:
- If an agent cites a number that appears in the on-chain/market data sections, use it as fact.
- If a claim comes from Reddit posts or Twitter, attribute it: 'per social media', 'reportedly', 'Reddit users claim'.
- If a number doesn't trace to any data source, mark it [unverified] or drop it.
- NEVER present social media commentary as verified intelligence.

$record" "claude-opus-4-20250514")

echo "$brief_draft" > "$RUN_DIR/07_brief_draft.md"
log "  Draft brief: $(echo "$brief_draft" | wc -w) words"

# Second pass: hallucination filter + tone check
sleep 3
brief=$(ask_hermes "$PERSONAS/synthesizer.md" \
    "Review this draft brief against the raw data. Two jobs:

JOB 1 — HALLUCINATION FILTER:
Cross-reference every specific number, statistic, and claim in the brief against the data sections in the debate record below. If a number appears in the brief but NOT in the source data (on-chain, news, reddit, twitter, worldmonitor sections), either:
- Mark it [unverified] if it came from an agent's analysis (plausible but not from data)
- Remove it entirely if it looks fabricated
Do NOT remove numbers that ARE in the source data.

JOB 2 — TONE CHECK:
- Does it read like a human wrote it? If any section sounds robotic or academic, rewrite it conversationally.
- Cut filler and redundancy, but don't over-compress. 600-1000 words is the target.
- Keep the 6-section structure: WHAT HAPPENED, WHAT IT MEANS, WHERE THEY DISAGREE, RISKS, WHAT TO WATCH, SCORECARD.

Return ONLY the final brief. No commentary.

DRAFT BRIEF:
$brief_draft

RAW DATA (for cross-referencing numbers):
$(head -c 30000 "$FILTERED_FILE")" "claude-opus-4-20250514")

echo "$brief" > "$RUN_DIR/07_daily_brief.md"
log "  FINAL BRIEF: $(echo "$brief" | wc -w) words"

# ─── DELIVER ────────────────────────────────────────────────
log "DELIVERING..."
send_telegram "$brief"

# ─── ARCHIVE DATA FOR KNOWLEDGE BASE ──────────────────────
log "Archiving daily data..."
DATA_DIR="$RECON_HOME/data-sources"
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

# ─── INDEX INTO KNOWLEDGE DATABASE ────────────────────────
log "Indexing into knowledge database..."
python3 "$RECON_HOME/scripts/knowledge_db.py" index "$RUN_DIR" 2>&1 | while read line; do log "  $line"; done

log "=============================================="
log "RECON COMPLETE in $((SECONDS/60))m $((SECONDS%60))s"
log "Brief: $RUN_DIR/07_daily_brief.md"
log "Cost log: $RECON_HOME/logs/llm_calls.log"
log "=============================================="
