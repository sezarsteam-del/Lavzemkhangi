import csv
import io
import json
import os
import urllib.request
import urllib.error
from openpyxl import Workbook
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort, Response

advanced_bp = Blueprint("advanced", __name__, url_prefix="/admin/advanced")
assistant_bp = Blueprint("assistant", __name__, url_prefix="/store")
DB = None

def init_advanced_store(db_func):
    global DB
    DB = db_func

def ensure_advanced_schema():
    with DB() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS store_suppliers (
            id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_purchase_orders (
            id BIGSERIAL PRIMARY KEY, supplier_id BIGINT NOT NULL REFERENCES store_suppliers(id),
            product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL CHECK(quantity>0),
            unit_cost BIGINT NOT NULL CHECK(unit_cost>=0), expected_date DATE,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','received','cancelled')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), received_at TIMESTAMPTZ)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_shipments (
            id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL UNIQUE REFERENCES store_orders(id),
            carrier TEXT NOT NULL DEFAULT '', tracking_code TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'preparing' CHECK(status IN ('preparing','shipped','delivered','returned')),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS product_questions (
            id BIGSERIAL PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            customer_id BIGINT REFERENCES customers(id) ON DELETE SET NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '', is_approved BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), answered_at TIMESTAMPTZ)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_banners (
            id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '', target_url TEXT NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_loyalty_ledger (
            id BIGSERIAL PRIMARY KEY, customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            points INTEGER NOT NULL CHECK(points<>0), reason TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("ALTER TABLE customer_loyalty_ledger ADD COLUMN IF NOT EXISTS source_order_id BIGINT UNIQUE REFERENCES store_orders(id) ON DELETE SET NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_admin_audit (
            id BIGSERIAL PRIMARY KEY, admin_name TEXT NOT NULL DEFAULT 'admin', action TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '', entity_id BIGINT, details TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_status ON store_purchase_orders(status,created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shipments_status ON store_shipments(status,updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_product ON product_questions(product_id,is_approved,created_at DESC)")

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def audit(conn, action, entity, entity_id, details=""):
    conn.execute("INSERT INTO store_admin_audit(admin_name,action,entity_type,entity_id,details) VALUES(%s,%s,%s,%s,%s)",
        (str(session.get("admin_username") or "admin")[:120], action, entity, entity_id, details[:1000]))

@assistant_bp.route("/assistant", methods=["GET", "POST"])
def shopping_assistant():
    history=session.get("shopping_assistant_history",[])
    if request.method=="POST":
        if request.form.get("action")=="clear":
            session.pop("shopping_assistant_history",None)
            return redirect(url_for("assistant.shopping_assistant"))
        message=request.form.get("message","").strip()
        if not 2<=len(message)<=1500:
            flash("پیام باید بین ۲ تا ۱۵۰۰ نویسه باشد.","error")
        else:
            api_key=os.environ.get("OPENAI_API_KEY","").strip()
            if not api_key:
                flash("دستیار هوشمند آماده اتصال است؛ ابتدا OPENAI_API_KEY را در متغیرهای امن هاست تنظیم کنید.","error")
            else:
                try:
                    with DB() as conn:
                        matches=conn.execute("""SELECT p.name,p.price,c.name AS category,b.name AS brand
                            FROM products p LEFT JOIN categories c ON c.id=p.category_id
                            LEFT JOIN brands b ON b.id=p.brand_id
                            WHERE p.is_active=TRUE AND (p.name ILIKE %s OR p.description ILIKE %s OR b.name ILIKE %s)
                            ORDER BY p.created_at DESC LIMIT 8""",
                            ("%"+message[:80]+"%","%"+message[:80]+"%","%"+message[:80]+"%")).fetchall()
                    catalog="\n".join(f"- {x['name']} | {x['price']} تومان | {x['category'] or ''} | {x['brand'] or ''}" for x in matches)
                    messages=[{"role":"system","content":"تو دستیار خرید فروشگاه لوازم خانگی هستی. فقط فارسی پاسخ بده. برای قیمت و موجودی حدس نزن؛ فقط از فهرست محصولات داده‌شده استفاده کن و اگر اطلاعات کافی نیست از مشتری سؤال روشن بپرس. اطلاعات محرمانه یا سفارش مشتریان را افشا نکن.\nمحصولات مرتبط فعلی:\n"+(catalog or "محصول منطبق در جستجوی سریع پیدا نشد.")}]
                    messages.extend(history[-8:])
                    messages.append({"role":"user","content":message})
                    endpoint=os.environ.get("OPENAI_BASE_URL","https://api.openai.com/v1/chat/completions").strip()
                    if not endpoint.endswith("/chat/completions"):
                        endpoint=endpoint.rstrip("/")+"/chat/completions"
                    payload={"model":os.environ.get("OPENAI_MODEL","gpt-4o-mini"),"messages":messages,"temperature":0.4}
                    req=urllib.request.Request(endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),
                        headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},method="POST")
                    with urllib.request.urlopen(req,timeout=35) as response:
                        data=json.loads(response.read(200000).decode("utf-8","replace"))
                    answer=data["choices"][0]["message"]["content"].strip()
                    history=(history+[{"role":"user","content":message},{"role":"assistant","content":answer}])[-12:]
                    session["shopping_assistant_history"]=history
                except Exception:
                    flash("ارتباط با سرویس هوش مصنوعی برقرار نشد؛ کلید، مدل و آدرس سرویس را بررسی کنید.","error")
    return render_template("shopping_assistant.html",history=history)


@advanced_bp.get("")
@admin_required
def dashboard():
    with DB() as conn:
        stats = {
            "suppliers": conn.execute("SELECT COUNT(*) n FROM store_suppliers WHERE is_active").fetchone()["n"],
            "purchases": conn.execute("SELECT COUNT(*) n FROM store_purchase_orders WHERE status='pending'").fetchone()["n"],
            "shipments": conn.execute("SELECT COUNT(*) n FROM store_shipments WHERE status IN ('preparing','shipped')").fetchone()["n"],
            "questions": conn.execute("SELECT COUNT(*) n FROM product_questions WHERE NOT is_approved").fetchone()["n"],
            "low_stock": conn.execute("SELECT COUNT(*) n FROM products WHERE is_active AND stock<=min_stock").fetchone()["n"]}
        suppliers = conn.execute("SELECT * FROM store_suppliers ORDER BY name").fetchall()
        products = conn.execute("SELECT id,name,stock FROM products WHERE is_active ORDER BY name LIMIT 500").fetchall()
        purchases = conn.execute("SELECT po.*,s.name supplier_name,p.name product_name FROM store_purchase_orders po JOIN store_suppliers s ON s.id=po.supplier_id JOIN products p ON p.id=po.product_id ORDER BY po.id DESC LIMIT 100").fetchall()
        shipments = conn.execute("SELECT sh.*,o.id order_number FROM store_shipments sh JOIN store_orders o ON o.id=sh.order_id ORDER BY sh.id DESC LIMIT 100").fetchall()
        orders = conn.execute("SELECT id FROM store_orders ORDER BY id DESC LIMIT 300").fetchall()
        questions = conn.execute("SELECT q.*,p.name product_name FROM product_questions q JOIN products p ON p.id=q.product_id WHERE NOT q.is_approved ORDER BY q.id DESC LIMIT 100").fetchall()
        banners = conn.execute("SELECT * FROM store_banners ORDER BY id DESC").fetchall()
        customers = conn.execute("SELECT id,first_name,last_name,phone FROM customers WHERE is_active ORDER BY id DESC LIMIT 500").fetchall()
        low_stock = conn.execute("SELECT name,stock,min_stock FROM products WHERE is_active AND stock<=min_stock ORDER BY stock LIMIT 100").fetchall()
        abandoned_carts = conn.execute("""SELECT ac.customer_id,ac.cart_items,ac.last_updated,
            c.first_name,c.last_name,c.phone
            FROM store_abandoned_carts ac JOIN customers c ON c.id=ac.customer_id
            WHERE ac.last_updated<NOW()-INTERVAL '1 hour'
            ORDER BY ac.last_updated DESC LIMIT 100""").fetchall()
    return render_template("advanced_store_admin.html", stats=stats, suppliers=suppliers, products=products,
        purchases=purchases, shipments=shipments, orders=orders, questions=questions, banners=banners,
        customers=customers, low_stock=low_stock, abandoned_carts=abandoned_carts)

@advanced_bp.post("/suppliers")
@admin_required
def supplier_create():
    name=request.form.get("name","").strip()[:180]
    if not name:
        flash("نام تأمین‌کننده الزامی است.","error")
    else:
        with DB() as conn:
            row=conn.execute("INSERT INTO store_suppliers(name,phone,email,notes) VALUES(%s,%s,%s,%s) RETURNING id",
                (name,request.form.get("phone","")[:40],request.form.get("email","")[:180],request.form.get("notes","")[:1000])).fetchone()
            audit(conn,"supplier_created","supplier",row["id"],name)
        flash("تأمین‌کننده ثبت شد.","success")
    return redirect(url_for("advanced.dashboard")+"#suppliers")

@advanced_bp.post("/purchases")
@admin_required
def purchase_create():
    try:
        sid=int(request.form.get("supplier_id","0")); pid=int(request.form.get("product_id","0"))
        qty=int(request.form.get("quantity","0")); cost=int(request.form.get("unit_cost","-1"))
        expected=request.form.get("expected_date","").strip() or None
        if min(sid,pid,qty)<1 or cost<0: raise ValueError
        with DB() as conn:
            row=conn.execute("INSERT INTO store_purchase_orders(supplier_id,product_id,quantity,unit_cost,expected_date) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (sid,pid,qty,cost,expected)).fetchone()
            audit(conn,"purchase_created","purchase",row["id"],"quantity="+str(qty))
        flash("سفارش خرید ثبت شد.","success")
    except (ValueError,TypeError):
        flash("اطلاعات سفارش خرید معتبر نیست.","error")
    return redirect(url_for("advanced.dashboard")+"#purchases")

@advanced_bp.post("/purchases/<int:purchase_id>/receive")
@admin_required
def purchase_receive(purchase_id):
    with DB() as conn:
        row=conn.execute("SELECT * FROM store_purchase_orders WHERE id=%s FOR UPDATE",(purchase_id,)).fetchone()
        if not row: abort(404)
        if row["status"]!="pending":
            flash("این سفارش قبلاً پردازش شده است.","error")
        else:
            conn.execute("UPDATE products SET stock=stock+%s WHERE id=%s",(row["quantity"],row["product_id"]))
            conn.execute("INSERT INTO stock_movements(product_id,movement_type,quantity,note) VALUES(%s,'in',%s,%s)",
                (row["product_id"],row["quantity"],"دریافت سفارش خرید #"+str(purchase_id)))
            conn.execute("UPDATE store_purchase_orders SET status='received',received_at=NOW() WHERE id=%s",(purchase_id,))
            audit(conn,"purchase_received","purchase",purchase_id,"quantity="+str(row["quantity"]))
            flash("دریافت کالا و افزایش موجودی ثبت شد.","success")
    return redirect(url_for("advanced.dashboard")+"#purchases")

@advanced_bp.post("/shipments")
@admin_required
def shipment_create():
    try:
        oid=int(request.form.get("order_id","0"))
        carrier=request.form.get("carrier","").strip()[:120]
        tracking=request.form.get("tracking_code","").strip()[:160]
        with DB() as conn:
            if oid<1 or not conn.execute("SELECT id FROM store_orders WHERE id=%s",(oid,)).fetchone(): raise ValueError
            row=conn.execute("INSERT INTO store_shipments(order_id,carrier,tracking_code) VALUES(%s,%s,%s) ON CONFLICT(order_id) DO UPDATE SET carrier=EXCLUDED.carrier,tracking_code=EXCLUDED.tracking_code,updated_at=NOW() RETURNING id",
                (oid,carrier,tracking)).fetchone()
            conn.execute("UPDATE store_orders SET tracking_code=%s WHERE id=%s",(tracking,oid))
            audit(conn,"shipment_saved","shipment",row["id"],"order="+str(oid))
        flash("اطلاعات ارسال ذخیره شد.","success")
    except (ValueError,TypeError):
        flash("شماره سفارش معتبر نیست.","error")
    return redirect(url_for("advanced.dashboard")+"#shipments")

@advanced_bp.post("/shipments/<int:shipment_id>/status")
@admin_required
def shipment_status(shipment_id):
    status=request.form.get("status","")
    if status not in ("preparing","shipped","delivered","returned"): abort(400)
    with DB() as conn:
        row=conn.execute("UPDATE store_shipments SET status=%s,updated_at=NOW() WHERE id=%s RETURNING id,order_id",(status,shipment_id)).fetchone()
        if not row: abort(404)
        fulfillment={"preparing":"processing","shipped":"shipped","delivered":"delivered","returned":"returned"}[status]
        conn.execute("UPDATE store_orders SET fulfillment_status=%s,shipped_at=CASE WHEN %s='shipped' THEN NOW() ELSE shipped_at END,delivered_at=CASE WHEN %s='delivered' THEN NOW() ELSE delivered_at END WHERE id=%s",
            (fulfillment,status,status,row["order_id"]))
        audit(conn,"shipment_status","shipment",shipment_id,status)
    return redirect(url_for("advanced.dashboard")+"#shipments")

@advanced_bp.post("/banners")
@admin_required
def banner_create():
    title=request.form.get("title","").strip()[:180]
    if not title:
        flash("عنوان بنر الزامی است.","error")
    else:
        with DB() as conn:
            row=conn.execute("INSERT INTO store_banners(title,body,image_url,target_url) VALUES(%s,%s,%s,%s) RETURNING id",
                (title,request.form.get("body","")[:1000],request.form.get("image_url","")[:1000],request.form.get("target_url","")[:1000])).fetchone()
            audit(conn,"banner_created","banner",row["id"],title)
        flash("بنر ثبت شد.","success")
    return redirect(url_for("advanced.dashboard")+"#banners")

@advanced_bp.post("/banners/<int:banner_id>/toggle")
@admin_required
def banner_toggle(banner_id):
    with DB() as conn:
        row=conn.execute("UPDATE store_banners SET is_active=NOT is_active WHERE id=%s RETURNING id",(banner_id,)).fetchone()
        if not row: abort(404)
        audit(conn,"banner_toggled","banner",banner_id)
    return redirect(url_for("advanced.dashboard")+"#banners")

@advanced_bp.route("/products/<int:product_id>/questions",methods=["GET","POST"])
def product_questions(product_id):
    with DB() as conn:
        product=conn.execute("SELECT id,name FROM products WHERE id=%s AND is_active=TRUE",(product_id,)).fetchone()
        if not product: abort(404)
        if request.method=="POST":
            question=request.form.get("question","").strip()
            if not session.get("customer_id"):
                return redirect(url_for("customer.customer_login",next=request.path))
            if not 10<=len(question)<=1500:
                flash("متن پرسش باید بین ۱۰ تا ۱۵۰۰ نویسه باشد.","error")
            else:
                conn.execute("INSERT INTO product_questions(product_id,customer_id,question) VALUES(%s,%s,%s)",(product_id,session["customer_id"],question))
                flash("پرسش برای بررسی مدیر ثبت شد.","success")
                return redirect(url_for("advanced.product_questions",product_id=product_id))
        rows=conn.execute("SELECT q.question,q.answer,q.created_at,c.first_name FROM product_questions q LEFT JOIN customers c ON c.id=q.customer_id WHERE q.product_id=%s AND q.is_approved=TRUE ORDER BY q.id DESC LIMIT 100",(product_id,)).fetchall()
    return render_template("product_questions.html",product=product,questions=rows)

@advanced_bp.post("/questions/<int:question_id>/answer")
@admin_required
def question_answer(question_id):
    answer=request.form.get("answer","").strip()[:2000]
    if not answer:
        flash("پاسخ را وارد کنید.","error")
    else:
        with DB() as conn:
            row=conn.execute("UPDATE product_questions SET answer=%s,is_approved=TRUE,answered_at=NOW() WHERE id=%s RETURNING id",(answer,question_id)).fetchone()
            if not row: abort(404)
            audit(conn,"question_answered","question",question_id)
        flash("پاسخ منتشر شد.","success")
    return redirect(url_for("advanced.dashboard")+"#questions")

@advanced_bp.post("/abandoned-carts/<int:customer_id>/remind")
@admin_required
def abandoned_cart_remind(customer_id):
    with DB() as conn:
        cart=conn.execute("SELECT customer_id FROM store_abandoned_carts WHERE customer_id=%s AND last_updated<NOW()-INTERVAL '1 hour'",(customer_id,)).fetchone()
        if not cart:
            flash("سبد رهاشده‌ای برای این مشتری پیدا نشد.","error")
        else:
            conn.execute("INSERT INTO customer_notifications(customer_id,title,body) VALUES(%s,%s,%s)",
                (customer_id,"یادآوری سبد خرید","سبد خرید شما هنوز تکمیل نشده است. در صورت تمایل می‌توانید خرید خود را ادامه دهید."))
            conn.execute("UPDATE store_abandoned_carts SET reminder_sent_at=NOW() WHERE customer_id=%s",(customer_id,))
            audit(conn,"abandoned_cart_reminder","customer",customer_id)
            flash("یادآوری داخل حساب مشتری ثبت شد.","success")
    return redirect(url_for("advanced.dashboard")+"#abandoned-carts")


@advanced_bp.post("/loyalty")
@admin_required
def loyalty_adjust():
    try:
        cid=int(request.form.get("customer_id","0")); points=int(request.form.get("points","0"))
        reason=request.form.get("reason","").strip()[:300]
        if cid<1 or points==0 or abs(points)>100000: raise ValueError
        with DB() as conn:
            if not conn.execute("SELECT id FROM customers WHERE id=%s",(cid,)).fetchone(): abort(404)
            conn.execute("INSERT INTO customer_loyalty_ledger(customer_id,points,reason) VALUES(%s,%s,%s)",(cid,points,reason))
            audit(conn,"loyalty_adjusted","customer",cid,"points="+str(points))
        flash("امتیاز مشتری ثبت شد.","success")
    except (ValueError,TypeError):
        flash("اطلاعات امتیاز معتبر نیست.","error")
    return redirect(url_for("advanced.dashboard")+"#loyalty")

@advanced_bp.get("/export/<kind>.xlsx")
@admin_required
def export_xlsx(kind):
    data={
        "products":("SELECT id,name,price,stock,min_stock FROM products ORDER BY id",["شناسه","نام محصول","قیمت","موجودی","حد هشدار"]),
        "orders":("SELECT id,customer_id,total_amount,status,created_at FROM store_orders ORDER BY id DESC",["شماره سفارش","شناسه مشتری","مبلغ","وضعیت","تاریخ"]),
        "customers":("SELECT id,first_name,last_name,phone,is_active,created_at FROM customers ORDER BY id",["شناسه","نام","نام خانوادگی","موبایل","فعال","تاریخ"])}
    if kind not in data: abort(404)
    sql,headers=data[kind]
    workbook=Workbook(write_only=True)
    sheet=workbook.create_sheet(title={"products":"محصولات","orders":"سفارش‌ها","customers":"مشتریان"}[kind])
    sheet.append(headers)
    with DB() as conn:
        for row in conn.execute(sql).fetchall():
            sheet.append([row.get(k) for k in {
                "products":["id","name","price","stock","min_stock"],
                "orders":["id","customer_id","total_amount","status","created_at"],
                "customers":["id","first_name","last_name","phone","is_active","created_at"]}[kind]])
    output=io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return Response(output.getvalue(),mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":f"attachment; filename={kind}.xlsx"})


@advanced_bp.get("/export/<kind>.csv")
@admin_required
def export_csv(kind):
    data={
        "products":("SELECT id,name,price,stock,min_stock FROM products ORDER BY id",["id","name","price","stock","min_stock"]),
        "orders":("SELECT id,customer_id,total_amount,status,created_at FROM store_orders ORDER BY id DESC",["id","customer_id","total_amount","status","created_at"]),
        "customers":("SELECT id,first_name,last_name,phone,is_active,created_at FROM customers ORDER BY id",["id","first_name","last_name","phone","is_active","created_at"])}
    if kind not in data: abort(404)
    sql,keys=data[kind]; output=io.StringIO(); writer=csv.writer(output); writer.writerow(keys)
    with DB() as conn:
        for row in conn.execute(sql).fetchall(): writer.writerow([row.get(k) for k in keys])
    return Response("\ufeff"+output.getvalue(),mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition":"attachment; filename="+kind+".csv"})
