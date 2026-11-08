import os
import hmac
import secrets
import threading
import re
import json
import uuid
import urllib.request
import urllib.error

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

import jdatetime
import psycopg

from psycopg.rows import dict_row
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)
from werkzeug.middleware.proxy_fix import ProxyFix
from customer_store import customer_bp, init_customer_store, ensure_customer_schema
from store_features import features_bp, init_features, ensure_features_schema, run_price_alerts
from android_api import android_api_bp, init_android_api, ensure_android_api_schema
from chatgpt_api import chatgpt_bp, init_chatgpt_api

# ==================================================
# تنظیمات اصلی
# ==================================================

app = Flask(__name__)

# فقط اگر هاست شما یک reverse proxy قابل اعتماد دارد، TRUST_PROXY=true کنید.
# در غیر این صورت هدرهای Forwarded را به‌عنوان IP واقعی کاربر قبول نکنید.
if os.environ.get("TRUST_PROXY", "false").lower() == "true":
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=0,
        x_port=0,
        x_prefix=0
    )

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if len(SECRET_KEY) < 32:
    raise RuntimeError(
        "متغیر SECRET_KEY باید در تنظیمات هاست تعریف شود "
        "و حداقل ۳۲ کاراکتر تصادفی داشته باشد."
    )

app.config["SECRET_KEY"] = SECRET_KEY
app.register_blueprint(customer_bp)
app.register_blueprint(features_bp)
app.register_blueprint(android_api_bp)
app.register_blueprint(chatgpt_bp)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "true").lower() == "true"
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_REFRESH_EACH_REQUEST"] = False

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

TEHRAN = ZoneInfo("Asia/Tehran")

if not DATABASE_URL:
    raise RuntimeError("متغیر DATABASE_URL تنظیم نشده است.")


# ==================================================
# اعداد فارسی و انگلیسی
# ==================================================

PERSIAN_DIGITS = str.maketrans(
    "0123456789",
    "۰۱۲۳۴۵۶۷۸۹"
)

ENGLISH_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789"
)


def normalize_digits(value):
    if value is None:
        return ""

    return str(value).translate(ENGLISH_DIGITS)


def persian_numbers(value):
    if value is None:
        return ""

    return str(value).translate(PERSIAN_DIGITS)


def tehran_now():
    return datetime.now(TEHRAN)


# ==================================================
# تاریخ شمسی و ساعت تهران
# ==================================================

def jalali_date(value, fmt=None):

    if value is None:
        return ""

    if isinstance(value, datetime):

        if value.tzinfo is None:
            value = value.replace(tzinfo=TEHRAN)

        value = value.astimezone(TEHRAN)
        value = value.replace(tzinfo=None)

        result = jdatetime.datetime.fromgregorian(
            datetime=value
        )

        output = result.strftime(
            fmt or "%Y/%m/%d %H:%M"
        )

    elif isinstance(value, date):

        result = jdatetime.date.fromgregorian(
            date=value
        )

        output = result.strftime(
            fmt or "%Y/%m/%d"
        )

    else:
        return value

    return output.translate(PERSIAN_DIGITS)


def jalali_time(value):

    if value is None:
        return ""

    if isinstance(value, datetime):

        if value.tzinfo is None:
            value = value.replace(tzinfo=TEHRAN)

        value = value.astimezone(TEHRAN)

        return value.strftime("%H:%M").translate(
            PERSIAN_DIGITS
        )

    return ""


def jalali_day(value):
    return jalali_date(value, "%Y/%m/%d")


def jalali_hour(value):
    return jalali_date(value, "%H:%M")


app.jinja_env.filters["jalali"] = jalali_date
app.jinja_env.filters["jalali_date"] = jalali_day
app.jinja_env.filters["jalali_time"] = jalali_hour
app.jinja_env.filters["persian_numbers"] = persian_numbers

app.jinja_env.globals["tehran_now"] = tehran_now

app.jinja_env.finalize = jalali_date


# ==================================================
# اتصال دیتابیس
# ==================================================

def db():
    return psycopg.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=15,
        row_factory=dict_row
    )

# اتصال توابع حساب مشتری پس از تعریف db()
init_customer_store(db, SECRET_KEY)
init_features(db)
init_android_api(db, SECRET_KEY)
init_chatgpt_api(db)


# ==================================================
# ساخت و به‌روزرسانی جداول
# ==================================================

_schema_ready = False
_schema_lock = threading.Lock()


def init_db():

    with db() as conn:

        conn.execute("SET TIME ZONE 'Asia/Tehran'")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS brands (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                category_id INTEGER
                    REFERENCES categories(id) ON DELETE RESTRICT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price BIGINT NOT NULL DEFAULT 0
                    CHECK (price >= 0),
                image_url TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS stock INTEGER NOT NULL DEFAULT 0
        """)

        conn.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS min_stock INTEGER NOT NULL DEFAULT 2
        """)

        conn.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS brand_id INTEGER
            REFERENCES brands(id) ON DELETE SET NULL
        """)

        conn.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_movements (
                id BIGSERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL
                    REFERENCES products(id) ON DELETE RESTRICT,
                movement_type TEXT NOT NULL
                    CHECK (movement_type IN
                    ('in', 'out', 'adjustment', 'sale', 'return')),
                quantity INTEGER NOT NULL,
                note TEXT DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id BIGSERIAL PRIMARY KEY,
                invoice_number BIGINT GENERATED BY DEFAULT AS IDENTITY UNIQUE,
                total_amount BIGINT NOT NULL DEFAULT 0,
                discount BIGINT NOT NULL DEFAULT 0,
                final_amount BIGINT NOT NULL DEFAULT 0,
                description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'completed'
                    CHECK (status IN ('completed', 'cancelled')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # ستون‌های اطلاعات مشتری
        # برای فاکتورهای قدیمی مقدار این ستون‌ها NULL باقی می‌ماند.

        conn.execute("""
            ALTER TABLE sales
            ADD COLUMN IF NOT EXISTS customer_first_name TEXT
        """)

        conn.execute("""
            ALTER TABLE sales
            ADD COLUMN IF NOT EXISTS customer_last_name TEXT
        """)

        conn.execute("""
            ALTER TABLE sales
            ADD COLUMN IF NOT EXISTS customer_phone TEXT
        """)

        conn.execute("""
            ALTER TABLE sales
            ADD COLUMN IF NOT EXISTS customer_national_id TEXT
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sale_items (
                id BIGSERIAL PRIMARY KEY,
                sale_id BIGINT NOT NULL
                    REFERENCES sales(id) ON DELETE RESTRICT,
                product_id INTEGER NOT NULL
                    REFERENCES products(id) ON DELETE RESTRICT,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price BIGINT NOT NULL CHECK (unit_price >= 0),
                line_total BIGINT NOT NULL CHECK (line_total >= 0)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_product
            ON stock_movements(product_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sales_created
            ON sales(created_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sale_items_sale
            ON sale_items(sale_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_products_active
            ON products(is_active)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id BIGSERIAL PRIMARY KEY,
                ip_hash TEXT NOT NULL,
                attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
            ON login_attempts(ip_hash, attempted_at)
        """)


        conn.execute("""CREATE TABLE IF NOT EXISTS installment_plans (
            id BIGSERIAL PRIMARY KEY, customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL, total_amount BIGINT NOT NULL CHECK(total_amount > 0),
            installment_count INTEGER NOT NULL CHECK(installment_count > 0),
            notes TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS installment_payments (
            id BIGSERIAL PRIMARY KEY, plan_id BIGINT NOT NULL REFERENCES installment_plans(id) ON DELETE RESTRICT,
            installment_no INTEGER NOT NULL CHECK(installment_no > 0),
            amount BIGINT NOT NULL CHECK(amount > 0), due_date DATE NOT NULL,
            paid_at TIMESTAMPTZ, UNIQUE(plan_id, installment_no)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_installments_due ON installment_payments(due_date, paid_at)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sms_settings (
                id SMALLINT PRIMARY KEY CHECK (id = 1),
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                welcome_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                reminder_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                send_hour SMALLINT NOT NULL DEFAULT 9 CHECK (send_hour BETWEEN 0 AND 23),
                send_minute SMALLINT NOT NULL DEFAULT 0 CHECK (send_minute BETWEEN 0 AND 59),
                line_number TEXT NOT NULL DEFAULT '30005088',
                welcome_template TEXT NOT NULL DEFAULT 'مرسی که لوازم خانگی بولحسنی را انتخاب کردید. تاریخ اقساط شما از {first_due_date} تا {last_due_date} است. با سپاس',
                reminder_template TEXT NOT NULL DEFAULT 'مشتری گرامی {customer_name}، یادآوری می‌شود قسط شماره {installment_no} به مبلغ {amount} تومان در تاریخ {due_date} سررسید می‌شود. لوازم خانگی بولحسنی'
            )
        """)
        conn.execute("INSERT INTO sms_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS installment_sms_logs (
                id BIGSERIAL PRIMARY KEY,
                plan_id BIGINT REFERENCES installment_plans(id) ON DELETE SET NULL,
                payment_id BIGINT REFERENCES installment_payments(id) ON DELETE SET NULL,
                kind TEXT NOT NULL CHECK (kind IN ('welcome', 'reminder')),
                receptor TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing'
                    CHECK (status IN ('processing', 'sent', 'failed')),
                provider_message_id TEXT,
                error_text TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_installment_sms_once
            ON installment_sms_logs (plan_id, (COALESCE(payment_id, 0)), kind)
            WHERE plan_id IS NOT NULL
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_installment_sms_logs_created ON installment_sms_logs (created_at DESC)")



@app.before_request
def prepare_database():

    global _schema_ready

    if not _schema_ready:

        with _schema_lock:

            if not _schema_ready:
                init_db()
                ensure_customer_schema()
                ensure_features_schema()
                ensure_android_api_schema()
                _schema_ready = True


# ==================================================
# امنیت و CSRF
# ==================================================

def csrf_token():

    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)

    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )

    # قالب‌های فعلی از CSS و JavaScript داخلی استفاده می‌کنند؛
    # بنابراین unsafe-inline فعلاً برای حفظ عملکرد سایت لازم است.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "connect-src 'self' https:; "
    )

    # COOKIE_SECURE=true یعنی سایت پشت HTTPS ارائه می‌شود.
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000"
        )

    return response


@app.before_request
def check_csrf():

    if request.method == "POST":
        # Native Android API uses JSON bodies and bearer tokens, not browser sessions.
        if request.path.startswith("/api/android/v1/") and request.is_json:
            return

        if request.path in ("/internal/sms/run", "/internal/price-alerts/run"):
            cron_secret = os.environ.get("SMS_CRON_SECRET", "").strip()
            auth = request.headers.get("Authorization", "")
            if len(cron_secret) >= 32 and hmac.compare_digest(auth, "Bearer " + cron_secret):
                return

        sent = (
            request.form.get("_csrf", "")
            or request.form.get("csrf_token", "")
        )

        saved = session.get("_csrf", "")

        if (
            not saved
            or not sent
            or not hmac.compare_digest(sent, saved)
        ):

            abort(
                400,
                description="درخواست نامعتبر است. صفحه را تازه‌سازی کنید."
            )


def admin_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if not session.get("admin"):

            flash("ابتدا وارد حساب مدیر شوید.", "error")

            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


# ==================================================
# صفحه عمومی فروشگاه
# ==================================================

@app.route("/")
def index():

    q = request.args.get("q", "").strip()

    selected_category = request.args.get(
        "category", ""
    ).strip()

    with db() as conn:

        categories = conn.execute("""
            SELECT id, name
            FROM categories
            ORDER BY name
        """).fetchall()

        sql = """
            SELECT
                p.*,
                c.name AS category_name,
                b.name AS brand_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN brands b ON b.id = p.brand_id
            WHERE p.is_active = TRUE
        """

        params = []

        if q:

            sql += """
                AND (
                    p.name ILIKE %s
                    OR p.description ILIKE %s
                    OR c.name ILIKE %s
                    OR b.name ILIKE %s
                )
            """

            term = f"%{q}%"

            params.extend([
                term, term, term, term
            ])

        if selected_category.isdigit():

            sql += " AND p.category_id = %s"

            params.append(int(selected_category))

        sql += " ORDER BY p.created_at DESC"

        products = conn.execute(
            sql, params
        ).fetchall()

    return render_template(
        "index.html",
        products=products,
        categories=categories,
        q=q,
        selected_category=selected_category
    )


# ==================================================
# محدودسازی تلاش ورود مدیر
# ==================================================

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_MINUTES = 15


def login_ip_hash():
    # آدرس IP خام در دیتابیس ذخیره نمی‌شود.
    ip_address = request.remote_addr or "unknown"
    return hmac.new(
        app.config["SECRET_KEY"].encode("utf-8"),
        ip_address.encode("utf-8"),
        digestmod="sha256"
    ).hexdigest()


def login_attempts_count(ip_hash):
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS attempts
            FROM login_attempts
            WHERE ip_hash = %s
              AND attempted_at > NOW() - (%s * INTERVAL '1 minute')
            """,
            (ip_hash, LOGIN_WINDOW_MINUTES)
        ).fetchone()
    return int(row["attempts"] or 0)


def record_login_failure(ip_hash):
    with db() as conn:
        conn.execute(
            "INSERT INTO login_attempts (ip_hash) VALUES (%s)",
            (ip_hash,)
        )
        # پاکسازی دوره‌ای رکوردهای قدیمی
        conn.execute(
            """
            DELETE FROM login_attempts
            WHERE attempted_at < NOW() - INTERVAL '2 days'
            """
        )


def clear_login_failures(ip_hash):
    with db() as conn:
        conn.execute(
            "DELETE FROM login_attempts WHERE ip_hash = %s",
            (ip_hash,)
        )


# ==================================================
# ورود و خروج مدیر
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if session.get("admin"):
        return redirect(url_for("admin"))

    ip_hash = login_ip_hash()

    if request.method == "POST":

        if login_attempts_count(ip_hash) >= LOGIN_MAX_ATTEMPTS:
            flash(
                "تعداد تلاش‌های ورود بیش از حد مجاز است. "
                "۱۵ دقیقه بعد دوباره تلاش کنید.",
                "error"
            )
            return render_template("login.html"), 429

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        username_ok = hmac.compare_digest(username, ADMIN_USERNAME)
        password_ok = (
            bool(ADMIN_PASSWORD)
            and hmac.compare_digest(password, ADMIN_PASSWORD)
        )

        if username_ok and password_ok:
            clear_login_failures(ip_hash)
            session.clear()
            session.permanent = True
            session["admin"] = True
            session["_csrf"] = secrets.token_urlsafe(32)
            flash("با موفقیت وارد شدید.", "success")
            return redirect(url_for("admin"))

        record_login_failure(ip_hash)
        flash("نام کاربری یا رمز عبور اشتباه است.", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@admin_required
def logout():

    session.clear()

    flash("از حساب مدیر خارج شدید.", "success")

    return redirect(url_for("login"))


# ==================================================
# داشبورد مدیریت
# ==================================================

@app.route("/admin")
@admin_required
def admin():

    with db() as conn:

        categories = conn.execute("""
            SELECT
                c.id,
                c.name,
                COUNT(p.id) AS product_count
            FROM categories c
            LEFT JOIN products p
                ON p.category_id = c.id
                AND p.is_active = TRUE
            GROUP BY c.id
            ORDER BY c.name
        """).fetchall()

        products = conn.execute("""
            SELECT
                p.*,
                c.name AS category_name,
                b.name AS brand_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN brands b ON b.id = p.brand_id
            WHERE p.is_active = TRUE
            ORDER BY p.created_at DESC
        """).fetchall()

        brands = conn.execute("""
            SELECT *
            FROM brands
            ORDER BY name
        """).fetchall()

        stats = conn.execute("""
            SELECT
                COUNT(*) AS product_count,
                COALESCE(SUM(stock), 0) AS total_stock,
                COUNT(*) FILTER (WHERE stock = 0) AS out_of_stock,
                COUNT(*) FILTER (
                    WHERE stock > 0 AND stock <= min_stock
                ) AS low_stock
            FROM products
            WHERE is_active = TRUE
        """).fetchone()

        today_sales = conn.execute("""
            SELECT
                COUNT(*) AS invoice_count,
                COALESCE(SUM(final_amount), 0) AS total
            FROM sales
            WHERE status = 'completed'
              AND (created_at AT TIME ZONE 'Asia/Tehran')::date
                  = (NOW() AT TIME ZONE 'Asia/Tehran')::date
        """).fetchone()

    return render_template(
        "admin.html",
        categories=categories,
        products=products,
        brands=brands,
        stats=stats,
        today_sales=today_sales
    )


# ==================================================
# صفحه مستقل مدیریت محصولات
# ==================================================

@app.route("/admin/products")
@admin_required
def manage_products():

    with db() as conn:

        products = conn.execute("""
            SELECT
                p.*,
                c.name AS category_name,
                b.name AS brand_name
            FROM products p
            LEFT JOIN categories c
                ON c.id = p.category_id
            LEFT JOIN brands b
                ON b.id = p.brand_id
            WHERE p.is_active = TRUE
            ORDER BY p.created_at DESC
        """).fetchall()

    return render_template(
        "manage_products.html",
        products=products
    )


# ==================================================
# جست‌وجو و جایگزینی متن
# ==================================================

@app.route("/admin/find-replace", methods=["GET", "POST"])
@admin_required
def find_replace():

    allowed_scopes = {
        "product_name": (
            "products", "name", "نام محصول"
        ),
        "product_description": (
            "products", "description", "توضیحات محصول"
        ),
        "category_name": (
            "categories", "name", "نام دسته‌بندی"
        ),
        "brand_name": (
            "brands", "name", "نام برند"
        )
    }

    term = request.values.get("term", "").strip()
    replacement = request.values.get("replacement", "")
    scope = request.values.get("scope", "product_name")

    if scope not in allowed_scopes:
        scope = "product_name"

    table, column, scope_label = allowed_scopes[scope]

    results = []
    total_found = 0

    if (
        request.method == "POST"
        and request.form.get("action") == "apply"
    ):

        selected_ids = request.form.getlist("selected_ids")

        if not term:

            flash(
                "عبارت جست‌وجو نمی‌تواند خالی باشد.",
                "error"
            )

        elif not selected_ids:

            flash(
                "حداقل یک مورد را انتخاب کن.",
                "error"
            )

        else:

            try:

                if any(
                    not item.isdigit()
                    for item in selected_ids
                ):
                    raise ValueError

                ids = sorted(set(
                    int(item)
                    for item in selected_ids
                ))

                if not ids:
                    raise ValueError

                changed_count = 0

                with db() as conn:

                    current_rows = conn.execute(
                        f"""
                        SELECT id, {column} AS old_value
                        FROM {table}
                        WHERE id = ANY(%s)
                          AND POSITION(%s IN COALESCE({column}, '')) > 0
                        FOR UPDATE
                        """,
                        (ids, term)
                    ).fetchall()

                    for row in current_rows:

                        old_value = row["old_value"] or ""

                        new_value = old_value.replace(
                            term,
                            replacement
                        )

                        if new_value == old_value:
                            continue

                        conn.execute(
                            f"""
                            UPDATE {table}
                            SET {column} = %s
                            WHERE id = %s
                            """,
                            (new_value, row["id"])
                        )

                        changed_count += 1

                flash(
                    f"{changed_count} مورد با موفقیت اصلاح شد.",
                    "success"
                )

            except (ValueError, TypeError):

                flash(
                    "شناسه‌های انتخاب‌شده معتبر نیستند.",
                    "error"
                )

            except psycopg.errors.UniqueViolation:

                flash(
                    "این جایگزینی باعث تکراری شدن نام دسته‌بندی یا برند می‌شود. "
                    "تغییرات اعمال نشدند.",
                    "error"
                )

            except Exception:

                app.logger.exception(
                    "Find and replace failed"
                )

                flash(
                    "هنگام جایگزینی خطایی رخ داد. تغییرات ثبت نشدند.",
                    "error"
                )

        return redirect(url_for(
            "find_replace",
            term=term,
            replacement=replacement,
            scope=scope
        ))

    if term:

        with db() as conn:

            count_result = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM {table}
                WHERE POSITION(%s IN COALESCE({column}, '')) > 0
                """,
                (term,)
            ).fetchone()

            total_found = count_result["total"]

            results = conn.execute(
                f"""
                SELECT id, {column} AS old_value
                FROM {table}
                WHERE POSITION(%s IN COALESCE({column}, '')) > 0
                ORDER BY id
                LIMIT 200
                """,
                (term,)
            ).fetchall()

    return render_template(
        "find_replace.html",
        term=term,
        replacement=replacement,
        scope=scope,
        scope_label=scope_label,
        results=results,
        total_found=total_found
    )


# ==================================================
# دسته‌بندی‌ها
# ==================================================

@app.route("/admin/category/add", methods=["POST"])
@admin_required
def add_category():

    name = request.form.get("name", "").strip()

    if not name:

        flash("نام دسته‌بندی را وارد کنید.", "error")

        return redirect(url_for("admin"))

    try:

        with db() as conn:

            conn.execute(
                "INSERT INTO categories (name) VALUES (%s)",
                (name,)
            )

        flash("دسته‌بندی اضافه شد.", "success")

    except psycopg.errors.UniqueViolation:

        flash(
            "این دسته‌بندی قبلاً ثبت شده است.",
            "error"
        )

    return redirect(url_for("admin"))


@app.route(
    "/admin/category/<int:category_id>/edit",
    methods=["POST"]
)
@admin_required
def edit_category(category_id):

    name = request.form.get("name", "").strip()

    if not name:

        flash(
            "نام دسته‌بندی نمی‌تواند خالی باشد.",
            "error"
        )

        return redirect(url_for("admin"))

    try:

        with db() as conn:

            conn.execute(
                """
                UPDATE categories
                SET name = %s
                WHERE id = %s
                """,
                (name, category_id)
            )

        flash("دسته‌بندی ویرایش شد.", "success")

    except psycopg.errors.UniqueViolation:

        flash(
            "این نام قبلاً استفاده شده است.",
            "error"
        )

    return redirect(url_for("admin"))


@app.route(
    "/admin/category/<int:category_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_category(category_id):

    with db() as conn:

        count = conn.execute("""
            SELECT COUNT(*) AS total
            FROM products
            WHERE category_id = %s
        """, (category_id,)).fetchone()["total"]

        if count:

            flash(
                "ابتدا محصولات این دسته‌بندی را به دسته دیگری منتقل کنید.",
                "error"
            )

        else:

            conn.execute(
                "DELETE FROM categories WHERE id = %s",
                (category_id,)
            )

            flash(
                "دسته‌بندی حذف شد.",
                "success"
            )

    return redirect(url_for("admin"))


# ==================================================
# افزودن محصول
# ==================================================

@app.route("/admin/product/add", methods=["GET", "POST"])
@admin_required
def add_product():

    if request.method == "GET":

        with db() as conn:

            categories = conn.execute("""
                SELECT id, name
                FROM categories
                ORDER BY name
            """).fetchall()

            brands = conn.execute("""
                SELECT id, name
                FROM brands
                ORDER BY name
            """).fetchall()

        return render_template(
            "add_product.html",
            categories=categories,
            brands=brands
        )

    name = request.form.get("name", "").strip()

    description = request.form.get(
        "description", ""
    ).strip()

    image_url = (
        request.form.get("image_url", "").strip() or None
    )

    try:

        category_id = int(
            normalize_digits(request.form.get("category_id", ""))
        )

        price = int(
            normalize_digits(request.form.get("price", "0"))
        )

        stock = int(
            normalize_digits(request.form.get("stock", "0"))
        )

        min_stock = int(
            normalize_digits(request.form.get("min_stock", "2"))
        )

        brand_value = normalize_digits(
            request.form.get("brand_id", "").strip()
        )

        brand_id = (
            int(brand_value)
            if brand_value.isdigit()
            else None
        )

        if (
            not name
            or price < 0
            or stock < 0
            or min_stock < 0
            or category_id <= 0
        ):
            raise ValueError

        with db() as conn:

            category = conn.execute("""
                SELECT id
                FROM categories
                WHERE id = %s
            """, (category_id,)).fetchone()

            if not category:

                flash(
                    "دسته‌بندی انتخاب‌شده معتبر نیست.",
                    "error"
                )

                return redirect(url_for("add_product"))

            if brand_id is not None:

                brand = conn.execute("""
                    SELECT id
                    FROM brands
                    WHERE id = %s
                """, (brand_id,)).fetchone()

                if not brand:

                    flash(
                        "برند انتخاب‌شده معتبر نیست.",
                        "error"
                    )

                    return redirect(url_for("add_product"))

            conn.execute("""
                INSERT INTO products
                    (
                        name,
                        category_id,
                        brand_id,
                        description,
                        price,
                        image_url,
                        stock,
                        min_stock
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                name,
                category_id,
                brand_id,
                description,
                price,
                image_url,
                stock,
                min_stock
            ))

        flash(
            "محصول با موفقیت ثبت شد.",
            "success"
        )

        return redirect(url_for("manage_products"))

    except (ValueError, TypeError):

        flash(
            "اطلاعات محصول معتبر نیست.",
            "error"
        )

        return redirect(url_for("add_product"))

    except psycopg.errors.UniqueViolation:

        flash(
            "اطلاعات تکراری است.",
            "error"
        )

        return redirect(url_for("add_product"))

    except psycopg.errors.ForeignKeyViolation:

        flash(
            "دسته‌بندی یا برند انتخاب‌شده معتبر نیست.",
            "error"
        )

        return redirect(url_for("add_product"))


# ==================================================
# ویرایش محصول - صفحه مستقل
# ==================================================

@app.route(
    "/admin/product/<int:product_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def edit_product(product_id):

    if request.method == "GET":

        with db() as conn:

            product = conn.execute("""
                SELECT
                    p.*,
                    c.name AS category_name,
                    b.name AS brand_name
                FROM products p
                LEFT JOIN categories c
                    ON c.id = p.category_id
                LEFT JOIN brands b
                    ON b.id = p.brand_id
                WHERE p.id = %s
                  AND p.is_active = TRUE
            """, (product_id,)).fetchone()

            if not product:
                abort(404)

            categories = conn.execute("""
                SELECT id, name
                FROM categories
                ORDER BY name
            """).fetchall()

            brands = conn.execute("""
                SELECT id, name
                FROM brands
                ORDER BY name
            """).fetchall()

        return render_template(
            "edit_product.html",
            product=product,
            categories=categories,
            brands=brands
        )

    try:

        name = request.form.get(
            "name", ""
        ).strip()

        category_id = int(
            normalize_digits(request.form.get("category_id", ""))
        )

        price = int(
            normalize_digits(request.form.get("price", "0"))
        )

        min_stock = int(
            normalize_digits(request.form.get("min_stock", "2"))
        )

        brand_value = normalize_digits(
            request.form.get("brand_id", "").strip()
        )

        brand_id = (
            int(brand_value)
            if brand_value.isdigit()
            else None
        )

        description = request.form.get(
            "description", ""
        ).strip()

        image_url = (
            request.form.get(
                "image_url", ""
            ).strip() or None
        )

        if (
            not name
            or price < 0
            or min_stock < 0
            or category_id <= 0
        ):
            raise ValueError

        with db() as conn:

            category = conn.execute("""
                SELECT id
                FROM categories
                WHERE id = %s
            """, (category_id,)).fetchone()

            if not category:

                flash(
                    "دسته‌بندی انتخاب‌شده معتبر نیست.",
                    "error"
                )

                return redirect(
                    url_for(
                        "edit_product",
                        product_id=product_id
                    )
                )

            if brand_id is not None:

                brand = conn.execute("""
                    SELECT id
                    FROM brands
                    WHERE id = %s
                """, (brand_id,)).fetchone()

                if not brand:

                    flash(
                        "برند انتخاب‌شده معتبر نیست.",
                        "error"
                    )

                    return redirect(
                        url_for(
                            "edit_product",
                            product_id=product_id
                        )
                    )

            result = conn.execute("""
                UPDATE products
                SET
                    name = %s,
                    category_id = %s,
                    brand_id = %s,
                    price = %s,
                    min_stock = %s,
                    description = %s,
                    image_url = %s
                WHERE id = %s
                  AND is_active = TRUE
                RETURNING id
            """, (
                name,
                category_id,
                brand_id,
                price,
                min_stock,
                description,
                image_url,
                product_id
            )).fetchone()

            if not result:
                abort(404)

        flash(
            "اطلاعات محصول با موفقیت ذخیره شد.",
            "success"
        )

        return redirect(url_for("manage_products"))

    except (ValueError, TypeError):

        flash(
            "اطلاعات واردشده معتبر نیست.",
            "error"
        )

        return redirect(
            url_for(
                "edit_product",
                product_id=product_id
            )
        )

    except psycopg.errors.ForeignKeyViolation:

        flash(
            "دسته‌بندی یا برند انتخاب‌شده معتبر نیست.",
            "error"
        )

        return redirect(
            url_for(
                "edit_product",
                product_id=product_id
            )
        )


# ==================================================
# حذف محصول - غیرفعال‌سازی
# ==================================================

@app.route(
    "/admin/product/<int:product_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_product(product_id):

    with db() as conn:

        result = conn.execute("""
            UPDATE products
            SET is_active = FALSE
            WHERE id = %s
              AND is_active = TRUE
            RETURNING id
        """, (product_id,)).fetchone()

    if result:

        flash(
            "محصول از فهرست فعال خارج شد.",
            "success"
        )

    else:

        flash(
            "محصول پیدا نشد یا قبلاً غیرفعال شده است.",
            "error"
        )

    return redirect(url_for("manage_products"))

# ==================================================
# مدیریت برندها
# ==================================================

@app.route("/admin/brand/add", methods=["POST"])
@admin_required
def add_brand():

    name = request.form.get("name", "").strip()

    if not name:

        flash(
            "نام برند را وارد کنید.",
            "error"
        )

        return redirect(url_for("admin"))

    try:

        with db() as conn:

            conn.execute("""
                INSERT INTO brands (name)
                VALUES (%s)
            """, (name,))

        flash(
            "برند با موفقیت اضافه شد.",
            "success"
        )

    except psycopg.errors.UniqueViolation:

        flash(
            "این برند قبلاً ثبت شده است.",
            "error"
        )

    return redirect(url_for("admin"))


@app.route(
    "/admin/brand/<int:brand_id>/edit",
    methods=["POST"]
)
@admin_required
def edit_brand(brand_id):

    name = request.form.get("name", "").strip()

    if not name:

        flash(
            "نام برند نمی‌تواند خالی باشد.",
            "error"
        )

        return redirect(url_for("admin"))

    try:

        with db() as conn:

            result = conn.execute("""
                UPDATE brands
                SET name = %s
                WHERE id = %s
                RETURNING id
            """, (
                name,
                brand_id
            )).fetchone()

            if not result:

                flash(
                    "برند موردنظر پیدا نشد.",
                    "error"
                )

                return redirect(url_for("admin"))

        flash(
            "برند با موفقیت ویرایش شد.",
            "success"
        )

    except psycopg.errors.UniqueViolation:

        flash(
            "این نام برند قبلاً ثبت شده است.",
            "error"
        )

    return redirect(url_for("admin"))


@app.route(
    "/admin/brand/<int:brand_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_brand(brand_id):

    with db() as conn:

        count = conn.execute("""
            SELECT COUNT(*) AS total
            FROM products
            WHERE brand_id = %s
        """, (
            brand_id,
        )).fetchone()["total"]

        if count > 0:

            flash(
                "این برند به محصول متصل است. "
                "ابتدا برند محصولات مربوطه را تغییر دهید.",
                "error"
            )

            return redirect(url_for("admin"))

        result = conn.execute("""
            DELETE FROM brands
            WHERE id = %s
            RETURNING id
        """, (
            brand_id,
        )).fetchone()

        if result:

            flash(
                "برند با موفقیت حذف شد.",
                "success"
            )

        else:

            flash(
                "برند موردنظر پیدا نشد.",
                "error"
            )

    return redirect(url_for("admin"))


# ==================================================
# انبار
# ==================================================

@app.route(
    "/admin/inventory",
    methods=["GET", "POST"]
)
@admin_required
def inventory():

    if request.method == "POST":

        try:

            product_id = int(
                normalize_digits(
                    request.form.get("product_id", "")
                )
            )

            movement_type = request.form.get(
                "movement_type", ""
            )

            quantity = int(
                normalize_digits(
                    request.form.get("quantity", "0")
                )
            )

            note = request.form.get(
                "note", ""
            ).strip()

            if quantity <= 0:
                raise ValueError

            if movement_type not in (
                "in",
                "out",
                "adjustment"
            ):
                raise ValueError

            with db() as conn:

                product = conn.execute("""
                    SELECT id, stock
                    FROM products
                    WHERE id = %s
                      AND is_active = TRUE
                    FOR UPDATE
                """, (product_id,)).fetchone()

                if not product:

                    flash(
                        "محصول پیدا نشد.",
                        "error"
                    )

                    return redirect(
                        url_for("inventory")
                    )

                old_stock = product["stock"]

                if movement_type == "in":

                    new_stock = old_stock + quantity

                    movement_quantity = quantity

                elif movement_type == "out":

                    if quantity > old_stock:

                        flash(
                            "موجودی برای این خروج کافی نیست.",
                            "error"
                        )

                        return redirect(
                            url_for("inventory")
                        )

                    new_stock = old_stock - quantity

                    movement_quantity = quantity

                else:

                    new_stock = quantity

                    movement_quantity = (
                        new_stock - old_stock
                    )

                conn.execute("""
                    UPDATE products
                    SET stock = %s
                    WHERE id = %s
                """, (
                    new_stock,
                    product_id
                ))

                conn.execute("""
                    INSERT INTO stock_movements
                        (
                            product_id,
                            movement_type,
                            quantity,
                            note
                        )
                    VALUES (%s, %s, %s, %s)
                """, (
                    product_id,
                    movement_type,
                    movement_quantity,
                    note
                ))

            flash(
                "تغییر موجودی ثبت شد.",
                "success"
            )

        except (ValueError, TypeError):

            flash(
                "اطلاعات ورود یا خروج معتبر نیست.",
                "error"
            )

        return redirect(url_for("inventory"))

    with db() as conn:

        products = conn.execute("""
            SELECT
                id,
                name,
                stock,
                min_stock
            FROM products
            WHERE is_active = TRUE
            ORDER BY name
        """).fetchall()

        movements = conn.execute("""
            SELECT
                m.*,
                p.name AS product_name
            FROM stock_movements m
            JOIN products p ON p.id = m.product_id
            ORDER BY m.created_at DESC
            LIMIT 100
        """).fetchall()

    return render_template(
        "inventory.html",
        products=products,
        movements=movements
    )


# ==================================================
# اعتبارسنجی اطلاعات مشتری
# ==================================================

def validate_customer_data(form):

    first_name = form.get(
        "customer_first_name", ""
    ).strip()

    last_name = form.get(
        "customer_last_name", ""
    ).strip()

    phone = normalize_digits(
        form.get("customer_phone", "")
    )

    national_id = normalize_digits(
        form.get("customer_national_id", "")
    ).strip()

    if not first_name:
        raise ValueError("نام مشتری را وارد کنید.")

    if not last_name:
        raise ValueError("نام خانوادگی مشتری را وارد کنید.")

    # حذف فاصله و خط تیره و پرانتز از شماره تماس
    phone = re.sub(r"[\s\-\(\)]", "", phone)

    if not re.fullmatch(r"\+?\d{10,15}", phone):
        raise ValueError(
            "شماره تماس مشتری معتبر نیست."
        )

    # کد ملی اختیاری است
    if national_id:

        if not national_id.isdigit() or len(national_id) != 10:
            raise ValueError(
                "کد ملی باید دقیقاً ۱۰ رقم باشد."
            )

        # اعتبارسنجی رقم کنترل کد ملی ایران
        digits = [int(x) for x in national_id]

        if len(set(digits)) == 1:
            raise ValueError("کد ملی معتبر نیست.")

        checksum = sum(
            digits[i] * (10 - i)
            for i in range(9)
        ) % 11

        control = digits[9]

        expected = (
            checksum
            if checksum < 2
            else 11 - checksum
        )

        if control != expected:
            raise ValueError("کد ملی معتبر نیست.")

    return {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "national_id": national_id or None
    }


# ==================================================
# دفتر فروش
# ==================================================

@app.route(
    "/admin/sales",
    methods=["GET", "POST"]
)
@admin_required
def sales():

    if request.method == "POST":

        product_ids = request.form.getlist(
            "product_id"
        )

        quantities = request.form.getlist(
            "quantity"
        )

        discount_text = normalize_digits(
            request.form.get("discount", "0")
        ).strip()

        description = request.form.get(
            "description", ""
        ).strip()

        try:

            customer = validate_customer_data(
                request.form
            )

            discount = int(discount_text)

            if discount < 0:
                raise ValueError(
                    "مبلغ تخفیف معتبر نیست."
                )

            if len(product_ids) != len(quantities):
                raise ValueError(
                    "اطلاعات ردیف‌های فاکتور ناقص است."
                )

            grouped_items = {}

            for raw_pid, raw_qty in zip(
                product_ids,
                quantities
            ):

                raw_pid = normalize_digits(
                    raw_pid.strip()
                )

                raw_qty = normalize_digits(
                    raw_qty.strip()
                )

                if not raw_pid and not raw_qty:
                    continue

                if not raw_pid or not raw_qty:
                    raise ValueError(
                        "برای هر ردیف فاکتور، محصول و تعداد را وارد کنید."
                    )

                pid = int(raw_pid)
                qty = int(raw_qty)

                if pid <= 0 or qty <= 0:
                    raise ValueError(
                        "محصول یا تعداد معتبر نیست."
                    )

                grouped_items[pid] = (
                    grouped_items.get(pid, 0) + qty
                )

            if not grouped_items:
                raise ValueError(
                    "حداقل یک محصول به فاکتور اضافه کنید."
                )

            items = []
            total_amount = 0

            with db() as conn:

                locked_products = {}

                for pid in sorted(grouped_items.keys()):

                    product = conn.execute("""
                        SELECT
                            id,
                            name,
                            price,
                            stock
                        FROM products
                        WHERE id = %s
                          AND is_active = TRUE
                        FOR UPDATE
                    """, (pid,)).fetchone()

                    if not product:
                        raise ValueError(
                            f"محصول با شناسه {pid} پیدا نشد."
                        )

                    requested_qty = grouped_items[pid]

                    if product["stock"] < requested_qty:
                        raise ValueError(
                            f"موجودی محصول «{product['name']}» کافی نیست. "
                            f"موجودی فعلی: {product['stock']}، "
                            f"تعداد درخواستی: {requested_qty}"
                        )

                    locked_products[pid] = product

                for pid, requested_qty in grouped_items.items():

                    product = locked_products[pid]

                    line_total = (
                        product["price"] * requested_qty
                    )

                    total_amount += line_total

                    items.append({
                        "id": pid,
                        "name": product["name"],
                        "quantity": requested_qty,
                        "price": product["price"],
                        "line_total": line_total
                    })

                if discount > total_amount:
                    raise ValueError(
                        "تخفیف نمی‌تواند از مبلغ کل فاکتور بیشتر باشد."
                    )

                final_amount = total_amount - discount

                # ثبت فاکتور همراه با مشخصات مشتری
                sale = conn.execute("""
                    INSERT INTO sales
                        (
                            total_amount,
                            discount,
                            final_amount,
                            description,
                            customer_first_name,
                            customer_last_name,
                            customer_phone,
                            customer_national_id
                        )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, invoice_number
                """, (
                    total_amount,
                    discount,
                    final_amount,
                    description,
                    customer["first_name"],
                    customer["last_name"],
                    customer["phone"],
                    customer["national_id"]
                )).fetchone()

                for item in items:

                    conn.execute("""
                        INSERT INTO sale_items
                            (
                                sale_id,
                                product_id,
                                quantity,
                                unit_price,
                                line_total
                            )
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        sale["id"],
                        item["id"],
                        item["quantity"],
                        item["price"],
                        item["line_total"]
                    ))

                    conn.execute("""
                        UPDATE products
                        SET stock = stock - %s
                        WHERE id = %s
                    """, (
                        item["quantity"],
                        item["id"]
                    ))

                    conn.execute("""
                        INSERT INTO stock_movements
                            (
                                product_id,
                                movement_type,
                                quantity,
                                note
                            )
                        VALUES (%s, 'sale', %s, %s)
                    """, (
                        item["id"],
                        item["quantity"],
                        f"فروش فاکتور {sale['invoice_number']}"
                    ))

            flash(
                f"فاکتور شماره {sale['invoice_number']} با موفقیت ثبت شد.",
                "success"
            )

        except (ValueError, TypeError) as error:

            flash(
                str(error) or "اطلاعات فاکتور معتبر نیست.",
                "error"
            )

        return redirect(url_for("sales"))

    with db() as conn:

        products = conn.execute("""
            SELECT
                id,
                name,
                price,
                stock
            FROM products
            WHERE is_active = TRUE
            ORDER BY name
        """).fetchall()

        invoices = conn.execute("""
            SELECT *
            FROM sales
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()

    return render_template(
        "sales.html",
        products=products,
        invoices=invoices
    )


# ==================================================
# جزئیات فاکتور
# ==================================================

@app.route("/admin/sales/<int:sale_id>")
@admin_required
def sale_detail(sale_id):

    with db() as conn:

        sale = conn.execute("""
            SELECT *
            FROM sales
            WHERE id = %s
        """, (sale_id,)).fetchone()

        if not sale:
            abort(404)

        items = conn.execute("""
            SELECT
                si.*,
                p.name AS product_name
            FROM sale_items si
            JOIN products p ON p.id = si.product_id
            WHERE si.sale_id = %s
        """, (sale_id,)).fetchall()

    return render_template(
        "sale_detail.html",
        sale=sale,
        items=items
    )


# ==================================================
# لغو فاکتور و بازگشت موجودی
# ==================================================

@app.route(
    "/admin/sales/<int:sale_id>/cancel",
    methods=["POST"]
)
@admin_required
def cancel_sale(sale_id):

    with db() as conn:

        sale = conn.execute("""
            SELECT *
            FROM sales
            WHERE id = %s
            FOR UPDATE
        """, (sale_id,)).fetchone()

        if not sale:
            abort(404)

        if sale["status"] == "cancelled":

            flash(
                "این فاکتور قبلاً لغو شده است.",
                "error"
            )

            return redirect(url_for("sales"))

        items = conn.execute("""
            SELECT
                product_id,
                quantity
            FROM sale_items
            WHERE sale_id = %s
            ORDER BY product_id
        """, (sale_id,)).fetchall()

        for item in items:

            conn.execute("""
                UPDATE products
                SET stock = stock + %s
                WHERE id = %s
            """, (
                item["quantity"],
                item["product_id"]
            ))

            conn.execute("""
                INSERT INTO stock_movements
                    (
                        product_id,
                        movement_type,
                        quantity,
                        note
                    )
                VALUES (%s, 'return', %s, %s)
            """, (
                item["product_id"],
                item["quantity"],
                f"برگشت از فاکتور {sale['invoice_number']}"
            ))

        conn.execute("""
            UPDATE sales
            SET status = 'cancelled'
            WHERE id = %s
        """, (sale_id,))

    flash(
        "فاکتور لغو شد و موجودی کالاها برگشت.",
        "success"
    )

    return redirect(url_for("sales"))


# ==================================================
# حذف فاکتور و بازگشت موجودی
# ==================================================

@app.route(
    "/admin/sales/<int:sale_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_sale(sale_id):

    with db() as conn:

        sale = conn.execute("""
            SELECT *
            FROM sales
            WHERE id = %s
            FOR UPDATE
        """, (sale_id,)).fetchone()

        if not sale:
            abort(404)

        invoice_number = sale["invoice_number"]

        items = conn.execute("""
            SELECT
                product_id,
                quantity
            FROM sale_items
            WHERE sale_id = %s
            ORDER BY product_id
        """, (sale_id,)).fetchall()

        if sale["status"] == "completed":

            for item in items:

                product = conn.execute("""
                    SELECT id
                    FROM products
                    WHERE id = %s
                    FOR UPDATE
                """, (item["product_id"],)).fetchone()

                if not product:
                    raise ValueError(
                        "یکی از محصولات فاکتور پیدا نشد."
                    )

                conn.execute("""
                    UPDATE products
                    SET stock = stock + %s
                    WHERE id = %s
                """, (
                    item["quantity"],
                    item["product_id"]
                ))

                conn.execute("""
                    INSERT INTO stock_movements
                        (
                            product_id,
                            movement_type,
                            quantity,
                            note
                        )
                    VALUES (%s, 'return', %s, %s)
                """, (
                    item["product_id"],
                    item["quantity"],
                    f"بازگشت کالا بابت حذف فاکتور {invoice_number}"
                ))

        conn.execute("""
            DELETE FROM stock_movements
            WHERE note IN (%s, %s)
        """, (
            f"فروش فاکتور {invoice_number}",
            f"برگشت از فاکتور {invoice_number}"
        ))

        conn.execute("""
            DELETE FROM sale_items
            WHERE sale_id = %s
        """, (sale_id,))

        conn.execute("""
            DELETE FROM sales
            WHERE id = %s
        """, (sale_id,))

    flash(
        f"فاکتور شماره {invoice_number} حذف شد. "
        "موجودی کالاهای فاکتور تکمیل‌شده به انبار برگشت.",
        "success"
    )

    return redirect(url_for("sales"))




# ==================================================
# پیامک اقساط - تنظیمات و زمان‌بندی
# ==================================================
GHASEDAK_URL = "https://gateway.ghasedak.me/rest/api/v1/WebService/SendSingleSMS"


def get_sms_settings():
    with db() as conn:
        return conn.execute("SELECT * FROM sms_settings WHERE id=1").fetchone()


def sms_fill(template, values):
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template[:1000]


def send_installment_sms(plan_id, payment_id, kind, receptor, message):
    with db() as conn:
        existing = conn.execute(
            """SELECT id, status, created_at
               FROM installment_sms_logs
               WHERE plan_id=%s AND COALESCE(payment_id, 0)=COALESCE(%s, 0)
                 AND kind=%s
               FOR UPDATE""",
            (plan_id, payment_id, kind)
        ).fetchone()
        if existing:
            age = (tehran_now() - existing["created_at"].astimezone(TEHRAN)).total_seconds() if existing["created_at"].tzinfo else 0
            if existing["status"] == "sent" or (
                existing["status"] == "processing" and age < 900
            ):
                return False
            log_id = existing["id"]
            conn.execute(
                """UPDATE installment_sms_logs
                   SET status='processing', receptor=%s, message=%s,
                       error_text='', created_at=NOW()
                   WHERE id=%s""",
                (receptor, message, log_id)
            )
        else:
            log = conn.execute(
                """INSERT INTO installment_sms_logs
                   (plan_id, payment_id, kind, receptor, message, status)
                   VALUES (%s, %s, %s, %s, %s, 'processing')
                   ON CONFLICT DO NOTHING RETURNING id""",
                (plan_id, payment_id, kind, receptor, message)
            ).fetchone()
            if not log:
                return False
            log_id = log["id"]

    api_key = os.environ.get("GHASEDAK_API_KEY", "").strip()
    if not api_key:
        error_text = "GHASEDAK_API_KEY در متغیرهای محرمانه هاست تنظیم نشده است."
        with db() as conn:
            conn.execute(
                "UPDATE installment_sms_logs SET status='failed', error_text=%s WHERE id=%s",
                (error_text, log_id)
            )
        return False

    settings = get_sms_settings()
    # sendDate اختیاری است؛ برای ارسال فوری باید حذف شود، نه رشته خالی.
    payload = {
        "message": message,
        "lineNumber": str(settings["line_number"] or "").strip(),
        "receptor": str(receptor).strip(),
        "clientReferenceId": str(uuid.uuid4()),
        "udh": False
    }
    if not payload["lineNumber"]:
        payload.pop("lineNumber")
    req = urllib.request.Request(
        GHASEDAK_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"ApiKey": api_key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(100000).decode("utf-8", errors="replace")
        result = json.loads(raw) if raw else {}
        success = (
            result.get("IsSuccess") is True
            or str(result.get("IsSuccess", "")).lower() == "true"
            or str(result.get("StatusCode", "")) == "200"
        )
        if not success:
            raise RuntimeError(str(result.get("Message") or result.get("message") or "پاسخ ناموفق سرویس پیامک"))
        provider_id = str(result.get("MessageId") or "")
        with db() as conn:
            conn.execute(
                """UPDATE installment_sms_logs
                   SET status='sent', provider_message_id=%s, sent_at=NOW(), error_text=''
                   WHERE id=%s""",
                (provider_id, log_id)
            )
        return True
    except Exception as exc:
        error_detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            try:
                provider_body = exc.read(4000).decode("utf-8", errors="replace").strip()
                if provider_body:
                    error_detail = f"HTTP {exc.code}: {provider_body}"
                else:
                    error_detail = f"HTTP {exc.code}: {exc.reason}"
            except Exception:
                error_detail = f"HTTP {exc.code}: {exc.reason}"
        with db() as conn:
            conn.execute(
                "UPDATE installment_sms_logs SET status='failed', error_text=%s WHERE id=%s",
                (error_detail[:1000], log_id)
            )
        app.logger.exception("Ghasedak SMS send failed")
        return False

def send_installment_welcome(plan_id, name, phone, total, count, parsed):
    settings = get_sms_settings()
    if not settings or not settings["enabled"] or not settings["welcome_enabled"]:
        return
    first_due = min(item[1] for item in parsed)
    last_due = max(item[1] for item in parsed)
    message = sms_fill(settings["welcome_template"], {
        "customer_name": name,
        "total_amount": f"{total:,}",
        "installment_count": count,
        "first_due_date": jalali_day(first_due),
        "last_due_date": jalali_day(last_due),
        "brand": "لوازم خانگی بولحسنی"
    })
    send_installment_sms(plan_id, None, "welcome", phone, message)


@app.route("/internal/sms/run", methods=["POST"])
def run_installment_sms_reminders():
    secret = os.environ.get("SMS_CRON_SECRET", "").strip()
    if len(secret) < 32 or not hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + secret
    ):
        abort(404)

    settings = get_sms_settings()
    if not settings or not settings["enabled"] or not settings["reminder_enabled"]:
        return {"ok": True, "sent": 0, "message": "reminders disabled"}

    now = tehran_now()
    if (now.hour, now.minute) < (int(settings["send_hour"]), int(settings["send_minute"])):
        return {"ok": True, "sent": 0, "message": "waiting for configured Tehran time"}

    target_date = now.date() + timedelta(days=1)
    with db() as conn:
        due_rows = conn.execute(
            """SELECT p.id AS plan_id, p.customer_name, p.customer_phone,
                      i.id AS payment_id, i.installment_no, i.amount, i.due_date
               FROM installment_payments i
               JOIN installment_plans p ON p.id=i.plan_id
               WHERE i.due_date=%s AND i.paid_at IS NULL
               ORDER BY i.id LIMIT 500""",
            (target_date,)
        ).fetchall()

    sent_count = 0
    for item in due_rows:
        message = sms_fill(settings["reminder_template"], {
            "customer_name": item["customer_name"],
            "installment_no": item["installment_no"],
            "amount": f'{int(item["amount"]):,}',
            "due_date": jalali_day(item["due_date"]),
            "brand": "لوازم خانگی بولحسنی"
        })
        if send_installment_sms(
            item["plan_id"], item["payment_id"], "reminder",
            item["customer_phone"], message
        ):
            sent_count += 1
    return {"ok": True, "sent": sent_count, "checked": len(due_rows)}


@app.route("/internal/price-alerts/run", methods=["POST"])
def run_price_alert_notifications():
    secret = os.environ.get("SMS_CRON_SECRET", "").strip()
    if len(secret) < 32 or not hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + secret
    ):
        abort(404)
    try:
        return {"ok": True, **run_price_alerts(db)}
    except Exception:
        app.logger.exception("Price alert worker failed")
        return {"ok": False, "error": "worker_failed"}, 500


@app.route("/admin/sms", methods=["GET", "POST"])
@admin_required
def installment_sms_settings():
    if request.method == "POST":
        try:
            enabled = request.form.get("enabled") == "on"
            welcome_enabled = request.form.get("welcome_enabled") == "on"
            reminder_enabled = request.form.get("reminder_enabled") == "on"
            hour = int(request.form.get("send_hour", "9"))
            minute = int(request.form.get("send_minute", "0"))
            line_number = normalize_digits(request.form.get("line_number", "")).strip()
            welcome_template = request.form.get("welcome_template", "").strip()
            reminder_template = request.form.get("reminder_template", "").strip()
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
            if not line_number or len(line_number) > 30:
                raise ValueError
            if not welcome_template or not reminder_template or len(welcome_template) > 1000 or len(reminder_template) > 1000:
                raise ValueError
            with db() as conn:
                conn.execute(
                    """UPDATE sms_settings SET enabled=%s, welcome_enabled=%s,
                       reminder_enabled=%s, send_hour=%s, send_minute=%s,
                       line_number=%s, welcome_template=%s, reminder_template=%s
                       WHERE id=1""",
                    (enabled, welcome_enabled, reminder_enabled, hour, minute,
                     line_number, welcome_template, reminder_template)
                )
            flash("تنظیمات پیامک ذخیره شد.", "success")
            return redirect(url_for("installment_sms_settings"))
        except (ValueError, TypeError):
            flash("ساعت یا متن پیامک معتبر نیست.", "error")

    with db() as conn:
        settings = conn.execute("SELECT * FROM sms_settings WHERE id=1").fetchone()
        logs = conn.execute(
            """SELECT l.*, p.customer_name
               FROM installment_sms_logs l
               LEFT JOIN installment_plans p ON p.id=l.plan_id
               ORDER BY l.created_at DESC LIMIT 50"""
        ).fetchall()
    return render_template(
        "installment_sms.html", settings=settings, logs=logs,
        api_configured=bool(os.environ.get("GHASEDAK_API_KEY")),
        cron_configured=len(os.environ.get("SMS_CRON_SECRET", "").strip()) >= 32
    )


@app.route("/admin/sms/test", methods=["POST"])
@admin_required
def installment_sms_test():
    phone = normalize_digits(request.form.get("test_phone", "")).strip()
    if not phone or len(phone) < 7 or len(phone) > 20:
        flash("شماره موبایل برای تست معتبر نیست.", "error")
        return redirect(url_for("installment_sms_settings"))

    message = "این یک پیامک آزمایشی از پنل اقساط لوازم خانگی بولحسنی است."
    sent = send_installment_sms(None, None, "welcome", phone, message)
    if sent:
        flash("درخواست ارسال پیامک آزمایشی با موفقیت به پنل قاصدک ارسال شد. دریافت نهایی را روی گوشی بررسی کنید.", "success")
    else:
        flash("ارسال پیامک آزمایشی ناموفق بود؛ علت را در سوابق پیامک، ستون خطا بررسی کنید.", "error")
    return redirect(url_for("installment_sms_settings"))


# ==================================================
# مدیریت اقساط
# ==================================================
@app.route("/admin/installments", methods=["GET", "POST"])
@admin_required
def installments():
    if request.method == "POST":
        name=request.form.get("customer_name","").strip()
        phone=normalize_digits(request.form.get("customer_phone","")).strip()
        notes=request.form.get("notes","").strip()
        try:
            total=int(normalize_digits(request.form.get("total_amount","0")).replace(",",""))
            count=int(normalize_digits(request.form.get("installment_count","0")))
            amounts=request.form.getlist("installment_amount[]")
            dates=request.form.getlist("due_date[]")
            if not name or not phone or len(phone)<7 or len(phone)>20:
                raise ValueError
            if total<=0 or count<1 or count>120 or len(amounts)!=count or len(dates)!=count:
                raise ValueError
            parsed=[]
            for a,d in zip(amounts,dates):
                amount=int(normalize_digits(a).replace(",",""))
                # تاریخ از فرم به‌صورت شمسی (YYYY/MM/DD) دریافت می‌شود.
                jalali_due = jdatetime.datetime.strptime(
                    normalize_digits(d).replace("-", "/"), "%Y/%m/%d"
                ).date()
                due = jalali_due.togregorian()
                if amount<=0: raise ValueError
                parsed.append((amount,due))
            if sum(x[0] for x in parsed)!=total or parsed!=sorted(parsed,key=lambda x:x[1]):
                raise ValueError
            with db() as conn:
                plan=conn.execute("""INSERT INTO installment_plans
                    (customer_name,customer_phone,total_amount,installment_count,notes)
                    VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (name,phone,total,count,notes)).fetchone()
                for no,(amount,due) in enumerate(parsed,1):
                    conn.execute("""INSERT INTO installment_payments
                        (plan_id,installment_no,amount,due_date) VALUES (%s,%s,%s,%s)""",
                        (plan["id"],no,amount,due))
            send_installment_welcome(plan["id"], name, phone, total, count, parsed)
            flash("قرارداد ثبت شد. پیام تشکر در صورت فعال بودن پیامک ارسال شد.", "success")
            return redirect(url_for("installment_detail",plan_id=plan["id"]))
        except (ValueError,TypeError):
            flash("اطلاعات نامعتبر است؛ جمع اقساط باید برابر مبلغ کل باشد و تاریخ‌ها به ترتیب باشند.","error")
    with db() as conn:
        plans=conn.execute("""SELECT p.*,
            COALESCE(SUM(i.amount) FILTER (WHERE i.paid_at IS NOT NULL),0) AS paid_amount,
            COUNT(i.id) FILTER (WHERE i.paid_at IS NOT NULL) AS paid_count,
            COUNT(i.id) FILTER (WHERE i.paid_at IS NULL AND i.due_date<CURRENT_DATE) AS overdue_count
            FROM installment_plans p LEFT JOIN installment_payments i ON i.plan_id=p.id
            GROUP BY p.id ORDER BY p.created_at DESC""").fetchall()
    return render_template("installments.html",plans=plans)


@app.route("/admin/installments/<int:plan_id>")
@admin_required
def installment_detail(plan_id):
    with db() as conn:
        plan=conn.execute("""SELECT p.*,
            COALESCE(SUM(i.amount) FILTER (WHERE i.paid_at IS NOT NULL),0) AS paid_amount
            FROM installment_plans p LEFT JOIN installment_payments i ON i.plan_id=p.id
            WHERE p.id=%s GROUP BY p.id""",(plan_id,)).fetchone()
        if not plan: abort(404)
        rows=conn.execute("SELECT * FROM installment_payments WHERE plan_id=%s ORDER BY installment_no",(plan_id,)).fetchall()
    return render_template("installment_detail.html",plan=plan,installments=rows)




@app.route("/admin/installments/<int:plan_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_installment_plan(plan_id):
    with db() as conn:
        plan = conn.execute(
            "SELECT * FROM installment_plans WHERE id=%s",
            (plan_id,)
        ).fetchone()
        if not plan:
            abort(404)
        payments = conn.execute(
            "SELECT * FROM installment_payments WHERE plan_id=%s ORDER BY installment_no",
            (plan_id,)
        ).fetchall()

    if request.method == "POST":
        name = request.form.get("customer_name", "").strip()
        phone = normalize_digits(request.form.get("customer_phone", "")).strip()
        notes = request.form.get("notes", "").strip()
        try:
            total = int(normalize_digits(request.form.get("total_amount", "0")).replace(",", ""))
            count = int(normalize_digits(request.form.get("installment_count", "0")))
            amounts = request.form.getlist("installment_amount[]")
            dates = request.form.getlist("due_date[]")
            if not name or not phone or len(phone) < 7 or len(phone) > 20:
                raise ValueError
            if total <= 0 or count < 1 or count > 120 or len(amounts) != count or len(dates) != count:
                raise ValueError

            old_by_no = {int(p["installment_no"]): p for p in payments}
            # تعداد اقساط پس از ثبت قرارداد ثابت می‌ماند تا سوابق پرداخت حذف یا جابه‌جا نشوند.
            if count != len(payments):
                raise ValueError

            parsed = []
            for idx, (amount_raw, date_raw) in enumerate(zip(amounts, dates), 1):
                old = old_by_no.get(idx)
                if old and old["paid_at"] is not None:
                    amount = int(old["amount"])
                    due = old["due_date"]
                else:
                    amount = int(normalize_digits(amount_raw).replace(",", ""))
                    jalali_due = jdatetime.datetime.strptime(
                        normalize_digits(date_raw).replace("-", "/"), "%Y/%m/%d"
                    ).date()
                    due = jalali_due.togregorian()
                if amount <= 0:
                    raise ValueError
                parsed.append((amount, due))

            if sum(x[0] for x in parsed) != total or parsed != sorted(parsed, key=lambda x: x[1]):
                raise ValueError

            with db() as conn:
                locked = conn.execute(
                    "SELECT id FROM installment_plans WHERE id=%s FOR UPDATE",
                    (plan_id,)
                ).fetchone()
                if not locked:
                    abort(404)
                current = conn.execute(
                    "SELECT * FROM installment_payments WHERE plan_id=%s ORDER BY installment_no FOR UPDATE",
                    (plan_id,)
                ).fetchall()
                has_paid = any(p["paid_at"] is not None for p in current)
                if count != len(current):
                    raise ValueError

                conn.execute(
                    """UPDATE installment_plans
                       SET customer_name=%s, customer_phone=%s, total_amount=%s,
                           installment_count=%s, notes=%s
                       WHERE id=%s""",
                    (name, phone, total, count, notes, plan_id)
                )

                for no, (amount, due) in enumerate(parsed, 1):
                    old = next((p for p in current if int(p["installment_no"]) == no), None)
                    if old and old["paid_at"] is not None:
                        continue
                    conn.execute(
                        """UPDATE installment_payments
                           SET amount=%s, due_date=%s
                           WHERE plan_id=%s AND installment_no=%s""",
                        (amount, due, plan_id, no)
                    )

            flash("قرارداد با موفقیت ویرایش شد.", "success")
            return redirect(url_for("installment_detail", plan_id=plan_id))
        except (ValueError, TypeError):
            flash(
                "اطلاعات نامعتبر است. جمع اقساط باید برابر مبلغ کل باشد و تاریخ‌ها به ترتیب باشند. "
                "در صورت ثبت پرداخت، تعداد اقساط قابل تغییر نیست.",
                "error"
            )
            with db() as conn:
                plan = conn.execute(
                    "SELECT * FROM installment_plans WHERE id=%s", (plan_id,)
                ).fetchone()
                payments = conn.execute(
                    "SELECT * FROM installment_payments WHERE plan_id=%s ORDER BY installment_no",
                    (plan_id,)
                ).fetchall()

    return render_template(
        "edit_installment.html",
        plan=plan,
        installments=payments,
        has_paid=any(p["paid_at"] is not None for p in payments)
    )


@app.route("/admin/installments/<int:plan_id>/delete", methods=["POST"])
@admin_required
def delete_installment_plan(plan_id):
    with db() as conn:
        plan = conn.execute(
            "SELECT id, customer_name FROM installment_plans WHERE id=%s FOR UPDATE",
            (plan_id,)
        ).fetchone()
        if not plan:
            abort(404)
        conn.execute("DELETE FROM installment_payments WHERE plan_id=%s", (plan_id,))
        conn.execute("DELETE FROM installment_plans WHERE id=%s", (plan_id,))
    flash("قرارداد و سوابق اقساط آن حذف شد.", "success")
    return redirect(url_for("installments"))


@app.route("/admin/installments/payment/<int:payment_id>/toggle",methods=["POST"])
@admin_required
def toggle_installment_payment(payment_id):
    with db() as conn:
        row=conn.execute("SELECT id,plan_id,paid_at FROM installment_payments WHERE id=%s FOR UPDATE",(payment_id,)).fetchone()
        if not row: abort(404)
        if row["paid_at"] is None:
            conn.execute("UPDATE installment_payments SET paid_at=NOW() WHERE id=%s",(payment_id,))
            flash("پرداخت قسط ثبت شد.","success")
        else:
            conn.execute("UPDATE installment_payments SET paid_at=NULL WHERE id=%s",(payment_id,))
            flash("وضعیت قسط به پرداخت‌نشده تغییر کرد.","success")
        pid=row["plan_id"]
    return redirect(url_for("installment_detail",plan_id=pid))


# ==================================================
# گزارش فروش
# ==================================================

@app.route("/admin/reports")
@admin_required
def reports():

    with db() as conn:

        summary = conn.execute("""
            SELECT
                COUNT(*) AS invoice_count,
                COALESCE(SUM(final_amount), 0) AS total_sales
            FROM sales
            WHERE status = 'completed'
        """).fetchone()

        daily = conn.execute("""
            SELECT
                (created_at AT TIME ZONE 'Asia/Tehran')::date
                    AS sale_date,
                COUNT(*) AS invoice_count,
                SUM(final_amount) AS total
            FROM sales
            WHERE status = 'completed'
            GROUP BY sale_date
            ORDER BY sale_date DESC
            LIMIT 30
        """).fetchall()

        best_products = conn.execute("""
            SELECT
                p.name,
                SUM(si.quantity) AS sold_quantity,
                SUM(si.line_total) AS total
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN products p ON p.id = si.product_id
            WHERE s.status = 'completed'
            GROUP BY p.id, p.name
            ORDER BY sold_quantity DESC
            LIMIT 10
        """).fetchall()

    return render_template(
        "reports.html",
        summary=summary,
        daily=daily,
        best_products=best_products
    )


# ==================================================
# صفحات خطا
# ==================================================

@app.errorhandler(400)
def bad_request(error):

    return render_template(
        "error.html",
        message=getattr(
            error,
            "description",
            "درخواست نامعتبر است."
        )
    ), 400


@app.errorhandler(404)
def not_found(error):

    return render_template(
        "error.html",
        message="صفحه موردنظر پیدا نشد."
    ), 404


@app.errorhandler(500)
def server_error(error):

    return render_template(
        "error.html",
        message="خطای داخلی سرور رخ داده است."
    ), 500


# ==================================================
# اجرای برنامه
# ==================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
    
