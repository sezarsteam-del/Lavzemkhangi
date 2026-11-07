import secrets
import string
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort

features_bp = Blueprint("features", __name__)
DB = None

def init_features(db_func):
    global DB
    DB = db_func

def ensure_features_schema():
    if DB is None:
        return
    with DB() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_wishlists (
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY(customer_id, product_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_reviews (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                rating SMALLINT NOT NULL CHECK(rating BETWEEN 1 AND 5),
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                is_approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(customer_id, product_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','answered','closed')),
                admin_reply TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_ledger (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                points INTEGER NOT NULL,
                reason TEXT NOT NULL,
                order_id BIGINT REFERENCES store_orders(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gift_cards (
                id BIGSERIAL PRIMARY KEY,
                code_hash TEXT UNIQUE NOT NULL,
                amount BIGINT NOT NULL CHECK(amount > 0),
                remaining_amount BIGINT NOT NULL CHECK(remaining_amount >= 0),
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gift_card_redemptions (
                id BIGSERIAL PRIMARY KEY,
                gift_card_id BIGINT NOT NULL REFERENCES gift_cards(id),
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                amount BIGINT NOT NULL CHECK(amount > 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                target_price BIGINT NOT NULL CHECK(target_price > 0),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(customer_id, product_id)
            )
        """)
        conn.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS loyalty_points INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS referral_code TEXT")
        conn.execute("UPDATE customers SET referral_code=upper(substr(md5(id::text || phone),1,8)) WHERE referral_code IS NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_referral_code ON customers(referral_code) WHERE referral_code IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_product ON product_reviews(product_id,is_approved,created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_customer ON support_tickets(customer_id,created_at DESC)")
        conn.execute("""
            CREATE OR REPLACE FUNCTION award_store_order_points() RETURNS trigger AS $$
            DECLARE earned INTEGER;
            BEGIN
                IF NEW.status = 'paid'
                   AND OLD.status IS DISTINCT FROM 'paid'
                   AND NOT EXISTS (SELECT 1 FROM loyalty_ledger WHERE order_id = NEW.id) THEN
                    earned := GREATEST(1, FLOOR(NEW.total_amount / 100000)::INTEGER);
                    UPDATE customers SET loyalty_points=loyalty_points+earned WHERE id=NEW.customer_id;
                    INSERT INTO loyalty_ledger(customer_id,points,reason,order_id)
                    VALUES(NEW.customer_id,earned,'امتیاز خرید سفارش',NEW.id);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        conn.execute("DROP TRIGGER IF EXISTS trg_award_store_order_points ON store_orders")
        conn.execute("""
            CREATE TRIGGER trg_award_store_order_points
            AFTER UPDATE OF status ON store_orders
            FOR EACH ROW EXECUTE FUNCTION award_store_order_points()
        """)

def customer_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        cid = session.get("customer_id")
        if not cid:
            flash("برای استفاده از این امکان ابتدا وارد حساب مشتری شوید.", "error")
            return redirect(url_for("customer.customer_login", next=request.path))
        with DB() as conn:
            user = conn.execute("SELECT id,phone,first_name,last_name FROM customers WHERE id=%s AND is_active=TRUE", (cid,)).fetchone()
        if not user:
            session.pop("customer_id", None)
            return redirect(url_for("customer.customer_login"))
        return fn(user, *args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            flash("برای دسترسی مدیر وارد شوید.", "error")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

@features_bp.route("/account/wishlist", methods=["GET"])
@customer_required
def wishlist(user):
    with DB() as conn:
        products = conn.execute("""
            SELECT p.id,p.name,p.price,p.image_url,b.name AS brand_name
            FROM product_wishlists w JOIN products p ON p.id=w.product_id
            LEFT JOIN brands b ON b.id=p.brand_id
            WHERE w.customer_id=%s AND p.is_active=TRUE ORDER BY w.created_at DESC
        """, (user["id"],)).fetchall()
    return render_template("feature_wishlist.html", products=products)

@features_bp.route("/account/wishlist/<int:product_id>", methods=["POST"])
@customer_required
def wishlist_toggle(user, product_id):
    with DB() as conn:
        exists = conn.execute("SELECT id FROM products WHERE id=%s AND is_active=TRUE", (product_id,)).fetchone()
        if not exists:
            abort(404)
        row = conn.execute("DELETE FROM product_wishlists WHERE customer_id=%s AND product_id=%s RETURNING product_id", (user["id"], product_id)).fetchone()
        if row:
            flash("محصول از علاقه‌مندی‌ها حذف شد.", "success")
        else:
            conn.execute("INSERT INTO product_wishlists(customer_id,product_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (user["id"], product_id))
            flash("محصول به علاقه‌مندی‌ها اضافه شد.", "success")
    return redirect(request.referrer or url_for("index"))

@features_bp.route("/compare/add/<int:product_id>", methods=["POST"])
def compare_add(product_id):
    with DB() as conn:
        product = conn.execute("SELECT id FROM products WHERE id=%s AND is_active=TRUE", (product_id,)).fetchone()
    if not product:
        abort(404)
    ids = [int(x) for x in session.get("compare_ids", []) if str(x).isdigit()]
    if product_id not in ids:
        if len(ids) >= 4:
            flash("حداکثر چهار محصول را می‌توانید مقایسه کنید.", "error")
        else:
            ids.append(product_id)
            session["compare_ids"] = ids
            flash("محصول به فهرست مقایسه اضافه شد.", "success")
    return redirect(request.referrer or url_for("features.compare"))

@features_bp.route("/compare")
def compare():
    ids = [int(x) for x in session.get("compare_ids", []) if str(x).isdigit()][:4]
    products = []
    if ids:
        with DB() as conn:
            products = conn.execute("""
                SELECT p.id,p.name,p.description,p.price,p.image_url,c.name AS category_name,b.name AS brand_name
                FROM products p LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN brands b ON b.id=p.brand_id
                WHERE p.id=ANY(%s) AND p.is_active=TRUE
            """, (ids,)).fetchall()
        products = sorted(products, key=lambda p: ids.index(p["id"]))
    return render_template("feature_compare.html", products=products)

@features_bp.route("/compare/remove/<int:product_id>", methods=["POST"])
def compare_remove(product_id):
    session["compare_ids"] = [int(x) for x in session.get("compare_ids", []) if str(x).isdigit() and int(x) != product_id]
    return redirect(url_for("features.compare"))

@features_bp.route("/product/<int:product_id>", methods=["GET", "POST"])
def product_detail(product_id):
    with DB() as conn:
        product = conn.execute("""
            SELECT p.id,p.name,p.description,p.price,p.image_url,c.name AS category_name,b.name AS brand_name
            FROM products p LEFT JOIN categories c ON c.id=p.category_id
            LEFT JOIN brands b ON b.id=p.brand_id
            WHERE p.id=%s AND p.is_active=TRUE
        """, (product_id,)).fetchone()
        if not product:
            abort(404)
        if request.method == "POST":
            if not session.get("customer_id"):
                return redirect(url_for("customer.customer_login", next=request.path))
            rating_raw = request.form.get("rating", "5")
            body = request.form.get("body", "").strip()
            title = request.form.get("title", "").strip()[:120]
            if not rating_raw.isdigit() or not 1 <= int(rating_raw) <= 5 or len(body) < 10 or len(body) > 2000:
                flash("امتیاز و متن نظر معتبر وارد کنید (حداقل ۱۰ نویسه).", "error")
            else:
                conn.execute("""
                    INSERT INTO product_reviews(customer_id,product_id,rating,title,body)
                    VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(customer_id,product_id) DO UPDATE SET
                    rating=EXCLUDED.rating,title=EXCLUDED.title,body=EXCLUDED.body,
                    is_approved=FALSE,created_at=NOW()
                """, (session["customer_id"], product_id, int(rating_raw), title, body))
                flash("نظر شما برای بررسی مدیر ثبت شد.", "success")
                return redirect(url_for("features.product_detail", product_id=product_id))
        reviews = conn.execute("""
            SELECT r.rating,r.title,r.body,r.created_at,c.first_name
            FROM product_reviews r JOIN customers c ON c.id=r.customer_id
            WHERE r.product_id=%s AND r.is_approved=TRUE ORDER BY r.created_at DESC LIMIT 30
        """, (product_id,)).fetchall()
        stats = conn.execute("SELECT COUNT(*) AS count,COALESCE(AVG(rating),0) AS average FROM product_reviews WHERE product_id=%s AND is_approved=TRUE", (product_id,)).fetchone()
    recent = [int(x) for x in session.get("recent_products", []) if str(x).isdigit() and int(x) != product_id]
    session["recent_products"] = ([product_id] + recent)[:8]
    return render_template("feature_product_detail.html", product=product, reviews=reviews, review_stats=stats)

@features_bp.route("/account/rewards")
@customer_required
def rewards(user):
    with DB() as conn:
        row = conn.execute("SELECT loyalty_points,referral_code FROM customers WHERE id=%s", (user["id"],)).fetchone()
        ledger = conn.execute("SELECT points,reason,created_at FROM loyalty_ledger WHERE customer_id=%s ORDER BY created_at DESC LIMIT 50", (user["id"],)).fetchall()
    return render_template("feature_rewards.html", customer=user, rewards=row, ledger=ledger)

@features_bp.route("/account/support", methods=["GET", "POST"])
@customer_required
def support(user):
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()[:140]
        message = request.form.get("message", "").strip()
        if len(subject) < 3 or len(message) < 10 or len(message) > 4000:
            flash("عنوان و متن درخواست را کامل وارد کنید.", "error")
        else:
            with DB() as conn:
                conn.execute("INSERT INTO support_tickets(customer_id,subject,message) VALUES(%s,%s,%s)", (user["id"], subject, message))
            flash("درخواست پشتیبانی ثبت شد.", "success")
            return redirect(url_for("features.support"))
    with DB() as conn:
        tickets = conn.execute("SELECT id,subject,message,status,admin_reply,created_at,updated_at FROM support_tickets WHERE customer_id=%s ORDER BY created_at DESC LIMIT 50", (user["id"],)).fetchall()
    return render_template("feature_support.html", tickets=tickets)

@features_bp.route("/admin/reviews")
@admin_required
def admin_reviews():
    with DB() as conn:
        reviews = conn.execute("""
            SELECT r.id,r.rating,r.title,r.body,r.created_at,p.name AS product_name,c.first_name,c.last_name
            FROM product_reviews r JOIN products p ON p.id=r.product_id
            JOIN customers c ON c.id=r.customer_id
            WHERE r.is_approved=FALSE ORDER BY r.created_at ASC LIMIT 200
        """).fetchall()
    return render_template("feature_admin_reviews.html", reviews=reviews)

@features_bp.route("/admin/reviews/<int:review_id>/<action>", methods=["POST"])
@admin_required
def moderate_review(review_id, action):
    if action not in ("approve", "reject"):
        abort(400)
    with DB() as conn:
        if action == "approve":
            conn.execute("UPDATE product_reviews SET is_approved=TRUE WHERE id=%s", (review_id,))
        else:
            conn.execute("DELETE FROM product_reviews WHERE id=%s AND is_approved=FALSE", (review_id,))
    flash("وضعیت نظر به‌روزرسانی شد.", "success")
    return redirect(url_for("features.admin_reviews"))

@features_bp.route("/admin/support")
@admin_required
def admin_support():
    with DB() as conn:
        tickets = conn.execute("""
            SELECT t.*,c.first_name,c.last_name,c.phone
            FROM support_tickets t JOIN customers c ON c.id=t.customer_id
            ORDER BY CASE WHEN t.status='open' THEN 0 ELSE 1 END,t.updated_at DESC LIMIT 200
        """).fetchall()
    return render_template("feature_admin_support.html", tickets=tickets)

@features_bp.route("/admin/support/<int:ticket_id>", methods=["POST"])
@admin_required
def admin_support_reply(ticket_id):
    reply = request.form.get("reply", "").strip()[:4000]
    status = request.form.get("status", "answered")
    if status not in ("answered", "closed", "open") or not reply:
        flash("پاسخ و وضعیت معتبر وارد کنید.", "error")
    else:
        with DB() as conn:
            conn.execute("UPDATE support_tickets SET admin_reply=%s,status=%s,updated_at=NOW() WHERE id=%s", (reply,status,ticket_id))
        flash("پاسخ پشتیبانی ثبت شد.", "success")
    return redirect(url_for("features.admin_support"))

@features_bp.route("/account/gift-card", methods=["GET", "POST"])
@customer_required
def gift_card(user):
    if request.method == "POST":
        raw = "".join(ch for ch in request.form.get("code", "").upper() if ch.isalnum())[:40]
        import hashlib
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with DB() as conn:
            card = conn.execute("SELECT id,remaining_amount FROM gift_cards WHERE code_hash=%s AND remaining_amount>0 AND (expires_at IS NULL OR expires_at>NOW()) FOR UPDATE", (digest,)).fetchone()
            if not card:
                flash("کارت هدیه معتبر نیست یا اعتبار آن تمام شده است.", "error")
            else:
                flash("کد معتبر است؛ اما اتصال اعتبار کارت هدیه به پرداخت سفارش هنوز تکمیل نشده و هیچ اعتباری مصرف نشد.", "error")
        return redirect(url_for("features.gift_card"))
    with DB() as conn:
        history = conn.execute("SELECT amount,created_at FROM gift_card_redemptions WHERE customer_id=%s ORDER BY created_at DESC", (user["id"],)).fetchall()
    return render_template("feature_gift_card.html", history=history)

@features_bp.route("/admin/gift-cards", methods=["GET", "POST"])
@admin_required
def admin_gift_cards():
    import hashlib
    issued_code = None
    if request.method == "POST":
        amount = request.form.get("amount", "")
        if not amount.isdigit() or not 10000 <= int(amount) <= 1000000000:
            flash("مبلغ کارت هدیه باید بین ۱۰ هزار تا یک میلیارد تومان (واحد ثبت فروشگاه) باشد.", "error")
        else:
            alphabet = string.ascii_uppercase + string.digits
            issued_code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
            digest = hashlib.sha256(issued_code.encode()).hexdigest()
            with DB() as conn:
                conn.execute("INSERT INTO gift_cards(code_hash,amount,remaining_amount) VALUES(%s,%s,%s)", (digest,int(amount),int(amount)))
    return render_template("feature_admin_gift_cards.html", issued_code=issued_code)

@features_bp.route("/account/price-alert/<int:product_id>", methods=["POST"])
@customer_required
def price_alert(user, product_id):
    target = request.form.get("target_price", "")
    if not target.isdigit() or int(target) < 1:
        flash("قیمت هدف معتبر وارد کنید.", "error")
    else:
        with DB() as conn:
            product = conn.execute("SELECT id FROM products WHERE id=%s AND is_active=TRUE", (product_id,)).fetchone()
            if not product:
                abort(404)
            conn.execute("""
                INSERT INTO price_alerts(customer_id,product_id,target_price)
                VALUES(%s,%s,%s)
                ON CONFLICT(customer_id,product_id) DO UPDATE SET target_price=EXCLUDED.target_price,is_active=TRUE
            """, (user["id"],product_id,int(target)))
        flash("هشدار قیمت ذخیره شد؛ بررسی و اطلاع‌رسانی خودکار نیازمند اتصال سرویس پیامک/اعلان است.", "success")
    return redirect(url_for("features.product_detail", product_id=product_id))
