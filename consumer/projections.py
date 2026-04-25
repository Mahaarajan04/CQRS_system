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


def project_event(conn, event: dict, silent: bool = False):
    """
    Apply one event to the 3NF PostgreSQL read model. Idempotent.
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
            # 1. Upsert customer (customer details live in their own table)
            cur.execute(
                """
                INSERT INTO customers (customer_id, customer_name, customer_email)
                VALUES (%s, %s, %s)
                ON CONFLICT (customer_id) DO UPDATE
                    SET customer_name  = EXCLUDED.customer_name,
                        customer_email = EXCLUDED.customer_email
                """,
                (payload["customer_id"], payload["customer_name"], payload["customer_email"]),
            )

            # 2. Insert order (FK → customers)
            cur.execute(
                """
                INSERT INTO orders (order_id, customer_id, status, region, placed_at, version)
                VALUES (%s, %s, 'placed', %s, %s, 1)
                ON CONFLICT (order_id) DO NOTHING
                """,
                (order_id, payload["customer_id"], payload["region"], ts),
            )

            # 3. Insert one row per item (composite PK: order_id + product_id)
            for item in payload["items"]:
                cur.execute(
                    """
                    INSERT INTO order_items (order_id, product_id, name, quantity, unit_price)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (order_id, product_id) DO NOTHING
                    """,
                    (order_id, item["product_id"], item["name"], item["quantity"], item["price"]),
                )

        elif event_type == "PaymentConfirmed":
            cur.execute(
                """
                UPDATE orders
                SET status = 'payment_confirmed',
                    payment_confirmed_at = %s,
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
                    version = version + 1
                WHERE order_id = %s
                """,
                (ts, order_id),
            )

        elif event_type == "InventoryReserved":
            cur.execute(
                "UPDATE orders SET status = 'inventory_reserved', version = version + 1 WHERE order_id = %s",
                (order_id,),
            )

        elif event_type == "InventoryReserveFailed":
            cur.execute(
                "UPDATE orders SET status = 'inventory_failed', version = version + 1 WHERE order_id = %s",
                (order_id,),
            )

        elif event_type == "PaymentAuthorized":
            cur.execute(
                "UPDATE orders SET status = 'payment_authorized', version = version + 1 WHERE order_id = %s",
                (order_id,),
            )

        elif event_type == "PaymentAuthorizeFailed":
            cur.execute(
                "UPDATE orders SET status = 'payment_failed', version = version + 1 WHERE order_id = %s",
                (order_id,),
            )

        _mark_processed(cur, event_id)

    conn.commit()
    if not silent:
        print(f"[OK] {event_type} → order {order_id}")


def refresh_materialized_views(conn, silent: bool = False):
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_orders_by_customer")
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_revenue")
    conn.commit()
    if not silent:
        print("[REFRESH] Materialized views updated")
