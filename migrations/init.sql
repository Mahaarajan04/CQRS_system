-- ─────────────────────────────────────────────────────────────
-- 3NF Read Model
-- Every non-key attribute depends on the key, the whole key,
-- and nothing but the key.
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS customers (
    customer_id    VARCHAR(36)  PRIMARY KEY,
    customer_name  VARCHAR(255) NOT NULL,
    customer_email VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id             VARCHAR(36) PRIMARY KEY,
    customer_id          VARCHAR(36) NOT NULL REFERENCES customers(customer_id),
    status               VARCHAR(50) NOT NULL DEFAULT 'placed',
    region               VARCHAR(100),
    placed_at            TIMESTAMPTZ,
    payment_confirmed_at TIMESTAMPTZ,
    shipped_at           TIMESTAMPTZ,
    cancelled_at         TIMESTAMPTZ,
    version              INTEGER NOT NULL DEFAULT 0
);

-- unit_price is the price AT ORDER TIME (snapshot), not current product price.
-- (order_id, product_id) → quantity, name, unit_price — no transitive deps.
CREATE TABLE IF NOT EXISTS order_items (
    order_id   VARCHAR(36)    NOT NULL REFERENCES orders(order_id),
    product_id VARCHAR(36)    NOT NULL,
    name       VARCHAR(255)   NOT NULL,
    quantity   INTEGER        NOT NULL,
    unit_price NUMERIC(10,2)  NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

-- Idempotency: never project the same event twice
CREATE TABLE IF NOT EXISTS processed_events (
    event_id     VARCHAR(36) PRIMARY KEY,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_orders_customer  ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_placed_at ON orders(placed_at);
CREATE INDEX IF NOT EXISTS idx_orders_region    ON orders(region);
CREATE INDEX IF NOT EXISTS idx_items_order      ON order_items(order_id);

-- ── Materialized views (pre-joined for fast reads) ────────────

-- Customer summary: JOINs customers + orders + order_items
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_orders_by_customer AS
SELECT
    c.customer_id,
    c.customer_name,
    c.customer_email,
    COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'shipped')      AS total_orders,
    COALESCE(SUM(oi.quantity * oi.unit_price)
        FILTER (WHERE o.status = 'shipped'), 0)                         AS total_spent,
    COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'cancelled')    AS cancelled_orders,
    MAX(o.placed_at)                                                    AS last_order_at
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
LEFT JOIN order_items oi ON o.order_id = oi.order_id
GROUP BY c.customer_id, c.customer_name, c.customer_email;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_customer_id
    ON mv_orders_by_customer(customer_id);

-- Daily revenue: JOINs orders + order_items
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_revenue AS
SELECT
    DATE(o.placed_at)                                                       AS date,
    o.region,
    COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'shipped')         AS total_orders,
    COALESCE(SUM(oi.quantity * oi.unit_price)
        FILTER (WHERE o.status = 'shipped'), 0)                            AS revenue,
    COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'cancelled')       AS cancelled_orders
FROM orders o
LEFT JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.placed_at IS NOT NULL
GROUP BY DATE(o.placed_at), o.region
ORDER BY date DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_daily_date_region
    ON mv_daily_revenue(date, region);

-- ── Inventory ─────────────────────────────────────────────────
-- Managed directly by the saga (not event-sourced).
-- available = total_qty - reserved_qty (computed at query time).

CREATE TABLE IF NOT EXISTS inventory (
    product_id   VARCHAR(36)  PRIMARY KEY,
    name         VARCHAR(255) NOT NULL,
    total_qty    INTEGER      NOT NULL CHECK (total_qty >= 0),
    reserved_qty INTEGER      NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0)
);

INSERT INTO inventory (product_id, name, total_qty) VALUES
    ('p1', 'Laptop',   1000),
    ('p2', 'Mouse',    5000),
    ('p3', 'Keyboard', 3000),
    ('p4', 'Monitor',  2000)
ON CONFLICT DO NOTHING;
