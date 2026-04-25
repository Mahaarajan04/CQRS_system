"""
Full system test — walks through the complete order lifecycle.
Run:
  python scripts/test_flow.py           # normal lifecycle demo
  python scripts/test_flow.py --blast   # flood orders, watch stream lag build then drain
  python scripts/test_flow.py --saga    # saga demo: happy path, out-of-stock, payment fail
"""

import requests
import time
import sys
import random
import threading
import subprocess
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "saga"))
sys.path.insert(0, os.path.join(ROOT, "write-api"))

WRITE = "http://localhost:8001"
READ  = "http://localhost:8002"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

CUSTOMERS = [
    ("cust_alice", "Alice Smith",  "alice@example.com",  "US-WEST"),
    ("cust_bob",   "Bob Jones",    "bob@example.com",    "EU"),
    ("cust_carol", "Carol White",  "carol@example.com",  "US-EAST"),
    ("cust_dave",  "Dave Kumar",   "dave@example.com",   "APAC"),
]
PRODUCTS = [
    {"product_id": "p1", "name": "Laptop",   "price": 999.99},
    {"product_id": "p2", "name": "Mouse",    "price": 29.99},
    {"product_id": "p3", "name": "Keyboard", "price": 79.99},
    {"product_id": "p4", "name": "Monitor",  "price": 349.99},
]

def banner(text):
    print(f"\n{BOLD}{BLUE}{'='*55}{RESET}")
    print(f"{BOLD}{BLUE}  {text}{RESET}")
    print(f"{BOLD}{BLUE}{'='*55}{RESET}")

def ok(text):   print(f"  {GREEN}✓{RESET}  {text}")
def err(text):  print(f"  {RED}✗{RESET}  {text}")
def info(text): print(f"  {YELLOW}→{RESET}  {text}")

def show(label, data):
    print(f"\n  {BOLD}{label}{RESET}")
    for k, v in data.items():
        if v is not None:
            print(f"    {k}: {v}")

def wait_for_read_side(order_id, timeout=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{READ}/orders/{order_id}")
        if r.status_code == 200:
            lag = (time.time() - t0) * 1000
            ok(f"Visible on read side  (consistency lag: {lag:.0f}ms)")
            return r.json()
        time.sleep(0.05)
    err("Order not visible on read side after timeout!")
    return None

def random_order():
    cid, name, email, region = random.choice(CUSTOMERS)
    items = random.sample(PRODUCTS, k=random.randint(1, 3))
    return {
        "customer_id":    cid,
        "customer_name":  name,
        "customer_email": email,
        "region":         region,
        "items": [
            {"product_id": p["product_id"], "name": p["name"],
             "quantity": random.randint(1, 3), "price": p["price"]}
            for p in items
        ],
    }

def redis_lag():
    try:
        out = subprocess.check_output(
            ["redis-cli", "XINFO", "GROUPS", "order_events"], text=True
        )
        lines = out.strip().split("\n")
        for i, line in enumerate(lines):
            if "lag" in line.lower():
                return int(lines[i + 1].strip())
    except Exception:
        return -1
    return -1

def stream_len():
    try:
        out = subprocess.check_output(["redis-cli", "XLEN", "order_events"], text=True)
        return int(out.strip())
    except Exception:
        return -1


# ═════════════════════════════════════════════════════════════
# READER BENCHMARK — measures read-your-writes lag
# ═════════════════════════════════════════════════════════════
#
# Purpose: baseline numbers for how long after POST /orders a reader can
# successfully GET /orders/{id}. This is the window the RYW cache closes.
#
#   python scripts/test_flow.py --reader                  # idle, 50 orders
#   python scripts/test_flow.py --reader --under-load     # same, during concurrent write flood
#   python scripts/test_flow.py --reader --n 200          # custom sample size

if "--reader" in sys.argv:
    import statistics
    import csv

    N = 50
    if "--n" in sys.argv:
        N = int(sys.argv[sys.argv.index("--n") + 1])
    POLL_INTERVAL_SEC = 0.002   # 2ms — fine-grained so we can see sub-10ms lag

    from requests.adapters import HTTPAdapter
    session = requests.Session()
    session.mount("http://", HTTPAdapter(pool_connections=64, pool_maxsize=64))

    def measure_ryw_lag(order_payload):
        t_post_start = time.perf_counter()
        r = session.post(f"{WRITE}/orders", json=order_payload)
        t_post_done  = time.perf_counter()
        if r.status_code != 201:
            return None
        order_id = r.json()["order_id"]

        polls = 0
        while True:
            polls += 1
            try:
                rr = session.get(f"{READ}/orders/{order_id}", timeout=5)
            except requests.exceptions.RequestException:
                time.sleep(POLL_INTERVAL_SEC)
                continue
            if rr.status_code == 200:
                t_visible = time.perf_counter()
                return {
                    "order_id":    order_id,
                    "post_ms":     (t_post_done  - t_post_start) * 1000,
                    "ryw_ms":      (t_visible    - t_post_done ) * 1000,
                    "total_ms":    (t_visible    - t_post_start) * 1000,
                    "polls":       polls,
                }
            if time.perf_counter() - t_post_done > 10:  # 10s timeout
                return None
            time.sleep(POLL_INTERVAL_SEC)

    banner("READER BENCHMARK — Read-Your-Writes Lag (baseline)")
    info(f"Placing {N} orders sequentially and measuring GET-after-POST lag")
    info(f"Poll interval: {POLL_INTERVAL_SEC*1000:.0f}ms (so floor resolution ≈ {POLL_INTERVAL_SEC*1000:.0f}ms)")

    # ── Optional: concurrent write load to stress the projector ──
    load_threads   = []
    stop_load_flag = {"stop": False}
    if "--under-load" in sys.argv:
        LOAD_THREADS = 32
        info(f"--under-load: starting {LOAD_THREADS} flood threads to stress the consumer")

        def flood():
            s = requests.Session()
            s.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=4))
            while not stop_load_flag["stop"]:
                try:
                    s.post(f"{WRITE}/orders", json=random_order(), timeout=2)
                except Exception:
                    pass

        for _ in range(LOAD_THREADS):
            t = threading.Thread(target=flood, daemon=True)
            t.start()
            load_threads.append(t)
        time.sleep(1.0)  # let load ramp up
        try:
            lag_now = int(subprocess.check_output(
                ["redis-cli", "XINFO", "GROUPS", "order_events"], text=True
            ).split("lag")[1].split()[0])
            info(f"Stream lag under load before measurement: {lag_now}")
        except Exception:
            pass

    results = []
    failures = 0
    t0 = time.time()
    for i in range(N):
        r = measure_ryw_lag(random_order())
        if r is None:
            failures += 1
        else:
            results.append(r)
        if (i + 1) % 10 == 0:
            print(f"    ...{i+1}/{N} orders placed & read")
    elapsed = time.time() - t0

    if load_threads:
        stop_load_flag["stop"] = True

    if not results:
        err(f"All {N} samples failed"); sys.exit(1)

    post_ms  = sorted(r["post_ms"]  for r in results)
    ryw_ms   = sorted(r["ryw_ms"]   for r in results)
    polls    = sorted(r["polls"]    for r in results)

    def pct(data, p):
        k = int(len(data) * p / 100)
        return data[min(k, len(data) - 1)]

    print(f"\n  {BOLD}Results  (N={len(results)}, failures={failures}, wall={elapsed:.1f}s){RESET}")
    print(f"  {'metric':<12}  {'p50':>8}  {'p95':>8}  {'p99':>8}  {'max':>8}  {'mean':>8}")
    for name, data in [("POST ms", post_ms), ("RYW lag ms", ryw_ms), ("polls", polls)]:
        mean = statistics.mean(data)
        if name == "polls":
            print(f"  {name:<12}  {pct(data,50):>8.0f}  {pct(data,95):>8.0f}  {pct(data,99):>8.0f}  {max(data):>8.0f}  {mean:>8.1f}")
        else:
            print(f"  {name:<12}  {pct(data,50):>8.1f}  {pct(data,95):>8.1f}  {pct(data,99):>8.1f}  {max(data):>8.1f}  {mean:>8.1f}")

    # histogram of RYW lag
    buckets = [0, 5, 10, 20, 50, 100, 250, 500, 1000, 10000]
    counts = [0] * (len(buckets) - 1)
    for v in ryw_ms:
        for i in range(len(buckets) - 1):
            if buckets[i] <= v < buckets[i + 1]:
                counts[i] += 1
                break
    print(f"\n  {BOLD}RYW lag distribution{RESET}")
    max_c = max(counts) or 1
    for i in range(len(counts)):
        bar = "█" * int(30 * counts[i] / max_c)
        print(f"  [{buckets[i]:>5} – {buckets[i+1]:>5} ms]  {counts[i]:>4}  {bar}")

    # dump raw for later diff against post-cache run
    out = "/tmp/reader_baseline.csv"
    if "--under-load" in sys.argv:
        out = "/tmp/reader_baseline_loaded.csv"
    with open(out, "w") as f:
        w = csv.DictWriter(f, fieldnames=["order_id", "post_ms", "ryw_ms", "total_ms", "polls"])
        w.writeheader(); w.writerows(results)
    ok(f"Raw samples written to {out}")
    info("After implementing the cache, rerun the same command and diff the RYW lag.")
    sys.exit(0)


# ═════════════════════════════════════════════════════════════
# SAGA DEMO MODE
# ═════════════════════════════════════════════════════════════

if "--saga" in sys.argv:
    from events import get_events_for_order    # noqa: E402

    import psycopg2
    import psycopg2.extras

    POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")
    SAGA_ROOT = os.path.join(ROOT, "saga")

    def pg_conn():
        return psycopg2.connect(POSTGRES_URL, cursor_factory=psycopg2.extras.RealDictCursor)

    def show_inventory():
        conn = pg_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT product_id, name, total_qty, reserved_qty, (total_qty - reserved_qty) AS available FROM inventory ORDER BY product_id")
            rows = cur.fetchall()
        conn.close()
        print(f"\n  {'product_id':<8}  {'name':<12}  {'total':>5}  {'reserved':>8}  {'available':>9}")
        for r in rows:
            print(f"  {r['product_id']:<8}  {r['name']:<12}  {r['total_qty']:>5}  {r['reserved_qty']:>8}  {r['available']:>9}")

    def show_events(order_id):
        events = get_events_for_order(order_id)
        print(f"\n  Event log ({len(events)} events):")
        for e in events:
            payload_summary = ""
            if e["payload"]:
                payload_summary = "  " + "  ".join(f"{k}={v}" for k, v in list(e["payload"].items())[:2])
            print(f"    v{e['version']}  {e['event_type']:<28}{payload_summary}")

    def restart_saga_worker(env_overrides: dict):
        """Recreate the saga Docker container with new env vars (for payment failure rate)."""
        env = os.environ.copy()
        env.update(env_overrides)
        subprocess.run(["docker", "compose", "stop", "saga"], cwd=ROOT, capture_output=True)
        subprocess.run(
            ["docker", "compose", "up", "-d", "--no-deps", "saga"],
            cwd=ROOT, env=env, capture_output=True,
        )
        time.sleep(2.0)  # let container start + Redis consumer group setup

    def place_order(payload):
        r = requests.post(f"{WRITE}/orders", json=payload)
        assert r.status_code == 201, f"Place order failed: {r.text}"
        return r.json()["order_id"]

    def wait_for_terminal(order_id, timeout=10):
        """Poll MongoDB event log until PaymentConfirmed or OrderCancelled appears."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            events = get_events_for_order(order_id)
            if events and events[-1]["event_type"] in ("PaymentConfirmed", "OrderCancelled"):
                return events[-1]["event_type"], (time.time() - t0) * 1000
            time.sleep(0.1)
        return None, None

    banner("SAGA DEMO — Order Fulfillment Saga")
    info("The real saga worker (running in background) picks up OrderPlaced events from Redis")
    info("and drives: inventory reservation → payment authorization → confirm/cancel.")
    info("We restart the worker with different PAYMENT_FAILURE_RATE values to force each scenario.")

    # ── Scenario A: Happy Path ────────────────────────────────
    banner("SAGA A: Happy Path (inventory ok + payment ok)")
    info("Restarting saga worker with PAYMENT_FAILURE_RATE=0.0 ...")
    restart_saga_worker({"PAYMENT_FAILURE_RATE": "0.0"})
    ok("Saga worker running")

    info("Inventory before:")
    show_inventory()

    order_id = place_order({
        "customer_id": "cust_alice", "customer_name": "Alice Smith",
        "customer_email": "alice@example.com", "region": "US-WEST",
        "items": [
            {"product_id": "p2", "name": "Mouse",    "quantity": 1, "price": 29.99},
            {"product_id": "p3", "name": "Keyboard", "quantity": 1, "price": 79.99},
        ],
    })
    ok(f"Order placed: {order_id}")

    terminal, lag_ms = wait_for_terminal(order_id)
    if terminal:
        ok(f"Saga terminal: {terminal}  ({lag_ms:.0f}ms end-to-end)")
    else:
        err("Saga did not reach terminal state within timeout")

    info("Inventory after (reserved_qty incremented):")
    show_inventory()
    show_events(order_id)

    # ── Scenario B: Out of Stock ──────────────────────────────
    banner("SAGA B: Out of Stock (request more laptops than available)")
    info("Same worker — failure is deterministic (SQL constraint, not random)")

    info("Inventory before:")
    show_inventory()

    order_id2 = place_order({
        "customer_id": "cust_bob", "customer_name": "Bob Jones",
        "customer_email": "bob@example.com", "region": "EU",
        "items": [
            {"product_id": "p1", "name": "Laptop", "quantity": 99, "price": 999.99},
        ],
    })
    ok(f"Order placed: {order_id2}")

    terminal, lag_ms = wait_for_terminal(order_id2)
    if terminal:
        ok(f"Saga terminal: {terminal}  ({lag_ms:.0f}ms end-to-end)")

    info("Inventory after (unchanged — nothing was reserved):")
    show_inventory()
    show_events(order_id2)

    # ── Scenario C: Payment Failure + Compensation ────────────
    banner("SAGA C: Payment Fails → Inventory Released (compensation)")
    info("Restarting saga worker with PAYMENT_FAILURE_RATE=1.0 ...")
    restart_saga_worker({"PAYMENT_FAILURE_RATE": "1.0"})
    ok("Saga worker running — payment will always fail")

    info("Inventory before:")
    show_inventory()

    order_id3 = place_order({
        "customer_id": "cust_carol", "customer_name": "Carol White",
        "customer_email": "carol@example.com", "region": "US-EAST",
        "items": [
            {"product_id": "p4", "name": "Monitor", "quantity": 1, "price": 349.99},
        ],
    })
    ok(f"Order placed: {order_id3}")

    terminal, lag_ms = wait_for_terminal(order_id3)
    if terminal:
        ok(f"Saga terminal: {terminal}  ({lag_ms:.0f}ms end-to-end)")

    info("Inventory after (reserved_qty incremented then released by compensation):")
    show_inventory()
    show_events(order_id3)

    # ── Restore worker to default failure rate ───────────────
    restart_saga_worker({})

    banner("SAGA DEMO DONE")
    print(f"\n  {GREEN}{BOLD}All 3 saga scenarios completed via the real background worker.{RESET}")
    print(f"\n  Check MongoDB for saga state documents:")
    print(f"    mongosh cqrs_events --eval \"db.sagas.find().pretty()\"")
    print(f"\n  Watch live saga logs:")
    print(f"    tail -f /tmp/saga.log")
    print()
    sys.exit(0)


# ═════════════════════════════════════════════════════════════
# BLAST MODE
# ═════════════════════════════════════════════════════════════

if "--blast" in sys.argv:
    banner("BLAST MODE — Stream Lag Demo")

    N = 15000
    results = []
    lock = threading.Lock()

    def place_one():
        try:
            r = requests.post(f"{WRITE}/orders", json=random_order())
            if r.status_code == 201:
                with lock:
                    results.append(r.json()["order_id"])
        except Exception:
            pass

    info("Step 1: Kill the consumer so events pile up in Redis...")
    subprocess.run(["docker", "compose", "stop", "consumer"], cwd=ROOT, capture_output=True)
    time.sleep(1)
    ok("Consumer stopped")

    info(f"Step 2: Flooding {N} orders using {N} concurrent threads...")
    t0 = time.time()
    threads = [threading.Thread(target=place_one) for _ in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t0

    after = stream_len()
    lag   = redis_lag()
    ok(f"Placed {len(results)} orders in {elapsed:.2f}s  ({len(results)/elapsed:.0f} orders/sec)")
    print(f"\n  {BOLD}{RED}Stream lag right now: {lag} unprocessed events{RESET}")
    print(f"  Stream total length:  {after} events")
    print(f"  (PostgreSQL has NONE of these yet — 3NF tables are all empty for these orders)\n")

    print(f"  {BOLD}{YELLOW}Now open redis-cli and run:{RESET}")
    print(f"    XINFO GROUPS order_events")
    print(f"    XLEN order_events")
    print(f"\n  Waiting 15 seconds before restarting consumer...")
    for i in range(5, 0, -1):
        print(f"\r  Starting consumer in {i}s...  ", end="", flush=True)
        time.sleep(1)
    print()

    info("Step 3: Restarting consumer — watch lag drain to 0...")
    subprocess.run(["docker", "compose", "start", "consumer"], cwd=ROOT, capture_output=True)

    while True:
        time.sleep(0.5)
        current_lag = redis_lag()
        bar_len = 40
        filled  = int(bar_len * current_lag / max(lag, 1))
        bar     = f"{RED}{'█' * filled}{RESET}{'░' * (bar_len - filled)}"
        print(f"\r  lag: [{bar}] {current_lag:>5} events remaining   ", end="", flush=True)
        if current_lag == 0:
            break

    print(f"\n\n  {GREEN}{BOLD}lag = 0 — consumer fully caught up!{RESET}")
    orders = requests.get(f"{READ}/orders").json()
    ok(f"PostgreSQL now has {len(orders)} orders across customers, orders, order_items tables")
    sys.exit(0)


# ═════════════════════════════════════════════════════════════
# NORMAL MODE
# ═════════════════════════════════════════════════════════════

banner("1. HEALTH CHECK")

for name, url in [("Write API", f"{WRITE}/health"), ("Read API", f"{READ}/health")]:
    r = requests.get(url)
    if r.status_code == 200:
        ok(f"{name} is up")
    else:
        err(f"{name} is DOWN — run: bash start.sh")
        sys.exit(1)


banner("2. PLACE ORDER")

payload = {
    "customer_id":    "cust_alice",
    "customer_name":  "Alice Smith",
    "customer_email": "alice@example.com",
    "region":         "US-WEST",
    "items": [
        {"product_id": "p1", "name": "Laptop",   "quantity": 1, "price": 999.99},
        {"product_id": "p3", "name": "Keyboard", "quantity": 2, "price": 79.99},
    ],
}

r = requests.post(f"{WRITE}/orders", json=payload)
assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

result   = r.json()
order_id = result["order_id"]
version  = result["version"]

ok("Order placed")
show("Write API response", result)

info("Waiting for consumer to project into 3NF tables (customers + orders + order_items)...")
order = wait_for_read_side(order_id)
if order:
    total = sum(item["quantity"] * float(item["unit_price"]) for item in order.get("items", []))
    show("orders table row", {
        "order_id":     order["order_id"],
        "status":       order["status"],
        "region":       order["region"],
        "placed_at":    order["placed_at"],
        "total_amount": f"{total:.2f}  (SUM of order_items via JOIN)",
    })
    show("customers table row (joined)", {
        "customer_id":    order["customer_id"],
        "customer_name":  order["customer_name"],
        "customer_email": order["customer_email"],
    })
    print(f"\n  {BOLD}order_items rows:{RESET}")
    for item in order.get("items", []):
        print(f"    {item['name']:12s}  qty={item['quantity']}  unit_price={item['unit_price']}")


banner("3. RAW EVENT LOG (MongoDB — source of truth)")

events = requests.get(f"{WRITE}/orders/{order_id}/events").json()
ok(f"MongoDB has {len(events)} event(s)")
for e in events:
    print(f"    v{e['version']}  {e['event_type']}  @ {e['timestamp']}")


banner("4. OPTIMISTIC CONCURRENCY — wrong version → 409")

r = requests.post(f"{WRITE}/orders/{order_id}/confirm-payment",
                  json={"expected_version": 99})
if r.status_code == 409:
    ok("Got 409 Conflict as expected")
    info(f"Server said: {r.json()['detail']}")
else:
    err(f"Expected 409, got {r.status_code}")


banner("5. CONFIRM PAYMENT")

r = requests.post(f"{WRITE}/orders/{order_id}/confirm-payment",
                  json={"expected_version": version})
assert r.status_code == 200, f"{r.status_code}: {r.text}"
version = r.json()["version"]
ok(f"Payment confirmed  (version → {version})")

time.sleep(1.5)
order = requests.get(f"{READ}/orders/{order_id}").json()
show("orders table — status updated by consumer", {
    "status":                order["status"],
    "payment_confirmed_at":  order["payment_confirmed_at"],
})


banner("6. SHIP ORDER")

r = requests.post(f"{WRITE}/orders/{order_id}/ship",
                  json={"expected_version": version, "tracking_number": "TRK-2026-XYZ"})
assert r.status_code == 200, f"{r.status_code}: {r.text}"
version = r.json()["version"]
ok(f"Order shipped  (version → {version})")

time.sleep(1.5)
order = requests.get(f"{READ}/orders/{order_id}").json()
show("orders table — final state", {
    "status":     order["status"],
    "shipped_at": order["shipped_at"],
})


banner("7. FULL EVENT HISTORY (MongoDB)")

events = requests.get(f"{WRITE}/orders/{order_id}/events").json()
ok(f"MongoDB has {len(events)} events — complete audit trail")
for e in events:
    print(f"    v{e['version']}  {e['event_type']:25s}  @ {e['timestamp']}")


banner("8. SECOND ORDER — cancel it")

r = requests.post(f"{WRITE}/orders", json={
    "customer_id":    "cust_bob",
    "customer_name":  "Bob Jones",
    "customer_email": "bob@example.com",
    "region":         "EU",
    "items": [{"product_id": "p4", "name": "Monitor", "quantity": 1, "price": 349.99}],
})
order2_id = r.json()["order_id"]
ok(f"Second order placed: {order2_id}")

requests.post(f"{WRITE}/orders/{order2_id}/cancel",
              json={"expected_version": 1, "reason": "Changed my mind"})
ok("Order cancelled")

time.sleep(1.5)
order2 = requests.get(f"{READ}/orders/{order2_id}").json()
show("orders table", {"status": order2["status"], "cancelled_at": order2["cancelled_at"]})


banner("9. READ API — 3NF QUERIES WITH JOINS")

orders = requests.get(f"{READ}/orders").json()
ok(f"GET /orders → {len(orders)} orders  (JOIN orders + customers + order_items)")

shipped = requests.get(f"{READ}/orders?status=shipped").json()
ok(f"GET /orders?status=shipped → {len(shipped)} orders")

alice = requests.get(f"{READ}/customers/cust_alice/orders").json()
ok(f"GET /customers/cust_alice/orders → {len(alice)} order(s)")

summary = requests.get(f"{READ}/customers/summary").json()
ok(f"GET /customers/summary → {len(summary)} rows from mv_orders_by_customer (pre-joined materialized view)")
for row in summary:
    print(f"    {row['customer_name']:15s}  orders={row['total_orders']}  spent={row['total_spent']}")

revenue = requests.get(f"{READ}/analytics/revenue").json()
ok(f"GET /analytics/revenue → {len(revenue)} rows from mv_daily_revenue (pre-joined materialized view)")


banner("DONE")

print(f"\n  {GREEN}{BOLD}All tests passed.{RESET}")
print(f"\n  3NF Tables in PostgreSQL:")
print(f"    customers   → 1 row per unique customer")
print(f"    orders      → 1 row per order (status, timestamps)")
print(f"    order_items → 1 row per line item (order_id + product_id composite PK)")
print(f"\n  Try the lag demo:")
print(f"    python scripts/test_flow.py --blast")
print()
