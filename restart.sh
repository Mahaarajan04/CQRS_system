#!/bin/bash
# Restart all CQRS services with a clean slate.
#
# Two Redis instances:
#   port 6379 — stream (order_events)  no memory limit
#   port 6380 — cache  (order:*)       2MB + allkeys-lru
#
# Usage:
#   bash restart.sh                        # fast consumer, cache enabled (default)
#   bash restart.sh --slow                 # 100ms/event consumer lag
#   bash restart.sh --no-cache             # read-api bypasses Redis cache
#   bash restart.sh --slow --no-cache      # both
#   bash restart.sh --delay 0.5            # custom delay (seconds per event)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/venv/bin/activate"
PYTHON="$ROOT/venv/bin/python"

# ── Parse flags ───────────────────────────────────────────────────────────────
CONSISTENCY_DELAY=0
DISABLE_CACHE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --slow)     CONSISTENCY_DELAY=0.1 ;;
        --no-cache) DISABLE_CACHE=1 ;;
        --delay)    CONSISTENCY_DELAY="$2"; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

# ── Kill Python services ──────────────────────────────────────────────────────
echo "Stopping Python services..."
pkill -f "uvicorn main:app --port 8001" 2>/dev/null || true
pkill -f "uvicorn main:app --port 8002" 2>/dev/null || true
pkill -f "[Pp]ython.*worker\.py"        2>/dev/null || true
sleep 1.5

for port in 8001 8002; do
    if lsof -ti tcp:$port >/dev/null 2>&1; then
        echo "Port $port still in use — force-killing..."
        lsof -ti tcp:$port | xargs kill -9 2>/dev/null || true
        sleep 0.5
    fi
done

# ── Ensure cache Redis (port 6380) is running ─────────────────────────────────
echo "Checking cache Redis (port 6380)..."
if ! redis-cli -p 6380 ping > /dev/null 2>&1; then
    echo "  Starting cache Redis on port 6380..."
    redis-server \
        --port 6380 \
        --daemonize yes \
        --logfile /tmp/redis-cache.log \
        --maxmemory 2mb \
        --maxmemory-policy allkeys-lru
    sleep 0.5
else
    # Already running — apply config in case it changed
    redis-cli -p 6380 CONFIG SET maxmemory      2mb       > /dev/null
    redis-cli -p 6380 CONFIG SET maxmemory-policy allkeys-lru > /dev/null
fi

# Stream Redis (port 6379) — assumed always running (system service), no limit
echo "Confirming stream Redis (port 6379)..."
redis-cli -p 6379 ping > /dev/null
redis-cli -p 6379 CONFIG SET maxmemory 0 > /dev/null  # no limit — stream must never evict

# ── Wipe data ─────────────────────────────────────────────────────────────────
echo "Clearing stream Redis (port 6379) — order_events..."
redis-cli -p 6379 DEL order_events > /dev/null

echo "Clearing cache Redis (port 6380) — order:* keys..."
redis-cli -p 6380 --scan --pattern "order:*" | xargs -r redis-cli -p 6380 DEL > /dev/null 2>&1 || true

echo "Clearing MongoDB events and sagas..."
mongosh cqrs_events --quiet --eval "db.events.deleteMany({}); db.sagas.deleteMany({})"

echo "Clearing PostgreSQL read model..."
psql -U cqrs -d cqrs_read -q <<'SQL'
DROP MATERIALIZED VIEW IF EXISTS mv_daily_revenue CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_orders_by_customer CASCADE;
DROP TABLE IF EXISTS processed_events, order_items, orders, customers, inventory CASCADE;
SQL
psql -U cqrs -d cqrs_read -f "$ROOT/migrations/init.sql" -q

# ── Start Python services ─────────────────────────────────────────────────────
source "$VENV"

echo ""
echo "Starting services..."
[[ -n "$DISABLE_CACHE"         ]] && echo "  cache:    DISABLED"                          || echo "  cache:    enabled  (redis :6380, 2MB allkeys-lru)"
[[ "$CONSISTENCY_DELAY" != "0" ]] && echo "  consumer: SLOW (${CONSISTENCY_DELAY}s/event)" || echo "  consumer: fast"
echo "  stream:   redis :6379  (no memory limit)"
echo ""

cd "$ROOT/write-api" && \
    REDIS_CACHE_URL="redis://localhost:6380" \
    uvicorn main:app --port 8001 --log-level warning > /tmp/write-api.log 2>&1 &
WRITE_PID=$!

cd "$ROOT/read-api" && \
    DISABLE_CACHE="$DISABLE_CACHE" \
    REDIS_CACHE_URL="redis://localhost:6380" \
    uvicorn main:app --port 8002 --log-level warning > /tmp/read-api.log 2>&1 &
READ_PID=$!

cd "$ROOT/consumer" && \
    REDIS_URL="redis://localhost:6379" \
    CONSISTENCY_DELAY="$CONSISTENCY_DELAY" \
    $PYTHON -u worker.py > /tmp/consumer.log 2>&1 &
CONSUMER_PID=$!

cd "$ROOT/saga" && \
    REDIS_URL="redis://localhost:6379" \
    $PYTHON -u saga_worker.py > /tmp/saga.log 2>&1 &
SAGA_PID=$!

# Wait for APIs to be up
for i in $(seq 1 10); do
    sleep 0.5
    OK=0
    curl -sf http://localhost:8001/health > /dev/null 2>&1 && \
    curl -sf http://localhost:8002/health > /dev/null 2>&1 && OK=1
    [[ $OK -eq 1 ]] && break
done

echo "PIDs: write=$WRITE_PID  read=$READ_PID  consumer=$CONSUMER_PID  saga=$SAGA_PID"
echo "Logs: /tmp/write-api.log  /tmp/read-api.log  /tmp/consumer.log  /tmp/saga.log"
echo "      /tmp/redis-cache.log  (cache Redis :6380)"
