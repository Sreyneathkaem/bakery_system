-- ពងទាប្រៃបេកខេរី / Bakery Tracker — Postgres schema (for Supabase)
-- Run this once in the Supabase SQL Editor (Project > SQL Editor > New query)
-- before deploying the app.

CREATE TABLE IF NOT EXISTS materials (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL,
    stock_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_per_unit DOUBLE PRECISION NOT NULL DEFAULT 0,
    reorder_threshold DOUBLE PRECISION NOT NULL DEFAULT 0,
    supplier_name TEXT,
    supplier_contact TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock_transactions (
    id SERIAL PRIMARY KEY,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    change_qty DOUBLE PRECISION NOT NULL,
    reason TEXT NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    price DOUBLE PRECISION NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'other',  -- 'bread' | 'pastry' | 'cake' | 'drink' | 'other'
    active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product_ingredients (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    batch_qty DOUBLE PRECISION NOT NULL,
    yield_count DOUBLE PRECISION NOT NULL,
    quantity_per_unit DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    customer_name TEXT,
    customer_phone TEXT,
    customer_address TEXT,
    note TEXT,
    total_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_profit DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' or 'paid'
    created_by_email TEXT,               -- which logged-in person placed it
    ordered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price DOUBLE PRECISION NOT NULL,
    line_total DOUBLE PRECISION NOT NULL,
    line_cost DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    note TEXT,
    spent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);