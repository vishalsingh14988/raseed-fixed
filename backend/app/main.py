from google.auth.transport.requests import Request as GoogleAuthRequest

from google.oauth2 import id_token

import json

from types import SimpleNamespace

import os

import re

import sqlite3

import tempfile

import base64

import hashlib

import hmac

import time

import threading

from email.utils import parsedate_to_datetime

from datetime import date, datetime, timedelta

from pathlib import Path

from typing import Optional

from urllib.parse import urlencode

import cloudinary

import cloudinary.uploader

import httpx

from dotenv import load_dotenv

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import RedirectResponse, Response, JSONResponse

from starlette.middleware.sessions import SessionMiddleware

from google.oauth2.credentials import Credentials

from google.auth.exceptions import RefreshError

from googleapiclient.discovery import build

from googleapiclient.errors import HttpError

from pydantic import BaseModel

from PIL import Image, ImageOps, ImageEnhance, ImageFilter

try:

    import pytesseract

except ImportError:

    pytesseract = None

try:

    from pypdf import PdfReader

except ImportError:

    PdfReader = None

try:

    import fitz

except ImportError:

    fitz = None

# --------------------------------------------------

# CONFIGURATION

# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "billproof.db"

FRONTEND_ORIGIN = os.getenv(

    "FRONTEND_ORIGIN",

    "http://localhost:5173"

)

GOOGLE_SCOPES = [

    "openid",

    "email",

    "profile",

    "https://www.googleapis.com/auth/gmail.readonly",

]

# Sign-in only: identity scopes. Non-sensitive, so a published app lets

# anyone log in with their own Google account (no Google verification,

# no test-user list). Gmail access is requested separately, only when

# a user connects Gmail for bill scanning.

LOGIN_SCOPES = [

    "openid",

    "email",

    "profile",

]

app = FastAPI(title="Raseed API", version="0.3.0")

app.add_middleware(

    SessionMiddleware,

    secret_key=os.getenv(

        "SESSION_SECRET",

        "CHANGE_THIS_LOCAL_DEVELOPMENT_SECRET"

    ),

    # The frontend lives on a different Render subdomain, and

    # onrender.com is a public suffix - so the two services count as

    # DIFFERENT sites for cookies. A Lax session cookie would never

    # be sent by the frontend's API calls and login would appear to

    # do nothing (the site would stay in Guest mode forever).

    # SameSite=None + Secure makes the session work across them.

    same_site="none",

    https_only=True,

)

app.add_middleware(

    CORSMiddleware,

    allow_origins=[FRONTEND_ORIGIN],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)

# --------------------------------------------------

# DATABASE

# --------------------------------------------------

def db():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn

# --------------------------------------------------

# VAULT BACKUP — permanent storage for the bills database

#

# Render's free services have temporary local storage: every redeploy

# deletes the SQLite file, which used to wipe the whole vault. The bill

# files themselves already live permanently in Cloudinary, so the

# database file is backed up there too: after every change it is

# uploaded to Cloudinary, and when the server starts with no local

# database it downloads the backup back. If Cloudinary is not

# configured, the app simply behaves as before (local file only).

# --------------------------------------------------

VAULT_BACKUP_PUBLIC_ID = "billproof/db/vault-backup.db"

_vault_restore_attempted = False

vault_restored_from_backup = False

def _cloudinary_env():

    return (

        os.getenv("CLOUDINARY_CLOUD_NAME"),

        os.getenv("CLOUDINARY_API_KEY"),

        os.getenv("CLOUDINARY_API_SECRET"),

    )

def restore_vault_if_missing():

    """Download the vault backup from Cloudinary when starting empty."""

    global _vault_restore_attempted, vault_restored_from_backup

    if _vault_restore_attempted:

        return

    _vault_restore_attempted = True

    try:

        if DB_PATH.exists() and DB_PATH.stat().st_size > 0:

            return

        cloud_name, api_key, api_secret = _cloudinary_env()

        if not all([cloud_name, api_key, api_secret]):

            return

        # Ask Cloudinary's Admin API where the current backup file lives.

        meta = httpx.get(

            f"https://api.cloudinary.com/v1_1/{cloud_name}/resources/raw/upload/{VAULT_BACKUP_PUBLIC_ID}",

            auth=(api_key, api_secret),

            timeout=30,

        )

        if meta.status_code != 200:

            print("VAULT: no backup found yet (status", meta.status_code, ")")

            return

        secure_url = meta.json().get("secure_url")

        if not secure_url:

            return

        data = httpx.get(secure_url, timeout=60).content

        if not data.startswith(b"SQLite format 3\x00"):

            print("VAULT: downloaded backup is not a SQLite file; ignoring it")

            return

        DB_PATH.write_bytes(data)

        vault_restored_from_backup = True

        print("VAULT: restored", len(data), "bytes of bill data from Cloudinary backup")

    except Exception as exc:

        print("VAULT: restore skipped:", str(exc)[:160])

def backup_vault():

    """Upload the current database file to Cloudinary (best effort)."""

    try:

        cloud_name, api_key, api_secret = _cloudinary_env()

        if not all([cloud_name, api_key, api_secret]):

            return

        if not DB_PATH.exists():

            return

        cloudinary.config(

            cloud_name=cloud_name,

            api_key=api_key,

            api_secret=api_secret,

            secure=True,

        )

        cloudinary.uploader.upload(

            str(DB_PATH),

            resource_type="raw",

            public_id=VAULT_BACKUP_PUBLIC_ID,

            overwrite=True,

        )

        print("VAULT: backup saved to Cloudinary")

    except Exception as exc:

        print("VAULT: backup failed:", str(exc)[:160])

def init_db():

    restore_vault_if_missing()

    with db() as conn:

        conn.execute("""

            CREATE TABLE IF NOT EXISTS bills (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                product TEXT NOT NULL,

                seller TEXT DEFAULT '',

                purchase_date TEXT DEFAULT '',

                amount REAL,

                warranty_months INTEGER,

                warranty_end TEXT DEFAULT '',

                warranty_lifetime INTEGER DEFAULT 0,

                category TEXT DEFAULT 'Other',

                invoice_no TEXT DEFAULT '',

                order_no TEXT DEFAULT '',

                cloudinary_url TEXT DEFAULT '',

                public_id TEXT DEFAULT '',

                original_filename TEXT DEFAULT '',

                extracted_text TEXT DEFAULT '',

                created_at TEXT NOT NULL,

                owner_email TEXT NOT NULL DEFAULT 'local-demo',

                source_key TEXT DEFAULT '',

                logo_url TEXT DEFAULT '',

                company_domain TEXT DEFAULT '',

                company_about TEXT DEFAULT '',

                received_date TEXT DEFAULT ''

            )

        """)

        columns = {

            row[1]

            for row in conn.execute(

                "PRAGMA table_info(bills)"

            ).fetchall()

        }

        if "owner_email" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN owner_email TEXT NOT NULL DEFAULT 'local-demo'

            """)

        if "source_key" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN source_key TEXT DEFAULT ''

            """)

        if "invoice_no" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN invoice_no TEXT DEFAULT ''

            """)

        if "order_no" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN order_no TEXT DEFAULT ''

            """)

        if "warranty_lifetime" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN warranty_lifetime INTEGER DEFAULT 0

            """)

        if "logo_url" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN logo_url TEXT DEFAULT ''

            """)

        if "company_domain" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN company_domain TEXT DEFAULT ''

            """)

        if "company_about" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN company_about TEXT DEFAULT ''

            """)

        if "received_date" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN received_date TEXT DEFAULT ''

            """)

        if "needs_review" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN needs_review INTEGER

            """)

        if "review_reason" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN review_reason TEXT

            """)

        conn.execute("""

            CREATE TABLE IF NOT EXISTS gmail_connections (

                email TEXT PRIMARY KEY,

                access_token TEXT NOT NULL,

                refresh_token TEXT DEFAULT '',

                token_uri TEXT NOT NULL,

                scope TEXT DEFAULT '',

                updated_at TEXT NOT NULL

            )

        """)

        conn.execute("""

            CREATE TABLE IF NOT EXISTS learned_rules (

                owner_email TEXT NOT NULL,

                seller_key TEXT NOT NULL,

                category TEXT NOT NULL,

                updated_at TEXT DEFAULT '',

                PRIMARY KEY (owner_email, seller_key)

            )

        """)

        conn.execute("""

            CREATE TABLE IF NOT EXISTS users (

                email TEXT PRIMARY KEY,

                name TEXT DEFAULT '',

                purpose TEXT DEFAULT '',

                age INTEGER,

                created_at TEXT DEFAULT ''

            )

        """)

        # Prevent the same Gmail attachment from being imported twice.

        conn.execute("""

            CREATE UNIQUE INDEX IF NOT EXISTS

            idx_bills_owner_source_key

            ON bills(owner_email, source_key)

            WHERE source_key IS NOT NULL AND source_key != ''

        """)

init_db()

# --------------------------------------------------

# GENERAL HELPERS

# --------------------------------------------------

# Login tokens: the session cookie alone is not reliable on phones,
# because the frontend and backend are different sites and many mobile
# browsers block or drop cross-site cookies - the site then flips a
# signed-in user back to Guest. After Google sign-in the browser is
# handed a signed token, the website stores it and sends it back as an
# Authorization header, which no cookie setting can block.

def _token_secret() -> bytes:

    return os.getenv(

        "SESSION_SECRET",

        "CHANGE_THIS_LOCAL_DEVELOPMENT_SECRET"

    ).encode()


def issue_login_token(email: str, days: int = 30) -> str:

    payload = email + "|" + str(int(time.time()) + days * 86400)

    sig = hmac.new(_token_secret(), payload.encode(), hashlib.sha256).hexdigest()

    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    return body + "." + sig


def read_login_token(token: str):

    try:

        body, sig = token.split(".", 1)

        payload = base64.urlsafe_b64decode(

            body + "=" * (-len(body) % 4)

        ).decode()

        expected = hmac.new(

            _token_secret(), payload.encode(), hashlib.sha256

        ).hexdigest()

        if not hmac.compare_digest(sig, expected):

            return None

        email, exp = payload.rsplit("|", 1)

        if int(exp) < int(time.time()):

            return None

        return email

    except Exception:

        return None


def current_owner(request: Request) -> str:

    auth = request.headers.get("authorization") or ""

    if auth.lower().startswith("bearer "):

        email = read_login_token(auth[7:].strip())

        if email:

            return email

    return request.session.get("owner_email") or "local-demo"

def google_oauth_configured() -> bool:

    return bool(

        os.getenv("GOOGLE_CLIENT_ID")

        and os.getenv("GOOGLE_CLIENT_SECRET")

    )

def get_user_profile(email: str) -> dict:

    """Stored onboarding profile for a signed-in user."""

    with db() as conn:

        row = conn.execute(

            "SELECT email, name, purpose, age FROM users WHERE email=?",

            (email,),

        ).fetchone()

    if not row:

        return {"name": "", "purpose": "", "age": None}

    profile = dict(row)

    return {

        "name": profile.get("name") or "",

        "purpose": profile.get("purpose") or "",

        "age": profile.get("age"),

    }

def profile_complete(profile: dict) -> bool:

    return bool(

        profile.get("name") and profile.get("purpose") and profile.get("age")

    )

def guest_blocked(owner: str) -> bool:

    """Once Google sign-in is switched on, signed-out visitors (the

    local-demo owner) see an empty vault and cannot change bills.

    Before sign-in is configured the app stays open, as before."""

    return owner == "local-demo" and google_oauth_configured()

def configure_cloudinary():

    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")

    api_key = os.getenv("CLOUDINARY_API_KEY")

    api_secret = os.getenv("CLOUDINARY_API_SECRET")

    if not all([cloud_name, api_key, api_secret]):

        raise HTTPException(

            status_code=503,

            detail="Cloudinary is not configured in backend/.env."

        )

    cloudinary.config(

        cloud_name=cloud_name,

        api_key=api_key,

        api_secret=api_secret,

        secure=True,

    )

def get_gmail_credentials(email: str):

    with db() as conn:

        row = conn.execute(

            "SELECT * FROM gmail_connections WHERE email = ?",

            (email,)

        ).fetchone()

    if not row:

        raise HTTPException(

            status_code=401,

            detail="Connect your Gmail account first."

        )

    return Credentials(

        token=row["access_token"],

        refresh_token=row["refresh_token"] or None,

        token_uri=row["token_uri"],

        client_id=os.getenv("GOOGLE_CLIENT_ID"),

        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),

        scopes=GOOGLE_SCOPES,

    )



def ocr_status():
    """Report which OCR engines are usable in this deployment (no secrets)."""
    status = {"pytesseract_installed": bool(pytesseract), "tesseract_binary": False,
              "tesseract_version": "", "rapidocr_available": False}
    if pytesseract:
        try:
            status["tesseract_version"] = str(pytesseract.get_tesseract_version())
            status["tesseract_binary"] = True
        except Exception:
            status["tesseract_binary"] = False
    try:
        import rapidocr_onnxruntime  # noqa: F401
        status["rapidocr_available"] = True
    except Exception:
        status["rapidocr_available"] = False
    status["available"] = bool(status["tesseract_binary"] or status["rapidocr_available"])
    return status


def _run_rapidocr(path: str) -> str:
    """Fallback OCR that needs no system Tesseract binary (pip-only engine)."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        result, _elapsed = engine(path)
        if not result:
            return ""
        return "\n".join(str(item[1]) for item in result if len(item) > 1).strip()
    except Exception as exc:
        print(f"RAPIDOCR ERROR: {type(exc).__name__}: {exc}")
        return ""


def extract_text(path: str, content_type: str) -> str:
    """Extract selectable PDF text or OCR text from scanned PDFs and images.

    Important: selectable PDF text must be tried even when Tesseract is
    missing (e.g. a Render service that was not started from the Docker
    image). OCR is only needed for scanned PDFs and photos/images.
    """
    ocr_available = False
    if not pytesseract:
        print("OCR ERROR: pytesseract Python package is not installed.")
    else:
        try:
            pytesseract.get_tesseract_version()
            ocr_available = True
        except Exception as exc:
            print(f"OCR ENGINE ERROR: Tesseract is unavailable: {exc}")

    def run_ocr(source_image):
        if not ocr_available:
            return ""
        image = ImageOps.exif_transpose(source_image).convert("RGB")

        # Upscale small receipts and improve contrast before OCR.
        if image.width < 1600:
            scale = 1600 / image.width
            image = image.resize(
                (1600, max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )

        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        image = ImageEnhance.Contrast(image).enhance(1.5)
        image = image.filter(ImageFilter.SHARPEN)

        text = pytesseract.image_to_string(
            image,
            config="--oem 3 --psm 6",
            timeout=30,
        )
        if len(text.strip()) < 20:
            alternate = pytesseract.image_to_string(
                image,
                config="--oem 3 --psm 11",
                timeout=30,
            )
            if len(alternate.strip()) > len(text.strip()):
                text = alternate
        return text.strip()

    is_pdf = (
        content_type == "application/pdf"
        or path.lower().endswith(".pdf")
    )

    if is_pdf:
        if PdfReader:
            try:
                reader = PdfReader(path)
                selectable_text = "\n".join(
                    page.extract_text() or ""
                    for page in reader.pages[:5]
                ).strip()
                if len(selectable_text) >= 20:
                    print(
                        "PDF TEXT EXTRACTION:",
                        len(selectable_text),
                        "characters",
                    )
                    return selectable_text[:20000]
            except Exception as exc:
                print(f"PDF TEXT EXTRACTION ERROR: {exc}")

        if not fitz:
            print("PDF OCR ERROR: PyMuPDF (fitz) is not installed.")
            return ""

        try:
            pages = []
            with fitz.open(path) as document:
                for page in list(document)[:5]:
                    pix = page.get_pixmap(
                        dpi=250,
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )
                    page_image = Image.frombytes(
                        "RGB",
                        (pix.width, pix.height),
                        pix.samples,
                    )
                    pages.append(run_ocr(page_image))

            result = "\n".join(pages).strip()[:20000]
            print("SCANNED PDF OCR:", len(result), "characters")
            if not result:
                print("SCANNED PDF OCR WARNING: no text recognized.")
            return result
        except Exception as exc:
            print(f"SCANNED PDF OCR ERROR: {type(exc).__name__}: {exc}")
            return ""

    try:
        with Image.open(path) as source_image:
            result = run_ocr(source_image)[:20000]
        print("IMAGE OCR:", len(result), "characters")
        if not result:
            print("IMAGE OCR: trying RapidOCR fallback (no Tesseract result).")
            result = _run_rapidocr(path)[:20000]
            print("IMAGE RAPIDOCR:", len(result), "characters")
        if not result:
            print("IMAGE OCR WARNING: no text recognized.")
        return result
    except Exception as exc:
        print(f"IMAGE OCR ERROR: {type(exc).__name__}: {exc}")
        return _run_rapidocr(path)[:20000]

def parse_invoice_date(value: str) -> str:
    """Parse common invoice date formats into YYYY-MM-DD."""
    value = re.sub(r"\s+", " ", value.strip()).replace(",", "")
    # Ordinal days: "Oct 21st 2026", "1st Jan 2026" -> "Oct 21 2026".
    value = re.sub(r"(\d{1,2})(?:st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)
    formats = (
        "%d-%m-%Y", "%d-%m-%y", "%d/%m/%Y", "%d/%m/%y",
        "%Y-%m-%d", "%d %B %Y", "%d %b %Y",
        "%B %d %Y", "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _is_future_date(iso: str) -> bool:
    """True when an ISO date is implausibly ahead (> today + 1 day).

    A purchase cannot be dated in the future; future dates found on a
    bill are something else (a subscription renewal, the next billing
    date, a warranty expiry) and must never become its purchase_date.
    One day of grace covers timezone edges on same-day bills."""
    try:
        return date.fromisoformat(iso) > date.today() + timedelta(days=1)
    except (ValueError, TypeError):
        return False


_DATE_VALUE_PATTERN = (
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}|"
    r"[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|"
    r"\d{4}-\d{1,2}-\d{1,2}"
)

_DATE_PATTERNS = (
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\b",
    r"\b[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    r"\b\d{4}-\d{1,2}-\d{1,2}\b",
)

# Phrases that mark a nearby date as NOT the day of purchase: it is
# when a subscription renews, an offer/warranty lapses, or the next
# charge lands. Google Play renewal mail is the classic case — it
# prints "Renewal date: <next year>" prominently, and with no guard
# that date was imported as the bill's purchase date (13 Mar 2027).
_NON_PURCHASE_DATE_CONTEXT = (
    "renew", "expir", "valid till", "valid until", "valid upto",
    "next billing", "will be charged", "auto-renew", "autorenew",
    "auto renew",
)


def _date_has_non_purchase_context(text: str, start: int) -> bool:
    """Check the ~30 chars before a date for renewal/expiry wording."""
    context = text[max(0, start - 30):start].lower()
    return any(word in context for word in _NON_PURCHASE_DATE_CONTEXT)


def _select_purchase_date(text: str) -> str:
    """Choose a bill's purchase date from its text.

    Labelled dates ("Invoice Date:", "Order Date:", ...) win over bare
    ones, and every match is scanned rather than stopping at the first
    — but a candidate is never accepted when it lies in the future or
    when its label context marks it as a renewal/expiry/next-billing
    date. A strong label (invoice/purchase/order/bill/transaction) is
    trusted as-is; only the bare word "date" needs its context checked,
    since that is how "Renewal date:" used to sneak in."""
    labelled_re = re.compile(
        r"(invoice date|purchase date|order date|date of purchase"
        r"|bill date|transaction date|date)\s*[:\-]?\s*("
        + _DATE_VALUE_PATTERN + r")",
        re.IGNORECASE,
    )
    for match in labelled_re.finditer(text):
        iso = parse_invoice_date(match.group(2))
        if not iso or _is_future_date(iso):
            continue
        if match.group(1).lower() == "date" and \
                _date_has_non_purchase_context(text, match.start()):
            continue
        return iso
    for pattern in _DATE_PATTERNS:
        for match in re.finditer(pattern, text):
            iso = parse_invoice_date(match.group(0))
            if not iso or _is_future_date(iso):
                continue
            if _date_has_non_purchase_context(text, match.start()):
                continue
            return iso
    return ""


def parse_warranty_months(text: str) -> int:
    """Detect explicit warranty duration; avoid guessing if absent.

    Accepts the ways real bills actually write it: "1 Year Warranty",
    "6-month warranty", "Warranty: 12M", "1yr warranty", "6 mos".
    """
    patterns = (
        (r"\b(\d{1,2})\s*[- ]?\s*(?:years?|yrs?)\b.{0,50}\bwarranty\b", 12),
        (r"\bwarranty\b.{0,50}\b(\d{1,2})\s*[- ]?\s*(?:years?|yrs?)\b", 12),
        (r"\b(\d{1,2})\s*y\b.{0,30}\bwarranty\b", 12),
        (r"\bwarranty\b.{0,30}\b(\d{1,2})\s*y\b", 12),
        (r"\b(\d{1,3})\s*[- ]?\s*(?:months?|mos?)\b.{0,50}\bwarranty\b", 1),
        (r"\bwarranty\b.{0,50}\b(\d{1,3})\s*[- ]?\s*(?:months?|mos?)\b", 1),
        (r"\bwarranty\s*[:\-]?\s*(\d{1,3})\s*(?:months?|mos?)\b", 1),
        (r"\b(\d{1,3})\s*m\b.{0,30}\bwarranty\b", 1),
        (r"\bwarranty\b.{0,30}\b(\d{1,3})\s*m\b", 1),
    )
    for pattern, multiplier in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            months = int(match.group(1)) * multiplier
            if 1 <= months <= 120:
                return months
    return 0


def parse_warranty_end(text: str) -> str:
    """Detect an explicitly labelled warranty expiry/end date."""
    date_pattern = (
        r"(?:warranty\s*)?(?:end|expiry|expiration|expires|valid\s*(?:until|till|upto))"
        r"\s*(?:date|on)?\s*[:\-]?\s*"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
        r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}|"
        r"[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})"
    )
    match = re.search(date_pattern, text, re.IGNORECASE)
    return parse_invoice_date(match.group(1)) if match else ""


def parse_warranty_lifetime(text: str) -> int:
    """1 when the bill promises a lifetime warranty, else 0."""
    return 1 if re.search(
        r"life\s*[- ]?time\b.{0,30}\bwarranty\b|\bwarranty\b.{0,30}\blife\s*[- ]?time\b",
        text,
        re.IGNORECASE | re.DOTALL,
    ) else 0


KNOWN_SELLERS = (
    "amazon india", "amazon", "flipkart", "croma", "reliance digital",
    "vijay sales", "ikea", "myntra", "blinkit", "ajio", "nykaa",
    "samsung", "apple", "decathlon", "bigbasket", "zepto", "swiggy", "zomato",
    "rapido", "ola", "uber", "eatsure",
)


# Company identification for bill logos: seller name (lowercase key) ->
# (website domain, one short line on what the company does). The logo
# shown in the app is the site's favicon for that domain. Unknown
# sellers are deliberately NOT guessed — they keep empty fields and
# the frontend shows a default bill logo instead.
COMPANY_INFO = {
    "eatsure": ("eatsure.com", "Food delivery"),
    "rapido": ("rapido.bike", "Bike taxi and ride-hailing"),
    "croma": ("croma.com", "Electronics retail"),
    "samsung": ("samsung.com", "Electronics and smartphones"),
    "oneplus": ("oneplus.in", "Smartphones and electronics"),
    "one plus": ("oneplus.in", "Smartphones and electronics"),
    "ajio": ("ajio.com", "Fashion shopping"),
    "amazon india": ("amazon.in", "Online shopping"),
    "amazon": ("amazon.in", "Online shopping"),
    "flipkart": ("flipkart.com", "Online shopping"),
    "zomato": ("zomato.com", "Food delivery"),
    "swiggy": ("swiggy.com", "Food delivery"),
    "myntra": ("myntra.com", "Fashion shopping"),
    "bigbasket": ("bigbasket.com", "Online groceries"),
    "big basket": ("bigbasket.com", "Online groceries"),
    "blinkit": ("blinkit.com", "Quick grocery delivery"),
    "ola": ("olacabs.com", "Ride-hailing cabs and autos"),
    "uber": ("uber.com", "Ride-hailing"),
    "apple": ("apple.com", "Electronics and smartphones"),
    "xiaomi": ("mi.com", "Smartphones and electronics"),
    "mi": ("mi.com", "Smartphones and electronics"),
    "boat": ("boat-lifestyle.com", "Audio accessories"),
    "boAt": ("boat-lifestyle.com", "Audio accessories"),
    "reliance digital": ("reliancedigital.in", "Electronics retail"),
    "reliance": ("reliancedigital.in", "Electronics retail"),
    "tata": ("tata.com", "Conglomerate — retail, electronics and more"),
    "dmart": ("dmart.in", "Supermarket and groceries"),
    "d-mart": ("dmart.in", "Supermarket and groceries"),
    "avenue supermarts": ("dmart.in", "Supermarket and groceries"),
    "decathlon": ("decathlon.in", "Sports goods retail"),
    "makemytrip": ("makemytrip.com", "Travel bookings"),
    "make my trip": ("makemytrip.com", "Travel bookings"),
    "irctc": ("irctc.co.in", "Indian Railways train bookings"),
    "vijay sales": ("vijaysales.com", "Electronics retail"),
    "ikea": ("ikea.com", "Furniture and home goods"),
    "nykaa": ("nykaa.com", "Beauty and personal care shopping"),
    "zepto": ("zeptonow.com", "Quick grocery delivery"),
}


def _clean_domain(value) -> str:
    """Validate/normalise a website domain from the AI reader.

    Returns '' unless it looks like a real domain: lowercase, no
    scheme/www prefix left, contains a dot, no spaces or slashes."""
    if not isinstance(value, str):
        return ""
    domain = value.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain.split("/")[0].split("?")[0].strip().strip(".")
    if not domain or " " in domain or "." not in domain:
        return ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}", domain):
        return ""
    return domain


def _company_info_for_seller(seller) -> tuple:
    """COMPANY_INFO lookup tolerant like KNOWN_SELLERS handling:
    case-insensitive, longest keys first, substring/startswith match,
    word boundaries for very short keys (ola/uber/mi/boat)."""
    name = re.sub(r"\s+", " ", str(seller or "").strip().lower())
    if not name:
        return "", ""
    for key in sorted(COMPANY_INFO, key=len, reverse=True):
        lowered_key = key.lower()
        if len(lowered_key) <= 4:
            found = re.search(rf"\b{re.escape(lowered_key)}\b", name)
        else:
            found = lowered_key in name
        if found:
            return COMPANY_INFO[key]
    return "", ""


def enrich_company(seller, ai_fields=None) -> dict:
    """Company logo/domain/about for a bill.

    Prefers the AI reader's seller_domain/company_about when present,
    then the COMPANY_INFO match for the seller, else empty strings
    (never guess a domain for an unknown seller)."""
    ai_fields = ai_fields or {}
    domain = _clean_domain(ai_fields.get("seller_domain") or "")
    about = ""
    ai_about = ai_fields.get("company_about")
    if isinstance(ai_about, str) and ai_about.strip():
        about = ai_about.strip()[:140]
    if not domain or not about:
        known_domain, known_about = _company_info_for_seller(seller)
        domain = domain or known_domain
        about = about or known_about
    logo_url = (
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
        if domain else ""
    )
    return {
        "logo_url": logo_url,
        "company_domain": domain,
        "company_about": about,
    }


# --------------------------------------------------
# BILL CATEGORIES — automatic categorisation
# --------------------------------------------------
# Fixed vocabulary: must match the frontend CAT_COLORS keys in
# main.jsx exactly, because the spending donut groups bills by these
# values. Almost every bill used to be stored as "Other", which left
# the donut at 100% Other; infer_category() now decides the category
# at insert time, and row_to_dict() re-infers at read time for bills
# already stored as "Other" (no re-import needed).
BILL_CATEGORIES = (
    "Food", "Travel", "Electronics", "Fashion", "Home & Kitchen",
    "Appliances", "Furniture", "Personal care", "Education", "Health",
    "Entertainment", "Bills & Utilities", "Other",
)

# Sellers whose range is basically a single category. Amazon and
# Flipkart are deliberately absent: they sell everything, so their
# bills fall through to the keyword rules below.
SELLER_CATEGORY = {
    "eatsure": "Food",
    "zomato": "Food",
    "swiggy": "Food",
    "faasos": "Food",
    "bigbasket": "Food",
    "big basket": "Food",
    "blinkit": "Food",
    "zepto": "Food",
    "dmart": "Food",
    "d-mart": "Food",
    "avenue supermarts": "Food",
    "rapido": "Travel",
    "ola": "Travel",
    "uber": "Travel",
    "irctc": "Travel",
    "makemytrip": "Travel",
    "make my trip": "Travel",
    "samsung": "Electronics",
    "oneplus": "Electronics",
    "one plus": "Electronics",
    "apple": "Electronics",
    "xiaomi": "Electronics",
    "mi": "Electronics",
    "boat": "Electronics",
    "ajio": "Fashion",
    "myntra": "Fashion",
    "decathlon": "Fashion",
    "ikea": "Furniture",
    "nykaa": "Personal care",
    "pharmeasy": "Health",
    "netmeds": "Health",
    "1mg": "Health",
    "practo": "Health",
    "bookmyshow": "Entertainment",
    "netflix": "Entertainment",
    "spotify": "Entertainment",
    "google play": "Entertainment",
    "unacademy": "Education",
    "byjus": "Education",
    "vedantu": "Education",
}


def _seller_category_for(seller) -> str:
    """SELLER_CATEGORY lookup with the same matching discipline as
    _company_info_for_seller: case-insensitive, longest keys first,
    word boundaries for very short keys (so "Chocolate World" does
    not match "ola", and plain "Reliance" does not claim Electronics
    — only "Reliance Digital" does)."""
    name = re.sub(r"\s+", " ", str(seller or "").strip().lower())
    if not name:
        return ""
    for key in sorted(SELLER_CATEGORY, key=len, reverse=True):
        if len(key) <= 4:
            found = re.search(rf"\b{re.escape(key)}\b", name)
        else:
            found = key in name
        if found:
            return SELLER_CATEGORY[key]
    return ""


# Keyword rules, checked in order: Food first (a food order stays
# Food whatever shop sold it), Appliances before Electronics (a
# washing machine is not a gadget). Matching uses word boundaries
# throughout, so "pan" does not fire inside "company".
_CATEGORY_KEYWORDS = (
    ("Food", (
        "burger", "pizza", "biryani", "wrap", "roll", "meal", "food",
        "restaurant", "cafe", "coffee", "dosa", "thali", "cake",
        "gulab jamun", "snacks", "grocery", "groceries", "vegetables",
        "milk",
    )),
    ("Education", (
        "university", "college", "school", "hostel", "tuition",
        "institute", "academy", "semester", "examination fee",
        "course fee", "coaching",
    )),
    ("Health", (
        "hospital", "clinic", "pharmacy", "medical", "medicine",
        "medicines", "diagnostic", "diagnostics", "laboratory",
        "pathology", "dental", "surgery", "consultation",
    )),
    ("Bills & Utilities", (
        "electricity", "electric bill", "broadband", "internet bill",
        "recharge", "postpaid", "water bill", "gas bill", "dth",
        "utility bill", "mobile bill", "phone bill",
    )),
    ("Appliances", (
        "washing machine", "refrigerator", "fridge", "microwave",
        "air conditioner", "ac", "cooler", "geyser", "mixer grinder",
        "oven", "chimney",
    )),
    ("Electronics", (
        "phone", "smartphone", "mobile", "laptop", "tv", "television",
        "earbuds", "earphones", "headphones", "charger", "tablet",
        "smartwatch", "camera", "speaker", "keyboard", "mouse",
        "monitor",
    )),
    ("Fashion", (
        "shirt", "t-shirt", "shoes", "sneakers", "dress", "jeans",
        "kurta", "saree", "jacket", "handbag", "sunglasses",
    )),
    ("Furniture", (
        "sofa", "chair", "table", "bed", "mattress", "wardrobe",
        "bookshelf",
    )),
    ("Home & Kitchen", (
        "utensil", "cookware", "pressure cooker", "pan", "detergent",
        "cleaning", "kitchen", "dinner set", "bottle",
    )),
    ("Personal care", (
        "shampoo", "soap", "cream", "lipstick", "makeup", "skincare",
        "perfume", "toothpaste",
    )),
    ("Entertainment", (
        "google play", "netflix", "spotify", "prime video", "hotstar",
        "bookmyshow", "movie", "movies", "game", "games",
        "subscription", "app store",
    )),
    ("Travel", (
        "ride", "taxi", "cab", "flight", "air ticket", "train ticket",
        "hotel", "trip", "petrol", "diesel", "fuel", "toll",
    )),
)


def _keyword_category(haystack: str) -> str:
    """First keyword-rule category matching the normalised haystack."""
    for category, words in _CATEGORY_KEYWORDS:
        for word in words:
            needle = re.sub(r"[^a-z0-9]+", " ", word).strip()
            if needle and re.search(rf"\b{re.escape(needle)}\b", haystack):
                return category
    return ""


def infer_category(seller, product, text, ai_fields=None) -> str:
    """Best-guess category for a bill, from the fixed BILL_CATEGORIES.

    Priority: (1) the AI reader's own category when it names a valid
    one, (2) the seller map for single-category sellers,
    (3) keyword rules over product + seller + a capped slice of the
    bill text. Falls back to "Other"."""
    ai_fields = ai_fields or {}
    ai_category = ai_fields.get("category")
    if isinstance(ai_category, str) \
            and ai_category.strip() in BILL_CATEGORIES:
        return ai_category.strip()
    seller_category = _seller_category_for(seller)
    if seller_category:
        return seller_category
    haystack = " ".join([
        str(product or ""),
        str(seller or ""),
        str(text or "")[:4000],
    ]).lower()
    haystack = f" {re.sub(r'[^a-z0-9]+', ' ', haystack)} "
    return _keyword_category(haystack) or "Other"


def ai_classify_category(seller, product, text) -> str:
    """Ask the AI reader to categorise a bill the rules could not.

    This is the "Raseed AI decides by itself" path: it runs only when
    the deterministic rules land on "Other". It sends a short
    text-only prompt and accepts only an exact category from
    BILL_CATEGORIES. Best-effort: any failure (no API key, timeout,
    odd reply) returns "" and the bill simply stays "Other".
    """
    api_key = _gemini_api_key()
    if not api_key:
        return ""
    excerpt = " ".join([
        str(seller or ""), str(product or ""), str(text or "")[:1500],
    ]).strip()
    if len(excerpt) < 8:
        return ""
    base = os.getenv(
        "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    models = []
    for candidate in [os.getenv("GEMINI_MODEL", "")] + list(AI_READER_MODELS):
        if candidate and candidate not in models:
            models.append(candidate)
    prompt = (
        "You categorise Indian purchase bills. Reply with ONLY a JSON "
        "object {\"category\": \"...\"} choosing exactly one of: "
        + ", ".join(BILL_CATEGORIES) + ". "
        "Guidance: college/university/school fees and hostel charges "
        "are Education; medicines, hospitals and clinics are Health; "
        "apps, subscriptions and movies are Entertainment; electricity, "
        "internet, phone recharge and similar utility payments are "
        "Bills & Utilities. Choose Other only if nothing fits.\n\n"
        f"Bill details: {excerpt}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0, "responseMimeType": "application/json",
        },
    }
    for model in models[:2]:
        try:
            with httpx.Client(timeout=15.0, trust_env=False) as client:
                response = client.post(
                    f"{base}/models/{model}:generateContent",
                    headers={
                        "x-goog-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if response.status_code != 200:
                continue
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            raw = "".join(part.get("text", "") for part in parts)
            category = _parse_ai_json(raw).get("category")
            if isinstance(category, str) \
                    and category.strip() in BILL_CATEGORIES \
                    and category.strip() != "Other":
                return category.strip()
            # The model answered but had no better idea: do not burn
            # another model on the same question.
            return ""
        except Exception:
            continue
    return ""


def categorize_bill(seller, product, text, ai_fields=None, owner=None) -> str:
    """Category for a newly saved bill.

    Deterministic rules first (instant, free, predictable); when they
    can only say "Other", the AI reader takes a look at the bill and
    decides by itself. Used at save time only — read-time backfill in
    row_to_dict stays rule-based so listing bills never waits on AI.
    """
    if owner:
        learned = _learned_category(owner, seller)
        if learned:
            return learned
    category = infer_category(seller, product, text, ai_fields)
    if category != "Other":
        return category
    return ai_classify_category(seller, product, text) or "Other"


def _filename_product(filename: str) -> str:
    """Guess a product from a helpful filename, e.g.
    'Flipkart Samsung Earbuds Tax Invoice.png' -> 'Samsung Earbuds'.
    Returns '' for useless filenames (mostly numbers, etc.)."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or sum(ch.isdigit() for ch in cleaned) > len(cleaned) * 0.6:
        return ""
    lowered = cleaned.lower()
    for seller in sorted(KNOWN_SELLERS, key=len, reverse=True):
        if lowered.startswith(seller):
            cleaned = cleaned[len(seller):].strip(" -")
            lowered = cleaned.lower()
            break
    # 'Amazon India ...' leaves a leading 'India' once 'Amazon' is stripped in some orders.
    if lowered.startswith("india "):
        cleaned = cleaned[6:].strip(" -")
    for noise in ("tax invoice", "invoice", "receipt", "bill", "order"):
        if lowered.endswith(noise):
            cleaned = cleaned[: -len(noise)].strip(" -")
            lowered = cleaned.lower()
    return cleaned.title() if 3 < len(cleaned) < 90 else ""


def parse_reference_numbers(text: str):
    """Extract invoice number and order number, as printed on the bill.

    Service centres ask for these first, so they are worth capturing.
    Only values sitting right next to their label are accepted.
    """
    label_words = {
        "invoice", "order", "number", "date", "total", "amount", "state",
        "bill", "tax", "id", "no", "the", "customer", "place", "supply",
        "category", "name", "gst", "gstin",
    }

    def _valid(value):
        value = value.strip(" .,-")
        if len(value) < 4 or value.lower() in label_words:
            return ""
        # Real invoice/order numbers contain a digit. This guard also
        # stops the next label being captured as the value (e.g. the
        # Rapido layout, where "Invoice No." is followed by "Invoice
        # Date" on the next line and the real value sits elsewhere).
        if not any(ch.isdigit() for ch in value):
            return ""
        # Reject obvious non-numbers (e.g. a date used as the value).
        if re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", value):
            return ""
        return value

    def _find(patterns):
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                value = _valid(match.group(1))
                if value:
                    return value
        return ""

    invoice_no = _find((
        r"tax\s*invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{3,})",
        r"invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{3,})",
        r"\binv\.?\s*(?:no\.?|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{3,})",
        # Some PDFs (Rapido) print the value on the line ABOVE its label.
        r"\n\s*([A-Za-z0-9][A-Za-z0-9\-/]{5,})\s*\n\s*invoice\s*(?:no\.?|number|#)\b",
    ))
    order_no = _find((
        r"order\s*(?:number|no\.?|id|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})",
        r"\n\s*([A-Za-z0-9][A-Za-z0-9\-]{5,})\s*\n\s*order\s*(?:number|no\.?|id|#)\b",
    ))
    return {"invoice_no": invoice_no, "order_no": order_no}


def parse_fields(text: str, filename: str):
    """Best-effort invoice extraction; always verify OCR results."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Prefer a plausible product line, skipping common invoice headings,
    # address/contact lines and table headers. Item rows in Indian tax
    # invoices sit just after the HSN table header, so look there first.
    ignored = ("invoice", "receipt", "order id", "order number", "gstin",
               "bill to", "ship to", "ship from", "tax invoice", "date:",
               "amount", "subtotal", "original for recipient", "duplicate for",
               "seller", "consignor", "customer support", "customer care", "customer name", "customer address",
               "email", "mobile", "state code", "place of supply",
               "payment mode", "carrier", "awb", "hsn", "qty", "price",
               "unit", "mrp", "discount", "taxable", "tax amt",
               "limited", "ltd.", " pvt", "hostel", "sector",
               "floor", "total in words",
               "distance", "duration", "payment summary", "bill details",
               "ride id", "time of ride", "ride charge", "booking fee",
               "convenience", "you paid", "inclusive of taxes",
               "thank you", "captain")

    def _looks_like_product(line: str) -> bool:
        low = line.lower()
        if low in KNOWN_SELLERS:
            return False
        if any(word in low for word in ignored):
            return False
        if re.search(r"\b\d{6}\b", line):  # PIN code -> address line
            return False
        if sum(c.isdigit() for c in line) > 4:  # mostly numbers/codes
            return False
        # A line that is only a money value ("₹ 79.82") is not a product.
        if re.fullmatch(r"[-+]?\s*(?:₹|rs\.?|inr)?\s*[0-9][0-9,]*\.?[0-9]*\s*", low):
            return False
        # A pure measurement ("9.86 kms", "24.12 mins") is not a product.
        if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:kms?|km|mins?|minutes|hrs?|hours|kg|gms?|ml|l)\.?", low):
            return False
        return 3 < len(line) < 90

    product = ""
    hsn_idx = next((i for i, ln in enumerate(lines) if "hsn" in ln.lower()), None)
    search_windows = []
    if hsn_idx is not None:
        search_windows.append(lines[hsn_idx:hsn_idx + 15])
    search_windows.append(lines)
    for window in search_windows:
        for line in window:
            if _looks_like_product(line):
                product = line
                break
        if product:
            break
    filename_product = _filename_product(filename)
    if filename_product and (not product or product.strip().lower() in KNOWN_SELLERS
                             or "invoice" in product.lower() or len(product) < 5):
        product = filename_product
    if not product:
        product = filename_product or "New bill"

    amount = None
    # Indian invoices phrase the payable total many different ways
    # ("NET PAYABLE AMOUNT: 331.30", "TOTAL INVOICE VALUE", ...), often as a
    # plain number with no ₹/Rs symbol. Try the labels in priority order and
    # accept the first positive value sitting right after the label (the
    # number may be on the following line when PDF columns are reordered).
    total_labels = (
        "net payable amount", "net payable", "payable amount",
        "total invoice value", "invoice value", "grand total",
        "amount payable", "total amount", "amount paid", "total payable",
        "invoice total", "bill total", "total",
    )
    for label in total_labels:
        for match in re.finditer(
            re.escape(label) + r"\s*[:\-]?\s*(?:₹|INR|Rs\.?)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            text,
            re.IGNORECASE,
        ):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if value > 0:
                amount = value
                break
        if amount is not None:
            break
    if amount is None:
        amount_matches = re.findall(
            r"(?:₹|INR|Rs\.?)\s*([0-9,]+(?:\.[0-9]{1,2})?)",
            text,
            re.IGNORECASE,
        )
        if amount_matches:
            try:
                # The bill total is normally the largest amount on the page,
                # not the last one (which is often a tax component).
                amount = max(float(m.replace(",", "")) for m in amount_matches)
            except ValueError:
                pass

    # Prefer dates near a purchase/invoice label, but never a future
    # date or a renewal/expiry date (see _select_purchase_date).
    purchase_date = _select_purchase_date(text)

    seller = ""
    lower_text = (text + " " + filename).lower()
    for candidate in KNOWN_SELLERS:
        # Short names ("ola", "uber") need word boundaries — as plain
        # substrings they also hide inside words like "chocolate".
        if len(candidate) <= 4:
            found = re.search(rf"\b{re.escape(candidate)}\b", lower_text)
        else:
            found = candidate in lower_text
        if found:
            seller = candidate.title()
            break

    references = parse_reference_numbers(text)
    if not references["invoice_no"]:
        # Last resort: ride/food invoices often carry the number in the
        # filename itself, e.g. ORDER_INVOICE_RD17925806574029425.pdf.
        name_match = re.search(r"[A-Za-z]{2}\d{8,}|\d{4,}[A-Za-z]{2}\d+", filename or "")
        if name_match:
            references["invoice_no"] = name_match.group(0)

    # Ride bills have no product line to find — the service IS the item.
    if seller in ("Rapido", "Ola", "Uber") and (
        "ride charge" in lower_text or " kms" in lower_text
    ):
        product = f"{seller} ride"

    return {
        "product": product,
        "seller": seller,
        "purchase_date": purchase_date,
        "amount": amount,
        "warranty_months": parse_warranty_months(text),
        "warranty_end": parse_warranty_end(text),
        "warranty_lifetime": parse_warranty_lifetime(text),
        "invoice_no": references["invoice_no"],
        "order_no": references["order_no"],
    }

def add_months(original_date: date, months: int) -> date:

    import calendar

    month_index = original_date.month - 1 + months

    year = original_date.year + month_index // 12

    month = month_index % 12 + 1

    day = min(

        original_date.day,

        calendar.monthrange(year, month)[1],

    )

    return date(year, month, day)

def warranty_status(end_text, warranty_months=None, warranty_lifetime=0):

    if warranty_lifetime:

        return "Active"

    if not end_text:

        # A known warranty duration whose end date could not be

        # calculated (e.g. no purchase date was read) is still a

        # warranty — it must not be reported as "No Warranty".

        if warranty_months:

            return "Active"

        return "No Warranty"

    try:

        end_date = date.fromisoformat(end_text)

    except (ValueError, TypeError):

        return "Unknown"

    days_remaining = (end_date - date.today()).days

    if days_remaining < 0:

        return "Expired"

    if days_remaining <= 30:

        return "Expiring Soon"

    return "Active"

# --------------------------------------------------
# AI BILL READER (Gemini vision) — reads handwriting
# --------------------------------------------------
# Print OCR (Tesseract) cannot read pen handwriting, which is how many
# Indian shop bills are written. When a Gemini API key is configured
# (GEMINI_API_KEY or GOOGLE_API_KEY), photo uploads are also read by
# Google's Gemini vision model, which reads printed AND handwritten
# text. Everything is best-effort: with no key, or on any API failure,
# the upload simply uses the normal OCR text exactly as before.

# Newest first. Google limits older models (2.5 and below) to projects
# that used them before, so a freshly created API key can only use the
# current models — the list must start with those.
AI_READER_MODELS = (
    "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash",
    "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash",
)

# Diagnostics for /api/health: what the AI reader last did, so a silent
# fallback (e.g. a retired model name) can be spotted without server logs.
_ai_reader_state = {"last_status": "not tried yet", "last_model": "", "last_error": ""}

AI_READER_PROMPT = (
    "You are reading a photo of an Indian purchase bill or invoice. "
    "The bill may mix printed text and handwriting. Read it carefully and "
    "reply with ONLY a JSON object (no markdown, no explanation) with keys: "
    "\"text\": a full transcription of all readable text on the bill; "
    "\"product\": the main product/item bought (brand + model), or \"\"; "
    "\"seller\": the shop/store name, or \"\"; "
    "\"purchase_date\": the bill date as YYYY-MM-DD, or \"\"; "
    "\"amount\": the grand total amount payable as a plain number (no commas, no currency symbol), or null; "
    "\"invoice_no\": the invoice/bill number, or \"\"; "
    "\"order_no\": the order number/id, or \"\"; "
    "\"warranty_months\": warranty duration in months as an integer (convert years to months; 0 if none stated); "
    "\"warranty_end\": warranty end/expiry date as YYYY-MM-DD if written, else \"\"; "
    "\"warranty_lifetime\": true if a lifetime warranty is stated, else false; "
    "\"seller_domain\": the seller/company's official website domain (e.g. \"croma.com\"), or \"\" if you do not know it; "
    "\"company_about\": one short line saying what the company/seller does (e.g. \"Electronics retail\"), or \"\"; "
    "\"category\": the purchase category, exactly one of \"Food\", \"Travel\", \"Electronics\", \"Fashion\", \"Home & Kitchen\", \"Appliances\", \"Furniture\", \"Personal care\", \"Education\", \"Health\", \"Entertainment\", \"Bills & Utilities\", \"Other\". Guidance: college, university or school fees and hostel charges are Education; medicines, hospitals and clinics are Health; apps, games, subscriptions and movies are Entertainment; electricity, internet, phone recharge and similar utility payments are Bills & Utilities. "
    "Never guess a value you cannot read; use \"\" or null instead."
)


def _gemini_api_key() -> str:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def _parse_ai_json(raw: str) -> dict:
    """Tolerant JSON extraction from a model reply."""
    if not raw:
        return {}
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_ai_fields(parsed: dict) -> dict:
    """Validate/coerce the model's field values before they are trusted."""
    fields = {}
    for key in ("product", "seller", "invoice_no", "order_no"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value.strip()[:200]
    for key in ("purchase_date", "warranty_end"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            iso = parse_invoice_date(value.strip())
            if not iso:
                continue
            # A purchase date in the future is a misread (usually a
            # subscription renewal date): drop it so the caller's
            # fallbacks (parsed text, email date) apply instead.
            # warranty_end, by contrast, is supposed to be future.
            if key == "purchase_date" and _is_future_date(iso):
                continue
            fields[key] = iso
    amount = parsed.get("amount")
    try:
        amount = float(str(amount).replace(",", "")) if amount is not None else None
    except (TypeError, ValueError):
        amount = None
    if amount is not None and 0 < amount < 100000000:
        fields["amount"] = amount
    try:
        months = int(parsed.get("warranty_months") or 0)
    except (TypeError, ValueError):
        months = 0
    if 0 < months <= 120:
        fields["warranty_months"] = months
    if parsed.get("warranty_lifetime") in (True, 1, "true", "True", "yes"):
        fields["warranty_lifetime"] = 1
    domain = _clean_domain(parsed.get("seller_domain"))
    if domain:
        fields["seller_domain"] = domain
    about = parsed.get("company_about")
    if isinstance(about, str) and about.strip():
        fields["company_about"] = about.strip()[:140]
    category = parsed.get("category")
    if isinstance(category, str) and category.strip() in BILL_CATEGORIES:
        fields["category"] = category.strip()
    return fields


def ai_read_bill(data: bytes, content_type: str) -> dict:
    """Read a bill photo with Gemini vision. Returns {"text", "fields"} or {}."""
    api_key = _gemini_api_key()
    if not api_key or not data:
        return {}
    base = os.getenv(
        "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    models = []
    for candidate in [os.getenv("GEMINI_MODEL", "")] + list(AI_READER_MODELS):
        if candidate and candidate not in models:
            models.append(candidate)
    payload = {
        "contents": [{
            "parts": [
                {"text": AI_READER_PROMPT},
                {"inline_data": {
                    "mime_type": content_type,
                    "data": base64.b64encode(data).decode("ascii"),
                }},
            ]
        }],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    for model in models:
        try:
            # trust_env=False: ignore any proxy variables of the host —
            # the Gemini API is always reached directly (e.g. on Render).
            with httpx.Client(timeout=45.0, trust_env=False) as client:
                response = client.post(
                    f"{base}/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                )
            if response.status_code != 200:
                _ai_reader_state.update({
                    "last_status": "failed",
                    "last_model": model,
                    "last_error": f"HTTP {response.status_code}: {response.text[:160]}",
                })
                continue
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            raw = "".join(part.get("text", "") for part in parts)
            parsed = _parse_ai_json(raw)
            if not parsed:
                _ai_reader_state.update({
                    "last_status": "failed",
                    "last_model": model,
                    "last_error": "reply was not valid JSON",
                })
                continue
            _ai_reader_state.update({
                "last_status": "ok",
                "last_model": model,
                "last_error": "",
            })
            return {
                "text": str(parsed.get("text") or ""),
                "fields": _normalize_ai_fields(parsed),
            }
        except Exception as exc:
            _ai_reader_state.update({
                "last_status": "failed",
                "last_model": model,
                "last_error": f"{type(exc).__name__}: {exc}"[:200],
            })
            continue
    return {}


def _ai_read_pdf(data: bytes) -> dict:
    """Read an image-only PDF with the AI reader, page by page.

    Some senders (EatSure is one) generate PDFs whose text is drawn
    as pictures/vector outlines: there is no text layer for the PDF
    reader, and print OCR on a small free-tier server is too slow —
    it hits its timeout and the bill is silently skipped, which is
    why Rapido's text PDFs imported while EatSure's never did.
    Rendering the pages to images and letting the vision model read
    them (the same reader used for bill photos) takes seconds.
    Returns {"text", "fields"} like ai_read_bill, or {}."""
    if not fitz or not data or not _gemini_api_key():
        return {}
    try:
        merged_text = []
        merged_fields = {}
        with fitz.open(stream=data, filetype="pdf") as document:
            for index in range(min(len(document), 2)):
                pix = document[index].get_pixmap(
                    dpi=150, colorspace=fitz.csRGB, alpha=False)
                result = ai_read_bill(pix.tobytes("png"), "image/png")
                if not result:
                    continue
                page_text = (result.get("text") or "").strip()
                if page_text:
                    merged_text.append(page_text)
                for key, value in (result.get("fields") or {}).items():
                    merged_fields.setdefault(key, value)
                # Receipts put everything that matters on page 1;
                # only read page 2 when the amount is still missing.
                if merged_fields.get("amount") is not None:
                    break
        if merged_text:
            return {
                "text": "\n".join(merged_text)[:20000],
                "fields": merged_fields,
            }
    except Exception as exc:
        print(f"AI PDF READ ERROR: {type(exc).__name__}: {exc}")
    return {}



_CSS_PROPS = {
    "font-family", "font-size", "font-weight", "font-style", "line-height",
    "color", "background", "background-color", "margin", "margin-top",
    "margin-bottom", "margin-left", "margin-right", "padding",
    "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-top", "border-bottom", "border-left", "border-right",
    "border-radius", "width", "height", "max-width", "min-width",
    "display", "text-align", "text-decoration", "vertical-align",
    "letter-spacing", "white-space", "word-break", "overflow",
    "position", "top", "left", "right", "bottom", "float", "clear",
    "content", "box-sizing", "box-shadow", "cursor", "visibility",
    "opacity", "z-index", "text-transform", "list-style", "outline",
}

def _clean_display_text(text):
    """Bill text saved from HTML emails can carry the email's CSS
    (style blocks whose tags were stripped, leaving lines like
    'font-family: ...;'). Remove those lines so the bill reads like
    a bill. Conservative: a line is dropped only when it is a known
    CSS property declaration, a brace, a bare HTML-tag selector, or
    a selector line opening a CSS block."""
    if not text:
        return text
    tags = {"html", "body", "div", "span", "table", "tr", "td", "th",
            "thead", "tbody", "p", "a", "img", "ul", "ol", "li", "h1",
            "h2", "h3", "h4", "h5", "h6", "head", "style", "link",
            "meta", "br", "hr", "form", "input", "button", "select",
            "option", "label", "section", "article", "header",
            "footer", "nav", "main", "strong", "b", "i", "em",
            "small", "center", "font"}
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s in ("{", "}"):
            continue
        m = re.match(r"^([a-zA-Z-]+)\s*:\s*[^:;]+;\s*$", s)
        if m and (m.group(1).lower() in _CSS_PROPS or "-" in m.group(1)):
            continue
        m = re.match(r"^([.#]?)([a-zA-Z][\w-]*)\s*,?$", s)
        if m and (m.group(1) or m.group(2).lower() in tags):
            continue
        if s.endswith("{") and len(s) < 100 and re.match(
                r"^[@.#a-zA-Z][\w\s.,#:>+~*()\[\]='\"-]*\{$", s):
            continue
        core = s[:-1].strip() if s.endswith((",", "{")) else s
        if core != s:
            ok, marked = True, False
            for piece in core.split(","):
                for word in piece.split():
                    if word in (">", "+", "~", "*"):
                        marked = marked or word == "*"
                        continue
                    if not re.match(
                            r"^[.#*]?[a-zA-Z][\w-]*(:[a-z-]+(\([^)]*\))?)?$",
                            word):
                        ok = False
                        break
                    if (":" in word or word.startswith((".", "#"))
                            or word.strip(".:#").lower() in tags):
                        marked = True
                if not ok:
                    break
            if ok and marked:
                continue
        out.append(ln)
    cleaned = re.sub(r"\n\s*\n+", "\n", "\n".join(out)).strip()
    return cleaned or text


# --------------------------------------------------
# AGENT V1 — verify, remember, report
#
# The agent's first tools. Every newly saved bill is assessed the
# moment it is stored: when a field could not be found, or the
# printed total disagrees with the saved amount, the bill is
# flagged needs_review WITH the reason, instead of the old silent
# failure (blank bills nobody was told about). Category
# corrections a user makes by hand are remembered as personal
# rules for that seller, so the same fix never has to be repeated.
# --------------------------------------------------

_LEARN_SKIP_SELLERS = {"amazon", "flipkart"}


def _seller_key(seller) -> str:
    """Normalised seller identity used for learned rules."""
    return re.sub(r"[^a-z0-9]+", " ", str(seller or "").lower()).strip()


def _learned_category(owner, seller):
    """The category this owner taught Raseed for this seller, if any."""
    key = _seller_key(seller)
    if not owner or not key or key in _LEARN_SKIP_SELLERS:
        return ""
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT category FROM learned_rules"
                " WHERE owner_email=? AND seller_key=?",
                (owner, key),
            ).fetchone()
        return (row[0] if row else "") or ""
    except Exception:
        return ""


def _save_learned_category(owner, seller, category):
    """Remember: bills from this seller belong to this category."""
    key = _seller_key(seller)
    if not owner or not key or key in _LEARN_SKIP_SELLERS:
        return
    if category not in BILL_CATEGORIES:
        return
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO learned_rules
                    (owner_email, seller_key, category, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_email, seller_key)
                DO UPDATE SET category=excluded.category,
                              updated_at=excluded.updated_at
                """,
                (owner, key, category,
                 datetime.utcnow().isoformat(timespec="seconds")),
            )
    except Exception:
        pass


_TOTAL_LABEL_PATTERN = re.compile(
    r"(?:grand\s*total|net\s*payable(?:\s*amount)?"
    r"|total\s*(?:invoice\s*)?value|amount\s*payable"
    r"|total\s*amount|invoice\s*total|total)"
    r"\s*[:\-]?\s*(?:\u20B9|rs\.?|inr)?\s*"
    r"([0-9][0-9,]*\.?[0-9]*)",
    re.IGNORECASE,
)


def _review_problems(product, seller, amount, purchase_date,
                     extracted_text):
    """Plain-language reasons this bill deserves a human look.

    Conservative on purpose: a flag is raised only for things that
    are actually broken (missing amount/date/seller, an impossible
    date, or a printed total that disagrees with the saved amount),
    never for mere uncertainty.
    """
    problems = []
    if amount is None:
        problems.append("Amount was not found on this bill")
    elif amount <= 0:
        problems.append("The saved amount looks wrong (Rs 0 or less)")
    if not purchase_date:
        problems.append("Purchase date was not found")
    elif _is_future_date(str(purchase_date)):
        problems.append("Purchase date is in the future")
    if not str(seller or "").strip():
        problems.append("The company/seller could not be identified")
    if amount is not None and extracted_text:
        try:
            labelled = _TOTAL_LABEL_PATTERN.findall(
                str(extracted_text))
            if labelled:
                printed_total = float(labelled[-1].replace(",", ""))
                if printed_total > 0 and abs(printed_total - amount) > 1.0:
                    problems.append(
                        "The total printed on the bill (Rs "
                        + f"{printed_total:,.2f}) does not match the "
                        + f"saved amount (Rs {amount:,.2f})"
                    )
        except Exception:
            pass
    seen = set()
    unique = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique


def _assess_review(product, seller, amount, purchase_date,
                   extracted_text):
    """(needs_review, review_reason) for a bill about to be saved."""
    problems = _review_problems(
        product, seller, amount, purchase_date, extracted_text)
    return (1 if problems else 0), "; ".join(problems)


def _norm_ref(value):
    """Normalise an invoice/order number for comparison."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _duplicate_match(conn, owner, seller, product, amount,
                     purchase_date, invoice_no, order_no,
                     exclude_id=None):
    """(kind, row) if another of the owner's bills looks like the
    same purchase, else None. kind "ref" = a shared invoice/order
    number (certain); kind "fuzzy" = same company + amount +
    purchase date (probable). The guard that stops one purchase
    arriving in several emails from becoming several bills."""
    try:
        rows = conn.execute(
            "SELECT id, product, seller, amount, purchase_date,"
            " invoice_no, order_no FROM bills WHERE owner_email=?",
            (owner,),
        ).fetchall()
    except Exception:
        return None
    new_refs = {_norm_ref(invoice_no), _norm_ref(order_no)} - {""}
    fuzzy_key = None
    if amount is not None and purchase_date:
        try:
            fuzzy_key = (
                _seller_key(seller or product or ""),
                round(float(amount), 2),
                str(purchase_date),
            )
        except (TypeError, ValueError):
            fuzzy_key = None
    fuzzy_hit = None
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        if new_refs:
            old_refs = {
                _norm_ref(row["invoice_no"]),
                _norm_ref(row["order_no"]),
            } - {""}
            if new_refs & old_refs:
                return "ref", row
        if fuzzy_key and fuzzy_hit is None:
            # Proven distinct: both bills carry an invoice (or
            # order) number and the numbers differ — never a
            # duplicate, however similar the rest looks.
            inv_conflict = (
                _norm_ref(invoice_no) and _norm_ref(row["invoice_no"])
                and _norm_ref(invoice_no) != _norm_ref(row["invoice_no"]))
            ord_conflict = (
                _norm_ref(order_no) and _norm_ref(row["order_no"])
                and _norm_ref(order_no) != _norm_ref(row["order_no"]))
            if inv_conflict or ord_conflict:
                continue
            if row["amount"] is not None and row["purchase_date"]:
                try:
                    key = (
                        _seller_key(
                            row["seller"] or row["product"] or ""),
                        round(float(row["amount"]), 2),
                        str(row["purchase_date"]),
                    )
                except (TypeError, ValueError):
                    continue
                if key == fuzzy_key:
                    fuzzy_hit = row
    if fuzzy_hit is not None:
        return "fuzzy", fuzzy_hit
    return None


def _duplicate_reason(match):
    """Plain-language Possible-duplicate reason for a match."""
    kind, row = match
    other = str(row["product"] or "another bill")[:60]
    if kind == "ref":
        return (
            "Possible duplicate: the same invoice/order number "
            f"is already on '{other}'"
        )
    return (
        "Possible duplicate: same company, amount and date as "
        f"'{other}'"
    )


def _duplicate_problem(conn, owner, seller, product, amount,
                       purchase_date, invoice_no, order_no,
                       exclude_id=None):
    """Possible-duplicate reason for a bill about to be saved,
    or "" when nothing in the vault looks like the same purchase."""
    match = _duplicate_match(
        conn, owner, seller, product, amount, purchase_date,
        invoice_no, order_no, exclude_id=exclude_id)
    return _duplicate_reason(match) if match else ""


def row_to_dict(row):

    result = dict(row)

    if result.get("extracted_text"):

        result["extracted_text"] = _clean_display_text(
            result["extracted_text"])

    # Self-repair of future purchase dates: bills imported before the
    # future-date guard existed can carry a renewal/next-billing date
    # as their purchase_date (a Google Play bill showed 13 Mar 2027),
    # which also poisons any warranty end computed from it. Recompute
    # from the bill's own saved text with the fixed parser; failing
    # that, use the day the bill entered the vault. Read-time only,
    # like the other backfills below — the stored row is not rewritten.
    stored_purchase_date = str(result.get("purchase_date") or "")

    if stored_purchase_date and _is_future_date(stored_purchase_date):

        repaired_date = ""

        saved_text = result.get("extracted_text") or ""

        if saved_text:

            try:

                repaired_date = parse_fields(

                    saved_text,

                    result.get("original_filename") or "",

                ).get("purchase_date") or ""

            except Exception:

                repaired_date = ""

        if not repaired_date:

            repaired_date = str(result.get("created_at") or "")[:10]

        result["purchase_date"] = repaired_date

        # warranty_end was computed from the bad date at insert time;
        # recompute it from the repaired date the same way inserts do.
        # Lifetime warranties have no end date to move.
        warranty_months = result.get("warranty_months") or 0

        if warranty_months and result.get("warranty_end") \
                and not result.get("warranty_lifetime") and repaired_date:

            try:

                result["warranty_end"] = add_months(

                    date.fromisoformat(repaired_date),

                    int(warranty_months),

                ).isoformat()

            except (ValueError, TypeError):

                pass

    result["warranty_status"] = warranty_status(

        result.get("warranty_end", ""),

        result.get("warranty_months"),

        result.get("warranty_lifetime", 0),

    )

    result.setdefault("warranty_lifetime", 0)

    # Bills saved before invoice/order numbers were captured can still
    # show them: re-read the numbers from the bill's own saved text.
    if not result.get("invoice_no") or not result.get("order_no"):

        saved_text = result.get("extracted_text") or ""

        if saved_text:

            try:

                references = parse_reference_numbers(saved_text)

                if not result.get("invoice_no"):

                    result["invoice_no"] = references["invoice_no"]

                if not result.get("order_no"):

                    result["order_no"] = references["order_no"]

            except Exception:

                result.setdefault("invoice_no", "")

                result.setdefault("order_no", "")

    result.setdefault("invoice_no", "")

    result.setdefault("order_no", "")

    # Company identification backfill: bills saved before logos were
    # captured (or whose seller the map/AI could only name) still get
    # a logo + about line from their seller, without a re-upload.
    if not result.get("logo_url") or not result.get("company_about"):

        try:

            enriched = enrich_company(result.get("seller") or "")

            if not result.get("logo_url"):

                result["logo_url"] = enriched["logo_url"]

            if not result.get("company_domain"):

                result["company_domain"] = enriched["company_domain"]

            if not result.get("company_about"):

                result["company_about"] = enriched["company_about"]

        except Exception:

            pass

    result.setdefault("logo_url", "")

    result.setdefault("company_domain", "")

    result.setdefault("company_about", "")

    # Category backfill: bills saved before automatic categorisation
    # (nearly everything was stored as "Other") get an inferred
    # category at read time, so the spending donut splits by real
    # categories without anyone re-importing old bills. Read-time
    # only — the stored value is not rewritten here.
    if not result.get("category") or result.get("category") == "Other":

        try:

            result["category"] = infer_category(

                result.get("seller") or "",

                result.get("product") or "",

                result.get("extracted_text") or "",

            )

        except Exception:

            result.setdefault("category", "Other")

    result.setdefault("category", "Other")

    # Arrangement ("received") date: the day this bill arrived
    # in Raseed — for Gmail bills the day the email came, for
    # scanned / uploaded bills the day they were added. Bills are
    # ARRANGED by this date; purchase_date stays the date printed
    # on the bill and is never used for ordering. Bills saved
    # before this field existed fall back to the day they entered
    # the vault (created_at).
    received_value = str(result.get("received_date") or "")

    try:

        date.fromisoformat(received_value)

    except (ValueError, TypeError):

        received_value = str(result.get("created_at") or "")[:10]

    result["received_date"] = received_value

    # How this bill arrived: imported from Gmail, or scanned/uploaded.
    source_key = result.get("source_key") or ""

    result["source"] = "gmail" if str(source_key).startswith("gmail:") else "scan"

    result["has_file"] = bool(str(result.get("public_id") or ""))

    # Agent v1 needs-review: bills assessed at save time carry a
    # stored verdict (review_reason is '' when assessed clean, and
    # also when the owner has marked the bill correct). Bills saved
    # before the agent existed have review_reason NULL and are
    # assessed live here, so old blank bills get flagged without
    # anyone re-importing them.
    if result.get("review_reason") is not None:

        result["needs_review"] = 1 if result.get("needs_review") else 0

    else:

        _problems = _review_problems(

            result.get("product"),

            result.get("seller"),

            result.get("amount"),

            result.get("purchase_date"),

            result.get("extracted_text"),

        )

        result["needs_review"] = 1 if _problems else 0

        result["review_reason"] = "; ".join(_problems)

    result.setdefault("review_reason", "")

    return result

class BillUpdate(BaseModel):

    product: Optional[str] = None

    seller: Optional[str] = None

    purchase_date: Optional[str] = None

    amount: Optional[float] = None

    warranty_months: Optional[int] = None

    category: Optional[str] = None

    invoice_no: Optional[str] = None

    order_no: Optional[str] = None

    warranty_lifetime: Optional[int] = None

    needs_review: Optional[int] = None

# --------------------------------------------------

# GOOGLE LOGIN

# --------------------------------------------------

@app.get("/api/auth/google")

async def google_login(request: Request):

    if not google_oauth_configured():

        raise HTTPException(

            status_code=503,

            detail="Add Google OAuth credentials to backend/.env."

        )

    redirect_uri = os.getenv(

        "GOOGLE_REDIRECT_URI",

        "http://localhost:8000/api/auth/google/callback",

    )

    state = os.urandom(24).hex()

    request.session["oauth_state"] = state

    request.session["oauth_purpose"] = "login"

    params = {

        "client_id": os.getenv("GOOGLE_CLIENT_ID"),

        "redirect_uri": redirect_uri,

        "response_type": "code",

        "scope": " ".join(LOGIN_SCOPES),

        "prompt": "select_account",

        "state": state,

    }

    return RedirectResponse(

        "https://accounts.google.com/o/oauth2/v2/auth?"

        + urlencode(params)

    )

@app.get("/api/auth/gmail/connect")

async def gmail_connect(request: Request):

    """Separate, optional step: connect Gmail for bill scanning.

    Requests the Gmail read scope, so Google limits it while the app

    is unverified - the plain login above does not need it."""

    if not google_oauth_configured():

        raise HTTPException(

            status_code=503,

            detail="Add Google OAuth credentials to backend/.env.",

        )

    redirect_uri = os.getenv(

        "GOOGLE_REDIRECT_URI",

        "http://localhost:8000/api/auth/google/callback",

    )

    state = os.urandom(24).hex()

    request.session["oauth_state"] = state

    request.session["oauth_purpose"] = "gmail"

    params = {

        "client_id": os.getenv("GOOGLE_CLIENT_ID"),

        "redirect_uri": redirect_uri,

        "response_type": "code",

        "scope": " ".join(GOOGLE_SCOPES),

        "access_type": "offline",

        "include_granted_scopes": "true",

        "prompt": "consent",

        "state": state,

    }

    return RedirectResponse(

        "https://accounts.google.com/o/oauth2/v2/auth?"

        + urlencode(params)

    )

@app.get("/api/auth/google/callback")

async def google_callback(request: Request):

    error = request.query_params.get("error")

    if error:

        return RedirectResponse(

            FRONTEND_ORIGIN + "/?gmail_error=" + error

        )

    code = request.query_params.get("code")

    state = request.query_params.get("state")

    if (

        not code

        or not state

        or state != request.session.get("oauth_state")

    ):

        raise HTTPException(

            status_code=400,

            detail="Google sign-in state check failed. Please try again."

        )

    purpose = request.session.get("oauth_purpose", "gmail")

    redirect_uri = os.getenv(

        "GOOGLE_REDIRECT_URI",

        "http://localhost:8000/api/auth/google/callback",

    )

    async with httpx.AsyncClient(timeout=20) as client:

        token_response = await client.post(

            "https://oauth2.googleapis.com/token",

            data={

                "code": code,

                "client_id": os.getenv("GOOGLE_CLIENT_ID"),

                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),

                "redirect_uri": redirect_uri,

                "grant_type": "authorization_code",

            },

        )

        if token_response.status_code >= 400:

            raise HTTPException(

                status_code=400,

                detail=(

                    "Google token exchange failed. Check your "

                    "OAuth redirect URI and credentials."

                ),

            )

        token_data = token_response.json()

        user_response = await client.get(

            "https://www.googleapis.com/oauth2/v2/userinfo",

            headers={

                "Authorization":

                f"Bearer {token_data['access_token']}"

            },

        )

        if user_response.status_code >= 400:

            raise HTTPException(

                status_code=400,

                detail="Could not retrieve the Google account profile."

            )

        profile = user_response.json()

    email = (profile.get("email") or "").lower()

    if not email:

        raise HTTPException(

            status_code=400,

            detail="Google did not return an email address."

        )

    if purpose == "gmail":
        with db() as conn:

            existing = conn.execute(

                "SELECT refresh_token FROM gmail_connections WHERE email=?",

                (email,),

            ).fetchone()

            refresh_token = token_data.get("refresh_token")

            if not refresh_token and existing:

                refresh_token = existing["refresh_token"]

            conn.execute(

                """

                INSERT INTO gmail_connections (

                    email, access_token, refresh_token,

                    token_uri, scope, updated_at

                )

                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(email) DO UPDATE SET

                    access_token=excluded.access_token,

                    refresh_token=excluded.refresh_token,

                    token_uri=excluded.token_uri,

                    scope=excluded.scope,

                    updated_at=excluded.updated_at

                """,

                (

                    email,

                    token_data["access_token"],

                    refresh_token or "",

                    "https://oauth2.googleapis.com/token",

                    token_data.get("scope", ""),

                    datetime.utcnow().isoformat(timespec="seconds"),

                ),

            )

    # Remember the user (with the name Google gives us) so the app can

    # ask the first-time questions and personalise the vault.

    with db() as conn:

        conn.execute(

            """

            INSERT INTO users (email, name, created_at) VALUES (?, ?, ?)

            ON CONFLICT(email) DO UPDATE SET

                name = CASE WHEN users.name = '' THEN excluded.name ELSE users.name END

            """,

            (

                email,

                (profile.get("name") or "").strip(),

                datetime.utcnow().isoformat(timespec="seconds"),

            ),

        )

    request.session["owner_email"] = email

    request.session.pop("oauth_state", None)

    request.session.pop("oauth_purpose", None)

    # Hand the website a login token as well as the session cookie, so
    # sign-in also sticks on phones whose browsers block the cross-site
    # cookie. The token travels in the URL fragment, which is never sent
    # to any server; the website stores it and removes it from the URL.
    return RedirectResponse(

        FRONTEND_ORIGIN + "/#raseed_token=" + issue_login_token(email)

    )

@app.get("/api/auth/me")

def auth_me(request: Request):

    email = current_owner(request)

    configured = google_oauth_configured()

    if email == "local-demo":

        return {

            "connected": False,

            "email": None,

            "auth_configured": configured,

            "profile": None,

            "profile_complete": False,

        }

    profile = get_user_profile(email)

    return {

        "connected": True,

        "email": email,

        "auth_configured": configured,

        "profile": profile,

        "profile_complete": profile_complete(profile),

    }

class ProfileUpdate(BaseModel):

    name: str

    purpose: str

    age: int

@app.post("/api/auth/profile")

def save_profile(payload: ProfileUpdate, request: Request):

    email = current_owner(request)

    if email == "local-demo":

        raise HTTPException(

            status_code=401,

            detail="Sign in with Google first.",

        )

    name = payload.name.strip()

    purpose = payload.purpose.strip()

    if not name or not purpose:

        raise HTTPException(

            status_code=400,

            detail="Please tell us your name and what you will use Raseed for.",

        )

    if payload.age < 5 or payload.age > 120:

        raise HTTPException(

            status_code=400,

            detail="Please enter a valid age.",

        )

    was_complete = profile_complete(get_user_profile(email))

    with db() as conn:

        conn.execute(

            """

            INSERT INTO users (email, name, purpose, age, created_at)

            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(email) DO UPDATE SET

                name=excluded.name, purpose=excluded.purpose, age=excluded.age

            """,

            (

                email,

                name,

                purpose,

                payload.age,

                datetime.utcnow().isoformat(timespec="seconds"),

            ),

        )

        if not was_complete:

            # First-time setup: bills saved before accounts existed (the

            # shared local-demo vault) become this user's own bills, so

            # signing in never makes existing bills disappear.

            conn.execute(

                "UPDATE bills SET owner_email=? WHERE owner_email='local-demo'",

                (email,),

            )

    if not was_complete:

        backup_vault()

    profile = get_user_profile(email)

    return {

        "connected": True,

        "email": email,

        "auth_configured": google_oauth_configured(),

        "profile": profile,

        "profile_complete": True,

    }

@app.post("/api/auth/logout")

def auth_logout(request: Request):

    request.session.clear()

    return {"ok": True}

@app.get("/api/gmail/status")

def gmail_status(request: Request):

    email = current_owner(request)

    if email == "local-demo":

        return {"connected": False, "email": None}

    with db() as conn:

        row = conn.execute(

            "SELECT email FROM gmail_connections WHERE email = ?",

            (email,),

        ).fetchone()

    return {"connected": bool(row), "email": email if row else None}

# --------------------------------------------------

# --------------------------------------------------
# GMAIL BILL SCANNER
# --------------------------------------------------
#
# How a scan runs now (rebuilt after users reported scans that took
# minutes with no feedback, stopped halfway, produced bills with no
# date and no amount, and only showed results after a manual refresh):
#
# * POST /api/gmail/scan starts the scan in a background thread and
#   returns at once. GET /api/gmail/scan/status reports live progress
#   (emails checked / bills imported so far), so the website shows
#   progress and adds new bills to the list as they land — and a page
#   refresh no longer loses the scan, the page just reconnects to it.
# * The scanner follows Gmail's page tokens, so an inbox with more
#   than one page of invoice emails is actually finished.
# * An attachment is imported only when it really looks like a bill
#   (readable text plus an amount or an invoice/order number). Logo
#   and signature images attached to invoice emails used to become
#   blank bills with no date and no amount; they are skipped now,
#   and tiny inline images are never even downloaded.
# * Image bills are read with the AI reader first (the same reader
#   manual uploads use), falling back to OCR when AI is unavailable.
# * When the bill itself yields no date, the email's own date is
#   used; when it yields no amount, the subject/snippet is searched.
# * Order emails with no attachment at all (Amazon, Flipkart and
#   ride receipts often work this way) can become bills too, under
#   the same strict evidence rules.

GMAIL_MAX_NEW_PER_SCAN = 50
GMAIL_MAX_MESSAGES_PER_SCAN = 400
GMAIL_MAX_BODY_MESSAGES_PER_SCAN = 100

_gmail_jobs = {}
_gmail_jobs_lock = threading.Lock()


def _gmail_job_update(email, **fields):
    with _gmail_jobs_lock:
        job = _gmail_jobs.setdefault(email, {})
        job.update(fields)


def _gmail_job_snapshot(email):
    with _gmail_jobs_lock:
        job = dict(_gmail_jobs.get(email) or {})
    return {
        "state": job.get("state", "idle"),
        "checked": job.get("checked", 0),
        "imported": job.get("imported", 0),
        "skipped": job.get("skipped", 0),
        "more_remaining": job.get("more_remaining", False),
        "note": job.get("note", ""),
        "detail": job.get("detail", ""),
        "status_code": job.get("status_code", 0),
        "errors": job.get("errors", []),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
    }


def _email_date_iso(headers, internal_date):
    """The email's own date as YYYY-MM-DD — the fallback purchase date
    for bills whose text does not yield one."""
    raw = headers.get("date", "")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed is not None:
                return parsed.date().isoformat()
        except Exception:
            pass
    try:
        millis = int(internal_date)
        if millis > 0:
            return datetime.utcfromtimestamp(millis / 1000).date().isoformat()
    except Exception:
        pass
    return ""


def _amount_near_keywords(text):
    """Amount fallback from an email subject/snippet: prefer a currency
    value near words like total/paid, else the largest currency value."""
    if not text:
        return None
    labelled = re.search(
        r"(?:total|paid|payable|amount|charged)\D{0,20}?"
        r"(?:₹|INR|Rs\.?)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        text, re.IGNORECASE)
    if labelled:
        try:
            value = float(labelled.group(1).replace(",", ""))
            if value > 0:
                return value
        except ValueError:
            pass
    values = []
    for match in re.findall(
            r"(?:₹|INR|Rs\.?)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            text, re.IGNORECASE):
        try:
            values.append(float(match.replace(",", "")))
        except ValueError:
            continue
    values = [v for v in values if v > 0]
    return max(values) if values else None


def _decode_gmail_body(payload):
    """Best-effort text of an email body (for attachment-less bills)."""
    chunks = []

    def walk(part):
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        data = body.get("data")
        if data and mime in ("text/plain", "text/html"):
            try:
                raw = base64.urlsafe_b64decode(
                    data + "=" * (-len(data) % 4))
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            if mime == "text/html":
                text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ",
                              text, flags=re.IGNORECASE | re.DOTALL)
                text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
                text = re.sub(r"<(br|/p|/div|/tr)[^>]*>", "\n", text,
                              flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = (text.replace("&nbsp;", " ")
                        .replace("&amp;", "&")
                        .replace("&#8377;", "Rs"))
            chunks.append(text)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    text = "\n".join(chunks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:20000]


def _gmail_seller_fallback(sender):
    sender_email = sender.split("<")[-1].split(">")[0]
    guess = (sender_email.split("@")[0]
             .replace(".", " ").replace("_", " ").title())
    return (guess or "Email merchant")[:80]


def _list_gmail_messages(service, query, cap):
    """Fetch message stubs following page tokens. Returns
    (stubs, more_available)."""
    stubs = []
    page_token = None
    more_available = False
    while len(stubs) < cap:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=100,
            pageToken=page_token,
        ).execute()
        stubs.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    else:
        more_available = bool(page_token)
    if len(stubs) > cap:
        stubs = stubs[:cap]
        more_available = True
    return stubs, more_available


def _perform_gmail_scan(email, report=None):
    """Run one full scan for a connected account. `report` (optional)
    is called with checked/imported/skipped counts as work proceeds.
    Returns the final summary dict; raises HTTPException on auth/API
    failures, exactly as the old synchronous endpoint did."""
    credentials = get_gmail_credentials(email)

    def progress(checked, imported, skipped):
        if report:
            try:
                report(checked=checked, imported=imported, skipped=skipped)
            except Exception:
                pass

    # Bills arrive two ways: as attachments (invoices/receipts), and as
    # attachment-less order emails. Search for both, attachments first.
    attachment_query = (
        'newer_than:365d has:attachment '
        '{invoice receipt bill order purchase warranty}'
    )
    body_query = (
        'newer_than:365d -has:attachment '
        '(invoice OR receipt OR "order confirmation" OR '
        '"payment confirmation" OR "tax invoice" OR '
        '"your order" OR "order delivered")'
    )

    try:
        service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False,
        )

        attachment_stubs, att_more = _list_gmail_messages(
            service, attachment_query, GMAIL_MAX_MESSAGES_PER_SCAN)
        body_stubs, body_more = _list_gmail_messages(
            service, body_query, GMAIL_MAX_BODY_MESSAGES_PER_SCAN)
        more_remaining = att_more or body_more

        # (message stub, allow_body_bill) — attachment mail first.
        plan = [(stub, False) for stub in attachment_stubs]
        plan += [(stub, True) for stub in body_stubs]

        # Every Gmail source already in the vault, loaded once: repeat
        # scans then skip known bills without a database hit per file.
        with db() as conn:
            rows = conn.execute(
                "SELECT source_key FROM bills "
                "WHERE owner_email=? AND source_key LIKE 'gmail:%'",
                (email,),
            ).fetchall()
        known_keys = {row["source_key"] for row in rows}

        imported = 0
        skipped = 0
        checked = 0
        errors = []
        stop_scan = False

        strict_purchase_terms = re.compile(
            r"\b(invoice|receipt|purchase|order confirmation|"
            r"order placed|tax invoice|payment confirmation|"
            r"bill|warranty|your order|order delivered)\b",
            re.IGNORECASE,
        )
        broader_purchase_terms = re.compile(
            r"\b(invoice|receipt|purchase|order|confirmation|"
            r"tax|payment|bill|warranty|delivered|shipment|"
            r"transaction|thank you for your order|"
            r"order number|amount paid|your item)\b",
            re.IGNORECASE,
        )

        for message_stub, allow_body in plan:
            checked += 1
            progress(checked, imported, skipped)
            message_id = message_stub["id"]
            temp_path = None
            try:
                message = service.users().messages().get(
                    userId="me", id=message_id, format="full",
                ).execute()

                headers = {
                    header["name"].lower(): header["value"]
                    for header in message.get("payload", {}).get("headers", [])
                }
                subject = headers.get("subject", "Email invoice")
                sender = headers.get("from", "")
                snippet = message.get("snippet", "")
                payload = message.get("payload", {})
                email_date = _email_date_iso(
                    headers, message.get("internalDate", ""))

                attachments = []

                def collect_attachments(part):
                    filename = part.get("filename", "")
                    mime_type = part.get("mimeType", "")
                    body = part.get("body", {})
                    if filename and (
                        mime_type == "application/pdf"
                        or mime_type.startswith("image/")
                    ):
                        part_headers = {
                            h["name"].lower(): h["value"]
                            for h in part.get("headers", []) or []
                        }
                        disposition = part_headers.get(
                            "content-disposition", "")
                        attachments.append({
                            "filename": filename,
                            "mime_type": mime_type,
                            "attachment_id": body.get("attachmentId"),
                            "inline_data": body.get("data"),
                            "size": body.get("size", 0) or 0,
                            "inline": "inline" in disposition.lower(),
                        })
                    for child in part.get("parts", []) or []:
                        collect_attachments(child)

                collect_attachments(payload)

                filenames = " ".join(
                    attachment["filename"] for attachment in attachments)
                searchable_text = f"{subject} {sender} {snippet} {filenames}"

                if not broader_purchase_terms.search(searchable_text):
                    skipped += 1
                    continue

                # ---- Attachment-less order email -> bill from body ----
                if allow_body and not attachments:
                    source_key = f"gmail:{message_id}:body"
                    if source_key in known_keys:
                        skipped += 1
                        continue
                    if imported >= GMAIL_MAX_NEW_PER_SCAN:
                        stop_scan = True
                        break
                    body_text = _decode_gmail_body(payload)
                    if len(body_text) < 40:
                        skipped += 1
                        continue
                    fields = parse_fields(body_text, "")
                    amount = fields.get("amount")
                    if amount is None:
                        amount = _amount_near_keywords(
                            f"{subject} {snippet}")
                    has_ref = bool(fields.get("invoice_no")
                                   or fields.get("order_no"))
                    strict_hit = bool(strict_purchase_terms.search(
                        f"{subject} {body_text[:3000]}"))
                    if amount is None or not (has_ref or strict_hit):
                        skipped += 1
                        continue
                    purchase_date_value = (
                        fields.get("purchase_date") or email_date)
                    warranty_months = fields.get("warranty_months", 0)
                    warranty_end = fields.get("warranty_end", "") or ""
                    if (not warranty_end and warranty_months
                            and purchase_date_value):
                        try:
                            warranty_end = add_months(
                                date.fromisoformat(purchase_date_value),
                                warranty_months,
                            ).isoformat()
                        except (ValueError, TypeError):
                            warranty_end = ""
                    product = fields.get("product") or ""
                    if (not product or product == "New bill"
                            or len(product) < 4):
                        product = subject[:100] or "Email bill"
                    body_seller = (
                        fields.get("seller")
                        or _gmail_seller_fallback(sender))
                    body_company = enrich_company(body_seller, fields)
                    body_category = categorize_bill(
                        body_seller, product, body_text, fields,
                        owner=email)
                    _bdm = None
                    with db() as _dc:
                        _bdm = _duplicate_match(
                            _dc, email, body_seller, product, amount,
                            purchase_date_value,
                            fields.get("invoice_no", ""),
                            fields.get("order_no", ""))
                    if _bdm and _bdm[0] == "ref":
                        skipped += 1
                        progress(checked, imported, skipped)
                        continue
                    _ba = _assess_review(
                        product, body_seller, amount,
                        purchase_date_value, body_text)
                    if _bdm:
                        _ba = (1, "; ".join([p for p in [
                            _ba[1], _duplicate_reason(_bdm)] if p]))
                    with db() as conn:
                        conn.execute(
                            """
                            INSERT INTO bills (
                                product, seller, purchase_date, amount,
                                warranty_months, warranty_end,
                                warranty_lifetime, category,
                                invoice_no, order_no,
                                cloudinary_url, public_id,
                                original_filename, extracted_text,
                                created_at, owner_email, source_key,
                                logo_url, company_domain, company_about, received_date, needs_review, review_reason
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                product,
                                body_seller,
                                purchase_date_value,
                                amount,
                                warranty_months or None,
                                warranty_end,
                                fields.get("warranty_lifetime", 0),
                                body_category,
                                fields.get("invoice_no", ""),
                                fields.get("order_no", ""),
                                "",
                                "",
                                "Email (no attachment)",
                                body_text,
                                datetime.utcnow().isoformat(timespec="seconds"),
                                email,
                                source_key,
                                body_company["logo_url"],
                                body_company["company_domain"],
                                body_company["company_about"],
                                email_date,
                                _ba[0],
                                _ba[1],
                            ),
                        )
                    known_keys.add(source_key)
                    imported += 1
                    progress(checked, imported, skipped)
                    continue

                if not attachments:
                    skipped += 1
                    continue

                # ---- Attachments -> bills ----
                for attachment in attachments:
                    filename = attachment["filename"]
                    mime_type = attachment["mime_type"]
                    attachment_id = attachment["attachment_id"]
                    raw_data = attachment["inline_data"]

                    # Cheap skips before any download: logo/signature
                    # and tiny inline images are never bills, and OCR
                    # on them is what used to create blank bills.
                    filename_low = filename.lower()
                    if mime_type.startswith("image/"):
                        if any(word in filename_low for word in (
                                "logo", "icon", "banner", "spacer",
                                "pixel", "signature", "tracker")):
                            skipped += 1
                            continue
                        if attachment["size"] and attachment["size"] < 8000:
                            skipped += 1
                            continue
                        if (attachment["inline"] and attachment["size"]
                                and attachment["size"] < 60000):
                            skipped += 1
                            continue

                    source_key = (
                        f"gmail:{message_id}:"
                        f"{attachment_id or filename}"
                    )
                    if source_key in known_keys:
                        skipped += 1
                        continue

                    if imported >= GMAIL_MAX_NEW_PER_SCAN:
                        stop_scan = True
                        break

                    if attachment_id:
                        attachment_result = (
                            service.users()
                            .messages()
                            .attachments()
                            .get(
                                userId="me",
                                messageId=message_id,
                                id=attachment_id,
                            )
                            .execute()
                        )
                        raw_data = attachment_result.get("data", "")

                    if not raw_data:
                        skipped += 1
                        continue

                    try:
                        file_bytes = base64.urlsafe_b64decode(
                            raw_data + "=" * (-len(raw_data) % 4)
                        )
                    except Exception:
                        skipped += 1
                        continue

                    if not file_bytes or len(file_bytes) > 12 * 1024 * 1024:
                        skipped += 1
                        continue

                    suffix = Path(filename).suffix or (
                        ".pdf" if mime_type == "application/pdf" else ".jpg"
                    )

                    try:
                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=suffix,
                        ) as temp_file:
                            temp_file.write(file_bytes)
                            temp_path = temp_file.name

                        # Image bills: the AI reader first (same as a
                        # manual upload, and much faster than OCR);
                        # OCR only when the AI read is unavailable.
                        ai_fields = {}
                        if mime_type in (
                                "image/jpeg", "image/png", "image/webp"):
                            ai_result = ai_read_bill(file_bytes, mime_type)
                            if ai_result:
                                ai_fields = ai_result.get("fields") or {}
                                ai_text = (ai_result.get("text")
                                           or "").strip()
                                if len(ai_text) >= 20:
                                    extracted = ai_text[:20000]
                                else:
                                    extracted = extract_text(
                                        temp_path, mime_type)
                            else:
                                extracted = extract_text(
                                    temp_path, mime_type)
                        else:
                            extracted = extract_text(temp_path, mime_type)

                        if (mime_type == "application/pdf"
                                and len(extracted.strip()) < 40):
                            # Image-only PDF (no text layer): read the
                            # pages with the AI reader instead of OCR.
                            pdf_ai = _ai_read_pdf(file_bytes)
                            if pdf_ai:
                                pdf_text = (pdf_ai.get("text") or "").strip()
                                if len(pdf_text) >= 20:
                                    extracted = pdf_text[:20000]
                                for k, v in (pdf_ai.get("fields") or {}).items():
                                    ai_fields.setdefault(k, v)

                        fields = parse_fields(extracted, filename)
                        for ai_key, ai_value in ai_fields.items():
                            fields[ai_key] = ai_value

                        # Quality gate: a real bill has readable text
                        # AND an amount or an invoice/order number.
                        # Anything else (logos, photos, fragments) is
                        # skipped instead of becoming a blank bill.
                        has_amount = fields.get("amount") is not None
                        has_ref = bool(fields.get("invoice_no")
                                       or fields.get("order_no"))
                        if len(extracted.strip()) < 40 or not (
                                has_amount or has_ref):
                            skipped += 1
                            continue

                        # Fallbacks: never ship a dateless bill when
                        # the email itself is dated, and try the
                        # subject/snippet for a missing amount.
                        gmail_purchase_date = (
                            fields.get("purchase_date") or email_date)
                        gmail_amount = fields.get("amount")
                        if gmail_amount is None:
                            gmail_amount = _amount_near_keywords(
                                f"{subject} {snippet}")
                        gmail_warranty_months = fields.get(
                            "warranty_months", 0)
                        gmail_warranty_end = (
                            fields.get("warranty_end", "") or "")
                        if (not gmail_warranty_end
                                and gmail_warranty_months
                                and gmail_purchase_date):
                            try:
                                gmail_warranty_end = add_months(
                                    date.fromisoformat(gmail_purchase_date),
                                    gmail_warranty_months,
                                ).isoformat()
                            except (ValueError, TypeError):
                                gmail_warranty_end = ""

                        if not fields.get("seller"):
                            fields["seller"] = _gmail_seller_fallback(
                                sender)

                        if (
                            not fields.get("product")
                            or fields["product"].lower()
                            == Path(filename).stem.lower()
                        ):
                            fields["product"] = (
                                subject[:100] or Path(filename).stem
                            )

                        gmail_company = enrich_company(
                            fields.get("seller", ""), fields)
                        gmail_category = categorize_bill(
                            fields.get("seller", ""),
                            fields.get("product") or subject[:100],
                            extracted, fields, owner=email)

                        _gdm = None
                        with db() as _dc:
                            _gdm = _duplicate_match(
                                _dc, email, fields.get("seller", ""),
                                fields.get("product") or subject[:100],
                                gmail_amount, gmail_purchase_date,
                                fields.get("invoice_no", ""),
                                fields.get("order_no", ""))
                        if _gdm and _gdm[0] == "ref":
                            skipped += 1
                            progress(checked, imported, skipped)
                            continue

                        configure_cloudinary()
                        uploaded = cloudinary.uploader.upload(
                            temp_path,
                            folder="billproof/bills",
                            resource_type=("raw" if str(filename).lower().endswith(".pdf") else "auto"),
                            use_filename=True,
                            unique_filename=True,
                            overwrite=False,
                        )

                        _ga = _assess_review(fields.get("product") or subject[:100], fields.get("seller", ""), gmail_amount, gmail_purchase_date, extracted)
                        if _gdm:
                            _ga = (1, "; ".join([p for p in [_ga[1], _duplicate_reason(_gdm)] if p]))
                        with db() as conn:
                            conn.execute(
                                """
                                INSERT INTO bills (
                                    product, seller, purchase_date, amount,
                                    warranty_months, warranty_end,
                                    warranty_lifetime, category,
                                    invoice_no, order_no,
                                    cloudinary_url, public_id,
                                    original_filename, extracted_text,
                                    created_at, owner_email, source_key,
                                    logo_url, company_domain, company_about, received_date, needs_review, review_reason
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    fields.get("product")
                                    or subject[:100]
                                    or "Email bill",
                                    fields.get("seller", "Email merchant"),
                                    gmail_purchase_date,
                                    gmail_amount,
                                    gmail_warranty_months or None,
                                    gmail_warranty_end,
                                    fields.get("warranty_lifetime", 0),
                                    gmail_category,
                                    fields.get("invoice_no", ""),
                                    fields.get("order_no", ""),
                                    uploaded.get("secure_url", ""),
                                    uploaded.get("public_id", ""),
                                    filename,
                                    extracted,
                                    datetime.utcnow().isoformat(
                                        timespec="seconds"),
                                    email,
                                    source_key,
                                    gmail_company["logo_url"],
                                    gmail_company["company_domain"],
                                    gmail_company["company_about"],
                                    email_date,
                                    _ga[0],
                                    _ga[1],
                                ),
                            )
                        known_keys.add(source_key)
                        imported += 1
                        progress(checked, imported, skipped)

                    except sqlite3.IntegrityError:
                        # A concurrent scan may have imported it already.
                        skipped += 1
                    except Exception as exc:
                        skipped += 1
                        errors.append(f"{filename}: {str(exc)[:120]}")
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            os.unlink(temp_path)
                        temp_path = None

                if stop_scan:
                    break

            except Exception as exc:
                skipped += 1
                errors.append(
                    f"Message processing failed: {str(exc)[:120]}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)

        if stop_scan:
            more_remaining = True

        if imported:
            backup_vault()

        return {
            "email": email,
            "messages_checked": checked,
            "imported": imported,
            "skipped": skipped,
            "more_remaining": more_remaining,
            "search_window": "Last 365 days",
            "errors": errors[:5],
            "note": (
                "Imported a batch from your Gmail. Press Import "
                "again to continue - bills already imported are "
                "skipped automatically."
                if more_remaining else
                "Your Gmail scan is complete. Review extracted "
                "details for accuracy."
            ),
        }

    except RefreshError as exc:
        raise HTTPException(
            status_code=401,
            detail="Your Gmail connection expired. Please connect Gmail again.",
        ) from exc
    except HTTPException:
        raise
    except HttpError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail could not be scanned. Check Gmail API access "
                "and reconnect your account if needed."
            ),
        ) from exc


def _gmail_scan_worker(email):
    """Background thread body: run the scan, record progress + result
    in the job store for /api/gmail/scan/status to serve."""
    def report(checked, imported, skipped):
        _gmail_job_update(
            email, checked=checked, imported=imported, skipped=skipped)

    try:
        result = _perform_gmail_scan(email, report)
        _gmail_job_update(
            email,
            state="done",
            checked=result["messages_checked"],
            imported=result["imported"],
            skipped=result["skipped"],
            more_remaining=result["more_remaining"],
            note=result["note"],
            errors=result.get("errors", []),
            finished_at=datetime.utcnow().isoformat(timespec="seconds"),
        )
    except HTTPException as exc:
        _gmail_job_update(
            email,
            state="error",
            detail=str(exc.detail),
            status_code=exc.status_code,
            finished_at=datetime.utcnow().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        _gmail_job_update(
            email,
            state="error",
            detail=f"Gmail scan failed: {str(exc)[:200]}",
            status_code=500,
            finished_at=datetime.utcnow().isoformat(timespec="seconds"),
        )


@app.post("/api/gmail/scan")
def scan_gmail(request: Request):
    """Start a Gmail scan in the background and return at once.

    The scan itself can take minutes on a large inbox; the website
    follows it through /api/gmail/scan/status instead of holding one
    request open (which is why scans used to look frozen and only
    showed results after a manual refresh)."""
    email = current_owner(request)
    if email == "local-demo":
        raise HTTPException(
            status_code=401,
            detail="Click Connect Gmail and sign in first.",
        )
    get_gmail_credentials(email)  # raises 401 when not connected
    snapshot = _gmail_job_snapshot(email)
    if snapshot["state"] == "running":
        return {"started": False, "already_running": True, **snapshot}
    _gmail_job_update(
        email,
        state="running", checked=0, imported=0, skipped=0,
        more_remaining=False, note="", detail="", status_code=0,
        errors=[],
        started_at=datetime.utcnow().isoformat(timespec="seconds"),
        finished_at="",
    )
    threading.Thread(
        target=_gmail_scan_worker, args=(email,), daemon=True,
    ).start()
    return {"started": True, "already_running": False,
            **_gmail_job_snapshot(email)}


@app.get("/api/gmail/scan/status")
def gmail_scan_status(request: Request):
    """Live progress of the signed-in user's Gmail scan, if any."""
    email = current_owner(request)
    if email == "local-demo":
        return {
            "state": "idle", "checked": 0, "imported": 0, "skipped": 0,
            "more_remaining": False, "note": "", "detail": "",
            "status_code": 0, "errors": [], "started_at": "",
            "finished_at": "",
        }
    return _gmail_job_snapshot(email)


# --------------------------------------------------

# HEALTH CHECK

# --------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server error: {exc.__class__.__name__}: {str(exc)[:160]}"},
    )

_probe_cache = {"at": 0.0, "value": None}

def _probe_stage(cloud_name, api_key, api_secret, rtype, public_id):
    try:
        meta = httpx.get(
            f"https://api.cloudinary.com/v1_1/{cloud_name}"
            f"/resources/{rtype}/upload/{public_id}",
            auth=(api_key, api_secret), timeout=30)
        if meta.status_code != 200:
            return f"lookup HTTP {meta.status_code}"
        url = meta.json().get("secure_url")
        if not url:
            return "lookup ok, no file address"
        r = httpx.get(url, timeout=30, follow_redirects=True)
        return f"lookup 200, download HTTP {r.status_code}"
    except Exception as exc:
        return f"{exc.__class__.__name__}: {str(exc)[:80]}"

def _file_probe():
    """Server-side test of the exact path the bill-file endpoint
    uses (Admin API lookup, then download the file address).
    Status codes only — safe on the public health check. Cached
    for five minutes."""
    import time as _time
    now = _time.time()
    if _probe_cache["value"] is not None and now - _probe_cache["at"] < 300:
        return _probe_cache["value"]
    probe = {"raw_backup": "not tried", "image_bill": "not tried"}
    if all(_cloudinary_env()):
        try:
            configure_cloudinary()
            cname, ckey, csecret = _cloudinary_env()
            probe["raw_backup"] = _probe_stage(
                cname, ckey, csecret, "raw", VAULT_BACKUP_PUBLIC_ID)
            with db() as conn:
                row = conn.execute(
                    "SELECT public_id FROM bills "
                    "WHERE public_id != '' AND cloudinary_url LIKE '%/image/upload/%' "
                    "ORDER BY id LIMIT 1").fetchone()
            probe["image_bill"] = (
                _probe_stage(cname, ckey, csecret, "image", row["public_id"])
                if row else "no image bill found")
        except Exception as exc:
            probe["error"] = f"{exc.__class__.__name__}: {str(exc)[:80]}"
    _probe_cache.update(at=now, value=probe)
    return probe

@app.get("/api/health")
def health():

    return {

        "ok": True,

        "service": "Raseed API",

        "ocr": ocr_status(),

        "vault": {

            "cloud_backup_configured": all(_cloudinary_env()),

            "restored_from_backup": vault_restored_from_backup,

        },

        "ai_reader": {

            "configured": bool(_gemini_api_key()),

            "last_status": _ai_reader_state["last_status"],

            "last_model": _ai_reader_state["last_model"],

            "last_error": _ai_reader_state["last_error"],

        },

        "auth": {

            "google_configured": google_oauth_configured(),

        },

        "file_probe": _file_probe(),

    }

# --------------------------------------------------

# LIST BILLS

# --------------------------------------------------


@app.get("/api/bills/{bill_id}/file")
def bill_file(bill_id: int, request: Request):
    """Stream the bill's original file through Raseed itself.

    Cloudinary delivery of the stored PDFs is blocked on this
    account (401 "deny or ACL failure") — even signed delivery URLs
    are refused — so the frontend never links to Cloudinary. The
    backend downloads the asset with a signed API call, which the
    account accepts, and serves the bytes itself.
    """
    owner = current_owner(request)
    if guest_blocked(owner):
        raise HTTPException(status_code=404, detail="Bill not found")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM bills WHERE id = ? AND owner_email = ?",
            (bill_id, owner),
        ).fetchone()
        if not row or not row["public_id"]:
            raise HTTPException(
                status_code=404, detail="No file saved for this bill")
        public_id = row["public_id"]
        stored_url = row["cloudinary_url"] or ""
        filename = row["original_filename"] or "bill"
    configure_cloudinary()
    cloud_name, api_key, api_secret = _cloudinary_env()
    rtype = "raw" if "/raw/upload/" in stored_url else "image"
    try:
        meta = httpx.get(
            f"https://api.cloudinary.com/v1_1/{cloud_name}"
            f"/resources/{rtype}/upload/{public_id}",
            auth=(api_key, api_secret), timeout=30)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Storage lookup error: {str(exc)[:120]}")
    if meta.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=(f"Storage lookup failed ({meta.status_code}): "
                    f"{meta.text[:120]}"))
    secure_url = meta.json().get("secure_url")
    if not secure_url:
        raise HTTPException(
            status_code=502, detail="Storage returned no file address")
    try:
        fetched = httpx.get(
            secure_url, timeout=45, follow_redirects=True)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"File download error: {str(exc)[:120]}")
    if fetched.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"File download failed ({fetched.status_code})")
    media_type = (
        "application/pdf"
        if filename.lower().endswith(".pdf")
        else fetched.headers.get("content-type") or "application/octet-stream"
    )
    safe_name = filename.replace('"', "")
    return Response(
        content=fetched.content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )

@app.get("/api/bills")

def list_bills(

    request: Request,

    q: str = "",

    status: str = "All Documents",

):

    owner = current_owner(request)

    if guest_blocked(owner):

        return []

    with db() as conn:

        rows = conn.execute(

            """

            SELECT * FROM bills

            WHERE owner_email=?

            ORDER BY COALESCE(NULLIF(received_date, ''), substr(created_at, 1, 10)) DESC, created_at DESC, id DESC

            """,

            (owner,),

        ).fetchall()

    bills = [row_to_dict(row) for row in rows]

    # Bills are ARRANGED by arrival date (received_date): the
    # day a scanned/uploaded bill was added, or the day a Gmail
    # bill's email arrived — never by purchase_date, which is
    # display-only. row_to_dict computes the effective
    # received_date at read time, so re-sort by it here.
    bills.sort(

        key=lambda bill: (

            bill.get("received_date") or str(bill.get("created_at") or "")[:10],

            str(bill.get("created_at") or ""),

            bill.get("id") or 0,

        ),

        reverse=True,

    )

    if q.strip():

        needle = q.strip().lower()

        bills = [

            bill for bill in bills

            if needle in " ".join([

                str(bill.get("product", "")),

                str(bill.get("seller", "")),

                str(bill.get("category", "")),

                str(bill.get("original_filename", "")),

            ]).lower()

        ]

    if status == "Active Warranties":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "Active"

        ]

    elif status == "Expiring Soon":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "Expiring Soon"

        ]

    elif status == "No Warranty":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "No Warranty"

        ]

    elif status == "Expired":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "Expired"

        ]

    elif status == "Needs Review":

        bills = [

            bill for bill in bills

            if bill.get("needs_review")

        ]

    elif status == "Uploaded Bills":

        bills = [

            bill for bill in bills

            if bill.get("source") != "gmail"

        ]

    elif status == "Mail Bills":

        bills = [

            bill for bill in bills

            if bill.get("source") == "gmail"

        ]

    return bills

# --------------------------------------------------

# MANUAL BILL UPLOAD

# --------------------------------------------------

@app.post("/api/bills")

async def upload_bill(

    request: Request,

    file: UploadFile = File(...),

    warranty_months: int = Form(0),

    category: str = Form("Other"),

    product_override: str = Form(""),

    seller_override: str = Form(""),

    purchase_date_override: str = Form(""),

    amount_override: str = Form(""),

    extracted_text_override: str = Form(""),

):

    if guest_blocked(current_owner(request)):

        raise HTTPException(

            status_code=401,

            detail="Please login first to add bills.",

        )

    allowed_types = {

        "image/jpeg",

        "image/png",

        "image/webp",

        "application/pdf",

    }

    if file.content_type not in allowed_types:

        raise HTTPException(

            status_code=415,

            detail="Upload a JPG, PNG, WEBP, or PDF invoice.",

        )

    if warranty_months < 0 or warranty_months > 120:

        raise HTTPException(

            status_code=400,

            detail="Warranty must be between 0 and 120 months.",

        )

    data = await file.read()

    if len(data) > 12 * 1024 * 1024:

        raise HTTPException(

            status_code=413,

            detail="Maximum file size is 12 MB.",

        )

    filename = file.filename or "bill"

    suffix = Path(filename).suffix or ".bin"

    temp_path = None

    try:

        with tempfile.NamedTemporaryFile(

            delete=False,

            suffix=suffix,

        ) as temp_file:

            temp_file.write(data)

            temp_path = temp_file.name

        text = extract_text(temp_path, file.content_type or "")

        # OCR fallback: the browser can run tesseract.js and send the text it
        # read. Use it when the server extracted less (usually because
        # Tesseract is not installed on the host, e.g. a Render service that
        # was not started from the Docker image).
        client_text = (extracted_text_override or "").strip()
        if len(client_text) >= 20 and len(client_text) > len(text.strip()):
            print("BILL EXTRACTION: using client-side OCR text,",
                  len(client_text), "characters (server produced",
                  len(text.strip()), ")")
            text = client_text[:20000]

        # AI reader: for photo uploads, a vision model reads the
        # handwriting that print OCR misses. Its transcription becomes
        # the bill text and its explicit fields win over regex guesses.
        ai_fields = {}
        if (file.content_type or "").startswith("image/"):
            ai_result = ai_read_bill(data, file.content_type or "")
            if ai_result:
                ai_text = (ai_result.get("text") or "").strip()
                if len(ai_text) >= 20:
                    print("BILL EXTRACTION: using AI reader text,",
                          len(ai_text), "characters")
                    text = ai_text[:20000]
                ai_fields = ai_result.get("fields") or {}

        if (file.content_type or "") == "application/pdf" and len(text.strip()) < 40:
            pdf_ai = _ai_read_pdf(data)
            if pdf_ai:
                pdf_text = (pdf_ai.get("text") or "").strip()
                if len(pdf_text) >= 20:
                    text = pdf_text[:20000]
                for k, v in (pdf_ai.get("fields") or {}).items():
                    ai_fields.setdefault(k, v)

        fields = parse_fields(text, filename)
        for ai_key, ai_value in ai_fields.items():
            fields[ai_key] = ai_value

        print(
            "BILL EXTRACTION DEBUG:",
            {
                "file": filename,
                "text_characters": len(text),
                "product_found": bool(fields.get("product")),
                "seller_found": bool(fields.get("seller")),
                "purchase_date_found": bool(fields.get("purchase_date")),
                "amount_found": fields.get("amount") is not None,
                "warranty_months": fields.get("warranty_months", 0),
            },
        )

        # Use an explicitly supplied warranty duration; otherwise use OCR.
        if warranty_months <= 0:
            warranty_months = fields.get("warranty_months", 0)

        product = product_override.strip() or fields["product"]

        seller = seller_override.strip() or fields["seller"]

        purchase_date = (

            purchase_date_override.strip()

            or fields["purchase_date"]

        )

        try:

            amount = (

                float(amount_override)

                if amount_override.strip()

                else fields["amount"]

            )

        except ValueError:

            amount = fields["amount"]

        configure_cloudinary()

        uploaded = cloudinary.uploader.upload(

            temp_path,

            folder="billproof/bills",

            resource_type=("raw" if str(filename).lower().endswith(".pdf") else "auto"),

            use_filename=True,

            unique_filename=True,

            overwrite=False,

            context={"original_filename": filename},

        )

        company = enrich_company(seller, fields)

        # Category: an explicit non-"Other" pick in the upload form
        # wins. The form defaults to "Other", which means "not
        # chosen" — infer the category automatically in that case
        # (the AI reader's own category, if any, is used first).
        chosen_category = (category or "").strip()
        if chosen_category != "Other" \
                and chosen_category in BILL_CATEGORIES:
            bill_category = chosen_category
        else:
            bill_category = categorize_bill(seller, product, text, fields, owner=current_owner(request))

        # Prefer an expiry date printed on the invoice, otherwise calculate it.
        warranty_end = fields.get("warranty_end", "") or ""
        if not warranty_end and warranty_months and purchase_date:
            try:
                warranty_end = add_months(
                    date.fromisoformat(purchase_date),
                    warranty_months,
                ).isoformat()
            except (ValueError, TypeError):
                warranty_end = ""

        with db() as conn:

            _ur = _assess_review(product, seller, amount, purchase_date, text)

            _dup = _duplicate_problem(

                conn, current_owner(request), seller, product,

                amount, purchase_date,

                fields.get("invoice_no", ""), fields.get("order_no", ""))

            if _dup:

                _ur = (1, "; ".join([p for p in [_ur[1], _dup] if p]))

            cursor = conn.execute(

                """

                INSERT INTO bills (

                    product, seller, purchase_date, amount,

                    warranty_months, warranty_end, warranty_lifetime, category,

                    invoice_no, order_no,

                    cloudinary_url, public_id, original_filename,

                    extracted_text, created_at, owner_email,

                    logo_url, company_domain, company_about, received_date, needs_review, review_reason

                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                """,

                (

                    product,

                    seller,

                    purchase_date,

                    amount,

                    warranty_months or None,

                    warranty_end,

                    fields.get("warranty_lifetime", 0),

                    bill_category,

                    fields.get("invoice_no", ""),

                    fields.get("order_no", ""),

                    uploaded.get("secure_url", ""),

                    uploaded.get("public_id", ""),

                    filename,

                    text,

                    datetime.utcnow().isoformat(timespec="seconds"),

                    current_owner(request),

                    company["logo_url"],

                    company["company_domain"],

                    company["company_about"],

                    datetime.utcnow().date().isoformat(),

                    _ur[0],

                    _ur[1],

                ),

            )

            bill_id = cursor.lastrowid

            row = conn.execute(

                "SELECT * FROM bills WHERE id=?",

                (bill_id,),

            ).fetchone()

        backup_vault()

        return row_to_dict(row)

    except HTTPException:

        raise

    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=f"Could not upload bill: {str(exc)[:240]}",

        ) from exc

    finally:

        if temp_path and os.path.exists(temp_path):

            os.unlink(temp_path)

# --------------------------------------------------

# EDIT BILL

# --------------------------------------------------

@app.patch("/api/bills/{bill_id}")

def update_bill(

    bill_id: int,

    payload: BillUpdate,

    request: Request,

):

    updates = payload.model_dump(exclude_unset=True)

    owner = current_owner(request)

    if guest_blocked(owner):

        raise HTTPException(

            status_code=401,

            detail="Please login first.",

        )

    learn_pair = None

    with db() as conn:

        old = conn.execute(

            """

            SELECT * FROM bills

            WHERE id=? AND owner_email=?

            """,

            (bill_id, owner),

        ).fetchone()

        if not old:

            raise HTTPException(

                status_code=404,

                detail="Bill not found.",

            )

        values = dict(old)

        values.update({

            key: value

            for key, value in updates.items()

            if value is not None

        })

        warranty_end = ""

        if (

            values.get("warranty_months")

            and values.get("purchase_date")

        ):

            try:

                warranty_end = add_months(

                    date.fromisoformat(values["purchase_date"]),

                    int(values["warranty_months"]),

                ).isoformat()

            except (ValueError, TypeError):

                warranty_end = ""

        # If the seller changed, the old company logo/about no longer
        # matches — recompute enrichment for the new seller and store
        # it (blanking it when the new seller is unknown).
        if "seller" in updates:

            patch_company = enrich_company(values.get("seller") or "")

        else:

            patch_company = {

                "logo_url": values.get("logo_url") or "",

                "company_domain": values.get("company_domain") or "",

                "company_about": values.get("company_about") or "",

            }

        # Category: a category explicitly included in the payload is
        # respected (already merged into values above). If the seller
        # or product changed without one, re-run inference on the new
        # details so the bill does not keep a stale category.
        if "category" not in updates and (

                "seller" in updates or "product" in updates):

            values["category"] = categorize_bill(

                values.get("seller") or "",

                values.get("product") or "",

                values.get("extracted_text") or "",

                owner=owner,

            )

        # Agent v1 — remember a manual category correction as a
        # personal rule for this seller (saved after this update).
        if "category" in updates and updates.get("category") \
                and str(updates.get("category")) != str(old["category"] or ""):

            learn_pair = (

                values.get("seller") or "",

                str(updates["category"]),

            )

        # Agent v1 — needs-review verdict: a PATCH carrying ONLY
        # needs_review=0 is the owner pressing "Mark as correct" and
        # clears the flag; any other edit re-assesses the bill from
        # its final values.
        if set(updates.keys()) == {"needs_review"} \
                and updates.get("needs_review") == 0:

            values["needs_review"] = 0

            values["review_reason"] = ""

        else:

            _problems = _review_problems(

                values.get("product"),

                values.get("seller"),

                values.get("amount"),

                values.get("purchase_date"),

                values.get("extracted_text"),

            )

            _dup_reason = _duplicate_problem(

                conn, owner, values.get("seller"),

                values.get("product"), values.get("amount"),

                values.get("purchase_date"),

                values.get("invoice_no", ""),

                values.get("order_no", ""),

                exclude_id=bill_id,

            )

            if _dup_reason and _dup_reason not in _problems:

                _problems = _problems + [_dup_reason]

            values["needs_review"] = 1 if _problems else 0

            values["review_reason"] = "; ".join(_problems)

        conn.execute(

            """

            UPDATE bills

            SET product=?, seller=?, purchase_date=?, amount=?,

                warranty_months=?, warranty_end=?, category=?,

                invoice_no=?, order_no=?, warranty_lifetime=?,

                logo_url=?, company_domain=?, company_about=?,

                needs_review=?, review_reason=?

            WHERE id=?

            """,

            (

                values["product"],

                values["seller"],

                values["purchase_date"],

                values["amount"],

                values["warranty_months"],

                warranty_end,

                values["category"],

                values.get("invoice_no") or "",

                values.get("order_no") or "",

                values.get("warranty_lifetime") or 0,

                patch_company["logo_url"],

                patch_company["company_domain"],

                patch_company["company_about"],

                values.get("needs_review") or 0,

                values.get("review_reason") or "",

                bill_id,

            ),

        )

        row = conn.execute(

            "SELECT * FROM bills WHERE id=?",

            (bill_id,),

        ).fetchone()

    if learn_pair:

        _save_learned_category(owner, learn_pair[0], learn_pair[1])

    backup_vault()

    return row_to_dict(row)

# --------------------------------------------------

# DELETE BILL

# --------------------------------------------------

@app.delete("/api/bills/{bill_id}")

def delete_bill(

    bill_id: int,

    request: Request,

):

    owner = current_owner(request)

    if guest_blocked(owner):

        raise HTTPException(

            status_code=401,

            detail="Please login first.",

        )

    with db() as conn:

        row = conn.execute(

            """

            SELECT * FROM bills

            WHERE id=? AND owner_email=?

            """,

            (bill_id, owner),

        ).fetchone()

        if not row:

            raise HTTPException(

                status_code=404,

                detail="Bill not found.",

            )

        if row["public_id"]:

            try:

                configure_cloudinary()

                _types = (

                    ["image", "raw"]

                    if row["original_filename"].lower().endswith(".pdf")

                    else ["image"]

                )

                for _rtype in _types:

                    cloudinary.uploader.destroy(

                        row["public_id"],

                        resource_type=_rtype,

                    )

            except Exception:

                pass

        conn.execute(

            "DELETE FROM bills WHERE id=?",

            (bill_id,),

        )

    backup_vault()

    return {"deleted": True}

# --------------------------------------------------

# AUTOMATIC GMAIL MONITORING

# --------------------------------------------------


# --------------------------------------------------
# AGENT V1 — ASK RASEED
#
# One entry point the frontend chat box calls. The agent picks a
# tool from the message, runs it against the owner's own vault,
# and answers in plain words, returning the bills it used so the
# UI can show them. Tools in v1: search the vault, spending
# summary, needs-review list, check-all (re-assess every bill and
# apply learned category rules), expiring warranties, duplicate
# finder, vault totals. Destructive tools (delete, merge) are
# deliberately NOT here — those stay ask-first buttons in the UI.
# --------------------------------------------------

class AgentAsk(BaseModel):
    message: str = ""
    history: list = []


def _agent_money(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount == int(amount):
        return "Rs " + f"{int(amount):,}"
    return "Rs " + f"{amount:,.2f}"


def _agent_bills(owner):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bills WHERE owner_email=?",
            (owner,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


_AGENT_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3,
    "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11,
    "nov": 11, "december": 12, "dec": 12,
}


def _agent_tool_search(bills, message, query=None):
    stopwords = {
        "find", "show", "search", "list", "bill", "bills", "my",
        "the", "a", "an", "for", "me", "all", "of", "in", "on",
        "please", "raseed", "get", "give",
    }
    tokens = [
        token for token in re.findall(
            r"[a-z0-9]+", (query or message).lower())
        if len(token) >= 3 and token not in stopwords
    ]
    if not tokens:
        return None
    matches = []
    for bill in bills:
        haystack = " ".join([
            str(bill.get("product") or ""),
            str(bill.get("seller") or ""),
            str(bill.get("category") or ""),
            str(bill.get("invoice_no") or ""),
            str(bill.get("order_no") or ""),
            str(bill.get("original_filename") or ""),
        ]).lower()
        if any(token in haystack for token in tokens):
            matches.append(bill)
    total = sum(bill.get("amount") or 0 for bill in matches)
    if matches:
        answer = (
            f"Found {len(matches)} bill(s) matching your search, "
            f"totalling {_agent_money(total)}."
        )
    else:
        answer = "I could not find any bill matching that."
    return answer, matches[:200]


def _agent_tool_spending(bills, message, category=None,
                         month_num=None):
    lowered = message.lower()
    if category not in BILL_CATEGORIES:
        category = ""
        for candidate in BILL_CATEGORIES:
            if candidate.lower() in lowered:
                category = candidate
                break
    if not category:
        synonyms = {
            "hostel": "Education", "fees": "Education",
            "fee": "Education", "course": "Education",
            "food": "Food", "eat": "Food", "restaurant": "Food",
            "movie": "Entertainment", "groceries": "Food",
        }
        for word, mapped in synonyms.items():
            if re.search(r"\b" + word + r"\b", lowered):
                category = mapped
                break
    month_name = ""
    if month_num:
        names = [k for k, v in _AGENT_MONTHS.items()
                 if v == month_num and len(k) > 3]
        month_name = names[0].capitalize() if names else ""
    else:
        for name, num in _AGENT_MONTHS.items():
            if re.search(r"\b" + name + r"\b", lowered):
                month_num, month_name = num, name.capitalize()
                break
        if "this month" in lowered:
            today = datetime.utcnow().date()
            month_num, month_name = today.month, "this month"
    selected = bills
    if category:
        selected = [
            bill for bill in selected
            if (bill.get("category") or "") == category
        ]
    if month_num:
        selected = [
            bill for bill in selected
            if str(bill.get("purchase_date") or "")[5:7]
            == f"{month_num:02d}"
        ]
    total = sum(bill.get("amount") or 0 for bill in selected)
    scope = " ".join(part for part in [
        category, ("in " + month_name) if month_name else "",
    ] if part).strip()
    answer = (
        f"You spent {_agent_money(total)}"
        + (f" on {scope}" if scope else " in total")
        + f" across {len(selected)} bill(s)."
    )
    ranked = sorted(
        selected, key=lambda bill: bill.get("amount") or 0,
        reverse=True)
    return answer, ranked[:200]


def _agent_tool_review_list(bills):
    flagged = [bill for bill in bills if bill.get("needs_review")]
    if not flagged:
        return (
            "Good news — no bill in your vault needs review. "
            "Every bill has an amount, a date and a seller, and "
            "the totals match.",
            [],
        )
    lines = [
        f"{len(flagged)} bill(s) need your review:"
    ]
    for bill in flagged[:5]:
        lines.append(
            "- " + (bill.get("product") or "Bill")
            + ": " + (bill.get("review_reason") or "")
        )
    if len(flagged) > 5:
        lines.append(f"...and {len(flagged) - 5} more.")
    lines.append(
        "Open a bill and fix the detail, or press Mark as correct "
        "if it is actually right."
    )
    return "\n".join(lines), flagged[:200]


def _agent_tool_check_all(owner):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bills WHERE owner_email=?",
            (owner,),
        ).fetchall()
        changed = 0
        recategorised = 0
        flagged = 0
        _dup_ids = {
            _b["id"] for _g in _duplicate_groups(
                [dict(_r) for _r in rows]) for _b in _g}
        for row in rows:
            data = dict(row)
            problems = _review_problems(
                data.get("product"), data.get("seller"),
                data.get("amount"), data.get("purchase_date"),
                data.get("extracted_text"),
            )
            needs_review = 1 if problems else 0
            reason = "; ".join(problems)
            if data["id"] in _dup_ids:
                problems = problems + [
                    "Possible duplicate: another bill in your "
                    "vault looks like the same purchase"]
                needs_review = 1
                reason = "; ".join(problems)
            learned = _learned_category(owner, data.get("seller"))
            new_category = data.get("category") or "Other"
            if learned and learned != new_category:
                new_category = learned
                recategorised += 1
            if (data.get("needs_review") != needs_review
                    or (data.get("review_reason") or "") != reason
                    or new_category != (data.get("category") or "")):
                conn.execute(
                    "UPDATE bills SET needs_review=?,"
                    " review_reason=?, category=?"
                    " WHERE id=?",
                    (needs_review, reason, new_category, data["id"]),
                )
                changed += 1
            if needs_review:
                flagged += 1
    if changed:
        backup_vault()
    answer = (
        f"I checked all {len(rows)} bill(s) in your vault. "
        f"{flagged} need(s) review"
        + (f", and I fixed the category of {recategorised} "
           "bill(s) using the rules you taught me."
           if recategorised else ".")
    )
    bills = _agent_bills(owner)
    return answer, [b for b in bills if b.get("needs_review")][:200]


def _agent_tool_warranties(bills, message, days=None):
    lowered = message.lower()
    if not days:
        days_match = re.search(r"(\d+)\s*day", lowered)
        days = int(days_match.group(1)) if days_match else 30
    today = datetime.utcnow().date()
    expiring = []
    for bill in bills:
        if bill.get("warranty_lifetime"):
            continue
        end_text = bill.get("warranty_end") or ""
        try:
            end = date.fromisoformat(end_text)
        except (ValueError, TypeError):
            continue
        left = (end - today).days
        if 0 <= left <= days:
            expiring.append((left, bill))
    expiring.sort(key=lambda pair: pair[0])
    if not expiring:
        return (
            f"No warranties are expiring in the next {days} days.",
            [],
        )
    lines = [f"{len(expiring)} warranty(ies) expiring soon:"]
    for left, bill in expiring[:5]:
        lines.append(
            "- " + (bill.get("product") or "Bill")
            + f": {left} day(s) left (ends "
            + (bill.get("warranty_end") or "") + ")"
        )
    return "\n".join(lines), [bill for _, bill in expiring[:200]]


def _dup_product_key(bill):
    return re.sub(
        r"[^a-z0-9]+", " ",
        str(bill.get("product") or "").lower()).strip()[:40]


def _duplicate_groups(bills):
    """Groups of bills that look like the same purchase, strict
    enough not to swallow legitimate repeat purchases:

    - Ref groups: bills sharing a normalised invoice/order number
      (length >= 4), unioned across both number fields.
    - Field groups (bills not already in a ref group): same
      company + amount + purchase date + item, and the purchase
      date must be real — dateless bills never group. A field
      group carrying two different invoice numbers is split,
      because different invoice numbers prove distinct purchases
      (e.g. same-fare Rapido rides)."""
    parent = list(range(len(bills)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_ref = {}
    for i, bill in enumerate(bills):
        for raw in (bill.get("invoice_no"), bill.get("order_no")):
            ref = _norm_ref(raw)
            if len(ref) >= 4:
                by_ref.setdefault(ref, []).append(i)
    for idxs in by_ref.values():
        for j in idxs[1:]:
            union(idxs[0], j)
    sets = {}
    for i in range(len(bills)):
        sets.setdefault(find(i), []).append(i)
    groups = []
    grouped_ids = set()
    for idxs in sets.values():
        if len(idxs) > 1:
            groups.append([bills[i] for i in idxs])
            grouped_ids.update(id(bills[i]) for i in idxs)

    field_groups = {}
    for bill in bills:
        if id(bill) in grouped_ids:
            continue
        purchase_date = str(bill.get("purchase_date") or "")
        if not purchase_date or not bill.get("amount"):
            continue
        try:
            amount_key = round(float(bill["amount"]), 2)
        except (TypeError, ValueError):
            continue
        key = (
            _seller_key(bill.get("seller") or bill.get("product")
                        or ""),
            amount_key,
            purchase_date,
            _dup_product_key(bill),
        )
        if not key[0] or not key[3]:
            continue
        field_groups.setdefault(key, []).append(bill)
    for group in field_groups.values():
        if len(group) < 2:
            continue
        inv_values = {
            _norm_ref(b.get("invoice_no")) for b in group} - {""}
        if len(inv_values) > 1:
            buckets = {}
            for b in group:
                buckets.setdefault(
                    _norm_ref(b.get("invoice_no")), []).append(b)
            for value, members in buckets.items():
                if len(members) > 1:
                    groups.append(members)
        else:
            groups.append(group)
    return groups


def _agent_tool_duplicates(bills):
    duplicates = _duplicate_groups(bills)
    if not duplicates:
        return "I found no duplicate bills in your vault.", []
    flat = [bill for group in duplicates for bill in group]
    answer = (
        f"I found {len(duplicates)} possible duplicate set(s) "
        f"covering {len(flat)} bills. A set means bills sharing "
        "an invoice/order number, or the same company, amount, "
        "date and item. Bills with different invoice numbers are "
        "never grouped, so repeat purchases like same-fare rides "
        "stay separate. I have not deleted anything — open a bill "
        "and delete the extra copy yourself if it really is a "
        "duplicate."
    )
    return answer, flat[:200]




def _gemini_text_json(prompt):
    """One text-only Gemini call expecting a JSON object back.

    Same best-effort pattern as ai_classify_category: any failure
    (no key, timeout, odd reply) returns {} and the caller falls
    back to deterministic behaviour.
    """
    key = _gemini_api_key()
    if not key:
        return {}
    base = os.getenv(
        "GEMINI_API_BASE",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")
    models = []
    for candidate in [os.getenv("GEMINI_MODEL", "")] + list(
            AI_READER_MODELS):
        if candidate and candidate not in models:
            models.append(candidate)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    for model in models[:2]:
        try:
            with httpx.Client(timeout=15.0, trust_env=False) as client:
                response = client.post(
                    f"{base}/models/{model}:generateContent",
                    headers={
                        "x-goog-api-key": key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if response.status_code != 200:
                continue
            body = response.json()
            parts = (
                body.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            text = "".join(
                part.get("text", "") for part in parts).strip()
            parsed = _parse_ai_json(text)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except Exception:
            continue
    return {}


_AGENT_TOOL_NAMES = {
    "search", "spending", "breakdown", "biggest", "recent",
    "seller_total", "explain", "review_list", "check_all",
    "warranties", "duplicates", "count", "learned_list",
    "learned_forget",
}


def _agent_plan(message, history, bills):
    """Ask Gemini which tool fits this message, with parameters.

    Returns a validated plan dict, or None when Gemini is not
    available or its answer cannot be trusted — the deterministic
    router then decides. Gemini never sees or invents amounts:
    tools compute every number from the vault itself.
    """
    sellers = []
    for bill in bills:
        seller = str(bill.get("seller") or "").strip()
        if seller and seller not in sellers:
            sellers.append(seller)
    flagged = sum(1 for bill in bills if bill.get("needs_review"))
    history_lines = []
    for item in (history or [])[-3:]:
        if isinstance(item, dict):
            q = str(item.get("question") or "")[:200]
            a = str(item.get("answer") or "")[:300]
            if q:
                history_lines.append(f"User asked: {q}\nAgent said: {a}")
    prompt = (
        "You route questions for Raseed, a personal bill vault, to "
        "one tool. Reply with ONLY a JSON object like "
        "{\"tool\": \"spending\", \"category\": \"Food\", "
        "\"month\": 10, \"seller\": null, \"query\": null, "
        "\"days\": null}.\n"
        "Tools: search (find bills matching words; put the words "
        "in query), spending (total spent; optional category and "
        "month number), breakdown (totals per category), biggest "
        "(largest bills), recent (latest bills), seller_total "
        "(total at one company; put its name in seller), explain "
        "(details of one bill; put identifying words in query), "
        "review_list (bills flagged needs review), check_all "
        "(re-check every bill), warranties (expiring warranties; "
        "days = window, default 30), duplicates, count (how many "
        "bills / grand total), learned_list (rules the agent "
        "learned from the user's corrections), learned_forget "
        "(forget one learned rule; seller = the company).\n"
        "Categories: " + ", ".join(BILL_CATEGORIES) + ".\n"
        "Companies in this vault: "
        + (", ".join(sellers[:25]) if sellers else "none") + ".\n"
        f"The vault has {len(bills)} bills, {flagged} flagged "
        "needs review. Today is "
        + datetime.utcnow().date().isoformat() + ".\n"
        + ("Recent conversation:\n" + "\n".join(history_lines) + "\n"
           if history_lines else "")
        + "User's new message: " + str(message)[:500]
    )
    plan = _gemini_text_json(prompt)
    if not isinstance(plan, dict):
        return None
    tool = plan.get("tool")
    if tool not in _AGENT_TOOL_NAMES:
        return None
    clean = {"tool": tool}
    category = plan.get("category")
    if category in BILL_CATEGORIES:
        clean["category"] = category
    month = plan.get("month")
    if isinstance(month, int) and 1 <= month <= 12:
        clean["month"] = month
    days = plan.get("days")
    if isinstance(days, int) and 1 <= days <= 3650:
        clean["days"] = days
    seller = plan.get("seller")
    if isinstance(seller, str) and seller.strip():
        for known in sellers:
            if seller.strip().lower() in known.lower() \
                    or known.lower() in seller.strip().lower():
                clean["seller"] = known
                break
    query = plan.get("query")
    if isinstance(query, str) and query.strip():
        clean["query"] = query.strip()[:80]
    return clean


def _agent_tool_breakdown(bills):
    by_category = {}
    for bill in bills:
        category = bill.get("category") or "Other"
        entry = by_category.setdefault(
            category, {"total": 0.0, "count": 0})
        entry["total"] += bill.get("amount") or 0
        entry["count"] += 1
    if not by_category:
        return "Your vault is empty, so there is no breakdown yet.", []
    ordered = sorted(
        by_category.items(),
        key=lambda pair: pair[1]["total"], reverse=True)
    grand = sum(entry["total"] for _, entry in ordered)
    lines = [f"Your spending by category (total {_agent_money(grand)}):"]
    for category, entry in ordered:
        pct = (entry["total"] / grand * 100) if grand else 0
        lines.append(
            f"- {category}: {_agent_money(entry['total'])} "
            f"({entry['count']} bill(s), {pct:.0f}%)"
        )
    return "\n".join(lines), []


def _agent_tool_biggest(bills, count=5):
    ranked = sorted(
        (bill for bill in bills if bill.get("amount")),
        key=lambda bill: bill.get("amount") or 0, reverse=True)
    if not ranked:
        return "No bills with amounts yet.", []
    lines = [f"Your {min(count, len(ranked))} biggest bills:"]
    for bill in ranked[:count]:
        lines.append(
            "- " + (bill.get("product") or "Bill")
            + f": {_agent_money(bill.get('amount'))}"
            + (f" ({bill.get('seller')})" if bill.get("seller") else "")
            + (f", {bill.get('purchase_date')}"
               if bill.get("purchase_date") else "")
        )
    return "\n".join(lines), ranked[:count]


def _agent_tool_recent(bills, count=5):
    ordered = sorted(
        bills,
        key=lambda bill: (
            str(bill.get("received_date") or ""),
            str(bill.get("created_at") or ""),
        ),
        reverse=True,
    )
    if not ordered:
        return "Your vault is empty.", []
    lines = ["Your most recently added bills:"]
    for bill in ordered[:count]:
        lines.append(
            "- " + (bill.get("product") or "Bill")
            + (f": {_agent_money(bill.get('amount'))}"
               if bill.get("amount") else "")
            + (f" (received {bill.get('received_date')})"
               if bill.get("received_date") else "")
        )
    return "\n".join(lines), ordered[:count]


def _agent_tool_seller_total(bills, seller):
    matched = [
        bill for bill in bills
        if seller.lower() in str(bill.get("seller") or "").lower()
    ]
    if not matched:
        return f"I found no bills from {seller}.", []
    total = sum(bill.get("amount") or 0 for bill in matched)
    answer = (
        f"You spent {_agent_money(total)} at {seller} "
        f"across {len(matched)} bill(s)."
    )
    return answer, matched[:200]


def _agent_tool_explain(bills, message, query=None):
    found = _agent_tool_search(bills, message, query)
    if not found or not found[1]:
        return "I could not find that bill in your vault.", []
    bill = found[1][0]
    lines = [
        (bill.get("product") or "Bill")
        + (f" from {bill.get('seller')}" if bill.get("seller") else "")
        + f" — {_agent_money(bill.get('amount'))}"
        if bill.get("amount") else (bill.get("product") or "Bill"),
        f"Category: {bill.get('category') or 'Other'}",
        f"Purchased: {bill.get('purchase_date') or 'not recorded'}"
        f" · Received in Raseed: {bill.get('received_date') or '—'}",
    ]
    if bill.get("invoice_no"):
        lines.append(f"Invoice number: {bill['invoice_no']}")
    if bill.get("order_no"):
        lines.append(f"Order number: {bill['order_no']}")
    if bill.get("warranty_lifetime"):
        lines.append("Warranty: Lifetime")
    elif bill.get("warranty_end"):
        lines.append(
            f"Warranty: {bill.get('warranty_status') or ''}, "
            f"ends {bill['warranty_end']}"
        )
    else:
        lines.append("Warranty: none recorded")
    lines.append(
        "Source: " + ("came from Gmail"
                       if bill.get("source") == "gmail"
                       else "uploaded/scanned by you")
    )
    if bill.get("needs_review"):
        lines.append(
            "Needs review: " + (bill.get("review_reason") or ""))
    return "\n".join(lines), [bill]


def _agent_learned_rules(owner):
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT seller_key, category FROM learned_rules"
                " WHERE owner_email=? ORDER BY seller_key",
                (owner,),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]
    except Exception:
        return []


def _agent_tool_learned_list(owner):
    rules = _agent_learned_rules(owner)
    if not rules:
        return (
            "I have not learned any rules from you yet. When you "
            "change a bill's category in Edit details, I remember "
            "it for that company.",
            [],
        )
    lines = ["Rules you taught me:"]
    for seller_key, category in rules:
        lines.append(
            f"- Bills from {seller_key.title()} go to {category}")
    lines.append(
        "To remove one, say: forget the <company> rule.")
    return "\n".join(lines), []


def _agent_tool_learned_forget(owner, message, seller=None):
    rules = _agent_learned_rules(owner)
    if not rules:
        return "I have no learned rules to forget.", []
    target = None
    if seller:
        wanted = _seller_key(seller)
        for seller_key, _category in rules:
            if seller_key == wanted:
                target = seller_key
                break
    if not target:
        lowered = message.lower()
        for seller_key, _category in rules:
            if seller_key in lowered:
                target = seller_key
                break
    if not target:
        names = ", ".join(key for key, _ in rules)
        return (
            "Which rule should I forget? I know rules for: "
            + names + ".",
            [],
        )
    with db() as conn:
        conn.execute(
            "DELETE FROM learned_rules"
            " WHERE owner_email=? AND seller_key=?",
            (owner, target),
        )
    return (
        f"Done — I forgot the rule for {target.title()}. "
        "New bills from them will be categorised normally again.",
        [],
    )


def _agent_dispatch(owner, bills, message, tool, plan):
    """Run one planned/named tool. Returns (answer, bills) or None."""
    plan = plan or {}
    if tool == "search":
        return _agent_tool_search(
            bills, message, plan.get("query")) or None
    if tool == "spending":
        return _agent_tool_spending(
            bills, message, plan.get("category"), plan.get("month"))
    if tool == "breakdown":
        return _agent_tool_breakdown(bills)
    if tool == "biggest":
        return _agent_tool_biggest(bills)
    if tool == "recent":
        return _agent_tool_recent(bills)
    if tool == "seller_total":
        seller = plan.get("seller")
        if not seller:
            return None
        return _agent_tool_seller_total(bills, seller)
    if tool == "explain":
        return _agent_tool_explain(
            bills, message, plan.get("query"))
    if tool == "review_list":
        return _agent_tool_review_list(bills)
    if tool == "check_all":
        return _agent_tool_check_all(owner)
    if tool == "warranties":
        return _agent_tool_warranties(
            bills, message, plan.get("days"))
    if tool == "duplicates":
        return _agent_tool_duplicates(bills)
    if tool == "count":
        total = sum(bill.get("amount") or 0 for bill in bills)
        return (
            f"You have {len(bills)} bill(s) in your vault, "
            f"totalling {_agent_money(total)}.",
            [],
        )
    if tool == "learned_list":
        return _agent_tool_learned_list(owner)
    if tool == "learned_forget":
        return _agent_tool_learned_forget(
            owner, message, plan.get("seller"))
    return None


def _agent_route_deterministic(owner, bills, message):
    """Keyword router — the no-Gemini fallback. Returns
    (answer, bills, tool) or None."""
    lowered = message.lower()
    if "check all" in lowered or "verify" in lowered:
        answer, used = _agent_tool_check_all(owner)
        return answer, used, "check_all"
    if "duplicate" in lowered:
        answer, used = _agent_tool_duplicates(bills)
        return answer, used, "duplicates"
    if "forget" in lowered and "rule" in lowered:
        answer, used = _agent_tool_learned_forget(owner, message)
        return answer, used, "learned_forget"
    if ("what have you learned" in lowered
            or "what do you remember" in lowered
            or "my rules" in lowered
            or "learned" in lowered):
        answer, used = _agent_tool_learned_list(owner)
        return answer, used, "learned_list"
    if "review" in lowered or "problem" in lowered:
        answer, used = _agent_tool_review_list(bills)
        return answer, used, "review_list"
    if "warrant" in lowered and (
            "expir" in lowered or "end" in lowered
            or "left" in lowered or "soon" in lowered):
        answer, used = _agent_tool_warranties(bills, message)
        return answer, used, "warranties"
    if ("breakdown" in lowered or "by category" in lowered
            or "each category" in lowered
            or "category wise" in lowered):
        answer, used = _agent_tool_breakdown(bills)
        return answer, used, "breakdown"
    if ("biggest" in lowered or "largest" in lowered
            or "most expensive" in lowered):
        answer, used = _agent_tool_biggest(bills)
        return answer, used, "biggest"
    if ("recent" in lowered or "latest" in lowered
            or "last bills" in lowered):
        answer, used = _agent_tool_recent(bills)
        return answer, used, "recent"
    if (lowered.startswith("tell me about")
            or "details of" in lowered
            or lowered.startswith("explain")):
        answer, used = _agent_tool_explain(bills, message)
        return answer, used, "explain"
    sellers = []
    for bill in bills:
        seller = str(bill.get("seller") or "").strip()
        if seller and seller not in sellers:
            sellers.append(seller)
    for seller in sellers:
        if len(seller) >= 3 and seller.lower() in lowered and any(
                word in lowered
                for word in ("how much", "spent", "spend", "total")):
            answer, used = _agent_tool_seller_total(bills, seller)
            return answer, used, "seller_total"
    if any(word in lowered for word in (
            "spend", "spent", "expense", "how much", "total")):
        answer, used = _agent_tool_spending(bills, message)
        return answer, used, "spending"
    if "how many" in lowered:
        total = sum(bill.get("amount") or 0 for bill in bills)
        return (
            f"You have {len(bills)} bill(s) in your vault, "
            f"totalling {_agent_money(total)}.",
            [],
            "count",
        )
    found = _agent_tool_search(bills, message)
    if found is not None:
        answer, used = found
        return answer, used, "search"
    return None


@app.post("/api/agent/ask")
def agent_ask(payload: AgentAsk, request: Request):
    owner = current_owner(request)
    message = (payload.message or "").strip()
    if guest_blocked(owner):
        return {
            "answer": "Please login first — the agent only works "
                      "on the vault of the person signed in.",
            "tool": "none",
            "bills": [],
        }
    help_text = (
        "Ask me about your bills — for example: 'How much did I "
        "spend on food in October?', 'Show my spending breakdown', "
        "'What are my biggest bills?', 'Tell me about my OnePlus "
        "bill', 'How much have I spent at Croma?', 'Which bills "
        "need review?', 'Check all my bills', 'What have you "
        "learned from me?' or 'Which warranties expire in 30 "
        "days?'."
    )
    if not message:
        return {"answer": help_text, "tool": "help", "bills": []}
    bills = _agent_bills(owner)
    history = payload.history if isinstance(
        payload.history, list) else []
    plan = _agent_plan(message, history, bills)
    routed = None
    if plan:
        dispatched = _agent_dispatch(
            owner, bills, message, plan["tool"], plan)
        if dispatched is not None:
            answer, used = dispatched
            routed = (answer, used, plan["tool"])
    if routed is None:
        routed = _agent_route_deterministic(owner, bills, message)
    if routed is None:
        routed = (
            "I could not match that to one of my tools yet. "
            + help_text,
            [],
            "help",
        )
    answer, used_bills, tool = routed
    return {"answer": answer, "tool": tool, "bills": used_bills}


@app.post("/api/gmail/watch")

def start_gmail_watch(request: Request):

    """

    Enable Gmail notifications for the currently connected account.

    Requires PUBSUB_TOPIC_NAME in backend/.env.

    """

    email = current_owner(request)

    if email == "local-demo":

        raise HTTPException(

            status_code=401,

            detail="Connect Gmail before enabling automatic monitoring.",

        )

    topic_name = os.getenv("PUBSUB_TOPIC_NAME")

    if not topic_name:

        raise HTTPException(

            status_code=503,

            detail="Set PUBSUB_TOPIC_NAME in backend/.env first.",

        )

    credentials = get_gmail_credentials(email)

    try:

        service = build(

            "gmail",

            "v1",

            credentials=credentials,

            cache_discovery=False,

        )

        result = service.users().watch(

            userId="me",

            body={"topicName": topic_name},

        ).execute()

        return {

            "enabled": True,

            "email": email,

            "history_id": result.get("historyId"),

            "expiration": result.get("expiration"),

            "message": "Gmail notifications enabled.",

        }

    except Exception as exc:

        raise HTTPException(

            status_code=502,

            detail=f"Could not enable Gmail notifications: {str(exc)[:200]}",

        ) from exc

@app.post("/api/gmail/push")

async def gmail_push(request: Request):

    """

    Receive a Google Pub/Sub push notification and start

    processing recent purchase-related email attachments.

    Configure the Pub/Sub push endpoint as:

    https://YOUR_PUBLIC_HOST/api/gmail/push

    """

    authorization = request.headers.get("authorization", "")

    expected_audience = os.getenv(

        "PUBSUB_PUSH_AUDIENCE",

        "https://billproof-starter.onrender.com",

    ).strip()

    expected_service_account = os.getenv(

        "PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", ""

    ).strip().lower()



    if not authorization.startswith("Bearer ") or not expected_service_account:

        raise HTTPException(

            status_code=401,

            detail="Missing or invalid push authentication.",

        )



    google_token = authorization.split(" ", 1)[1].strip()

    try:

        claims = id_token.verify_oauth2_token(

            google_token,

            GoogleAuthRequest(),

            audience=expected_audience,

        )

    except Exception as exc:

        raise HTTPException(

            status_code=401,

            detail="Google push token verification failed.",

        ) from exc



    if (

        claims.get("email", "").lower() != expected_service_account

        or claims.get("email_verified") is not True

    ):

        raise HTTPException(

            status_code=401,

            detail="Unexpected push service account.",

        )

    try:

        body = await request.json()

        message_data = body.get("message", {}).get("data")

        if not message_data:

            return {"ok": True, "ignored": True}

        decoded = base64.urlsafe_b64decode(

            message_data + "=" * (-len(message_data) % 4)

        )

        notification = json.loads(decoded)

        email = (notification.get("emailAddress") or "").lower()

        if not email:

            return {"ok": True, "ignored": True}

        with db() as conn:

            connected = conn.execute(

                "SELECT email FROM gmail_connections WHERE email = ?",

                (email,),

            ).fetchone()

        if not connected:

            return {"ok": True, "ignored": True}

        # Reuse the existing scanner and its duplicate detection.

        result = _perform_gmail_scan(email)

        return {

            "ok": True,

            "email": email,

            "scan": result,

        }

    except HTTPException:

        raise

    except Exception as exc:

        # A non-2xx response tells Pub/Sub to retry delivery.

        raise HTTPException(

            status_code=500,

            detail=f"Automatic Gmail processing failed: {str(exc)[:200]}",

        ) from exc


