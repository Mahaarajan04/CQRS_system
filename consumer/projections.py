import os
import json
import psycopg2

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")


def get_conn():
    return psycopg2.connect(POSTGRES_URL)


def is_processed(conn, event_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM processed_events WHERE event_id = %s", (event_id,))
        return cur.fetchone() is not None


def _mark_processed(cur, event_id: str):
    cur.execute(
        "INSERT INTO processed_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (event_id,),
    )


def project_event(conn, event: dict):
    """
    Apply a single event to the PostgreSQL read model.

    Idempotent: checks processed_events before writing. Safe to call twice —
    the second call is a no-op. This protects against double-processing when
    the consumer crashes after updating Postgres but before ACKing Redis.
    """
    event_id   = event["event_id"]
    event_type = event["event_type"]
    order_id   = event["aggregate_id"]
    payload    = event["payload"]
    ts         = event["timestamp"]

    if is_processed(conn, event_id):
        print(f"[SKIP] Already processed {event_id} ({event_type})")
        return

    with conn.cursor() as cur:
        if event_type == "OrderPlaced":
            cur.execute(
                """
                INSERT INTO orders (
                    order_id, customer_id, customer_name, customer_email,
                    status, items, total_amount, region, placed_at, version
                ) VALUES (%s, %s, %s, %s, 'placed', %s, %s, %s, %s, 1)
                ON CONFLICT (order_id) DO NOTHING
                """,
                (
                    order_id,
                    payload["customer_id"],
                    payload["customer_name"],
                    payload["customer_email"],
                    json.dumps(payload["items"]),
                    payload["total_amount"],
                    payload["region"],
                    ts,
                ),
            )

        elif event_type == "PaymentConfirmed":
            cur.execute(
                """
                UPDATE orders
                SET status = 'payment_confirmed',
                    payment_confirmed_at = %s,
                    last_updated_at = NOW(),
                    version = version + 1
                WHERE order_id = %s
                """,
                (ts, order_id),
            )

        elif event_type == "OrderShipped":
            cur.execute(
                """
                UPDATE orders
                SET status = 'shipped',
                    shipped_at = %s,
                    last_updated_at = NOW(),
                    version = version + 1
                WHERE order_id = %s
                """,
                (ts, order_id),
            )

        elif event_type == "OrderCancelled":
            cur.execute(
                """
                UPDATE orders
                SET status = 'cancelled',
                    cancelled_at = %s,
                    last_updated_at = NOW(),
                    version = version + 1
                WHERE order_id = %s
                """,
                (ts, order_id),
            )

        _mark_processed(cur, event_id)

    conn.commit()
    print(f"[OK] {event_type} → order {order_id}")


def refresh_materialized_views(conn):
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_orders_by_customer")
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_revenue")
    conn.commit()
    print("[REFRESH] Materialized views updated")
