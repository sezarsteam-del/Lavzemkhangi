"""Secure admin API for a ChatGPT Custom GPT Action.

Configure CHATGPT_API_TOKEN in the hosting environment with a random value
of at least 40 characters. Never commit the token to source control.
"""
import hmac
import os
from functools import wraps
from flask import Blueprint, request, jsonify

chatgpt_bp = Blueprint("chatgpt_manager", __name__, url_prefix="/api/chatgpt/v1")
DB = None

def init_chatgpt_api(db_func):
    global DB
    DB = db_func

def api_error(message, status=400):
    return jsonify({"ok": False, "error": message}), status

def manager_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        expected = os.environ.get("CHATGPT_API_TOKEN", "").strip()
        supplied = request.headers.get("Authorization", "")
        if len(expected) < 40:
            return api_error("CHATGPT_API_TOKEN is not configured on the server.", 503)
        if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:].strip(), expected):
            return api_error("Unauthorized.", 401)
        return fn(*args, **kwargs)
    return wrapped

def confirmed(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if request.headers.get("X-Confirm-Action", "").lower() != "yes":
            return api_error("برای اجرای تغییر، هدر X-Confirm-Action: yes الزامی است.", 409)
        return fn(*args, **kwargs)
    return wrapped

def body():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}

@chatgpt_bp.get("/health")
def health():
    configured = len(os.environ.get("CHATGPT_API_TOKEN", "").strip()) >= 40
    return jsonify({"ok": True, "service": "Lavzemkhangi ChatGPT Manager", "configured": configured})

@chatgpt_bp.get("/openapi.json")
def openapi():
    """OpenAPI 3.1 schema suitable for importing as a GPT Action."""
    confirm = {"$ref": "#/components/parameters/ConfirmAction"}
    def path_id(name):
        return {"name": name, "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}
    def json_body(schema):
        return {"required": True, "content": {"application/json": {"schema": schema}}}
    responses = {
        "200": {"description": "عملیات موفق"},
        "400": {"description": "ورودی نامعتبر"},
        "401": {"description": "توکن نامعتبر"},
        "409": {"description": "تعارض یا نیاز به تأیید"},
        "503": {"description": "توکن روی سرور تنظیم نشده"}
    }
    name_schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 120}}}
    return jsonify({
        "openapi": "3.1.0",
        "info": {"title": "Lavzemkhangi Store Manager", "version": "1.1.0", "description": "مدیریت فروشگاه. تمام عملیات تغییردهنده نیازمند X-Confirm-Action: yes هستند."},
        "servers": [{"url": request.url_root.rstrip("/") + "/api/chatgpt/v1"}],
        "components": {
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
            "parameters": {"ConfirmAction": {"name": "X-Confirm-Action", "in": "header", "required": True, "schema": {"type": "string", "enum": ["yes"]}}},
            "schemas": {
                "ProductInput": {"type": "object", "required": ["name", "price"], "properties": {
                    "name": {"type": "string", "maxLength": 200}, "description": {"type": "string", "maxLength": 5000},
                    "price": {"type": "integer", "minimum": 0}, "image_url": {"type": ["string", "null"], "maxLength": 2000},
                    "category_id": {"type": ["integer", "null"], "minimum": 1}, "brand_id": {"type": ["integer", "null"], "minimum": 1},
                    "stock": {"type": "integer", "minimum": 0}, "min_stock": {"type": "integer", "minimum": 0}}},
                "ProductUpdate": {"type": "object", "minProperties": 1, "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200}, "description": {"type": "string", "maxLength": 5000},
                    "price": {"type": "integer", "minimum": 0}, "image_url": {"type": ["string", "null"], "maxLength": 2000},
                    "category_id": {"type": ["integer", "null"], "minimum": 1}, "brand_id": {"type": ["integer", "null"], "minimum": 1},
                    "min_stock": {"type": "integer", "minimum": 0}}},
                "InventoryInput": {"type": "object", "required": ["product_id", "quantity", "movement_type"], "properties": {
                    "product_id": {"type": "integer", "minimum": 1}, "quantity": {"type": "integer", "minimum": 1},
                    "movement_type": {"type": "string", "enum": ["in", "out", "adjustment"]}, "note": {"type": "string", "maxLength": 500}}},
                "OrderUpdate": {"type": "object", "minProperties": 1, "properties": {
                    "fulfillment_status": {"type": "string", "enum": ["awaiting_confirmation", "preparing", "shipped", "delivered", "cancelled"]},
                    "tracking_code": {"type": "string", "maxLength": 100}}}
            }
        },
        "security": [{"BearerAuth": []}],
        "paths": {
            "/health": {"get": {"security": [], "operationId": "apiHealth", "summary": "بررسی سلامت API", "responses": {"200": {"description": "وضعیت سرویس"}}}},
            "/dashboard": {"get": {"operationId": "getDashboard", "summary": "آمار کلی فروشگاه", "responses": responses}},
            "/products": {
                "get": {"operationId": "listProducts", "summary": "جستجوی محصولات", "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}, {"name": "include_inactive", "in": "query", "schema": {"type": "boolean"}}], "responses": responses},
                "post": {"operationId": "createProduct", "summary": "ایجاد محصول", "parameters": [confirm], "requestBody": json_body({"$ref": "#/components/schemas/ProductInput"}), "responses": responses}},
            "/products/{product_id}": {
                "patch": {"operationId": "updateProduct", "summary": "ویرایش محصول", "parameters": [path_id("product_id"), confirm], "requestBody": json_body({"$ref": "#/components/schemas/ProductUpdate"}), "responses": responses},
                "delete": {"operationId": "archiveProduct", "summary": "غیرفعال‌سازی محصول", "parameters": [path_id("product_id"), confirm], "responses": responses}},
            "/categories": {
                "get": {"operationId": "listCategories", "summary": "فهرست دسته‌بندی‌ها", "responses": responses},
                "post": {"operationId": "createCategory", "summary": "ایجاد دسته‌بندی", "parameters": [confirm], "requestBody": json_body(name_schema), "responses": responses}},
            "/categories/{category_id}": {"patch": {"operationId": "updateCategory", "summary": "ویرایش دسته‌بندی", "parameters": [path_id("category_id"), confirm], "requestBody": json_body(name_schema), "responses": responses}},
            "/brands": {
                "get": {"operationId": "listBrands", "summary": "فهرست برندها", "responses": responses},
                "post": {"operationId": "createBrand", "summary": "ایجاد برند", "parameters": [confirm], "requestBody": json_body(name_schema), "responses": responses}},
            "/brands/{brand_id}": {"patch": {"operationId": "updateBrand", "summary": "ویرایش برند", "parameters": [path_id("brand_id"), confirm], "requestBody": json_body(name_schema), "responses": responses}},
            "/inventory": {
                "get": {"operationId": "getInventory", "summary": "موجودی و گردش انبار", "responses": responses},
                "post": {"operationId": "adjustInventory", "summary": "ثبت ورود یا خروج یا اصلاح موجودی", "parameters": [confirm], "requestBody": json_body({"$ref": "#/components/schemas/InventoryInput"}), "responses": responses}},
            "/orders": {"get": {"operationId": "listOrders", "summary": "فهرست سفارش‌ها", "parameters": [{"name": "status", "in": "query", "schema": {"type": "string"}}], "responses": responses}},
            "/orders/{order_id}": {
                "get": {"operationId": "getOrder", "summary": "جزئیات سفارش", "parameters": [path_id("order_id")], "responses": responses},
                "patch": {"operationId": "updateOrder", "summary": "تغییر وضعیت ارسال و کد رهگیری", "parameters": [path_id("order_id"), confirm], "requestBody": json_body({"$ref": "#/components/schemas/OrderUpdate"}), "responses": responses}},
            "/customers": {"get": {"operationId": "listCustomers", "summary": "جستجوی مشتریان", "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}], "responses": responses}},
            "/reports/sales": {"get": {"operationId": "getSalesReport", "summary": "گزارش فروش", "parameters": [{"name": "days", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 365}}], "responses": responses}}
        }
    })

@chatgpt_bp.get("/dashboard")
@manager_required
def dashboard():
    with DB() as conn:
        products = conn.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE stock=0) AS out_of_stock, COUNT(*) FILTER (WHERE stock>0 AND stock<=min_stock) AS low_stock, COALESCE(SUM(stock),0) AS stock_units FROM products WHERE is_active=TRUE").fetchone()
        sales = conn.execute("SELECT COUNT(*) AS invoices, COALESCE(SUM(final_amount),0) AS amount FROM sales WHERE status='completed' AND (created_at AT TIME ZONE 'Asia/Tehran')::date=(NOW() AT TIME ZONE 'Asia/Tehran')::date").fetchone()
        orders = conn.execute("SELECT COUNT(*) AS pending FROM store_orders WHERE status IN ('pending_confirmation','pending_payment')").fetchone()
    return jsonify({"ok": True, "products": products, "today_sales": sales, "pending_orders": orders})

@chatgpt_bp.get("/products")
@manager_required
def list_products():
    q = (request.args.get("q") or "").strip()[:100]
    include_inactive = request.args.get("include_inactive", "false").lower() == "true"
    sql = """SELECT p.id,p.name,p.description,p.price,p.image_url,p.category_id,p.brand_id,p.stock,p.min_stock,p.is_active,c.name AS category_name,b.name AS brand_name
             FROM products p LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN brands b ON b.id=p.brand_id"""
    params = []
    if not include_inactive:
        sql += " WHERE p.is_active=TRUE"
    if q:
        sql += (" AND" if " WHERE " in sql else " WHERE ") + " (p.name ILIKE %s OR p.description ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY p.created_at DESC LIMIT 100"
    with DB() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify({"ok": True, "products": rows})

@chatgpt_bp.post("/products")
@manager_required
@confirmed
def create_product():
    d = body()
    name = str(d.get("name", "")).strip()[:200]
    try:
        price = int(d.get("price"))
        stock = int(d.get("stock", 0))
        min_stock = int(d.get("min_stock", 2))
        category_id = int(d["category_id"]) if d.get("category_id") is not None else None
        brand_id = int(d["brand_id"]) if d.get("brand_id") is not None else None
        if not name or price < 0 or stock < 0 or min_stock < 0:
            raise ValueError()
    except (TypeError, ValueError, KeyError):
        return api_error("نام، قیمت و مقادیر محصول معتبر نیستند.")
    with DB() as conn:
        row = conn.execute("""INSERT INTO products(name,description,price,image_url,category_id,brand_id,stock,min_stock)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,name,price,stock""",
            (name, str(d.get("description", ""))[:5000], price, d.get("image_url"), category_id, brand_id, stock, min_stock)).fetchone()
    return jsonify({"ok": True, "product": row}), 201

@chatgpt_bp.patch("/products/<int:product_id>")
@manager_required
@confirmed
def update_product(product_id):
    d = body()
    allowed = {"name", "description", "price", "image_url", "category_id", "brand_id", "min_stock"}
    changes = {k: d[k] for k in allowed if k in d}
    if not changes:
        return api_error("هیچ فیلد معتبری برای تغییر ارسال نشده است.")
    if "name" in changes and not str(changes["name"]).strip():
        return api_error("نام محصول نمی‌تواند خالی باشد.")
    for key in ("price", "min_stock", "category_id", "brand_id"):
        if key in changes and changes[key] is not None:
            try:
                changes[key] = int(changes[key])
                if changes[key] < 0 or (key == "price" and changes[key] < 0):
                    return api_error("مقدار عددی نامعتبر است.")
            except (TypeError, ValueError):
                return api_error("مقدار عددی نامعتبر است.")
    columns = {"name": "name", "description": "description", "price": "price", "image_url": "image_url", "category_id": "category_id", "brand_id": "brand_id", "min_stock": "min_stock"}
    sets = [f"{columns[k]}=%s" for k in changes]
    values = [changes[k] for k in changes]
    with DB() as conn:
        row = conn.execute(f"UPDATE products SET {','.join(sets)} WHERE id=%s AND is_active=TRUE RETURNING id,name,price,stock", values + [product_id]).fetchone()
    if not row:
        return api_error("محصول فعال پیدا نشد.", 404)
    return jsonify({"ok": True, "product": row})

@chatgpt_bp.delete("/products/<int:product_id>")
@manager_required
@confirmed
def archive_product(product_id):
    with DB() as conn:
        row = conn.execute("UPDATE products SET is_active=FALSE WHERE id=%s AND is_active=TRUE RETURNING id,name", (product_id,)).fetchone()
    if not row:
        return api_error("محصول پیدا نشد.", 404)
    return jsonify({"ok": True, "archived": row})

@chatgpt_bp.get("/categories")
@manager_required
def list_categories():
    with DB() as conn:
        rows = conn.execute("SELECT c.id,c.name,COUNT(p.id) AS product_count FROM categories c LEFT JOIN products p ON p.category_id=c.id AND p.is_active=TRUE GROUP BY c.id ORDER BY c.name").fetchall()
    return jsonify({"ok": True, "categories": rows})

@chatgpt_bp.post("/categories")
@manager_required
@confirmed
def create_category():
    name = str(body().get("name", "")).strip()[:120]
    if not name:
        return api_error("نام دسته‌بندی الزامی است.")
    try:
        with DB() as conn:
            row = conn.execute("INSERT INTO categories(name) VALUES(%s) RETURNING id,name", (name,)).fetchone()
        return jsonify({"ok": True, "category": row}), 201
    except Exception:
        return api_error("دسته‌بندی ثبت نشد؛ احتمالاً نام تکراری است.", 409)

@chatgpt_bp.patch("/categories/<int:category_id>")
@manager_required
@confirmed
def update_category(category_id):
    name = str(body().get("name", "")).strip()[:120]
    if not name:
        return api_error("نام دسته‌بندی الزامی است.")
    try:
        with DB() as conn:
            row = conn.execute("UPDATE categories SET name=%s WHERE id=%s RETURNING id,name", (name, category_id)).fetchone()
        if not row:
            return api_error("دسته‌بندی پیدا نشد.", 404)
        return jsonify({"ok": True, "category": row})
    except Exception:
        return api_error("ویرایش انجام نشد؛ احتمالاً نام تکراری است.", 409)

@chatgpt_bp.get("/brands")
@manager_required
def list_brands():
    with DB() as conn:
        rows = conn.execute("SELECT b.id,b.name,COUNT(p.id) AS product_count FROM brands b LEFT JOIN products p ON p.brand_id=b.id AND p.is_active=TRUE GROUP BY b.id ORDER BY b.name").fetchall()
    return jsonify({"ok": True, "brands": rows})

@chatgpt_bp.post("/brands")
@manager_required
@confirmed
def create_brand():
    name = str(body().get("name", "")).strip()[:120]
    if not name:
        return api_error("نام برند الزامی است.")
    try:
        with DB() as conn:
            row = conn.execute("INSERT INTO brands(name) VALUES(%s) RETURNING id,name", (name,)).fetchone()
        return jsonify({"ok": True, "brand": row}), 201
    except Exception:
        return api_error("برند ثبت نشد؛ احتمالاً نام تکراری است.", 409)

@chatgpt_bp.patch("/brands/<int:brand_id>")
@manager_required
@confirmed
def update_brand(brand_id):
    name = str(body().get("name", "")).strip()[:120]
    if not name:
        return api_error("نام برند الزامی است.")
    try:
        with DB() as conn:
            row = conn.execute("UPDATE brands SET name=%s WHERE id=%s RETURNING id,name", (name, brand_id)).fetchone()
        if not row:
            return api_error("برند پیدا نشد.", 404)
        return jsonify({"ok": True, "brand": row})
    except Exception:
        return api_error("ویرایش برند انجام نشد؛ احتمالاً نام تکراری است.", 409)

@chatgpt_bp.get("/inventory")
@manager_required
def get_inventory():
    with DB() as conn:
        rows = conn.execute("SELECT id,name,stock,min_stock,CASE WHEN stock=0 THEN 'out_of_stock' WHEN stock<=min_stock THEN 'low_stock' ELSE 'ok' END AS stock_status FROM products WHERE is_active=TRUE ORDER BY stock ASC,name LIMIT 200").fetchall()
        movements = conn.execute("SELECT m.id,m.product_id,p.name AS product_name,m.movement_type,m.quantity,m.note,m.created_at FROM stock_movements m JOIN products p ON p.id=m.product_id ORDER BY m.created_at DESC LIMIT 30").fetchall()
    return jsonify({"ok": True, "products": rows, "recent_movements": movements})

@chatgpt_bp.post("/inventory")
@manager_required
@confirmed
def adjust_inventory():
    d = body()
    try:
        pid, qty = int(d.get("product_id")), int(d.get("quantity"))
        movement = str(d.get("movement_type", ""))
        if pid <= 0 or qty <= 0 or movement not in ("in", "out", "adjustment"):
            raise ValueError()
    except (TypeError, ValueError):
        return api_error("شناسه، تعداد یا نوع گردش انبار معتبر نیست.")
    with DB() as conn:
        product = conn.execute("SELECT id,stock FROM products WHERE id=%s AND is_active=TRUE FOR UPDATE", (pid,)).fetchone()
        if not product:
            return api_error("محصول پیدا نشد.", 404)
        old = int(product["stock"])
        if movement == "in":
            new = old + qty
            movement_qty = qty
        elif movement == "out":
            if qty > old:
                return api_error("موجودی برای خروج کافی نیست.", 409)
            new = old - qty
            movement_qty = qty
        else:
            new = qty
            movement_qty = new - old
        conn.execute("UPDATE products SET stock=%s WHERE id=%s", (new, pid))
        conn.execute("INSERT INTO stock_movements(product_id,movement_type,quantity,note) VALUES(%s,%s,%s,%s)", (pid, movement, movement_qty, str(d.get("note", ""))[:500]))
    return jsonify({"ok": True, "product_id": pid, "old_stock": old, "new_stock": new})

@chatgpt_bp.get("/orders")
@manager_required
def list_orders():
    status = (request.args.get("status") or "").strip()[:40]
    sql = """SELECT o.id,o.customer_id,c.first_name,c.last_name,c.phone,o.total_amount,o.payment_amount,o.status,o.fulfillment_status,o.tracking_code,o.shipping_address,o.created_at
             FROM store_orders o JOIN customers c ON c.id=o.customer_id"""
    params = []
    if status:
        sql += " WHERE o.status=%s OR o.fulfillment_status=%s"
        params += [status, status]
    sql += " ORDER BY o.created_at DESC LIMIT 100"
    with DB() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify({"ok": True, "orders": rows})

@chatgpt_bp.get("/orders/<int:order_id>")
@manager_required
def get_order(order_id):
    with DB() as conn:
        order = conn.execute("""SELECT o.id,o.customer_id,o.total_amount,o.subtotal_amount,
            o.gift_card_discount,o.payment_amount,o.status,o.payment_provider,o.paid_at,
            o.fulfillment_status,o.tracking_code,o.shipped_at,o.delivered_at,o.cancelled_at,
            o.created_at,c.first_name,c.last_name,c.phone,o.shipping_address
            FROM store_orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=%s""",
            (order_id,)).fetchone()
        if not order:
            return api_error("سفارش پیدا نشد.", 404)
        items = conn.execute("SELECT product_id,product_name,quantity,unit_price,line_total FROM store_order_items WHERE order_id=%s ORDER BY id", (order_id,)).fetchall()
    return jsonify({"ok": True, "order": order, "items": items})

@chatgpt_bp.patch("/orders/<int:order_id>")
@manager_required
@confirmed
def update_order(order_id):
    d = body()
    status = d.get("fulfillment_status")
    tracking = d.get("tracking_code")
    allowed = {"awaiting_confirmation", "preparing", "shipped", "delivered", "cancelled"}
    if status is None and tracking is None:
        return api_error("وضعیت ارسال یا کد رهگیری را مشخص کنید.")
    if status is not None and status not in allowed:
        return api_error("وضعیت ارسال معتبر نیست.")
    sets, values = [], []
    if status is not None:
        sets.append("fulfillment_status=%s")
        values.append(status)
        if status == "shipped":
            sets.append("shipped_at=COALESCE(shipped_at,NOW())")
        if status == "delivered":
            sets.append("delivered_at=COALESCE(delivered_at,NOW())")
    if tracking is not None:
        sets.append("tracking_code=%s")
        values.append(str(tracking).strip()[:100])
    with DB() as conn:
        row = conn.execute(f"UPDATE store_orders SET {','.join(sets)} WHERE id=%s RETURNING id,fulfillment_status,tracking_code", values + [order_id]).fetchone()
    if not row:
        return api_error("سفارش پیدا نشد.", 404)
    return jsonify({"ok": True, "order": row})

@chatgpt_bp.get("/customers")
@manager_required
def list_customers():
    q = (request.args.get("q") or "").strip()[:100]
    with DB() as conn:
        if q:
            term = f"%{q}%"
            rows = conn.execute("SELECT id,first_name,last_name,phone,loyalty_points,created_at FROM customers WHERE is_active=TRUE AND (phone ILIKE %s OR first_name ILIKE %s OR last_name ILIKE %s) ORDER BY created_at DESC LIMIT 50", (term, term, term)).fetchall()
        else:
            rows = conn.execute("SELECT id,first_name,last_name,phone,loyalty_points,created_at FROM customers WHERE is_active=TRUE ORDER BY created_at DESC LIMIT 50").fetchall()
    return jsonify({"ok": True, "customers": rows})

@chatgpt_bp.get("/reports/sales")
@manager_required
def sales_report():
    try:
        days = max(1, min(int(request.args.get("days", "30")), 365))
    except ValueError:
        days = 30
    with DB() as conn:
        totals = conn.execute("""SELECT COUNT(*) AS invoice_count,COALESCE(SUM(final_amount),0) AS total_amount,
            COALESCE(AVG(final_amount),0) AS average_invoice FROM sales
            WHERE status='completed' AND created_at>=NOW()-(%s * INTERVAL '1 day')""", (days,)).fetchone()
        daily = conn.execute("""SELECT (created_at AT TIME ZONE 'Asia/Tehran')::date AS day,
            COUNT(*) AS invoice_count,COALESCE(SUM(final_amount),0) AS total_amount
            FROM sales WHERE status='completed' AND created_at>=NOW()-(%s * INTERVAL '1 day')
            GROUP BY day ORDER BY day""", (days,)).fetchall()
    return jsonify({"ok": True, "days": days, "summary": totals, "daily": daily})

