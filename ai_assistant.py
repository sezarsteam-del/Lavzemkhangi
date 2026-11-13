"""Public AI shopping assistant for Lavzemkhangi.

Requires OPENAI_API_KEY in the hosting environment. The key is never sent to
the browser. Product answers are grounded in the live product catalogue.
"""
import json
import os
import re
import time
import threading
import urllib.request
import urllib.error
from collections import defaultdict, deque
from flask import Blueprint, request, jsonify

ai_assistant_bp = Blueprint("ai_assistant", __name__, url_prefix="/api/assistant")
DB = None
_rate_lock = threading.Lock()
_rate_events = defaultdict(deque)
WINDOW_SECONDS = 300
MAX_REQUESTS = 12


def init_ai_assistant(db_func):
    global DB
    DB = db_func


def _limited(ip):
    now = time.time()
    with _rate_lock:
        events = _rate_events[ip]
        while events and now - events[0] > WINDOW_SECONDS:
            events.popleft()
        if len(events) >= MAX_REQUESTS:
            return True
        events.append(now)
    return False


def _product_context(question):
    words = re.findall(r"[\w\u0600-\u06FF]{3,}", question or "")[:6]
    with DB() as conn:
        if words:
            clauses = []
            params = []
            for word in words:
                clauses.append("(p.name ILIKE %s OR p.description ILIKE %s OR c.name ILIKE %s OR b.name ILIKE %s)")
                term = "%" + word + "%"
                params.extend([term, term, term, term])
            sql = """
                SELECT p.name,p.description,p.price,c.name AS category,b.name AS brand
                FROM products p
                LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN brands b ON b.id=p.brand_id
                WHERE p.is_active=TRUE AND (""" + " OR ".join(clauses) + """)
                ORDER BY p.created_at DESC LIMIT 8
            """
            rows = conn.execute(sql, params).fetchall()
        else:
            rows = []
    result = []
    for row in rows:
        item = {
            "name": row["name"],
            "price_toman": int(row["price"]),
            "description": (row.get("description") or "")[:700],
            "category": row.get("category"),
            "brand": row.get("brand"),
        }
        result.append(item)
    return result


@ai_assistant_bp.get("/health")
def health():
    return jsonify({
        "ok": True,
        "assistant": "Lavzemkhangi AI",
        "configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    })


@ai_assistant_bp.post("/chat")
def chat():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return jsonify({
            "ok": False,
            "error": "دستیار هوشمند هنوز روی سرور فعال نشده است. لطفاً بعداً دوباره امتحان کنید."
        }), 503

    if _limited(request.remote_addr or "unknown"):
        return jsonify({"ok": False, "error": "تعداد پیام‌ها زیاد است؛ چند دقیقه دیگر دوباره تلاش کنید."}), 429

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "درخواست نامعتبر است."}), 400

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 12:
        return jsonify({"ok": False, "error": "گفت‌وگو معتبر نیست."}), 400

    clean = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()[:2500]
        if content:
            clean.append({"role": item["role"], "content": content})
    if not clean or clean[-1]["role"] != "user":
        return jsonify({"ok": False, "error": "پیام کاربر دریافت نشد."}), 400

    question = clean[-1]["content"]
    try:
        products = _product_context(question)
    except Exception:
        return jsonify({"ok": False, "error": "در حال حاضر دریافت اطلاعات محصولات ممکن نیست."}), 503

    catalogue = json.dumps(products, ensure_ascii=False)
    system_prompt = (
        "تو دستیار هوشمند فروشگاه لوازم خانگی هستی. به زبان پیام کاربر پاسخ بده؛ "
        "پاسخ‌ها مودبانه، کوتاه و کاربردی باشند. فقط درباره محصولات، خرید، نگهداری "
        "و خدمات فروشگاه راهنمایی کن. اطلاعات محصولات زیر از پایگاه داده زنده فروشگاه "
        "آمده‌اند؛ قیمت را دقیقاً از همین داده‌ها اعلام کن و هیچ قیمت، موجودی، ضمانت، "
        "ویژگی یا زمان تحویلی را حدس نزن. این فهرست فقط نتایج مرتبط با پرسش است؛ اگر "
        "محصولی در آن نیست بگو برای تأیید موجودی و قیمت با پشتیبانی تماس بگیرند. "
        "موجودی انبار برای مشتری نمایش داده نشود. اطلاعات شخصی مشتریان یا سفارش‌های "
        "دیگران را افشا نکن و برای دریافت رمز، کد یکبارمصرف یا اطلاعات بانکی درخواست نکن. "
        "هیچ عملیاتی مثل ثبت سفارش، پرداخت یا تغییر قیمت را ادعا نکن؛ فقط راهنمایی کن. "
        "اگر پرسش خارج از موضوع فروشگاه است، محترمانه محدودیت را توضیح بده.\n"
        "محصولات مرتبط از فروشگاه (JSON): " + catalogue
    )
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()[:80]
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + clean[-10:],
        "temperature": 0.35,
        "max_tokens": 700,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.loads(response.read(1024 * 1024).decode("utf-8"))
        answer = result["choices"][0]["message"]["content"].strip()
        if not answer:
            raise ValueError("empty response")
        return jsonify({"ok": True, "reply": answer, "products_used": len(products)})
    except urllib.error.HTTPError as exc:
        # Never return provider response bodies or credentials to the browser.
        if exc.code == 429:
            return jsonify({"ok": False, "error": "سرویس هوش مصنوعی موقتاً شلوغ است؛ کمی بعد تلاش کنید."}), 503
        return jsonify({"ok": False, "error": "ارتباط با سرویس هوش مصنوعی برقرار نشد."}), 502
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError):
        return jsonify({"ok": False, "error": "پاسخ هوش مصنوعی دریافت نشد؛ دوباره تلاش کنید."}), 502
