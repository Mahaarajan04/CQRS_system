#!/bin/bash
# Replay Demo — PostgreSQL is disposable; MongoDB is the source of truth.
#
# Usage:
#   bash scripts/replay_demo.sh           # uses existing data
#   bash scripts/replay_demo.sh --seed    # seeds 2000 orders first

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"

BOLD="\033[1m"; GREEN="\033[92m"; RED="\033[91m"; CYAN="\033[96m"; RESET="\033[0m"
sep()   { printf "\n${BOLD}%s${RESET}\n\n" "$(printf '=%.0s' {1..55})"; }
bold()  { printf "${BOLD}  %s${RESET}\n" "$*"; }
green() { printf "${GREEN}  ✓  %s${RESET}\n" "$*"; }
red()   { printf "${RED}  ✗  %s${RESET}\n" "$*"; }

pg_count() { docker compose exec -T postgres psql -U cqrs cqrs_read -t -c "SELECT COUNT(*) FROM $1;" 2>/dev/null | tr -d ' \n'; }

# ── optional seed ─────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--seed" ]]; then
    sep; bold "Seeding 2000 orders..."
    "$PYTHON" "$ROOT/scripts/seed.py" --orders 2000 --publish
    echo ""; echo "  Waiting 30s for consumer to project..."; sleep 30
fi

# ── check data ────────────────────────────────────────────────────────────────
sep
bold "STEP 1 — PostgreSQL state BEFORE (the read model)"
echo ""

ORDERS=$(pg_count orders)
if [[ "$ORDERS" -eq 0 ]]; then
    red "PostgreSQL is empty. Run:  bash scripts/replay_demo.sh --seed"; exit 1
fi

CUSTOMERS=$(pg_count customers)
ITEMS=$(pg_count order_items)
EVENTS=$("$PYTHON" -c "
import os; from pymongo import MongoClient
c = MongoClient(os.getenv('MONGO_URL','mongodb://localhost:27017'))
print(c['cqrs_events']['events'].count_documents({}))
")

printf "  %-20s ${CYAN}%s rows${RESET}\n"  "orders:"      "$ORDERS"
printf "  %-20s ${CYAN}%s rows${RESET}\n"  "customers:"   "$CUSTOMERS"
printf "  %-20s ${CYAN}%s rows${RESET}\n"  "order_items:" "$ITEMS"
printf "  %-20s ${CYAN}%s events${RESET}\n" "MongoDB (source):" "$EVENTS"

# capture midpoint timestamp for point-in-time demo
MIDPOINT=$("$PYTHON" - <<'PY'
import os; from pymongo import MongoClient
c = MongoClient(os.getenv("MONGO_URL","mongodb://localhost:27017"))
evts = list(c["cqrs_events"]["events"].find({},{"timestamp":1,"_id":0}).sort("timestamp",1))
if len(evts) >= 2: print(evts[len(evts)//2]["timestamp"])
PY
)

# ── wipe postgres ─────────────────────────────────────────────────────────────
sep
bold "STEP 2 — DROP all PostgreSQL tables (simulating full data loss)"
echo ""

docker compose exec -T postgres psql -U cqrs cqrs_read -q <<'SQL'
DROP MATERIALIZED VIEW IF EXISTS mv_daily_revenue CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_orders_by_customer CASCADE;
DROP TABLE IF EXISTS processed_events, order_items, orders, customers, inventory CASCADE;
SQL

red  "All tables dropped."
echo ""
echo "  Read API is now broken. MongoDB still has all $EVENTS events."

# ── verify empty ──────────────────────────────────────────────────────────────
sep
bold "STEP 3 — Verify PostgreSQL is empty"
echo ""

TABLE_COUNT=$(docker compose exec -T postgres psql -U cqrs cqrs_read -t -c \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';" \
    2>/dev/null | tr -d ' \n')

printf "  Tables in public schema: ${RED}%s${RESET}\n\n" "$TABLE_COUNT"

# ── full replay ───────────────────────────────────────────────────────────────
sep
bold "STEP 4 — Rebuild PostgreSQL from MongoDB event log"
echo ""

# restore schema first (tables were dropped)
docker compose exec -T postgres psql -U cqrs cqrs_read -q \
    -f /docker-entrypoint-initdb.d/init.sql 2>/dev/null || true

"$PYTHON" "$ROOT/scripts/replay.py" --quiet
echo ""

# ── verify restored ───────────────────────────────────────────────────────────
sep
bold "STEP 5 — PostgreSQL state AFTER"
echo ""

ORDERS_AFTER=$(pg_count orders)
CUSTOMERS_AFTER=$(pg_count customers)
ITEMS_AFTER=$(pg_count order_items)

printf "  %-20s ${GREEN}%s rows${RESET}  (was %s)\n" "orders:"      "$ORDERS_AFTER"      "$ORDERS"
printf "  %-20s ${GREEN}%s rows${RESET}  (was %s)\n" "customers:"   "$CUSTOMERS_AFTER"   "$CUSTOMERS"
printf "  %-20s ${GREEN}%s rows${RESET}  (was %s)\n" "order_items:" "$ITEMS_AFTER"        "$ITEMS"
echo ""

if [[ "$ORDERS_AFTER" -eq "$ORDERS" ]]; then
    green "Full restore confirmed — row counts match exactly."
else
    red   "Row count mismatch: expected $ORDERS, got $ORDERS_AFTER"
fi

# ── point-in-time replay ──────────────────────────────────────────────────────
if [[ -n "${MIDPOINT:-}" ]]; then
    sep
    bold "STEP 6 — Point-in-time replay (state as of ${MIDPOINT:0:19})"
    echo ""
    echo "  Impossible in a traditional UPDATE-based DB — events are immutable."
    echo ""

    "$PYTHON" "$ROOT/scripts/replay.py" --until "$MIDPOINT" --quiet
    echo ""

    ORDERS_PIT=$(pg_count orders)
    printf "  Orders at midpoint:  ${CYAN}%s${RESET}  (full dataset: %s)\n" "$ORDERS_PIT" "$ORDERS_AFTER"
    echo "  Orders placed after $MIDPOINT do not exist in this view."

    sep
    bold "STEP 7 — Restoring to current state..."
    echo ""
    "$PYTHON" "$ROOT/scripts/replay.py" --quiet
    green "Back to full current state ($ORDERS_AFTER orders)."
fi

# ── summary ───────────────────────────────────────────────────────────────────
sep
bold "DONE"
echo ""
echo "  • PostgreSQL is a disposable projection of the MongoDB event log"
echo "  • Full rebuild from $EVENTS events completed in seconds"
echo "  • Point-in-time replay is free — events are immutable by design"
echo ""
