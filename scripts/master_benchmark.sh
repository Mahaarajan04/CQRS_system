#!/bin/bash
# Master benchmark — diagonal-capped N × delay grid.
# N values:     500, 1250, 2500, 3750, 5000
# Delay values: 0.01, 0.05, 0.10, 0.30
#
# High delays are skipped for high N to keep runtimes sane:
#   N=500,  1250 → all delays
#   N=2500       → max 0.10
#   N=3750       → max 0.05
#   N=5000       → max 0.01
#
# Usage:
#   bash scripts/master_benchmark.sh
#   bash scripts/master_benchmark.sh --n-values "500 1000 5000" --delay-values "0.01 0.05 0.1"

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$(cd "$(dirname "$0")" && pwd)/benchmark_cache.sh"
RESULTS_DIR="$ROOT/results/master_$(date +%Y%m%d_%H%M%S)"
SUMMARY="$RESULTS_DIR/summary.txt"

# ── Defaults ──────────────────────────────────────────────────────────────────
N_VALUES=(500 1000 2500 4000 5000)
DELAY_VALUES=(0.01 0.05 0.10 0.30)

# ── Diagonal cap: max delay allowed per N ─────────────────────────────────────
declare -A MAX_DELAY
MAX_DELAY[500]=0.30
MAX_DELAY[1000]=0.30
MAX_DELAY[2500]=0.10
MAX_DELAY[4000]=0.05
MAX_DELAY[5000]=0.05

# ── Arg overrides ─────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --n-values)     read -ra N_VALUES     <<< "$2"; shift ;;
        --delay-values) read -ra DELAY_VALUES <<< "$2"; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

# ── Ensure results root exists ────────────────────────────────────────────────
RESULTS_ROOT="$ROOT/results"
if [[ ! -d "$RESULTS_ROOT" ]]; then
    echo "  'results/' folder not found — creating $RESULTS_ROOT"
    mkdir -p "$RESULTS_ROOT"
fi
mkdir -p "$RESULTS_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
grey()   { printf "\033[90m%s\033[0m\n" "$*"; }
sep()    { printf "\n%s\n\n" "$(printf '=%.0s' {1..60})"; }

# ── Pre-calculate actual runs (respecting diagonal cap) ───────────────────────
TOTAL=0
for N in "${N_VALUES[@]}"; do
    for DELAY in "${DELAY_VALUES[@]}"; do
        CAP="${MAX_DELAY[$N]:-9999}"
        if (( $(echo "$DELAY <= $CAP" | bc -l) )); then
            TOTAL=$(( TOTAL + 1 ))
        fi
    done
done

PASS=0
SKIP=0
RUN=0
SKIPPED_CAP=0
FAILED_RUNS=()

log() { echo "$*" | tee -a "$SUMMARY"; }

# ── Header ────────────────────────────────────────────────────────────────────
sep
bold "MASTER BENCHMARK — Diagonal-Capped Grid ($TOTAL active runs)"
echo "  N values:     ${N_VALUES[*]}"
echo "  Delay values: ${DELAY_VALUES[*]}"
echo ""
echo "  Cap table (max delay per N):"
for N in "${N_VALUES[@]}"; do
    printf "    N=%-5s  max delay=%s\n" "$N" "${MAX_DELAY[$N]:-none}"
done
echo ""
echo "  Results dir:  $RESULTS_DIR"
sep

log "master_benchmark started at $(date)"
log "N values:     ${N_VALUES[*]}"
log "Delay values: ${DELAY_VALUES[*]}"
log "Active runs:  $TOTAL"
log ""
log "$(printf '%-6s  %-8s  %-10s  %s' 'N' 'delay' 'status' 'notes')"
log "$(printf '%-6s  %-8s  %-10s  %s' '------' '--------' '----------' '-----')"

# ── Grid loop ─────────────────────────────────────────────────────────────────
for N in "${N_VALUES[@]}"; do
    for DELAY in "${DELAY_VALUES[@]}"; do

        # ── Diagonal cap check ─────────────────────────────────────────────
        CAP="${MAX_DELAY[$N]:-9999}"
        if (( $(echo "$DELAY > $CAP" | bc -l) )); then
            SKIPPED_CAP=$(( SKIPPED_CAP + 1 ))
            grey "  [CAP-SKIP] N=$N delay=$DELAY — exceeds max delay ($CAP) for this N"
            log "$(printf '%-6s  %-8s  %-10s  %s' "$N" "$DELAY" "CAP-SKIP" "exceeds diagonal cap")"
            continue
        fi

        RUN=$(( RUN + 1 ))
        sep
        bold "[$RUN/$TOTAL]  N=$N  delay=${DELAY}s"

        LOG_FILE="$RESULTS_DIR/run_n${N}_d${DELAY}.log"
        STATUS="FAIL"
        NOTE=""

        # ── First attempt ──────────────────────────────────────────────────
        if bash "$SCRIPT" --n "$N" --delay "$DELAY" > "$LOG_FILE" 2>&1; then
            STATUS="PASS"
            PASS=$(( PASS + 1 ))
            green "  ✓ Passed"

        else
            # ── Retry once ─────────────────────────────────────────────────
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
                NOTE="failed twice — skipped"
                FAILED_RUNS+=("N=$N delay=$DELAY")
                echo "" >> "$LOG_FILE"
                echo "=== RETRY ATTEMPT ===" >> "$LOG_FILE"
                cat "$RETRY_LOG" >> "$LOG_FILE"
                rm -f "$RETRY_LOG"

                echo ""
                red "╔══════════════════════════════════════════╗"
                red "║       ✗  RUN FAILED (x2) — SKIPPED      ║"
                red "║  N=$N   delay=$DELAY   run [$RUN/$TOTAL]"
                red "║  log: $(basename "$LOG_FILE")"
                red "╚══════════════════════════════════════════╝"
                echo ""
            fi
        fi

        log "$(printf '%-6s  %-8s  %-10s  %s' "$N" "$DELAY" "$STATUS" "$NOTE")"
    done
done

# ── Final summary ─────────────────────────────────────────────────────────────
sep
bold "MASTER BENCHMARK COMPLETE"
echo ""
printf "  Total combos:    %s\n" "$(( TOTAL + SKIPPED_CAP ))"
printf "  Ran:             %s\n" "$TOTAL"
printf "  Cap-skipped:     %s\n" "$SKIPPED_CAP"
green "$(printf '  Passed:          %s' "$PASS")"
echo ""

if [[ ${#FAILED_RUNS[@]} -gt 0 ]]; then
    red "$(printf '  Failed:          %s' "${#FAILED_RUNS[@]}")"
    echo ""
    red "  ✗ FAILED RUNS:"
    for entry in "${FAILED_RUNS[@]}"; do
        red "      • $entry"
        log "  FAILED: $entry"
    done
else
    green "  Failed:          0"
fi

echo ""
green "Results: $RESULTS_DIR"
echo "Summary: $SUMMARY"
sep

log ""
log "Finished at $(date)"
log "Passed: $PASS / $TOTAL   Failed: ${#FAILED_RUNS[@]}   Cap-skipped: $SKIPPED_CAP"