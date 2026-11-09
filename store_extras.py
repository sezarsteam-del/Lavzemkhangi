"""Optional storefront features: wishlist, comparison, verified reviews, coupons and notices.
Payment gateways are intentionally not implemented here.
"""
import re
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort

extras_bp = Blueprint("extras", __name__, url_prefix="/store")
DB = None

def init_store_extras(db_func):
    global DB
    DB = db_func

def ensure_store_extras_schema():
    if DB is None:
        raise RuntimeError("Store extras database is not initialized")
    with DB() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_wishlist (
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(customer_id, product_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS product_reviews (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            rating SMALLINT NOT NULL CHECK(rating BETWEEN 1 AND 5),
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            is_approved BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(customer_id, product_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_coupons (
            id BIGSERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')),
            discount_value BIGINT NOT NULL CHECK(discount_value > 0),
            min_order_amount BIGINT NOT NULL DEFAULT 0 CHECK(min_order_amount >= 0),
            max_discount BIGINT,
            usage_limit INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            starts_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK(discount_type <> 'percent' OR discount_value <= 100))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_notifications (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            title TEXT NOT NULL, body TEXT NOT NULL,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_product ON product_reviews(product_id,is_approved,created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_customer ON customer_notifications(customer_id,is_read,created_at DESC)")

def customer_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("customer_id"):
            return redirect(url_for("customer.customer_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

@extras_bp.get("/wishlist")
@customer_required
def wishlist():
    with DB() as conn:
        rows = conn.execute("""SELECT p.id,p.name,p.price,p.image_url,p.description
            FROM customer_wishlist w JOIN products p ON p.id=w.product_id
            WHERE w.customer_id=%s AND p.is_active=TRUE ORDER BY w.created_at DESC""",
            (session["customer_id"],)).fetchall()
    return render_template("store_wishlist.html", products=rows)

@extras_bp.post("/wishlist/<int:product_id>/toggle")
@customer_required
def wishlist_toggle(product_id):
    with DB() as conn:
        product = conn.execute("SELECT id FROM products WHERE id=%s AND is_active=TRUE",(product_id,)).fetchone()
        if not product: abort(404)
        exists = conn.execute("SELECT 1 FROM customer_wishlist WHERE customer_id=%s AND product_id=%s",(session["customer_id"],product_id)).fetchone()
        if exists:
            conn.execute("DELETE FROM customer_wishlist WHERE customer_id=%s AND product_id=%s",(session["customer_id"],product_id))
            flash("محصول از علاقه‌مندی‌ها حذف شد.","success")
        else:
            conn.execute("INSERT INTO customer_wishlist(customer_id,product_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",(session["customer_id"],product_id))
            flash("محصول به علاقه‌مندی‌ها اضافه شد.","success")
    return redirect(request.referrer or url_for("extras.wishlist"))

@extras_bp.get("/compare")
def compare():
    raw = request.args.get("ids","")
    ids=[]
    for part in raw.split(",")[:4]:
        if part.isdigit() and int(part)>0 and int(part) not in ids: ids.append(int(part))
    products=[]
    if ids:
        with DB() as conn:
            products=conn.execute("""SELECT id,name,description,price,image_url
                FROM products WHERE id=ANY(%s) AND is_active=TRUE ORDER BY name""",(ids,)).fetchall()
    return render_template("store_compare.html", products=products, selected_ids=ids)

@extras_bp.route("/products/<int:product_id>/reviews",methods=["GET","POST"])
def reviews(product_id):
    with DB() as conn:
        product=conn.execute("SELECT id,name FROM products WHERE id=%s AND is_active=TRUE",(product_id,)).fetchone()
        if not product: abort(404)
        if request.method=="POST":
            if not session.get("customer_id"):
                return redirect(url_for("customer.customer_login",next=request.path))
            try:
                rating=int(request.form.get("rating","0"))
                body=request.form.get("body","").strip()
                title=request.form.get("title","").strip()[:120]
                if rating not in range(1,6) or not 10<=len(body)<=2000:
                    raise ValueError
                purchased=conn.execute("""SELECT 1 FROM store_orders o JOIN store_order_items i ON i.order_id=o.id
                    WHERE o.customer_id=%s AND i.product_id=%s LIMIT 1""",(session["customer_id"],product_id)).fetchone()
                if not purchased:
                    flash("ثبت نظر فقط برای مشتریانی که این کالا را سفارش داده‌اند امکان‌پذیر است.","error")
                else:
                    conn.execute("""INSERT INTO product_reviews(customer_id,product_id,rating,title,body)
                        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(customer_id,product_id)
                        DO UPDATE SET rating=EXCLUDED.rating,title=EXCLUDED.title,body=EXCLUDED.body,
                        is_approved=FALSE,created_at=NOW()""",(session["customer_id"],product_id,rating,title,body))
                    flash("نظر شما برای بررسی مدیر ثبت شد.","success")
                    return redirect(url_for("extras.reviews",product_id=product_id))
            except (ValueError,TypeError):
                flash("امتیاز یا متن نظر معتبر نیست.","error")
        rows=conn.execute("""SELECT r.rating,r.title,r.body,r.created_at,c.first_name
            FROM product_reviews r JOIN customers c ON c.id=r.customer_id
            WHERE r.product_id=%s AND r.is_approved=TRUE ORDER BY r.created_at DESC""",(product_id,)).fetchall()
        avg=conn.execute("SELECT AVG(rating) AS avg_rating,COUNT(*) AS total FROM product_reviews WHERE product_id=%s AND is_approved=TRUE",(product_id,)).fetchone()
    return render_template("store_reviews.html",product=product,reviews=rows,summary=avg)

@extras_bp.get("/notifications")
@customer_required
def notifications():
    with DB() as conn:
        rows=conn.execute("SELECT * FROM customer_notifications WHERE customer_id=%s ORDER BY created_at DESC LIMIT 100",(session["customer_id"],)).fetchall()
        conn.execute("UPDATE customer_notifications SET is_read=TRUE WHERE customer_id=%s",(session["customer_id"],))
    return render_template("store_notifications.html",notifications=rows)

@extras_bp.route("/admin/coupons",methods=["GET","POST"])
@admin_required
def coupons_admin():
    if request.method=="POST":
        try:
            code=re.sub(r"[^A-Z0-9_-]","",request.form.get("code","").upper())[:32]
            kind=request.form.get("discount_type","percent")
            value=int(request.form.get("discount_value","0"))
            minimum=int(request.form.get("min_order_amount","0"))
            limit=request.form.get("usage_limit","").strip()
            limit=int(limit) if limit else None
            if len(code)<3 or kind not in ("percent","fixed") or value<=0 or minimum<0 or (kind=="percent" and value>100) or (limit is not None and limit<1):
                raise ValueError
            with DB() as conn:
                conn.execute("""INSERT INTO store_coupons(code,discount_type,discount_value,min_order_amount,usage_limit)
                    VALUES(%s,%s,%s,%s,%s)""",(code,kind,value,minimum,limit))
            flash("کد تخفیف ساخته شد.","success")
        except Exception:
            flash("اطلاعات کد تخفیف نامعتبر است یا کد تکراری است.","error")
        return redirect(url_for("extras.coupons_admin"))
    with DB() as conn:
        rows=conn.execute("SELECT * FROM store_coupons ORDER BY created_at DESC").fetchall()
    return render_template("store_coupons_admin.html",coupons=rows)

@extras_bp.post("/admin/coupons/<int:coupon_id>/toggle")
@admin_required
def coupon_toggle(coupon_id):
    with DB() as conn:
        row=conn.execute("UPDATE store_coupons SET is_active=NOT is_active WHERE id=%s RETURNING id",(coupon_id,)).fetchone()
        if not row: abort(404)
    return redirect(url_for("extras.coupons_admin"))

@extras_bp.get("/coupon/check")
@customer_required
def coupon_check():
    code=re.sub(r"[^A-Z0-9_-]","",request.args.get("code","").upper())[:32]
    try: amount=int(request.args.get("amount","0"))
    except ValueError: amount=0
    if amount<0: amount=0
    with DB() as conn:
        row=conn.execute("""SELECT * FROM store_coupons WHERE code=%s AND is_active=TRUE
            AND (starts_at IS NULL OR starts_at<=NOW()) AND (expires_at IS NULL OR expires_at>NOW())
            AND (usage_limit IS NULL OR used_count<usage_limit)""",(code,)).fetchone()
    if not row or amount<int(row["min_order_amount"]):
        return {"valid":False,"message":"کد تخفیف معتبر نیست یا حداقل مبلغ سفارش رعایت نشده است."},400
    discount=(amount*int(row["discount_value"])//100 if row["discount_type"]=="percent" else int(row["discount_value"]))
    if row["max_discount"] is not None: discount=min(discount,int(row["max_discount"]))
    discount=min(discount,amount)
    return {"valid":True,"discount":discount,"final_amount":amount-discount,"message":"کد تخفیف معتبر است."}

@extras_bp.get("/admin/reviews")
@admin_required
def reviews_admin():
    with DB() as conn:
        rows=conn.execute("""SELECT r.*,p.name AS product_name,c.phone,c.first_name
            FROM product_reviews r JOIN products p ON p.id=r.product_id JOIN customers c ON c.id=r.customer_id
            ORDER BY r.created_at DESC LIMIT 200""").fetchall()
    return render_template("store_reviews_admin.html",reviews=rows)

@extras_bp.post("/admin/reviews/<int:review_id>/approve")
@admin_required
def review_approve(review_id):
    with DB() as conn:
        row=conn.execute("UPDATE product_reviews SET is_approved=TRUE WHERE id=%s RETURNING id",(review_id,)).fetchone()
        if not row: abort(404)
    return redirect(url_for("extras.reviews_admin"))

@extras_bp.post("/admin/reviews/<int:review_id>/delete")
@admin_required
def review_delete(review_id):
    with DB() as conn:
        conn.execute("DELETE FROM product_reviews WHERE id=%s",(review_id,))
    return redirect(url_for("extras.reviews_admin"))
