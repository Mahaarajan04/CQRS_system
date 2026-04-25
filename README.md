# CQRS Demo Guide

> All demos are run from the **project root**. The dashboard at **http://localhost:3000** shows live state for most demos; some are best observed in the **terminal** (numeric output / latency measurements). Each demo below specifies **where to watch**.

---

## Setup — Run Once

```bash
bash restart.sh
```

Wait ~15 seconds for all containers to start, then open **http://localhost:3000**.

---

## Demo 1 — Basic CQRS Flow (Write → Event → Project → Read)

**Where to watch:****Dashboard** (http://localhost:3000) — Orders table populates and status progresses `placed → reserved → confirmed → shipped`.

```bash
curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"c1","customer_name":"Alice","customer_email":"alice@example.com","region":"Delhi","items":[{"product_id":"p1","name":"Laptop","quantity":1,"price":999.99}]}'

curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"c2","customer_name":"Bob","customer_email":"bob@example.com","region":"Mumbai","items":[{"product_id":"p2","name":"Mouse","quantity":2,"price":29.99},{"product_id":"p3","name":"Keyboard","quantity":1,"price":79.99}]}'

curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"c3","customer_name":"Carol","customer_email":"carol@example.com","region":"Chennai","items":[{"product_id":"p4","name":"Monitor","quantity":1,"price":349.99}]}'

curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"c4","customer_name":"Dave","customer_email":"dave@example.com","region":"Bengaluru","items":[{"product_id":"p1","name":"Laptop","quantity":1,"price":999.99},{"product_id":"p2","name":"Mouse","quantity":1,"price":29.99}]}'

curl -s -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"c5","customer_name":"Eve","customer_email":"eve@example.com","region":"Pune","items":[{"product_id":"p3","name":"Keyboard","quantity":2,"price":79.99},{"product_id":"p4","name":"Monitor","quantity":1,"price":349.99}]}'

```

Watch the Orders table — the new row appears within ~1 second and the status badge changes as the saga progresses. Inventory bar for Laptop decreases.

---

## Demo 2 — Read-Your-Writes Cache (Before/After Benchmark)

**Where to watch:** **Terminal** — the script prints p50/p95/p99 latency for both phases and a side-by-side speedup table. The dashboard's Hit Rate chip will also climb during phase 2 as a secondary visual.

The benchmark runs two phases back-to-back so you can see exactly what the cache buys you under a slow projection pipeline:

- **Phase 1** — cache OFF + slow consumer → every read goes to PostgreSQL through the full lag window (the baseline).
- **Phase 2** — cache ON + same slow consumer → repeat reads return in ~1ms from Redis (the win).

```bash
bash scripts/benchmark_cache.sh                    # default N=500, consumer delay=0.1s
bash scripts/benchmark_cache.sh --n 100 --delay 0.05
```

At the end you'll see a `Speedup:` block (e.g. `p99  430.2 ms  →  1.4 ms  (300x faster)`) and the cache hit rate from phase 2. Raw output is saved under `results/`.

---

## Demo 3 — Eventual Consistency (Projection Lag)

**Where to watch:**  **Terminal** — the script places orders and polls the read side until each one appears, printing the exact lag in milliseconds. The dashboard alone can't show you *how long* the gap is; the terminal output makes it concrete.

This demo shows the lag window between a write landing in MongoDB (Write API) and it becoming visible on the read side (PostgreSQL via Read API).

**Step 1 — slow consumer + cache OFF (see the gap):**
```bash
bash restart.sh --delay 2 --no-cache
python3 scripts/consistency_demo.py            # N=5 orders
python3 scripts/consistency_demo.py --n 20     # larger sample for stats
```

The `--delay` flag adds a 2-second sleep in the consumer (simulates a slow projection pipeline). With no cache, every `GET /orders/{id}` goes straight to PostgreSQL — so you see the full lag window. Each order prints `stale, stale, stale, … VISIBLE` with timestamps, and a final p50/p95/p99 summary.

**Step 2 — cache ON, same slow consumer (see what the cache fixes):**
```bash
bash restart.sh --delay 2
python3 scripts/consistency_demo.py
```

Every read now returns in ~1ms with `X-Cache: HIT` — the read-your-writes cache hides the projection lag entirely.

**Restore normal speed when done:**
```bash
bash restart.sh
```

---

## Demo 4 — Materialized View Performance (Revenue Chart)

**Where to watch:** **Dashboard** for the visual (Revenue chart fills in with bar-per-region data) +  **Terminal** for the speedup numbers (`mv_benchmark.py` prints MV vs raw JOIN vs MongoDB latencies).

```bash
# Seed 2000 orders spread across all regions
python3 scripts/seed.py --orders 2000 --publish
```

Wait 10–15 seconds for projection. The Revenue chart will show bars for different regions.

To see the speedup numbers (MV vs raw JOIN vs MongoDB):
```bash
python3 scripts/mv_benchmark.py --read-n 7
```

The dashboard revenue data comes from the materialized view — the benchmark proves it is 100x+ faster than the equivalent raw JOIN.

---

## Demo 5 — Saga Recovery (Stuck Saga Resumed)

**Where to watch:** **Dashboard \ Terminal** — an order visibly stuck in `reserved` state that never progresses, then suddenly jumps to `confirmed/shipped` after recovery runs. Keep a  **terminal** open too for the recovery script's log output.

**Terminal 1** — restart with a slow saga so you have a kill window:
```bash
bash restart.sh 
docker compose exec saga env SAGA_STEP_DELAY_SEC=15 python saga_worker.py
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

## Demo 6 — Event Replay (PostgreSQL is Disposable)

**Where to watch:** **Terminal** — the script prints before/after row counts, replay progress, and a point-in-time replay step. 

The script does the whole demo end-to-end: shows current state → drops every PostgreSQL table → replays the full MongoDB event log → verifies row counts match → optionally replays to a midpoint timestamp to demonstrate point-in-time recovery.

```bash
# Use existing data (run after demos 1/2/5 have populated some orders)
bash scripts/replay_demo.sh

# Or seed 2000 orders first if PostgreSQL is empty
bash scripts/replay_demo.sh --seed
```

What you'll see in the terminal:
- **STEP 1** — current row counts in `orders`, `customers`, `order_items` + total events in MongoDB
- **STEP 2** — all PostgreSQL tables dropped (read API is now broken)
- **STEP 3** — confirms `public` schema is empty
- **STEP 4** — replays every event from MongoDB back into PostgreSQL
- **STEP 5** — row counts after replay match the originals exactly
- **STEP 6** — point-in-time replay to the midpoint timestamp (a smaller subset of orders)
- **STEP 7** — restores to full current state

The key takeaway: PostgreSQL is a **disposable projection**. The event log in MongoDB is the only source of truth, and any historical state can be reconstructed from it.

---

## Quick Reference

| Demo | Where to watch | Signal |
|---|---|---|
| 1. Basic flow | Dashboard | Order row appears, status badge progresses through saga states |
| 2. Cache benchmark | Terminal | p50/p95/p99 before vs after, speedup table |
| 3. Consistency gap | Terminal | Per-order lag in ms (stale → VISIBLE) |
| 4. MV / Revenue | Dashboard + Terminal | Chart populates; benchmark prints speedup |
| 5. Saga recovery | Dashboard + Terminal | Stuck badge → resumes |
| 6. Replay | Terminal  | Tables dropped → replayed → row counts match |

---

## Ports

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| Write API | http://localhost:8001/docs |
| Read API | http://localhost:8002/docs |
