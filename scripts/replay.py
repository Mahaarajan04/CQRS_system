"""
Event Replay Script — rebuilds PostgreSQL read model from MongoDB event log.

Full replay:
  python replay.py

Point-in-time replay (rebuild state as of a past timestamp):
  python replay.py --until "2026-04-18T15:00:00+00:00"

This works because MongoDB is an append-only log — we never update or delete
events, so we can always reconstruct any past state by replaying up to a
given point in time. A normal UPDATE-based database cannot do this.
"""

import argparse
import os
import sys
import time

import psycopg2
from pymongo import MongoClient

MONGO_URL    = os.getenv("MONGO_URL",    "mongodb://localhost:27017")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")

# Allow importing projections from the consumer directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../consumer"))
from projections import project_event, refresh_materialized_views


def wipe_postgres(conn):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE orders, processed_events RESTART IDENTITY CASCADE")
    conn.commit()
    print("[WIPE] PostgreSQL read model cleared")


def replay(until_timestamp: str = None):
    mongo = MongoClient(MONGO_URL)
    db    = mongo["cqrs_events"]
    conn  = psycopg2.connect(POSTGRES_URL)

    query = {}
    if until_timestamp:
        query["timestamp"] = {"$lte": until_timestamp}
        print(f"[REPLAY] Point-in-time mode: replaying events up to {until_timestamp}")
    else:
        print("[REPLAY] Full replay mode")

    events = list(
        db["events"].find(query, projection={"_id": 0}).sort("timestamp", 1)
    )
    total = len(events)
    print(f"[REPLAY] {total} events to replay")

    wipe_postgres(conn)

    start = time.time()
    for i, event in enumerate(events, start=1):
        project_event(conn, event)
        if i % 1000 == 0:
            elapsed = time.time() - start
            rate    = i / elapsed
            print(f"[REPLAY] {i:,}/{total:,}  ({rate:,.0f} events/sec)")

    refresh_materialized_views(conn)

    elapsed = time.time() - start
    rate    = total / elapsed if elapsed > 0 else 0
    print(f"\n[DONE] Replayed {total:,} events in {elapsed:.2f}s  ({rate:,.0f} events/sec)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay events from MongoDB into PostgreSQL")
    parser.add_argument(
        "--until",
        metavar="TIMESTAMP",
        help="ISO 8601 timestamp — only replay events up to this point",
    )
    args = parser.parse_args()
    replay(until_timestamp=args.until)
