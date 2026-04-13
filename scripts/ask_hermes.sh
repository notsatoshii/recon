#!/usr/bin/env bash
#
# ask_hermes.sh -- Send a prompt to Claude and get text back
#
# Usage: source scripts/ask_hermes.sh
#        result=$(ask_hermes "personas/trader.md" "Analyze this data..." "claude-sonnet-4-20250514")
#

ask_hermes() {
    local persona_file="$1"
    local prompt="$2"
    local model="${3:-claude-sonnet-4-20250514}"
    local persona_content=$(cat "$persona_file")
    local full_prompt="You are playing a specific role. Stay in character completely.

--- YOUR PERSONA ---
$persona_content
--- END PERSONA ---

--- YOUR TASK ---
$prompt
--- END TASK ---"

    local result=""

    # Primary: claude CLI (confirmed working on this server)
    result=$(claude -p "$full_prompt" --model "$model" 2>/dev/null) || true

    # Fallback: hermes chat -q
    if [ -z "$result" ]; then
        result=$(hermes chat -q "$full_prompt" 2>/dev/null) || true
    fi

    if [ -z "$result" ]; then
        result="[ERROR] All methods failed for this agent call."
    fi

    echo "$result"
}
