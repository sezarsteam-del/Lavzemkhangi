import hashlib, hmac, secrets, re
from functools import wraps
from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

android_api_bp = Blueprint("android_api", __name__, url_prefix="/api/android/v1")
DB = SECRET = None

def init_android_api(db_func, secret_key):
    global DB, SECRET
    DB, SECRET = db_func, secret_key

def ensure_android_api_schema():
    with DB() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS android_api_tokens (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_android_tokens_customer ON android_api_tokens(customer_id,expires_at)")

def digest(token):
    return hmac.new(SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()

def error(message, status=400):
    return jsonify({"ok":False,"error":message}), status

def issue(conn, cid):
    token = secrets.token_urlsafe(48)
    conn.execute("INSERT INTO android_api_tokens(customer_id,token_hash,expires_at) VALUES(%s,%s,NOW()+INTERVAL '30 days')",(cid,digest(token)))
    return token

def required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        auth=request.headers.get("Authorization","")
        if not auth.startswith("Bearer "): return error("ابتدا وارد حساب شوید.",401)
        token=auth[7:].strip()
        if len(token)<40 or len(token)>200: return error("نشست معتبر نیست.",401)
        with DB() as conn:
            user=conn.execute("""SELECT c.id,c.phone,c.first_name,c.last_name FROM android_api_tokens t
                JOIN customers c ON c.id=t.customer_id WHERE t.token_hash=%s AND t.revoked_at IS NULL
                AND t.expires_at>NOW() AND c.is_active=TRUE""",(digest(token),)).fetchone()
        if not user: return error("نشست منقضی شده است؛ دوباره وارد شوید.",401)
        g.android_customer=user
        return fn(*args,**kwargs)
    return wrapped

@android_api_bp.get("/health")
def health():
    return jsonify({"ok":True,"service":"lavzemkhangi-android-api","version":1})

@android_api_bp.get("/categories")
def categories():
    with DB() as conn: rows=conn.execute("SELECT id,name FROM categories ORDER BY name").fetchall()
    return jsonify({"ok":True,"categories":rows})

@android_api_bp.get("/products")
def products():
    q=(request.args.get("q") or "").strip()[:100]
    cat=(request.args.get("category_id") or "").strip()
    try: page=max(1,min(int(request.args.get("page","1")),10000))
    except ValueError: page=1
    sql="""SELECT p.id,p.name,p.description,p.price,p.image_url,c.id AS category_id,
        c.name AS category_name,b.name AS brand_name FROM products p
        LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN brands b ON b.id=p.brand_id
        WHERE p.is_active=TRUE"""
    params=[]
    if q:
        sql+=" AND (p.name ILIKE %s OR p.description ILIKE %s OR c.name ILIKE %s OR b.name ILIKE %s)"
        term="%"+q+"%"; params += [term]*4
    if cat:
        if not cat.isdigit(): return error("دسته‌بندی معتبر نیست.")
        sql+=" AND p.category_id=%s"; params.append(int(cat))
    sql+=" ORDER BY p.created_at DESC LIMIT 40 OFFSET %s"; params.append((page-1)*40)
    with DB() as conn: rows=conn.execute(sql,params).fetchall()
    return jsonify({"ok":True,"products":rows,"page":page})

@android_api_bp.post("/auth/register")
def register():
    d=request.get_json(silent=True) or {}
    phone=str(d.get("phone","")).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹","0123456789")).strip()
    first=str(d.get("first_name","")).strip()[:80]; last=str(d.get("last_name","")).strip()[:80]; pw=str(d.get("password",""))
    if not re.fullmatch(r"09\d{9}",phone): return error("شماره موبایل معتبر نیست.")
    if not first or not last: return error("نام و نام خانوادگی را وارد کنید.")
    if not 10<=len(pw)<=128: return error("رمز عبور باید بین ۱۰ تا ۱۲۸ نویسه باشد.")
    try:
        with DB() as conn:
            user=conn.execute("INSERT INTO customers(phone,first_name,last_name,password_hash) VALUES(%s,%s,%s,%s) RETURNING id,phone,first_name,last_name",(phone,first,last,generate_password_hash(pw))).fetchone()
            token=issue(conn,user["id"])
        return jsonify({"ok":True,"token":token,"customer":user}),201
    except Exception: return error("ثبت‌نام انجام نشد؛ ممکن است شماره قبلاً ثبت شده باشد.",409)

@android_api_bp.post("/auth/login")
def login():
    d=request.get_json(silent=True) or {}
    phone=str(d.get("phone","")).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹","0123456789")).strip(); pw=str(d.get("password",""))
    if not re.fullmatch(r"09\d{9}",phone) or not pw: return error("شماره موبایل یا رمز عبور صحیح نیست.",401)
    with DB() as conn:
        user=conn.execute("SELECT id,phone,first_name,last_name,password_hash FROM customers WHERE phone=%s AND is_active=TRUE",(phone,)).fetchone()
        if not user or not user["password_hash"] or not check_password_hash(user["password_hash"],pw): return error("شماره موبایل یا رمز عبور صحیح نیست.",401)
        token=issue(conn,user["id"]); user.pop("password_hash",None)
    return jsonify({"ok":True,"token":token,"customer":user})

@android_api_bp.post("/auth/logout")
@required
def logout():
    token=request.headers.get("Authorization","")[7:].strip()
    with DB() as conn: conn.execute("UPDATE android_api_tokens SET revoked_at=NOW() WHERE token_hash=%s",(digest(token),))
    return jsonify({"ok":True})

@android_api_bp.get("/me")
@required
def me(): return jsonify({"ok":True,"customer":g.android_customer})

@android_api_bp.get("/orders")
@required
def orders():
    with DB() as conn:
        rows=conn.execute("""SELECT id,total_amount,payment_amount,status,fulfillment_status,tracking_code,shipping_address,created_at
            FROM store_orders WHERE customer_id=%s ORDER BY created_at DESC LIMIT 50""",(g.android_customer["id"],)).fetchall()
    return jsonify({"ok":True,"orders":rows})

@android_api_bp.get("/orders/<int:oid>")
@required
def order_detail(oid):
    with DB() as conn:
        order=conn.execute("""SELECT id,total_amount,payment_amount,status,fulfillment_status,tracking_code,shipping_address,created_at
            FROM store_orders WHERE id=%s AND customer_id=%s""",(oid,g.android_customer["id"])).fetchone()
        if not order: return error("سفارش پیدا نشد.",404)
        items=conn.execute("SELECT product_id,product_name,quantity,unit_price,line_total FROM store_order_items WHERE order_id=%s ORDER BY id",(oid,)).fetchall()
    return jsonify({"ok":True,"order":order,"items":items})

@android_api_bp.post("/orders")
@required
def create_order():
    d=request.get_json(silent=True) or {}; address=str(d.get("shipping_address","")).strip(); items=d.get("items")
    if not 15<=len(address)<=1000: return error("آدرس باید بین ۱۵ تا ۱۰۰۰ نویسه باشد.")
    if not isinstance(items,list) or not items or len(items)>50: return error("سبد خرید خالی یا نامعتبر است.")
    quantities={}
    for item in items:
        if not isinstance(item,dict): return error("اقلام سبد معتبر نیستند.")
        pid,qty=item.get("product_id"),item.get("quantity")
        if type(pid) is not int or type(qty) is not int or pid<=0 or not 1<=qty<=99: return error("تعداد یا شناسه کالا معتبر نیست.")
        quantities[pid]=quantities.get(pid,0)+qty
    if any(q>99 for q in quantities.values()): return error("تعداد یک کالا بیش از حد مجاز است.")
    try:
        with DB() as conn:
            locked=[]; total=0
            for pid in sorted(quantities):
                p=conn.execute("SELECT id,name,price,stock FROM products WHERE id=%s AND is_active=TRUE FOR UPDATE",(pid,)).fetchone()
                qty=quantities[pid]
                if not p: return error("یکی از محصولات در دسترس نیست.",409)
                if int(p["stock"])<qty: return error("موجودی یکی از کالاها کافی نیست.",409)
                locked.append((p,qty)); total+=int(p["price"])*qty
            if total<=0: return error("مبلغ سفارش معتبر نیست.")
            order=conn.execute("""INSERT INTO store_orders(customer_id,total_amount,subtotal_amount,gift_card_discount,
                payment_amount,status,shipping_address,payment_provider)
                VALUES(%s,%s,%s,0,%s,'pending_confirmation',%s,'manual')
                RETURNING id,total_amount,status,fulfillment_status,created_at""",
                (g.android_customer["id"],total,total,total,address)).fetchone()
            for p,qty in locked:
                conn.execute("""INSERT INTO store_order_items(order_id,product_id,product_name,quantity,unit_price,line_total)
                    VALUES(%s,%s,%s,%s,%s,%s)""",(order["id"],p["id"],p["name"],qty,p["price"],int(p["price"])*qty))
        return jsonify({"ok":True,"order":order,"message":"سفارش برای تأیید فروشگاه ثبت شد."}),201
    except Exception: return error("ثبت سفارش انجام نشد؛ دوباره تلاش کنید.",500)
