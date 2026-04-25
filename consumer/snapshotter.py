"""
Snapshotter — periodically snapshots aggregate state into MongoDB.

Every SNAPSHOT_EVERY events for an order, we save its current PostgreSQL state
into MongoDB's `snapshots` collection. On replay, instead of re-running every
event from v1, we load the snapshot and only replay events after it.

For an order with 1000 events and SNAPSHOT_EVERY=10:
  Without snapshots: replay all 1000 events
  With snapshots:    load snapshot at v990, replay only 10 remaining events → 100x less work
"""

import os
import json
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URL       = os.getenv("MONGO_URL",      "mongodb://localhost:27017")
SNAPSHOT_EVERY  = int(os.getenv("SNAPSHOT_EVERY", "5"))  # snapshot every N events per order

_mongo      = MongoClient(MONGO_URL)
_snapshots  = _mongo["cqrs_events"]["snapshots"]


def maybe_snapshot(pg_conn, order_id: str, version: int) -> bool:
    """
    Take a snapshot of the order if version is a multiple of SNAPSHOT_EVERY.
    Returns True if a snapshot was taken.
    """
    if version % SNAPSHOT_EVERY != 0:
        return False

    state = _read_order_state(pg_conn, order_id)
    if not state:
        return False

    _snapshots.replace_one(
        {"aggregate_id": order_id},
        {
            "aggregate_id": order_id,
            "version":      version,
            "state":        state,
            "snapshot_at":  datetime.now(timezone.utc).isoformat(),
        },
        upsert=True,
    )
    return True


def _read_order_state(pg_conn, order_id: str) -> dict | None:
    """Read current order + items from PostgreSQL into a snapshot-able dict."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.order_id, o.customer_id, o.status, o.region,
                   o.placed_at, o.payment_confirmed_at, o.shipped_at, o.cancelled_at,
                   o.version,
                   c.customer_name, c.customer_email
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_id = %s
            """,
            (order_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        col = [d[0] for d in cur.description]
        order = dict(zip(col, row))

        cur.execute(
            "SELECT product_id, name, quantity, unit_price FROM order_items WHERE order_id = %s",
            (order_id,),
        )
        items_cols = [d[0] for d in cur.description]
        order["items"] = [dict(zip(items_cols, r)) for r in cur.fetchall()]

        # make timestamps JSON-serialisable
        for field in ("placed_at", "payment_confirmed_at", "shipped_at", "cancelled_at"):
            if order.get(field) is not None:
                order[field] = order[field].isoformat()

        for item in order["items"]:
            if hasattr(item.get("unit_price"), "to_eng_string"):
                item["unit_price"] = float(item["unit_price"])

    return order


def load_all_snapshots() -> dict:
    """
    Return a dict: {aggregate_id: {version, state}} for all snapshots.
    Used by replay.py to skip already-snapshotted events.
    """
    return {
        doc["aggregate_id"]: {"version": doc["version"], "state": doc["state"]}
        for doc in _snapshots.find({}, {"_id": 0})
    }


def count_snapshots() -> int:
    return _snapshots.count_documents({})
