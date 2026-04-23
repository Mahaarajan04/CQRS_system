import os
import json
from datetime import date
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from cache import OrderCache

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")

app = FastAPI(title="CQRS Read API — Query Side")
cache = OrderCache()


def get_conn():
    return psycopg2.connect(POSTGRES_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Orders ────────────────────────────────────────────────────

@app.get("/orders")
def list_orders(
    status: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = Query(50, le=500),
):
    """
    JOIN orders → customers → order_items.
    Total is computed via SUM on order_items — no denormalized total_amount column.
    """
    conn = get_conn()
    with conn.cursor() as cur:
        query = """
            SELECT
                o.order_id,
                o.status,
                o.region,
                o.placed_at,
                o.payment_confirmed_at,
                o.shipped_at,
                o.cancelled_at,
                c.customer_id,
                c.customer_name,
                c.customer_email,
                COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS total_amount
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN order_items oi ON o.order_id = oi.order_id
            WHERE 1=1
        """
        params = []
        if status:
            query += " AND o.status = %s"
            params.append(status)
        if region:
            query += " AND o.region = %s"
            params.append(region)
        query += " GROUP BY o.order_id, c.customer_id ORDER BY o.placed_at DESC LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        return cur.fetchall()


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    """
    Cache-first read: Redis HIT short-circuits PG. On MISS we fall through to
    PG and populate the cache so the next reader hits (read-through).
    404s are not cached — avoids poisoning the cache during projection lag.
    """
    cached = cache.get_order(order_id)
    if cached is not None:
        return JSONResponse(content=cached, headers={"X-Cache": "HIT"})

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                o.order_id,
                o.status,
                o.region,
                o.placed_at,
                o.payment_confirmed_at,
                o.shipped_at,
                o.cancelled_at,
                o.version,
                c.customer_id,
                c.customer_name,
                c.customer_email
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_id = %s
            """,
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        cur.execute(
            "SELECT product_id, name, quantity, unit_price FROM order_items WHERE order_id = %s",
            (order_id,),
        )
        items = cur.fetchall()

    doc = {**order, "items": items}
    cache.set_order(order_id, doc)
    return JSONResponse(
        content=json.loads(json.dumps(doc, default=str)),
        headers={"X-Cache": "MISS"},
    )


# ── Customers ─────────────────────────────────────────────────

@app.get("/customers/summary")
def customer_summaries():
    """
    Served from mv_orders_by_customer — a pre-joined materialized view.
    This is why CQRS + materialized views exist: the JOIN already happened
    at projection time, so this query is instant at any scale.
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM mv_orders_by_customer ORDER BY total_spent DESC")
        return cur.fetchall()


@app.get("/customers/{customer_id}/orders")
def customer_orders(customer_id: str):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                o.order_id,
                o.status,
                o.region,
                o.placed_at,
                COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS total_amount
            FROM orders o
            LEFT JOIN order_items oi ON o.order_id = oi.order_id
            WHERE o.customer_id = %s
            GROUP BY o.order_id
            ORDER BY o.placed_at DESC
            """,
            (customer_id,),
        )
        return cur.fetchall()


# ── Analytics ─────────────────────────────────────────────────

@app.get("/analytics/revenue")
def daily_revenue(
    start_date: Optional[date] = None,
    end_date:   Optional[date] = None,
    region:     Optional[str]  = None,
):
    """
    Served from mv_daily_revenue — pre-aggregated JOIN of orders + order_items.
    Answers 'revenue by region this week' in milliseconds regardless of data size.
    """
    conn = get_conn()
    with conn.cursor() as cur:
        query  = "SELECT * FROM mv_daily_revenue WHERE 1=1"
        params = []
        if start_date:
            query += " AND date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND date <= %s"
            params.append(end_date)
        if region:
            query += " AND region = %s"
            params.append(region)
        query += " ORDER BY date DESC"
        cur.execute(query, params)
        return cur.fetchall()


@app.get("/health")
def health():
    return {"status": "ok"}
