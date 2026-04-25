"""
Seed script — generates realistic order events for benchmarking replay speed.

Usage:
  python seed.py --orders 1000            # insert 1k orders into MongoDB only
  python seed.py --orders 10000 --publish # insert + publish to Redis (consumer will project)
"""

import argparse
import json
import os
import random
import uuid
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient
from redis import Redis

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

REGIONS  = ["US-WEST", "US-EAST", "EU", "APAC"]
PRODUCTS = [
    {"product_id": "p1", "name": "Laptop",   "price": 999.99},
    {"product_id": "p2", "name": "Mouse",    "price": 29.99},
    {"product_id": "p3", "name": "Keyboard", "price": 79.99},
    {"product_id": "p4", "name": "Monitor",  "price": 349.99},
    {"product_id": "p5", "name": "Webcam",   "price": 89.99},
]

# Final status → sequence of events that produce it
STATUS_PATHS = {
    "placed":              ["OrderPlaced"],
    "payment_confirmed":   ["OrderPlaced", "PaymentConfirmed"],
    "shipped":             ["OrderPlaced", "PaymentConfirmed", "OrderShipped"],
    "cancelled_early":     ["OrderPlaced", "OrderCancelled"],
    "cancelled_after_pay": ["OrderPlaced", "PaymentConfirmed", "OrderCancelled"],
}
STATUS_WEIGHTS = [0.05, 0.10, 0.55, 0.15, 0.15]


def build_events_for_order(base_time: datetime) -> list:
    order_id    = str(uuid.uuid4())
    customer_id = f"cust_{random.randint(1, 200):03d}"
    region      = random.choice(REGIONS)
    path        = random.choices(list(STATUS_PATHS.values()), weights=STATUS_WEIGHTS)[0]
    chosen      = random.sample(PRODUCTS, k=random.randint(1, 3))
    items       = [
        {**p, "quantity": random.randint(1, 4)}
        for p in chosen
    ]
    total = sum(i["price"] * i["quantity"] for i in items)

    events = []
    for version, event_type in enumerate(path, start=1):
        ts = (base_time + timedelta(minutes=version * random.randint(3, 15))).isoformat()
        payload = {}
        if event_type == "OrderPlaced":
            payload = {
                "customer_id":    customer_id,
                "customer_name":  f"Customer {customer_id}",
                "customer_email": f"{customer_id}@example.com",
                "region":         region,
                "items":          items,
                "total_amount":   total,
            }
        events.append({
            "event_id":       str(uuid.uuid4()),
            "event_type":     event_type,
            "aggregate_id":   order_id,
            "aggregate_type": "Order",
            "version":        version,
            "timestamp":      ts,
            "payload":        payload,
        })
    return events


def seed(n_orders: int, publish: bool):
    mongo = MongoClient(MONGO_URL)
    db    = mongo["cqrs_events"]
    redis = Redis.from_url(REDIS_URL) if publish else None

    base = datetime.now(timezone.utc) - timedelta(days=30)
    all_events = []

    for i in range(n_orders):
        order_time = base + timedelta(seconds=random.randint(0, 30 * 24 * 3600))
        all_events.extend(build_events_for_order(order_time))

    # Sort by timestamp so replay produces consistent state
    all_events.sort(key=lambda e: e["timestamp"])

    db["events"].insert_many(all_events)
    # insert_many mutates dicts in-place with _id (ObjectId) — strip before publishing
    for e in all_events:
        e.pop("_id", None)
    print(f"[SEED] Inserted {len(all_events)} events for {n_orders} orders into MongoDB")

    if redis:
        pipe = redis.pipeline()
        for event in all_events:
            pipe.xadd("order_events", {"data": json.dumps(event)})
        pipe.execute()
        print(f"[SEED] Published {len(all_events)} events to Redis Streams")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders",  type=int, default=1000, help="Number of orders to generate")
    parser.add_argument("--publish", action="store_true",    help="Also publish to Redis Streams")
    args = parser.parse_args()
    seed(n_orders=args.orders, publish=args.publish)
