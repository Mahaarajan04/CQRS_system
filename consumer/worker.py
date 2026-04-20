"""
Consumer worker — reads events from Redis Streams, projects into PostgreSQL.

Consumer group ensures each event is processed by exactly one worker instance
even when multiple replicas run. On crash, unACKed messages are reclaimed and
reprocessed — idempotency in projections.py makes this safe.
"""

import os
import json
import time

from redis import Redis
from projections import get_conn, project_event, refresh_materialized_views

REDIS_URL          = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_NAME        = "order_events"
CONSUMER_GROUP     = "projector_group"
CONSUMER_NAME      = "projector_1"
CONSISTENCY_DELAY  = float(os.getenv("CONSISTENCY_DELAY", "0"))
VIEW_REFRESH_SECS  = 30

redis_client = Redis.from_url(REDIS_URL)


def setup_consumer_group():
    try:
        # id="0" means: start from the very beginning of the stream
        redis_client.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
        print(f"[SETUP] Created consumer group '{CONSUMER_GROUP}'")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            print(f"[SETUP] Consumer group '{CONSUMER_GROUP}' already exists")
        else:
            raise


def reclaim_pending():
    """Re-process any messages that were delivered but never ACKed (e.g. after a crash)."""
    pending = redis_client.xpending_range(STREAM_NAME, CONSUMER_GROUP, "-", "+", count=100)
    if pending:
        print(f"[RECLAIM] Found {len(pending)} unACKed messages — reprocessing")
    conn = get_conn()
    for entry in pending:
        msg_id = entry["message_id"]
        messages = redis_client.xrange(STREAM_NAME, min=msg_id, max=msg_id)
        for _, fields in messages:
            event = json.loads(fields[b"data"])
            project_event(conn, event)
            redis_client.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)


def run():
    setup_consumer_group()
    reclaim_pending()

    conn         = get_conn()
    last_refresh = time.time()

    print("[WORKER] Listening for events...")

    while True:
        messages = redis_client.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {STREAM_NAME: ">"},   # ">" means: only new, undelivered messages
            count=10,
            block=1000,           # block up to 1s waiting for messages
        )

        if messages:
            for _stream, events in messages:
                for msg_id, fields in events:
                    event = json.loads(fields[b"data"])

                    if CONSISTENCY_DELAY > 0:
                        print(f"[DEMO] Artificial lag: sleeping {CONSISTENCY_DELAY}s")
                        time.sleep(CONSISTENCY_DELAY)

                    project_event(conn, event)
                    redis_client.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)

        if time.time() - last_refresh > VIEW_REFRESH_SECS:
            refresh_materialized_views(conn)
            last_refresh = time.time()


if __name__ == "__main__":
    run()
