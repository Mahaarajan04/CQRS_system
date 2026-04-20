-- Denormalized orders table — shaped for reads, not writes
CREATE TABLE IF NOT EXISTS orders (
    order_id        VARCHAR(36) PRIMARY KEY,
    customer_id     VARCHAR(36) NOT NULL,
    customer_name   VARCHAR(255),
    customer_email  VARCHAR(255),
    status          VARCHAR(50) NOT NULL DEFAULT 'placed',
    items           JSONB NOT NULL DEFAULT '[]',
    total_amount    DECIMAL(10,2) NOT NULL DEFAULT 0,
    region          VARCHAR(100),
    placed_at       TIMESTAMPTZ,
    payment_confirmed_at TIMESTAMPTZ,
    shipped_at      TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    last_updated_at TIMESTAMPTZ DEFAULT NOW(),
    version         INTEGER NOT NULL DEFAULT 0
);

-- Idempotency: never process the same event twice even on consumer crash/restart
CREATE TABLE IF NOT EXISTS processed_events (
    event_id     VARCHAR(36) PRIMARY KEY,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes tuned for expected query patterns
CREATE INDEX IF NOT EXISTS idx_orders_customer  ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_placed_at ON orders(placed_at);
CREATE INDEX IF NOT EXISTS idx_orders_region    ON orders(region);

-- Materialized view: per-customer order summary
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_orders_by_customer AS
SELECT
    customer_id,
    customer_name,
    customer_email,
    COUNT(*)                                            AS total_orders,
    SUM(total_amount)                                   AS total_spent,
    COUNT(*) FILTER (WHERE status = 'shipped')          AS completed_orders,
    COUNT(*) FILTER (WHERE status = 'cancelled')        AS cancelled_orders,
    MAX(placed_at)                                      AS last_order_at
FROM orders
GROUP BY customer_id, customer_name, customer_email;

-- Unique index required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_customer_id
    ON mv_orders_by_customer(customer_id);

-- Materialized view: daily revenue by region
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_revenue AS
SELECT
    DATE(placed_at)                                         AS date,
    region,
    COUNT(*)                                                AS total_orders,
    SUM(total_amount)                                       AS revenue,
    COUNT(*) FILTER (WHERE status = 'shipped')              AS fulfilled_orders,
    COUNT(*) FILTER (WHERE status = 'cancelled')            AS cancelled_orders
FROM orders
WHERE placed_at IS NOT NULL
GROUP BY DATE(placed_at), region
ORDER BY date DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_daily_date_region
    ON mv_daily_revenue(date, region);
