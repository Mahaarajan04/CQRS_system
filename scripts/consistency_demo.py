"""
Eventual consistency demo.

Shows the lag window between a write landing in MongoDB (via write API)
and it becoming visible on the read side (PostgreSQL via read API).

Run with CONSISTENCY_DELAY set on the consumer to make the gap visible:
  # In docker-compose.yml set CONSISTENCY_DELAY=2 on the consumer service, then:
  python consistency_demo.py
"""

import time
import requests

WRITE_API = "http://localhost:8001"
READ_API  = "http://localhost:8002"


def demo():
    print("=== Eventual Consistency Demo ===\n")

    order_payload = {
        "customer_id":    "cust_demo",
        "customer_name":  "Demo User",
        "customer_email": "demo@example.com",
        "region":         "US-WEST",
        "items": [
            {"product_id": "p1", "name": "Laptop", "quantity": 1, "price": 999.99}
        ],
    }

    t0   = time.time()
    resp = requests.post(f"{WRITE_API}/orders", json=order_payload)
    resp.raise_for_status()
    order_id = resp.json()["order_id"]
    print(f"[t=0ms]  Write accepted — order_id={order_id}")
    print(f"         Event stored in MongoDB + published to Redis Streams\n")

    lag_ms = None
    poll   = 0
    while True:
        elapsed_ms = (time.time() - t0) * 1000
        poll      += 1
        r = requests.get(f"{READ_API}/orders/{order_id}")

        if r.status_code == 200:
            lag_ms = elapsed_ms
            print(f"[t={elapsed_ms:6.0f}ms] READ SIDE CONSISTENT  (poll #{poll})")
            break
        else:
            print(f"[t={elapsed_ms:6.0f}ms] Stale — order not visible on read side yet (poll #{poll})")
            time.sleep(0.05)

    print(f"\n>>> Consistency lag: {lag_ms:.0f}ms")
    print("    During this window a user who placed an order and immediately")
    print("    refreshed their order list would have seen no order.")


if __name__ == "__main__":
    demo()
