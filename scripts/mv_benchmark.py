"""
Materialized View + CQRS vs Traditional Database Benchmark.

Demonstrates three comparisons:

  1. READ: CQRS materialized view  vs  raw SQL JOIN  vs  MongoDB aggregation
  2. WRITE: CQRS event-log append  vs  direct PostgreSQL 3NF INSERT

Prerequisites:
  bash restart.sh
  python scripts/seed.py --orders 5000 --publish
  # wait ~30-60s for consumer to project all events, then:
  python scripts/mv_benchmark.py

Or with fewer orders for a quicker demo:
  python scripts/seed.py --orders 1000 --publish
  python scripts/mv_benchmark.py --write-n 100
"""

import argparse
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import psycopg2.extras
import requests
from pymongo import MongoClient

WRITE_API    = "http://localhost:8001"
READ_API     = "http://localhost:8002"
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")
MONGO_URL    = os.getenv("MONGO_URL",    "mongodb://localhost:27017")

BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

READ_RUNS   = 7   # run each read query this many times, take median
WARMUP_RUNS = 2   # discard first N runs (cache warm-up)


# ── helpers ───────────────────────────────────────────────────────────────────

def sep(title=""):
    line = "=" * 55
    if title:
        print(f"\n{BOLD}{line}{RESET}")
        print(f"{BOLD}  {title}{RESET}")
        print(f"{BOLD}{line}{RESET}\n")
    else:
        print(f"\n{BOLD}{line}{RESET}\n")


def pg_conn():
    return psycopg2.connect(POSTGRES_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def measure_ms(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def run_n(fn, n=READ_RUNS, warmup=WARMUP_RUNS) -> list:
    times = []
    for i in range(n):
        ms = measure_ms(fn)
        if i >= warmup:
            times.append(ms)
    return times


def pct(times, p):
    s = sorted(times)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


# ── check data ────────────────────────────────────────────────────────────────

def check_data():
    try:
        conn = pg_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM orders")
            n = cur.fetchone()["n"]
        conn.close()
    except Exception as e:
        print(f"Cannot connect to PostgreSQL: {e}")
        sys.exit(1)

    if n < 500:
        print(f"  Only {n} orders in PostgreSQL.")
        print("  Run: python scripts/seed.py --orders 5000 --publish")
        print("  Then wait ~60s for the consumer to project, and retry.")
        sys.exit(1)

    print(f"  {GREEN}Data check passed:{RESET} {n:,} orders in PostgreSQL read model")
    return n


# ── READ BENCHMARK ────────────────────────────────────────────────────────────

def bench_mv_api():
    """Via read API — uses mv_daily_revenue (pre-aggregated at projection time)."""
    requests.get(f"{READ_API}/analytics/revenue", timeout=10).raise_for_status()


def bench_raw_join():
    """Direct PostgreSQL query — same aggregation without the materialized view."""
    conn = pg_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                DATE(o.placed_at)                                              AS date,
                o.region,
                COUNT(DISTINCT o.order_id)                                     AS total_orders,
                COALESCE(SUM(oi.quantity * oi.unit_price), 0)                  AS revenue,
                COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'shipped') AS fulfilled_orders,
                COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'cancelled') AS cancelled_orders
            FROM orders o
            LEFT JOIN order_items oi ON o.order_id = oi.order_id
            WHERE o.placed_at IS NOT NULL
            GROUP BY DATE(o.placed_at), o.region
            ORDER BY date DESC
        """)
        cur.fetchall()
    conn.close()


def bench_mongo_agg():
    """MongoDB aggregation on the raw event log (write model)."""
    mongo = MongoClient(MONGO_URL)
    db = mongo["cqrs_events"]
    list(db["events"].aggregate([
        {"$match": {"event_type": "OrderPlaced"}},
        {"$unwind": "$payload.items"},
        {"$addFields": {
            "day": {"$substr": ["$timestamp", 0, 10]},
        }},
        {"$group": {
            "_id": {"date": "$day", "region": "$payload.region"},
            "total_orders": {"$addToSet": "$aggregate_id"},
            "revenue": {
                "$sum": {
                    "$multiply": [
                        {"$toDouble": "$payload.items.quantity"},
                        {"$toDouble": "$payload.items.price"}
                    ]
                }
            }
        }},
        {"$project": {
            "date": "$_id.date",
            "region": "$_id.region",
            "total_orders": {"$size": "$total_orders"},
            "revenue": 1,
        }},
        {"$sort": {"date": -1}},
    ]))
    mongo.close()


def run_read_benchmark(order_count):
    sep("READ BENCHMARK — Analytical Query (daily revenue by region)")
    print(f"  Dataset: {order_count:,} orders across ~30 days, 4 regions")
    print(f"  Each query run {READ_RUNS - WARMUP_RUNS} times after {WARMUP_RUNS} warm-up runs. Showing median / p95.\n")

    print(f"  {'Running materialized view query...':<45}", end="", flush=True)
    mv_times = run_n(bench_mv_api)
    mv_rows = len(requests.get(f"{READ_API}/analytics/revenue").json())
    print(f"done")

    print(f"  {'Running raw SQL JOIN query...':<45}", end="", flush=True)
    join_times = run_n(bench_raw_join)
    conn = pg_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS n FROM (
                SELECT DATE(placed_at), region FROM orders
                WHERE placed_at IS NOT NULL GROUP BY DATE(placed_at), region
            ) t
        """)
        join_rows = cur.fetchone()["n"]
    conn.close()
    print(f"done")

    print(f"  {'Running MongoDB aggregation...':<45}", end="", flush=True)
    mongo_times = run_n(bench_mongo_agg)
    print(f"done\n")

    mv_med   = statistics.median(mv_times)
    mv_p95   = pct(mv_times, 95)
    join_med = statistics.median(join_times)
    join_p95 = pct(join_times, 95)
    mg_med   = statistics.median(mongo_times)
    mg_p95   = pct(mongo_times, 95)

    print(f"  {'query':<40} {'median':>8}  {'p95':>8}  {'rows':>6}")
    print(f"  {'-'*66}")
    print(f"  {CYAN}{'CQRS — materialized view (mv_daily_revenue)':<40}{RESET} "
          f"{mv_med:>7.1f}ms  {mv_p95:>7.1f}ms  {mv_rows:>6}")
    print(f"  {'CQRS — raw SQL JOIN (no view)':<40} "
          f"{join_med:>7.1f}ms  {join_p95:>7.1f}ms  {join_rows:>6}")
    print(f"  {'MongoDB — aggregation on event log':<40} "
          f"{mg_med:>7.1f}ms  {mg_p95:>7.1f}ms  {'~':>6}")
    print()

    if mv_med > 0:
        join_speedup = join_med / mv_med
        mongo_speedup = mg_med / mv_med
        print(f"  {GREEN}Speedup:{RESET}")
        print(f"    MV is {join_speedup:.0f}x faster than raw SQL JOIN")
        print(f"    MV is {mongo_speedup:.0f}x faster than MongoDB aggregation pipeline")
        print()
        print(f"  {YELLOW}Why:{RESET}")
        print(f"    The materialized view pre-computes the GROUP BY at projection time.")
        print(f"    Raw JOIN scans all rows in orders + order_items at query time.")
        print(f"    MongoDB aggregates the event payload — no schema, no indexes,")
        print(f"    and must unwind nested items arrays event-by-event.")

    return {"mv_med": mv_med, "join_med": join_med, "mongo_med": mg_med}


# ── WRITE BENCHMARK ───────────────────────────────────────────────────────────

_PRODUCTS = [
    {"product_id": "p1", "name": "Laptop",   "price": 999.99},
    {"product_id": "p2", "name": "Mouse",    "price":  29.99},
    {"product_id": "p3", "name": "Keyboard", "price":  79.99},
]


def _random_order_payload():
    return {
        "customer_id":    f"cust_bench_{uuid.uuid4().hex[:6]}",
        "customer_name":  "Bench User",
        "customer_email": "bench@example.com",
        "region":         "Mumbai",
        "items": [{"product_id": "p2", "name": "Mouse", "quantity": 1, "price": 29.99}],
    }


def cqrs_write_one(_):
    """CQRS write: POST /orders → MongoDB append + Redis xadd."""
    t0 = time.perf_counter()
    r = requests.post(f"{WRITE_API}/orders", json=_random_order_payload(), timeout=10)
    r.raise_for_status()
    return (time.perf_counter() - t0) * 1000


def direct_pg_write_one(_):
    """Direct PostgreSQL 3NF write: INSERT into customers + orders + order_items."""
    order_id    = str(uuid.uuid4())
    customer_id = f"cust_bench_{uuid.uuid4().hex[:6]}"
    t0 = time.perf_counter()
    conn = psycopg2.connect(POSTGRES_URL)
    try:
        with conn.cursor() as cur:
            # customers table (ON CONFLICT in case customer_id reused)
            cur.execute(
                "INSERT INTO customers (customer_id, customer_name, customer_email) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (customer_id, "Bench User", "bench@example.com"),
            )
            # orders table
            cur.execute(
                "INSERT INTO orders (order_id, customer_id, status, region, placed_at, version) "
                "VALUES (%s, %s, 'placed', 'Mumbai', NOW(), 1)",
                (order_id, customer_id),
            )
            # order_items table
            cur.execute(
                "INSERT INTO order_items (order_id, product_id, name, quantity, unit_price) "
                "VALUES (%s, 'p2', 'Mouse', 1, 29.99)",
                (order_id,),
            )
        conn.commit()
    finally:
        conn.close()
    return (time.perf_counter() - t0) * 1000


def run_concurrent(fn, n, workers, label):
    print(f"  {label:<50}", end="", flush=True)
    latencies = []
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, i) for i in range(n)]
        for fut in as_completed(futures):
            try:
                latencies.append(fut.result())
            except Exception:
                pass
    elapsed = time.perf_counter() - t_start
    print(f"done")
    return latencies, elapsed


def run_write_benchmark(n, workers):
    sep(f"WRITE BENCHMARK — {n} orders, {workers} concurrent threads")
    print(f"  Comparing CQRS event-log append vs direct PostgreSQL 3NF INSERT.\n")

    cqrs_lats, cqrs_elapsed   = run_concurrent(cqrs_write_one,     n, workers, "CQRS:  POST /orders ...")
    pg_lats,   pg_elapsed     = run_concurrent(direct_pg_write_one, n, workers, "Direct PG: INSERT into 3 tables ...")

    def stats(lats, elapsed):
        ops = len(lats) / elapsed
        med = statistics.median(lats)
        p99 = pct(lats, 99)
        return ops, med, p99

    cqrs_ops, cqrs_med, cqrs_p99 = stats(cqrs_lats, cqrs_elapsed)
    pg_ops,   pg_med,   pg_p99   = stats(pg_lats,   pg_elapsed)

    print()
    print(f"  {'path':<42} {'ops/sec':>8}  {'p50':>8}  {'p99':>8}")
    print(f"  {'-'*70}")
    print(f"  {CYAN}{'CQRS: POST /orders (MongoDB append)':<42}{RESET} "
          f"{cqrs_ops:>7.0f}   {cqrs_med:>7.1f}ms  {cqrs_p99:>7.1f}ms")
    print(f"  {'Direct: PostgreSQL 3NF INSERT':<42} "
          f"{pg_ops:>7.0f}   {pg_med:>7.1f}ms  {pg_p99:>7.1f}ms")
    print()

    if pg_ops > 0:
        speedup = cqrs_ops / pg_ops
        if speedup >= 1:
            print(f"  {GREEN}CQRS write is {speedup:.1f}x higher throughput than direct PG INSERT{RESET}")
        else:
            print(f"  {YELLOW}Direct PG INSERT is {1/speedup:.1f}x higher throughput (small N — "
                  f"PG connection overhead dominates at this scale){RESET}")
    print()
    print(f"  {YELLOW}Why CQRS write scales better at high load:{RESET}")
    print(f"    MongoDB append: no FK checks, no schema enforcement, pure sequential write.")
    print(f"    Direct PG INSERT: 3 round-trips (customers + orders + order_items),")
    print(f"    FK constraint validation, index updates on every INSERT.")
    print(f"    Under contention (many concurrent writers) PG lock waits dominate.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MV + CQRS vs traditional benchmark")
    parser.add_argument("--write-n",       type=int, default=200, help="Orders for write benchmark (default 200)")
    parser.add_argument("--write-workers", type=int, default=20,  help="Concurrent threads for write benchmark (default 20)")
    parser.add_argument("--read-only",  action="store_true", help="Skip write benchmark")
    parser.add_argument("--write-only", action="store_true", help="Skip read benchmark")
    args = parser.parse_args()

    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}  CQRS BENCHMARK — MV + Write Throughput{RESET}")
    print(f"{BOLD}{'='*55}{RESET}")
    print()

    order_count = check_data()

    if not args.write_only:
        run_read_benchmark(order_count)

    if not args.read_only:
        run_write_benchmark(args.write_n, args.write_workers)

    sep()
    print(f"  {GREEN}Done.{RESET}")
    print(f"  Re-run read benchmark only:   python scripts/mv_benchmark.py --read-only")
    print(f"  Re-run write benchmark only:  python scripts/mv_benchmark.py --write-only")
    print(f"  Scale up write benchmark:     python scripts/mv_benchmark.py --write-n 500 --write-workers 50")
    print()


if __name__ == "__main__":
    main()
