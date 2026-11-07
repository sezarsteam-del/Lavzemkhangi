import os, re, hmac, hashlib, secrets, json, urllib.request
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

customer_bp = Blueprint("customer", __name__)
DB = None
SECRET = None

def init_customer_store(db_func, secret_key):
    global DB, SECRET
    DB, SECRET = db_func, secret_key

def ensure_customer_schema():
    if DB is None: return
    with DB() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS customers (
            id BIGSERIAL PRIMARY KEY, phone TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL, last_name TEXT NOT NULL,
            password_hash TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_login_codes (
            id BIGSERIAL PRIMARY KEY, phone TEXT NOT NULL, code_hash TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL, attempts SMALLINT NOT NULL DEFAULT 0,
            consumed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_orders (
            id BIGSERIAL PRIMARY KEY, customer_id BIGINT NOT NULL REFERENCES customers(id),
            total_amount BIGINT NOT NULL CHECK(total_amount > 0),
            status TEXT NOT NULL DEFAULT 'pending_payment',
            shipping_address TEXT NOT NULL, payment_provider TEXT,
            payment_authority TEXT, payment_reference TEXT, paid_at TIMESTAMPTZ,
            fulfillment_status TEXT NOT NULL DEFAULT 'awaiting_confirmation',
            tracking_code TEXT, shipped_at TIMESTAMPTZ, delivered_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_order_items (
            id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES store_orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id), product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0), unit_price BIGINT NOT NULL,
            line_total BIGINT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_store_orders_customer ON store_orders(customer_id,created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_codes_phone ON customer_login_codes(phone,created_at DESC)")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS sale_id BIGINT REFERENCES sales(id) ON DELETE RESTRICT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS fulfillment_status TEXT NOT NULL DEFAULT 'awaiting_confirmation'")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS tracking_code TEXT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_store_orders_authority ON store_orders(payment_authority) WHERE payment_authority IS NOT NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_addresses (
            id BIGSERIAL PRIMARY KEY, customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'خانه', recipient_name TEXT NOT NULL,
            recipient_phone TEXT NOT NULL, address TEXT NOT NULL,
            postal_code TEXT NOT NULL DEFAULT '', is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_addresses_owner ON customer_addresses(customer_id,id)")

def clean_phone(value):
    value = str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩","01234567890123456789"))
    value = re.sub(r"[\s\-()]", "", value)
    if value.startswith("+98"): value = "0" + value[3:]
    elif value.startswith("98") and len(value) == 12: value = "0" + value[2:]
    if not re.fullmatch(r"09\d{9}", value): raise ValueError("شماره موبایل معتبر نیست.")
    return value

def current_customer():
    cid = session.get("customer_id")
    if not cid: return None
    with DB() as conn:
        return conn.execute("SELECT id,phone,first_name,last_name FROM customers WHERE id=%s AND is_active=TRUE",(cid,)).fetchone()

def customer_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_customer():
            flash("برای ادامه ابتدا وارد حساب مشتری شوید.","error")
            return redirect(url_for("customer.customer_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def otp_hash(phone, code):
    return hmac.new(SECRET.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()

def send_otp(phone, code):
    key = os.environ.get("GHASEDAK_API_KEY","").strip()
    if not key: raise RuntimeError("کلید GHASEDAK_API_KEY در هاست تنظیم نشده است.")
    payload = {"message":f"کد ورود فروشگاه لوازم خانگی: {code} - اعتبار ۵ دقیقه","receptor":phone,"clientReferenceId":secrets.token_hex(16),"udh":False}
    line = os.environ.get("GHASEDAK_LINE_NUMBER","").strip()
    if line: payload["lineNumber"] = line
    req = urllib.request.Request("https://gateway.ghasedak.me/rest/api/v1/WebService/SendSingleSMS",data=json.dumps(payload,ensure_ascii=False).encode(),headers={"ApiKey":key,"Content-Type":"application/json","Accept":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=15) as res: result=json.loads(res.read(50000).decode("utf-8","replace"))
    if not (result.get("IsSuccess") is True or str(result.get("StatusCode",""))=="200"):
        raise RuntimeError("سرویس پیامک ارسال کد را تأیید نکرد.")

@customer_bp.route("/account/register",methods=["GET","POST"])
def customer_register():
    if current_customer(): return redirect(url_for("customer.customer_account"))
    if request.method=="POST":
        try:
            phone=clean_phone(request.form.get("phone")); first=request.form.get("first_name","").strip()[:80]; last=request.form.get("last_name","").strip()[:80]; password=request.form.get("password","")
            if not first or not last: raise ValueError("نام و نام خانوادگی را وارد کنید.")
            if len(password)<10 or len(password)>128: raise ValueError("رمز عبور باید بین ۱۰ تا ۱۲۸ نویسه باشد.")
            with DB() as conn:
                row=conn.execute("INSERT INTO customers(phone,first_name,last_name,password_hash) VALUES(%s,%s,%s,%s) RETURNING id",(phone,first,last,generate_password_hash(password))).fetchone()
            session.pop("customer_id", None); session.permanent=True; session["customer_id"]=row["id"]
            flash("حساب مشتری ساخته شد.","success"); return redirect(url_for("customer.customer_account"))
        except ValueError as e: flash(str(e),"error")
        except Exception: flash("ثبت‌نام انجام نشد؛ ممکن است شماره قبلاً ثبت شده باشد.","error")
    return render_template("customer_register.html")

@customer_bp.route("/account/login",methods=["GET","POST"])
def customer_login():
    if current_customer(): return redirect(url_for("customer.customer_account"))
    if request.method=="POST":
        try:
            phone=clean_phone(request.form.get("phone"))
            with DB() as conn: user=conn.execute("SELECT id,password_hash FROM customers WHERE phone=%s AND is_active=TRUE",(phone,)).fetchone()
            if not user: raise ValueError("حسابی با این شماره پیدا نشد. ابتدا ثبت‌نام کنید.")
            mode=request.form.get("mode","password")
            if mode=="password":
                if not user["password_hash"] or not check_password_hash(user["password_hash"],request.form.get("password","")): raise ValueError("شماره موبایل یا رمز عبور صحیح نیست.")
                session.pop("customer_id", None); session.permanent=True; session["customer_id"]=user["id"]
                flash("با موفقیت وارد حساب شدید.","success"); return redirect(url_for("customer.customer_account"))
            if mode=="otp":
                code=f"{secrets.randbelow(1000000):06d}"
                with DB() as conn:
                    conn.execute("INSERT INTO customer_login_codes(phone,code_hash,expires_at) VALUES(%s,%s,NOW()+INTERVAL '5 minutes')",(phone,otp_hash(phone,code)))
                    conn.execute("DELETE FROM customer_login_codes WHERE phone=%s AND consumed_at IS NULL AND created_at<NOW()-INTERVAL '30 minutes'",(phone,))
                send_otp(phone,code); session["pending_customer_phone"]=phone
                return redirect(url_for("customer.customer_verify"))
            raise ValueError("روش ورود معتبر نیست.")
        except ValueError as e: flash(str(e),"error")
        except Exception as e: flash(str(e) if isinstance(e,RuntimeError) else "ورود پیامکی انجام نشد؛ تنظیمات پیامک را بررسی کنید.","error")
    return render_template("customer_login.html")

@customer_bp.route("/account/verify",methods=["GET","POST"])
def customer_verify():
    phone=session.get("pending_customer_phone")
    if not phone: return redirect(url_for("customer.customer_login"))
    if request.method=="POST":
        code="".join(c for c in request.form.get("code","") if c.isdigit())
        if not re.fullmatch(r"\d{6}",code): flash("کد باید ۶ رقم باشد.","error"); return render_template("customer_verify.html")
        with DB() as conn:
            challenge=conn.execute("SELECT id,code_hash,attempts FROM customer_login_codes WHERE phone=%s AND consumed_at IS NULL AND expires_at>NOW() ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(phone,)).fetchone()
            if not challenge or challenge["attempts"]>=5: flash("کد منقضی شده یا تعداد تلاش‌ها تمام شده است.","error"); return render_template("customer_verify.html"),429
            if not hmac.compare_digest(challenge["code_hash"],otp_hash(phone,code)):
                conn.execute("UPDATE customer_login_codes SET attempts=attempts+1 WHERE id=%s",(challenge["id"],)); flash("کد صحیح نیست.","error"); return render_template("customer_verify.html"),400
            user=conn.execute("SELECT id FROM customers WHERE phone=%s AND is_active=TRUE",(phone,)).fetchone()
            conn.execute("UPDATE customer_login_codes SET consumed_at=NOW() WHERE id=%s",(challenge["id"],))
        if not user: session.pop("pending_customer_phone",None); flash("حساب پیدا نشد؛ ابتدا ثبت‌نام کنید.","error"); return redirect(url_for("customer.customer_register"))
        session.clear(); session.permanent=True; session["customer_id"]=user["id"]; session["_csrf"]=secrets.token_urlsafe(32)
        flash("ورود پیامکی موفق بود.","success"); return redirect(url_for("customer.customer_account"))
    return render_template("customer_verify.html")

@customer_bp.route("/account/logout",methods=["POST"])
@customer_required
def customer_logout():
    session.pop("customer_id", None); session.pop("pending_customer_phone", None); flash("از حساب خارج شدید.","success"); return redirect(url_for("index"))

@customer_bp.route("/account")
@customer_required
def customer_account():
    user=current_customer()
    with DB() as conn: orders=conn.execute("SELECT id,total_amount,status,fulfillment_status,tracking_code,created_at FROM store_orders WHERE customer_id=%s ORDER BY created_at DESC LIMIT 50",(user["id"],)).fetchall()
    return render_template("customer_account.html",customer=user,orders=orders)

@customer_bp.route("/cart",methods=["GET","POST"])
def cart():
    basket=session.get("cart",{})
    if request.method=="POST":
        action=request.form.get("action","add"); pid=request.form.get("product_id","")
        if action=="add":
            if not pid.isdigit(): abort(400)
            key=str(int(pid)); basket[key]=min(99,int(basket.get(key,0))+1); session["cart"]=basket; flash("محصول به سبد خرید اضافه شد.","success")
        elif action=="update":
            new={}
            for key,value in request.form.items():
                if key.startswith("qty_") and key[4:].isdigit() and value.isdigit() and 1<=int(value)<=99: new[str(int(key[4:]))]=int(value)
            session["cart"]=new; flash("سبد خرید به‌روزرسانی شد.","success")
        elif action=="remove" and pid.isdigit():
            basket.pop(str(int(pid)),None); session["cart"]=basket
        return redirect(url_for("customer.cart"))
    items=[]; total=0
    if basket:
        ids=[int(k) for k in basket if str(k).isdigit()]
        with DB() as conn: products=conn.execute("SELECT id,name,price,image_url,stock FROM products WHERE id=ANY(%s) AND is_active=TRUE",(ids,)).fetchall()
        for p in products:
            qty=int(basket.get(str(p["id"]),0)); line=int(p["price"])*qty; items.append({**p,"quantity":qty,"line_total":line}); total+=line
    return render_template("cart.html",items=items,total=total)

@customer_bp.route("/checkout",methods=["GET","POST"])
@customer_required
def checkout():
    gateway=os.environ.get("PAYMENT_GATEWAY","").strip().lower()
    if gateway not in {"zarinpal","idpay","nextpay"}:
        flash("برای پرداخت آنلاین باید ارائه‌دهنده درگاه و کلید پذیرنده در هاست مشخص شود.","error"); return redirect(url_for("customer.cart"))
    basket=session.get("cart",{})
    if not basket: flash("سبد خرید خالی است.","error"); return redirect(url_for("customer.cart"))
    user=current_customer()
    with DB() as conn:
        addresses=conn.execute("SELECT id,title,recipient_name,recipient_phone,address,postal_code,is_default FROM customer_addresses WHERE customer_id=%s ORDER BY is_default DESC,id DESC",(user["id"],)).fetchall()
    if request.method=="POST":
        address=request.form.get("shipping_address","").strip()
        if len(address)<15 or len(address)>1000: flash("آدرس باید بین ۱۵ تا ۱۰۰۰ نویسه باشد.","error"); return render_template("checkout.html",customer=user,addresses=addresses)
        try:
            with DB() as conn:
                locked=[]; total=0
                for pid in sorted(int(k) for k in basket if str(k).isdigit()):
                    p=conn.execute("SELECT id,name,price,stock FROM products WHERE id=%s AND is_active=TRUE FOR UPDATE",(pid,)).fetchone(); qty=int(basket.get(str(pid),0))
                    if not p or qty<1 or qty>p["stock"]: raise ValueError("موجودی یکی از کالاها کافی نیست؛ سبد خرید را اصلاح کنید.")
                    locked.append((p,qty)); total+=int(p["price"])*qty
                if total<=0: raise ValueError("مبلغ سفارش معتبر نیست.")
                order=conn.execute("INSERT INTO store_orders(customer_id,total_amount,shipping_address,payment_provider) VALUES(%s,%s,%s,%s) RETURNING id",(user["id"],total,address,gateway)).fetchone()
                for p,qty in locked: conn.execute("INSERT INTO store_order_items(order_id,product_id,product_name,quantity,unit_price,line_total) VALUES(%s,%s,%s,%s,%s,%s)",(order["id"],p["id"],p["name"],qty,p["price"],p["price"]*qty))
            session["cart"]={}; flash("سفارش اولیه ثبت شد؛ اتصال درگاه و تأیید بانکی هنوز باید تکمیل شود.","warning"); return redirect(url_for("customer.customer_account"))
        except ValueError as e: flash(str(e),"error")
    return render_template("checkout.html",customer=user)

@customer_bp.route("/account/orders/<int:order_id>")
@customer_required
def customer_order_detail(order_id):
    user=current_customer()
    with DB() as conn:
        order=conn.execute("SELECT * FROM store_orders WHERE id=%s AND customer_id=%s",(order_id,user["id"])).fetchone()
        if not order: abort(404)
        items=conn.execute("SELECT product_name,quantity,unit_price,line_total FROM store_order_items WHERE order_id=%s ORDER BY id",(order_id,)).fetchall()
    return render_template("customer_order_detail.html",order=order,items=items)


# ---------------- Customer profile, saved addresses and order management ----------------

@customer_bp.route("/account/profile", methods=["GET", "POST"])
@customer_required
def customer_profile():
    user = current_customer()
    if request.method == "POST":
        first = request.form.get("first_name", "").strip()[:80]
        last = request.form.get("last_name", "").strip()[:80]
        if not first or not last:
            flash("نام و نام خانوادگی را کامل وارد کنید.", "error")
        else:
            with DB() as conn:
                conn.execute("UPDATE customers SET first_name=%s,last_name=%s WHERE id=%s", (first, last, user["id"]))
            flash("اطلاعات حساب به‌روزرسانی شد.", "success")
            return redirect(url_for("customer.customer_profile"))
    return render_template("customer_profile.html", customer=user)

@customer_bp.route("/account/security", methods=["GET", "POST"])
@customer_required
def customer_security():
    user = current_customer()
    if request.method == "POST":
        old = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        with DB() as conn:
            row = conn.execute("SELECT password_hash FROM customers WHERE id=%s AND is_active=TRUE", (user["id"],)).fetchone()
            if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], old):
                flash("رمز عبور فعلی صحیح نیست.", "error")
            elif len(new) < 10 or len(new) > 128:
                flash("رمز جدید باید بین ۱۰ تا ۱۲۸ نویسه باشد.", "error")
            elif new != confirm:
                flash("تکرار رمز جدید مطابقت ندارد.", "error")
            else:
                conn.execute("UPDATE customers SET password_hash=%s WHERE id=%s", (generate_password_hash(new), user["id"]))
                flash("رمز عبور با موفقیت تغییر کرد.", "success")
                return redirect(url_for("customer.customer_security"))
    return render_template("customer_security.html", customer=user)

@customer_bp.route("/account/addresses", methods=["GET", "POST"])
@customer_required
def customer_addresses():
    user = current_customer()
    if request.method == "POST":
        title = request.form.get("title", "خانه").strip()[:40] or "خانه"
        recipient = request.form.get("recipient_name", "").strip()[:100]
        phone = clean_phone(request.form.get("recipient_phone", ""))
        address = request.form.get("address", "").strip()
        postal = re.sub(r"\D", "", request.form.get("postal_code", ""))[:10]
        if not recipient or len(address) < 15 or len(address) > 1000:
            flash("نام گیرنده و نشانی کامل را وارد کنید.", "error")
        elif postal and len(postal) != 10:
            flash("کد پستی باید ۱۰ رقم باشد.", "error")
        else:
            with DB() as conn:
                make_default = request.form.get("is_default") == "1"
                if make_default:
                    conn.execute("UPDATE customer_addresses SET is_default=FALSE WHERE customer_id=%s", (user["id"],))
                conn.execute("""INSERT INTO customer_addresses(customer_id,title,recipient_name,recipient_phone,address,postal_code,is_default)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (user["id"], title, recipient, phone, address, postal, make_default))
            flash("نشانی ذخیره شد.", "success")
            return redirect(url_for("customer.customer_addresses"))
    with DB() as conn:
        addresses = conn.execute("SELECT * FROM customer_addresses WHERE customer_id=%s ORDER BY is_default DESC,id DESC", (user["id"],)).fetchall()
    return render_template("customer_addresses.html", customer=user, addresses=addresses)

@customer_bp.route("/account/addresses/<int:address_id>/delete", methods=["POST"])
@customer_required
def customer_address_delete(address_id):
    user = current_customer()
    with DB() as conn:
        conn.execute("DELETE FROM customer_addresses WHERE id=%s AND customer_id=%s", (address_id, user["id"]))
    flash("نشانی حذف شد.", "success")
    return redirect(url_for("customer.customer_addresses"))

@customer_bp.route("/account/orders/<int:order_id>/cancel", methods=["POST"])
@customer_required
def customer_cancel_order(order_id):
    user = current_customer()
    with DB() as conn:
        order = conn.execute("SELECT id,status,fulfillment_status FROM store_orders WHERE id=%s AND customer_id=%s FOR UPDATE", (order_id, user["id"])).fetchone()
        if not order:
            abort(404)
        if order["status"] != "pending_payment" or order["fulfillment_status"] in ("shipped", "delivered"):
            flash("این سفارش از این بخش قابل لغو نیست. برای سفارش پرداخت‌شده با پشتیبانی تماس بگیرید.", "error")
        else:
            conn.execute("UPDATE store_orders SET status='cancelled',fulfillment_status='cancelled',cancelled_at=NOW() WHERE id=%s", (order_id,))
            flash("سفارش لغو شد.", "success")
    return redirect(url_for("customer.customer_order_detail", order_id=order_id))

@customer_bp.route("/admin/customer-orders")
def customer_orders_admin():
    if not session.get("admin"):
        flash("ابتدا وارد حساب مدیر شوید.", "error")
        return redirect(url_for("login"))
    with DB() as conn:
        orders = conn.execute("""SELECT o.id,o.total_amount,o.status,o.fulfillment_status,o.tracking_code,o.created_at,
            c.first_name,c.last_name,c.phone FROM store_orders o JOIN customers c ON c.id=o.customer_id
            ORDER BY o.created_at DESC LIMIT 200""").fetchall()
    return render_template("admin_customer_orders.html", orders=orders)

@customer_bp.route("/admin/customer-orders/<int:order_id>/status", methods=["POST"])
def customer_order_admin_status(order_id):
    if not session.get("admin"):
        flash("ابتدا وارد حساب مدیر شوید.", "error")
        return redirect(url_for("login"))
    status = request.form.get("fulfillment_status", "")
    allowed = {"awaiting_confirmation", "processing", "ready_to_ship", "shipped", "delivered"}
    if status not in allowed:
        abort(400)
    tracking = request.form.get("tracking_code", "").strip()[:100]
    with DB() as conn:
        order = conn.execute("SELECT id,status,fulfillment_status FROM store_orders WHERE id=%s FOR UPDATE", (order_id,)).fetchone()
        if not order:
            abort(404)
        if order["status"] != "paid":
            flash("وضعیت ارسال فقط برای سفارش پرداخت‌شده قابل تغییر است.", "error")
        else:
            conn.execute("""UPDATE store_orders SET fulfillment_status=%s,tracking_code=%s,
                shipped_at=CASE WHEN %s='shipped' THEN COALESCE(shipped_at,NOW()) ELSE shipped_at END,
                delivered_at=CASE WHEN %s='delivered' THEN COALESCE(delivered_at,NOW()) ELSE delivered_at END
                WHERE id=%s""", (status, tracking or None, status, status, order_id))
            flash("وضعیت سفارش به‌روزرسانی شد.", "success")
    return redirect(url_for("customer.customer_orders_admin"))
