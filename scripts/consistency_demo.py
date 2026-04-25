"""
Eventual Consistency Demo.

Shows the lag window between a write landing in MongoDB (via Write API)
and it becoming visible on the read side (PostgreSQL via Read API).

Run the system with --no-cache and a slow consumer so the gap is clearly
visible, then run this script:

    bash restart.sh --delay 2 --no-cache
    python scripts/consistency_demo.py           # N=5 orders
    python scripts/consistency_demo.py --n 20    # larger sample for stats

The --delay flag on restart.sh adds an artificial sleep in the consumer
(simulates a slow projection pipeline). Without cache, every GET /orders/{id}
goes straight to PostgreSQL — so you see the full lag window.

To see what the cache fixes:
    bash restart.sh --delay 2               # cache ON, same 2s consumer lag
    python scripts/consistency_demo.py      # all reads return in ~1ms (cache HIT)
"""

import argparse
import statistics
import time

import requests

WRITE_API = "http://localhost:8001"
READ_API  = "http://localhost:8002"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
CYAN   = "\033[96m"

ORDER_PAYLOAD = {
    "customer_id":    "cust_demo",
    "customer_name":  "Demo User",
    "customer_email": "demo@example.com",
    "region":         "US-WEST",
    "items": [
        {"product_id": "p1", "name": "Laptop", "quantity": 1, "price": 999.99}
    ],
}


def place_and_measure(label: str) -> dict:
    """Place one order and poll until visible on the read side. Returns timing info."""
    t0   = time.perf_counter()
    resp = requests.post(f"{WRITE_API}/orders", json=ORDER_PAYLOAD, timeout=5)
    resp.raise_for_status()
    order_id  = resp.json()["order_id"]
    write_ms  = (time.perf_counter() - t0) * 1000

    polls = 0
    lag_ms = None
    while True:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        polls += 1
        r = requests.get(f"{READ_API}/orders/{order_id}", timeout=5)
        cache_hdr = r.headers.get("x-cache", "—")

        if r.status_code == 200:
            lag_ms = elapsed_ms
            print(f"  [{elapsed_ms:7.0f}ms]  {GREEN}VISIBLE{RESET}  "
                  f"(poll #{polls}, X-Cache: {cache_hdr})  order={order_id[:8]}")
            break
        else:
            print(f"  [{elapsed_ms:7.0f}ms]  {RED}stale{RESET}    "
                  f"(poll #{polls})  order={order_id[:8]}")
            time.sleep(0.05)

    return {"order_id": order_id, "write_ms": write_ms, "lag_ms": lag_ms, "polls": polls}


def main():
    parser = argparse.ArgumentParser(description="Eventual consistency lag demo")
    parser.add_argument("--n", type=int, default=5,
                        help="Number of orders to place (default: 5)")
    args = parser.parse_args()
    N = args.n

    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}  EVENTUAL CONSISTENCY DEMO — {N} orders{RESET}")
    print(f"{BOLD}{'='*55}{RESET}\n")
    print(f"  Write path:  Write API → MongoDB → Redis Stream")
    print(f"  Read path:   Consumer → PostgreSQL ← Read API")
    print(f"  Cache:       {'DISABLED (DISABLE_CACHE set)' if _cache_disabled() else 'ENABLED (results will be ~1ms)'}")
    print()

    results = []
    for i in range(1, N + 1):
        print(f"{CYAN}Order {i}/{N}:{RESET}")
        r = place_and_measure(f"order-{i}")
        results.append(r)
        print()

    lags = [r["lag_ms"] for r in results]
    polls = [r["polls"] for r in results]

    lags_sorted = sorted(lags)
    p50 = statistics.median(lags_sorted)
    p95 = lags_sorted[int(len(lags_sorted) * 0.95)] if len(lags_sorted) >= 2 else lags_sorted[-1]
    p99 = lags_sorted[int(len(lags_sorted) * 0.99)] if len(lags_sorted) >= 2 else lags_sorted[-1]

    print(f"{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}  RESULTS  (N={N}){RESET}")
    print(f"{BOLD}{'='*55}{RESET}\n")
    print(f"  {'metric':<20} {'value':>10}")
    print(f"  {'-'*32}")
    print(f"  {'consistency lag ms':<20}")
    print(f"    {'p50':<18} {p50:>9.1f}")
    print(f"    {'p95':<18} {p95:>9.1f}")
    print(f"    {'p99':<18} {p99:>9.1f}")
    print(f"    {'min':<18} {min(lags):>9.1f}")
    print(f"    {'max':<18} {max(lags):>9.1f}")
    print(f"  {'avg polls/read':<20} {sum(polls)/len(polls):>9.1f}")
    print()
    print(f"  {YELLOW}What this means:{RESET}")
    print(f"  A user who placed an order and immediately refreshed")
    print(f"  would have seen no order for up to {max(lags):.0f}ms.")
    print(f"  During this window the read side is STALE — MongoDB has")
    print(f"  the event, but PostgreSQL has not projected it yet.")
    print()
    print(f"  {GREEN}The RYW cache fixes this:{RESET}")
    print(f"    bash restart.sh --delay 2   # same consumer lag, cache ON")
    print(f"    python scripts/consistency_demo.py")
    print(f"  → Every read returns in ~1ms (cache HIT, no PG needed).")
    print()


def _cache_disabled() -> bool:
    try:
        r = requests.get(f"{READ_API}/orders/nonexistent-probe-id", timeout=2)
        return r.headers.get("x-cache") is None
    except Exception:
        return False


if __name__ == "__main__":
    main()
