import os, re, hmac, hashlib, secrets, json, urllib.request
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from store_extras import coupon_for_update, release_coupon_reservation, create_customer_notification, send_customer_sms

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
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS payment_authority TEXT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS payment_reference TEXT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS payment_provider TEXT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS fulfillment_status TEXT NOT NULL DEFAULT 'awaiting_confirmation'")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS tracking_code TEXT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS subtotal_amount BIGINT NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS discount_amount BIGINT NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS coupon_id BIGINT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS coupon_code TEXT")
        conn.execute("ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS coupon_redeemed BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_store_orders_authority ON store_orders(payment_authority) WHERE payment_authority IS NOT NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS customer_addresses (
            id BIGSERIAL PRIMARY KEY, customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'خانه', recipient_name TEXT NOT NULL,
            recipient_phone TEXT NOT NULL, address TEXT NOT NULL,
            postal_code TEXT NOT NULL DEFAULT '', is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_addresses_owner ON customer_addresses(customer_id,id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS store_abandoned_carts (
            customer_id BIGINT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
            cart_items JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reminder_sent_at TIMESTAMPTZ
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_abandoned_carts_updated ON store_abandoned_carts(last_updated DESC)")


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

def enforce_otp_rate_limit(conn, phone):
    stats = conn.execute("""SELECT COUNT(*) AS total,
        MAX(created_at) AS latest FROM customer_login_codes
        WHERE phone=%s AND created_at>NOW()-INTERVAL '1 hour'""", (phone,)).fetchone()
    if int(stats["total"] or 0) >= 5:
        raise ValueError("تعداد درخواست کد بیش از حد مجاز است؛ یک ساعت دیگر تلاش کنید.")
    if stats["latest"] and (stats["latest"] > __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(seconds=60)):
        raise ValueError("برای دریافت کد جدید حداقل ۶۰ ثانیه صبر کنید.")

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
                    enforce_otp_rate_limit(conn, phone)
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
        session.pop("customer_id", None); session.permanent=True; session["customer_id"]=user["id"]
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
    with DB() as conn:
        orders=conn.execute("SELECT id,total_amount,status,fulfillment_status,tracking_code,created_at FROM store_orders WHERE customer_id=%s ORDER BY created_at DESC LIMIT 50",(user["id"],)).fetchall()
        loyalty=conn.execute("SELECT COALESCE(SUM(points),0) AS points FROM customer_loyalty_ledger WHERE customer_id=%s",(user["id"],)).fetchone()
    return render_template("customer_account.html",customer=user,orders=orders,loyalty_points=loyalty["points"])

def persist_abandoned_cart(customer_id, basket):
    if not customer_id:
        return
    clean = {str(int(k)): int(v) for k, v in (basket or {}).items()
             if str(k).isdigit() and str(v).isdigit() and 1 <= int(v) <= 99}
    with DB() as conn:
        if clean:
            conn.execute("""INSERT INTO store_abandoned_carts(customer_id,cart_items,last_updated,reminder_sent_at)
                VALUES(%s,%s::jsonb,NOW(),NULL)
                ON CONFLICT(customer_id) DO UPDATE SET cart_items=EXCLUDED.cart_items,
                last_updated=NOW(),reminder_sent_at=NULL""",
                (customer_id, json.dumps(clean)))
        else:
            conn.execute("DELETE FROM store_abandoned_carts WHERE customer_id=%s",(customer_id,))


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
        basket=session.get("cart",{})
        persist_abandoned_cart(session.get("customer_id"), basket)
        return redirect(url_for("customer.cart"))
    items=[]; total=0
    if basket:
        ids=[int(k) for k in basket if str(k).isdigit()]
        with DB() as conn: products=conn.execute("SELECT id,name,price,image_url,stock FROM products WHERE id=ANY(%s) AND is_active=TRUE",(ids,)).fetchall()
        for p in products:
            qty=int(basket.get(str(p["id"]),0)); line=int(p["price"])*qty; items.append({**p,"quantity":qty,"line_total":line}); total+=line
    return render_template("cart.html",items=items,total=total)

def zarinpal_config():
    merchant_id = os.environ.get("ZARINPAL_MERCHANT_ID", "").strip()
    sandbox = os.environ.get("ZARINPAL_SANDBOX", "true").strip().lower() not in ("0", "false", "no", "off")
    if sandbox:
        api_base = "https://sandbox.zarinpal.com/pg/v4"
        start_base = "https://sandbox.zarinpal.com/pg/StartPay/"
    else:
        api_base = "https://payment.zarinpal.com/pg/v4"
        start_base = "https://www.zarinpal.com/pg/StartPay/"
    return merchant_id, api_base, start_base, sandbox


def zarinpal_is_configured():
    return bool(os.environ.get("ZARINPAL_MERCHANT_ID", "").strip())


def zarinpal_api(action, payload):
    merchant_id, api_base, _start_base, _sandbox = zarinpal_config()
    if not merchant_id:
        raise RuntimeError("شناسه پذیرنده زرین‌پال در تنظیمات امن هاست ثبت نشده است.")
    body = dict(payload)
    body["merchant_id"] = merchant_id
    req = urllib.request.Request(
        f"{api_base}/payment/{action}.json",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.loads(response.read(100000).decode("utf-8", "replace"))
    return result


def release_order_stock(conn, order_id):
    rows = conn.execute(
        "SELECT product_id,quantity FROM store_order_items WHERE order_id=%s",
        (order_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE products SET stock=stock+%s WHERE id=%s",
            (int(row["quantity"]), row["product_id"]),
        )


def fail_zarinpal_order(order_id):
    with DB() as conn:
        order = conn.execute(
            "SELECT id,status,payment_provider FROM store_orders WHERE id=%s FOR UPDATE",
            (order_id,),
        ).fetchone()
        if order and order["status"] == "pending_payment" and order["payment_provider"] == "zarinpal":
            release_order_stock(conn, order_id)
            release_coupon_reservation(conn, order_id)
            conn.execute(
                "UPDATE store_orders SET status='payment_failed' WHERE id=%s",
                (order_id,),
            )


@customer_bp.route("/checkout",methods=["GET","POST"])
@customer_required
def checkout():
    basket=session.get("cart",{})
    if not basket:
        flash("سبد خرید خالی است.","error")
        return redirect(url_for("customer.cart"))
    user=current_customer()
    with DB() as conn:
        addresses=conn.execute("SELECT id,title,recipient_name,recipient_phone,address,postal_code,is_default FROM customer_addresses WHERE customer_id=%s ORDER BY is_default DESC,id DESC",(user["id"],)).fetchall()
    zarinpal_enabled=zarinpal_is_configured()
    if request.method=="POST":
        address=request.form.get("shipping_address","").strip()
        payment_method=request.form.get("payment_method","manual").strip()
        coupon_code=request.form.get("coupon_code","").strip()
        if payment_method not in ("manual","zarinpal"): abort(400)
        if len(address)<15 or len(address)>1000:
            flash("آدرس باید بین ۱۵ تا ۱۰۰۰ نویسه باشد.","error")
            return render_template("checkout.html",customer=user,addresses=addresses,zarinpal_enabled=zarinpal_enabled)
        if payment_method=="zarinpal" and not zarinpal_enabled:
            flash("درگاه زرین‌پال در تنظیمات هاست فعال نیست.","error")
            return render_template("checkout.html",customer=user,addresses=addresses,zarinpal_enabled=False)
        try:
            with DB() as conn:
                locked=[]; subtotal=0
                ids=sorted({int(k) for k,v in basket.items() if str(k).isdigit() and str(v).isdigit() and 1<=int(v)<=99})
                if not ids: raise ValueError("سبد خرید معتبر نیست؛ دوباره کالاها را انتخاب کنید.")
                for pid in ids:
                    p=conn.execute("SELECT id,name,price,stock FROM products WHERE id=%s AND is_active=TRUE FOR UPDATE",(pid,)).fetchone()
                    qty=int(basket.get(str(pid),0))
                    if not p or qty<1 or qty>int(p["stock"]): raise ValueError("موجودی یکی از کالاها کافی نیست؛ سبد خرید را اصلاح کنید.")
                    locked.append((p,qty)); subtotal+=int(p["price"])*qty
                if subtotal<=0: raise ValueError("مبلغ سفارش معتبر نیست.")
                coupon=coupon_for_update(conn,coupon_code,subtotal) if coupon_code else None
                discount=int(coupon["discount"]) if coupon else 0; payable=int(subtotal)-discount
                order_status="pending_payment" if payment_method=="zarinpal" else "pending_confirmation"
                provider="zarinpal" if payment_method=="zarinpal" else "manual"
                order=conn.execute("""INSERT INTO store_orders (customer_id,total_amount,status,shipping_address,payment_provider,subtotal_amount,discount_amount,coupon_id,coupon_code,coupon_redeemed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",(user["id"],payable,order_status,address,provider,subtotal,discount,coupon["id"] if coupon else None,coupon["code"] if coupon else None,bool(coupon))).fetchone()
                for p,qty in locked:
                    reserved=conn.execute("UPDATE products SET stock=stock-%s WHERE id=%s AND stock>=%s RETURNING id",(qty,p["id"],qty)).fetchone()
                    if not reserved: raise ValueError("موجودی یکی از کالاها هنگام ثبت سفارش تغییر کرد؛ سبد را دوباره بررسی کنید.")
                    conn.execute("INSERT INTO store_order_items(order_id,product_id,product_name,quantity,unit_price,line_total) VALUES(%s,%s,%s,%s,%s,%s)",(order["id"],p["id"],p["name"],qty,p["price"],int(p["price"])*qty))
                create_customer_notification(conn,user["id"],"ثبت سفارش","سفارش شماره "+str(order["id"])+" با مبلغ "+format(payable,",")+" تومان ثبت شد.")
            if payment_method=="zarinpal":
                try:
                    _merchant,_api_base,start_base,_sandbox=zarinpal_config()
                    response=zarinpal_api("request",{"amount":int(payable)*10,"callback_url":url_for("customer.zarinpal_callback",_external=True),"description":"خرید اینترنتی سفارش شماره "+str(order["id"]),"metadata":{"mobile":user["phone"]}})
                    data=response.get("data") or {}; authority=data.get("authority")
                    if data.get("code")!=100 or not authority: raise RuntimeError("زرین‌پال درخواست پرداخت را تأیید نکرد؛ کد پاسخ: "+str(data.get("code","نامشخص")))
                    with DB() as conn: conn.execute("UPDATE store_orders SET payment_authority=%s WHERE id=%s AND status='pending_payment'",(authority,order["id"]))
                    session["cart"]={}; return redirect(start_base+authority)
                except Exception as exc:
                    fail_zarinpal_order(order["id"]); flash("ساخت درگاه پرداخت انجام نشد. مبلغی از شما کسر نشده است. "+str(exc)[:180],"error")
                    return redirect(url_for("customer.customer_order_detail",order_id=order["id"]))
            session["cart"]={}; persist_abandoned_cart(user["id"], {})
            flash("سفارش ثبت شد؛ موجودی کالا رزرو و برای هماهنگی با فروشگاه ارسال شد.","success")
            return redirect(url_for("customer.customer_order_detail",order_id=order["id"]))
        except ValueError as exc: flash(str(exc),"error")
    return render_template("checkout.html",customer=user,addresses=addresses,zarinpal_enabled=zarinpal_enabled)
@customer_bp.route("/payment/zarinpal/callback", methods=["GET"])
def zarinpal_callback():
    authority = (request.args.get("Authority") or "").strip()
    status = (request.args.get("Status") or "").strip().upper()
    if not re.fullmatch(r"[A-Za-z0-9]{10,64}", authority):
        abort(400)
    with DB() as conn:
        order = conn.execute(
            "SELECT id,customer_id,total_amount,status,payment_provider FROM store_orders WHERE payment_authority=%s",
            (authority,),
        ).fetchone()
    if not order or order["payment_provider"] != "zarinpal":
        abort(404)
    order_id = order["id"]
    if order["status"] == "paid":
        flash("پرداخت این سفارش قبلاً تأیید شده است.","success")
        return redirect(url_for("customer.customer_order_detail",order_id=order_id))
    if order["status"] != "pending_payment":
        flash("وضعیت این سفارش تغییر کرده است؛ برای پیگیری با فروشگاه تماس بگیرید.","error")
        return redirect(url_for("customer.customer_order_detail",order_id=order_id))
    if status != "OK":
        fail_zarinpal_order(order_id)
        flash("پرداخت انجام نشد یا از سوی شما لغو شد؛ مبلغی به‌عنوان پرداخت موفق ثبت نشده است.","error")
        return redirect(url_for("customer.customer_order_detail",order_id=order_id))
    try:
        verification = zarinpal_api("verify", {
            "amount": int(order["total_amount"]) * 10,
            "authority": authority,
        })
        data = verification.get("data") or {}
        code = data.get("code")
        if code not in (100, 101):
            fail_zarinpal_order(order_id)
            flash("زرین‌پال پرداخت را تأیید نکرد. اگر مبلغی از حساب شما کسر شده، با پشتیبانی تماس بگیرید.","error")
            return redirect(url_for("customer.customer_login"))
        reference = str(data.get("ref_id") or "")
        with DB() as conn:
            locked = conn.execute(
                "SELECT id,status FROM store_orders WHERE id=%s FOR UPDATE",
                (order_id,),
            ).fetchone()
            if locked and locked["status"] == "pending_payment":
                conn.execute(
                    "UPDATE store_orders SET status='paid',payment_reference=%s,paid_at=NOW() WHERE id=%s",
                    (reference,order_id),
                )
                conn.execute("""INSERT INTO customer_loyalty_ledger(customer_id,points,reason,source_order_id)
                    SELECT customer_id,GREATEST(1,(total_amount/100000)::INTEGER),'امتیاز خرید اینترنتی',id
                    FROM store_orders WHERE id=%s ON CONFLICT(source_order_id) DO NOTHING""",(order_id,))
                create_customer_notification(conn, order["customer_id"], "پرداخت تأیید شد",
                    "پرداخت سفارش شماره "+str(order_id)+" با موفقیت تأیید شد.")
        flash("پرداخت با موفقیت تأیید شد. شماره پیگیری: " + (reference or "ثبت شد"),"success")
        return redirect(url_for("customer.customer_order_detail",order_id=order_id))
    except Exception:
        flash("بررسی پرداخت موقتاً با مشکل روبه‌رو شد. سفارش در انتظار بررسی باقی مانده؛ دوباره تلاش نکنید و با فروشگاه تماس بگیرید.","error")
        return redirect(url_for("customer.customer_login"))


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
        try:
            phone = clean_phone(request.form.get("recipient_phone", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            with DB() as conn:
                addresses = conn.execute("SELECT * FROM customer_addresses WHERE customer_id=%s ORDER BY is_default DESC,id DESC", (user["id"],)).fetchall()
            return render_template("customer_addresses.html", customer=user, addresses=addresses)
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
        order = conn.execute("SELECT id,status,payment_provider,fulfillment_status FROM store_orders WHERE id=%s AND customer_id=%s FOR UPDATE", (order_id, user["id"])).fetchone()
        if not order:
            abort(404)
        if order["status"] == "pending_payment" and order["payment_provider"] == "zarinpal":
            flash("سفارش در مرحله پرداخت آنلاین است و از این صفحه قابل لغو نیست؛ اگر پرداخت را کامل نکرده‌اید از درگاه خارج شوید.", "error")
        elif order["status"] not in ("pending_payment", "pending_confirmation") or order["fulfillment_status"] in ("processing", "ready_to_ship", "shipped", "delivered"):
            flash("این سفارش از این بخش قابل لغو نیست. برای سفارش پرداخت‌شده با پشتیبانی تماس بگیرید.", "error")
        else:
            if order["status"] in ("pending_payment", "pending_confirmation"):
                release_order_stock(conn, order_id)
                release_coupon_reservation(conn, order_id)
            conn.execute("UPDATE store_orders SET status='cancelled',fulfillment_status='cancelled',cancelled_at=NOW() WHERE id=%s", (order_id,))
            flash("سفارش لغو شد.", "success")
    return redirect(url_for("customer.customer_order_detail", order_id=order_id))

@customer_bp.route("/admin/customer-orders")
def customer_orders_admin():
    if not session.get("admin"):
        flash("ابتدا وارد حساب مدیر شوید.", "error")
        return redirect(url_for("login"))
    with DB() as conn:
        orders = conn.execute("""SELECT o.id,o.total_amount,o.status,o.payment_provider,o.fulfillment_status,o.tracking_code,o.created_at,
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
    notify_customer = False
    with DB() as conn:
        order = conn.execute("SELECT id,customer_id,status,fulfillment_status FROM store_orders WHERE id=%s FOR UPDATE", (order_id,)).fetchone()
        if not order:
            abort(404)
        if order["status"] not in ("pending_confirmation", "pending_payment", "paid", "paid_stock_issue"):
            flash("وضعیت این سفارش قابل تغییر نیست.", "error")
        elif order["status"] == "pending_confirmation" and status in ("shipped", "delivered"):
            flash("ابتدا سفارش را تأیید و آماده‌سازی کنید؛ وضعیت پرداخت دستی نیز باید جداگانه با مشتری هماهنگ شود.", "error")
        else:
            conn.execute("""UPDATE store_orders SET fulfillment_status=%s,tracking_code=%s,
                shipped_at=CASE WHEN %s='shipped' THEN COALESCE(shipped_at,NOW()) ELSE shipped_at END,
                delivered_at=CASE WHEN %s='delivered' THEN COALESCE(delivered_at,NOW()) ELSE delivered_at END
                WHERE id=%s""", (status, tracking or None, status, status, order_id))
            status_labels = {
                "awaiting_confirmation": "در انتظار تأیید",
                "processing": "در حال آماده‌سازی",
                "ready_to_ship": "آماده ارسال",
                "shipped": "ارسال‌شده",
                "delivered": "تحویل‌شده",
            }
            conn.execute(
                "INSERT INTO customer_notifications(customer_id,title,body) VALUES(%s,%s,%s)",
                (order["customer_id"], "به‌روزرسانی سفارش",
                 "وضعیت سفارش شماره " + str(order_id) + " به «" + status_labels[status] + "» تغییر کرد."
                 + ((" کد رهگیری: " + tracking) if status == "shipped" and tracking else ""))
            )
            notify_customer = True
            flash("وضعیت سفارش به‌روزرسانی شد.", "success")
    if notify_customer:
        with DB() as conn:
            recipient=conn.execute("SELECT phone,sms_notifications_enabled FROM customers WHERE id=%s",(order["customer_id"],)).fetchone()
    if notify_customer and recipient and recipient["sms_notifications_enabled"]:
        labels={"awaiting_confirmation":"در انتظار تأیید","processing":"در حال آماده‌سازی","ready_to_ship":"آماده ارسال","shipped":"ارسال‌شده","delivered":"تحویل‌شده"}
        sms_text="وضعیت سفارش "+str(order_id)+" تغییر کرد: "+labels[status]
        if status=="shipped" and tracking: sms_text+=". کد رهگیری: "+tracking
        send_customer_sms(recipient["phone"],sms_text)
    return redirect(url_for("customer.customer_orders_admin"))


@customer_bp.route("/admin/customer-orders/<int:order_id>/confirm-payment", methods=["POST"])
def customer_order_admin_confirm_payment(order_id):
    if not session.get("admin"):
        flash("ابتدا وارد حساب مدیر شوید.", "error")
        return redirect(url_for("login"))
    notify_customer=False
    with DB() as conn:
        order=conn.execute("SELECT id,customer_id,status,payment_provider,total_amount FROM store_orders WHERE id=%s FOR UPDATE",(order_id,)).fetchone()
        if not order: abort(404)
        if order["status"]!="pending_confirmation" or order["payment_provider"]!="manual":
            flash("این سفارش در انتظار تأیید پرداخت دستی نیست.", "error")
        else:
            conn.execute("UPDATE store_orders SET status='paid',paid_at=NOW() WHERE id=%s",(order_id,))
            conn.execute("""INSERT INTO customer_loyalty_ledger(customer_id,points,reason,source_order_id)
                SELECT customer_id,GREATEST(1,(total_amount/100000)::INTEGER),'امتیاز خرید اینترنتی',id
                FROM store_orders WHERE id=%s ON CONFLICT(source_order_id) DO NOTHING""",(order_id,))
            create_customer_notification(conn,order["customer_id"],"پرداخت سفارش تأیید شد","پرداخت سفارش شماره "+str(order_id)+" توسط فروشگاه تأیید شد.")
            notify_customer=True
            flash("پرداخت سفارش تأیید شد.", "success")
    if notify_customer:
        with DB() as conn:
            recipient=conn.execute("SELECT phone,sms_notifications_enabled FROM customers WHERE id=%s",(order["customer_id"],)).fetchone()
        if recipient and recipient["sms_notifications_enabled"]:
            send_customer_sms(recipient["phone"],"پرداخت سفارش شماره "+str(order_id)+" توسط فروشگاه تأیید شد.")
    return redirect(url_for("customer.customer_orders_admin"))


@customer_bp.route("/account/password/reset", methods=["GET", "POST"])
def customer_password_reset():
    if request.method == "POST":
        try:
            phone = clean_phone(request.form.get("phone", ""))
            with DB() as conn:
                user = conn.execute("SELECT id FROM customers WHERE phone=%s AND is_active=TRUE", (phone,)).fetchone()
                if not user:
                    flash("اگر حساب فعالی با این شماره وجود داشته باشد، کد بازیابی ارسال می‌شود.", "success")
                    return render_template("customer_password_reset.html")
                code = f"{secrets.randbelow(1000000):06d}"
                enforce_otp_rate_limit(conn, phone)
                conn.execute("INSERT INTO customer_login_codes(phone,code_hash,expires_at) VALUES(%s,%s,NOW()+INTERVAL '5 minutes')", (phone, otp_hash(phone, code)))
                conn.execute("DELETE FROM customer_login_codes WHERE phone=%s AND consumed_at IS NULL AND created_at<NOW()-INTERVAL '30 minutes'", (phone,))
            send_otp(phone, code)
            session["pending_reset_phone"] = phone
            flash("کد بازیابی ارسال شد و پنج دقیقه اعتبار دارد.", "success")
            return redirect(url_for("customer.customer_password_reset_verify"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception:
            flash("ارسال کد بازیابی انجام نشد؛ تنظیمات پیامک را بررسی کنید.", "error")
    return render_template("customer_password_reset.html")

@customer_bp.route("/account/password/reset/verify", methods=["GET", "POST"])
def customer_password_reset_verify():
    phone = session.get("pending_reset_phone")
    if not phone:
        return redirect(url_for("customer.customer_password_reset"))
    if request.method == "POST":
        code = "".join(c for c in request.form.get("code", "") if c.isdigit())
        password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not re.fullmatch(r"\d{6}", code):
            flash("کد باید ۶ رقم باشد.", "error")
        elif len(password) < 10 or len(password) > 128:
            flash("رمز جدید باید بین ۱۰ تا ۱۲۸ نویسه باشد.", "error")
        elif password != confirm:
            flash("تکرار رمز جدید مطابقت ندارد.", "error")
        else:
            with DB() as conn:
                challenge = conn.execute("SELECT id,code_hash,attempts FROM customer_login_codes WHERE phone=%s AND consumed_at IS NULL AND expires_at>NOW() ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (phone,)).fetchone()
                if not challenge or challenge["attempts"] >= 5:
                    flash("کد منقضی شده یا تعداد تلاش‌ها تمام شده است.", "error")
                elif not hmac.compare_digest(challenge["code_hash"], otp_hash(phone, code)):
                    conn.execute("UPDATE customer_login_codes SET attempts=attempts+1 WHERE id=%s", (challenge["id"],))
                    flash("کد صحیح نیست.", "error")
                else:
                    conn.execute("UPDATE customers SET password_hash=%s WHERE phone=%s AND is_active=TRUE", (generate_password_hash(password), phone))
                    conn.execute("UPDATE customer_login_codes SET consumed_at=NOW() WHERE id=%s", (challenge["id"],))
                    session.pop("pending_reset_phone", None)
                    flash("رمز عبور با موفقیت بازیابی شد؛ اکنون وارد شوید.", "success")
                    return redirect(url_for("customer.customer_login"))
    return render_template("customer_password_reset_verify.html")


@customer_bp.route("/account/phone/change", methods=["GET", "POST"])
@customer_required
def customer_phone_change():
    user = current_customer()
    if request.method == "POST":
        try:
            phone = clean_phone(request.form.get("phone", ""))
            with DB() as conn:
                existing = conn.execute("SELECT id FROM customers WHERE phone=%s AND id<>%s", (phone, user["id"])).fetchone()
                if existing:
                    raise ValueError("این شماره موبایل قبلاً در حساب دیگری ثبت شده است.")
                enforce_otp_rate_limit(conn, phone)
                code = f"{secrets.randbelow(1000000):06d}"
                conn.execute("INSERT INTO customer_login_codes(phone,code_hash,expires_at) VALUES(%s,%s,NOW()+INTERVAL '5 minutes')", (phone, otp_hash(phone, code)))
            send_otp(phone, code)
            session["pending_phone_change"] = phone
            flash("کد تأیید به شماره جدید ارسال شد.", "success")
            return redirect(url_for("customer.customer_phone_change_verify"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception:
            flash("ارسال کد تأیید انجام نشد؛ تنظیمات پیامک را بررسی کنید.", "error")
    return render_template("customer_phone_change.html", customer=user)

@customer_bp.route("/account/phone/change/verify", methods=["GET", "POST"])
@customer_required
def customer_phone_change_verify():
    user = current_customer()
    phone = session.get("pending_phone_change")
    if not phone:
        return redirect(url_for("customer.customer_phone_change"))
    if request.method == "POST":
        code = "".join(c for c in request.form.get("code", "") if c.isdigit())
        with DB() as conn:
            challenge = conn.execute("SELECT id,code_hash,attempts FROM customer_login_codes WHERE phone=%s AND consumed_at IS NULL AND expires_at>NOW() ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (phone,)).fetchone()
            if not challenge or challenge["attempts"] >= 5:
                flash("کد منقضی شده یا تعداد تلاش‌ها تمام شده است.", "error")
            elif not re.fullmatch(r"\d{6}", code):
                flash("کد باید ۶ رقم باشد.", "error")
            elif not hmac.compare_digest(challenge["code_hash"], otp_hash(phone, code)):
                conn.execute("UPDATE customer_login_codes SET attempts=attempts+1 WHERE id=%s", (challenge["id"],))
                flash("کد صحیح نیست.", "error")
            else:
                existing = conn.execute("SELECT id FROM customers WHERE phone=%s AND id<>%s", (phone, user["id"])).fetchone()
                if existing:
                    flash("این شماره در حساب دیگری ثبت شده است.", "error")
                else:
                    conn.execute("UPDATE customers SET phone=%s WHERE id=%s", (phone, user["id"]))
                    conn.execute("UPDATE customer_login_codes SET consumed_at=NOW() WHERE id=%s", (challenge["id"],))
                    session.pop("pending_phone_change", None)
                    flash("شماره موبایل حساب با موفقیت تغییر کرد.", "success")
                    return redirect(url_for("customer.customer_profile"))
    return render_template("customer_phone_change_verify.html")
