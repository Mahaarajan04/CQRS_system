#!/bin/bash
# Full before/after cache benchmark demo.
# Runs N orders twice: once with cache disabled (slow consumer), once enabled.
# Saves results to results/nocache_*.txt and results/cached_*.txt, then prints a
# side-by-side summary.
#
# Usage:
#   bash scripts/benchmark_demo.sh           # default N=500
#   bash scripts/benchmark_demo.sh --n 100   # smaller run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"
N=500
DELAY=0.1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --n)     N="$2";     shift ;;
        --delay) DELAY="$2"; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

NOCACHE_OUT="$ROOT/results/nocache_slowconsumer_${DELAY}_${N}.txt"
CACHED_OUT="$ROOT/results/cached_slowconsumer_${DELAY}_${N}.txt"

# ── helpers ───────────────────────────────────────────────────────────────────
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
blue()  { printf "\033[34m%s\033[0m\n" "$*"; }
sep()   { printf "\n%s\n\n" "$(printf '=%.0s' {1..55})"; }

# ── PHASE 1: no-cache baseline ────────────────────────────────────────────────
sep
bold "PHASE 1 of 2 — NO CACHE + SLOW CONSUMER (baseline)"
echo "  Restarting with --delay $DELAY --no-cache ..."

bash "$ROOT/restart.sh" --delay "$DELAY" --no-cache

sep
blue "Running benchmark (N=$N, consumer delay=${DELAY}s/event) — no cache..."
"$PYTHON" "$ROOT/scripts/test_flow.py" --reader --n "$N" 2>&1 | tee "$NOCACHE_OUT"

# ── PHASE 2: cache enabled ────────────────────────────────────────────────────
sep
bold "PHASE 2 of 2 — CACHE ENABLED + SLOW CONSUMER"
echo "  Restarting with --delay $DELAY (cache on) ..."

bash "$ROOT/restart.sh" --delay "$DELAY"

sep
blue "Running benchmark (N=$N, consumer delay=${DELAY}s/event) ..."
"$PYTHON" "$ROOT/scripts/test_flow.py" --reader --n "$N" 2>&1 | tee "$CACHED_OUT"

# ── Summary ───────────────────────────────────────────────────────────────────
sep
bold "RESULTS COMPARISON (N=$N, consumer delay=${DELAY}s/event)"
echo ""

# Extract the metrics line from each file (strip ANSI codes, grep for the p50 row)
strip_ansi() { sed 's/\x1b\[[0-9;]*m//g' "$1"; }

NOCACHE_CLEAN=$(mktemp); strip_ansi "$NOCACHE_OUT" > "$NOCACHE_CLEAN"
CACHED_CLEAN=$(mktemp);  strip_ansi "$CACHED_OUT"  > "$CACHED_CLEAN"

# Pull the results table lines
echo "WITHOUT CACHE:"
grep -A5 "^  metric" "$NOCACHE_CLEAN" | head -6 | sed 's/^/    /'

echo ""
echo "WITH CACHE:"
grep -A5 "^  metric" "$CACHED_CLEAN" | head -6 | sed 's/^/    /'

echo ""
bold "Speedup:"
# Use Python to extract numbers — avoids grep -P incompatibility on macOS BSD grep
"$PYTHON" - "$NOCACHE_CLEAN" "$CACHED_CLEAN" <<'PYEOF'
import sys, re

def extract_ryw(path):
    with open(path) as f:
        for line in f:
            if "RYW lag ms" in line:
                nums = re.findall(r"[0-9]+\.[0-9]+", line)
                if len(nums) >= 3:
                    return float(nums[0]), float(nums[1]), float(nums[2])  # p50, p95, p99
    return None, None, None

before = extract_ryw(sys.argv[1])
after  = extract_ryw(sys.argv[2])
labels = ["p50", "p95", "p99"]
for lbl, b, a in zip(labels, before, after):
    if b and a:
        speedup = int(b / a)
        print(f"    {lbl:<6}  {b:>8.1f} ms  →  {a:>6.1f} ms   ({speedup}x faster)")
PYEOF

echo ""
green "Results saved to:"
echo "  no-cache: $NOCACHE_OUT"
echo "  cached:   $CACHED_OUT"

# ── Clean up ─────────────────────────────────────────────────────────────────
sep
bold "Cleaning up — restarting with defaults (fast consumer, cache on)..."
bash "$ROOT/restart.sh"
green "Done. System back to default state."
rm -f "$NOCACHE_CLEAN" "$CACHED_CLEAN"
