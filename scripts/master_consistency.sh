#!/bin/bash
# Master consistency demo — FULL GRID of (N, delay) pairs.
# Runs all combinations of N_VALUES × DELAY_VALUES.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"
RESULTS_ROOT="$ROOT/results"
RESULTS_DIR="$RESULTS_ROOT/consistency_$(date +%Y%m%d_%H%M%S)"
SUMMARY="$RESULTS_DIR/summary.txt"

# ── Grid values ───────────────────────────────────────────────────────────────
N_VALUES=(100 200 500 1000 1500)
DELAY_VALUES=(0.10 0.05 0.01)

# ── Ensure results folder exists ──────────────────────────────────────────────
mkdir -p "$RESULTS_ROOT"
mkdir -p "$RESULTS_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
sep()    { printf "\n%s\n\n" "$(printf '=%.0s' {1..60})"; }

TOTAL=$(( ${#N_VALUES[@]} * ${#DELAY_VALUES[@]} ))
PASS=0
RUN=0
FAILED_RUNS=()

log() { echo "$*" | tee -a "$SUMMARY"; }

# ── Header ────────────────────────────────────────────────────────────────────
sep
bold "CONSISTENCY DEMO — FULL GRID ($TOTAL runs)"
echo ""
echo "  Grid:"
for N in "${N_VALUES[@]}"; do
  for D in "${DELAY_VALUES[@]}"; do
    printf "    N=%-5s  delay=%s\n" "$N" "$D"
  done
done
echo ""
echo "  Results dir: $RESULTS_DIR"
sep

log "master_consistency started at $(date)"
log ""
log "$(printf '%-6s  %-8s  %-10s  %s' 'N' 'delay' 'status' 'notes')"
log "$(printf '%-6s  %-8s  %-10s  %s' '------' '--------' '----------' '-----')"

# ── FULL GRID LOOP ────────────────────────────────────────────────────────────
for N in "${N_VALUES[@]}"; do
  for DELAY in "${DELAY_VALUES[@]}"; do

    RUN=$(( RUN + 1 ))

    sep
    bold "[$RUN/$TOTAL]  N=$N  delay=${DELAY}s"

    LOG_FILE="$RESULTS_DIR/run_n${N}_d${DELAY}.log"
    STATUS="FAIL"
    NOTE=""

    # Restart system
    echo "  Restarting system with --delay $DELAY --no-cache ..."
    if ! bash "$ROOT/restart.sh" --delay "$DELAY" --no-cache >> "$LOG_FILE" 2>&1; then
        yellow "  restart.sh exited non-zero — continuing anyway"
    fi

    # First attempt
    if "$PYTHON" "$ROOT/scripts/consistency_demo.py" --n "$N" 2>&1 | tee -a "$LOG_FILE"; then
        STATUS="PASS"
        PASS=$(( PASS + 1 ))
        green "  Passed"

    else
        # Retry
        yellow "  Failed — retrying once..."
        echo "" >> "$LOG_FILE"
        echo "=== RETRY ATTEMPT ===" >> "$LOG_FILE"

        if "$PYTHON" "$ROOT/scripts/consistency_demo.py" --n "$N" 2>&1 | tee -a "$LOG_FILE"; then
            STATUS="PASS(retry)"
            PASS=$(( PASS + 1 ))
            NOTE="passed on retry"
            green "  Passed on retry"
        else
            STATUS="FAILED"
            NOTE="failed twice — skipped"
            FAILED_RUNS+=("N=$N delay=$DELAY")

            red "RUN FAILED (x2) — N=$N delay=$DELAY"
        fi
    fi

    log "$(printf '%-6s  %-8s  %-10s  %s' "$N" "$DELAY" "$STATUS" "$NOTE")"

  done
done

# ── Restore system ────────────────────────────────────────────────────────────
sep
bold "Restoring system to defaults..."
bash "$ROOT/restart.sh"
green "  System restored"

# ── Summary ───────────────────────────────────────────────────────────────────
sep
bold "CONSISTENCY DEMO COMPLETE"
echo ""
printf "  Total runs:  %s\n" "$TOTAL"
printf "  Passed:      %s\n" "$PASS"
printf "  Failed:      %s\n" "${#FAILED_RUNS[@]}"

echo ""
echo "Results: $RESULTS_DIR"
echo "Summary: $SUMMARY"
sep

log ""
log "Finished at $(date)"
log "Passed: $PASS / $TOTAL   Failed: ${#FAILED_RUNS[@]}"