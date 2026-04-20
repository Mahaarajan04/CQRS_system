import os
from datetime import date
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")

app = FastAPI(title="CQRS Read API — Query Side")


def get_conn():
    return psycopg2.connect(POSTGRES_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Order queries ─────────────────────────────────────────────────────────────

@app.get("/orders")
def list_orders(
    status: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = Query(50, le=500),
):
    conn = get_conn()
    with conn.cursor() as cur:
        query  = "SELECT * FROM orders WHERE 1=1"
        params = []
        if status:
            query += " AND status = %s"
            params.append(status)
        if region:
            query += " AND region = %s"
            params.append(region)
        query += " ORDER BY placed_at DESC LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        return cur.fetchall()


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


# ── Customer queries ──────────────────────────────────────────────────────────

@app.get("/customers/summary")
def customer_summaries():
    """Served from the mv_orders_by_customer materialized view — sub-millisecond."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM mv_orders_by_customer ORDER BY total_spent DESC")
        return cur.fetchall()


@app.get("/customers/{customer_id}/orders")
def customer_orders(customer_id: str):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM orders WHERE customer_id = %s ORDER BY placed_at DESC",
            (customer_id,),
        )
        return cur.fetchall()


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/analytics/revenue")
def daily_revenue(
    start_date: Optional[date] = None,
    end_date:   Optional[date] = None,
    region:     Optional[str]  = None,
):
    """
    Served from the mv_daily_revenue materialized view.
    Answers 'total revenue by region this week' in milliseconds regardless
    of how many orders exist — this is the whole point of the read model.
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
