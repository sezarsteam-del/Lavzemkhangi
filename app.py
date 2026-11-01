import os
import secrets
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-before-deploying"
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured. Add your Neon connection string."
        )

    dsn = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )

    return psycopg.connect(
        dsn,
        row_factory=dict_row,
        connect_timeout=10,
        sslmode="require"
    )


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                category_id INTEGER NOT NULL
                    REFERENCES categories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price BIGINT NOT NULL CHECK (price >= 0),
                image_url TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)


@app.before_request
def prepare_database():
    if DATABASE_URL:
        init_db()


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(
                url_for("login", next=request.path)
            )

        return fn(*args, **kwargs)

    return wrapper


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)

    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        supplied = request.form.get("_csrf", "")
        expected = session.get("_csrf", "")

        if not expected or not secrets.compare_digest(
            supplied, expected
        ):
            abort(
                400,
                "درخواست نامعتبر است. صفحه را تازه‌سازی کنید."
            )


# صفحه اصلی فروشگاه
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    category_id = request.args.get("category", type=int)

    with db() as conn:

        categories = conn.execute("""
            SELECT id, name
            FROM categories
            ORDER BY name
        """).fetchall()

        sql = """
            SELECT
                p.id,
                p.name,
                p.description,
                p.price,
                p.image_url,
                c.name AS category_name,
                c.id AS category_id
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE (
                %s = ''
                OR p.name ILIKE %s
                OR p.description ILIKE %s
                OR c.name ILIKE %s
            )
        """

        pattern = f"%{q}%"

        params = [
            q,
            pattern,
            pattern,
            pattern
        ]

        # فیلتر دسته‌بندی فقط در صورت انتخاب کاربر
        if category_id is not None:
            sql += " AND c.id = %s"
            params.append(category_id)

        sql += """
            ORDER BY p.created_at DESC, p.id DESC
        """

        products = conn.execute(
            sql,
            params
        ).fetchall()

    return render_template(
        "index.html",
        categories=categories,
        products=products,
        q=q,
        selected_category=category_id
    )


# ورود مدیر
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if (
            ADMIN_PASSWORD
            and secrets.compare_digest(username, ADMIN_USERNAME)
            and secrets.compare_digest(password, ADMIN_PASSWORD)
        ):

            session.clear()
            session["admin"] = True
            session["_csrf"] = secrets.token_urlsafe(32)

            return redirect(url_for("admin"))

        flash(
            "نام کاربری یا رمز عبور اشتباه است.",
            "error"
        )

    return render_template("login.html")


# خروج مدیر
@app.route("/logout", methods=["POST"])
@admin_required
def logout():

    session.clear()

    return redirect(url_for("index"))


# پنل مدیریت
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
            GROUP BY c.id
            ORDER BY c.name
        """).fetchall()

        products = conn.execute("""
            SELECT
                p.id,
                p.name,
                p.price,
                p.description,
                p.image_url,
                p.category_id,
                c.name AS category_name
            FROM products p
            JOIN categories c
                ON c.id = p.category_id
            ORDER BY p.id DESC
        """).fetchall()

    return render_template(
        "admin.html",
        categories=categories,
        products=products
    )


# افزودن دسته‌بندی
@app.route("/admin/categories/add", methods=["POST"])
@admin_required
def add_category():

    name = request.form.get("name", "").strip()

    if not name:
        flash(
            "نام دسته‌بندی را وارد کنید.",
            "error"
        )

    else:
        try:
            with db() as conn:
                conn.execute(
                    "INSERT INTO categories (name) VALUES (%s)",
                    (name,)
                )

            flash(
                "دسته‌بندی اضافه شد.",
                "success"
            )

        except psycopg.errors.UniqueViolation:
            flash(
                "این دسته‌بندی قبلاً ثبت شده است.",
                "error"
            )

    return redirect(url_for("admin"))


# ویرایش دسته‌بندی
@app.route(
    "/admin/categories/<int:category_id>/edit",
    methods=["POST"]
)
@admin_required
def edit_category(category_id):

    name = request.form.get("name", "").strip()

    if name:
        try:
            with db() as conn:
                conn.execute(
                    "UPDATE categories SET name=%s WHERE id=%s",
                    (name, category_id)
                )

            flash(
                "دسته‌بندی ویرایش شد.",
                "success"
            )

        except psycopg.errors.UniqueViolation:
            flash(
                "این نام قبلاً استفاده شده است.",
                "error"
            )

    return redirect(url_for("admin"))


# حذف دسته‌بندی
@app.route(
    "/admin/categories/<int:category_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_category(category_id):

    with db() as conn:
        conn.execute(
            "DELETE FROM categories WHERE id=%s",
            (category_id,)
        )

    flash(
        "دسته‌بندی و محصولات داخل آن حذف شدند.",
        "success"
    )

    return redirect(url_for("admin"))


# افزودن محصول
@app.route("/admin/products/add", methods=["POST"])
@admin_required
def add_product():

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    image_url = request.form.get("image_url", "").strip()

    category_id = request.form.get(
        "category_id",
        type=int
    )

    price = request.form.get(
        "price",
        type=int
    )

    if (
        not name
        or not category_id
        or price is None
        or price < 0
    ):

        flash(
            "نام، دسته‌بندی و قیمت معتبر الزامی است.",
            "error"
        )

    else:

        with db() as conn:
            conn.execute("""
                INSERT INTO products (
                    category_id,
                    name,
                    description,
                    price,
                    image_url
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                category_id,
                name,
                description,
                price,
                image_url
            ))

        flash(
            "محصول اضافه شد.",
            "success"
        )

    return redirect(url_for("admin"))


# ویرایش محصول
@app.route(
    "/admin/products/<int:product_id>/edit",
    methods=["POST"]
)
@admin_required
def edit_product(product_id):

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    image_url = request.form.get("image_url", "").strip()

    category_id = request.form.get(
        "category_id",
        type=int
    )

    price = request.form.get(
        "price",
        type=int
    )

    if (
        not name
        or not category_id
        or price is None
        or price < 0
    ):

        flash(
            "اطلاعات محصول معتبر نیست.",
            "error"
        )

    else:

        with db() as conn:
            conn.execute("""
                UPDATE products
                SET
                    category_id=%s,
                    name=%s,
                    description=%s,
                    price=%s,
                    image_url=%s
                WHERE id=%s
            """, (
                category_id,
                name,
                description,
                price,
                image_url,
                product_id
            ))

        flash(
            "محصول ویرایش شد.",
            "success"
        )

    return redirect(url_for("admin"))


# حذف محصول
@app.route(
    "/admin/products/<int:product_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_product(product_id):

    with db() as conn:
        conn.execute(
            "DELETE FROM products WHERE id=%s",
            (product_id,)
        )

    flash(
        "محصول حذف شد.",
        "success"
    )

    return redirect(url_for("admin"))


# مدیریت خطای 400
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


# مدیریت خطای 500
@app.errorhandler(500)
def server_error(error):

    return render_template(
        "error.html",
        message="مشکلی در سرور رخ داد. تنظیمات دیتابیس را بررسی کنید."
    ), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
