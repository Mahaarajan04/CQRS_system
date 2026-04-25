#!/bin/bash
# Full before/after cache benchmark demo.
# Phase 1: no-cache + slow consumer (baseline)
# Phase 2: cache + slow consumer
#
# Usage:
#   bash scripts/benchmark_demo.sh           # default N=500, delay=0.1s
#   bash scripts/benchmark_demo.sh --n 100 --delay 0.05

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

# Snapshot eviction counter before this run for delta reporting
EVICTED_BEFORE=$(redis-cli -p 6380 INFO stats | awk -F: '/evicted_keys:/ {gsub(/\r/,"",$2); print $2}')

sep
blue "Running benchmark (N=$N, consumer delay=${DELAY}s/event) ..."
"$PYTHON" "$ROOT/scripts/test_flow.py" --reader --n "$N" 2>&1 | tee "$CACHED_OUT"

# ── Summary ───────────────────────────────────────────────────────────────────
sep
bold "RESULTS COMPARISON (N=$N, consumer delay=${DELAY}s/event)"
echo ""

strip_ansi() { sed 's/\x1b\[[0-9;]*m//g' "$1"; }

NOCACHE_CLEAN=$(mktemp); strip_ansi "$NOCACHE_OUT" > "$NOCACHE_CLEAN"
CACHED_CLEAN=$(mktemp);  strip_ansi "$CACHED_OUT"  > "$CACHED_CLEAN"

echo "WITHOUT CACHE:"
grep -A5 "^  metric" "$NOCACHE_CLEAN" | head -6 | sed 's/^/    /'

echo ""
echo "WITH CACHE:"
grep -A5 "^  metric" "$CACHED_CLEAN" | head -6 | sed 's/^/    /'

echo ""
bold "Speedup:"
"$PYTHON" - "$NOCACHE_CLEAN" "$CACHED_CLEAN" <<'PYEOF'
import sys, re

def extract_ryw(path):
    with open(path) as f:
        for line in f:
            if "RYW lag ms" in line:
                nums = re.findall(r"[0-9]+\.[0-9]+", line)
                if len(nums) >= 3:
                    return float(nums[0]), float(nums[1]), float(nums[2])
    return None, None, None

before = extract_ryw(sys.argv[1])
after  = extract_ryw(sys.argv[2])
for lbl, b, a in zip(["p50", "p95", "p99"], before, after):
    if b and a:
        print(f"    {lbl:<6}  {b:>8.1f} ms  →  {a:>6.1f} ms   ({int(b/a)}x faster)")
PYEOF

echo ""
bold "Cache hit/miss stats (phase 2):"
"$PYTHON" - <<PYEOF
import urllib.request, json
try:
    data = json.loads(urllib.request.urlopen("http://localhost:8002/cache/stats").read())
    print(f"    hits:     {data['hits']}")
    print(f"    misses:   {data['misses']}")
    print(f"    hit rate: {data['hit_rate']*100:.1f}%")
except Exception as e:
    print(f"    (could not fetch: {e})")
PYEOF

EVICTED_NOW=$(redis-cli -p 6380 INFO stats  | awk -F: '/evicted_keys:/ {gsub(/\r/,"",$2); print $2}')
EVICTED_DELTA=$(( EVICTED_NOW - EVICTED_BEFORE ))
USED=$(redis-cli -p 6380 INFO memory | awk -F: '/used_memory_human:/ {gsub(/\r/,"",$2); print $2}')
MAX=$(redis-cli  -p 6380 INFO memory | awk -F: '/maxmemory_human:/   {gsub(/\r/,"",$2); print $2}')
KEYS=$(redis-cli -p 6380 --scan --pattern "order:*" 2>/dev/null | wc -l | tr -d ' ')
echo ""
bold "Cache Redis :6380 — memory & eviction (allkeys-lru, cap=${MAX}):"
printf "    evicted keys:  %s  (this run; lifetime=%s)\n" "$EVICTED_DELTA" "$EVICTED_NOW"
printf "    used memory:   %s / %s\n" "$USED" "$MAX"
printf "    order:* keys:  %s live in cache now\n" "$KEYS"

echo ""
green "Results saved to:"
echo "  no-cache:  $NOCACHE_OUT"
echo "  cached:    $CACHED_OUT"

# ── Clean up ──────────────────────────────────────────────────────────────────
sep
bold "Cleaning up — restarting with defaults (fast consumer, cache on)..."
bash "$ROOT/restart.sh"
green "Done. System back to default state."
rm -f "$NOCACHE_CLEAN" "$CACHED_CLEAN"
