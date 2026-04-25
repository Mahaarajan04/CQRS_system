#!/bin/bash
# Master benchmark — full N × delay grid (no diagonal cap)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$(cd "$(dirname "$0")" && pwd)/benchmark_cache.sh"
RESULTS_DIR="$ROOT/results/master_$(date +%Y%m%d_%H%M%S)"
SUMMARY="$RESULTS_DIR/summary.txt"

# ── Defaults ────────────────────────────────────────────────────────────────
N_VALUES=(100 200 300 500 1000)
DELAY_VALUES=(0.01 0.05 0.10)

# ── Arg overrides ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --n-values)     read -ra N_VALUES     <<< "$2"; shift ;;
        --delay-values) read -ra DELAY_VALUES <<< "$2"; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

# ── Ensure results dir ──────────────────────────────────────────────────────
RESULTS_ROOT="$ROOT/results"
mkdir -p "$RESULTS_ROOT"
mkdir -p "$RESULTS_DIR"

# ── Helpers ─────────────────────────────────────────────────────────────────
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
sep()    { printf "\n%s\n\n" "$(printf '=%.0s' {1..60})"; }

# ── Count total runs ─────────────────────────────────────────────────────────
TOTAL=$(( ${#N_VALUES[@]} * ${#DELAY_VALUES[@]} ))

PASS=0
RUN=0
FAILED_RUNS=()

log() { echo "$*" | tee -a "$SUMMARY"; }

# ── Header ──────────────────────────────────────────────────────────────────
sep
bold "MASTER BENCHMARK — Full Grid ($TOTAL runs)"
echo "  N values:     ${N_VALUES[*]}"
echo "  Delay values: ${DELAY_VALUES[*]}"
echo "  Results dir:  $RESULTS_DIR"
sep

log "master_benchmark started at $(date)"
log "N values:     ${N_VALUES[*]}"
log "Delay values: ${DELAY_VALUES[*]}"
log "Total runs:   $TOTAL"
log ""
log "$(printf '%-6s  %-8s  %-10s  %s' 'N' 'delay' 'status' 'notes')"
log "$(printf '%-6s  %-8s  %-10s  %s' '------' '--------' '----------' '-----')"

# ── Grid loop ───────────────────────────────────────────────────────────────
for N in "${N_VALUES[@]}"; do
    for DELAY in "${DELAY_VALUES[@]}"; do

        RUN=$(( RUN + 1 ))
        sep
        bold "[$RUN/$TOTAL]  N=$N  delay=${DELAY}s"

        LOG_FILE="$RESULTS_DIR/run_n${N}_d${DELAY}.log"
        STATUS="FAIL"
        NOTE=""

        # ── First attempt ────────────────────────────────────────────────
        if bash "$SCRIPT" --n "$N" --delay "$DELAY" > "$LOG_FILE" 2>&1; then
            STATUS="PASS"
            PASS=$(( PASS + 1 ))
            green "  ✓ Passed"

        else
            # ── Retry once ───────────────────────────────────────────────
            yellow "  ✗ Failed — retrying once..."
            RETRY_LOG="$RESULTS_DIR/run_n${N}_d${DELAY}_retry.log"

            if bash "$SCRIPT" --n "$N" --delay "$DELAY" > "$RETRY_LOG" 2>&1; then
                STATUS="PASS(retry)"
                PASS=$(( PASS + 1 ))
                NOTE="passed on retry"
                green "  ✓ Passed on retry"
                mv "$RETRY_LOG" "$LOG_FILE"

            else
                STATUS="FAILED"
                NOTE="failed twice"
                FAILED_RUNS+=("N=$N delay=$DELAY")

                echo "" >> "$LOG_FILE"
                echo "=== RETRY ATTEMPT ===" >> "$LOG_FILE"
                cat "$RETRY_LOG" >> "$LOG_FILE"
                rm -f "$RETRY_LOG"

                red "  ✗ FAILED (after retry)"
            fi
        fi

        log "$(printf '%-6s  %-8s  %-10s  %s' "$N" "$DELAY" "$STATUS" "$NOTE")"
    done
done

# ── Final summary ───────────────────────────────────────────────────────────
sep
bold "MASTER BENCHMARK COMPLETE"
echo ""
printf "  Total runs:  %s\n" "$TOTAL"
green "$(printf '  Passed:      %s' "$PASS")"

if [[ ${#FAILED_RUNS[@]} -gt 0 ]]; then
    red "$(printf '  Failed:      %s' "${#FAILED_RUNS[@]}")"
    for entry in "${FAILED_RUNS[@]}"; do
        red "    • $entry"
        log "FAILED: $entry"
    done
else
    green "  Failed:      0"
fi

echo ""
green "Results: $RESULTS_DIR"
echo "Summary: $SUMMARY"
sep

log ""
log "Finished at $(date)"
log "Passed: $PASS / $TOTAL   Failed: ${#FAILED_RUNS[@]}"