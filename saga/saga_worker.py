"""
Saga Worker — Redis Streams consumer for saga_group.

Reads from the same `order_events` stream as the projector but uses a
separate consumer group (`saga_group`) so it receives ALL events independently.
Only reacts to OrderPlaced; ACKs everything else immediately.
"""

import json
import time
import os

from redis import Redis
from orchestrator import SagaOrchestrator

REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_NAME    = "order_events"
CONSUMER_GROUP = "saga_group"
CONSUMER_NAME  = "saga_orchestrator_1"

redis_client  = Redis.from_url(REDIS_URL)
orchestrator  = SagaOrchestrator()


def setup_consumer_group():
    try:
        redis_client.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
        print(f"[SAGA] Consumer group '{CONSUMER_GROUP}' created")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            print(f"[SAGA] Consumer group '{CONSUMER_GROUP}' already exists")
        else:
            raise


def reclaim_pending():
    try:
        pending = redis_client.xpending_range(
            STREAM_NAME, CONSUMER_GROUP, min="-", max="+", count=100
        )
        if not pending:
            return
        print(f"[SAGA] Reclaiming {len(pending)} pending messages...")
        for entry in pending:
            msg_id = entry["message_id"]
            msgs = redis_client.xclaim(STREAM_NAME, CONSUMER_GROUP, CONSUMER_NAME, 0, [msg_id])
            for mid, fields in msgs:
                handle_message(mid, fields)
    except Exception as exc:
        print(f"[SAGA] reclaim_pending error: {exc}")


def handle_message(msg_id, fields):
    try:
        event = json.loads(fields[b"data"])
        if event["event_type"] == "OrderPlaced":
            handle_order_placed(event)
    except Exception as exc:
        print(f"[SAGA] Error handling message {msg_id}: {exc}")
    finally:
        redis_client.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)


def handle_order_placed(event):
    order_id = event["aggregate_id"]
    payload  = event["payload"]
    print(f"[SAGA] OrderPlaced → starting saga for order {order_id}")
    saga = orchestrator.create(order_id, payload)
    orchestrator.run(saga["saga_id"])


def run():
    setup_consumer_group()
    reclaim_pending()
    print("[SAGA] Worker started, listening for OrderPlaced events...")

    while True:
        try:
            messages = redis_client.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {STREAM_NAME: ">"},
                count=10,
                block=1000,
            )
            if messages:
                for _stream, events in messages:
                    for msg_id, fields in events:
                        handle_message(msg_id, fields)
        except Exception as exc:
            print(f"[SAGA] Worker error: {exc}")
            time.sleep(1)


if __name__ == "__main__":
    run()
