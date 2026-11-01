#!/bin/sh
# Neo status line: model · context usage % · session spend.
#
# Reads the Claude Code status JSON on stdin. Uses /usr/bin/jq (absolute path
# on purpose — the statusline runs with an unpredictable PATH, and `python3`
# here is a pyenv shim that may not resolve).
#
# Context usage is NOT in the status JSON; it is derived from the transcript by
# taking the most recent main-chain assistant message's usage and summing
# input + cache_creation + cache_read. That sum IS the context footprint of
# that request.

JQ=/usr/bin/jq
[ -x "$JQ" ] || JQ=$(command -v jq 2>/dev/null)

input=$(cat)

if [ -z "$JQ" ]; then
    printf 'statusline: jq not found'
    exit 0
fi

# --- fields from the status JSON -------------------------------------------
fields=$(printf '%s' "$input" | "$JQ" -r '
    [ (.model.display_name // ""),
      (.model.id          // ""),
      (.transcript_path   // ""),
      (.cost.total_cost_usd // 0 | tostring)
    ] | .[]' 2>/dev/null)

model_name=$(printf '%s\n' "$fields" | sed -n '1p')
model_id=$(printf '%s\n' "$fields" | sed -n '2p')
transcript=$(printf '%s\n' "$fields" | sed -n '3p')
cost=$(printf '%s\n' "$fields" | sed -n '4p')

[ -n "$model_name" ] || model_name="?"
[ -n "$cost" ] || cost=0

# --- context window size ---------------------------------------------------
# A [1m] suffix on the model id means the 1M-token context variant.
# Override with NEO_STATUSLINE_CONTEXT_LIMIT if a new tier appears.
if [ -n "$NEO_STATUSLINE_CONTEXT_LIMIT" ]; then
    limit="$NEO_STATUSLINE_CONTEXT_LIMIT"
else
    case "$model_id" in
        *'[1m]'*) limit=1000000 ;;
        *)        limit=200000 ;;
    esac
fi

# --- context tokens in use, from the transcript ----------------------------
# Streamed (no -s slurp) so this stays O(1) in memory as the file grows.
# isSidechain guards against subagent turns being mistaken for the main chain.
used=""
if [ -n "$transcript" ] && [ -f "$transcript" ]; then
    used=$("$JQ" -c '
        select(.type == "assistant")
        | select(.isSidechain != true)
        | select(.message.usage != null)
        | .message.usage
        | ( (.input_tokens                // 0)
          + (.cache_creation_input_tokens // 0)
          + (.cache_read_input_tokens     // 0) )
    ' "$transcript" 2>/dev/null | tail -1)
fi

# --- colors (dark theme) ---------------------------------------------------
ESC=$(printf '\033')
RESET="${ESC}[0m"
DIM="${ESC}[2m"
CYAN="${ESC}[36m"
GREEN="${ESC}[32m"
YELLOW="${ESC}[33m"
RED="${ESC}[31m"

out="${CYAN}${model_name}${RESET}"

# --- context segment -------------------------------------------------------
if [ -n "$used" ] && [ "$used" -gt 0 ] 2>/dev/null; then
    ctx=$(awk -v u="$used" -v l="$limit" 'BEGIN {
        pct = (l > 0) ? (u * 100 / l) : 0
        printf "%d\t%.0f\t", (pct + 0.5), pct
        if (u >= 1000000)   printf "%.1fM/", u/1000000
        else if (u >= 1000) printf "%dk/",   u/1000
        else                printf "%d/",    u
        if (l >= 1000000)   printf "%gM",    l/1000000
        else                printf "%dk",    l/1000
    }')
    pct_round=$(printf '%s' "$ctx" | cut -f1)
    pct_disp=$(printf '%s' "$ctx" | cut -f2)
    ratio=$(printf '%s' "$ctx" | cut -f3)

    if [ "$pct_round" -ge 85 ]; then      ctx_color="$RED"
    elif [ "$pct_round" -ge 60 ]; then    ctx_color="$YELLOW"
    else                                  ctx_color="$GREEN"
    fi

    out="${out} ${DIM}·${RESET} ${ctx_color}${pct_disp}%${RESET} ${DIM}(${ratio})${RESET}"
else
    out="${out} ${DIM}· ctx —${RESET}"
fi

# --- spend segment ---------------------------------------------------------
cost_disp=$(awk -v c="$cost" 'BEGIN {
    if (c == "" || c + 0 <= 0) { print "$0.00"; exit }
    if (c + 0 < 0.01) { printf "<$0.01"; exit }
    printf "$%.2f", c
}')
out="${out} ${DIM}·${RESET} ${DIM}${cost_disp}${RESET}"

printf '%s' "$out"
