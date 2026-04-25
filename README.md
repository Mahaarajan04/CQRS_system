# CQRS Order Processing System

A fully instrumented implementation of the **Command Query Responsibility Segregation (CQRS)** and **Event Sourcing** patterns, built as a course project for CS315. The system processes orders through a distributed pipeline and includes six runnable demos that make the core CQRS trade-offs measurable and visible.

---

## What this is

In a traditional application, the same database handles both writes and reads. CQRS separates them: **commands** (writes) go to one model optimised for fast appends; **queries** (reads) go to a separate model optimised for the access patterns your UI needs. The two models are kept in sync asynchronously by a **consumer** that processes events from a stream.

This project implements that pattern end-to-end:

- **Write path** — a FastAPI service accepts orders, appends events to MongoDB, and publishes them to a Redis Stream.
- **Consumer** — reads the stream, projects events into a PostgreSQL read model, and maintains a pre-aggregated materialized view.
- **Read path** — a second FastAPI service queries PostgreSQL (with a Redis read-your-writes cache in front of it).
- **Saga** — a separate worker orchestrates the multi-step order lifecycle (`placed → reserved → confirmed → shipped`) and persists state to MongoDB for crash recovery.
- **Dashboard** — a Vite/React frontend that polls the Read API and displays live order state, cache hit rate, and revenue charts.

---

## Architecture

```
┌─────────────┐   POST /orders   ┌───────────────┐   XADD   ┌──────────────────┐
│   Client    │ ───────────────► │   Write API   │ ────────► │  Redis Stream    │
└─────────────┘                  │  (FastAPI)    │           │  (order_events)  │
                                 │  MongoDB      │           └────────┬─────────┘
                                 │  (events +    │                    │ XREADGROUP
                                 │   sagas)      │           ┌────────▼─────────┐
                                 └───────────────┘           │    Consumer      │
                                                             │  projects into   │
┌─────────────┐  GET /orders/:id  ┌───────────────┐         │   PostgreSQL     │
│   Client    │ ◄──────────────── │   Read API    │ ◄───────┘  (read model +  │
└─────────────┘                  │  (FastAPI)    │            materialized     │
                                 │  Redis Cache  │            view)            │
                                 └───────────────┘           └─────────────────┘
```

---

## Repository Structure

```
.
├── write-api/              # FastAPI — accepts commands, appends to MongoDB + Redis Stream
│   ├── main.py             # POST /orders endpoint
│   ├── events.py           # event schema and MongoDB helpers
│   └── cache.py            # read-your-writes cache invalidation on write
│
├── read-api/               # FastAPI — serves queries from PostgreSQL
│   ├── main.py             # GET /orders, GET /analytics/revenue
│   └── cache.py            # Redis cache layer (X-Cache header)
│
├── consumer/               # Stream consumer — projects events into PostgreSQL
│   ├── worker.py           # XREADGROUP loop, acks, retry logic
│   └── projections.py      # project_event(), refresh_materialized_views()
│
├── saga/                   # Saga orchestrator — manages order lifecycle
│   ├── saga_worker.py      # event loop, picks up OrderPlaced events
│   ├── orchestrator.py     # SagaOrchestrator — reserve → pay → ship steps
│   └── recovery.py         # scans for stuck sagas and resumes them
│
├── frontend/               # Vite + React dashboard
│   └── src/                # live order table, cache hit rate, revenue chart
│
├── migrations/
│   └── init.sql            # PostgreSQL schema + mv_daily_revenue materialized view
│
├── scripts/
│   ├── seed.py             # bulk-insert synthetic orders into MongoDB + Redis
│   ├── consistency_demo.py # polls read side to measure projection lag
│   ├── mv_benchmark.py     # MV vs raw JOIN vs MongoDB aggregation benchmark
│   ├── replay.py           # replays MongoDB event log into PostgreSQL
│   ├── replay_demo.sh      # end-to-end event replay demo (drop → replay → verify)
│   ├── benchmark_cache.sh  # two-phase cache on/off benchmark
│   ├── plot_benchmark.py   # plots RYW lag from cached/nocache benchmark result files
│   ├── consistency_plot.py # plots consistency lag p50/p95/p99 from consistency_demo logs
│   └── test_flow.py        # integration smoke test
│
├── results/                # raw benchmark output files
├── docker-compose.yml      # all services + infrastructure
└── restart.sh              # tear down, wipe volumes, rebuild, wait for health
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Write model / event log | MongoDB 7 |
| Event stream | Redis 7 Streams |
| Read model | PostgreSQL 16 |
| Read-your-writes cache | Redis 7 (separate instance, 2 MB LRU) |
| Write API / Read API | Python 3.12 · FastAPI · Uvicorn |
| Consumer / Saga | Python 3.12 |
| Dashboard | Vite · React |
| Container orchestration | Docker Compose |

---

## Demo Guide

> All demos are run from the **project root**. The dashboard at **http://localhost:5173** shows live state for most demos; some are best observed in the **terminal** (numeric output / latency measurements). Each demo below specifies **where to watch**.

---

## Setup — Run Once

### Architecture

The services (Write API, Read API, Consumer, Saga) run inside Docker. The databases also run inside Docker but **expose ports to localhost** so that the local Python scripts (`scripts/`, `saga/recovery.py`) can connect directly without needing separate local installs.

| Database | Docker service | Localhost port |
|---|---|---|
| MongoDB | `mongodb` | `27017` |
| PostgreSQL | `postgres` | `5432` |
| Redis (stream) | `redis-stream` | `6379` |
| Redis (cache) | `redis-cache` | `6380` |

### Step 0 — Install Docker

**macOS:**
```bash
brew install --cask docker
open /Applications/Docker.app
```

**Linux:**
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER   # log out and back in after this
```

**Windows:** Download and install Docker Desktop from https://www.docker.com/products/docker-desktop and wait for the whale icon in the taskbar to stop animating.

---

### Step 1 — Install Python Dependencies

**macOS / Linux:**
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Step 2 — Start Docker Services

```bash
bash restart.sh
```

> **Windows:** Use Git Bash or WSL. If `bash` is unavailable, use WSL.

Wait ~15 seconds for all containers to start. All local scripts require Docker to be running since they connect to the databases over the exposed localhost ports listed above.

---

## Setup — Dashboard

### Install Node

**macOS:**
```bash
brew install node
```

**Linux:**
```bash
sudo apt-get install -y nodejs npm
```

**Windows:** Download and install from https://nodejs.org

Then install project dependencies:
```bash
npm install
```

### Start the Dashboard

```bash
npm run dev
```

Open **http://localhost:5173** in your browser.

---

## Demo 1 — Basic CQRS Flow (Write → Event → Project → Read)

**Where to watch:** **Dashboard** (http://localhost:5173) — orders table populates and the status badge progresses `placed → reserved → confirmed → shipped`.

Place five orders:

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

> **Windows:** `curl` is available in PowerShell 7+ and Git Bash. If unavailable, use the Write API Swagger UI at http://localhost:8001/docs.

Each row appears in the dashboard within ~1 second and the status badge changes as the saga progresses. The inventory bar for Laptop decreases.

---

## Demo 2 — Read-Your-Writes Cache (Before/After Benchmark)

**Where to watch:** **Terminal** — the script prints p50/p95/p99 latency for both phases and a side-by-side speedup table. The dashboard's Hit Rate chip also climbs during phase 2.

The benchmark runs two phases back-to-back:

- **Phase 1** — cache OFF + slow consumer → every read goes to PostgreSQL through the full projection lag (the baseline).
- **Phase 2** — cache ON + same slow consumer → reads return in ~1ms from Redis (the win).

```bash
bash scripts/benchmark_cache.sh                      # default N=500, consumer delay=0.1s
bash scripts/benchmark_cache.sh --n 100 --delay 0.05
```

At the end you'll see a `Speedup:` block (e.g. `p99  430.2 ms → 1.4 ms  (300x faster)`) and the cache hit rate from phase 2. Raw output is saved under `results/`.

---

## Demo 3 — Eventual Consistency (Projection Lag)

**Where to watch:** **Terminal** — the script places orders and polls the read side until each one appears, printing the exact lag in milliseconds.

**Step 1 — slow consumer + cache OFF (see the gap):**
```bash
bash restart.sh --delay 0.1 --no-cache
python3 scripts/consistency_demo.py            # N=5 orders
python3 scripts/consistency_demo.py --n 20     # larger sample for stats
```

With `--no-cache`, every `GET /orders/{id}` goes straight to PostgreSQL. Each order prints `stale … stale … VISIBLE` with timestamps, followed by a p50/p95/p99 summary.

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

**Where to watch:** **Dashboard** for the revenue chart (bars per region) + **Terminal** for the speedup numbers.

**Step 1 — seed data:**
```bash
python3 scripts/seed.py --orders 5000 --publish
```

Wait 30–60 seconds for the consumer to project all events. The Revenue chart on the dashboard will populate with bars for each region.

**Step 2 — run the benchmark:**
```bash
python3 scripts/mv_benchmark.py                                    # read + write benchmark (default: 200 writes, 20 threads)
python3 scripts/mv_benchmark.py --read-only                        # read benchmark only
python3 scripts/mv_benchmark.py --write-n 500 --write-workers 50  # scale up write benchmark
```

The benchmark runs the same daily-revenue analytical query three ways — CQRS materialized view, raw SQL JOIN, and MongoDB aggregation pipeline — and reports median / p95 over 5 trials (2 warm-up runs discarded). It also runs a concurrent write throughput comparison (CQRS event append vs direct PostgreSQL 3NF INSERT).

---

## Demo 5 — Saga Recovery (Stuck Saga Resumed)

**Where to watch:** **Dashboard + Terminal** — an order visibly stuck in `reserved` state that never progresses, then suddenly jumps to `confirmed/shipped` after recovery runs.

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

Watch the dashboard — order appears as `placed`, then `reserved`. Now **kill the saga container**:

```bash
docker compose stop saga
```

The order is now stuck at `reserved` and will never progress on its own.

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

```bash
bash scripts/replay_demo.sh           # use existing data
bash scripts/replay_demo.sh --seed    # seed 2000 orders first
```

What you'll see:

- **STEP 1** — current row counts in `orders`, `customers`, `order_items` + total event count in MongoDB
- **STEP 2** — all PostgreSQL tables dropped (Read API is now broken)
- **STEP 3** — confirms `public` schema is empty
- **STEP 4** — replays every event from MongoDB back into PostgreSQL (~1,550 events/sec)
- **STEP 5** — row counts after replay match the originals exactly
- **STEP 6** — point-in-time replay to a midpoint timestamp (reconstructs ~half the orders)
- **STEP 7** — restores to full current state

PostgreSQL is a **disposable projection**. The event log in MongoDB is the only source of truth — any historical state is recoverable by replaying a prefix of the log.

---

## Plotting Results

### Cache Benchmark Plots (`scripts/plot_benchmark.py`)

Parses `cached_slowconsumer_{delay}_{N}.txt` and `nocache_slowconsumer_{delay}_{N}.txt` files from `results/` and produces 5 graphs:

| Output file | Contents |
|---|---|
| `ryw_lag_p50.png` | p50 RYW lag — all modes × delays on one chart |
| `ryw_lag_p95.png` | p95 RYW lag — all modes × delays |
| `ryw_lag_p99.png` | p99 RYW lag — all modes × delays |
| `ryw_lag_cached_delay0_01.png` | Cached only, delay=0.01s — p50/p95/p99 as separate lines |
| `ryw_lag_nocache_delay0_01.png` | No-cache only, delay=0.01s — p50/p95/p99 as separate lines |

```bash
# Run after benchmark_cache.sh has populated results/
python3 scripts/plot_benchmark.py                         # reads from results/, writes plots there too
python3 scripts/plot_benchmark.py --dir results/my_run   # specific results directory
python3 scripts/plot_benchmark.py --dir results/ --out plots/
```

### Consistency Lag Plots (`scripts/consistency_plot.py`)

Parses `run_n{N}_d{delay}.log` files from `results/consistency_logs/` and produces 3 graphs (one per percentile):

| Output file | Contents |
|---|---|
| `lag_p50.png` | p50 consistency lag vs N, one line per delay |
| `lag_p95.png` | p95 consistency lag vs N |
| `lag_p99.png` | p99 consistency lag vs N |

Log files must be named exactly `run_n100_d0.10.log` (N and delay encoded in filename).

```bash
# Run after consistency_demo.py --n <N> has been run at multiple delays
python3 scripts/consistency_plot.py                                          # default: results/consistency_logs/
python3 scripts/consistency_plot.py --dir results/consistency_logs --out results/
```

---

## Quick Reference

| Demo | Where to watch | Signal |
|---|---|---|
| 1. Basic flow | Dashboard | Order row appears, status badge progresses through saga states |
| 2. Cache benchmark | Terminal | p50/p95/p99 before vs after, speedup table |
| 3. Consistency gap | Terminal | Per-order lag in ms (stale → VISIBLE) |
| 4. MV / Revenue | Dashboard + Terminal | Chart populates; benchmark prints speedup |
| 5. Saga recovery | Dashboard + Terminal | Stuck badge → resumes |
| 6. Replay | Terminal | Tables dropped → replayed → row counts match |

---

## Ports

| Service | URL |
|---|---|
| Dashboard | http://localhost:5173 |
| Write API | http://localhost:8001/docs |
| Read API | http://localhost:8002/docs |
| MongoDB | localhost:27017 |
| PostgreSQL | localhost:5432 |
| Redis (stream) | localhost:6379 |
| Redis (cache) | localhost:6380 |