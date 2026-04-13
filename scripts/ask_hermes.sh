#!/usr/bin/env bash
#
# ask_hermes.sh -- Send a prompt to Claude and get text back
#
# Usage: source scripts/ask_hermes.sh
#        result=$(ask_hermes "personas/trader.md" "Analyze this data..." "claude-sonnet-4-20250514")
#

HERMES_LOG="/home/recon/recon/logs/llm_calls.log"

ask_hermes() {
    local persona_file="$1"
    local prompt="$2"
    local model="${3:-claude-sonnet-4-20250514}"
    local persona_content=$(cat "$persona_file")
    local agent_name=$(basename "$persona_file" .md)
    local start_ts=$(date +%s)
    local full_prompt="You are playing a specific role. Stay in character completely.

--- YOUR PERSONA ---
$persona_content
--- END PERSONA ---

--- YOUR TASK ---
$prompt
--- END TASK ---"

    local input_bytes=${#full_prompt}
    local result=""
    local provider=""

    # Primary: claude CLI
    result=$(claude -p "$full_prompt" --model "$model" 2>/dev/null) || true
    provider="claude"

    # Fallback: hermes chat -q
    if [ -z "$result" ]; then
        result=$(hermes chat -q "$full_prompt" 2>/dev/null) || true
        provider="hermes"
    fi

    if [ -z "$result" ]; then
        result="[ERROR] All methods failed for this agent call."
        provider="FAILED"
    fi

    # Log the call
    local end_ts=$(date +%s)
    local duration=$((end_ts - start_ts))
    local output_bytes=${#result}
    echo "[$(date +%H:%M:%S)] agent=$agent_name model=$model provider=$provider input=${input_bytes}b output=${output_bytes}b duration=${duration}s" >> "$HERMES_LOG" 2>/dev/null

    echo "$result"
}
