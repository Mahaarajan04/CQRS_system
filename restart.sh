#!/bin/bash
# Restart all CQRS services via Docker Compose with a clean slate.
#
# Two Redis instances (defined in docker-compose.yml):
#   redis-stream  :6379  — order_events stream, no memory limit
#   redis-cache   :6380  — order:* KV cache, 2MB + allkeys-lru
#
# Usage:
#   bash restart.sh                        # fast consumer, cache enabled (default)
#   bash restart.sh --slow                 # 100ms/event consumer lag
#   bash restart.sh --no-cache             # read-api bypasses Redis cache
#   bash restart.sh --slow --no-cache      # both
#   bash restart.sh --delay 0.5            # custom delay (seconds per event)
#   bash restart.sh --demo                 # 1s pause between saga stages (visible transitions on dashboard)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Parse flags ───────────────────────────────────────────────────────────────
CONSISTENCY_DELAY=0
DISABLE_CACHE=""
SAGA_STEP_DELAY_SEC=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --slow)     CONSISTENCY_DELAY=0.1 ;;
        --no-cache) DISABLE_CACHE=1 ;;
        --delay)    CONSISTENCY_DELAY="$2"; shift ;;
        --demo)     SAGA_STEP_DELAY_SEC=1 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

cd "$ROOT"

# ── Tear down and wipe volumes (clean slate) ──────────────────────────────────
echo "Stopping services and wiping volumes..."
docker compose down -v --remove-orphans 2>/dev/null || true

# ── Print config ──────────────────────────────────────────────────────────────
echo ""
[[ -n "$DISABLE_CACHE"             ]] && echo "  cache:    DISABLED"                           || echo "  cache:    enabled  (redis-cache :6380, 2MB allkeys-lru)"
[[ "$CONSISTENCY_DELAY" != "0"     ]] && echo "  consumer: SLOW (${CONSISTENCY_DELAY}s/event)" || echo "  consumer: fast"
[[ "$SAGA_STEP_DELAY_SEC" != "0"   ]] && echo "  saga:     DEMO MODE (${SAGA_STEP_DELAY_SEC}s/stage)" || echo "  saga:     fast"
echo "  stream:   redis-stream :6379  (no memory limit)"
echo ""

# ── Start services ────────────────────────────────────────────────────────────
echo "Starting services..."
# saga and consumer are rebuilt without cache so Python source changes always land
docker compose build --no-cache saga consumer
CONSISTENCY_DELAY="$CONSISTENCY_DELAY" DISABLE_CACHE="$DISABLE_CACHE" \
    SAGA_STEP_DELAY_SEC="$SAGA_STEP_DELAY_SEC" \
    docker compose up -d --build

# ── Wait for APIs ─────────────────────────────────────────────────────────────
echo "Waiting for APIs to be ready..."
for i in $(seq 1 40); do
    sleep 1
    OK=0
    curl -sf http://localhost:8001/health > /dev/null 2>&1 && \
    curl -sf http://localhost:8002/health > /dev/null 2>&1 && OK=1
    [[ $OK -eq 1 ]] && break
    if [[ $i -eq 40 ]]; then
        echo "APIs did not start in time. Logs:"
        docker compose logs --tail=30
        exit 1
    fi
done

# ── Wipe MongoDB collections (belt-and-suspenders; volumes already wiped above) ──
echo "Clearing MongoDB events and sagas..."
docker compose exec -T mongodb mongosh cqrs_events --quiet \
    --eval "db.events.deleteMany({}); db.sagas.deleteMany({}); print('MongoDB cleared')"

echo "All services up."
echo "Logs: docker compose logs -f [write-api|read-api|consumer|saga]"
