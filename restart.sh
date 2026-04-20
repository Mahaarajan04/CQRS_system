#!/bin/bash
# Restart all Python services and wipe all data for a clean slate

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Stopping Python services..."
pkill -f "uvicorn main:app" 2>/dev/null
pkill -f "worker.py" 2>/dev/null
sleep 1


echo "Clearing Redis stream..."
redis-cli DEL order_events > /dev/null

echo "Clearing MongoDB events and sagas..."
mongosh cqrs_events --quiet --eval "db.events.deleteMany({}); db.sagas.deleteMany({})"

echo "Clearing PostgreSQL read model..."
psql -U cqrs -d cqrs_read -q <<'SQL'
DROP MATERIALIZED VIEW IF EXISTS mv_daily_revenue CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_orders_by_customer CASCADE;
DROP TABLE IF EXISTS processed_events, order_items, orders, customers, inventory CASCADE;
SQL
psql -U cqrs -d cqrs_read -f migrations/init.sql -q

echo "Starting services..."
bash "$ROOT/start.sh"
