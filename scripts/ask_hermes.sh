#!/usr/bin/env bash
#
# ask_hermes.sh -- shell shim over recon/llm.py (kept under its v1 name so call sites work)
#
# Usage: source scripts/ask_hermes.sh
#        result=$(ask_hermes "personas/trader.md" "Analyze this data..." [tier])
#
# tier is fast | analyst | synth (default analyst). Legacy Claude model ids are still
# accepted and mapped to a tier by recon/llm.py. Provider, models, effort, retries and the
# call log are all configured there (see RECON_LLM_* and RECON_MODEL_* in .recon.env).
#

ask_hermes() {
    local persona_file="$1"
    local prompt="$2"
    local tier="${3:-analyst}"
    local home="${RECON_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    local result
    # stdin carries the prompt (no ARG_MAX concerns); stdout carries the answer.
    result=$(printf '%s' "$prompt" | python3 "$home/recon/llm.py" --tier "$tier" --persona "$persona_file") || true
    [ -z "$result" ] && result="[ERROR] All methods failed for this agent call."
    echo "$result"
}
