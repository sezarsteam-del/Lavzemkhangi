"""Additional business suite: warranties, after-sales service, returns, price history,
customer credit ledger, expenses and serial-number tracking.
All schema changes are additive; existing catalog/order records are preserved.
"""
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort

suite_bp = Blueprint("suite", __name__)
DB = None


def init_store_suite(db_func):
    global DB
    DB = db_func


def ensure_store_suite_schema():
    if DB is None:
        raise RuntimeError("Store suite database is not initialized")
    with DB() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS product_price_history (
            id BIGSERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            old_price BIGINT NOT NULL CHECK(old_price >= 0),
            new_price BIGINT NOT NULL CHECK(new_price >= 0),
            changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_warranties (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            order_id BIGINT REFERENCES store_orders(id) ON DELETE SET NULL,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            serial_number TEXT NOT NULL UNIQUE,
            warranty_provider TEXT NOT NULL DEFAULT '',
            starts_on DATE NOT NULL DEFAULT CURRENT_DATE,
            ends_on DATE NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_service_tickets (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            warranty_id BIGINT REFERENCES store_warranties(id) ON DELETE SET NULL,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
              CHECK(status IN ('new','reviewing','scheduled','in_progress','resolved','rejected')),
            admin_note TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_return_requests (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            order_id BIGINT NOT NULL REFERENCES store_orders(id) ON DELETE RESTRICT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'submitted'
              CHECK(status IN ('submitted','reviewing','approved','rejected','received','refunded')),
            admin_note TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_credit_ledger (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            amount BIGINT NOT NULL CHECK(amount <> 0),
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by TEXT NOT NULL DEFAULT 'admin')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_expenses (
            id BIGSERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'عمومی',
            amount BIGINT NOT NULL CHECK(amount > 0),
            expense_date DATE NOT NULL DEFAULT CURRENT_DATE,
            notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_product_serials (
            id BIGSERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            serial_number TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'in_stock'
              CHECK(status IN ('in_stock','sold','returned','defective')),
            order_id BIGINT REFERENCES store_orders(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_price_alerts (
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            target_price BIGINT CHECK(target_price IS NULL OR target_price >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(customer_id, product_id))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_price_history_product ON product_price_history(product_id, changed_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_status ON store_service_tickets(status, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_status ON store_return_requests(status, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_warranty_customer ON store_warranties(customer_id, ends_on)")
        conn.execute("""CREATE OR REPLACE FUNCTION store_record_price_change() RETURNS trigger AS $$
            BEGIN
              IF OLD.price IS DISTINCT FROM NEW.price THEN
                INSERT INTO product_price_history(product_id, old_price, new_price)
                VALUES(OLD.id, OLD.price, NEW.price);
                INSERT INTO customer_notifications(customer_id,title,body)
                SELECT a.customer_id, 'تغییر قیمت محصول',
                       'قیمت ' || NEW.name || ' تغییر کرد. قیمت جدید: ' || NEW.price::text || ' تومان'
                FROM customer_price_alerts a
                WHERE a.product_id=NEW.id
                  AND (a.target_price IS NULL OR NEW.price<=a.target_price);
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql""")
        conn.execute("DROP TRIGGER IF EXISTS trg_store_price_history ON products")
        conn.execute("""CREATE TRIGGER trg_store_price_history
            AFTER UPDATE OF price ON products
            FOR EACH ROW EXECUTE FUNCTION store_record_price_change()""")


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def customer_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        customer_id = session.get("customer_id")
        if not customer_id:
            return redirect(url_for("customer.customer_login", next=request.path))
        with DB() as conn:
            active = conn.execute("SELECT id FROM customers WHERE id=%s AND is_active=TRUE",
                                  (customer_id,)).fetchone()
        if not active:
            session.pop("customer_id", None)
            return redirect(url_for("customer.customer_login"))
        return fn(*args, **kwargs)
    return wrapped


@suite_bp.get("/admin/suite")
@admin_required
def admin_dashboard():
    with DB() as conn:
        stats = {
            "warranties": conn.execute("SELECT COUNT(*) n FROM store_warranties").fetchone()["n"],
            "active_warranties": conn.execute("SELECT COUNT(*) n FROM store_warranties WHERE ends_on>=CURRENT_DATE").fetchone()["n"],
            "tickets": conn.execute("SELECT COUNT(*) n FROM store_service_tickets WHERE status NOT IN ('resolved','rejected')").fetchone()["n"],
            "returns": conn.execute("SELECT COUNT(*) n FROM store_return_requests WHERE status IN ('submitted','reviewing')").fetchone()["n"],
            "expenses": conn.execute("SELECT COALESCE(SUM(amount),0) n FROM store_expenses WHERE date_trunc('month',expense_date)=date_trunc('month',CURRENT_DATE)").fetchone()["n"],
            "credits": conn.execute("SELECT COALESCE(SUM(amount),0) n FROM customer_credit_ledger").fetchone()["n"],
        }
        tickets = conn.execute("""SELECT t.*, c.first_name, c.last_name, c.phone, p.name product_name
            FROM store_service_tickets t JOIN customers c ON c.id=t.customer_id
            LEFT JOIN products p ON p.id=t.product_id ORDER BY t.created_at DESC LIMIT 100""").fetchall()
        returns = conn.execute("""SELECT r.*, c.first_name, c.last_name, c.phone, p.name product_name
            FROM store_return_requests r JOIN customers c ON c.id=r.customer_id
            JOIN products p ON p.id=r.product_id ORDER BY r.created_at DESC LIMIT 100""").fetchall()
        expenses = conn.execute("SELECT * FROM store_expenses ORDER BY expense_date DESC,id DESC LIMIT 100").fetchall()
        warranties = conn.execute("""SELECT w.*,c.first_name,c.last_name,p.name product_name FROM store_warranties w
            JOIN customers c ON c.id=w.customer_id JOIN products p ON p.id=w.product_id
            ORDER BY w.created_at DESC LIMIT 100""").fetchall()
        serials = conn.execute("""SELECT s.*,p.name product_name FROM store_product_serials s
            JOIN products p ON p.id=s.product_id ORDER BY s.created_at DESC LIMIT 100""").fetchall()
    return render_template("store_suite_admin.html", stats=stats, tickets=tickets,
                           returns=returns, expenses=expenses, serials=serials, warranties=warranties)




@suite_bp.post("/admin/suite/warranties")
@admin_required
def add_warranty():
    try:
        customer_id = int(request.form.get("customer_id", "0"))
        product_id = int(request.form.get("product_id", "0"))
        order_raw = request.form.get("order_id", "").strip()
        order_id = int(order_raw) if order_raw else None
        serial = request.form.get("serial_number", "").strip()[:120]
        provider = request.form.get("warranty_provider", "").strip()[:180]
        starts = request.form.get("starts_on", "").strip() or None
        ends = request.form.get("ends_on", "").strip()
        if customer_id < 1 or product_id < 1 or not serial or not ends:
            raise ValueError
        with DB() as conn:
            if not conn.execute("SELECT id FROM customers WHERE id=%s AND is_active=TRUE", (customer_id,)).fetchone():
                raise ValueError
            if not conn.execute("SELECT id FROM products WHERE id=%s", (product_id,)).fetchone():
                raise ValueError
            if order_id and not conn.execute("SELECT id FROM store_orders WHERE id=%s AND customer_id=%s", (order_id, customer_id)).fetchone():
                raise ValueError
            conn.execute("""INSERT INTO store_warranties(customer_id,order_id,product_id,serial_number,warranty_provider,starts_on,ends_on,notes)
                VALUES(%s,%s,%s,%s,%s,COALESCE(%s::date,CURRENT_DATE),%s::date,%s)""",
                (customer_id, order_id, product_id, serial, provider, starts, ends, request.form.get("notes", "").strip()[:1000]))
        flash("ضمانت کالا ثبت شد.", "success")
    except Exception:
        flash("ثبت ضمانت انجام نشد؛ شناسه مشتری و محصول، تاریخ‌ها و یکتا بودن سریال را بررسی کنید.", "error")
    return redirect(url_for("suite.admin_dashboard") + "#warranties")

@suite_bp.post("/admin/suite/expenses")
@admin_required
def add_expense():
    try:
        title = request.form.get("title", "").strip()[:180]
        category = request.form.get("category", "عمومی").strip()[:80]
        amount = int(request.form.get("amount", "0"))
        expense_date = request.form.get("expense_date", "").strip() or None
        if not title or amount <= 0:
            raise ValueError
        with DB() as conn:
            conn.execute("""INSERT INTO store_expenses(title,category,amount,expense_date,notes)
                VALUES(%s,%s,%s,COALESCE(%s::date,CURRENT_DATE),%s)""",
                (title, category, amount, expense_date, request.form.get("notes", "").strip()[:1000]))
        flash("هزینه ثبت شد.", "success")
    except (ValueError, TypeError):
        flash("عنوان، مبلغ و تاریخ هزینه را بررسی کنید.", "error")
    return redirect(url_for("suite.admin_dashboard") + "#expenses")


@suite_bp.post("/admin/suite/credit")
@admin_required
def adjust_credit():
    try:
        customer_id = int(request.form.get("customer_id", "0"))
        amount = int(request.form.get("amount", "0"))
        reason = request.form.get("reason", "").strip()[:240]
        if customer_id < 1 or amount == 0 or not reason:
            raise ValueError
        with DB() as conn:
            conn.execute("INSERT INTO customer_credit_ledger(customer_id,amount,reason,created_by) VALUES(%s,%s,%s,%s)",
                         (customer_id, amount, reason, str(session.get("admin_username") or "admin")[:120]))
        flash("تراکنش اعتبار مشتری ثبت شد.", "success")
    except (ValueError, TypeError):
        flash("اطلاعات اعتبار مشتری معتبر نیست.", "error")
    return redirect(url_for("suite.admin_dashboard"))


@suite_bp.post("/admin/suite/serials")
@admin_required
def add_serial():
    try:
        product_id = int(request.form.get("product_id", "0"))
        serial = request.form.get("serial_number", "").strip()[:120]
        if product_id < 1 or not serial:
            raise ValueError
        with DB() as conn:
            conn.execute("INSERT INTO store_product_serials(product_id,serial_number) VALUES(%s,%s)",
                         (product_id, serial))
        flash("شماره سریال ثبت شد.", "success")
    except Exception:
        flash("ثبت سریال انجام نشد؛ محصول و یکتا بودن شماره سریال را بررسی کنید.", "error")
    return redirect(url_for("suite.admin_dashboard") + "#serials")


@suite_bp.post("/admin/suite/tickets/<int:ticket_id>")
@admin_required
def update_ticket(ticket_id):
    status = request.form.get("status", "")
    allowed = {"new", "reviewing", "scheduled", "in_progress", "resolved", "rejected"}
    if status not in allowed:
        abort(400)
    with DB() as conn:
        row = conn.execute("UPDATE store_service_tickets SET status=%s,admin_note=%s,updated_at=NOW() WHERE id=%s RETURNING id",
                           (status, request.form.get("admin_note", "").strip()[:1500], ticket_id)).fetchone()
        if not row:
            abort(404)
    flash("وضعیت درخواست خدمات به‌روزرسانی شد.", "success")
    return redirect(url_for("suite.admin_dashboard") + "#tickets")


@suite_bp.post("/admin/suite/returns/<int:return_id>")
@admin_required
def update_return(return_id):
    status = request.form.get("status", "")
    allowed = {"submitted", "reviewing", "approved", "rejected", "received", "refunded"}
    if status not in allowed:
        abort(400)
    with DB() as conn:
        row = conn.execute("UPDATE store_return_requests SET status=%s,admin_note=%s,updated_at=NOW() WHERE id=%s RETURNING id",
                           (status, request.form.get("admin_note", "").strip()[:1500], return_id)).fetchone()
        if not row:
            abort(404)
    flash("وضعیت مرجوعی به‌روزرسانی شد.", "success")
    return redirect(url_for("suite.admin_dashboard") + "#returns")


@suite_bp.get("/store/services")
@customer_required
def customer_services():
    customer_id = session["customer_id"]
    with DB() as conn:
        warranties = conn.execute("""SELECT w.*,p.name product_name FROM store_warranties w
            JOIN products p ON p.id=w.product_id WHERE w.customer_id=%s ORDER BY w.ends_on DESC""",
            (customer_id,)).fetchall()
        tickets = conn.execute("""SELECT t.*,p.name product_name FROM store_service_tickets t
            LEFT JOIN products p ON p.id=t.product_id WHERE t.customer_id=%s ORDER BY t.created_at DESC""",
            (customer_id,)).fetchall()
        returns = conn.execute("""SELECT r.*,p.name product_name FROM store_return_requests r
            JOIN products p ON p.id=r.product_id WHERE r.customer_id=%s ORDER BY r.created_at DESC""",
            (customer_id,)).fetchall()
        credit = conn.execute("SELECT COALESCE(SUM(amount),0) total FROM customer_credit_ledger WHERE customer_id=%s",
                              (customer_id,)).fetchone()["total"]
    return render_template("customer_services.html", warranties=warranties, tickets=tickets,
                           returns=returns, credit=credit)


@suite_bp.post("/store/services/tickets")
@customer_required
def create_ticket():
    subject = request.form.get("subject", "").strip()[:180]
    description = request.form.get("description", "").strip()[:3000]
    product_raw = request.form.get("product_id", "").strip()
    try:
        product_id = int(product_raw) if product_raw else None
        if not subject or len(description) < 10:
            raise ValueError
        with DB() as conn:
            if product_id and not conn.execute("SELECT id FROM products WHERE id=%s AND is_active=TRUE",
                                               (product_id,)).fetchone():
                raise ValueError
            conn.execute("INSERT INTO store_service_tickets(customer_id,product_id,subject,description) VALUES(%s,%s,%s,%s)",
                         (session["customer_id"], product_id, subject, description))
        flash("درخواست خدمات پس از فروش ثبت شد.", "success")
    except (ValueError, TypeError):
        flash("اطلاعات درخواست معتبر نیست.", "error")
    return redirect(url_for("suite.customer_services") + "#tickets")


@suite_bp.post("/store/services/returns")
@customer_required
def create_return():
    try:
        order_id = int(request.form.get("order_id", "0"))
        product_id = int(request.form.get("product_id", "0"))
        quantity = int(request.form.get("quantity", "0"))
        reason = request.form.get("reason", "").strip()[:1200]
        if min(order_id, product_id, quantity) < 1 or len(reason) < 5:
            raise ValueError
        with DB() as conn:
            purchased = conn.execute("""SELECT i.quantity FROM store_order_items i
                JOIN store_orders o ON o.id=i.order_id
                WHERE o.id=%s AND o.customer_id=%s AND i.product_id=%s
                  AND o.status IN ('paid','paid_stock_issue')""",
                (order_id, session["customer_id"], product_id)).fetchone()
            if not purchased or quantity > int(purchased["quantity"]):
                raise ValueError
            conn.execute("""INSERT INTO store_return_requests(customer_id,order_id,product_id,quantity,reason)
                VALUES(%s,%s,%s,%s,%s)""",
                (session["customer_id"], order_id, product_id, quantity, reason))
        flash("درخواست مرجوعی برای بررسی ثبت شد.", "success")
    except (ValueError, TypeError):
        flash("سفارش، کالا یا تعداد برای مرجوعی معتبر نیست.", "error")
    return redirect(url_for("suite.customer_services") + "#returns")


@suite_bp.post("/store/price-alert/<int:product_id>")
@customer_required
def toggle_price_alert(product_id):
    try:
        target_raw = request.form.get("target_price", "").strip()
        target = int(target_raw) if target_raw else None
        if target is not None and target < 0:
            raise ValueError
        with DB() as conn:
            if not conn.execute("SELECT id FROM products WHERE id=%s AND is_active=TRUE", (product_id,)).fetchone():
                abort(404)
            conn.execute("""INSERT INTO customer_price_alerts(customer_id,product_id,target_price)
                VALUES(%s,%s,%s) ON CONFLICT(customer_id,product_id)
                DO UPDATE SET target_price=EXCLUDED.target_price,created_at=NOW()""",
                (session["customer_id"], product_id, target))
        flash("هشدار قیمت برای این کالا ثبت شد.", "success")
    except (ValueError, TypeError):
        flash("قیمت هدف معتبر نیست.", "error")
    return redirect(request.referrer or url_for("index"))


@suite_bp.get("/store/products/<int:product_id>/price-history")
def price_history(product_id):
    with DB() as conn:
        product = conn.execute("SELECT id,name,price FROM products WHERE id=%s AND is_active=TRUE",
                               (product_id,)).fetchone()
        if not product:
            abort(404)
        history = conn.execute("""SELECT old_price,new_price,changed_at FROM product_price_history
            WHERE product_id=%s ORDER BY changed_at DESC LIMIT 100""", (product_id,)).fetchall()
    return render_template("product_price_history.html", product=product, history=history)
