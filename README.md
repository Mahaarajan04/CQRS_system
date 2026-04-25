# CQRS Demo Guide

> All demos are run from the **project root**. The dashboard at **http://localhost:3000** shows everything live — no need to read terminal output.

---

## Setup — Run Once

```bash
bash restart.sh
```

Wait ~15 seconds for all containers to start, then open **http://localhost:3000**.

---

## Demo 1 — Basic CQRS Flow (Write → Event → Project → Read)

**What to watch on dashboard:** Orders table populates. Status progresses `placed → reserved → confirmed → shipped`.

```bash
# Place a single order
curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "c1", "customer_name": "Alice", "customer_email": "alice@example.com",
    "region": "us-east",
    "items": [{"product_id": "p1", "name": "Laptop", "quantity": 1, "price": 999.99}]
  }' | python3 -m json.tool
```

Watch the Orders table — the new row appears within ~1 second and the status badge changes as the saga progresses. Inventory bar for Laptop decreases.

---

## Demo 2 — Saga States (placed → reserved → confirmed → shipped)

**What to watch on dashboard:** Orders table. Each order moves through colored status badges in sequence.

```bash
# Place 10 orders rapidly and watch the saga progress
python3 scripts/seed.py --orders 10 --publish
```

You will see:
- **Blue** `placed` badge appear first
- **Purple** `reserved` — inventory allocated
- **Green** `confirmed` — payment accepted
- **Teal** `shipped` — order complete

Some orders may show **Orange** `failed` — that is the 15% payment failure rate by design.

---

## Demo 3 — Read-Your-Writes Cache

**What to watch on dashboard:** Cache Stats bar — Hit Rate climbs from 0% toward 90%+.

```bash
# Restart with default settings (cache ON)
bash restart.sh

# Seed 200 orders to populate the read model
python3 scripts/seed.py --orders 200 --publish
sleep 5

# Flood the read API — every repeat read will be a cache hit
python3 - <<'EOF'
import requests, random

# Collect order IDs
ids = [o["order_id"] for o in requests.get("http://localhost:8002/orders?limit=200").json()]
print(f"Loaded {len(ids)} order IDs")

# Read each order twice — first read = MISS, second = HIT
for order_id in ids:
    requests.get(f"http://localhost:8002/orders/{order_id}")
    requests.get(f"http://localhost:8002/orders/{order_id}")
print("Done — check dashboard hit rate")
EOF
```

Watch the **Hit Rate** chip climb. After all orders are read twice it should be ~50%+. Keep refreshing to see it approach 90%+ as repeated reads accumulate.

---

## Demo 4 — Eventual Consistency (Projection Lag)

**What to watch on dashboard:** Orders table. Place an order — it does NOT appear immediately. You can see the gap.

```bash
# Restart with 3-second consumer delay and cache OFF
bash restart.sh --delay 3 --no-cache
```

Now place an order:
```bash
curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "c2", "customer_name": "Bob", "customer_email": "bob@example.com",
    "region": "eu-west",
    "items": [{"product_id": "p2", "name": "Mouse", "quantity": 2, "price": 29.99}]
  }' | python3 -m json.tool
```

Watch the dashboard — the order **does not appear for ~3 seconds**. That 3-second window is the consistency gap. The write API returned success but the read model hasn't caught up yet.

**Restore normal speed when done:**
```bash
bash restart.sh
```

---

## Demo 5 — Materialized View Performance (Revenue Chart)

**What to watch on dashboard:** Revenue chart fills in with bar-per-region data.

```bash
# Seed 2000 orders spread across all regions
python3 scripts/seed.py --orders 2000 --publish
```

Wait 10–15 seconds for projection. The Revenue chart will show bars for `us-east`, `us-west`, `eu-west`, `ap-south`.

To see the speedup numbers (MV vs raw JOIN vs MongoDB):
```bash
python3 scripts/mv_benchmark.py --read-n 7
```

The dashboard revenue data comes from the materialized view — the benchmark proves it is 100x+ faster than the equivalent raw JOIN.

---

## Demo 6 — Saga Recovery (Stuck Saga Resumed)

**What to watch on dashboard:** An order stuck in `reserved` state that never progresses — then suddenly jumps to `confirmed/shipped` after recovery runs.

**Terminal 1** — restart with a slow saga so you have a kill window:
```bash
SAGA_STEP_DELAY_SEC=8 docker compose up -d --no-deps --build saga
```

**Terminal 2** — place an order:
```bash
curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "c3", "customer_name": "Carol", "customer_email": "carol@example.com",
    "region": "ap-south",
    "items": [{"product_id": "p3", "name": "Keyboard", "quantity": 1, "price": 79.99}]
  }' | python3 -m json.tool
```

Watch the dashboard — order appears as `placed`. After ~2s it becomes `reserved`. Now **kill the saga container**:

```bash
docker compose stop saga
```

Watch the dashboard — the order is stuck at `reserved`. It will never progress on its own.

**Run recovery:**
```bash
python3 saga/recovery.py --timeout-sec 5
```

Watch the dashboard — the stuck order jumps to `confirmed` then `shipped`.

**Restore saga:**
```bash
docker compose up -d --no-deps saga
```

---

## Demo 7 — Event Replay (PostgreSQL is Disposable)

**What to watch on dashboard:** Orders table goes **empty**, then refills completely.

```bash
# First seed some data so there is something to lose
python3 scripts/seed.py --orders 500 --publish
sleep 15
```

Dashboard should show 500 orders. Now simulate a database disaster:

```bash
# Wipe the entire read model (PostgreSQL)
docker compose exec postgres psql -U cqrs cqrs_read \
  -c "TRUNCATE orders, order_items, customers, inventory CASCADE;"

# Re-seed inventory (saga needs it)
docker compose exec postgres psql -U cqrs cqrs_read \
  -c "INSERT INTO inventory (product_id, name, total_qty) VALUES
      ('p1','Laptop',100000),('p2','Mouse',500000),
      ('p3','Keyboard',300000),('p4','Monitor',200000)
      ON CONFLICT DO NOTHING;"
```

Watch the dashboard — **orders table goes blank**, revenue chart empty, customer list empty.

Now replay from MongoDB (the source of truth):

```bash
python3 scripts/replay.py
```

Watch the dashboard — rows reappear as replay progresses. When done, the count matches what was there before.

---

## Quick Reference

| Demo | Key flag | Dashboard signal |
|---|---|---|
| Basic flow | — | Order row appears, status badge changes |
| Saga states | — | Badge color progression |
| Cache | — | Hit Rate chip climbs |
| Consistency gap | `--delay 3 --no-cache` | Order missing for 3s |
| MV / Revenue | seed 2000 orders | Revenue chart populates |
| Saga recovery | kill saga mid-flight | Stuck badge → resumes |
| Replay | TRUNCATE then replay | Table empties → refills |

---

## Ports

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| Write API | http://localhost:8001/docs |
| Read API | http://localhost:8002/docs |
