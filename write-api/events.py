import os
import uuid
import json
from datetime import datetime, timezone

from pymongo import MongoClient
from redis import Redis

MONGO_URL   = os.getenv("MONGO_URL",  "mongodb://localhost:27017")
REDIS_URL   = os.getenv("REDIS_URL",  "redis://localhost:6379")
STREAM_NAME = "order_events"

_mongo  = MongoClient(MONGO_URL)
_db     = _mongo["cqrs_events"]
events_collection = _db["events"]

_redis  = Redis.from_url(REDIS_URL)


class ConcurrencyError(Exception):
    pass


def append_event(
    event_type: str,
    aggregate_id: str,
    payload: dict,
    expected_version: int,
) -> dict:
    """
    Append an immutable event to MongoDB then publish it to Redis Streams.

    Optimistic concurrency: if the current version of the aggregate doesn't
    match expected_version, the write is rejected. This prevents two concurrent
    writers from producing conflicting state.
    """
    last = events_collection.find_one(
        {"aggregate_id": aggregate_id},
        sort=[("version", -1)],
    )
    current_version = last["version"] if last else 0

    if current_version != expected_version:
        raise ConcurrencyError(
            f"Concurrency conflict on {aggregate_id}: "
            f"expected version {expected_version}, found {current_version}"
        )

    event = {
        "event_id":       str(uuid.uuid4()),
        "event_type":     event_type,
        "aggregate_id":   aggregate_id,
        "aggregate_type": "Order",
        "version":        current_version + 1,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "payload":        payload,
    }

    events_collection.insert_one(event)
    event.pop("_id", None)

    _redis.xadd(STREAM_NAME, {"data": json.dumps(event)})

    return event


def get_events_for_order(order_id: str) -> list:
    return list(
        events_collection.find(
            {"aggregate_id": order_id},
            sort=[("version", 1)],
            projection={"_id": 0},
        )
    )
