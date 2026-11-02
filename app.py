import os
import hmac
import secrets
import threading
import re

from datetime import datetime, date
from zoneinfo import ZoneInfo
from functools import wraps

import jdatetime
import psycopg

from psycopg.rows import dict_row
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)

# ==================================================
# تنظیمات اصلی
# ==================================================

app = Flask(__name__)

app.config["SECRET_KEY"] = (
    os.environ.get("SECRET_KEY") or secrets.token_hex(32)
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "true").lower() == "true"
)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

TEHRAN = ZoneInfo("Asia/Tehran")

if not DATABASE_URL:
    raise RuntimeError("متغیر DATABASE_URL تنظیم نشده است.")


# ==================================================
# اعداد فارسی و انگلیسی
# ==================================================

PERSIAN_DIGITS = str.maketrans(
    "0123456789",
    "۰۱۲۳۴۵۶۷۸۹"
)

ENGLISH_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789"
)


def normalize_digits(value):
    if value is None:
        return ""

    return str(value).translate(ENGLISH_DIGITS)


def persian_numbers(value):
    if value is None:
        return ""

    return str(value).translate(PERSIAN_DIGITS)


def tehran_now():
    return datetime.now(TEHRAN)


# ==================================================
# تاریخ شمسی و ساعت تهران
# ==================================================

def jalali_date(value, fmt=None):

    if value is None:
        return ""

    if isinstance(value, datetime):

        if value.tzinfo is None:
            value = value.replace(tzinfo=TEHRAN)

        value = value.astimezone(TEHRAN)
        value = value.replace(tzinfo=None)

        result = jdatetime.datetime.fromgregorian(datetime=value)
