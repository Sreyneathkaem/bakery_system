import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

import psycopg2
import psycopg2.extras
import psycopg2.pool
import requests
from flask import Flask, render_template, request, redirect, url_for, session, g, flash, jsonify, send_file
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename

from translations import get_translator, DEFAULT_LANG

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

# Postgres connection string (Supabase: Project Settings > Database >
# Connection string > URI). Required.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Supabase project URL + anon key (Project Settings > API). Required for
# login — this app uses Supabase Auth for individual accounts.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

app = Flask(__name__)

# The session cookie is signed with this key — it's what actually keeps
# someone logged in (login_required only checks the session, it doesn't
# re-verify with Supabase on every request). A guessable fallback here means
# anyone can forge a valid "logged in" cookie, so we fail loudly at startup
# instead of silently running with a known-insecure default.
try:
    app.secret_key = os.environ["SECRET_KEY"]
except KeyError:
    raise RuntimeError(
        "SECRET_KEY environment variable is not set. Set it to a long random "
        "string before starting the app (see README.md / docker-compose.yml)."
    )

# Hard cap on request body size (covers the QR image upload) so a huge file
# can't be pushed straight into the database as base64.
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB

csrf = CSRFProtect(app)

limiter = Limiter(get_remote_address, app=app, storage_uri="memory://", default_limits=[])


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash("Your session expired — please try that again.", "error")
    return redirect(request.referrer or url_for("dashboard"))


@app.errorhandler(413)
def handle_too_large(e):
    flash("That file is too large (5MB max).", "error")
    return redirect(request.referrer or url_for("dashboard"))


# Shown on invoices. Set these in your hosting provider's environment settings.
SHOP_NAME = os.environ.get("SHOP_NAME", "ពងទាប្រៃបេកខេរី")
SHOP_PHONE = os.environ.get("SHOP_PHONE", "")

# Used to show cost in both USD and Riel. Update if the rate changes a lot.
KHR_PER_USD = float(os.environ.get("KHR_PER_USD", "4100"))

# Optional: set these to enable the "Send to shop Telegram" button on
# invoices. See README.md for how to create a bot and find a chat ID.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# The shop is in Phnom Penh — every "today", timestamp, and chart date in
# this app is based on this timezone, not wherever the server happens to be
# hosted (Render's servers run in UTC, which is 7 hours off).
APP_TZ = ZoneInfo("Asia/Phnom_Penh")
PG_TIMEZONE_NAME = "Asia/Phnom_Penh"

# Cache-busting for static files (CSS/JS). Browsers aggressively cache
# /static/css/main.css since the filename never changes between deploys —
# without this, people can keep seeing an old stylesheet (unstyled-looking
# pages, missing new layout) after an update until they manually hard-
# refresh. Appending ?v=<mtime> to the link changes the URL whenever the
# file's contents actually change, so the browser fetches a fresh copy
# automatically on the next visit after a deploy.
#
# The mtime is read fresh on every request (not cached at import time).
# A stat() call is cheap, and reading it once at startup meant editing
# main.css while the dev server was still running left ?v= pointing at
# the old value indefinitely — Flask's reloader watches .py files, not
# static/, so the stylesheet would keep looking stale until the process
# was manually restarted.
_MAIN_CSS_PATH = os.path.join(BASE_DIR, "static", "css", "main.css")


def _current_asset_version():
    try:
        return str(int(os.path.getmtime(_MAIN_CSS_PATH)))
    except OSError:
        return "1"


@app.context_processor
def inject_asset_version():
    return {"asset_version": _current_asset_version()}


def now_local():
    """Current time in the shop's own timezone (Asia/Phnom_Penh)."""
    return datetime.now(APP_TZ)


# ---------- Database helpers (Postgres / Supabase) ----------
#
# get_db() returns a thin wrapper so the rest of the app can keep using the
# same db.execute(query, params).fetchone()/.fetchall() style as before —
# only the placeholder syntax (? -> %s) and a couple of Postgres-specific
# behaviors (transaction rollback on error, no lastrowid) needed handling.
#
# Connections are pooled (not reopened per request). Supabase is on its own
# server, so every fresh connection pays a full TCP + TLS handshake — slow
# on its own, and painfully slow on Render's free tier where limited CPU
# makes the TLS negotiation take even longer. Opening one of those on every
# single click is what was causing the ~10 second lag. A small pool keeps a
# handful of connections open and reuses them across requests instead.

class PGConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        pg_query = query.replace("?", "%s")
        try:
            cur.execute(pg_query, params)
        except psycopg2.Error:
            # Postgres aborts the whole transaction on error until rolled
            # back (unlike SQLite) — without this, every later query on
            # this connection would fail with "current transaction is
            # aborted" even after the caller catches the exception.
            self._conn.rollback()
            raise
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


_db_pool = None


def _get_pool():
    """Creates the connection pool once, on first use. The session timezone
    is set via a connection-startup option (not a per-request query), so
    it's paid once when a physical connection is opened, not on every
    request that reuses it from the pool."""
    global _db_pool
    if _db_pool is None:
        _db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=8,
            dsn=DATABASE_URL,
            options=f"-c timezone={PG_TIMEZONE_NAME}",
        )
    return _db_pool


def get_db():
    if "db" not in g:
        g.db = PGConnection(_get_pool().getconn())
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            # A request that errored may leave the connection mid-
            # transaction; roll back before it goes back in the pool so the
            # next request to reuse it starts clean.
            if exception is not None:
                db.rollback()
            _get_pool().putconn(db._conn)
        except psycopg2.Error:
            # Connection is in a bad state (e.g. dropped by the network) —
            # close it outright rather than returning it to the pool, so a
            # fresh one gets opened next time instead of a broken one being
            # handed out again.
            try:
                db._conn.close()
            except Exception:
                pass


def init_db():
    """Creates tables if they don't exist yet. Safe to run every startup.
    Runs once at process startup, so a plain one-off connection is fine —
    only per-request connections needed pooling."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()
    cur.close()
    conn.close()


def migrate_db():
    """Add columns introduced after the initial release, for anyone
    upgrading an existing database without losing data. Safe to call every
    startup — each check is a no-op if the column already exists."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    def column_exists(table, column):
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
            (table, column),
        )
        return cur.fetchone() is not None

    if not column_exists("orders", "customer_phone"):
        cur.execute("ALTER TABLE orders ADD COLUMN customer_phone TEXT")
    if not column_exists("orders", "customer_address"):
        cur.execute("ALTER TABLE orders ADD COLUMN customer_address TEXT")
    if not column_exists("orders", "created_by_email"):
        cur.execute("ALTER TABLE orders ADD COLUMN created_by_email TEXT")
    if not column_exists("orders", "payment_status"):
        cur.execute("ALTER TABLE orders ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'pending'")
    if not column_exists("products", "category"):
        cur.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")
    if not column_exists("products", "available_qty"):
        cur.execute("ALTER TABLE products ADD COLUMN available_qty DOUBLE PRECISION NOT NULL DEFAULT 0")
    if not column_exists("products", "batch_yield"):
        cur.execute("ALTER TABLE products ADD COLUMN batch_yield DOUBLE PRECISION NOT NULL DEFAULT 1")
    if not column_exists("materials", "supplier_name"):
        cur.execute("ALTER TABLE materials ADD COLUMN supplier_name TEXT")
    if not column_exists("materials", "supplier_contact"):
        cur.execute("ALTER TABLE materials ADD COLUMN supplier_contact TEXT")
    if not column_exists("materials", "notes"):
        cur.execute("ALTER TABLE materials ADD COLUMN notes TEXT")

    conn.commit()
    cur.close()
    conn.close()


def usd_to_riel(usd):
    return usd * KHR_PER_USD


def riel_to_usd(riel):
    return riel / KHR_PER_USD if KHR_PER_USD else 0


# Recipe amounts can be typed in a different (but compatible) unit than how
# a material's stock is tracked — e.g. "0.3 g" of salt even though salt's
# stock is kept in kg, since that's a far more natural number to type than
# "0.0003 kg". Every other calculation (stock deduction, cost per unit)
# assumes quantity_per_unit is in the material's own stock unit, so
# whatever's entered gets converted back to that unit before it's stored.
UNIT_CONVERSION_FACTORS = {
    ("g", "kg"): 0.001,
    ("kg", "g"): 1000.0,
    ("ml", "l"): 0.001,
    ("l", "ml"): 1000.0,
    ("pcs", "dozen"): 1.0 / 12.0,
    ("dozen", "pcs"): 12.0,
}


def convert_amount_to_base_unit(amount, entered_unit, base_unit):
    if not entered_unit or entered_unit == base_unit:
        return amount
    factor = UNIT_CONVERSION_FACTORS.get((entered_unit, base_unit))
    return amount * factor if factor is not None else amount


QR_MIMETYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def get_qr_data():
    """Returns (image_bytes, mimetype) for the stored payment QR, or None.
    Stored in the database (not local disk) so it survives redeploys on
    hosting providers with no persistent disk, like Render's free tier."""
    db = get_db()
    row = db.execute("SELECT value FROM app_settings WHERE key = 'payment_qr'").fetchone()
    if not row or not row["value"]:
        return None
    try:
        payload = json.loads(row["value"])
        return base64.b64decode(payload["data"]), payload["mimetype"]
    except (ValueError, KeyError):
        return None


def save_qr_upload(file_storage):
    """Saves an uploaded QR code image into the database, replacing any
    previous one."""
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in QR_MIMETYPES:
        return False

    image_bytes = file_storage.read()
    payload = json.dumps({
        "data": base64.b64encode(image_bytes).decode("ascii"),
        "mimetype": QR_MIMETYPES[ext],
    })
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES ('payment_qr', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (payload,),
    )
    db.commit()
    return True


def clear_qr_upload():
    db = get_db()
    db.execute("DELETE FROM app_settings WHERE key = 'payment_qr'")
    db.commit()


def send_telegram_message(chat_id, text):
    """Sends a plain-text message via the Telegram Bot API using only the
    standard library (no extra dependency needed). Returns (ok, error_msg)."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "not_configured"
    if not chat_id:
        return False, "no_chat_id"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                return True, None
            return False, body.get("description", "unknown_error")
    except urllib.error.URLError as e:
        return False, str(e)


def send_telegram_photo(chat_id, image_bytes, caption=""):
    """Sends a PNG image via the Telegram Bot API sendPhoto endpoint, using a
    hand-built multipart/form-data body so no extra dependency is needed.
    Returns (ok, error_msg)."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "not_configured"
    if not chat_id:
        return False, "no_chat_id"

    boundary = "----BakeryTrackerBoundary7f3a9c"
    body = bytearray()

    def add_field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    add_field("chat_id", chat_id)
    if caption:
        add_field("caption", caption[:1024])  # Telegram caption length limit

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="photo"; filename="invoice.png"\r\n')
    body.extend(b"Content-Type: image/png\r\n\r\n")
    body.extend(image_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return True, None
            return False, result.get("description", "unknown_error")
    except urllib.error.URLError as e:
        return False, str(e)


def build_invoice_text(t, order, items):
    """Plain-text invoice used for Telegram, Share, and Copy."""
    ordered_at_str = order["ordered_at"].strftime("%Y-%m-%d %H:%M") if order["ordered_at"] else ""
    lines = [
        f"🍞 {SHOP_NAME}",
        f"{t('invoice_title_km')} / {t('invoice_title_en')}",
        f"{t('invoice_no_label')}: #{order['id']}",
        f"{t('invoice_date_label')}: {ordered_at_str}",
    ]
    if order["customer_name"]:
        lines.append(f"{t('buyer_name_label')}: {order['customer_name']}")
    if order["customer_phone"]:
        lines.append(f"{t('buyer_tel_label')}: {order['customer_phone']}")
    if order["customer_address"]:
        lines.append(f"{t('buyer_address_label')}: {order['customer_address']}")
    lines.append("")
    for idx, it in enumerate(items, start=1):
        lines.append(f"{idx}. {it['product_name']} x{it['quantity']} @ ${it['unit_price']:.2f} = ${it['line_total']:.2f}")
    lines.append("")
    lines.append(f"{t('invoice_total_label')}: ${order['total_amount']:.2f}")
    if order["note"]:
        lines.append(f"({order['note']})")
    lines.append("")
    lines.append(t("thank_you_msg"))
    if SHOP_PHONE:
        lines.append(SHOP_PHONE)
    return "\n".join(lines)


# ---------- Language ----------

@app.before_request
def set_language():
    if "lang" not in session:
        session["lang"] = DEFAULT_LANG
    g.t = get_translator(session["lang"])


@app.context_processor
def inject_translator():
    return {"t": g.t, "current_lang": session.get("lang", DEFAULT_LANG)}


@app.route("/lang/<code>")
def set_lang(code):
    if code in ("km", "en"):
        session["lang"] = code
    return redirect(request.referrer or url_for("dashboard"))


# ---------- Auth (Supabase Auth — individual accounts) ----------
#
# Create your accounts once in the Supabase dashboard: Authentication >
# Users > Add user (turn off "auto confirm" only if you want an email
# confirmation step; for a private 2-person tool, auto-confirmed is fine).
# There's no public sign-up page in this app on purpose — accounts are
# created by whoever administers the Supabase project, not by visitors.

# How long "keep me signed in" lasts before needing to log in again.
REMEMBER_ME_DAYS = 30

# Fixed product categories, in the display order they should appear on the
# Orders page. Anything uncategorized (or from before this feature existed)
# falls back to "other".
PRODUCT_CATEGORIES = ["bread", "pastry", "cake", "drink", "other"]


def supabase_auth_request(path, payload):
    """POSTs to Supabase's Auth (GoTrue) REST API. Returns (ok, data)."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return False, {"error": "not_configured"}
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1{path}",
            json=payload,
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            timeout=10,
        )
        data = resp.json()
        return resp.status_code == 200, data
    except requests.RequestException as e:
        return False, {"error": str(e)}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "1"

        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            flash(g.t("auth_not_configured"), "error")
            return render_template("login.html")

        ok, data = supabase_auth_request(
            "/token?grant_type=password", {"email": email, "password": password}
        )
        if ok and data.get("access_token"):
            session["user_email"] = data.get("user", {}).get("email", email)
            session["access_token"] = data["access_token"]
            # "Keep me signed in" (checked by default) makes the session a
            # long-lived cookie instead of one that disappears the moment
            # the browser/PWA is closed — this is the actual fix for
            # "login doesn't stay logged in".
            session.permanent = bool(remember)
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        flash(g.t("login_wrong"), "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    lang = session.get("lang", DEFAULT_LANG)
    session.clear()
    session["lang"] = lang
    return redirect(url_for("login"))


# ---------- Dashboard ----------

@app.route("/")
@login_required
def dashboard():
    db = get_db()

    low_stock = db.execute(
        "SELECT * FROM materials WHERE ROUND(stock_qty::numeric, 2) <= 0 ORDER BY stock_qty ASC"
    ).fetchall()

    today = now_local().strftime("%Y-%m-%d")
    today_orders = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) as total, "
        "COALESCE((SELECT SUM(quantity) FROM order_items JOIN orders o ON o.id = order_items.order_id WHERE date(o.ordered_at) = ?), 0) as units "
        "FROM orders WHERE date(ordered_at) = ?",
        (today, today),
    ).fetchone()

    week_ago = (now_local() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_orders = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) as total FROM orders WHERE date(ordered_at) >= ?",
        (week_ago,),
    ).fetchone()

    material_count = db.execute("SELECT COUNT(*) as c FROM materials").fetchone()["c"]
    product_count = db.execute("SELECT COUNT(*) as c FROM products WHERE active = 1").fetchone()["c"]

    recent_orders = db.execute(
        "SELECT * FROM orders ORDER BY ordered_at DESC LIMIT 6"
    ).fetchall()

    # Filterable chart: orders total per day, over the selected window.
    chart_days = int(request.args.get("chart_days", 7))
    chart_since = (now_local() - timedelta(days=chart_days)).strftime("%Y-%m-%d")
    chart_data = db.execute(
        "SELECT date(ordered_at) as day, SUM(total_amount) as total, COUNT(*) as order_count "
        "FROM orders WHERE date(ordered_at) >= ? GROUP BY day ORDER BY day ASC",
        (chart_since,),
    ).fetchall()

    return render_template(
        "dashboard.html",
        low_stock=low_stock,
        today_total=today_orders["total"],
        today_units=today_orders["units"],
        week_total=week_orders["total"],
        material_count=material_count,
        product_count=product_count,
        recent_orders=recent_orders,
        chart_days=chart_days,
        chart_data=chart_data,
    )


# ---------- Materials ----------

@app.route("/materials")
@login_required
def materials():
    db = get_db()
    rows = db.execute("SELECT * FROM materials ORDER BY name ASC").fetchall()
    history_by_material = {}
    for m in rows:
        history_by_material[m["id"]] = db.execute(
            "SELECT * FROM stock_transactions WHERE material_id = ? ORDER BY created_at DESC LIMIT 10",
            (m["id"],),
        ).fetchall()

    # Bake planner: every active product's recipe, plus current material
    # stock, sent down as JSON so the "ingredients needed" check can update
    # live in the browser as she types quantities — no round trip needed
    # until she actually taps "Confirm bake".
    active_products = db.execute(
        "SELECT * FROM products WHERE active = 1 ORDER BY name ASC"
    ).fetchall()
    bake_products = []
    for p in active_products:
        recipe = db.execute(
            "SELECT material_id, quantity_per_unit FROM product_ingredients WHERE product_id = ?",
            (p["id"],),
        ).fetchall()
        bake_products.append({
            "id": p["id"],
            "name": p["name"],
            "recipe": [{"material_id": r["material_id"], "qty_per_unit": r["quantity_per_unit"]} for r in recipe],
        })
    materials_for_planner = {
        m["id"]: {"name": m["name"], "unit": m["unit"], "stock_qty": m["stock_qty"]} for m in rows
    }

    return render_template(
        "materials.html",
        materials=rows,
        khr_per_usd=KHR_PER_USD,
        history_by_material=history_by_material,
        bake_products=bake_products,
        bake_products_json=json.dumps(bake_products),
        materials_planner_json=json.dumps(materials_for_planner),
    )


@app.route("/materials/add", methods=["POST"])
@login_required
def add_material():
    db = get_db()
    name = request.form.get("name", "").strip()
    unit = request.form.get("unit", "").strip()
    stock_qty = float(request.form.get("stock_qty") or 0)
    total_cost_usd_raw = request.form.get("total_cost_usd", "").strip()
    total_cost_riel_raw = request.form.get("total_cost_riel", "").strip()
    supplier_name = request.form.get("supplier_name", "").strip()

    if not name or not unit:
        flash(g.t("flash_name_unit_required"), "error")
        return redirect(url_for("materials"))

    # The price entered is the TOTAL cost of the starting-stock quantity —
    # e.g. "2 kg, $4 total" — and we divide it here to store cost-per-unit
    # ($2/kg), which is what recipe costing on the Products tab multiplies
    # against. If a price is given but there's no quantity to divide it by,
    # we can't know a per-unit cost yet, so we save $0 and say why, rather
    # than guessing.
    total_cost = None
    if total_cost_usd_raw:
        total_cost = float(total_cost_usd_raw)
    elif total_cost_riel_raw:
        total_cost = riel_to_usd(float(total_cost_riel_raw))

    cost_per_unit = 0.0
    if total_cost is not None:
        if stock_qty > 0:
            cost_per_unit = total_cost / stock_qty
        else:
            flash(g.t("flash_price_needs_qty"), "error")

    try:
        db.execute(
            "INSERT INTO materials (name, unit, stock_qty, cost_per_unit, reorder_threshold, supplier_name) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (name, unit, stock_qty, cost_per_unit, supplier_name or None),
        )
        db.commit()
        if stock_qty > 0:
            mat_id = db.execute("SELECT id FROM materials WHERE name = ?", (name,)).fetchone()["id"]
            note = request.form.get("note", "").strip() or "Initial stock"
            db.execute(
                "INSERT INTO stock_transactions (material_id, change_qty, reason, note) VALUES (?, ?, 'restock', ?)",
                (mat_id, stock_qty, note),
            )
            db.commit()
        flash(g.t("flash_material_added", name=name), "success")
    except psycopg2.IntegrityError:
        flash(g.t("flash_material_exists", name=name), "error")

    return redirect(url_for("materials"))


@app.route("/materials/<int:material_id>/edit", methods=["POST"])
@login_required
def edit_material(material_id):
    db = get_db()
    name = request.form.get("name", "").strip()
    unit = request.form.get("unit", "").strip()
    total_cost_usd_raw = request.form.get("total_cost_usd", "").strip()
    total_cost_riel_raw = request.form.get("total_cost_riel", "").strip()
    supplier_name = request.form.get("supplier_name", "").strip()
    supplier_contact = request.form.get("supplier_contact", "").strip()
    notes = request.form.get("notes", "").strip()

    if not name or not unit:
        flash(g.t("flash_name_unit_required"), "error")
        return redirect(url_for("materials"))

    existing = db.execute("SELECT stock_qty, cost_per_unit FROM materials WHERE id = ?", (material_id,)).fetchone()

    total_cost = None
    if total_cost_usd_raw:
        total_cost = float(total_cost_usd_raw)
    elif total_cost_riel_raw:
        total_cost = riel_to_usd(float(total_cost_riel_raw))

    if total_cost is None:
        # No price entered — leave the existing cost-per-unit untouched.
        cost_per_unit = existing["cost_per_unit"] if existing else 0.0
    else:
        # Same "total price of the overall quantity" model as Add material:
        # the number entered here is the total value of what's currently in
        # stock, divided by the current stock quantity to get cost-per-unit.
        current_stock_qty = existing["stock_qty"] if existing else 0.0
        if current_stock_qty > 0:
            cost_per_unit = total_cost / current_stock_qty
        else:
            flash(g.t("flash_price_needs_qty"), "error")
            cost_per_unit = existing["cost_per_unit"] if existing else 0.0

    try:
        db.execute(
            "UPDATE materials SET name=?, unit=?, cost_per_unit=?, supplier_name=?, supplier_contact=?, notes=? "
            "WHERE id=?",
            (name, unit, cost_per_unit, supplier_name or None, supplier_contact or None, notes or None, material_id),
        )
        db.commit()
        flash(g.t("flash_material_updated"), "success")
    except psycopg2.IntegrityError:
        flash(g.t("flash_material_exists", name=name), "error")

    return redirect(url_for("materials"))


@app.route("/materials/<int:material_id>/restock", methods=["POST"])
@login_required
def restock_material(material_id):
    db = get_db()
    qty = float(request.form.get("qty") or 0)
    note = request.form.get("note", "").strip()
    total_cost_usd_raw = request.form.get("total_cost_usd", "").strip()
    total_cost_riel_raw = request.form.get("total_cost_riel", "").strip()

    if qty == 0:
        flash(g.t("flash_qty_nonzero"), "error")
        return redirect(url_for("materials"))

    total_cost = None
    if total_cost_usd_raw:
        total_cost = float(total_cost_usd_raw)
    elif total_cost_riel_raw:
        total_cost = riel_to_usd(float(total_cost_riel_raw))

    # If a total cost was given for this restock (only makes sense for an
    # actual restock, not a negative adjustment), recompute cost_per_unit as
    # a weighted average across old stock + new stock, so prices update
    # realistically as ingredient costs change over time.
    if total_cost is not None and qty > 0:
        material = db.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        old_stock = material["stock_qty"]
        old_cost_per_unit = material["cost_per_unit"]
        new_batch_cost_per_unit = total_cost / qty
        new_total_qty = old_stock + qty
        if new_total_qty > 0:
            weighted_cost = ((old_stock * old_cost_per_unit) + (qty * new_batch_cost_per_unit)) / new_total_qty
        else:
            weighted_cost = new_batch_cost_per_unit
        db.execute(
            "UPDATE materials SET stock_qty = stock_qty + ?, cost_per_unit = ? WHERE id = ?",
            (qty, weighted_cost, material_id),
        )
    else:
        db.execute("UPDATE materials SET stock_qty = stock_qty + ? WHERE id = ?", (qty, material_id))

    reason = "restock" if qty > 0 else "adjustment"
    db.execute(
        "INSERT INTO stock_transactions (material_id, change_qty, reason, note) VALUES (?, ?, ?, ?)",
        (material_id, qty, reason, note or None),
    )
    db.commit()
    flash(g.t("flash_stock_updated"), "success")
    return redirect(url_for("materials"))


@app.route("/materials/<int:material_id>/delete", methods=["POST"])
@login_required
def delete_material(material_id):
    db = get_db()
    # Only an *active* product's recipe blocks deletion. An archived
    # product (soft-deleted because it has order history — see
    # delete_product) can still hold old product_ingredients rows, but
    # those shouldn't lock up a material she's trying to remove; she's not
    # using that product day-to-day anymore.
    in_use = db.execute(
        "SELECT COUNT(*) as c FROM product_ingredients "
        "JOIN products ON products.id = product_ingredients.product_id "
        "WHERE product_ingredients.material_id = ? AND products.active = 1",
        (material_id,),
    ).fetchone()["c"]
    if in_use:
        flash(g.t("flash_material_in_use"), "error")
        return redirect(url_for("materials"))
    # Clear any leftover recipe rows from archived products before deleting
    # — the database's foreign key would otherwise still block the delete
    # even though those products are no longer active. If one of those
    # products gets reactivated later (see add_product), she'll just need
    # to re-add this ingredient to its recipe.
    db.execute(
        "DELETE FROM product_ingredients WHERE material_id = ? AND product_id IN "
        "(SELECT id FROM products WHERE active = 0)",
        (material_id,),
    )
    db.execute("DELETE FROM stock_transactions WHERE material_id = ?", (material_id,))
    db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
    db.commit()
    flash(g.t("flash_material_deleted"), "success")
    return redirect(url_for("materials"))


@app.route("/materials/bake", methods=["POST"])
@login_required
def confirm_bake():
    """Logs an actual production batch: deducts raw materials for real
    (based on each baked product's recipe, aggregated so a material shared
    across several products is only checked/deducted once), and adds the
    baked quantity to each product's available_qty — the finished-goods
    stock that Orders sells from. This is the one place materials actually
    leave stock now; selling on Orders no longer touches materials directly,
    since that already happened here."""
    db = get_db()
    lines = parse_order_form_lines(request.form, allow_decimal=True)  # same product_id[]/quantity[] shape as an order

    if not lines:
        flash(g.t("bake_no_items"), "error")
        return redirect(url_for("materials"))

    line_details, material_needed = compute_order_lines(db, lines)
    shortages = find_stock_shortages(db, material_needed)

    if shortages:
        flash(g.t("bake_shortage", details="; ".join(shortages)), "error")
        return redirect(url_for("materials"))

    for ld in line_details:
        for ing in ld["recipe"]:
            needed = ing["quantity_per_unit"] * ld["qty"]
            db.execute("UPDATE materials SET stock_qty = stock_qty - ? WHERE id = ?", (needed, ing["material_id"]))
            db.execute(
                "INSERT INTO stock_transactions (material_id, change_qty, reason, note) VALUES (?, ?, 'used_in_production', ?)",
                (ing["material_id"], -needed, f'{ld["product"]["name"]} x{ld["qty"]:g}'),
            )
        db.execute(
            "UPDATE products SET available_qty = available_qty + ? WHERE id = ?",
            (ld["qty"], ld["product"]["id"]),
        )

    db.commit()
    baked_summary = ", ".join(f'{ld["product"]["name"]} x{ld["qty"]:g}' for ld in line_details)
    flash(g.t("bake_success", items=baked_summary), "success")
    return redirect(url_for("materials"))


# ---------- Products & recipes (yield-based) ----------

@app.route("/products")
@login_required
def products():
    db = get_db()
    rows = db.execute("SELECT * FROM products WHERE active = 1 ORDER BY name ASC").fetchall()
    all_materials = db.execute("SELECT * FROM materials ORDER BY name ASC").fetchall()
    materials_by_id = {m["id"]: m for m in all_materials}

    # First pass: which products use each material, so we can flag materials
    # that are shared between products (making more of one leaves less for
    # the other, even though each product's own "can make" number looks
    # independent).
    product_recipes = {}
    material_usage = {}  # material_id -> set of product names using it
    for p in rows:
        recipe = db.execute(
            "SELECT product_ingredients.*, materials.name as material_name, materials.unit "
            "FROM product_ingredients JOIN materials ON materials.id = product_ingredients.material_id "
            "WHERE product_id = ?",
            (p["id"],),
        ).fetchall()
        product_recipes[p["id"]] = recipe
        for r in recipe:
            material_usage.setdefault(r["material_id"], set()).add(p["name"])

    products_with_recipes = []
    for p in rows:
        recipe = product_recipes[p["id"]]
        cost_per_unit = sum(
            r["quantity_per_unit"] * materials_by_id[r["material_id"]]["cost_per_unit"]
            for r in recipe if r["material_id"] in materials_by_id
        )

        max_producible = None
        limiting_material = None
        shared_materials = []
        for r in recipe:
            mat = materials_by_id.get(r["material_id"])
            if not mat or r["quantity_per_unit"] <= 0:
                continue
            # A tiny epsilon guards against float imprecision (e.g. 20 / 0.1
            # naively floors to 199 instead of 200 due to binary float
            # rounding) without meaningfully affecting real calculations.
            possible = int(mat["stock_qty"] / r["quantity_per_unit"] + 1e-9)
            if max_producible is None or possible < max_producible:
                max_producible = possible
                limiting_material = mat["name"]
            other_users = material_usage.get(r["material_id"], set()) - {p["name"]}
            if other_users:
                shared_materials.append({"material": mat["name"], "shared_with": sorted(other_users)})

        products_with_recipes.append({
            "product": p,
            "recipe": recipe,
            "cost_per_unit": cost_per_unit,
            "margin": p["price"] - cost_per_unit,
            "max_producible": max_producible,
            "limiting_material": limiting_material,
            "shared_materials": shared_materials,
        })

    open_product_id = request.args.get("open", type=int)
    return render_template(
        "products.html",
        products=products_with_recipes,
        all_materials=all_materials,
        khr_per_usd=KHR_PER_USD,
        open_product_id=open_product_id,
    )


@app.route("/products/add", methods=["POST"])
@login_required
def add_product():
    db = get_db()
    name = request.form.get("name", "").strip()
    price_usd_raw = request.form.get("price_usd", "").strip()
    price_riel_raw = request.form.get("price_riel", "").strip()
    category = request.form.get("category", "other").strip()
    if category not in PRODUCT_CATEGORIES:
        category = "other"

    if not name:
        flash(g.t("flash_product_name_required"), "error")
        return redirect(url_for("products"))

    if price_usd_raw:
        price = float(price_usd_raw)
    elif price_riel_raw:
        price = riel_to_usd(float(price_riel_raw))
    else:
        price = 0.0

    # "This batch makes: N pieces" — set once per product. Every ingredient
    # amount entered on this product's recipe gets divided by this number to
    # get cost-per-piece, so it must be a real, positive count; a blank or
    # invalid entry falls back to 1 rather than silently breaking recipe math.
    try:
        batch_yield = float(request.form.get("batch_yield") or 0)
    except ValueError:
        batch_yield = 0
    if batch_yield <= 0:
        batch_yield = 1.0

    # A product that was "deleted" while it had order history is actually
    # archived, not removed (see delete_product) — its name is still taken.
    # Re-adding the same name should bring it back rather than fail with a
    # confusing duplicate error, since from her side she just deleted it a
    # moment ago and expects to be able to use that name again.
    existing = db.execute("SELECT id, active FROM products WHERE name = ?", (name,)).fetchone()
    if existing and not existing["active"]:
        db.execute(
            "UPDATE products SET active = 1, price = ?, category = ?, batch_yield = ? WHERE id = ?",
            (price, category, batch_yield, existing["id"]),
        )
        db.commit()
        flash(g.t("flash_product_reactivated", name=name), "success")
        return redirect(url_for("products"))

    try:
        db.execute(
            "INSERT INTO products (name, price, category, batch_yield) VALUES (?, ?, ?, ?)",
            (name, price, category, batch_yield),
        )
        db.commit()
        flash(g.t("flash_product_added", name=name), "success")
    except psycopg2.IntegrityError:
        flash(g.t("flash_product_exists", name=name), "error")

    return redirect(url_for("products"))


def recompute_product_recipe_costs(db, product_id, new_batch_yield):
    """Re-derives cost-per-piece for every ingredient on a product after its
    batch size changes. Each ingredient's batch_qty (total amount used per
    full batch) is unchanged — only the per-piece split is recalculated."""
    rows = db.execute(
        "SELECT id, batch_qty FROM product_ingredients WHERE product_id = ?", (product_id,)
    ).fetchall()
    for row in rows:
        quantity_per_unit = row["batch_qty"] / new_batch_yield
        db.execute(
            "UPDATE product_ingredients SET yield_count = ?, quantity_per_unit = ? WHERE id = ?",
            (new_batch_yield, quantity_per_unit, row["id"]),
        )


@app.route("/products/<int:product_id>/batch-yield", methods=["POST"])
@login_required
def update_batch_yield(product_id):
    db = get_db()
    try:
        batch_yield = float(request.form.get("batch_yield") or 0)
    except ValueError:
        batch_yield = 0

    if batch_yield <= 0:
        flash(g.t("flash_batch_yield_invalid"), "error")
        return redirect(url_for("products", open=product_id))

    db.execute("UPDATE products SET batch_yield = ? WHERE id = ?", (batch_yield, product_id))
    recompute_product_recipe_costs(db, product_id, batch_yield)
    db.commit()
    flash(g.t("flash_batch_yield_updated"), "success")
    return redirect(url_for("products", open=product_id))


@app.route("/products/<int:product_id>/recipe/add", methods=["POST"])
@login_required
def add_recipe_ingredient(product_id):
    """Records how much of one material the WHOLE BATCH uses — e.g. "2kg
    flour" for a batch that makes 30 pieces — rather than asking her to
    pre-calculate a per-piece amount herself. The amount can be typed in any
    unit compatible with the material's own stock unit (e.g. grams for a
    material tracked in kg) and gets converted back before storing. The
    per-piece amount (quantity_per_unit, used everywhere else in the app for
    costing and stock deduction) is derived here by dividing the batch total
    by the product's batch_yield, and gets re-derived automatically whenever
    batch_yield changes (see recompute_product_recipe_costs)."""
    db = get_db()
    material_id = request.form.get("material_id")
    entered_unit = request.form.get("entered_unit", "").strip()
    try:
        entered_amount = float(request.form.get("batch_total_qty") or 0)
    except ValueError:
        entered_amount = 0

    if not material_id or entered_amount <= 0:
        flash(g.t("flash_recipe_invalid"), "error")
        return redirect(url_for("products"))

    product = db.execute("SELECT batch_yield FROM products WHERE id = ?", (product_id,)).fetchone()
    batch_yield = product["batch_yield"] if product and product["batch_yield"] > 0 else 1.0

    material = db.execute("SELECT unit FROM materials WHERE id = ?", (material_id,)).fetchone()
    base_unit = material["unit"] if material else entered_unit
    batch_qty = convert_amount_to_base_unit(entered_amount, entered_unit, base_unit)
    quantity_per_unit = batch_qty / batch_yield

    existing = db.execute(
        "SELECT id FROM product_ingredients WHERE product_id = ? AND material_id = ?",
        (product_id, material_id),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE product_ingredients SET batch_qty = ?, yield_count = ?, quantity_per_unit = ? WHERE id = ?",
            (batch_qty, batch_yield, quantity_per_unit, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO product_ingredients (product_id, material_id, batch_qty, yield_count, quantity_per_unit) "
            "VALUES (?, ?, ?, ?, ?)",
            (product_id, material_id, batch_qty, batch_yield, quantity_per_unit),
        )
    db.commit()
    flash(g.t("flash_recipe_updated"), "success")
    return redirect(url_for("products", open=product_id))


@app.route("/products/recipe/<int:ingredient_id>/remove", methods=["POST"])
@login_required
def remove_recipe_ingredient(ingredient_id):
    db = get_db()
    row = db.execute(
        "SELECT product_id FROM product_ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()
    db.execute("DELETE FROM product_ingredients WHERE id = ?", (ingredient_id,))
    db.commit()
    flash(g.t("flash_ingredient_removed"), "success")
    return redirect(url_for("products", open=row["product_id"] if row else None))


@app.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    db = get_db()
    in_use = db.execute(
        "SELECT COUNT(*) as c FROM order_items WHERE product_id = ?", (product_id,)
    ).fetchone()["c"]
    if in_use:
        db.execute("UPDATE products SET active = 0 WHERE id = ?", (product_id,))
        db.commit()
        flash(g.t("flash_product_archived"), "success")
    else:
        db.execute("DELETE FROM product_ingredients WHERE product_id = ?", (product_id,))
        db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        db.commit()
        flash(g.t("flash_product_deleted"), "success")
    return redirect(url_for("products"))


# ---------- Orders (customer orders, multi-item, real-time stock deduction) ----------

@app.route("/orders")
@login_required
def orders():
    db = get_db()
    active_products = db.execute(
        "SELECT * FROM products WHERE active = 1 ORDER BY name ASC"
    ).fetchall()

    # Group products by category, in a fixed display order, for the Orders
    # page's tap-to-add grid — this is presentation-only grouping, done here
    # rather than in SQL so the category order stays exactly PRODUCT_CATEGORIES
    # regardless of how products were inserted.
    products_by_category = {c: [] for c in PRODUCT_CATEGORIES}
    for p in active_products:
        cat = p["category"] if p["category"] in PRODUCT_CATEGORIES else "other"
        products_by_category[cat].append(p)
    grouped_products = [
        {"key": c, "label_key": f"category_{c}", "products": products_by_category[c]}
        for c in PRODUCT_CATEGORIES if products_by_category[c]
    ]

    # Date filter for the order list below — "today" and "all" are special-
    # cased since they aren't a fixed day count; everything else is treated
    # as "last N days". Defaults to 30 days, same as before.
    days_filter = request.args.get("days", "30")
    if days_filter == "today":
        since = now_local().strftime("%Y-%m-%d")
        recent = db.execute(
            "SELECT * FROM orders WHERE date(ordered_at) >= ? ORDER BY ordered_at DESC",
            (since,),
        ).fetchall()
    elif days_filter == "all":
        # Still capped so the page can't grow unbounded on a long-running shop.
        recent = db.execute("SELECT * FROM orders ORDER BY ordered_at DESC LIMIT 200").fetchall()
    else:
        try:
            days_n = int(days_filter)
        except ValueError:
            days_n, days_filter = 30, "30"
        since = (now_local() - timedelta(days=days_n)).strftime("%Y-%m-%d")
        recent = db.execute(
            "SELECT * FROM orders WHERE date(ordered_at) >= ? ORDER BY ordered_at DESC",
            (since,),
        ).fetchall()

    order_summaries = []
    for o in recent:
        items = db.execute(
            "SELECT order_items.*, products.name as product_name FROM order_items "
            "JOIN products ON products.id = order_items.product_id WHERE order_id = ?",
            (o["id"],),
        ).fetchall()
        items_text = ", ".join(f'{it["product_name"]} x{it["quantity"]}' for it in items)
        order_summaries.append({"order": o, "items_text": items_text})

    return render_template(
        "orders.html",
        products=active_products,
        grouped_products=grouped_products,
        orders=order_summaries,
        days_filter=days_filter,
    )


def parse_order_form_lines(form, allow_decimal=False):
    """Reads product_id[]/quantity[] pairs from a submitted order form.
    Customer orders keep whole-number quantities (you can't sell half a
    loaf to one customer), but the bake planner allows decimals — a batch
    might yield a fractional number of a product (e.g. 2.5kg of cake cut
    into slices), so it passes allow_decimal=True."""
    product_ids = form.getlist("product_id[]")
    quantities = form.getlist("quantity[]")
    lines = []
    for pid, qty_raw in zip(product_ids, quantities):
        if not pid or not qty_raw:
            continue
        try:
            qty = float(qty_raw) if allow_decimal else int(qty_raw)
        except ValueError:
            continue
        if qty <= 0:
            continue
        lines.append((int(pid), qty))
    return lines


def compute_order_lines(db, lines):
    """Given [(product_id, qty), ...], returns (line_details, material_needed)
    where material_needed aggregates quantity needed per material_id across
    all lines (so two products sharing an ingredient are handled correctly)."""
    material_needed = {}
    line_details = []
    for product_id, qty in lines:
        product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            continue
        recipe = db.execute(
            "SELECT * FROM product_ingredients WHERE product_id = ?", (product_id,)
        ).fetchall()
        line_cost = 0.0
        for ing in recipe:
            needed = ing["quantity_per_unit"] * qty
            material_needed[ing["material_id"]] = material_needed.get(ing["material_id"], 0) + needed
            mat = db.execute("SELECT cost_per_unit FROM materials WHERE id = ?", (ing["material_id"],)).fetchone()
            line_cost += needed * (mat["cost_per_unit"] if mat else 0)
        line_total = product["price"] * qty
        line_details.append({
            "product": product, "qty": qty, "recipe": recipe,
            "line_total": line_total, "line_cost": line_cost,
        })
    return line_details, material_needed


def find_stock_shortages(db, material_needed):
    shortages = []
    for material_id, needed in material_needed.items():
        mat = db.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        if mat and mat["stock_qty"] < needed:
            shortages.append(f'{mat["name"]} ({needed:g}{mat["unit"]} / {mat["stock_qty"]:g}{mat["unit"]})')
    return shortages


def find_availability_shortages(db, lines):
    """Checks planned order lines against each product's baked-and-available
    stock (available_qty) — not raw materials, which were already deducted
    when the batch was baked via /materials/bake. Aggregates by product in
    case the same product appears on more than one line."""
    needed_by_product = {}
    for product_id, qty in lines:
        needed_by_product[product_id] = needed_by_product.get(product_id, 0) + qty
    shortages = []
    for product_id, needed in needed_by_product.items():
        product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if product and product["available_qty"] < needed:
            shortages.append(f'{product["name"]} ({needed:g} / {product["available_qty"]:g})')
    return shortages


def restore_order_availability(db, order_id):
    """Reverses the available-to-sell deduction of an existing order's items
    (adds the quantity back to what's available). Used before re-applying an
    edited order. Does not touch raw materials — those were already consumed
    when the batch was baked, not when the order was placed."""
    items = db.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    for it in items:
        db.execute(
            "UPDATE products SET available_qty = available_qty + ? WHERE id = ?",
            (it["quantity"], it["product_id"]),
        )


@app.route("/orders/add", methods=["POST"])
@login_required
def add_order():
    db = get_db()
    customer_name = request.form.get("customer_name", "").strip()
    customer_phone = request.form.get("customer_phone", "").strip()
    customer_address = request.form.get("customer_address", "").strip()
    note = request.form.get("note", "").strip()
    lines = parse_order_form_lines(request.form)

    if not lines:
        flash(g.t("order_no_items"), "error")
        return redirect(url_for("orders"))

    # line_cost still uses each product's recipe and current material cost,
    # purely to estimate COGS for profit reporting — the recipe itself
    # isn't re-checked against materials here, only against what's already
    # baked and available (materials were consumed at bake time instead).
    line_details, _material_needed = compute_order_lines(db, lines)
    shortages = find_availability_shortages(db, lines)

    if shortages:
        flash(g.t("order_shortage", details="; ".join(shortages)), "error")
        return redirect(url_for("orders"))

    total_amount = sum(ld["line_total"] for ld in line_details)
    total_cost = sum(ld["line_cost"] for ld in line_details)
    total_profit = total_amount - total_cost

    # New orders always start out "pending" — the person taps to mark an
    # order paid once the customer actually pays.
    cursor = db.execute(
        "INSERT INTO orders (customer_name, customer_phone, customer_address, note, total_amount, total_cost, total_profit, payment_status, created_by_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?) RETURNING id",
        (customer_name or None, customer_phone or None, customer_address or None, note or None, total_amount, total_cost, total_profit, session.get("user_email")),
    )
    order_id = cursor.fetchone()["id"]

    for ld in line_details:
        db.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price, line_total, line_cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, ld["product"]["id"], ld["qty"], ld["product"]["price"], ld["line_total"], ld["line_cost"]),
        )
        db.execute(
            "UPDATE products SET available_qty = available_qty - ? WHERE id = ?",
            (ld["qty"], ld["product"]["id"]),
        )

    db.commit()
    flash(
        g.t(
            "order_success",
            customer=customer_name or g.t("walk_in_customer"),
            total=f"{total_amount:,.2f}",
            profit=f"{total_profit:,.2f}",
        ),
        "success",
    )
    return redirect(url_for("orders"))


@app.route("/orders/<int:order_id>/edit", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return redirect(url_for("orders"))

    if request.method == "GET":
        active_products = db.execute(
            "SELECT * FROM products WHERE active = 1 ORDER BY name ASC"
        ).fetchall()
        items = db.execute(
            "SELECT order_items.*, products.name as product_name FROM order_items "
            "JOIN products ON products.id = order_items.product_id WHERE order_id = ?",
            (order_id,),
        ).fetchall()
        return render_template("edit_order.html", order=order, items=items, products=active_products)

    # POST: apply the edit
    customer_name = request.form.get("customer_name", "").strip()
    customer_phone = request.form.get("customer_phone", "").strip()
    customer_address = request.form.get("customer_address", "").strip()
    note = request.form.get("note", "").strip()
    lines = parse_order_form_lines(request.form)

    if not lines:
        flash(g.t("order_no_items"), "error")
        return redirect(url_for("edit_order", order_id=order_id))

    # Undo the original availability deduction first (not committed yet),
    # then validate the new lines against that restored available stock. If
    # anything is short, we roll back so the restore itself never takes
    # effect. Raw materials are untouched either way — they were consumed
    # when the batch was baked, not when this order was placed.
    restore_order_availability(db, order_id)
    line_details, _material_needed = compute_order_lines(db, lines)
    shortages = find_availability_shortages(db, lines)

    if shortages:
        db.rollback()
        flash(g.t("order_shortage", details="; ".join(shortages)), "error")
        return redirect(url_for("edit_order", order_id=order_id))

    total_amount = sum(ld["line_total"] for ld in line_details)
    total_cost = sum(ld["line_cost"] for ld in line_details)
    total_profit = total_amount - total_cost

    db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    db.execute(
        "UPDATE orders SET customer_name=?, customer_phone=?, customer_address=?, note=?, "
        "total_amount=?, total_cost=?, total_profit=? WHERE id=?",
        (customer_name or None, customer_phone or None, customer_address or None, note or None,
         total_amount, total_cost, total_profit, order_id),
    )

    for ld in line_details:
        db.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price, line_total, line_cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, ld["product"]["id"], ld["qty"], ld["product"]["price"], ld["line_total"], ld["line_cost"]),
        )
        db.execute(
            "UPDATE products SET available_qty = available_qty - ? WHERE id = ?",
            (ld["qty"], ld["product"]["id"]),
        )

    db.commit()
    flash(g.t("order_updated_success"), "success")
    return redirect(url_for("order_invoice", order_id=order_id))


@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id):
    db = get_db()
    db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    db.commit()
    flash(g.t("order_deleted"), "success")
    return redirect(url_for("orders"))


@app.route("/orders/<int:order_id>/toggle-payment", methods=["POST"])
@login_required
def toggle_payment_status(order_id):
    db = get_db()
    order = db.execute("SELECT payment_status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order:
        new_status = "pending" if order["payment_status"] == "paid" else "paid"
        db.execute("UPDATE orders SET payment_status = ? WHERE id = ?", (new_status, order_id))
        db.commit()
        flash(g.t("flash_payment_updated"), "success")
    return redirect(request.referrer or url_for("orders"))


@app.route("/orders/<int:order_id>/invoice")
@login_required
def order_invoice(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return redirect(url_for("orders"))
    items = db.execute(
        "SELECT order_items.*, products.name as product_name FROM order_items "
        "JOIN products ON products.id = order_items.product_id WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    invoice_text = build_invoice_text(g.t, order, items)
    return render_template(
        "invoice.html",
        order=order,
        items=items,
        invoice_text=invoice_text,
        shop_name=SHOP_NAME,
        shop_phone=SHOP_PHONE,
        khr_per_usd=KHR_PER_USD,
        telegram_shop_enabled=bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        has_qr=bool(get_qr_data()),
    )


@app.route("/orders/<int:order_id>/send-telegram", methods=["POST"])
@login_required
def send_order_telegram(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return redirect(url_for("orders"))
    items = db.execute(
        "SELECT order_items.*, products.name as product_name FROM order_items "
        "JOIN products ON products.id = order_items.product_id WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    invoice_text = build_invoice_text(g.t, order, items)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        flash(g.t("telegram_not_configured"), "error")
        return redirect(url_for("order_invoice", order_id=order_id))

    ok, err = send_telegram_message(TELEGRAM_CHAT_ID, invoice_text)
    if ok:
        flash(g.t("telegram_sent_success"), "success")
    else:
        flash(g.t("telegram_sent_failed"), "error")
    return redirect(url_for("order_invoice", order_id=order_id))


@app.route("/orders/<int:order_id>/send-telegram-image", methods=["POST"])
@login_required
def send_order_telegram_image(order_id):
    """Receives a base64 PNG (captured client-side from the invoice card) and
    forwards it to the shop's Telegram chat as a photo. Used by the invoice
    page's 'Send image to shop Telegram' button."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return jsonify({"ok": False, "reason": "not_configured"}), 400

    data = request.get_json(silent=True) or {}
    image_data_url = data.get("image", "")
    if "," not in image_data_url:
        return jsonify({"ok": False, "reason": "bad_image"}), 400

    try:
        image_bytes = base64.b64decode(image_data_url.split(",", 1)[1])
    except Exception:
        return jsonify({"ok": False, "reason": "bad_image"}), 400

    caption = f"{SHOP_NAME} — {g.t('invoice_title_km')} #{order_id}"
    ok, err = send_telegram_photo(TELEGRAM_CHAT_ID, image_bytes, caption=caption)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "reason": err}), 502


# ---------- Expenses ----------

@app.route("/expenses/add", methods=["POST"])
@login_required
def add_expense():
    db = get_db()
    category = request.form.get("category", "").strip()
    amount_usd_raw = request.form.get("amount_usd", "").strip()
    amount_riel_raw = request.form.get("amount_riel", "").strip()
    note = request.form.get("note", "").strip()

    if amount_usd_raw:
        amount = float(amount_usd_raw)
    elif amount_riel_raw:
        amount = riel_to_usd(float(amount_riel_raw))
    else:
        amount = 0.0

    if not category or amount <= 0:
        flash(g.t("flash_expense_invalid"), "error")
        return redirect(url_for("reports"))

    db.execute(
        "INSERT INTO expenses (category, amount, note) VALUES (?, ?, ?)",
        (category, amount, note or None),
    )
    db.commit()
    flash(g.t("flash_expense_logged"), "success")
    return redirect(url_for("reports"))


# ---------- Reports ----------

@app.route("/reports")
@login_required
def reports():
    db = get_db()

    days = int(request.args.get("days", 30))
    since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d")

    daily_income = db.execute(
        "SELECT date(ordered_at) as day, SUM(total_amount) as total "
        "FROM orders WHERE date(ordered_at) >= ? GROUP BY day ORDER BY day ASC",
        (since,),
    ).fetchall()

    by_product = db.execute(
        "SELECT products.name, SUM(order_items.quantity) as units, SUM(order_items.line_total) as total "
        "FROM order_items JOIN products ON products.id = order_items.product_id "
        "JOIN orders ON orders.id = order_items.order_id "
        "WHERE date(orders.ordered_at) >= ? GROUP BY products.id ORDER BY total DESC",
        (since,),
    ).fetchall()

    totals = db.execute(
        "SELECT COALESCE(SUM(total_amount),0) as income, COALESCE(SUM(total_cost),0) as cost "
        "FROM orders WHERE date(ordered_at) >= ?",
        (since,),
    ).fetchone()

    total_expenses = db.execute(
        "SELECT COALESCE(SUM(amount),0) as total FROM expenses WHERE date(spent_at) >= ?", (since,)
    ).fetchone()["total"]

    recent_expenses = db.execute(
        "SELECT * FROM expenses WHERE date(spent_at) >= ? ORDER BY spent_at DESC", (since,)
    ).fetchall()

    return render_template(
        "reports.html",
        days=days,
        daily_income=daily_income,
        by_product=by_product,
        total_income=totals["income"],
        total_expenses=total_expenses,
        material_cost=totals["cost"],
        net_profit=totals["income"] - total_expenses - totals["cost"],
        recent_expenses=recent_expenses,
        khr_per_usd=KHR_PER_USD,
    )


# ---------- Settings (payment QR code) ----------

@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html", qr_filename=("payment_qr" if get_qr_data() else None))


@app.route("/settings/qr/upload", methods=["POST"])
@login_required
def upload_qr():
    file_storage = request.files.get("qr_image")
    if not file_storage or not file_storage.filename:
        flash(g.t("qr_upload_invalid"), "error")
        return redirect(url_for("settings"))

    if save_qr_upload(file_storage):
        flash(g.t("qr_upload_success"), "success")
    else:
        flash(g.t("qr_upload_invalid"), "error")
    return redirect(url_for("settings"))


@app.route("/settings/qr/delete", methods=["POST"])
@login_required
def delete_qr():
    clear_qr_upload()
    flash(g.t("qr_delete_success"), "success")
    return redirect(url_for("settings"))


@app.route("/settings/reset-test-data", methods=["POST"])
@login_required
def reset_test_data():
    """Wipes sales/money data only — orders, order items, expenses, and
    every product's available-to-sell quantity. Materials, products,
    recipes, and material stock/cost are deliberately left untouched, since
    those represent real setup rather than test transactions. Requires
    typing the literal word RESET as a confirmation, on top of a JS confirm
    dialog, since this can't be undone."""
    if request.form.get("confirm_text", "").strip() != "RESET":
        flash(g.t("reset_confirm_mismatch"), "error")
        return redirect(url_for("settings"))

    db = get_db()
    db.execute("DELETE FROM order_items")
    db.execute("DELETE FROM orders")
    db.execute("DELETE FROM expenses")
    db.execute("UPDATE products SET available_qty = 0")
    db.commit()
    flash(g.t("reset_success"), "success")
    return redirect(url_for("settings"))


@app.route("/payment-qr-image")
@login_required
def payment_qr_image():
    result = get_qr_data()
    if not result:
        return "", 404
    image_bytes, mimetype = result
    return app.response_class(image_bytes, mimetype=mimetype)


if __name__ == "__main__":
    # Sessions default to a 31-day lifetime once marked permanent (see the
    # "remember me" checkbox on the login page) — this is what keeps people
    # logged in across app restarts and phone reboots instead of only for
    # the current browser session.
    app.permanent_session_lifetime = timedelta(days=REMEMBER_ME_DAYS)
    init_db()
    migrate_db()
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
else:
    app.permanent_session_lifetime = timedelta(days=REMEMBER_ME_DAYS)
    init_db()
    migrate_db()