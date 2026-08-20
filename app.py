"""
Inner Circle, custom site
---------------------------
Single-file Flask app: routes + inline templates + inline CSS.
Deploys to Render exactly like the verification bot did.

Env vars needed:
  BOT_TOKEN, reused from the Telegram bot, to forward interest-form
                     submissions to the admin chat.
  ADMIN_CHAT_ID, your personal Telegram numeric ID.
"""

import os
import hashlib
import hmac
import html
import json
import re
import time
from datetime import timedelta
import secrets
import string
from urllib.parse import quote
import markdown as md_lib
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, request, render_template_string, session, redirect, url_for, g, Response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Members forget their access codes, so keep them signed in for a good while
# rather than dropping them the moment the browser closes.
app.permanent_session_lifetime = timedelta(days=180)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
DATABASE_URL = os.environ.get("DATABASE_URL")
GOLD_GROUP_LINK = os.environ.get("GOLD_GROUP_LINK", "https://t.me/+etxMbgmMTW1mYTlk")

# Everyone who has onboarded and had their first signals gets this one. It's
# where we share results and the trades we've personally taken, with details.
INNER_CIRCLE_GROUP_LINK = os.environ.get(
    "INNER_CIRCLE_GROUP_LINK", "https://t.me/+jfiZo4Xy1JY3N2Vk")

FEMALE_WEALTH_GROUP_LINK = os.environ.get(
    "FEMALE_WEALTH_GROUP_LINK", "https://t.me/+TWaAqQlTTuU1OGU0")

# Set INSTAGRAM_URL on Render to switch this on. Left blank, the button hides
# itself rather than linking somewhere broken.
INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "")

# Every Telegram group, and which section unlocks it. This is what drives the
# member signals page, so a member can always find a link they've lost.
SIGNAL_GROUPS = [
    # section,            name,                          what it is
    ("signals_gold",     "Gold Signals",                 "Your main signals, gold (XAUUSD).",
     GOLD_GROUP_LINK),
    ("signals_gold",     "Inner Circle Results",         "Results and the trades we've personally taken, with details.",
     INNER_CIRCLE_GROUP_LINK),
    ("signals_currency", "Premium Gold Group",           "Longer trades targeting bigger moves. Recommended.",
     "https://t.me/+n4gTE50QU3BiM2U0"),
    ("signals_currency", "Scalping Group",               "Fast-paced setups through the day. High risk, not for beginners.",
     "https://t.me/+N5YFDmAHPwE5MGM0"),
    ("signals_currency", "Asia & London Sessions",       "Session trading with structured setups across time zones.",
     "https://t.me/+Z6Fq0YqPEdo5ZWE8"),
    ("signals_currency", "Intraday Group",               "FX and XAU/USD through the day.",
     "https://t.me/+l1dItG6YvJs3YWM0"),
    ("signals_currency", "Education Group",              "Questions and learning alongside the signals.",
     "https://t.me/+A-gV6MD-PMs1YjVk"),
    ("signals_currency", "Signals Community",            "Where signal results get shared.",
     "https://t.me/+SRBSp3rDvndjMThk"),
    ("her",              "Female Wealth",                "Private women-only group to discuss and chat.",
     FEMALE_WEALTH_GROUP_LINK),
]
CURRENCY_APPROVED_MESSAGE = """Thank you for completing your full verification for extra signals. You are now successfully registered, please use your new PU Prime account going forward to stay in all the signal groups.

Below are the official links to our channels. Kindly request to join, and your access will be approved shortly:

Premium Gold Group (recommended)
Longer trades targeting bigger moves.
https://t.me/+n4gTE50QU3BiM2U0

Scalping Group (not for beginners) HIGH RISK
Fast-paced setups throughout the day.
https://t.me/+N5YFDmAHPwE5MGM0

Asia & London Sessions (different time zones)
Dedicated session trading with structured setups.
https://t.me/+Z6Fq0YqPEdo5ZWE8

Intraday Group
Trading FX and XAU/USD throughout the day
https://t.me/+l1dItG6YvJs3YWM0

Education Group
https://t.me/+A-gV6MD-PMs1YjVk

Community
https://t.me/+SRBSp3rDvndjMThk

Once you have joined the channels, please ensure notifications are enabled so you do not miss any important updates, analysis, or live sessions.

Keep us updated with your results! Just message "share results" here and send your screenshots, we love seeing how everyone is getting on.

All of your group links are saved on your account, so you can find them again any time:
https://innercircletrading.co/my-signals

If you have any questions or require further support, do not hesitate to contact me."""

# Results gallery content. Add entries as real results and feedback come in.
# Nothing is invented here, so an empty list simply shows an honest placeholder.
#   RESULTS_ITEMS: {"image": "https://...", "caption": "XAUUSD, +120 pips"}
#   MEMBER_FEEDBACK: {"quote": "...", "who": "First name"}
RESULTS_ITEMS = []
MEMBER_FEEDBACK = []

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


def notify_admin(text: str) -> None:
    if not TELEGRAM_API or not ADMIN_CHAT_ID:
        return
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": int(ADMIN_CHAT_ID), "text": text}, timeout=10)
    except Exception:
        pass


def forward_photo_to_admin(from_chat_id, message_id, caption: str = "") -> bool:
    """Copy a photo the user sent into the admin chat so it can actually be reviewed."""
    if not TELEGRAM_API or not ADMIN_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"{TELEGRAM_API}/copyMessage",
            json={
                "chat_id": int(ADMIN_CHAT_ID),
                "from_chat_id": from_chat_id,
                "message_id": message_id,
                "caption": caption[:1000] if caption else None,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def send_telegram_message(chat_id, text: str) -> bool:
    if not TELEGRAM_API or not chat_id:
        return False
    try:
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": int(chat_id), "text": text}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SECTIONS, the things a member can be given or refused access to
# ---------------------------------------------------------------------------
# Add a row here and it appears as a tick box on every member profile, is
# enforced by has_access(), and is carried through merges. Nothing else to edit.

SECTIONS = [
    # key,               label,                      what it unlocks
    ("signals_gold",     "Gold signals",             "Premium gold signals group"),
    ("signals_currency", "Currency signals",         "PU Prime currency signals groups"),
    ("fundamentals",     "Trading Fundamentals",     "Free course, 41 lessons"),
    ("advanced",         "Advanced Chart Reading",   "Paid course, 23 lessons"),
    ("her",              "Female Wealth",            "Masterclasses, mindset lessons and the Wealth Circle group link"),
]

SECTION_KEYS = [s[0] for s in SECTIONS]
SECTION_LABELS = {s[0]: s[1] for s in SECTIONS}


# ---------------------------------------------------------------------------
# PHONE NUMBERS, the member identifier
# ---------------------------------------------------------------------------

DEFAULT_COUNTRY_CODE = "44"  # UK


def normalize_phone(raw):
    """
    Turn whatever someone typed into one comparable form, so '07700 900123',
    '+44 7700 900123' and '00447700900123' are recognised as the same person.
    Returns None if there aren't enough digits to be a real number.
    """
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None

    had_plus = str(raw).strip().startswith("+")

    if digits.startswith("00"):
        digits = digits[2:]
    elif had_plus:
        pass
    elif digits.startswith("0"):
        # National format, e.g. 07700 900123
        digits = DEFAULT_COUNTRY_CODE + digits[1:]
    elif not digits.startswith(DEFAULT_COUNTRY_CODE) and len(digits) <= 10:
        # Bare national number with the leading zero left off
        digits = DEFAULT_COUNTRY_CODE + digits

    if len(digits) < 8:
        return None
    return "+" + digits


def clean_email(raw):
    """Lower-cased and trimmed, or None. Not validated beyond having an @ and a dot."""
    if not raw:
        return None
    e = str(raw).strip().lower()
    if "@" not in e or "." not in e.split("@")[-1] or len(e) < 5:
        return None
    return e


def pretty_phone(raw):
    """Display form. Falls back to whatever they typed if it can't be parsed."""
    norm = normalize_phone(raw)
    if not norm:
        return (raw or "").strip() or "no phone on file"
    if norm.startswith("+44") and len(norm) == 13:
        return f"{norm[:3]} {norm[3:7]} {norm[7:]}"
    return norm


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------

def get_db():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_contacts (
                    username TEXT PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    last_seen TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS verifications (
                    code TEXT PRIMARY KEY,
                    chat_id BIGINT,
                    username TEXT,
                    photo_count INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS photo_submissions (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    kind TEXT NOT NULL,
                    photo_count INT DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    member_id INT NOT NULL,
                    sender TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    read_by_admin BOOLEAN DEFAULT FALSE
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    id SERIAL PRIMARY KEY,
                    tier TEXT NOT NULL,
                    title TEXT,
                    name TEXT,
                    account_number TEXT,
                    deposit_amount TEXT,
                    phone TEXT,
                    telegram_username TEXT,
                    verification_code TEXT,
                    referred_by TEXT,
                    status TEXT DEFAULT 'pending',
                    access_code TEXT,
                    chat_id BIGINT,
                    paid BOOLEAN DEFAULT FALSE,
                    community_approved BOOLEAN DEFAULT FALSE,
                    community_requested BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    approved_at TIMESTAMP
                );
            """)
            # Per-section access. One row per member per unlocked section.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS member_access (
                    member_id INT NOT NULL,
                    section TEXT NOT NULL,
                    granted_at TIMESTAMP DEFAULT NOW(),
                    granted_by TEXT DEFAULT 'admin',
                    PRIMARY KEY (member_id, section)
                );
            """)
            # Trail of who changed what, so access changes are never a mystery.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id SERIAL PRIMARY KEY,
                    member_id INT,
                    action TEXT NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # Simple key/value so one-time migrations only ever run once.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    chat_id BIGINT PRIMARY KEY,
                    state TEXT,
                    photo_count INT DEFAULT 0,
                    greeted BOOLEAN DEFAULT FALSE,
                    tips_sent BOOLEAN DEFAULT FALSE,
                    pending_intent TEXT,
                    stuck_count INT DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

            # migrations for existing installs
            for col, coltype in [("community_approved", "BOOLEAN DEFAULT FALSE"),
                                  ("community_requested", "BOOLEAN DEFAULT FALSE"),
                                  ("phone_normalized", "TEXT"),
                                  ("merged_into", "INT"),
                                  ("email", "TEXT"),
                                  ("currency_account_number", "TEXT"),
                                  ("currency_deposit_amount", "TEXT"),
                                  ("currency_submitted_at", "TIMESTAMP"),
                                  ("telegram_first_name", "TEXT"),
                                  ("admin_notes", "TEXT"),
                                  ("updated_at", "TIMESTAMP")]:
                try:
                    cur.execute(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {coltype};")
                except Exception:
                    pass

            for col, coltype in [("stuck_count", "INT DEFAULT 0"),
                                 ("tips_sent", "BOOLEAN DEFAULT FALSE"),
                                 ("pending_intent", "TEXT")]:
                try:
                    cur.execute(f"ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS {col} {coltype};")
                except Exception:
                    pass

            for col, coltype in [("file_ids", "TEXT"),
                                 ("member_id", "INT"),
                                 ("message_id", "BIGINT"),
                                 ("resolved_at", "TIMESTAMP")]:
                try:
                    cur.execute(f"ALTER TABLE photo_submissions ADD COLUMN IF NOT EXISTS {col} {coltype};")
                except Exception:
                    pass

            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_members_phone_norm ON members (phone_normalized);",
                "CREATE INDEX IF NOT EXISTS idx_members_email ON members (email);",
                "CREATE INDEX IF NOT EXISTS idx_members_merged ON members (merged_into);",
                "CREATE INDEX IF NOT EXISTS idx_members_access_code ON members (access_code);",
                "CREATE INDEX IF NOT EXISTS idx_member_access_member ON member_access (member_id);",
                "CREATE INDEX IF NOT EXISTS idx_messages_member ON messages (member_id);",
            ]:
                try:
                    cur.execute(idx_sql)
                except Exception:
                    pass
    finally:
        conn.close()

    backfill_phone_normalized()
    backfill_access_from_flags()
    fold_community_into_her()
    backfill_currency_details()


def _meta_get(key):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_meta WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _meta_set(key, value):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO app_meta (key, value) VALUES (%s,%s)
                           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (key, value))
    except Exception:
        pass
    finally:
        conn.close()


def backfill_phone_normalized():
    """Fill phone_normalized for any row that hasn't got one yet. Safe to re-run."""
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, phone FROM members
                           WHERE phone_normalized IS NULL AND phone IS NOT NULL AND phone <> ''""")
            rows = cur.fetchall()
            for mid, raw in rows:
                norm = normalize_phone(raw)
                if norm:
                    cur.execute("UPDATE members SET phone_normalized=%s WHERE id=%s", (norm, mid))
    except Exception:
        pass
    finally:
        conn.close()


def backfill_access_from_flags():
    """
    One-time only: translate the old boolean flags into member_access rows so
    nobody loses access the moment this deploys. Guarded by app_meta, because
    re-running it would silently re-grant sections an admin had ticked off.
    """
    if _meta_get("access_backfill_v1") == "done":
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members")
            for m in cur.fetchall():
                sections = set()
                if m.get("status") == "approved":
                    sections.add("fundamentals")
                    if m.get("tier") == "gold":
                        sections.add("signals_gold")
                    elif m.get("tier") == "currency":
                        sections.add("signals_currency")
                if m.get("paid"):
                    sections.add("advanced")
                if m.get("community_approved") or m.get("tier") == "community":
                    sections.add("her")
                for s in sections:
                    cur.execute("""INSERT INTO member_access (member_id, section, granted_by)
                                   VALUES (%s,%s,'migration')
                                   ON CONFLICT (member_id, section) DO NOTHING""", (m["id"], s))
    except Exception:
        return
    finally:
        conn.close()
    _meta_set("access_backfill_v1", "done")


try:
    init_db()
except Exception:
    pass


def add_message(member_id, sender, body):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (member_id, sender, body, read_by_admin) VALUES (%s,%s,%s,%s)",
                (member_id, sender, body, sender == "admin"))
    finally:
        conn.close()


def get_messages(member_id):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE member_id=%s ORDER BY created_at ASC", (member_id,))
            return cur.fetchall()
    finally:
        conn.close()


def get_unread_count():
    conn = get_db()
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM messages WHERE read_by_admin = FALSE")
            return cur.fetchone()[0]
    finally:
        conn.close()


def get_member_by_id(member_id):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE id=%s", (member_id,))
            return cur.fetchone()
    finally:
        conn.close()


def mark_messages_read(member_id):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE messages SET read_by_admin=TRUE WHERE member_id=%s", (member_id,))
    finally:
        conn.close()


def member_for_chat(chat_id, username=None):
    """
    Which account does this Telegram chat belong to? Chat ID is the strongest
    link because it survives someone changing their @username, and plenty of
    people have no username at all.
    """
    if not chat_id:
        return None
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM members WHERE chat_id=%s AND merged_into IS NULL
                           ORDER BY created_at ASC LIMIT 1""", (chat_id,))
            row = cur.fetchone()
            if row:
                return row
            handle = (username or "").lstrip("@").lower()
            if handle:
                cur.execute("""SELECT * FROM members
                               WHERE LOWER(REPLACE(COALESCE(telegram_username,''),'@','')) = %s
                                 AND merged_into IS NULL
                               ORDER BY created_at ASC LIMIT 1""", (handle,))
                row = cur.fetchone()
                if row:
                    # Remember the chat so next time we match without the handle.
                    cur.execute("UPDATE members SET chat_id=%s WHERE id=%s", (chat_id, row["id"]))
                    return row
    except Exception:
        return None
    finally:
        conn.close()
    return None


def add_photo_file_id(chat_id, kind, file_id):
    """
    Keep the Telegram file id for each screenshot so the admin page can show
    the images itself, rather than sending you back to Telegram to look.
    """
    if not file_id:
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, file_ids FROM photo_submissions
                           WHERE chat_id=%s AND kind=%s AND status='pending'
                           ORDER BY id DESC LIMIT 1""", (chat_id, kind))
            row = cur.fetchone()
            if not row:
                return
            existing = [f for f in (row[1] or "").split(",") if f]
            if file_id in existing:
                return
            existing.append(file_id)
            cur.execute("UPDATE photo_submissions SET file_ids=%s WHERE id=%s",
                        (",".join(existing[-4:]), row[0]))
    except Exception:
        pass
    finally:
        conn.close()


def telegram_file_url(file_id):
    """Turn a Telegram file id into a URL we can stream the image from."""
    if not (TELEGRAM_API and file_id):
        return None
    try:
        r = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=10)
        path = (r.json().get("result") or {}).get("file_path")
        if not path:
            return None
        return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    except Exception:
        return None


def upsert_photo_submission(chat_id, username, first_name, kind, count, message_id=None):
    """Log a photo so it turns up in the admin queue as something to tick off."""
    member = member_for_chat(chat_id, username)
    member_id = member["id"] if member else None
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id FROM photo_submissions
                           WHERE chat_id=%s AND kind=%s AND status='pending'
                           ORDER BY id DESC LIMIT 1""", (chat_id, kind))
            row = cur.fetchone()
            if row:
                cur.execute("""UPDATE photo_submissions SET photo_count=%s, created_at=NOW(),
                               member_id=COALESCE(%s, member_id), message_id=COALESCE(%s, message_id),
                               username=COALESCE(NULLIF(%s,''), username),
                               first_name=COALESCE(NULLIF(%s,''), first_name)
                               WHERE id=%s""",
                            (count, member_id, message_id, username or "", first_name or "", row[0]))
            else:
                cur.execute("""INSERT INTO photo_submissions
                               (chat_id, username, first_name, kind, photo_count, member_id, message_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (chat_id, username, first_name, kind, count, member_id, message_id))
    finally:
        conn.close()


def get_photo_submissions():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT p.*, m.name AS member_name, m.phone AS member_phone,
                       m.access_code AS member_code, m.status AS member_status,
                       m.tier AS member_tier, m.account_number AS member_account,
                       m.currency_account_number AS member_currency_account
                FROM photo_submissions p
                LEFT JOIN members m ON m.id = p.member_id AND m.merged_into IS NULL
                WHERE p.status='pending' ORDER BY p.created_at DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


def get_photo_submission(sub_id):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM photo_submissions WHERE id=%s", (sub_id,))
            return cur.fetchone()
    except Exception:
        return None
    finally:
        conn.close()


def link_photo_to_member(sub_id, member_id):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE photo_submissions SET member_id=%s WHERE id=%s", (member_id, sub_id))
    except Exception:
        pass
    finally:
        conn.close()


def count_pending_photos():
    conn = get_db()
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM photo_submissions WHERE status='pending'")
            return int(cur.fetchone()[0])
    except Exception:
        return 0
    finally:
        conn.close()


def resolve_photo_submission(sub_id, status):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM photo_submissions WHERE id=%s", (sub_id,))
            row = cur.fetchone()
            cur.execute("UPDATE photo_submissions SET status=%s WHERE id=%s", (status, sub_id))
            return row
    finally:
        conn.close()


def get_all_conversations():
    """Every member who has messages, newest first, with unread count."""
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT m.id, m.name, m.title, m.tier, m.telegram_username,
                       MAX(msg.created_at) AS last_at,
                       SUM(CASE WHEN msg.read_by_admin = FALSE THEN 1 ELSE 0 END) AS unread,
                       (SELECT body FROM messages WHERE member_id = m.id ORDER BY created_at DESC LIMIT 1) AS last_body
                FROM members m JOIN messages msg ON msg.member_id = m.id
                WHERE m.merged_into IS NULL
                GROUP BY m.id, m.name, m.title, m.tier, m.telegram_username
                ORDER BY MAX(msg.created_at) DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


def gen_access_code():
    return "AC-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(7))


def create_pending_member(tier, title, name, account_number, deposit_amount, phone,
                           telegram_username=None, verification_code=None, referred_by=None,
                           email=None):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor() as cur:
            chat_id = None
            if verification_code:
                cur.execute("SELECT chat_id FROM verifications WHERE code = %s", (verification_code,))
                row = cur.fetchone()
                if row:
                    chat_id = row[0]
            if not chat_id and telegram_username:
                uname = telegram_username.lstrip("@")
                cur.execute("SELECT chat_id FROM bot_contacts WHERE username = %s", (uname,))
                row = cur.fetchone()
                if row:
                    chat_id = row[0]
            phone_norm = normalize_phone(phone)
            cur.execute("""
                INSERT INTO members (tier, title, name, account_number, deposit_amount, phone,
                                      phone_normalized, email, telegram_username, verification_code,
                                      referred_by, chat_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (tier, title, name, account_number, deposit_amount, phone, phone_norm,
                  clean_email(email), telegram_username, verification_code, referred_by, chat_id))
            new_id = cur.fetchone()[0]

            # Flag it straight away if this phone number is already on the books,
            # so duplicates surface in admin instead of piling up unnoticed.
            if phone_norm:
                cur.execute("""SELECT id FROM members
                               WHERE phone_normalized=%s AND id <> %s AND merged_into IS NULL""",
                            (phone_norm, new_id))
                existing = [r[0] for r in cur.fetchall()]
                if existing:
                    notify_admin(
                        f"Possible duplicate: {name or 'new signup'} used a phone number already "
                        f"on record (member {', '.join('#' + str(e) for e in existing)}). "
                        f"Review at https://innercircletrading.co/admin/member/{new_id}"
                    )
            return new_id
    finally:
        conn.close()


def approve_member(member_id):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            code = gen_access_code()
            cur.execute("""
                UPDATE members SET status='approved', access_code=%s, approved_at=NOW(), updated_at=NOW()
                WHERE id=%s RETURNING *
            """, (code, member_id))
            row = cur.fetchone()
    finally:
        conn.close()

    if row:
        sections = {"fundamentals"}
        if row.get("tier") == "gold":
            sections.add("signals_gold")
        elif row.get("tier") == "currency":
            sections.add("signals_currency")
        grant_sections(member_id, sections, actor="approval")
        audit(member_id, "approved", f"{row.get('tier')} tier, code {row.get('access_code')}")
    return row


def grant_community(member_id):
    """Unlock Female Wealth on this account. Keeps their existing access code."""
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE id=%s", (member_id,))
            row = cur.fetchone()
            if not row:
                return None
            # only mint a code if they genuinely don't have one yet
            if not row.get("access_code"):
                cur.execute("UPDATE members SET access_code=%s, approved_at=NOW() WHERE id=%s",
                            (gen_access_code(), member_id))
            cur.execute("""UPDATE members SET community_approved=TRUE, community_requested=FALSE,
                           status='approved', updated_at=NOW() WHERE id=%s""", (member_id,))
            cur.execute("SELECT * FROM members WHERE id=%s", (member_id,))
            result = cur.fetchone()
    finally:
        conn.close()

    grant_sections(member_id, {"her"}, actor="community approval")
    return result


def mark_paid(member_id):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE members SET paid=TRUE, updated_at=NOW() WHERE id=%s RETURNING *",
                        (member_id,))
            result = cur.fetchone()
    finally:
        conn.close()

    grant_sections(member_id, {"advanced"}, actor="payment")
    return result


def get_pending_members():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE status='pending' AND merged_into IS NULL ORDER BY created_at DESC")
            return cur.fetchall()
    finally:
        conn.close()


def get_community_requests():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM members
                           WHERE community_requested = TRUE AND COALESCE(community_approved, FALSE) = FALSE
                             AND merged_into IS NULL
                           ORDER BY created_at DESC""")
            return cur.fetchall()
    finally:
        conn.close()


def get_approved_members():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE status='approved' AND merged_into IS NULL ORDER BY approved_at DESC NULLS LAST")
            return cur.fetchall()
    finally:
        conn.close()


def fold_community_into_her():
    """
    Female Wealth and the Wealth Circle are one thing, so they're one section.
    Anyone who ended up with a separate 'community' row keeps their access as
    'her', and the old rows are cleared out. One-time, then never again.
    """
    if _meta_get("community_folded_v1") == "done":
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO member_access (member_id, section, granted_by)
                           SELECT member_id, 'her', 'migration' FROM member_access
                           WHERE section='community'
                           ON CONFLICT (member_id, section) DO NOTHING""")
            cur.execute("DELETE FROM member_access WHERE section='community'")
    except Exception:
        return
    finally:
        conn.close()
    _meta_set("community_folded_v1", "done")


def backfill_currency_details():
    """
    Extra signals details used to live on a separate record, which meant they
    vanished from view once that record was merged in. Lift them onto the
    account they belong to. One-time.
    """
    if _meta_get("currency_fields_v1") == "done":
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            # A currency record's own details are its extra signals details.
            cur.execute("""UPDATE members
                           SET currency_account_number = account_number,
                               currency_deposit_amount = deposit_amount,
                               currency_submitted_at = created_at
                           WHERE tier = 'currency'
                             AND currency_account_number IS NULL
                             AND COALESCE(account_number,'') <> ''""")
            # Records already merged away carry their details to the survivor.
            cur.execute("""
                UPDATE members p
                SET currency_account_number = d.account_number,
                    currency_deposit_amount = d.deposit_amount,
                    currency_submitted_at = d.created_at
                FROM members d
                WHERE d.merged_into = p.id
                  AND d.tier = 'currency'
                  AND COALESCE(d.account_number,'') <> ''
                  AND p.currency_account_number IS NULL
            """)
    except Exception:
        return
    finally:
        conn.close()
    _meta_set("currency_fields_v1", "done")


def resend_access_code(member_id):
    """
    Send a member their existing access code again. Codes go out once during
    onboarding and people lose them, so this is the recovery path.

    The code itself never changes, so old links and anything they wrote down
    still work. Returns (member, code, sent_on_telegram) so the admin page can
    show the code for copying when there's no Telegram chat to send it to.
    """
    member = get_member_by_id(member_id)
    if not member:
        return None, None, False

    code = member.get("access_code")
    if not code:
        if member.get("status") != "approved":
            # Minting a code here would hand them something that can't log in,
            # because /unlock only accepts codes on approved accounts.
            return member, None, False
        # Approved but never issued a code.
        code = gen_access_code()
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET access_code=%s, updated_at=NOW(),
                                   approved_at=COALESCE(approved_at, NOW()) WHERE id=%s""",
                                (code, member_id))
            finally:
                conn.close()
        member["access_code"] = code
        audit(member_id, "access code issued", "no code was on file, a new one was created")

    sent = False
    if member.get("chat_id"):
        sent = send_telegram_message(
            member["chat_id"],
            "Here's your Inner Circle access code again:\n\n"
            f"{code}\n\n"
            "Log in at https://innercircletrading.co/unlock\n\n"
            "Keep it somewhere safe, you'll need it again if you switch phone or clear your browser."
        )

    audit(member_id, "access code resent",
          "sent on Telegram" if sent else "no Telegram chat linked, code shown to admin instead")
    return member, code, sent


def regenerate_access_code(member_id):
    """
    Issue a brand new code and retire the old one. For when a code has been
    shared around or someone else has got hold of it. Returns the same shape as
    resend_access_code.
    """
    member = get_member_by_id(member_id)
    if not member:
        return None, None, False

    if member.get("status") != "approved":
        return member, None, False

    old = member.get("access_code")
    code = gen_access_code()
    conn = get_db()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""UPDATE members SET access_code=%s, updated_at=NOW(),
                               approved_at=COALESCE(approved_at, NOW()) WHERE id=%s""",
                            (code, member_id))
        finally:
            conn.close()

    sent = False
    if member.get("chat_id"):
        sent = send_telegram_message(
            member["chat_id"],
            "Your Inner Circle access code has been changed.\n\n"
            f"Your new code is: {code}\n\n"
            "Log in at https://innercircletrading.co/unlock\n\n"
            "Your old code no longer works, so use this one from now on."
        )

    audit(member_id, "access code replaced",
          f"old code {old or 'none'} retired" + ("" if sent else ", not sent, no Telegram chat linked"))
    return member, code, sent


def requested_sections(member, granted=None):
    """
    What this person is waiting on, worked out from what they submitted.
    This is what the one-click approve button grants, so the admin never has
    to open a profile and work it out by hand.
    """
    if granted is None:
        granted = get_member_sections(member["id"])
    want = set()

    # Onboarding submitted: gold signals and the free course.
    if str(member.get("account_number") or "").strip() or member.get("tier") == "gold":
        want |= {"signals_gold", "fundamentals"}
    # Extra signals form submitted.
    if str(member.get("currency_account_number") or "").strip() or member.get("tier") == "currency":
        want |= {"signals_currency", "fundamentals"}
    # Female Wealth requested.
    if member.get("community_requested") or member.get("tier") == "community":
        want.add("her")

    return want - set(granted)


def section_links_message(sections):
    """
    The Telegram message for a set of newly granted sections: the group links
    that section unlocks, plus where to find them again on the site.
    """
    parts = []
    for key in SECTION_KEYS:
        if key not in sections:
            continue
        label = SECTION_LABELS[key]
        groups = [(n, u) for sec, n, _b, u in SIGNAL_GROUPS if sec == key]
        if key == "fundamentals":
            parts.append("Trading Fundamentals is open to you:\n"
                         "https://innercircletrading.co/education/fundamentals")
        elif key == "advanced":
            parts.append("Advanced Chart Reading is unlocked:\n"
                         "https://innercircletrading.co/education/advanced")
        if groups:
            lines = "\n".join(f"{n}\n{u}" for n, u in groups)
            parts.append(f"{label}, your group links:\n{lines}")
    if not parts:
        return None
    return "\n\n".join(parts)


def deliver_sections(member_id, sections, note=""):
    """
    Give someone access and hand them everything that access is worth: their
    code if they haven't got one, and the group links for what was unlocked.
    Returns a short summary for the admin flash message.
    """
    if not sections:
        return "Nothing changed."

    member = get_member_by_id(member_id)
    if not member:
        return "Couldn't find that member."

    # Access is worth nothing without a way to log in, so make sure of that first.
    code = (member.get("access_code") or "").strip()
    code_note = ""
    if not code:
        code = gen_access_code()
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET access_code=%s, status='approved',
                                   approved_at=COALESCE(approved_at, NOW()), updated_at=NOW()
                                   WHERE id=%s""", (code, member_id))
            finally:
                conn.close()
        audit(member_id, "access code issued", "created automatically on approval")
        code_note = f" Code {code} created."

    body = section_links_message(sections)
    sent = False
    if member.get("chat_id") and body:
        labels = ", ".join(SECTION_LABELS[s] for s in SECTION_KEYS if s in sections)
        sent = send_telegram_message(
            member["chat_id"],
            f"You're approved for {labels}.\n\n"
            f"{body}\n\n"
            f"Your website access code: {code}\n"
            f"Log in: https://innercircletrading.co/unlock\n\n"
            f"Every link you have is saved here, so you can find them again any time:\n"
            f"https://innercircletrading.co/my-signals"
        )

    audit(member_id, "links delivered",
          ("sent on Telegram" if sent else "no Telegram chat linked, links not sent") + (" " + note if note else ""))

    if sent:
        return f"Approved and links sent on Telegram.{code_note}"
    return (f"Approved.{code_note} No Telegram chat linked, so nothing was sent. "
            f"Their links are on their account page at /my-signals once they log in with {code}.")


# ---------------------------------------------------------------------------
# CARD PAYMENT FOR THE ADVANCED COURSE
# ---------------------------------------------------------------------------
# Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET on Render to switch this on.
# With them set, paying by card unlocks the course by itself, no screenshot and
# nothing for the admin to do. Without them, the PayPal link and the screenshot
# flow carry on working exactly as before, so this is safe to deploy unset.

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ADVANCED_PRICE_PENCE = int(os.environ.get("ADVANCED_PRICE_PENCE", "9900"))
PAYPAL_LINK = os.environ.get(
    "PAYPAL_LINK", "https://www.paypal.com/ncp/payment/JMNWH9XAF6PXL")


def card_payments_on():
    return bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET)


def create_checkout_session(member):
    """
    Start a card payment for this member. Their member id rides along on the
    session, which is what lets the webhook unlock the right account without
    anyone having to work out who paid.
    """
    if not STRIPE_SECRET_KEY:
        return None
    data = {
        "mode": "payment",
        "success_url": "https://innercircletrading.co/advanced/paid?ok=1",
        "cancel_url": "https://innercircletrading.co/education/advanced",
        "client_reference_id": str(member["id"]),
        "metadata[member_id]": str(member["id"]),
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "gbp",
        "line_items[0][price_data][unit_amount]": str(ADVANCED_PRICE_PENCE),
        "line_items[0][price_data][product_data][name]": "Advanced Chart Reading",
        "line_items[0][price_data][product_data][description]":
            "One-time payment, 23 lessons, yours for good.",
    }
    if member.get("email"):
        data["customer_email"] = member["email"]
    try:
        r = requests.post("https://api.stripe.com/v1/checkout/sessions",
                          data=data, auth=(STRIPE_SECRET_KEY, ""), timeout=15)
        if r.status_code >= 300:
            return None
        return r.json().get("url")
    except Exception:
        return None


def stripe_signature_ok(payload, sig_header):
    """
    Check the webhook really came from Stripe. Without this anyone could post a
    fake 'they paid' event and help themselves to the course.
    """
    if not (STRIPE_WEBHOOK_SECRET and sig_header):
        return False
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        timestamp, given = parts.get("t"), parts.get("v1")
        if not (timestamp and given):
            return False
        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, given):
            return False
        # Reject anything more than five minutes old, so a captured request
        # can't be replayed later.
        return abs(time.time() - int(timestamp)) < 300
    except Exception:
        return False


@app.route("/advanced/buy", methods=["POST", "GET"])
def advanced_buy():
    if not is_verified():
        return redirect(url_for("unlock"))
    member = get_member_by_id(session["member_id"])
    if not member:
        return redirect(url_for("unlock"))
    if has_access("advanced"):
        return redirect("/education/advanced/0")

    url = create_checkout_session(member)
    if not url:
        return redirect("/education/advanced?err=1")
    audit(member["id"], "card payment started", "Advanced Chart Reading checkout opened")
    return redirect(url, code=303)


@app.route("/advanced/paid")
def advanced_paid():
    """
    Where Stripe sends them after paying. The webhook is what actually grants
    access, so this page copes with arriving a second or two early.
    """
    unlocked = has_access("advanced")
    if unlocked:
        body = """
      <div class="ring-mark" style="margin: 0 auto 24px;"><span>✓</span></div>
      <h1 style="font-size: 30px; margin: 12px 0 18px;">You're in</h1>
      <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 28px;">
        Advanced Chart Reading is unlocked on your account. All 23 lessons, yours for good.
      </p>
      <a href="/education/advanced/0" class="btn btn-primary">Start the course</a>"""
    else:
        body = """
      <div class="ring-mark" style="margin: 0 auto 24px;"><span>✓</span></div>
      <h1 style="font-size: 30px; margin: 12px 0 18px;">Payment received</h1>
      <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 28px;">
        Thank you. Your access is being switched on now, it usually takes a few seconds.
        Refresh this page, and if it's still locked in a minute or two message us and we'll sort it straight away.
      </p>
      <a href="/advanced/paid" class="btn btn-primary">Refresh</a>
      <a href="/messages" class="btn btn-ghost">Message us</a>"""

    content = f"""
<section style="padding: 90px 0;">
  <div class="wrap" style="max-width: 560px; text-align: center;">{body}</div>
</section>
"""
    return render_template_string(base_layout("Payment received", content, ""))


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    """Stripe tells us a payment completed, and the course unlocks itself."""
    payload = request.get_data()
    if not stripe_signature_ok(payload, request.headers.get("Stripe-Signature", "")):
        return "bad signature", 400

    try:
        event = json.loads(payload.decode())
    except Exception:
        return "bad payload", 400

    if event.get("type") != "checkout.session.completed":
        return "ignored", 200

    obj = (event.get("data") or {}).get("object") or {}
    if obj.get("payment_status") != "paid":
        return "not paid", 200

    ref = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("member_id")
    if not (ref and str(ref).isdigit()):
        notify_admin("💳 A card payment came through but it wasn't tied to an account. "
                     "Check Stripe and grant Advanced by hand.")
        return "no member", 200

    member_id = int(ref)
    member = get_member_by_id(member_id)
    if not member:
        return "unknown member", 200

    if "advanced" not in get_member_sections(member_id):
        grant_sections(member_id, {"advanced"}, actor="card payment")
        deliver_sections(member_id, {"advanced"})
        audit(member_id, "advanced unlocked", "paid by card, unlocked automatically")
        notify_admin(f"💳 PAID AND UNLOCKED\n\n{member.get('name') or 'Member'} "
                     f"({pretty_phone(member.get('phone'))}) paid for Advanced Chart Reading. "
                     f"Access was granted automatically, nothing for you to do.")
    return "ok", 200


def find_member_by_access_code(code):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM members
                           WHERE access_code=%s AND status='approved' AND merged_into IS NULL""", (code,))
            row = cur.fetchone()
            if row:
                return row
            # An old code from a record that has since been merged away should
            # still work, otherwise merging quietly locks that member out.
            cur.execute("""SELECT merged_into FROM members
                           WHERE access_code=%s AND merged_into IS NOT NULL LIMIT 1""", (code,))
            moved = cur.fetchone()
            if moved:
                cur.execute("""SELECT * FROM members
                               WHERE id=%s AND status='approved' AND merged_into IS NULL""",
                            (moved["merged_into"],))
                return cur.fetchone()
            return None
    finally:
        conn.close()


def match_existing_member(phone=None, account_number=None, email=None):
    """
    Work out whether this submission belongs to somebody already on the books.
    Checked in order of how reliably each identifies a person: being logged in,
    then phone number, then either broker account number, then email.

    Every public form runs through this, which is what stops a second account
    being created every time the same person asks for something new.
    """
    if session.get("member_id"):
        existing = get_member_by_id(session["member_id"])
        if existing and not existing.get("merged_into"):
            return existing

    hit = find_member_by_phone(phone)
    if hit:
        return hit

    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            acct = (account_number or "").strip().lower()
            if acct:
                cur.execute("""SELECT * FROM members
                               WHERE merged_into IS NULL AND (
                                   LOWER(COALESCE(account_number,'')) = %s
                                   OR LOWER(COALESCE(currency_account_number,'')) = %s)
                               ORDER BY created_at ASC LIMIT 1""", (acct, acct))
                row = cur.fetchone()
                if row:
                    return row
            mail = clean_email(email)
            if mail:
                cur.execute("""SELECT * FROM members
                               WHERE merged_into IS NULL AND LOWER(COALESCE(email,'')) = %s
                               ORDER BY created_at ASC LIMIT 1""", (mail,))
                row = cur.fetchone()
                if row:
                    return row
    except Exception:
        return None
    finally:
        conn.close()
    return None


def find_member_by_phone(raw_phone):
    """Phone is the identifier, so this is the main way to find someone."""
    norm = normalize_phone(raw_phone)
    if not norm:
        return None
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM members
                           WHERE phone_normalized=%s AND merged_into IS NULL
                           ORDER BY (status='approved') DESC, created_at ASC LIMIT 1""", (norm,))
            return cur.fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ACCESS CONTROL
# ---------------------------------------------------------------------------

def audit(member_id, action, detail=""):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO admin_audit (member_id, action, detail) VALUES (%s,%s,%s)",
                        (member_id, action, detail))
    except Exception:
        pass
    finally:
        conn.close()


def get_audit(member_id, limit=25):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM admin_audit WHERE member_id=%s
                           ORDER BY created_at DESC LIMIT %s""", (member_id, limit))
            return cur.fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def get_member_sections(member_id):
    """The set of section keys this member can currently see."""
    if not member_id:
        return set()
    conn = get_db()
    if not conn:
        return set()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT section FROM member_access WHERE member_id=%s", (member_id,))
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()
    finally:
        conn.close()


def get_sections_for_members(member_ids):
    """Bulk version, so the admin list doesn't fire one query per member."""
    out = {mid: set() for mid in member_ids}
    if not member_ids:
        return out
    conn = get_db()
    if not conn:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT member_id, section FROM member_access WHERE member_id = ANY(%s)",
                        (list(member_ids),))
            for mid, section in cur.fetchall():
                out.setdefault(mid, set()).add(section)
    except Exception:
        pass
    finally:
        conn.close()
    return out


def set_member_sections(member_id, wanted, actor="admin"):
    """
    Make this member's access exactly `wanted`. Returns (granted, revoked) so
    the caller can tell them what changed.
    """
    wanted = {s for s in wanted if s in SECTION_KEYS}
    current = get_member_sections(member_id)
    granted = sorted(wanted - current)
    revoked = sorted(current - wanted)
    if not granted and not revoked:
        return [], []

    conn = get_db()
    if not conn:
        return [], []
    try:
        with conn, conn.cursor() as cur:
            for s in granted:
                cur.execute("""INSERT INTO member_access (member_id, section, granted_by)
                               VALUES (%s,%s,%s) ON CONFLICT (member_id, section) DO NOTHING""",
                            (member_id, s, actor))
            if revoked:
                cur.execute("DELETE FROM member_access WHERE member_id=%s AND section = ANY(%s)",
                            (member_id, revoked))
            # Keep the legacy flags in step, older code paths still read them.
            cur.execute("UPDATE members SET paid=%s, community_approved=%s, updated_at=NOW() WHERE id=%s",
                        ("advanced" in wanted, "her" in wanted, member_id))
    finally:
        conn.close()

    if granted:
        audit(member_id, "access granted", ", ".join(SECTION_LABELS[s] for s in granted))
    if revoked:
        audit(member_id, "access removed", ", ".join(SECTION_LABELS[s] for s in revoked))
    return granted, revoked


def grant_sections(member_id, sections, actor="admin"):
    """Add sections without touching anything already granted."""
    current = get_member_sections(member_id)
    return set_member_sections(member_id, current | set(sections), actor)


def load_access_into_session(member):
    session["member_id"] = member["id"]
    session["member_tier"] = member.get("tier")
    session["member_name"] = member.get("name", "")
    session["member_phone"] = member.get("phone_normalized") or member.get("phone") or ""
    sections = get_member_sections(member["id"])
    session["member_sections"] = sorted(sections)
    # legacy keys, kept so nothing that still reads them breaks
    session["member_paid"] = "advanced" in sections
    session["member_community"] = "her" in sections


# ---------------------------------------------------------------------------
# ADMIN: SEARCH, DUPLICATES, MERGE
# ---------------------------------------------------------------------------

def get_all_members(search="", limit=500):
    """
    Every live member, newest first. Search matches phone (normalised or as
    typed), name, access code, account number or Telegram handle.
    """
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if search:
                like = f"%{search.strip().lower()}%"
                norm = normalize_phone(search)
                cur.execute("""
                    SELECT * FROM members
                    WHERE merged_into IS NULL AND (
                        LOWER(COALESCE(name,'')) LIKE %s
                        OR LOWER(COALESCE(phone,'')) LIKE %s
                        OR COALESCE(phone_normalized,'') LIKE %s
                        OR LOWER(COALESCE(access_code,'')) LIKE %s
                        OR LOWER(COALESCE(account_number,'')) LIKE %s
                        OR LOWER(COALESCE(telegram_username,'')) LIKE %s
                        OR LOWER(COALESCE(email,'')) LIKE %s
                        OR (%s <> '' AND phone_normalized = %s)
                    )
                    ORDER BY created_at DESC LIMIT %s
                """, (like, like, like, like, like, like, like, norm or "", norm or "", limit))
            else:
                cur.execute("""SELECT * FROM members WHERE merged_into IS NULL
                               ORDER BY created_at DESC LIMIT %s""", (limit,))
            return cur.fetchall()
    finally:
        conn.close()


def get_member_full(member_id):
    """Member row plus everything hanging off it, for the profile page."""
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE id=%s", (member_id,))
            member = cur.fetchone()
            if not member:
                return None
            cur.execute("SELECT COUNT(*) AS c FROM messages WHERE member_id=%s", (member_id,))
            member["message_count"] = cur.fetchone()["c"]
            cur.execute("""SELECT COUNT(*) AS c FROM messages
                           WHERE member_id=%s AND read_by_admin=FALSE""", (member_id,))
            member["unread_count"] = cur.fetchone()["c"]
            cur.execute("""SELECT id, name, phone, created_at FROM members
                           WHERE merged_into=%s ORDER BY id""", (member_id,))
            member["absorbed"] = cur.fetchall()
        member["sections"] = get_member_sections(member_id)
        return member
    finally:
        conn.close()


def find_duplicates_for(member):
    """
    Other live records that look like the same person. Phone is the strong
    signal; Telegram handle and broker account number are backups for older
    records that were created before phone was collected properly.
    """
    if not member:
        return []
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT *,
                    CASE
                        WHEN phone_normalized IS NOT NULL AND phone_normalized = %s THEN 'same phone number'
                        WHEN COALESCE(telegram_username,'') <> ''
                             AND LOWER(REPLACE(COALESCE(telegram_username,''),'@','')) = %s THEN 'same Telegram handle'
                        WHEN COALESCE(account_number,'') <> ''
                             AND LOWER(COALESCE(account_number,'')) = %s THEN 'same broker account'
                        ELSE 'same name'
                    END AS match_reason
                FROM members
                WHERE id <> %s AND merged_into IS NULL AND (
                    (phone_normalized IS NOT NULL AND phone_normalized = %s)
                    OR (COALESCE(telegram_username,'') <> ''
                        AND LOWER(REPLACE(COALESCE(telegram_username,''),'@','')) = %s)
                    OR (COALESCE(account_number,'') <> ''
                        AND LOWER(COALESCE(account_number,'')) = %s)
                    OR (COALESCE(name,'') <> '' AND LOWER(COALESCE(name,'')) = %s)
                )
                ORDER BY created_at ASC
            """, (
                member.get("phone_normalized"),
                (member.get("telegram_username") or "").lstrip("@").lower(),
                (member.get("account_number") or "").lower(),
                member["id"],
                member.get("phone_normalized"),
                (member.get("telegram_username") or "").lstrip("@").lower(),
                (member.get("account_number") or "").lower(),
                (member.get("name") or "").lower(),
            ))
            return cur.fetchall()
    finally:
        conn.close()


def find_all_duplicate_groups():
    """All clusters of live records sharing a phone number, oldest record first."""
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT phone_normalized FROM members
                WHERE merged_into IS NULL AND phone_normalized IS NOT NULL
                GROUP BY phone_normalized HAVING COUNT(*) > 1
                ORDER BY phone_normalized
            """)
            phones = [r["phone_normalized"] for r in cur.fetchall()]
            groups = []
            for p in phones:
                cur.execute("""SELECT * FROM members
                               WHERE phone_normalized=%s AND merged_into IS NULL
                               ORDER BY (status='approved') DESC, created_at ASC""", (p,))
                groups.append({"phone": p, "members": cur.fetchall()})
            return groups
    finally:
        conn.close()


MERGE_FIELDS = ["title", "name", "account_number", "deposit_amount", "phone", "email",
                "telegram_username", "verification_code", "referred_by", "admin_notes",
                "currency_account_number", "currency_deposit_amount"]


def merge_members(primary_id, duplicate_ids, actor="admin"):
    """
    Fold duplicates into the primary record. Nothing is deleted: the duplicates
    are flagged merged_into=<primary> and hidden from the lists, so a bad merge
    can be undone by clearing that column.

    The primary keeps its own access code and any value it already has. Blanks
    on the primary are filled from the duplicates, oldest first. Access sections
    and messages are combined so nobody loses anything in the merge.
    """
    duplicate_ids = [int(d) for d in duplicate_ids if int(d) != int(primary_id)]
    if not duplicate_ids:
        return None

    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE id=%s AND merged_into IS NULL", (primary_id,))
            primary = cur.fetchone()
            if not primary:
                return None

            cur.execute("""SELECT * FROM members WHERE id = ANY(%s) AND merged_into IS NULL
                           ORDER BY created_at ASC""", (duplicate_ids,))
            dups = cur.fetchall()
            if not dups:
                return None

            # 1. Fill in anything blank on the primary from the duplicates.
            updates = {}
            for field in MERGE_FIELDS:
                if str(primary.get(field) or "").strip():
                    continue
                for d in dups:
                    val = str(d.get(field) or "").strip()
                    if val:
                        updates[field] = val
                        break
            if not str(primary.get("access_code") or "").strip():
                for d in dups:
                    if str(d.get("access_code") or "").strip():
                        updates["access_code"] = d["access_code"]
                        break
            if not primary.get("chat_id"):
                for d in dups:
                    if d.get("chat_id"):
                        updates["chat_id"] = d["chat_id"]
                        break
            if primary.get("status") != "approved" and any(d.get("status") == "approved" for d in dups):
                updates["status"] = "approved"
            if not primary.get("approved_at"):
                for d in dups:
                    if d.get("approved_at"):
                        updates["approved_at"] = d["approved_at"]
                        break

            # A currency record being absorbed carries the extra signals details.
            if not str(primary.get("currency_account_number") or "").strip():
                for d in dups:
                    if d.get("tier") == "currency" and str(d.get("account_number") or "").strip():
                        updates["currency_account_number"] = d["account_number"]
                        updates["currency_deposit_amount"] = d.get("deposit_amount")
                        updates["currency_submitted_at"] = d.get("created_at")
                        break

            if "phone" in updates:
                updates["phone_normalized"] = normalize_phone(updates["phone"])

            if updates:
                sets = ", ".join(f"{k}=%s" for k in updates)
                cur.execute(f"UPDATE members SET {sets}, updated_at=NOW() WHERE id=%s",
                            list(updates.values()) + [primary_id])

            # 2. Combine access. A section unlocked on any record stays unlocked.
            cur.execute("""INSERT INTO member_access (member_id, section, granted_by)
                           SELECT %s, section, 'merge' FROM member_access
                           WHERE member_id = ANY(%s)
                           ON CONFLICT (member_id, section) DO NOTHING""",
                        (primary_id, duplicate_ids))

            # 3. Move the message history across so the thread stays whole.
            cur.execute("UPDATE messages SET member_id=%s WHERE member_id = ANY(%s)",
                        (primary_id, duplicate_ids))

            # 4. Retire the duplicates, and re-point anything merged into them.
            cur.execute("""UPDATE members SET merged_into=%s, status='merged', updated_at=NOW()
                           WHERE id = ANY(%s)""", (primary_id, duplicate_ids))
            cur.execute("UPDATE members SET merged_into=%s WHERE merged_into = ANY(%s)",
                        (primary_id, duplicate_ids))
            cur.execute("DELETE FROM member_access WHERE member_id = ANY(%s)", (duplicate_ids,))

            cur.execute("SELECT * FROM members WHERE id=%s", (primary_id,))
            result = cur.fetchone()
    finally:
        conn.close()

    names = ", ".join(f"#{d['id']} {d.get('name') or 'unnamed'}" for d in dups)
    audit(primary_id, "accounts merged", f"absorbed {names}")
    for d in dups:
        audit(d["id"], "merged away", f"folded into #{primary_id}")
    return result


def delete_member(member_id):
    """
    Remove a record for good. Only ever used from the admin buttons, either to
    clear out merged shells or to bin test data. Messages and access rows go
    with it.
    """
    conn = get_db()
    if not conn:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM member_access WHERE member_id=%s", (member_id,))
            cur.execute("DELETE FROM messages WHERE member_id=%s", (member_id,))
            cur.execute("UPDATE photo_submissions SET member_id=NULL WHERE member_id=%s", (member_id,))
            cur.execute("DELETE FROM members WHERE id=%s", (member_id,))
        return True
    except Exception:
        return False
    finally:
        conn.close()


def delete_merged_shells(primary_id=None):
    """Clear out the empty records left behind by merging. Returns how many went."""
    conn = get_db()
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            if primary_id:
                cur.execute("SELECT id FROM members WHERE merged_into=%s", (primary_id,))
            else:
                cur.execute("SELECT id FROM members WHERE merged_into IS NOT NULL")
            ids = [r[0] for r in cur.fetchall()]
    except Exception:
        return 0
    finally:
        conn.close()

    gone = 0
    for mid in ids:
        if delete_member(mid):
            gone += 1
    return gone


def unmerge_member(member_id):
    """Undo a merge for one record, putting it back in the lists on its own."""
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE members SET merged_into=NULL, status='pending', updated_at=NOW()
                           WHERE id=%s""", (member_id,))
    finally:
        conn.close()
    audit(member_id, "merge undone", "separated back out into its own record")


def update_member_details(member_id, fields):
    """Save edits from the profile page. Phone edits re-normalise automatically."""
    allowed = ["title", "name", "phone", "email", "account_number", "deposit_amount",
               "telegram_username", "referred_by", "tier", "status", "admin_notes",
               "currency_account_number", "currency_deposit_amount"]
    updates = {k: (v.strip() if isinstance(v, str) else v)
               for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "phone" in updates:
        updates["phone_normalized"] = normalize_phone(updates["phone"])
    if "email" in updates:
        updates["email"] = clean_email(updates["email"])
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            sets = ", ".join(f"{k}=%s" for k in updates)
            cur.execute(f"UPDATE members SET {sets}, updated_at=NOW() WHERE id=%s",
                        list(updates.values()) + [member_id])
    finally:
        conn.close()
    audit(member_id, "details edited", ", ".join(sorted(updates.keys())))


# ---------------------------------------------------------------------------
# COURSE CONTENT (markdown, rendered at request time)
# ---------------------------------------------------------------------------

FUNDAMENTALS_MD = """## Disclaimer

This course is educational content only and is not financial advice. Nothing in this course, or in any signal shared with Inner Circle, is a guarantee of results.

Trading carries risk, and past performance is never a guarantee of future results. No strategy, system, or signal provider wins on every trade. You are responsible for your own trading decisions, always do your own research and never risk money you can't afford to lose.

## SECTION 1: Placing a Trade

### Understanding TP & SL

Every trade you place should have two safety settings attached:

**TP, Take Profit**
- The price at which your trade automatically closes and locks in a win. You set this when you're happy with a target profit.

**SL, Stop Loss**
- The price at which your trade automatically closes to prevent further loss. This protects you if the market moves against you.

Think of TP and SL as guardrails, they mean you don't have to sit and watch every trade. Once they're set, the trade manages itself.

[[FDIAGRAM:2]]

### Lot Size

Lot size is simply the size of your trade.

- **0.01 lots** is the smallest standard size, this is what we use for verification trades and for beginners, since it keeps risk very small.
- Bigger lot sizes mean bigger swings in profit and loss for the same price movement. A 20-pip move might mean a couple of pounds at 0.01 lots, or hundreds of pounds at 1.00 lots.
- As a beginner, staying at 0.01 (or close to it) while you learn is one of the simplest ways to keep risk under control.

### Lot Size by Account Balance

Once you're past the initial 0.01-lot verification stage, your lot size should scale with your account balance, not stay fixed, and not be guessed. Here's a simple, conservative guide: **roughly 0.01 lots for every £100 in your account.**

| Account Balance | Suggested Lot Size |
|---|---|
| £300 | 0.03 |
| £500 | 0.05 |
| £1,000 | 0.10 |
| £1,500 | 0.15 |
| £2,000 | 0.20 |
| £3,000 | 0.30 |
| £5,000 | 0.50 |
| £10,000 | 1.00 |

**How to use this table:**
- Find the row closest to your current balance and use that lot size as your starting point.
- As your balance genuinely grows (not just after one good trade, over time), move up the table.
- If your balance drops, move back down the table too. Lot size should always reflect what's actually in the account right now, not what it used to be.

**Important:** this is a general, conservative starting guideline for people learning, not a guaranteed-safe number, and not financial advice. Market conditions, volatility, and your own risk tolerance all matter too. When following a signal that specifies its own volume, follow the signal's guidance rather than this table.

[[FDIAGRAM:3]]

### Practicing with a Demo Account

A demo account is a practice trading account, it uses fake money on real, live market prices. It's a genuinely useful way to build confidence before risking real money.

- **Most brokers offer one for free**, including the broker linked in onboarding. Check their app or website for a "demo" or "practice account" option.
- **A demo account behaves exactly like a real one**, same prices, same platform, same order types, the only difference is the money isn't real.
- **Good uses for a demo account:** getting comfortable with the MT5 interface, practicing placing and closing trades, and following a few signals to see how the process feels, all without any financial risk.
- **It won't replicate everything.** Demo trading can't fully replicate the emotional side of real trading, since there's no real money on the line, but it's still a smart way to build familiarity with the platform.

If you're ever unsure about a step in MT5, practicing it on a demo account first is a safe way to get comfortable before doing it for real.

### Buy vs Sell

The two directions every trade can go.

- **Buy**: you're betting the price will go up. You profit if it rises after you enter.
- **Sell**: you're betting the price will go down. You profit if it falls after you enter.

Every signal you follow will tell you which one to use, matching the Bullish/Bearish direction from the last lesson: Bullish means Buy, Bearish means Sell.

### Reading the Numbers Before You Enter

When you open a pair on MT5 to copy a signal, you'll see two prices sitting next to each other, this is the part that confuses almost everyone at first.

**Bid and Ask**
- The lower number (usually on the left, often shown in red) is the **Bid**, the price you'd get if you sold right now.
- The higher number (usually on the right, often shown in blue) is the **Ask**, the price you'd pay if you bought right now.
- The small gap between them is called the **spread**, this is normal and exists on every pair.

**Matching the signal to the screen**
- If the signal says **Buy**, you're looking at the Ask price (the buy price), tap "Buy by Market" and it fills at whatever the Ask shows at that moment.
- If the signal says **Sell**, you're looking at the Bid price (the sell price), tap "Sell by Market" and it fills at whatever the Bid shows at that moment.
- The exact number will move slightly between when the signal was sent and when you enter, that's completely normal. A few pips of difference isn't a problem; a huge difference (during major news, for example) is worth pausing on rather than entering blindly.

**When to enter**
- For signals marked as an instant entry (Market Execution), enter as soon as you reasonably can after receiving it, the faster you follow, the closer your entry is to the original signal price.
- If a signal gives a price range instead of "now," that's telling you it's fine to enter anywhere within that range, not that you need to hit the exact number.
- If you're ever unsure whether a number on your screen matches what the signal is asking for, it's better to ask before entering than to guess.

[[FDIAGRAM:4]]

### Placing Your First Trade

Now that you know Buy vs Sell and Lot Size, here's how they come together on screen.

When you open a trade in MT5, you'll set your Volume (this is your lot size) and then tap either Buy or Sell. That's genuinely the whole mechanic, everything else in trading is about *when* and *why* you make that choice.

We'll walk through the exact screens step by step in the Using MT5 section shortly, this lesson is just the concept: Volume + Buy/Sell = a trade.

### Setting TP & SL

1. Go to the Trade tab and tap on an open trade.
2. Look for the option to edit the trade, this is where you add Take Profit and Stop Loss levels. *[Insert Screenshot 3: Setting TP/SL]*
3. Enter the TP and SL prices. When you're following a signal, these numbers are given to you directly, you don't need to work them out yourself.
4. Confirm the changes. Your trade will now close automatically at either level, whichever is hit first.

### Closing a Trade

1. Go to the Trade tab. *[Insert Screenshot 4: Closing a trade]*
2. Press and hold (or tap, depending on your device) the trade you want to close.
3. Tap Close to close it manually, useful if you want to exit before your TP or SL is hit.
4. Go to the History tab to see your closed trades and their results. *[Insert Screenshot 5: Trade history]*

### How to Read a Signal

When you receive a signal in the Telegram group, it will typically include:

- **Pair**, which currency pair to trade (e.g. EUR/USD)
- **Direction**, Buy or Sell
- **Entry**, the price to enter at (or "Market" if entering immediately)
- **TP**, where to set your take profit
- **SL**, where to set your stop loss

Always double check you're entering the correct direction (Buy vs Sell) and the correct pair before placing anything.

### Copying a Trade from Telegram

Putting it all together, here's the full process from signal to open trade:

1. Read the signal message carefully: note the pair, direction (Buy/Sell), TP, and SL.
2. Open MT5 and find the matching pair in your watchlist (add it first if it's not there yet, covered in Placing Your First Trade).
3. Open the trade ticket, set your volume, and choose the matching direction (Buy or Sell).
4. Enter the TP and SL exactly as given in the signal.
5. Confirm the trade is open and double check it matches the signal, right pair, right direction, right TP/SL.

That's the whole loop, this is exactly what you'll do every time a new signal comes through.

### Your Pre-Trade Checklist

Before you tap confirm on any trade, run through this quickly. Once it's second nature, it takes seconds, but early on, actually pause and check each one.

1. **Right pair?** Double check the full name matches the signal.
2. **Right direction?** Buy matches Buy, Sell matches Sell.
3. **Right volume?** Confirm it says 0.01 (or whatever you intend), not 1.0.
4. **TP and SL both set?** Never confirm a trade with either one missing.
5. **Does the price roughly match the signal?** A few pips off is normal; a big gap means pause and check what happened.
6. **After confirming, did it actually open?** Glance at the Trade tab to be sure.

If all six check out, you're good. If anything feels off at any point, it's always better to stop and ask than to guess.

## SECTION 2: Terminology

### Candles & Wicks

You'll hear "candle" and "wick" constantly in the community and in signal chat, here's what they mean, kept as simple as possible.

**What a candle is**
- A candle is just a little picture of what price did over a short period of time (like 1 hour).
- Every candle is either **green** or **red**.
- **Green means price went up** during that time, a "buy" type candle.
- **Red means price went down** during that time, a "sell" type candle.

[[FDIAGRAM:5]]

**What a wick is**
- The thick block in the middle is the **body**.
- The thin line sticking out the top or bottom is the **wick**. It shows price briefly went there, but didn't stay.

**"Wicking out" / "got wicked"**
- You'll often hear things like *"moving stop loss to avoid being wicked out"* or *"we got wicked out here then reversed."*
- This means: price briefly touched a level (often someone's SL or TP, or a key chart level) via the wick, but didn't stay there, it reversed straight back afterward.
- If your SL or TP is exactly *at* a level, a brief wick through it can still close your trade, even if price immediately reverses right after. This is just how trading works, not a glitch, it's part of why some traders leave a small buffer around obvious levels rather than placing SL/TP exactly on them.

This is genuinely a deep topic, full candlestick patterns and reading exactly what different wick shapes mean are covered in the Advanced Chart Reading course. The basics above are enough to follow along in the community without confusion.

### Bulls vs Bears: What They Mean

Here's an important pair of words you'll see everywhere, in MT5, in our signals, and in this course.

- A "bull" market means prices are expected to rise. A "bullish" trade is a Buy, you're betting the price goes up from here.
- A "bear" market means prices are expected to fall. A "bearish" trade is a Sell, you're betting the price goes down from here.
- These words describe a *direction*, not a mood. When a signal or a trader says they're "bullish on gold," it simply means they expect the price to rise, so the trade is a Buy. "Bearish" means the opposite: expecting the price to fall, so the trade is a Sell.
- You'll see these terms everywhere in trading: a "bullish candle," a "bearish signal," a "bull run." They all point back to this same simple idea: bullish means up, bearish means down.

[[FDIAGRAM:1]]

### What Is a Pip?

A pip is the smallest standard price movement for a currency pair. It's how price movement is measured, similar to how a centimeter measures distance.

You'll see "pips" used constantly in trading, "the price moved 20 pips," "the stop loss is 15 pips away." It's just a consistent unit for describing how far price has moved, regardless of which pair you're looking at.

### Leverage

Leverage lets you control a larger position than your account balance alone would allow.

Higher leverage means both potential gains and potential losses are magnified, so it's something to use carefully rather than avoid entirely. As a beginner sticking to small lot sizes (see the Lot Size lesson), leverage isn't something you need to actively manage yet, it's more useful to understand the concept than to calculate it yourself early on.

### Margin

Margin is the amount of your own money "locked" as a deposit to open a leveraged trade.

It isn't money you've lost, it's held against your open position and released back to you once the trade closes. Running low on available margin (because too many trades are open at once) is one way accounts get into trouble, which is part of why starting with small lot sizes matters.

### Buy by Market / Sell by Market

These are the actual buttons you'll tap in MT5 to open a trade instantly at the current live price.

- **"Buy by Market"** opens a buy trade right now, at whatever the current price is.
- **"Sell by Market"** opens a sell trade right now, at whatever the current price is.

This is different from a "limit" order, which waits for a specific price before entering, covered later in Different Order Types.

### Different Order Types

So far we've covered "Market Execution", placing a trade instantly at the current price. MT5 also offers other order types for more advanced use:

- **Market Execution**, opens the trade immediately at the current price. This is what we use for following signals.
- **Buy Limit / Sell Limit**, queues an order to open automatically if the price drops to (Buy Limit) or rises to (Sell Limit) a level you set, rather than entering right now.
- **Buy Stop / Sell Stop**, queues an order to open once the price breaks past a certain level, often used to catch a move already in motion.

For following Inner Circle signals, Market Execution is all you'll typically need, the other types are worth knowing exist, but not something you need to use day-to-day.

### Drawdown

Drawdown is how far your account balance has dropped from its peak, usually shown as a percentage or dollar amount.

For example, if your account went from $1,000 up to $1,200, then back down to $1,080, that's a $120 (10%) drawdown from the peak. It's a way of measuring how much a losing streak has cost you, not just whether you're up or down overall, and it's a useful number to keep an eye on as you trade more.

### Retrace (Retracement)

A retrace, or retracement, is a temporary pull-back in price against the main direction it's moving, before continuing the same way.

For example, if gold is generally rising but dips briefly before climbing further, that dip is a retracement. Retraces are normal and don't necessarily mean a trend has reversed, but they can also trigger a Stop Loss if it's set too tight, so it's something to be aware of once you start managing trades yourself.

### A Few Handy MT5 Features

Once you're comfortable with the basics, a few built-in MT5 features are worth knowing:

- **Price Alerts**, under a pair's settings, you can set an alert for a specific price. MT5 will notify you when it's hit, so you don't have to watch the screen constantly.
- **Depth of Market**, shows live buy/sell orders sitting above and below the current price. More of an advanced feature, but useful once you're comfortable with the basics.
- **One-Click Trading**, an optional setting that lets Buy/Sell buttons execute instantly without a confirmation step. Convenient once you're experienced, but risky while still learning, an accidental tap becomes a real trade. We'd suggest leaving this off until you're confident with the basics.
- **Economic Calendar**, built into MT5, shows upcoming news events that can move the market. Worth a glance before placing trades, especially around major news times.

None of these are required to follow signals successfully, they're just useful once you want to go a bit deeper.

### Types of Traders

Not all trading looks the same. Three common styles:

- **Scalper**, takes small, quick profits, often closing trades within minutes to a couple of hours. This is closest to what we do with Inner Circle signals.
- **Intraday trader**, opens and closes positions within the same day, aiming for slightly bigger moves than a scalper.
- **Swing trader**, holds trades for days or even weeks, waiting for larger price moves. Requires more patience and is generally better suited to more experienced traders.

Inner Circle signals are built around the scalping style, quick, focused trades rather than long holds. Knowing this helps set the right expectations: you're not meant to hold a trade for a week waiting on it.

## SECTION 3: Advancing Your Trade

### Trailing Stop Loss

A trailing stop is a Stop Loss that automatically moves along with the price as a trade becomes more profitable, rather than staying fixed at the original level.

- As price moves in your favor, the trailing stop follows behind it at a set distance, locking in more profit as it goes.
- If price then reverses, the trailing stop stays where it last moved to, protecting the profit already gained rather than giving it all back.
- This is a way to let a winning trade keep running without needing to manually manage it every few minutes.

### Breakeven & Trailing as a Trade Progresses

A common way to manage a trade once it's in profit:

1. **Move SL to breakeven**, once the trade has moved a reasonable amount in your favor, move your Stop Loss to your original entry price. Now, even if the trade reverses, you can't lose money on it, the worst case is closing at breakeven.
2. **Start trailing from there**, once at breakeven, you can either leave it as is or begin trailing the SL further to lock in more of the growing profit as the trade continues to move favorably.

This combination is a popular way to protect gains on a winning trade while still leaving room for it to run further.

### Taking Partial Profits

Rather than closing a whole trade at once, you can close it in pieces:

- **Close half at your first target, let the rest run**, secures some profit immediately while leaving the remainder open for a bigger move.
- **Close a small portion at multiple levels**, e.g. a third at each of three targets, spreading your exit across several price points instead of guessing one.
- Taking partials is a way to reduce the all-or-nothing feeling of a single exit point, you lock something in even if the rest of the trade doesn't go as planned.

### Multiple Entries (Layering a Position)

Instead of entering a trade all in one go, some traders split their intended position into several smaller entries across a price range.

- This spreads your entry price across a range rather than betting everything on one exact price.
- It can smooth out the impact of short-term price noise around your entry.
- The trade-off: it takes more attention to manage several entries at once compared to a single trade, and total position size across all entries still needs to respect your overall risk per trade (see Lesson 4.2b).

### Averaging Into a Better Price

If you're looking to buy, and price dips lower after your first entry, some traders add a further entry at the lower price, and the same in reverse for a sell, adding at a higher price if it rises further.

- The goal is a better average entry price across the whole position, not chasing a single perfect number.
- This is different from "averaging down" on a losing trade out of hope it recovers, that's a mindset trap covered in Trading Mindset Basics. The distinction is whether the additional entry is part of a planned range from the start, or a reaction to a trade going against you.
- Like layering, this needs a clear plan in advance, decide your entry range and total position size before you start, not trade by trade as price moves.

### TP Open: Letting a Trade Run

Sometimes a trade won't have one final fixed Take Profit, instead it's left "open," meaning the plan is to let it run as far as the move continues, managed with a trailing stop rather than a hard exit point.

- This suits strong, clearly trending moves where cutting off profit at an arbitrary number would leave a lot on the table.
- It requires more active management than a simple fixed TP, you're relying on your trailing stop and judgement rather than a single number doing the work for you.
- Combining this with taking partials (Lesson 5.6) is common, secure some profit early, then let the remainder run with an open target.

### Liquidity

"Liquidity" refers to areas on the chart where a large number of buy or sell orders are sitting, often near obvious highs, lows, or round numbers. Price is often drawn toward these areas because there's enough volume there to fill large orders.

- Price often moves toward liquidity before reversing, this is sometimes why a trade dips just past an obvious level before turning in the expected direction.
- Recognizing where liquidity likely sits (recent swing highs/lows are common spots) can help explain price moves that otherwise look random.

This is genuinely one of the deeper topics in trading, the basics above are enough to recognize the pattern when it happens. The Advanced Chart Reading course goes further into how liquidity is actually targeted and used to time entries.

### Volatility

Volatility is how much and how fast price is moving, in either direction.

- **High volatility**, large, fast price swings. Common around major news releases. Bigger opportunity, but also bigger risk of being caught on the wrong side quickly.
- **Low volatility**, smaller, slower price movement. Calmer, more predictable, but moves take longer to develop.
- Being aware of current volatility helps set expectations, the same lot size carries very different risk in a high-volatility moment versus a quiet one.

### Funded Accounts (Prop Firms)

Once you're comfortable trading with your own money, some traders look into "funded accounts" as a way to trade with more capital than they personally have.

**How it generally works:**
- You pay a fee to attempt a "challenge", a set of trading targets and rules (e.g. reach a profit target without exceeding a maximum loss limit) on a demo account.
- If you pass, the firm gives you access to a funded account, often demo funds that mirror a live account, and you trade it under their rules.
- If you're profitable, you keep a share of the profits (commonly 70–90%, depending on the firm).

**Things worth knowing before considering one:**
- **It's not free money.** You're paying an upfront fee for the attempt, and most attempts don't pass the challenge.
- **Rules are strict.** Daily loss limits and overall drawdown limits are enforced automatically, breaking them fails the challenge, even if the trade would have recovered.
- **Payouts aren't instant.** Profit is usually paid out on a schedule (e.g. every two weeks), not withdrawn immediately like a personal live account.
- **The size looks bigger than it is.** A $100,000 funded account with a 5% daily loss limit effectively behaves like a $5,000 account risk-wise, trade it with that same discipline, not like you actually have $100,000 to play with.

This isn't something to jump into as a beginner, it's worth considering only once you're consistently comfortable with the fundamentals covered in this course. If you do look into it, research the firm's reputation and rules carefully before paying for a challenge.

## SECTION 4: Trading Mindset

### Trading Mindset Basics

Strategy gets a trade started, but mindset is what determines whether you stick to the plan or blow it up halfway through. Most trading mistakes aren't caused by a bad signal, they're caused by an emotional reaction to a signal that was actually fine.

A few mindset principles to hold onto:

- **You will lose trades. That's normal, not a sign something's broken.** Even a good strategy loses sometimes, what matters is the outcome over many trades, not any single one.
- **Boring is good.** The best trading often feels repetitive and unexciting. If it feels thrilling, that's usually a warning sign, not a good sign.
- **Your job is to follow the process, not to predict the future.** Nobody, no trader, no signal provider, gets it right every time. Consistency in following the plan is what actually compounds over time.

### The Dangers of Over-Leveraging

Leverage lets you control a larger position than your account balance alone would allow. It's tempting because it can multiply gains, but it multiplies losses exactly the same way, and that's the part beginners underestimate.

- **Higher leverage = smaller price moves can wipe out your account.** A move that would be a minor dip at low leverage can trigger a margin call at high leverage.
- **"I'll just use more leverage to make up for a small account" is one of the fastest ways to lose the whole account.** It doesn't fix a small account, it makes a small account riskier.
- **Stick to the lot sizes and risk levels you've been taught here (0.01 lots while learning).** Sizing up should happen slowly, with experience, never as a reaction to a loss.

If you ever feel the urge to "size up to win it back," that's the exact moment to stop and step away instead.

### Common Trading Emotions

A few patterns come up again and again, learning to spot them in yourself is half the battle.

- **FOMO (Fear Of Missing Out)**, jumping into a trade late because "everyone else is in it" or the price already moved. This usually means entering at a worse price with a worse risk setup than the original signal had.
- **Revenge trading**, trying to immediately win back a loss with a bigger, rushed trade. This is one of the most common ways small losses turn into big ones.
- **Overconfidence after a win streak**, a few wins in a row can make risk limits feel unnecessary. They're not, they matter most right after things have been going well.
- **Hesitation from fear**, after a loss, some people freeze and miss good, valid setups out of fear alone.

None of these make you a bad trader, they're just normal human reactions. Recognizing them in the moment is what separates people who improve from people who repeat the same mistakes.

### Building Discipline

A few habits that build long-term discipline:

- **Set your TP and SL and leave them alone.** Constantly adjusting them mid-trade based on emotion defeats their purpose.
- **Keep a simple trade journal.** Note what you entered, why, and the outcome. Patterns become obvious once you can see them written down.
- **Take a break after a loss, don't immediately jump into another trade.** A short pause prevents revenge trading before it starts.
- **Review, don't just react.** At the end of each week, look back at your trades as a whole rather than reacting to each one individually.

Discipline isn't about never feeling tempted, it's about having a plan for those moments before they happen.

## SECTION 5: More to Know

### Understanding Risk

Trading always carries risk, no signal or strategy wins 100% of the time, and that's completely normal. Here's how we manage it:

- **Only risk small, fixed amounts per trade.** This is why we practice with 0.01 lots, it keeps any single trade's impact small.
- **Never trade with money you can't afford to lose.** Only trade what's genuinely spare.
- **A losing trade isn't a failure.** It's a normal part of trading, the goal is consistency over time, not winning every single trade.
- **Stick to your TP and SL.** They exist precisely so a bad moment doesn't turn into a bad month.

*(Optional: add a short video here later walking through these points in your own words, not required to publish this lesson.)*

### Common Beginner Mistakes

These are the mistakes that trip up almost everyone at least once, knowing them ahead of time means you won't be the one making them.

- **Typing 1.0 instead of 0.01 for volume.** This is the single biggest one, it's a 100x sizing mistake, not a small one. Always double check the volume field before confirming, especially since some phones default the field to a round number.
- **Mixing up Buy and Sell.** Easy to do when moving fast. Always re-read the signal's direction one more time right before you tap.
- **Trading the wrong pair.** Pairs can look similar at a glance (e.g. EURUSD vs EURGBP), check the full pair name in the trade ticket before confirming.
- **Forgetting to set SL.** Never leave a trade with no Stop Loss "just this once." That's exactly how a small mistake turns into a large loss.
- **Not checking the trade after placing it.** Always glance at the Trade tab right after entering to confirm it actually opened correctly, right pair, right direction, right volume.
- **Panicking and closing early out of nerves.** If you've set TP and SL correctly, the trade is already protected, constantly watching and closing early defeats the purpose of setting them.

### Market Hours

Forex and gold markets aren't open all the time, this catches beginners off guard more than you'd think.

- **Forex and Gold (XAUUSD) trade Monday to Friday**, closing over the weekend. You generally can't open new trades on Saturday or Sunday.
- **The exact open/close times depend on your broker and time zone**, check your broker's app for their specific schedule if you're unsure.
- **Trades left open over the weekend stay open**, they just won't move until the market reopens Monday, at which point the price can occasionally "gap" up or down from where it closed Friday.
- **Liquidity is lower right at market open and close.** Prices can move more erratically in these windows, many traders prefer to avoid entering new trades in the first and last few minutes of the trading day.

### If Something Goes Wrong

A few situations you might run into, and what they mean:

- **"Not enough money" / "insufficient margin" error**, your account doesn't have enough available balance to open a trade at that volume. Lower the volume or check your balance.
- **Order rejected / requote**, the price moved between when you tapped and when MT5 tried to execute. Usually you can just try again; prices shift constantly in fast markets.
- **You opened the wrong trade by mistake**, don't panic. Go to the Trade tab, find it, and close it the same way you'd close any trade. A quick mistake caught immediately is a minor loss, not a disaster.
- **The app shows "Market Closed"**, you're likely trying to trade outside market hours (see Lesson 2.7). Wait until the market reopens.
- **You're not sure if a trade actually went through**, go to the Trade tab. If it's listed there, it's open. If you don't see it, check the History tab, and if you still can't find it, it probably didn't execute.

If anything ever looks genuinely wrong or you're unsure what happened, message @Innercircleverifybot or use the chat button on the site, better to ask than to guess with real money on the line.

### Doing Your Own Chart Analysis

Everything so far has been about following signals well. The next step for anyone who wants to go further is learning to read charts and form your own view of the market, rather than relying only on signals.

This is a big enough topic to be its own dedicated section, reading candlesticks, spotting trends, support and resistance, and the other basics of chart analysis. Consider this lesson the bridge: once you're confident with everything in Sections 1–5, chart reading is the natural next step.

Ready to go further? The **Advanced Chart Reading & Technical Analysis** course picks up exactly here, candlesticks, chart patterns, and building your own strategy.

"""

ADVANCED_MD = """## Disclaimer

This course is educational content only and is not financial advice. Nothing in this course is a signal, a recommendation, or a guarantee of results.

Any examples, walkthroughs, or worked strategies shown in this course are illustrative, built to demonstrate how concepts fit together, and are not verified backtests against real historical data unless explicitly stated otherwise. Treat them as teaching examples, not as signals to copy directly.

Trading carries risk, and past performance is never a guarantee of future results. No strategy, system, or signal provider wins on every trade. You are responsible for your own trading decisions, always do your own research and never risk money you can't afford to lose.

## SECTION 1: Understanding Candlesticks

### What a Candlestick Shows

Every candlestick on a chart represents price movement over a fixed period of time (e.g. one candle = one hour, on an hourly chart).

Each candle shows four prices:
- **Open**, the price when the candle started.
- **Close**, the price when the candle ended.
- **High**, the highest price reached during that period.
- **Low**, the lowest price reached during that period.

**The body** is the thick part of the candle, between the open and close.
**The wicks (or shadows)** are the thin lines above and below the body, showing the high and low.

A candle tells you a small story: where price started, where it ended, and how far it stretched in each direction along the way.

[[DIAGRAM:1]]

### Bullish vs Bearish Candles

- **Bullish candle**, close is higher than open (price rose over that period). Usually shown in green or white.
- **Bearish candle**, close is lower than open (price fell over that period). Usually shown in red or black.

**Long bodies** suggest strong, decisive movement in that direction. **Short bodies** suggest indecision or a quieter period. **Long wicks** show that price reached further in that direction before being pushed back, a sign of rejection at that level.

### Common Single Candlestick Patterns

A few individual candle shapes worth recognizing:

- **Doji**, open and close are almost identical, leaving a tiny body with wicks on either side. Signals indecision, neither buyers nor sellers won that period.
- **Hammer**, small body near the top, with a long lower wick. Often appears after a downtrend, suggesting sellers pushed price down but buyers pushed it back up, a possible reversal signal.
- **Shooting Star**, small body near the bottom, with a long upper wick. Often appears after an uptrend, suggesting buyers pushed price up but sellers pushed it back down, a possible reversal signal.
- **Marubozu**, a candle with little to no wick, just a strong body. Shows strong, one-directional conviction with barely any pushback.

No single candle should be traded in isolation, context (where it appears on the chart) matters as much as the shape itself.

[[DIAGRAM:2]]

### A Few More Candlestick Patterns

A handful of other shapes worth recognizing, beyond the core four:

- **Spinning Top**, small body with long wicks on both sides. Shows a real tug-of-war between buyers and sellers where neither side won, similar to a Doji but with a slightly bigger body.
- **Inverted Hammer**, small body near the bottom, long upper wick, appearing after a downtrend. A potential early reversal sign, similar in idea to a Hammer but shaped the other way up.
- **Hanging Man**, small body near the top, long lower wick, appearing after an uptrend. Looks identical to a Hammer shape-wise, but the context (after a rise, not a fall) changes what it might mean, a possible warning sign that selling pressure is creeping in.
- **Three White Soldiers**, three strong bullish candles in a row, each closing higher than the last. A sign of strong, sustained buying momentum.
- **Three Black Crows**, the mirror image: three strong bearish candles in a row. A sign of strong, sustained selling pressure.

As with every pattern in this course, these carry more weight when they show up at a meaningful level on the chart (like a support or resistance zone) rather than randomly in the middle of a range.

[[DIAGRAM:9]]

### Common Multi-Candle Patterns

Patterns formed by two or more candles together:

- **Bullish Engulfing**, a small bearish candle followed by a larger bullish candle that fully "engulfs" it. Suggests buyers have taken control after sellers had it.
- **Bearish Engulfing**, the mirror image: a small bullish candle followed by a larger bearish candle engulfing it. Suggests sellers have taken control.
- **Morning Star**, a three-candle pattern: a bearish candle, then a small indecisive candle, then a strong bullish candle. A potential bottom/reversal signal.
- **Evening Star**, the mirror image, potentially signaling a top/reversal after an uptrend.

Like single candles, these patterns are more meaningful when they appear at a relevant level on the chart, e.g. at a known support or resistance zone, rather than in the middle of nowhere.

## SECTION 2: Reading Price Movement

### Trends

Price generally moves in one of three ways:

- **Uptrend**, price is making higher highs and higher lows over time.
- **Downtrend**, price is making lower highs and lower lows over time.
- **Ranging (sideways) market**, price is moving between a fairly consistent high and low, without a clear overall direction.

Identifying which of these you're in matters a lot, strategies that work well in a trending market often perform poorly in a ranging one, and vice versa.

### Higher Highs, Higher Lows (and the Reverse)

This is how trends are actually identified on a chart, not just described in words:

- **Uptrend structure:** each swing high is higher than the last, and each swing low is higher than the last (Higher Highs, Higher Lows).
- **Downtrend structure:** each swing high is lower than the last, and each swing low is lower than the last (Lower Highs, Lower Lows).
- **A change in this pattern**, e.g. an uptrend suddenly making a lower low, is often one of the earliest visual signs that a trend may be shifting.

Training your eye to spot this structure is one of the most useful chart-reading skills there is.

[[DIAGRAM:5]]

### Break of Structure

A Break of Structure (often shortened to BOS) is when price breaks past a previous swing high or low in a way that suggests the trend itself may be shifting.

- In an uptrend (Higher Highs, Higher Lows), a break of structure happens when price makes a **lower low**, breaking the pattern that defined the uptrend.
- In a downtrend, it's the reverse: a break of structure happens when price makes a **higher high**.
- A break of structure doesn't guarantee a full trend reversal, sometimes it's just a deeper pullback, but it's one of the earliest visual warnings that the current trend may be losing control.
- Some traders wait for a confirmed break of structure before considering trades in the new direction, rather than trying to guess a reversal before it's actually shown on the chart.

### Trend Lines

A trend line is a simple line drawn directly on the chart connecting a series of highs or lows, making a trend visible at a glance rather than just implied by reading candle by candle.

- **In an uptrend**, draw a line connecting the swing lows, price tends to bounce off this rising line repeatedly.
- **In a downtrend**, draw a line connecting the swing highs, price tends to reject off this falling line repeatedly.
- The more times price has respected a trend line without breaking it, the more traders tend to watch that line, and the more significant a break of it tends to be.
- A trend line break doesn't automatically mean a reversal (similar to a Break of Structure), but it's another early signal worth paying attention to, especially when it lines up with other confluence like a support/resistance level or a candlestick confirmation.

### Support and Resistance

- **Support**, a price level where price has historically stopped falling and bounced back up. Think of it as a "floor."
- **Resistance**, a price level where price has historically stopped rising and turned back down. Think of it as a "ceiling."

These levels aren't exact laser lines, think of them as zones. Price often reacts around these areas, though not perfectly every time. Once a resistance level is broken through with strength, it can often flip and become support going forward (and vice versa), this is one of the more reliable patterns in price action.

[[DIAGRAM:4]]

### Supply and Demand

Supply and demand zones are close cousins of support and resistance, but thought of as areas of imbalance rather than a single reactive price.

- **A demand zone** is where strong buying previously overwhelmed selling, often visible as a sharp, fast move up away from that area. Price often returns to that same zone later looking for buyers again.
- **A supply zone** is the mirror image, where strong selling overwhelmed buying, and price later returns looking for sellers again.
- The key difference from support/resistance: supply and demand zones are drawn as a *range* (a box covering the area the sharp move started from), not a single line. Support/Resistance is usually about a specific price being tested repeatedly; supply/demand is about the origin of a strong move.
- Many traders use both together, a demand zone that also lines up with a support level is a stronger case than either alone, this is the same "confluence" idea covered in the Example Walkthrough.

### Liquidity

Section 3's free course covers the basics, this lesson goes further into how liquidity is actually used.

**Buy-side vs Sell-side liquidity**
- **Buy-side liquidity** sits above recent highs, resting sell-stop and breakout-buy orders cluster there.
- **Sell-side liquidity** sits below recent lows, resting buy-stop and breakout-sell orders cluster there.
- Price is statistically drawn toward these pools because there's enough resting volume to fill large orders without moving the market too much.

**Equal highs and equal lows**
- When price tests the same high (or low) more than once without breaking it, that level often has a larger-than-usual pool of stop orders sitting just beyond it.
- These "equal highs/lows" are a common target, price often pushes just past them to trigger those stops before reversing.

**Wicks, "wicking out," and sweeps**
- A candle's wick shows price that was reached but not held, the candle closed back away from it (covered in What a Candlestick Shows).
- When traders say a level "got wicked" or price "wicked out" at a level, they mean exactly this: the wick touched or pushed slightly past the level, but the candle didn't close there, price reversed back.
- A liquidity sweep is essentially this same thing with a specific cause behind it, see below.

**Liquidity sweeps (or "stop hunts")**
- A liquidity sweep is when price pushes briefly beyond an obvious level, triggers the resting stop orders sitting there, and then reverses sharply back the other way.
- This is often mistaken for "the market being unfair" when really it's a structural feature: large orders need liquidity to fill into, and obvious levels are where that liquidity sits.
- Recognizing a sweep after it happens (a sharp wick beyond a level, followed by a strong reversal candle) can be a higher-probability entry signal than trying to catch the level exactly.

**How this connects to your strategy**
- Instead of placing a Stop Loss exactly at an obvious swing high/low (where a sweep would hit it), some traders place it slightly further away, anticipating the sweep, so a normal stop-hunt doesn't take them out before the real move happens.
- This isn't about predicting every sweep, it's about being aware that obvious levels attract this behavior, and building a small buffer into your risk management around them.

**A note on caution**
This is a genuinely debated area of trading education, not every price move beyond a level is a deliberate "hunt," sometimes it's simply normal volatility. Treat liquidity concepts as one more piece of context, not a guaranteed signal on their own.

### Breakouts vs Liquidity Sweeps

When price pushes through a level, there are really only two outcomes: it keeps going (a genuine breakout), or it reverses back (a liquidity sweep, covered in Liquidity). Telling the two apart in the moment is one of the most useful skills in reading price action, here's how market structure and price action combine to help.

**Check market structure first**
- A genuine breakout usually comes with a Break of Structure, price doesn't just poke past the level, it goes on to make a new higher high (in an uptrend) or lower low (in a downtrend), confirming the structure has actually shifted.
- A sweep typically does *not* break structure in a lasting way, price pokes past the level, fails to follow through, and structure snaps back to where it was.

**Check price action at the level**
- **Breakout signs**: a strong-bodied candle *closing* beyond the level (not just wicking through it), often with increased volume (Volume and What It Tells You), and follow-through on the next candle or two.
- **Sweep signs**: a long wick pushing beyond the level with the candle closing back *inside* it, little to no follow-through, and often a reversal candlestick pattern forming right at the level (Common Single Candlestick Patterns).

**Putting it together**
- Wait for the candle to close, not just touch the level, before deciding which one you're looking at.
- If the close is beyond the level and structure confirms it, that's confluence for a breakout, consider trading with the new direction.
- If price wicks through, closes back inside, and structure holds, that's confluence for a sweep, consider trading the reversal instead, exactly as covered in Liquidity.
- Neither read is guaranteed, this is about stacking probability through confluence, not predicting the outcome with certainty, the same honest framing covered in Why No Strategy Wins Every Time.

### Fair Value Gaps (FVG)

A Fair Value Gap is a gap left in price on the chart where trading moved so fast that a "hole" was left behind, visible as a gap between candle wicks on a 3-candle sequence.

- Price often returns to "fill" these gaps before continuing in its original direction, since that price area effectively got skipped over.
- Some traders watch for price returning to a Fair Value Gap as a potential entry point, expecting the gap to act like a magnet before the move continues.
- This is a more advanced concept, it takes screen time and practice to start spotting these reliably on a chart.

### Fibonacci Levels

Fibonacci retracement levels are a set of horizontal lines based on a mathematical sequence, used to estimate where a pullback within a trend might find support (or resistance) before continuing.

- To use it, you draw the tool from a significant swing low to swing high (in an uptrend), and the platform automatically marks levels like 38.2%, 50%, and 61.8%, the most commonly watched retracement zones.
- The idea: after a strong move, price often pulls back to one of these levels before continuing in the original direction, rather than retracing all the way back to the start.
- The 50% and 61.8% levels are the two most widely watched by traders, they're often treated as a "reasonable pullback" zone worth watching for a confirmation entry, similar to how a support zone is used in the Example Walkthrough.
- Like every tool in this course, Fibonacci levels work best combined with something else, a fib level lining up with a support zone or a candlestick confirmation is a stronger case than a fib level alone.
- Different timeframes will show different Fibonacci levels for the same chart (since the swing high/low used to draw it changes), so be consistent about which timeframe you're drawing it from, this connects back to Timeframes and How They Interact.

### Volume and What It Tells You

Volume shows how much trading activity happened during a given candle, how many contracts or lots were traded.

- **High volume on a strong move** adds confidence that the move is genuine, backed by real participation.
- **A big price move on low volume** is worth treating with more suspicion, it may not be as strong or sustainable.
- **Volume spikes at support/resistance** can indicate a lot of interest at that level, which is worth paying attention to.

Not every platform's forex volume is a perfect measure (forex volume shows broker activity, not the entire global market), but it's still a useful extra piece of context.

[[DIAGRAM:7]]

### Timeframes and How They Interact

The same pair can look completely different depending on which timeframe you're viewing.

- **Higher timeframes** (e.g. Daily, 4-Hour) show the bigger picture, the overall trend and major levels.
- **Lower timeframes** (e.g. 15-Minute, 5-Minute) show fine detail, useful for precise entries.
- **A common approach:** check a higher timeframe first to understand the overall trend and key levels, then drop to a lower timeframe to time your actual entry within that bigger picture.

Trading purely off one timeframe without ever checking a higher one is one of the most common gaps in a beginner's analysis.

## SECTION 3: Building Your Own Strategy

### What Makes a Trading Strategy

A real strategy isn't a feeling, it's a written set of rules covering:

1. **Entry rules**, exactly what has to happen on the chart before you enter a trade.
2. **Exit rules**, exactly where your TP and SL go, decided before you enter, not adjusted emotionally afterward.
3. **Risk rules**, how much you're risking per trade (see the Lot Size by Account Balance lesson in Trading Fundamentals for a sizing guide).

If you can't write your strategy down in a few clear sentences, it isn't a strategy yet, it's a guess.

### Why No Strategy Wins Every Time

This needs to be said plainly: **no strategy, system, or signal provider wins on every trade, anyone claiming otherwise isn't being straight with you.**

- A genuinely good strategy might win somewhere between 40-60% of the time, and still be very profitable, because the wins are managed to be bigger than the losses (a core risk-management principle covered in Trading Fundamentals).
- The goal of everything in this course isn't to eliminate losses. It's to build a repeatable process that's profitable *over many trades*, while managing each individual loss so it stays small and controlled.
- If a strategy or approach seems to be winning every single trade for a long stretch, be more suspicious, not more confident, markets don't work that way, and it usually means increasing risk is being hidden somewhere, or it's simply a lucky run that hasn't ended yet.

### Example Walkthrough: Combining Concepts on XAUUSD

*Reminder: as covered in the disclaimer, this is a teaching example showing how the concepts fit together, not a verified backtest or a setup to copy directly.*

**The idea: confluence.** A single tool on its own (just a candlestick, or just a support line) is weak by itself. A trade idea gets stronger when several things line up at once, this is called "confluence," and it's how most solid strategies are actually built.

**A worked example, step by step:**

1. **Check the higher timeframe trend.** Say the 4-hour chart shows XAUUSD in a clear uptrend, higher highs and higher lows, as covered in Higher Highs, Higher Lows. This tells us the overall bias: we're looking for buying opportunities, not selling ones.

2. **Wait for price to pull back to a key level.** Rather than buying at any random point, wait for price to retrace down into a known area, a support zone, a supply/demand zone, or a previous high or low, anywhere price has reacted before (Support and Resistance, Supply and Demand).

3. **Look for a candlestick confirmation at that zone.** Instead of buying the instant price touches the zone, wait for a bullish signal to actually form there, for example, a Hammer or a Bullish Engulfing candle (Common Single Candlestick Patterns and Common Multi-Candle Patterns). This is the market telling you buyers are stepping in at that level, not just a hope that it'll hold.

4. **Entry, SL, and TP:**
   - **Entry**, once the confirmation candle closes, enter on the next candle.
   - **SL**, placed just below the support zone / below the confirmation candle's low, so if the level genuinely fails, the loss is small and controlled.
   - **TP**, a prior resistance level or swing high, or managed with a trailing stop if the trend looks strong (see Trailing Stop Loss in Trading Fundamentals).

5. **Position size**, set using the lot-size guide from the Lot Size by Account Balance lesson in Trading Fundamentals, based on your account balance, not on how confident this particular setup feels.

**Why this is a reasonable example, not a guarantee:** three things had to line up, trend direction, a meaningful level, and a candlestick confirmation. That's confluence. It doesn't mean the trade wins; it means the trade had a logical, repeatable reason behind it rather than being a guess. Some setups like this will hit SL, some will hit TP, the point made in Why No Strategy Wins Every Time still applies here in full.

**Try this yourself:** open a XAUUSD chart, scroll back through recent history, and see if you can spot a few real moments where trend + a level + a candlestick pattern lined up like this. This is exactly what backtesting (covered next) looks like in practice.

[[DIAGRAM:6]]

### Backtesting Before Going Live

Before trusting a strategy with real money, test it against past price data first.

- **Backtesting** means going through historical charts and checking: would your entry rules have triggered here? Would your exit rules have worked out?
- This can be done manually (scrolling back through charts candle by candle) or with software, depending on how deep you want to go.
- A strategy that looks good backtested isn't a guarantee of future results, but a strategy that fails when backtested is a strong warning sign not to trade it live.

### Combining Technical Analysis With Signals

You don't have to choose between following signals and doing your own analysis, many experienced traders do both.

- Use your own chart reading to sanity-check a signal before entering, does the direction make sense given the trend and key levels you can see?
- Over time, this builds your own judgement, so you become less dependent on any single source and better able to manage trades yourself.
- If your own analysis and a signal disagree, that's not a problem to panic about, it's useful information. It might mean sitting that particular trade out, or reducing size on it, rather than blindly following either side.

### Keeping a Trading Journal to Refine Your Strategy

The fastest way to actually improve is to track what you're doing and review it honestly.

For each trade, note:
- The pair, direction, and why you entered (which rule from your strategy triggered it)
- Your TP, SL, and lot size
- The outcome, and afterward, did you follow your own rules, or deviate?

Reviewing this regularly (weekly is a good habit) reveals patterns you won't notice trade-by-trade, which setups actually work for you, and which habits are quietly costing you money.

"""


TICKET_SVG = """<svg viewBox="0 0 700 560" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="560" fill="#F1E8DA" rx="16"/>
  <text x="350" y="30" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">The Real Trade Ticket Screen</text>

  <rect x="160" y="50" width="380" height="470" rx="18" fill="#FAF6F0" stroke="#3B2E26" stroke-width="3"/>

  <!-- Header -->
  <text x="350" y="90" text-anchor="middle" font-size="15" font-weight="bold" fill="#3B2E26">EURUSD ⌄</text>
  <text x="350" y="108" text-anchor="middle" font-size="11" fill="#8A7563">Euro vs US Dollar</text>
  <line x1="180" y1="122" x2="520" y2="122" stroke="#E4D6C3"/>

  <text x="196" y="146" font-size="12" fill="#3B2E26">Market Execution ⌄</text>
  <line x1="180" y1="160" x2="520" y2="160" stroke="#E4D6C3"/>

  <!-- Volume quick-select row -->
  <text x="220" y="190" text-anchor="middle" font-size="11" fill="#9C7A4E">-0.5</text>
  <text x="280" y="190" text-anchor="middle" font-size="11" fill="#9C7A4E">-0.1</text>
  <text x="350" y="190" text-anchor="middle" font-size="13" font-weight="bold" fill="#3B2E26">0.01</text>
  <text x="420" y="190" text-anchor="middle" font-size="11" fill="#9C7A4E">+0.1</text>
  <text x="480" y="190" text-anchor="middle" font-size="11" fill="#9C7A4E">+0.5</text>
  <circle cx="350" cy="184" r="14" fill="none" stroke="#9C5B52" stroke-width="2"/>
  <text x="350" y="189" text-anchor="middle" font-size="13" fill="#9C5B52" font-weight="bold">①</text>
  <line x1="180" y1="206" x2="520" y2="206" stroke="#E4D6C3"/>

  <!-- SL / TP -->
  <text x="196" y="232" font-size="12" fill="#3B2E26">Stop Loss</text>
  <text x="480" y="232" text-anchor="middle" font-size="11" fill="#8A7563">not set</text>
  <line x1="180" y1="246" x2="520" y2="246" stroke="#E4D6C3"/>
  <text x="196" y="270" font-size="12" fill="#3B2E26">Take Profit</text>
  <text x="480" y="270" text-anchor="middle" font-size="11" fill="#8A7563">not set</text>
  <line x1="180" y1="284" x2="520" y2="284" stroke="#E4D6C3"/>

  <text x="196" y="308" font-size="12" fill="#3B2E26">Fill Policy</text>
  <text x="480" y="308" text-anchor="middle" font-size="11" fill="#8A7563">Fill or Kill</text>

  <!-- Prices -->
  <rect x="180" y="330" width="340" height="52" fill="#EBDFCC"/>
  <text x="270" y="362" text-anchor="middle" font-size="17" font-weight="bold" fill="#9C5B52">1.15704</text>
  <text x="430" y="362" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B5A9C">1.15707</text>

  <!-- Sell / Buy buttons, real colours -->
  <rect x="180" y="382" width="170" height="54" fill="#C0392B"/>
  <text x="265" y="415" text-anchor="middle" font-size="14" fill="#FAF6F0" font-weight="bold">Sell by Market</text>
  <rect x="350" y="382" width="170" height="54" fill="#2E5DB5"/>
  <text x="435" y="415" text-anchor="middle" font-size="14" fill="#FAF6F0" font-weight="bold">Buy by Market</text>

  <circle cx="265" cy="409" r="14" fill="none" stroke="#FAF6F0" stroke-width="2"/>
  <text x="265" y="414" text-anchor="middle" font-size="13" fill="#FAF6F0" font-weight="bold">②</text>

  <!-- Callouts -->
  <text x="90" y="188" font-size="12" fill="#3B2E26" text-anchor="end">① Middle number</text>
  <text x="90" y="204" font-size="12" fill="#8A7563" text-anchor="end">is your lot size</text>

  <text x="610" y="405" font-size="12" fill="#3B2E26">② Sell or Buy,</text>
  <text x="610" y="421" font-size="12" fill="#8A7563">taps instantly</text>

  <text x="350" y="540" text-anchor="middle" font-size="12" fill="#8A7563">Real MT5 layout, red = Sell, blue = Buy</text>
</svg>"""

CLOSE_SVG = """<svg viewBox="0 0 700 480" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="480" fill="#F1E8DA" rx="16"/>
  <text x="350" y="30" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">Closing a Trade in MT5</text>

  <rect x="200" y="50" width="300" height="400" rx="24" fill="#FAF6F0" stroke="#3B2E26" stroke-width="3"/>
  <rect x="200" y="50" width="300" height="42" rx="24" fill="#EBDFCC"/>
  <rect x="200" y="72" width="300" height="20" fill="#EBDFCC"/>
  <text x="222" y="78" font-size="13" fill="#3B2E26" font-weight="bold">Trade</text>

  <!-- Open positions list -->
  <rect x="215" y="104" width="270" height="46" rx="8" fill="#EBDFCC"/>
  <text x="230" y="124" font-size="11" fill="#3B2E26" font-weight="bold">EURUSD buy 0.01</text>
  <text x="230" y="140" font-size="10" fill="#8A7563">1.08508</text>
  <text x="460" y="130" text-anchor="end" font-size="12" fill="#5B7A5E" font-weight="bold">+0.42</text>

  <rect x="215" y="158" width="270" height="46" rx="8" fill="#EBDFCC"/>
  <text x="230" y="178" font-size="11" fill="#3B2E26" font-weight="bold">EURUSD sell 0.01</text>
  <text x="230" y="194" font-size="10" fill="#8A7563">1.08492</text>
  <text x="460" y="184" text-anchor="end" font-size="12" fill="#9C5B52" font-weight="bold">−0.18</text>

  <circle cx="350" cy="127" r="13" fill="none" stroke="#9C7A4E" stroke-width="2"/>
  <text x="350" y="132" text-anchor="middle" font-size="14" fill="#9C7A4E" font-weight="bold">①</text>

  <!-- Close confirmation sheet -->
  <rect x="222" y="230" width="256" height="120" rx="12" fill="#FAF6F0" stroke="#9C7A4E" stroke-width="2"/>
  <text x="350" y="256" text-anchor="middle" font-size="12" fill="#3B2E26" font-weight="bold">EURUSD buy 0.01</text>
  <rect x="242" y="272" width="216" height="42" rx="10" fill="#9C5B52"/>
  <text x="350" y="298" text-anchor="middle" font-size="14" fill="#FAF6F0" font-weight="bold">Close</text>
  <text x="350" y="332" text-anchor="middle" font-size="11" fill="#8A7563">Modify · Trade · Chart</text>

  <circle cx="350" cy="293" r="13" fill="none" stroke="#9C7A4E" stroke-width="2"/>
  <text x="350" y="298" text-anchor="middle" font-size="14" fill="#EBDFCC" font-weight="bold">②</text>

  <!-- Bottom tab bar -->
  <rect x="200" y="392" width="300" height="42" fill="#EBDFCC"/>
  <text x="235" y="416" text-anchor="middle" font-size="9" fill="#8A7563">Quotes</text>
  <text x="292" y="416" text-anchor="middle" font-size="9" fill="#8A7563">Chart</text>
  <text x="350" y="416" text-anchor="middle" font-size="9" fill="#9C7A4E" font-weight="bold">Trade</text>
  <text x="408" y="416" text-anchor="middle" font-size="9" fill="#8A7563">History</text>
  <text x="465" y="416" text-anchor="middle" font-size="9" fill="#8A7563">Settings</text>

  <text x="90" y="122" font-size="12" fill="#3B2E26" text-anchor="end">① Press & hold</text>
  <text x="90" y="138" font-size="12" fill="#8A7563" text-anchor="end">a trade row</text>

  <text x="610" y="290" font-size="12" fill="#3B2E26">② Tap Close</text>
  <text x="610" y="306" font-size="12" fill="#8A7563">to exit the trade</text>

  <text x="350" y="462" text-anchor="middle" font-size="12" fill="#8A7563">Repeat for all 10, closed trades move to your History tab</text>
</svg>"""

ADDPAIR_SVG = """<svg viewBox="0 0 700 480" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="480" fill="#F1E8DA" rx="16"/>
  <text x="350" y="30" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">Adding EUR/USD in MT5</text>

  <rect x="200" y="50" width="300" height="400" rx="24" fill="#FAF6F0" stroke="#3B2E26" stroke-width="3"/>
  <rect x="200" y="50" width="300" height="42" rx="24" fill="#EBDFCC"/>
  <rect x="200" y="72" width="300" height="20" fill="#EBDFCC"/>
  <text x="222" y="78" font-size="13" fill="#3B2E26" font-weight="bold">Quotes</text>
  <circle cx="470" cy="71" r="12" fill="none" stroke="#9C5B52" stroke-width="2"/>
  <text x="470" y="76" text-anchor="middle" font-size="12" fill="#9C5B52">🔍</text>

  <!-- Search bar -->
  <rect x="216" y="104" width="268" height="34" rx="17" fill="#EBDFCC" stroke="#9C7A4E" stroke-width="2"/>
  <text x="230" y="126" font-size="12" fill="#3B2E26">EUR/USD</text>
  <circle cx="350" cy="121" r="13" fill="none" stroke="#9C5B52" stroke-width="2"/>
  <text x="350" y="126" text-anchor="middle" font-size="14" fill="#9C5B52" font-weight="bold">①</text>

  <!-- Search result -->
  <rect x="216" y="150" width="268" height="40" rx="8" fill="#EBDFCC"/>
  <text x="230" y="174" font-size="12" fill="#3B2E26" font-weight="bold">EURUSD  Euro vs US Dollar</text>

  <circle cx="350" cy="170" r="13" fill="none" stroke="#9C7A4E" stroke-width="2"/>
  <text x="350" y="175" text-anchor="middle" font-size="14" fill="#9C7A4E" font-weight="bold">②</text>

  <!-- Existing watchlist -->
  <text x="222" y="220" font-size="11" fill="#8A7563">Your watchlist</text>
  <rect x="216" y="228" width="268" height="34" rx="6" fill="#EBDFCC" opacity="0.6"/>
  <text x="230" y="249" font-size="11" fill="#8A7563">XAUUSD</text>
  <rect x="216" y="266" width="268" height="34" rx="6" fill="#9C7A4E" opacity="0.25"/>
  <text x="230" y="287" font-size="11" fill="#3B2E26" font-weight="bold">EURUSD  ← just added</text>

  <!-- Bottom tab bar -->
  <rect x="200" y="392" width="300" height="42" fill="#EBDFCC"/>
  <text x="235" y="416" text-anchor="middle" font-size="9" fill="#9C7A4E" font-weight="bold">Quotes</text>
  <text x="292" y="416" text-anchor="middle" font-size="9" fill="#8A7563">Chart</text>
  <text x="350" y="416" text-anchor="middle" font-size="9" fill="#8A7563">Trade</text>
  <text x="408" y="416" text-anchor="middle" font-size="9" fill="#8A7563">History</text>
  <text x="465" y="416" text-anchor="middle" font-size="9" fill="#8A7563">Settings</text>

  <text x="90" y="118" font-size="12" fill="#3B2E26" text-anchor="end">① Tap search,</text>
  <text x="90" y="134" font-size="12" fill="#8A7563" text-anchor="end">type EUR/USD</text>

  <text x="610" y="168" font-size="12" fill="#3B2E26">② Tap the result</text>
  <text x="610" y="184" font-size="12" fill="#8A7563">to add it</text>

  <text x="350" y="462" text-anchor="middle" font-size="12" fill="#8A7563">Illustrative mockup, your MT5 app may look slightly different</text>
</svg>"""

CHART_DIAGRAMS = {
    1: '''<svg viewBox="0 0 700 460" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="460" fill="#ffffff"/>
  <text x="350" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Candlestick Anatomy</text>

  <!-- Bullish candle -->
  <g>
    <text x="180" y="70" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a7a3d">Bullish Candle</text>
    <!-- wick -->
    <line x1="180" y1="100" x2="180" y2="330" stroke="#1a7a3d" stroke-width="3"/>
    <!-- body -->
    <rect x="140" y="160" width="80" height="140" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>

    <!-- labels -->
    <line x1="180" y1="100" x2="280" y2="100" stroke="#555" stroke-width="1"/>
    <text x="286" y="105" font-size="14" fill="#333">High</text>

    <line x1="220" y1="160" x2="300" y2="140" stroke="#555" stroke-width="1"/>
    <text x="306" y="144" font-size="14" fill="#333">Close</text>

    <line x1="220" y1="300" x2="300" y2="320" stroke="#555" stroke-width="1"/>
    <text x="306" y="324" font-size="14" fill="#333">Open</text>

    <line x1="180" y1="330" x2="280" y2="350" stroke="#555" stroke-width="1"/>
    <text x="286" y="354" font-size="14" fill="#333">Low</text>

    <line x1="90" y1="230" x2="140" y2="230" stroke="#555" stroke-width="1"/>
    <text x="10" y="234" font-size="14" fill="#333">Body</text>

    <line x1="60" y1="120" x2="180" y2="120" stroke="#555" stroke-width="1" stroke-dasharray="3,3"/>
    <text x="10" y="120" font-size="14" fill="#333">Wick</text>
  </g>

  <!-- Bearish candle -->
  <g>
    <text x="520" y="70" text-anchor="middle" font-size="16" font-weight="bold" fill="#c0392b">Bearish Candle</text>
    <line x1="520" y1="100" x2="520" y2="330" stroke="#c0392b" stroke-width="3"/>
    <rect x="480" y="150" width="80" height="140" fill="#e74c3c" stroke="#c0392b" stroke-width="2"/>

    <line x1="520" y1="100" x2="620" y2="100" stroke="#555" stroke-width="1"/>
    <text x="626" y="105" font-size="14" fill="#333">High</text>

    <line x1="560" y1="150" x2="640" y2="130" stroke="#555" stroke-width="1"/>
    <text x="646" y="134" font-size="14" fill="#333">Open</text>

    <line x1="560" y1="290" x2="640" y2="310" stroke="#555" stroke-width="1"/>
    <text x="646" y="314" font-size="14" fill="#333">Close</text>

    <line x1="520" y1="330" x2="620" y2="350" stroke="#555" stroke-width="1"/>
    <text x="626" y="354" font-size="14" fill="#333">Low</text>
  </g>

  <text x="350" y="410" text-anchor="middle" font-size="14" fill="#666">Bullish: close is higher than open. Bearish: close is lower than open.</text>
  <text x="350" y="432" font-size="14" text-anchor="middle" fill="#666">The wick shows the full high-to-low range; the body shows open-to-close.</text>
</svg>''',
    2: '''<svg viewBox="0 0 800 380" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="800" height="380" fill="#ffffff"/>
  <text x="400" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Single Candlestick Patterns</text>

  <!-- Doji -->
  <g>
    <line x1="120" y1="90" x2="120" y2="250" stroke="#555" stroke-width="3"/>
    <rect x="100" y="165" width="40" height="6" fill="#888" stroke="#555" stroke-width="1"/>
    <text x="120" y="290" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a1a1a">Doji</text>
    <text x="120" y="312" text-anchor="middle" font-size="12" fill="#666">Indecision</text>
  </g>

  <!-- Hammer -->
  <g>
    <line x1="300" y1="120" x2="300" y2="260" stroke="#1a7a3d" stroke-width="3"/>
    <rect x="280" y="120" width="40" height="35" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>
    <text x="300" y="290" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a1a1a">Hammer</text>
    <text x="300" y="312" text-anchor="middle" font-size="12" fill="#666">Possible bottom</text>
  </g>

  <!-- Shooting Star -->
  <g>
    <line x1="480" y1="90" x2="480" y2="230" stroke="#c0392b" stroke-width="3"/>
    <rect x="460" y="195" width="40" height="35" fill="#e74c3c" stroke="#c0392b" stroke-width="2"/>
    <text x="480" y="290" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a1a1a">Shooting Star</text>
    <text x="480" y="312" text-anchor="middle" font-size="12" fill="#666">Possible top</text>
  </g>

  <!-- Marubozu -->
  <g>
    <rect x="640" y="100" width="40" height="150" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>
    <text x="660" y="290" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a1a1a">Marubozu</text>
    <text x="660" y="312" text-anchor="middle" font-size="12" fill="#666">Strong conviction</text>
  </g>

  <text x="400" y="355" text-anchor="middle" font-size="13" fill="#666">These patterns matter most when they appear at a meaningful level on the chart, not in isolation.</text>
</svg>''',
    3: '''<svg viewBox="0 0 700 360" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="360" fill="#ffffff"/>
  <text x="350" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Bullish vs Bearish Engulfing</text>

  <!-- Bullish Engulfing -->
  <g>
    <text x="180" y="70" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a7a3d">Bullish Engulfing</text>
    <!-- small bearish candle -->
    <line x1="140" y1="140" x2="140" y2="220" stroke="#c0392b" stroke-width="2"/>
    <rect x="125" y="160" width="30" height="45" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
    <!-- large bullish engulfing candle -->
    <line x1="220" y1="100" x2="220" y2="250" stroke="#1a7a3d" stroke-width="3"/>
    <rect x="195" y="120" width="50" height="115" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>
    <text x="180" y="290" text-anchor="middle" font-size="13" fill="#666">Big bullish candle fully</text>
    <text x="180" y="308" text-anchor="middle" font-size="13" fill="#666">covers the prior candle</text>
  </g>

  <!-- Bearish Engulfing -->
  <g>
    <text x="520" y="70" text-anchor="middle" font-size="16" font-weight="bold" fill="#c0392b">Bearish Engulfing</text>
    <!-- small bullish candle -->
    <line x1="480" y1="140" x2="480" y2="220" stroke="#1a7a3d" stroke-width="2"/>
    <rect x="465" y="160" width="30" height="45" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
    <!-- large bearish engulfing candle -->
    <line x1="560" y1="100" x2="560" y2="250" stroke="#c0392b" stroke-width="3"/>
    <rect x="535" y="120" width="50" height="115" fill="#e74c3c" stroke="#c0392b" stroke-width="2"/>
    <text x="520" y="290" text-anchor="middle" font-size="13" fill="#666">Big bearish candle fully</text>
    <text x="520" y="308" text-anchor="middle" font-size="13" fill="#666">covers the prior candle</text>
  </g>
</svg>''',
    4: '''<svg viewBox="0 0 700 400" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="400" fill="#ffffff"/>
  <text x="350" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Support and Resistance</text>

  <!-- Resistance zone -->
  <rect x="60" y="90" width="580" height="18" fill="#e74c3c" opacity="0.15"/>
  <line x1="60" y1="99" x2="640" y2="99" stroke="#c0392b" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="650" y="104" font-size="14" fill="#c0392b" font-weight="bold">Resistance</text>

  <!-- Support zone -->
  <rect x="60" y="292" width="580" height="18" fill="#22a35a" opacity="0.15"/>
  <line x1="60" y1="301" x2="640" y2="301" stroke="#1a7a3d" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="650" y="306" font-size="14" fill="#1a7a3d" font-weight="bold">Support</text>

  <!-- Zigzag price line bouncing between zones -->
  <polyline points="80,250 130,105 190,270 250,100 320,295 380,110 450,290 510,105 580,260 620,150"
    fill="none" stroke="#2c3e50" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>

  <!-- dots at bounce points -->
  <circle cx="130" cy="105" r="5" fill="#c0392b"/>
  <circle cx="250" cy="100" r="5" fill="#c0392b"/>
  <circle cx="380" cy="110" r="5" fill="#c0392b"/>
  <circle cx="510" cy="105" r="5" fill="#c0392b"/>

  <circle cx="190" cy="270" r="5" fill="#1a7a3d"/>
  <circle cx="320" cy="295" r="5" fill="#1a7a3d"/>
  <circle cx="450" cy="290" r="5" fill="#1a7a3d"/>

  <text x="350" y="360" text-anchor="middle" font-size="14" fill="#666">Price repeatedly reacts around the same zones, until it eventually breaks through.</text>
  <text x="350" y="382" text-anchor="middle" font-size="14" fill="#666">A broken resistance level can then flip and act as support (and vice versa).</text>
</svg>''',
    5: '''<svg viewBox="0 0 760 420" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="760" height="420" fill="#ffffff"/>
  <text x="380" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Trend Structure</text>

  <!-- Uptrend -->
  <g>
    <text x="180" y="65" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a7a3d">Uptrend (HH / HL)</text>
    <polyline points="60,270 110,190 160,220 210,140 260,175 300,90"
      fill="none" stroke="#1a7a3d" stroke-width="3" stroke-linejoin="round"/>
    <circle cx="110" cy="190" r="5" fill="#1a7a3d"/>
    <circle cx="210" cy="140" r="5" fill="#1a7a3d"/>
    <circle cx="300" cy="90" r="5" fill="#1a7a3d"/>
    <text x="110" y="180" text-anchor="middle" font-size="11" fill="#1a7a3d">HH1</text>
    <text x="210" y="130" text-anchor="middle" font-size="11" fill="#1a7a3d">HH2</text>
    <text x="300" y="80" text-anchor="middle" font-size="11" fill="#1a7a3d">HH3</text>
    <circle cx="160" cy="220" r="5" fill="#2c3e50"/>
    <circle cx="260" cy="175" r="5" fill="#2c3e50"/>
    <text x="160" y="240" text-anchor="middle" font-size="11" fill="#2c3e50">HL1</text>
    <text x="260" y="195" text-anchor="middle" font-size="11" fill="#2c3e50">HL2</text>
    <text x="180" y="310" text-anchor="middle" font-size="13" fill="#666">Each high and low is higher than the last</text>
  </g>

  <!-- Downtrend -->
  <g>
    <text x="580" y="65" text-anchor="middle" font-size="16" font-weight="bold" fill="#c0392b">Downtrend (LH / LL)</text>
    <polyline points="460,90 510,170 560,140 610,220 660,185 700,270"
      fill="none" stroke="#c0392b" stroke-width="3" stroke-linejoin="round"/>
    <circle cx="510" cy="170" r="5" fill="#c0392b"/>
    <circle cx="610" cy="220" r="5" fill="#c0392b"/>
    <circle cx="700" cy="270" r="5" fill="#c0392b"/>
    <text x="510" y="195" text-anchor="middle" font-size="11" fill="#c0392b">LL1</text>
    <text x="610" y="245" text-anchor="middle" font-size="11" fill="#c0392b">LL2</text>
    <text x="700" y="295" text-anchor="middle" font-size="11" fill="#c0392b">LL3</text>
    <circle cx="560" cy="140" r="5" fill="#2c3e50"/>
    <circle cx="660" cy="185" r="5" fill="#2c3e50"/>
    <text x="560" y="125" text-anchor="middle" font-size="11" fill="#2c3e50">LH1</text>
    <text x="660" y="170" text-anchor="middle" font-size="11" fill="#2c3e50">LH2</text>
    <text x="580" y="310" text-anchor="middle" font-size="13" fill="#666">Each high and low is lower than the last</text>
  </g>

  <text x="380" y="370" text-anchor="middle" font-size="14" fill="#333" font-weight="bold">A break in this pattern is often the earliest sign a trend may be shifting.</text>
</svg>''',
    6: '''<svg viewBox="0 0 800 480" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="800" height="480" fill="#ffffff"/>
  <text x="400" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Example Setup: Trend + Support + Confirmation</text>

  <!-- Support zone -->
  <rect x="60" y="300" width="680" height="18" fill="#22a35a" opacity="0.15"/>
  <line x1="60" y1="309" x2="740" y2="309" stroke="#1a7a3d" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="70" y="330" font-size="13" fill="#1a7a3d" font-weight="bold">Support zone</text>

  <!-- Candles: initial small uptrend rise (candles 1-3) -->
  <line x1="100" y1="230" x2="100" y2="270" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="88" y="240" width="24" height="22" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
  <line x1="145" y1="205" x2="145" y2="250" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="133" y="215" width="24" height="25" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
  <line x1="190" y1="180" x2="190" y2="225" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="178" y="190" width="24" height="25" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>

  <!-- Pullback down into support (candles 4-6, bearish) -->
  <line x1="235" y1="190" x2="235" y2="240" stroke="#c0392b" stroke-width="2"/>
  <rect x="223" y="195" width="24" height="30" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
  <line x1="280" y1="220" x2="280" y2="275" stroke="#c0392b" stroke-width="2"/>
  <rect x="268" y="228" width="24" height="32" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
  <line x1="325" y1="260" x2="325" y2="303" stroke="#c0392b" stroke-width="2"/>
  <rect x="313" y="266" width="24" height="24" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>

  <!-- Confirmation candle: bullish engulfing at support -->
  <line x1="370" y1="235" x2="370" y2="308" stroke="#1a7a3d" stroke-width="3"/>
  <rect x="355" y="245" width="32" height="55" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>
  <text x="370" y="345" text-anchor="middle" font-size="12" fill="#1a7a3d" font-weight="bold">Confirmation</text>
  <text x="370" y="360" text-anchor="middle" font-size="12" fill="#1a7a3d" font-weight="bold">candle (engulfing)</text>

  <!-- Entry marker just after confirmation candle -->
  <circle cx="415" cy="235" r="6" fill="#2c3e50"/>
  <text x="425" y="231" font-size="13" font-weight="bold" fill="#2c3e50">Entry</text>

  <!-- Continuation candles up to TP -->
  <line x1="415" y1="195" x2="415" y2="245" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="403" y="200" width="24" height="30" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>

  <line x1="460" y1="160" x2="460" y2="210" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="448" y="168" width="24" height="30" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>

  <line x1="505" y1="125" x2="505" y2="175" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="493" y="132" width="24" height="30" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>

  <line x1="550" y1="95" x2="550" y2="140" stroke="#1a7a3d" stroke-width="2"/>
  <rect x="538" y="100" width="24" height="28" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>

  <!-- SL line -->
  <line x1="60" y1="320" x2="740" y2="320" stroke="#c0392b" stroke-width="2" stroke-dasharray="5,3"/>
  <text x="600" y="316" font-size="13" font-weight="bold" fill="#c0392b">SL, just below support</text>

  <!-- TP line -->
  <line x1="60" y1="95" x2="740" y2="95" stroke="#1a7a3d" stroke-width="2" stroke-dasharray="5,3"/>
  <text x="590" y="90" font-size="13" font-weight="bold" fill="#1a7a3d">TP, prior resistance</text>

  <text x="400" y="410" text-anchor="middle" font-size="14" fill="#333" font-weight="bold">Illustrative example only, not a verified backtest or a setup to copy directly.</text>
  <text x="400" y="432" text-anchor="middle" font-size="13" fill="#666">Price rises, pulls back into support, a bullish candle confirms buyers stepping back in, then entry.</text>
</svg>''',
    7: '''<svg viewBox="0 0 700 400" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="400" fill="#ffffff"/>
  <text x="350" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Volume Behind Price Moves</text>

  <!-- price candles -->
  <g>
    <!-- candle 1 small green -->
    <line x1="120" y1="110" x2="120" y2="180" stroke="#1a7a3d" stroke-width="2"/>
    <rect x="105" y="130" width="30" height="35" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
    <!-- candle 2 big green (strong move) -->
    <line x1="220" y1="70" x2="220" y2="190" stroke="#1a7a3d" stroke-width="3"/>
    <rect x="200" y="90" width="40" height="90" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>
    <!-- candle 3 small red -->
    <line x1="320" y1="120" x2="320" y2="190" stroke="#c0392b" stroke-width="2"/>
    <rect x="305" y="140" width="30" height="30" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
    <!-- candle 4 big green (weak-volume version) -->
    <line x1="420" y1="60" x2="420" y2="185" stroke="#1a7a3d" stroke-width="3"/>
    <rect x="400" y="80" width="40" height="95" fill="#22a35a" stroke="#1a7a3d" stroke-width="2"/>
  </g>

  <!-- volume bars -->
  <g>
    <rect x="105" y="245" width="30" height="30" fill="#95a5a6"/>
    <rect x="200" y="215" width="40" height="60" fill="#1a7a3d"/>
    <rect x="305" y="255" width="30" height="20" fill="#95a5a6"/>
    <rect x="400" y="265" width="40" height="10" fill="#e67e22"/>
  </g>
  <line x1="80" y1="280" x2="480" y2="280" stroke="#333" stroke-width="1.5"/>
  <text x="60" y="230" font-size="12" fill="#666">Volume</text>

  <text x="220" y="310" text-anchor="middle" font-size="13" fill="#1a7a3d" font-weight="bold">Strong move + high volume</text>
  <text x="220" y="328" text-anchor="middle" font-size="12" fill="#666">= more confidence the move is genuine</text>

  <text x="420" y="310" text-anchor="middle" font-size="13" fill="#e67e22" font-weight="bold">Big move + low volume</text>
  <text x="420" y="328" text-anchor="middle" font-size="12" fill="#666">= treat with more suspicion</text>

  <text x="350" y="375" text-anchor="middle" font-size="13" fill="#333">Volume is the participation behind a move, not just the size of the candle itself.</text>
</svg>''',
    8: '''<svg viewBox="0 0 760 400" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="760" height="400" fill="#ffffff"/>
  <text x="380" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">Timeframes: Big Picture vs Fine Detail</text>

  <!-- Higher timeframe: smooth clear uptrend -->
  <g>
    <text x="180" y="70" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a1a1a">Higher Timeframe (4H / Daily)</text>
    <rect x="60" y="90" width="240" height="200" fill="#fafafa" stroke="#ddd" stroke-width="1"/>
    <polyline points="75,260 110,230 145,200 180,175 215,150 250,120 285,100"
      fill="none" stroke="#1a7a3d" stroke-width="3" stroke-linejoin="round"/>
    <text x="180" y="310" text-anchor="middle" font-size="13" fill="#666">Clear overall trend and major levels</text>
  </g>

  <!-- Lower timeframe: same section zoomed in, choppier -->
  <g>
    <text x="580" y="70" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a1a1a">Lower Timeframe (15M / 5M)</text>
    <rect x="460" y="90" width="240" height="200" fill="#fafafa" stroke="#ddd" stroke-width="1"/>
    <polyline points="475,220 500,180 520,210 545,160 565,195 590,140 610,175 635,110 660,145 685,100"
      fill="none" stroke="#2c3e50" stroke-width="2.5" stroke-linejoin="round"/>
    <text x="580" y="310" text-anchor="middle" font-size="13" fill="#666">Fine detail, useful for precise entries</text>
  </g>

  <path d="M 300 190 L 460 190" stroke="#888" stroke-width="1.5" marker-end="url(#arrow)"/>
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
      <path d="M0,0 L0,6 L9,3 z" fill="#888"/>
    </marker>
  </defs>
  <text x="380" y="180" text-anchor="middle" font-size="12" fill="#666">zoom in</text>

  <text x="380" y="360" text-anchor="middle" font-size="14" fill="#333" font-weight="bold">Check the higher timeframe first for context, then drop down to time your entry.</text>
</svg>''',
    9: '''<svg viewBox="0 0 900 420" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="900" height="420" fill="#ffffff"/>
  <text x="450" y="35" text-anchor="middle" font-size="22" font-weight="bold" fill="#1a1a1a">More Candlestick Patterns</text>

  <!-- Spinning Top -->
  <g>
    <line x1="100" y1="90" x2="100" y2="240" stroke="#555" stroke-width="2"/>
    <rect x="82" y="150" width="36" height="30" fill="#95a5a6" stroke="#555" stroke-width="1.5"/>
    <text x="100" y="270" text-anchor="middle" font-size="15" font-weight="bold" fill="#1a1a1a">Spinning Top</text>
    <text x="100" y="290" text-anchor="middle" font-size="11" fill="#666">Small body, long</text>
    <text x="100" y="304" text-anchor="middle" font-size="11" fill="#666">wicks both sides ,</text>
    <text x="100" y="318" text-anchor="middle" font-size="11" fill="#666">tug-of-war, no winner</text>
  </g>

  <!-- Inverted Hammer -->
  <g>
    <line x1="270" y1="90" x2="270" y2="230" stroke="#1a7a3d" stroke-width="2"/>
    <rect x="252" y="180" width="36" height="30" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
    <text x="270" y="270" text-anchor="middle" font-size="15" font-weight="bold" fill="#1a1a1a">Inverted Hammer</text>
    <text x="270" y="290" text-anchor="middle" font-size="11" fill="#666">Small body near</text>
    <text x="270" y="304" text-anchor="middle" font-size="11" fill="#666">bottom, long upper</text>
    <text x="270" y="318" text-anchor="middle" font-size="11" fill="#666">wick, after a downtrend</text>
  </g>

  <!-- Hanging Man -->
  <g>
    <line x1="440" y1="90" x2="440" y2="230" stroke="#c0392b" stroke-width="2"/>
    <rect x="422" y="90" width="36" height="30" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
    <text x="440" y="270" text-anchor="middle" font-size="15" font-weight="bold" fill="#1a1a1a">Hanging Man</text>
    <text x="440" y="290" text-anchor="middle" font-size="11" fill="#666">Small body near top,</text>
    <text x="440" y="304" text-anchor="middle" font-size="11" fill="#666">long lower wick ,</text>
    <text x="440" y="318" text-anchor="middle" font-size="11" fill="#666">after an uptrend</text>
  </g>

  <!-- Three White Soldiers -->
  <g>
    <line x1="590" y1="200" x2="590" y2="240" stroke="#1a7a3d" stroke-width="2"/>
    <rect x="578" y="205" width="24" height="35" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
    <line x1="625" y1="160" x2="625" y2="210" stroke="#1a7a3d" stroke-width="2"/>
    <rect x="613" y="165" width="24" height="45" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
    <line x1="660" y1="110" x2="660" y2="170" stroke="#1a7a3d" stroke-width="2"/>
    <rect x="648" y="115" width="24" height="55" fill="#22a35a" stroke="#1a7a3d" stroke-width="1.5"/>
    <text x="625" y="270" text-anchor="middle" font-size="15" font-weight="bold" fill="#1a1a1a">Three White Soldiers</text>
    <text x="625" y="290" text-anchor="middle" font-size="11" fill="#666">3 strong bullish candles</text>
    <text x="625" y="304" text-anchor="middle" font-size="11" fill="#666">in a row, strong</text>
    <text x="625" y="318" text-anchor="middle" font-size="11" fill="#666">upward momentum</text>
  </g>

  <!-- Three Black Crows -->
  <g>
    <line x1="800" y1="100" x2="800" y2="150" stroke="#c0392b" stroke-width="2"/>
    <rect x="788" y="105" width="24" height="40" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
    <line x1="835" y1="140" x2="835" y2="195" stroke="#c0392b" stroke-width="2"/>
    <rect x="823" y="145" width="24" height="45" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
    <line x1="870" y1="185" x2="870" y2="240" stroke="#c0392b" stroke-width="2"/>
    <rect x="858" y="190" width="24" height="45" fill="#e74c3c" stroke="#c0392b" stroke-width="1.5"/>
    <text x="835" y="270" text-anchor="middle" font-size="15" font-weight="bold" fill="#1a1a1a">Three Black Crows</text>
    <text x="835" y="290" text-anchor="middle" font-size="11" fill="#666">3 strong bearish candles</text>
    <text x="835" y="304" text-anchor="middle" font-size="11" fill="#666">in a row, strong</text>
    <text x="835" y="318" text-anchor="middle" font-size="11" fill="#666">downward momentum</text>
  </g>

  <text x="450" y="380" text-anchor="middle" font-size="13" fill="#666">Like all patterns, these mean the most when they appear at a meaningful chart level.</text>
</svg>''',
}

FUND_DIAGRAMS = {
    5: '''<svg viewBox="0 0 700 380" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="380" fill="#FAF6F0"/>
  <text x="350" y="34" text-anchor="middle" font-size="20" font-weight="bold" fill="#3B2E26">What a Candle Looks Like</text>

  <!-- Green (buy) candle -->
  <g>
    <line x1="200" y1="80" x2="200" y2="290" stroke="#5B7A5E" stroke-width="3"/>
    <rect x="165" y="140" width="70" height="110" fill="#5B7A5E" stroke="#456348" stroke-width="2" rx="3"/>
    <text x="200" y="320" text-anchor="middle" font-size="15" font-weight="bold" fill="#5B7A5E">GREEN</text>
    <text x="200" y="340" text-anchor="middle" font-size="13" fill="#3B2E26">Price went UP</text>
    <text x="200" y="356" text-anchor="middle" font-size="13" fill="#3B2E26">(a Buy candle)</text>
  </g>

  <!-- Red (sell) candle -->
  <g>
    <line x1="500" y1="80" x2="500" y2="290" stroke="#9C5B52" stroke-width="3"/>
    <rect x="465" y="130" width="70" height="110" fill="#9C5B52" stroke="#7A453D" stroke-width="2" rx="3"/>
    <text x="500" y="320" text-anchor="middle" font-size="15" font-weight="bold" fill="#9C5B52">RED</text>
    <text x="500" y="340" text-anchor="middle" font-size="13" fill="#3B2E26">Price went DOWN</text>
    <text x="500" y="356" text-anchor="middle" font-size="13" fill="#3B2E26">(a Sell candle)</text>
  </g>

  <!-- Labels pointing to body and wick, on the green candle -->
  <line x1="235" y1="190" x2="290" y2="190" stroke="#8A7563" stroke-width="1.5"/>
  <text x="296" y="185" font-size="14" fill="#3B2E26" font-weight="bold">Body</text>
  <text x="296" y="202" font-size="11" fill="#8A7563">the thick part</text>

  <line x1="200" y1="100" x2="290" y2="100" stroke="#8A7563" stroke-width="1.5"/>
  <text x="296" y="105" font-size="14" fill="#3B2E26" font-weight="bold">Wick</text>
  <text x="296" y="122" font-size="11" fill="#8A7563">the thin line</text>
</svg>''',
    1: '''<svg viewBox="0 0 700 320" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="320" fill="#FAF6F0"/>
  <text x="350" y="34" text-anchor="middle" font-size="20" font-weight="bold" fill="#3B2E26">Bulls vs Bears</text>

  <!-- Bull side -->
  <g>
    <text x="180" y="80" text-anchor="middle" font-size="16" font-weight="bold" fill="#5B7A5E">BULLISH</text>
    <polyline points="90,220 140,180 180,200 230,130 270,150" fill="none" stroke="#5B7A5E" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M270,150 L255,140 M270,150 L262,165" stroke="#5B7A5E" stroke-width="4" stroke-linecap="round"/>
    <text x="180" y="255" text-anchor="middle" font-size="13" fill="#3B2E26">Price expected to rise</text>
    <text x="180" y="273" text-anchor="middle" font-size="13" fill="#3B2E26" font-weight="bold">→ this is a BUY</text>
  </g>

  <line x1="350" y1="60" x2="350" y2="280" stroke="#E4D6C3" stroke-width="1.5"/>

  <!-- Bear side -->
  <g>
    <text x="520" y="80" text-anchor="middle" font-size="16" font-weight="bold" fill="#9C5B52">BEARISH</text>
    <polyline points="430,150 470,130 520,200 560,180 610,220" fill="none" stroke="#9C5B52" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M610,220 L595,212 M610,220 L600,205" stroke="#9C5B52" stroke-width="4" stroke-linecap="round"/>
    <text x="520" y="255" text-anchor="middle" font-size="13" fill="#3B2E26">Price expected to fall</text>
    <text x="520" y="273" text-anchor="middle" font-size="13" fill="#3B2E26" font-weight="bold">→ this is a SELL</text>
  </g>
</svg>''',
    2: '''<svg viewBox="0 0 700 380" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="380" fill="#FAF6F0"/>
  <text x="350" y="34" text-anchor="middle" font-size="20" font-weight="bold" fill="#3B2E26">Take Profit & Stop Loss</text>

  <!-- Price line -->
  <line x1="100" y1="200" x2="600" y2="200" stroke="#E4D6C3" stroke-width="2"/>

  <!-- Entry marker -->
  <circle cx="350" cy="200" r="7" fill="#3B2E26"/>
  <text x="350" y="225" text-anchor="middle" font-size="13" fill="#3B2E26" font-weight="bold">Entry</text>

  <!-- TP zone -->
  <line x1="100" y1="110" x2="600" y2="110" stroke="#5B7A5E" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="612" y="115" font-size="14" fill="#5B7A5E" font-weight="bold">TP</text>
  <text x="612" y="132" font-size="11" fill="#8A7563">Take Profit</text>
  <path d="M 350 195 L 350 115" stroke="#5B7A5E" stroke-width="2" marker-end="url(#arrowGreen)"/>

  <!-- SL zone -->
  <line x1="100" y1="290" x2="600" y2="290" stroke="#9C5B52" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="612" y="295" font-size="14" fill="#9C5B52" font-weight="bold">SL</text>
  <text x="612" y="312" font-size="11" fill="#8A7563">Stop Loss</text>
  <path d="M 350 205 L 350 285" stroke="#9C5B52" stroke-width="2" marker-end="url(#arrowRed)"/>

  <defs>
    <marker id="arrowGreen" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
      <path d="M0,0 L8,4 L0,8 z" fill="#5B7A5E" transform="rotate(-90 4 4)"/>
    </marker>
    <marker id="arrowRed" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
      <path d="M0,0 L8,4 L0,8 z" fill="#9C5B52" transform="rotate(90 4 4)"/>
    </marker>
  </defs>

  <text x="130" y="105" font-size="12" fill="#5B7A5E">Closes automatically ,</text>
  <text x="130" y="120" font-size="12" fill="#5B7A5E">locks in your win</text>

  <text x="130" y="305" font-size="12" fill="#9C5B52">Closes automatically ,</text>
  <text x="130" y="320" font-size="12" fill="#9C5B52">limits your loss</text>

  <text x="350" y="358" text-anchor="middle" font-size="13" fill="#8A7563">Both are set when you open the trade, the trade manages itself from there.</text>
</svg>''',
    3: '''<svg viewBox="0 0 700 320" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="320" fill="#FAF6F0"/>
  <text x="350" y="34" text-anchor="middle" font-size="20" font-weight="bold" fill="#3B2E26">Lot Size = Risk Size</text>

  <!-- Scale -->
  <text x="90" y="90" font-size="14" fill="#3B2E26" font-weight="bold">0.01</text>
  <rect x="90" y="100" width="30" height="30" fill="#5B7A5E" opacity="0.6" rx="4"/>
  <text x="105" y="150" text-anchor="middle" font-size="11" fill="#8A7563">Smallest size</text>
  <text x="105" y="165" text-anchor="middle" font-size="11" fill="#8A7563">Lowest risk</text>

  <text x="330" y="80" font-size="14" fill="#3B2E26" font-weight="bold">0.10</text>
  <rect x="330" y="60" width="60" height="70" fill="#B08F5E" opacity="0.6" rx="4"/>
  <text x="360" y="150" text-anchor="middle" font-size="11" fill="#8A7563">10x the size</text>
  <text x="360" y="165" text-anchor="middle" font-size="11" fill="#8A7563">10x the risk</text>

  <text x="560" y="60" font-size="14" fill="#3B2E26" font-weight="bold">1.00</text>
  <rect x="560" y="40" width="110" height="90" fill="#9C5B52" opacity="0.6" rx="4"/>
  <text x="615" y="150" text-anchor="middle" font-size="11" fill="#8A7563">100x the size</text>
  <text x="615" y="165" text-anchor="middle" font-size="11" fill="#8A7563">100x the risk</text>

  <line x1="80" y1="200" x2="680" y2="200" stroke="#E4D6C3" stroke-width="2"/>

  <text x="350" y="240" text-anchor="middle" font-size="14" fill="#3B2E26" font-weight="bold">Same price move. Very different outcome.</text>
  <text x="350" y="264" text-anchor="middle" font-size="13" fill="#8A7563">A 20-pip move at 0.01 lots might cost £2. The same move at 1.00 lots costs £200.</text>
  <text x="350" y="288" text-anchor="middle" font-size="13" fill="#8A7563">This is why beginners always start small, see the Lot Size guide for a full table.</text>
</svg>''',
    4: '''<svg viewBox="0 0 700 300" xmlns="http://www.w3.org/2000/svg" font-family="Arial, Helvetica, sans-serif">
  <rect width="700" height="300" fill="#FAF6F0"/>
  <text x="350" y="34" text-anchor="middle" font-size="20" font-weight="bold" fill="#3B2E26">Bid, Ask & Spread</text>

  <rect x="140" y="90" width="180" height="90" rx="10" fill="#9C5B52" opacity="0.12" stroke="#9C5B52" stroke-width="1.5"/>
  <text x="230" y="125" text-anchor="middle" font-size="13" fill="#9C5B52" font-weight="bold">BID</text>
  <text x="230" y="150" text-anchor="middle" font-size="18" fill="#3B2E26" font-weight="bold">1.08492</text>
  <text x="230" y="168" text-anchor="middle" font-size="11" fill="#8A7563">Price you SELL at</text>

  <rect x="380" y="90" width="180" height="90" rx="10" fill="#5B7A5E" opacity="0.12" stroke="#5B7A5E" stroke-width="1.5"/>
  <text x="470" y="125" text-anchor="middle" font-size="13" fill="#5B7A5E" font-weight="bold">ASK</text>
  <text x="470" y="150" text-anchor="middle" font-size="18" fill="#3B2E26" font-weight="bold">1.08508</text>
  <text x="470" y="168" text-anchor="middle" font-size="11" fill="#8A7563">Price you BUY at</text>

  <line x1="320" y1="135" x2="380" y2="135" stroke="#9C7A4E" stroke-width="2" stroke-dasharray="4,3"/>
  <text x="350" y="215" text-anchor="middle" font-size="13" fill="#9C7A4E" font-weight="bold">Spread</text>
  <text x="350" y="235" text-anchor="middle" font-size="12" fill="#8A7563">The small gap between them, normal on every pair</text>

  <text x="350" y="275" text-anchor="middle" font-size="13" fill="#3B2E26">Signal says Buy → match it to the Ask. Signal says Sell → match it to the Bid.</text>
</svg>''',
}


# ---------------------------------------------------------------------------
# DESIGN TOKENS / BASE CSS
# ---------------------------------------------------------------------------

BASE_CSS = """
:root {
  --bg: #FAF6F0;
  --bg-alt: #F1E8DA;
  --bg-alt-2: #EBDFCC;
  --ink: #3B2E26;
  --ink-dim: #8A7563;
  --gold: #9C7A4E;
  --gold-bright: #B08F5E;
  --rose: #C9A29B;
  --green: #5B7A5E;
  --red: #9C5B52;
  --line: #E4D6C3;
  --radius: 14px;
  --dark: #241C16;
  --dark-alt: #2E241C;
}

* { box-sizing: border-box; }

html { scroll-behavior: smooth; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: 'Inter', -apple-system, sans-serif;
  font-size: 16px;
  line-height: 1.6;
}

h1, h2, h3, .display {
  font-family: 'Fraunces', Georgia, serif;
  font-weight: 500;
  letter-spacing: -0.01em;
  margin: 0;
}

.mono {
  font-family: 'IBM Plex Mono', monospace;
}

a { color: inherit; text-decoration: none; }

.wrap {
  max-width: 1120px;
  margin: 0 auto;
  padding: 0 24px;
}

/* Nav */
nav.topnav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(250,246,240,0.92);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line);
}
nav.topnav .wrap {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 72px;
}
.brand {
  font-family: 'Fraunces', serif;
  font-size: 20px;
  letter-spacing: 0.02em;
}
.brand .dot { color: var(--gold); }
.navlinks { display: flex; gap: 32px; font-size: 14px; color: var(--ink-dim); }
.navlinks a:hover { color: var(--gold); }
.nav-cta {
  border: 1px solid var(--gold);
  color: var(--gold);
  padding: 8px 18px;
  border-radius: var(--radius);
  font-size: 14px;
  transition: all 0.15s ease;
}
.nav-cta:hover { background: var(--gold); color: var(--bg); }

/* Hero */
.hero {
  padding: 120px 0 100px;
  border-bottom: 1px solid var(--line);
}
.hero .wrap {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 64px;
  align-items: center;
}
.eyebrow {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--gold);
  margin-bottom: 20px;
  display: block;
}
.hero h1 {
  font-size: 52px;
  line-height: 1.08;
  margin-bottom: 24px;
}
.hero h1 em { color: var(--gold); font-style: normal; }
.hero p.lede {
  font-size: 18px;
  color: var(--ink-dim);
  max-width: 480px;
  margin-bottom: 36px;
}
.cta-row { display: flex; gap: 16px; align-items: center; }
.btn {
  display: inline-block;
  padding: 16px 34px;
  border-radius: 999px;
  font-size: 15px;
  font-weight: 600;
  transition: all 0.2s ease;
  border: 1px solid transparent;
  letter-spacing: 0.01em;
}
.btn-primary { background: var(--ink); color: var(--bg); }
.btn-primary:hover { background: var(--gold); transform: translateY(-1px); }
.btn-ghost { border-color: var(--ink); color: var(--ink); }
.btn-ghost:hover { border-color: var(--gold); color: var(--gold); }

/* Signal ticket, signature element */
.ticket {
  background: var(--bg-alt);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 32px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 24px 60px rgba(59,46,38,0.08);
}
.ticket::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--gold), var(--rose));
}
.ticket-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
  padding-bottom: 18px;
  border-bottom: 1px dashed var(--line);
}
.ticket-pair { font-family: 'Fraunces', serif; font-size: 22px; }
.tag-buy {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  letter-spacing: 0.08em;
  background: rgba(91,122,94,0.12);
  color: var(--green);
  border: 1px solid rgba(91,122,94,0.35);
  padding: 4px 10px;
  border-radius: 2px;
}

/* Ring motif, echoes the Female Wealth Inner Circle badge */
.ring-mark {
  width: 84px; height: 84px;
  border-radius: 50%;
  border: 1px solid var(--gold);
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 28px;
  position: relative;
}
.ring-mark::before {
  content: "";
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  border: 1px solid var(--line);
}
.ring-mark span {
  font-family: 'Fraunces', serif;
  font-size: 22px;
  color: var(--gold);
}
.ring-divider {
  width: 40px; height: 40px;
  border-radius: 50%;
  border: 1px solid var(--gold);
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 32px;
  color: var(--gold);
  font-size: 16px;
}
.ticket-row {
  display: flex;
  justify-content: space-between;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 14px;
  padding: 8px 0;
  color: var(--ink-dim);
}
.ticket-row span:last-child { color: var(--ink); }
.ticket-row.tp span:last-child { color: var(--green); }
.ticket-row.sl span:last-child { color: var(--red); }
.ticket-foot {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed var(--line);
  font-size: 12px;
  color: var(--ink-dim);
  font-family: 'IBM Plex Mono', monospace;
}

/* Dark editorial section, signature contrast moment */
.editorial {
  background: var(--dark);
  color: #EDE3D6;
  padding: 110px 0;
  text-align: center;
}
.editorial .wrap { max-width: 780px; }
.editorial .ring-mark { border-color: var(--gold); }
.editorial .ring-mark span { color: var(--gold); }
.editorial blockquote {
  font-family: 'Fraunces', serif;
  font-size: 34px;
  line-height: 1.4;
  font-weight: 400;
  margin: 24px 0 20px;
  font-style: normal;
}
.editorial blockquote em { color: var(--gold); font-style: normal; }
.editorial cite {
  font-style: normal;
  font-size: 14px;
  color: #B8AA98;
  letter-spacing: 0.04em;
}

/* Course content (rendered markdown) */
.course-content {
  max-width: 720px;
}
.course-content h2 {
  font-size: 28px;
  margin: 56px 0 20px;
  padding-top: 40px;
  border-top: 1px solid var(--line);
}
.course-content h2:first-child { border-top: none; padding-top: 0; margin-top: 0; }
.course-content h3 {
  font-size: 20px;
  margin: 40px 0 14px;
  font-family: 'Inter';
  font-weight: 600;
  color: var(--gold);
}
.course-content p {
  color: var(--ink-dim);
  font-size: 16px;
  line-height: 1.8;
  margin: 0 0 16px;
}
.course-content ul, .course-content ol {
  color: var(--ink-dim);
  font-size: 16px;
  line-height: 1.8;
  padding-left: 22px;
  margin: 0 0 16px;
}
.course-content li { margin-bottom: 6px; }
.course-content strong { color: var(--ink); }
.course-content em { color: var(--gold); font-style: normal; }
.course-content hr { border: none; border-top: 1px solid var(--line); margin: 32px 0; }
.course-content table { width: 100%; border-collapse: collapse; margin: 20px 0; }
.course-content th, .course-content td {
  text-align: left;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  font-size: 14px;
  color: var(--ink-dim);
}
.course-content th { color: var(--ink); font-weight: 600; }

/* Sections */
section { padding: 110px 0; border-bottom: 1px solid var(--line); }
section:last-of-type { border-bottom: none; }
.section-head { max-width: 620px; margin-bottom: 56px; }
.section-head h2 { font-size: 36px; margin-bottom: 14px; }
.section-head p { color: var(--ink-dim); font-size: 17px; }

/* Benefits grid */
.grid5 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
}
.benefit {
  background: var(--bg-alt);
  padding: 36px 28px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.benefit:hover {
  transform: translateY(-3px);
  box-shadow: 0 12px 32px rgba(59,46,38,0.08);
}
.benefit .icon {
  width: 44px; height: 44px;
  border: 1px solid var(--gold);
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: var(--gold);
  margin-bottom: 22px;
  font-family: 'Fraunces', serif;
  font-size: 18px;
}
.benefit h3 { font-size: 17px; margin-bottom: 10px; font-family: 'Inter'; font-weight: 600;}
.benefit p { font-size: 14px; color: var(--ink-dim); margin: 0; }

/* Process steps */
.process {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 40px;
}
.step .num {
  font-family: 'IBM Plex Mono', monospace;
  color: var(--gold);
  font-size: 13px;
  margin-bottom: 14px;
}
.step h3 { font-size: 20px; margin-bottom: 10px; }
.step p { color: var(--ink-dim); font-size: 15px; }

/* Course cards */
.courses { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.course-card {
  background: var(--bg-alt);
  border: 1px solid var(--line);
  padding: 40px;
  border-radius: var(--radius);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.course-card:hover { transform: translateY(-3px); box-shadow: 0 16px 40px rgba(59,46,38,0.1); }
.course-card .price {
  font-family: 'IBM Plex Mono', monospace;
  color: var(--gold);
  font-size: 14px;
  margin-bottom: 16px;
}
.course-card h3 { font-size: 24px; margin-bottom: 12px; }
.course-card p { color: var(--ink-dim); font-size: 15px; margin-bottom: 20px; }
.course-card ul { padding-left: 18px; margin: 0 0 24px; color: var(--ink-dim); font-size: 14px; }
.course-card li { margin-bottom: 8px; }

/* Community */
.community-panel {
  background: var(--bg-alt);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 60px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 48px;
  align-items: center;
}
.community-panel .eyebrow { color: var(--rose); }
.community-panel h2 { font-size: 32px; margin-bottom: 16px; }
.community-panel p { color: var(--ink-dim); }
.community-list { list-style: none; padding: 0; margin: 24px 0 0; }
.community-list li {
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
  font-size: 14px;
  color: var(--ink-dim);
}
.community-list li:last-child { border-bottom: none; }
.community-list span { color: var(--rose); margin-right: 10px; }

/* Form */
.form-panel {
  background: var(--bg-alt);
  border: 1px solid var(--line);
  padding: 44px;
  border-radius: var(--radius);
  max-width: 520px;
  box-shadow: 0 20px 50px rgba(59,46,38,0.06);
}
.form-panel label { display: block; font-size: 13px; color: var(--ink-dim); margin: 18px 0 6px; }
.form-panel input {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--line);
  color: var(--ink);
  padding: 13px 16px;
  border-radius: 10px;
  font-family: 'Inter';
  font-size: 15px;
}
.form-panel input:focus { outline: none; border-color: var(--gold); }
.form-panel button {
  margin-top: 28px;
  width: 100%;
  background: var(--ink);
  color: var(--bg);
  border: none;
  padding: 16px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.2s ease;
}
.form-panel button:hover { background: var(--gold); }

/* Onboarding page */
.ob-step {
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: 24px;
  padding: 36px 0;
  border-bottom: 1px solid var(--line);
}
.ob-step:last-child { border-bottom: none; }
.ob-num {
  font-family: 'Fraunces', serif;
  font-size: 32px;
  color: var(--gold);
}
.ob-step h3 { font-size: 20px; margin-bottom: 10px; }
.ob-step p { color: var(--ink-dim); font-size: 15px; }
.ob-step a.inline-link { color: var(--gold); border-bottom: 1px solid var(--gold); }
.callout {
  background: rgba(184,147,90,0.08);
  border: 1px solid rgba(184,147,90,0.3);
  border-radius: var(--radius);
  padding: 16px 20px;
  font-size: 14px;
  color: var(--gold-bright);
  margin-top: 14px;
}
.diagram-wrap {
  margin-top: 20px;
  border-radius: var(--radius);
  overflow: hidden;
  border: 1px solid var(--line);
}
.diagram-wrap svg { display: block; width: 100%; height: auto; }

/* Footer */
footer { padding: 48px 0; }
footer .wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--ink-dim);
}
footer .foot-links { display: flex; gap: 24px; }
footer a:hover { color: var(--gold); }

/* Disclaimer strip */
.disclaimer-strip {
  background: var(--bg-alt);
  border-top: 1px solid var(--line);
  padding: 16px 0;
  font-size: 12px;
  color: var(--ink-dim);
  text-align: center;
}

/* Member quick access bar */

@media (max-width: 860px) {
  .member-bar { top: 0; position: relative; }
  .member-bar .wrap { gap: 18px; }
}

/* HER, women-only members space */
.her-hero {
  padding: 90px 0 60px;
  background: linear-gradient(170deg, #FBF2EE 0%, var(--bg) 100%);
  border-bottom: 1px solid var(--line);
}
.her-mark {
  width: 108px; height: 108px;
  border-radius: 50%;
  border: 1px solid var(--rose);
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 26px;
  font-family: 'Fraunces', serif;
  font-size: 13px;
  line-height: 1.3;
  text-align: center;
  color: var(--rose);
  letter-spacing: 0.1em;
  position: relative;
  background: rgba(255,255,255,0.5);
}
.her-mark::before {
  content: "";
  position: absolute; inset: 7px;
  border-radius: 50%;
  border: 1px solid rgba(201,162,155,0.35);
}
.her-panel {
  background: linear-gradient(160deg, #FBF2EE, var(--bg-alt));
  border: 1px solid rgba(201,162,155,0.35);
  border-radius: 18px;
  padding: 30px;
}
.her-icon {
  width: 44px; height: 44px;
  border: 1px solid var(--rose);
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: var(--rose);
  margin: 0 auto 16px;
  font-size: 17px;
}
.her-card {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 22px 26px;
  margin-bottom: 12px;
  background: var(--bg-alt);
  border: 1px solid rgba(201,162,155,0.3);
  border-radius: 16px;
  color: var(--ink);
  transition: all 0.2s ease;
}
.her-card:hover {
  border-color: var(--rose);
  transform: translateX(3px);
  box-shadow: 0 10px 26px rgba(201,162,155,0.16);
}
.her-card.small { padding: 16px 22px; }
.her-num {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  color: var(--rose);
  flex-shrink: 0;
}
.her-title {
  font-family: 'Fraunces', serif;
  font-size: 18px;
  flex: 1;
}
.her-card.small .her-title { font-size: 16px; font-family: 'Inter'; }
.her-arrow { color: var(--rose); flex-shrink: 0; }
.her-quotes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 18px;
}
.her-quote {
  background: linear-gradient(160deg, #FBF2EE, var(--bg-alt));
  border: 1px solid rgba(201,162,155,0.3);
  border-radius: 16px;
  padding: 26px;
}
.her-quote p {
  font-family: 'Fraunces', serif;
  font-size: 17px;
  line-height: 1.5;
  color: var(--ink);
  margin: 0 0 10px;
}
.her-quote cite {
  font-style: normal;
  font-size: 12px;
  color: var(--rose);
}
@media (max-width: 860px) {
  .her-hero h1 { font-size: 34px !important; }
  .her-quotes { grid-template-columns: 1fr; }
}

/* Floating support button */
.support-float {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: var(--ink);
  color: var(--bg);
  padding: 14px 22px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 600;
  box-shadow: 0 12px 30px rgba(59,46,38,0.25);
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: background 0.2s ease;
}
.support-float:hover { background: var(--gold); }

/* Mobile */
@media (max-width: 860px) {
  .hero .wrap { grid-template-columns: 1fr; }
  .hero h1 { font-size: 36px; }
  .navlinks { display: none; }
  .process { grid-template-columns: 1fr; }
  .courses { grid-template-columns: 1fr; }
  .community-panel { grid-template-columns: 1fr; padding: 32px; }
  footer .wrap { flex-direction: column; gap: 16px; text-align: center; }
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  * { transition: none !important; }
}

a:focus-visible, button:focus-visible, input:focus-visible {
  outline: 2px solid var(--gold);
  outline-offset: 2px;
}

.tabbar {
  background: var(--dark);
  border-bottom: 1px solid rgba(255,255,255,0.08);
  position: sticky; top: 0; z-index: 40;
  overflow-x: auto; -webkit-overflow-scrolling: touch;
}
.tabbar::-webkit-scrollbar { display: none; }
.tabbar .wrap {
  display: flex; gap: 4px; align-items: stretch;
  padding-top: 0; padding-bottom: 0; white-space: nowrap;
}
.tabbar a {
  color: rgba(255,255,255,0.68); text-decoration: none;
  font-size: 13.5px; font-weight: 500; letter-spacing: .01em;
  padding: 14px 16px; border-bottom: 2px solid transparent;
  transition: color .15s ease, border-color .15s ease;
}
.tabbar a:hover { color: #fff; }
.tabbar a.active { color: var(--gold-bright); border-bottom-color: var(--gold-bright); }

@media (max-width: 640px) {
  .tabbar a { padding: 12px 12px; font-size: 13px; }
}

/* ---------- Admin ---------- */

.adm-bar {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  margin-bottom: 26px;
}
.adm-bar a {
  font-size: 13px; color: var(--ink-dim); text-decoration: none;
  padding: 7px 14px; border: 1px solid var(--line); border-radius: 999px;
  background: var(--bg-alt); white-space: nowrap;
}
.adm-bar a:hover { color: var(--ink); border-color: var(--gold); }
.adm-bar a.on { background: var(--ink); color: var(--bg); border-color: var(--ink); }

.adm-stats { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 28px; }
.adm-stat {
  flex: 1 1 130px; background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 16px 18px;
}
.adm-stat b { display: block; font-size: 26px; font-family: 'Fraunces', serif; line-height: 1.1; }
.adm-stat span { font-size: 12px; color: var(--ink-dim); text-transform: uppercase; letter-spacing: .06em; }

.adm-search { display: flex; gap: 10px; margin-bottom: 26px; }
.adm-search input {
  flex: 1; background: var(--bg); border: 1px solid var(--line); color: var(--ink);
  padding: 13px 16px; border-radius: 10px; font-size: 15px;
}
.adm-search button { width: auto; margin: 0; padding: 13px 24px; }

.adm-card {
  display: block; text-decoration: none; color: inherit;
  background: var(--bg-alt); border: 1px solid var(--line); border-left: 3px solid var(--line);
  border-radius: var(--radius); padding: 16px 18px; margin-bottom: 10px;
  transition: border-color .15s ease, transform .15s ease;
}
.adm-card:hover { border-color: var(--gold); border-left-color: var(--gold); transform: translateX(2px); }
.adm-card.is-pending { border-left-color: var(--gold-bright); }
.adm-card.is-approved { border-left-color: var(--green); }
.adm-card.is-dupe { border-left-color: var(--red); }
.adm-card-top { display: flex; justify-content: space-between; gap: 14px; align-items: baseline; }
.adm-card-name { font-weight: 600; font-size: 16px; }
.adm-phone {
  font-family: 'IBM Plex Mono', monospace; font-size: 14px; color: var(--ink);
  letter-spacing: .01em;
}
.adm-meta { font-size: 12.5px; color: var(--ink-dim); margin-top: 5px; }

.pill {
  display: inline-block; font-size: 11px; padding: 3px 9px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--ink-dim); background: var(--bg);
  margin: 4px 4px 0 0; white-space: nowrap;
}
.pill.on { background: var(--green); border-color: var(--green); color: #fff; }
.pill.warn { background: var(--red); border-color: var(--red); color: #fff; }
.pill.gold { background: var(--gold); border-color: var(--gold); color: #fff; }

.adm-grid { display: grid; grid-template-columns: 1.15fr 1fr; gap: 22px; align-items: start; }
@media (max-width: 860px) { .adm-grid { grid-template-columns: 1fr; } }

.adm-panel {
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 22px 24px; margin-bottom: 22px;
}
.adm-panel h3 {
  font-size: 15px; margin: 0 0 4px; text-transform: uppercase;
  letter-spacing: .07em; color: var(--ink-dim); font-family: 'Inter', sans-serif; font-weight: 600;
}
.adm-panel .hint { font-size: 13px; color: var(--ink-dim); margin: 0 0 18px; }

.adm-field { display: flex; justify-content: space-between; gap: 16px; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: 14px; }
.adm-field:last-child { border-bottom: none; }
.adm-field span { color: var(--ink-dim); flex-shrink: 0; }
.adm-field strong { font-weight: 500; text-align: right; word-break: break-word; }

.tick {
  display: flex; gap: 12px; align-items: flex-start; padding: 12px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--bg);
  margin-bottom: 8px; cursor: pointer;
}
.tick:hover { border-color: var(--gold); }
.tick input { width: 19px; height: 19px; margin: 2px 0 0; accent-color: var(--green); flex-shrink: 0; cursor: pointer; }
.tick b { display: block; font-size: 14.5px; font-weight: 600; }
.tick small { color: var(--ink-dim); font-size: 12.5px; }

.adm-edit label { display: block; font-size: 12px; color: var(--ink-dim); margin: 12px 0 5px; text-transform: uppercase; letter-spacing: .05em; }
.adm-edit input, .adm-edit select, .adm-edit textarea {
  width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink);
  padding: 11px 13px; border-radius: 9px; font-size: 14.5px; font-family: inherit;
}
.adm-edit textarea { min-height: 80px; resize: vertical; }

.adm-flash {
  background: var(--green); color: #fff; padding: 13px 18px; border-radius: 10px;
  font-size: 14px; margin-bottom: 22px;
}
.adm-flash.warn { background: var(--red); }

.dupe-row {
  display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  padding: 13px 0; border-bottom: 1px solid var(--line);
}
.dupe-row:last-child { border-bottom: none; }
.dupe-row .grow { flex: 1; min-width: 200px; }
.btn-sm { padding: 8px 16px; font-size: 13px; width: auto; margin: 0; }
.btn-danger { background: var(--red); color: #fff; border-color: var(--red); }

.adm-log { font-size: 13px; color: var(--ink-dim); }
.adm-log li { padding: 7px 0; border-bottom: 1px solid var(--line); list-style: none; }
.adm-log li:last-child { border-bottom: none; }
.adm-log b { color: var(--ink); font-weight: 600; }
"""

FONT_LINK = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">"""


# ---------------------------------------------------------------------------
# ADMIN VIEW HELPERS
# ---------------------------------------------------------------------------

def esc(value):
    """Escape anything a member typed before it goes into a page."""
    return html.escape(str(value if value is not None else ""), quote=True)


def fmt_date(value):
    try:
        return value.strftime("%d %b %Y")
    except Exception:
        return ""


def fmt_datetime(value):
    try:
        return value.strftime("%d %b %Y, %H:%M")
    except Exception:
        return ""


def flash_banner():
    """Feedback after a redirect, passed through the query string."""
    ok = request.args.get("ok", "")
    err = request.args.get("err", "")
    if err:
        return f'<div class="adm-flash warn">{esc(err)}</div>'
    if ok:
        return f'<div class="adm-flash">{esc(ok)}</div>'
    return ""


def member_card(m, sections, view=""):
    """
    One row in the admin lists. Shows who they are, what they've submitted and
    what they're waiting on, with an approve button right here so nothing needs
    opening first.
    """
    status = m.get("status") or "pending"
    cls = "is-approved" if status == "approved" else "is-pending"

    pills = "".join(f'<span class="pill on">{SECTION_LABELS[s]}</span>'
                    for s in SECTION_KEYS if s in sections)
    if not pills:
        pills = '<span class="pill">no access yet</span>'

    waiting = requested_sections(m, sections)
    if waiting:
        pills += "".join(f'<span class="pill gold">wants {SECTION_LABELS[s]}</span>'
                         for s in SECTION_KEYS if s in waiting)

    tg = (m.get("telegram_username") or "").lstrip("@")
    bits = [(m.get("tier") or "").title()]
    if m.get("account_number"):
        bits.append(f"Gold acct {esc(m['account_number'])}")
    if m.get("currency_account_number"):
        bits.append(f"PU Prime acct {esc(m['currency_account_number'])}")
    if m.get("deposit_amount"):
        bits.append(f"£{esc(m['deposit_amount'])}")
    bits.append(f"@{esc(tg)}" if tg else "no Telegram")
    bits.append("chat linked" if m.get("chat_id") else "no chat linked")
    bits.append(f"joined {fmt_date(m.get('created_at'))}")
    meta = " · ".join(filter(None, bits))

    if waiting:
        labels = ", ".join(SECTION_LABELS[s] for s in SECTION_KEYS if s in waiting)
        approve = (
            f'<form method="POST" action="/admin/member/{m["id"]}/approve" style="margin-top:12px;">'
            f'<input type="hidden" name="back" value="/admin?view={view}">'
            f'<button type="submit" class="btn btn-primary btn-sm">Approve {labels}</button>'
            f'</form>'
        )
    else:
        approve = ""

    return (
        f'<div class="adm-card {cls}">'
        f'<a href="/admin/member/{m["id"]}" style="text-decoration:none; color:inherit; display:block;">'
        f'<div class="adm-card-top">'
        f'<span class="adm-card-name">{esc(m.get("title") or "")} {esc(m.get("name") or "Unnamed member")}</span>'
        f'<span class="adm-phone">{esc(pretty_phone(m.get("phone")))}</span>'
        f'</div>'
        f'<div class="adm-meta">ID #{m["id"]} · {meta}</div>'
        f'<div style="margin-top:6px;">{pills}</div>'
        f'</a>'
        f'{approve}'
        f'</div>'
    )


def base_layout(title: str, content: str, active: str = "") -> str:
    def nav_class(key):
        if key == active:
            return "active"
        # both course tabs sit under the older "education" key
        if active == "education" and key in ("fundamentals", "advanced"):
            return ""
        return ""

    try:
        logged_in = bool(session.get("member_id"))
    except Exception:
        logged_in = False

    try:
        has_community = has_access("her")
    except Exception:
        has_community = False

    community_label = "Female Wealth" if has_community else "Community"

    if logged_in:
        nav_cta = '<a href="/account" class="nav-cta">My Account</a>'
        # One bar only. Members get their own places, not the public tabs repeated.
        granted = current_sections()
        links = [("/account", "My Account", "account"),
                 ("/my-signals", "Your Signals", "signals")]
        if "fundamentals" in granted:
            links.append(("/education/fundamentals", "Foundation Course", "fundamentals"))
        if "advanced" in granted:
            links.append(("/education/advanced", "Advanced Course", "advanced"))
        else:
            links.append(("/education/advanced", "Unlock Advanced", "advanced"))
        if "signals_currency" not in granted:
            links.append(("/signals", "Extra Signals", "extra"))
        if "her" in granted:
            links.append(("/her", community_label, "community"))
        links.append(("/messages", "Messages", "messages"))
        tabs = "".join(f'<a href="{href}" class="{nav_class(key)}">{label}</a>'
                       for href, label, key in links)
    else:
        nav_cta = '<a href="/unlock" class="nav-cta">Log In</a>'
        tabs = "".join(f'<a href="{href}" class="{nav_class(key)}">{label}</a>' for href, label, key in [
            ("/onboarding", "Onboarding", "onboarding"),
            ("/education/fundamentals", "Foundation Course", "fundamentals"),
            ("/education/advanced", "Advanced Course", "advanced"),
            ("/community", "Community", "community"),
            ("/signals", "Extra Signals", "signals"),
        ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}, Inner Circle</title>
{FONT_LINK}
<style>{BASE_CSS}</style>
</head>
<body>
<nav class="topnav">
  <div class="wrap">
    <a href="/" class="brand">INNER<span class="dot">·</span>CIRCLE</a>
    <div class="navlinks">
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" style="color: var(--gold);">Support</a>
    </div>
    {nav_cta}
  </div>
</nav>
<div class="tabbar">
  <div class="wrap">{tabs}</div>
</div>
{content}
<a href="https://t.me/Innercircleverifybot?start=help" target="_blank" rel="noopener" class="support-float">💬 Need help?</a>
<div class="disclaimer-strip">
  Educational content only, not financial advice. Trading carries risk, past performance is never a guarantee of future results.
</div>
<footer>
  <div class="wrap">
    <span>© Inner Circle</span>
    <div class="foot-links">
      <a href="/onboarding">Onboarding</a>
      <a href="/education">Education</a>
      <a href="/community">Community</a>
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener">Support</a>
    </div>
  </div>
</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ADMIN & UNLOCK
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST" and "password" in request.form:
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
        return redirect(url_for("admin"))

    if not session.get("admin"):
        content = """
<section style="padding: 100px 0;">
  <div class="wrap" style="max-width: 400px;">
    <div class="form-panel">
      <h3 style="font-size: 20px; margin-bottom: 20px;">Admin Login</h3>
      <form method="POST">
        <input type="password" name="password" placeholder="Password" style="width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink); padding: 13px 16px; border-radius: 10px;">
        <button type="submit" style="margin-top: 16px;">Log In</button>
      </form>
    </div>
  </div>
</section>
"""
        return render_template_string(base_layout("Admin", content, ""))

    view = request.args.get("view", "pending")
    search = request.args.get("q", "").strip()

    if search:
        members = get_all_members(search)
        view = "search"
        heading = f'{len(members)} result{"" if len(members) == 1 else "s"} for "{esc(search)}"'
    elif view == "approved":
        members = get_approved_members()
        heading = f"Approved members ({len(members)})"
    elif view == "all":
        members = get_all_members()
        heading = f"Everyone ({len(members)})"
    elif view == "community":
        members = get_community_requests()
        heading = f"Female Wealth requests ({len(members)})"
    else:
        view = "pending"
        members = get_pending_members()
        heading = f"Waiting for approval ({len(members)})"

    sections_by_member = get_sections_for_members([m["id"] for m in members])
    dupe_groups = find_all_duplicate_groups()
    unread = get_unread_count()

    counts = {
        "pending": len(get_pending_members()),
        "approved": len(get_approved_members()),
        "community": len(get_community_requests()),
        "dupes": len(dupe_groups),
    }

    pending_photos = count_pending_photos()
    if pending_photos:
        photo_panel = (
            f'<div class="adm-panel" style="border-color:var(--gold); margin-bottom:30px;">'
            f'<h3>Screenshots to check ({pending_photos})</h3>'
            f'<p class="hint">Someone has sent verification or payment screenshots. '
            f'Check them in your Telegram admin chat, then tick to approve.</p>'
            f'<a href="/admin/photos" class="btn btn-primary" style="display:inline-block; width:auto;">'
            f'Open the queue</a></div>'
        )
    else:
        photo_panel = (
            '<div class="adm-panel" style="margin-bottom:30px;">'
            '<h3>Screenshots to check</h3>'
            '<p class="hint" style="margin:0;">Nothing waiting. Verification and payment screenshots '
            'appear here to tick off. Lost codes are resent from a member\'s profile.</p></div>'
        )

    dupe_banner = ""
    if dupe_groups:
        n = len(dupe_groups)
        dupe_banner = (
            f'<div class="adm-flash warn">{n} phone number{"" if n == 1 else "s"} '
            f'appear{"s" if n == 1 else ""} on more than one account. '
            f'<a href="/admin/duplicates" style="color:#fff; text-decoration:underline;">Review and merge</a></div>'
        )

    unread_banner = ""
    if unread:
        unread_banner = (
            f'<div class="adm-flash">{unread} unread message{"" if unread == 1 else "s"} from members. '
            f'<a href="/admin/inbox" style="color:#fff; text-decoration:underline;">Open the inbox</a></div>'
        )

    cards = "".join(member_card(m, sections_by_member.get(m["id"], set()), view) for m in members)
    if not cards:
        empty = {
            "pending": "Nothing waiting. New signups land here.",
            "approved": "No approved members yet.",
            "community": "No Female Wealth requests right now.",
            "all": "No members yet.",
            "search": "No one matches that. Try a phone number, name or access code.",
        }[view]
        cards = f'<p style="color: var(--ink-dim); padding: 30px 0;">{empty}</p>'

    content = f"""
<section style="padding: 48px 0 70px;">
  <div class="wrap">
    <span class="eyebrow">Admin</span>
    <h1 style="font-size: 30px; margin: 10px 0 26px;">Members</h1>

    {flash_banner()}
    {dupe_banner}
    {unread_banner}

    <form class="adm-search" method="GET" action="/admin">
      <input type="search" name="q" value="{esc(search)}" placeholder="Search by phone number, name, access code or account number">
      <button type="submit">Search</button>
    </form>

    <div class="adm-stats">
      <div class="adm-stat"><b>{counts['pending']}</b><span>Waiting</span></div>
      <div class="adm-stat"><b>{counts['approved']}</b><span>Approved</span></div>
      <div class="adm-stat"><b>{counts['community']}</b><span>Female Wealth requests</span></div>
      <div class="adm-stat"><b>{counts['dupes']}</b><span>Duplicate phones</span></div>
    </div>

    <div class="adm-bar">
      <a href="/admin?view=pending" class="{'on' if view == 'pending' else ''}">Waiting ({counts['pending']})</a>
      <a href="/admin?view=approved" class="{'on' if view == 'approved' else ''}">Approved ({counts['approved']})</a>
      <a href="/admin?view=community" class="{'on' if view == 'community' else ''}">Female Wealth ({counts['community']})</a>
      <a href="/admin?view=all" class="{'on' if view == 'all' else ''}">Everyone</a>
      <a href="/admin/photos" class="">Screenshots ({pending_photos})</a>
      <a href="/admin/inbox" class="">Inbox ({unread})</a>
      <a href="/admin/duplicates" class="">Duplicates ({counts['dupes']})</a>
    </div>

    {photo_panel}

    <h2 style="font-size: 19px; margin: 0 0 16px;">{heading}</h2>
    {cards}
  </div>
</section>
"""
    return render_template_string(base_layout("Admin", content, ""))


@app.route("/admin/member/<int:member_id>/resend-code", methods=["POST"])
def admin_resend_code(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    member, code, sent = resend_access_code(member_id)
    if not member:
        return redirect("/admin?err=" + quote("Couldn't find that member."))
    if not code:
        return redirect(f"/admin/member/{member_id}?err=" + quote("They haven't been approved yet, so a code would not let them in. Approve them first, which sends the code automatically."))
    if sent:
        msg = f"Code {code} sent to them on Telegram."
    else:
        msg = f"No Telegram chat linked, so nothing was sent. Their code is {code}, pass it on yourself."
        return redirect(f"/admin/member/{member_id}?err=" + quote(msg))
    return redirect(f"/admin/member/{member_id}?ok=" + quote(msg))


@app.route("/admin/member/<int:member_id>/new-code", methods=["POST"])
def admin_new_code(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    member, code, sent = regenerate_access_code(member_id)
    if not member:
        return redirect("/admin?err=" + quote("Couldn't find that member."))
    if not code:
        return redirect(f"/admin/member/{member_id}?err=" + quote("They haven't been approved yet, so a code would not let them in. Approve them first, which sends the code automatically."))
    if sent:
        msg = f"New code {code} issued and sent on Telegram. The old one no longer works."
        return redirect(f"/admin/member/{member_id}?ok=" + quote(msg))
    msg = (f"New code {code} issued, but there's no Telegram chat linked so it wasn't sent. "
           f"Pass it on yourself, their old code has already stopped working.")
    return redirect(f"/admin/member/{member_id}?err=" + quote(msg))


@app.route("/admin/resend-code", methods=["POST"])
def admin_resend_code_lookup():
    """Find a member by phone, name or handle and send their code straight back."""
    if not session.get("admin"):
        return redirect(url_for("admin"))

    lookup = request.form.get("lookup", "").strip()
    if not lookup:
        return redirect("/admin?err=" + quote("Type a phone number, name or @username first."))

    member = find_member_by_phone(lookup)
    if member:
        matches = [member]
    else:
        matches = get_all_members(lookup)

    if not matches:
        return redirect("/admin?err=" + quote(f"Nobody matches '{lookup}'."))
    if len(matches) > 1:
        return redirect("/admin?q=" + quote(lookup) + "&err=" +
                        quote(f"{len(matches)} people match '{lookup}'. "
                              f"Open the right one and resend from their profile."))

    mid = matches[0]["id"]
    member, code, sent = resend_access_code(mid)
    who = member.get("name") or f"member #{mid}"
    if not code:
        return redirect(f"/admin/member/{mid}?err=" + quote("They haven't been approved yet, so a code would not let them in. Approve them first, which sends the code automatically."))
    if sent:
        return redirect("/admin?ok=" + quote(f"Code {code} sent to {who} on Telegram."))
    return redirect(f"/admin/member/{mid}?err=" +
                    quote(f"{who} has no Telegram chat linked, so nothing was sent. "
                          f"Their code is {code}, pass it on yourself."))


@app.route("/admin/member/<int:member_id>/approve", methods=["POST"])
def admin_approve_requested(member_id):
    """
    One click: grant everything this person has asked for and send the links.
    No typing, no scrolling, no working out what they wanted.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    member = get_member_by_id(member_id)
    if not member:
        return redirect("/admin?err=" + quote("Couldn't find that member."))

    granted_now = get_member_sections(member_id)
    want = requested_sections(member, granted_now)
    extra = set(request.form.getlist("also"))          # optional extra tick, e.g. advanced
    want |= (extra - granted_now)

    if not want:
        return redirect(f"/admin/member/{member_id}?ok=" +
                        quote("They already have everything they've asked for."))

    set_member_sections(member_id, granted_now | want, actor="one-click approve")

    conn = get_db()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""UPDATE members SET status='approved', community_requested=FALSE,
                               approved_at=COALESCE(approved_at, NOW()), updated_at=NOW()
                               WHERE id=%s""", (member_id,))
        finally:
            conn.close()

    summary = deliver_sections(member_id, want)
    labels = ", ".join(SECTION_LABELS[x] for x in SECTION_KEYS if x in want)
    back = request.form.get("back") or "/admin"
    joiner = "&" if "?" in back else "?"
    return redirect(f"{back}{joiner}ok=" + quote(f"{member.get('name') or 'Member'}: {labels}. {summary}"))


@app.route("/admin/member/<int:member_id>/clear-merged", methods=["POST"])
def admin_clear_merged(member_id):
    """Bin the leftover shells folded into this profile. Their details are already here."""
    if not session.get("admin"):
        return redirect(url_for("admin"))
    gone = delete_merged_shells(member_id)
    audit(member_id, "merged records deleted", f"{gone} leftover record(s) removed for good")
    return redirect(f"/admin/member/{member_id}?ok=" +
                    quote(f"Deleted {gone} leftover merged record{'' if gone == 1 else 's'}."))


@app.route("/admin/clear-merged", methods=["POST"])
def admin_clear_all_merged():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    gone = delete_merged_shells()
    return redirect("/admin/duplicates?ok=" +
                    quote(f"Deleted {gone} leftover merged record{'' if gone == 1 else 's'} across all profiles."))


@app.route("/admin/member/<int:member_id>/delete", methods=["POST"])
def admin_delete_member(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    member = get_member_by_id(member_id)
    name = (member or {}).get("name") or f"#{member_id}"
    delete_merged_shells(member_id)
    delete_member(member_id)
    return redirect("/admin?ok=" + quote(f"Deleted {name} and everything attached to them."))


@app.route("/admin/photos")
def admin_photos():
    """
    Screenshots waiting to be checked, each with a tick to approve. No typing:
    if we know whose account it is the button just works, and if we don't there
    is a picker to attach it to the right person first.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    subs = get_photo_submissions()
    unattached = [s for s in subs if not s.get("member_id")]
    candidates = get_all_members()[:200] if unattached else []

    rows = []
    for sub in subs:
        kind = sub.get("kind") or "onboarding"
        kind_label = "Advanced payment" if kind == "payment" else "Onboarding verification"
        expected = 1 if kind == "payment" else 2
        count = int(sub.get("photo_count") or 0)
        complete = count >= expected

        # Onboarding is two specific shots, so say which is which and which is missing.
        if kind == "payment":
            checklist = '<div class="adm-meta">Looking for: proof of the £99 payment.</div>'
        else:
            def step(n, label):
                got = count >= n
                colour = "var(--green)" if got else "var(--ink-dim)"
                return (f'<strong style="color:{colour};">{"received" if got else "waiting"} '
                        f'{n}. {label}</strong>')
            checklist = ('<div class="adm-meta">Two shots needed: '
                         + step(1, "deposit confirmation") + ' · '
                         + step(2, "closed trades history, 10 activation trades") + '</div>')

        who = (sub.get("member_name") or sub.get("first_name") or "Unknown sender")
        handle = (sub.get("username") or "").lstrip("@")
        ident = []
        if sub.get("member_id"):
            ident.append(f'<a href="/admin/member/{sub["member_id"]}" class="inline-link">'
                         f'#{sub["member_id"]} on file</a>')
            if sub.get("member_phone"):
                ident.append(f'<span class="adm-phone">{esc(pretty_phone(sub["member_phone"]))}</span>')
            if sub.get("member_account"):
                ident.append(f'Gold acct {esc(sub["member_account"])}')
        ident.append(f"@{esc(handle)}" if handle else "no Telegram username")
        ident.append(f'chat {sub.get("chat_id")}')

        if sub.get("member_id"):
            grant_label = ("Approve and unlock Advanced" if kind == "payment"
                           else "Approve and send their links")
            action = (
                f'<form method="POST" action="/admin/photos/{sub["id"]}/approve" style="display:inline;">'
                f'<button type="submit" class="btn btn-primary btn-sm">{grant_label}</button></form>'
            )
        else:
            options = "".join(
                f'<option value="{c["id"]}">{esc(c.get("name") or "unnamed")} · '
                f'{esc(pretty_phone(c.get("phone")))} · #{c["id"]}</option>'
                for c in candidates
            )
            action = (
                f'<form method="POST" action="/admin/photos/{sub["id"]}/approve" '
                f'style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">'
                f'<select name="member_id" required style="background:var(--bg); border:1px solid var(--line); '
                f'color:var(--ink); padding:9px 11px; border-radius:9px; font-size:13px; max-width:320px;">'
                f'<option value="">Whose account is this?</option>{options}</select>'
                f'<button type="submit" class="btn btn-primary btn-sm">Attach and approve</button></form>'
            )

        file_ids = [f for f in (sub.get("file_ids") or "").split(",") if f]
        if file_ids:
            shots = "".join(
                f'<a href="/admin/photos/{sub["id"]}/image/{i}" target="_blank" rel="noopener" '
                f'style="display:block; flex:1 1 220px; max-width:300px;">'
                f'<img src="/admin/photos/{sub["id"]}/image/{i}" alt="Screenshot {i+1}" '
                f'style="width:100%; border:1px solid var(--line); border-radius:10px; display:block;"></a>'
                for i in range(len(file_ids))
            )
            thumbs = (f'<p class="hint" style="margin:12px 0 8px;">Tap an image to open it full size.</p>'
                      f'<div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px;">{shots}</div>')
        else:
            thumbs = ('<p class="hint" style="margin:12px 0 10px;">No image saved for this one. '
                      'It was forwarded to your Telegram admin chat, check it there.</p>')

        warn = ("" if complete else
                f'<div class="adm-meta" style="color:var(--red);">Only {count} of {expected} '
                f'screenshot{"" if expected == 1 else "s"} so far.</div>')

        rows.append(
            f'<div class="adm-panel" style="margin-bottom:14px;">'
            f'<div class="adm-card-top"><span class="adm-card-name">{esc(who)}</span>'
            f'<span class="pill {"on" if complete else "warn"}">{kind_label}</span></div>'
            f'<div class="adm-meta">{" · ".join(ident)} · {fmt_datetime(sub.get("created_at"))}</div>'
            f'{checklist}{warn}'
            f'{thumbs}'
            f'<div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">{action}'
            f'<form method="POST" action="/admin/photos/{sub["id"]}/reject" style="display:inline;">'
            f'<button type="submit" class="btn btn-ghost btn-sm">Not right, dismiss</button></form></div>'
            f'</div>'
        )

    body = "".join(rows) or (
        '<div class="adm-panel"><h3>Nothing waiting</h3>'
        '<p class="hint" style="margin:0;">When someone sends screenshots to the bot they appear here '
        'with a tick to approve.</p></div>')

    content = f"""
<section style="padding: 48px 0 70px;">
  <div class="wrap" style="max-width: 780px;">
    <a href="/admin" class="inline-link" style="font-size: 13px;">← Back to members</a>
    <h1 style="font-size: 30px; margin: 16px 0 8px;">Screenshots to check</h1>
    <p style="color: var(--ink-dim); margin-bottom: 24px;">
      Onboarding needs two, an Advanced payment needs one. Approving sends their code and
      group links straight away.
    </p>
    {flash_banner()}
    {body}
  </div>
</section>
"""
    return render_template_string(base_layout("Screenshots", content, ""))


@app.route("/admin/photos/<int:sub_id>/approve", methods=["POST"])
def admin_photo_approve(sub_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    sub = get_photo_submission(sub_id)
    if not sub:
        return redirect("/admin/photos?err=" + quote("That submission has gone."))

    member_id = sub.get("member_id")
    picked = request.form.get("member_id")
    if picked and str(picked).isdigit():
        member_id = int(picked)
        link_photo_to_member(sub_id, member_id)
        # Remember the chat so this person is recognised automatically next time.
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET chat_id=COALESCE(chat_id, %s),
                                   telegram_username=COALESCE(NULLIF(telegram_username,''), %s),
                                   updated_at=NOW() WHERE id=%s""",
                                (sub.get("chat_id"), sub.get("username"), member_id))
            finally:
                conn.close()

    if not member_id:
        return redirect("/admin/photos?err=" + quote("Pick whose account this belongs to first."))

    if sub.get("kind") == "payment":
        want = {"advanced"}
    else:
        member = get_member_by_id(member_id)
        want = requested_sections(member, get_member_sections(member_id)) or {"signals_gold", "fundamentals"}

    granted = get_member_sections(member_id)
    set_member_sections(member_id, granted | want, actor="photo approval")

    conn = get_db()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""UPDATE members SET status='approved', community_requested=FALSE,
                               approved_at=COALESCE(approved_at, NOW()), updated_at=NOW()
                               WHERE id=%s""", (member_id,))
        finally:
            conn.close()

    summary = deliver_sections(member_id, want)
    resolve_photo_submission(sub_id, "approved")

    labels = ", ".join(SECTION_LABELS[x] for x in SECTION_KEYS if x in want)
    return redirect("/admin/photos?ok=" + quote(f"Approved for {labels}. {summary}"))


@app.route("/admin/photos/<int:sub_id>/reject", methods=["POST"])
def admin_photo_reject(sub_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    resolve_photo_submission(sub_id, "rejected")
    return redirect("/admin/photos?ok=" + quote("Dismissed. Nothing was sent to them."))


@app.route("/admin/inbox")
def admin_inbox():
    """Everyone who has messaged, newest first, so nothing gets missed."""
    if not session.get("admin"):
        return redirect(url_for("admin"))

    convos = get_all_conversations()
    unread_total = sum(int(c.get("unread") or 0) for c in convos)

    if convos:
        rows = "".join(
            f'<a class="adm-card {"is-dupe" if int(c.get("unread") or 0) else ""}" '
            f'href="/admin/member/{c["id"]}">'
            f'<div class="adm-card-top">'
            f'<span class="adm-card-name">{esc(c.get("title") or "")} {esc(c.get("name") or "Unnamed member")}'
            + (f' <span class="pill warn">{int(c["unread"])} new</span>' if int(c.get("unread") or 0) else "")
            + f'</span>'
            f'<span class="adm-meta" style="margin:0;">{fmt_datetime(c.get("last_at"))}</span>'
            f'</div>'
            f'<div class="adm-meta" style="color:var(--ink); margin-top:6px;">'
            f'{esc((c.get("last_body") or "")[:130])}'
            + ("..." if len(c.get("last_body") or "") > 130 else "")
            + f'</div>'
            f'<div class="adm-meta">{esc((c.get("tier") or "").title())}</div>'
            f'</a>'
            for c in convos
        )
    else:
        rows = '<p style="color:var(--ink-dim); padding:30px 0;">No messages yet. When a member writes in, they appear here.</p>'

    content = f"""
<section style="padding: 48px 0 70px;">
  <div class="wrap" style="max-width: 780px;">
    <a href="/admin" class="inline-link" style="font-size: 13px;">← Back to members</a>
    <h1 style="font-size: 30px; margin: 16px 0 8px;">Inbox</h1>
    <p style="color: var(--ink-dim); margin-bottom: 24px;">
      Everyone who has messaged, newest first. {unread_total} unread.
    </p>
    {flash_banner()}
    {rows}
  </div>
</section>
"""
    return render_template_string(base_layout("Inbox", content, ""))


@app.route("/admin/member/<int:member_id>")
def admin_member(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    member = get_member_full(member_id)
    if not member:
        return redirect(url_for("admin"))

    granted = member["sections"]
    dupes = find_duplicates_for(member)

    # --- access tick boxes -------------------------------------------------
    ticks = "".join(
        f'<label class="tick">'
        f'<input type="checkbox" name="sections" value="{key}" {"checked" if key in granted else ""}>'
        f'<span><b>{label}</b><small>{blurb}</small></span></label>'
        for key, label, blurb in SECTIONS
    )

    # --- editable details --------------------------------------------------
    tier_options = "".join(
        f'<option value="{t}" {"selected" if member.get("tier") == t else ""}>{lbl}</option>'
        for t, lbl in [("gold", "Gold"), ("currency", "Currency"), ("community", "Wealth Circle")]
    )
    status_options = "".join(
        f'<option value="{s}" {"selected" if member.get("status") == s else ""}>{lbl}</option>'
        for s, lbl in [("pending", "Waiting"), ("approved", "Approved"), ("declined", "Declined")]
    )

    # --- duplicates for this person ---------------------------------------
    if dupes:
        dupe_rows = "".join(
            f'<div class="dupe-row">'
            f'<div class="grow">'
            f'<a href="/admin/member/{d["id"]}" class="inline-link"><strong>#{d["id"]} {esc(d.get("name") or "unnamed")}</strong></a>'
            f'<div class="adm-meta">{esc(pretty_phone(d.get("phone")))} · {esc(d.get("tier") or "")} · '
            f'{esc(d.get("status") or "")} · created {fmt_date(d.get("created_at"))}</div>'
            f'<span class="pill warn">{esc(d.get("match_reason", "possible match"))}</span>'
            f'</div>'
            f'<form method="POST" action="/admin/merge">'
            f'<input type="hidden" name="primary_id" value="{member_id}">'
            f'<input type="hidden" name="duplicate_ids" value="{d["id"]}">'
            f'<button type="submit" class="btn btn-primary btn-sm">Merge into this profile</button>'
            f'</form></div>'
            for d in dupes
        )
        dupe_panel = f"""
    <div class="adm-panel" style="border-color: var(--red);">
      <h3 style="color: var(--red);">Possible duplicates</h3>
      <p class="hint">Merging keeps this profile and folds the other one into it: messages move across, unlocked sections combine, and any blank field here is filled in from the other record. Nothing is deleted, so a merge can be undone.</p>
      {dupe_rows}
    </div>"""
    else:
        dupe_panel = ""

    absorbed_panel = ""
    if member.get("absorbed"):
        rows = "".join(
            f'<div class="dupe-row"><div class="grow">'
            f'<strong>#{a["id"]} {esc(a.get("name") or "unnamed")}</strong>'
            f'<div class="adm-meta">{esc(pretty_phone(a.get("phone")))} · merged in</div></div>'
            f'<form method="POST" action="/admin/unmerge/{a["id"]}">'
            f'<button type="submit" class="btn btn-ghost btn-sm">Separate again</button></form></div>'
            for a in member["absorbed"]
        )
        absorbed_panel = f"""
    <div class="adm-panel">
      <h3>Merged into this profile ({len(member['absorbed'])})</h3>
      <p class="hint">Their details are already on this profile, so these are empty shells. Delete them to tidy up, or separate one to put it back on its own.</p>
      <form method="POST" action="/admin/member/{member_id}/clear-merged" style="margin-bottom:16px;"
            onsubmit="return confirm('Delete these {len(member['absorbed'])} leftover records for good?');">
        <button type="submit" class="btn btn-danger">Delete all {len(member['absorbed'])} leftover records</button>
      </form>
      {rows}
    </div>"""

    # --- audit trail -------------------------------------------------------
    log = get_audit(member_id)
    log_html = "".join(
        f'<li><b>{esc(e["action"])}</b> · {fmt_datetime(e["created_at"])}'
        + (f'<br>{esc(e["detail"])}' if e.get("detail") else "") + '</li>'
        for e in log
    ) or '<li>Nothing recorded yet.</li>'

    merged_notice = ""
    if member.get("merged_into"):
        merged_notice = (
            f'<div class="adm-flash warn">This record was merged into '
            f'<a href="/admin/member/{member["merged_into"]}" style="color:#fff; text-decoration:underline;">'
            f'#{member["merged_into"]}</a> and is hidden from the member lists.</div>'
        )

    unread_pill = f'<span class="pill warn">{member["unread_count"]} unread</span>' if member.get("unread_count") else ""
    has_currency = bool(str(member.get("currency_account_number") or "").strip())

    recent = get_messages(member_id)[-6:]
    # Reading the thread here counts as reading it, so the inbox count stays
    # honest. The unread pill above still reflects what was new on arrival.
    if member.get("unread_count"):
        mark_messages_read(member_id)
    if recent:
        thread_preview = "".join(
            f'<div style="margin-bottom:10px; text-align:{"left" if m["sender"]=="member" else "right"};">'
            f'<div style="display:inline-block; max-width:85%; text-align:left; padding:9px 12px; '
            f'border-radius:12px; font-size:13.5px; '
            f'background:{"var(--bg-alt-2)" if m["sender"]=="member" else "var(--gold)"}; '
            f'color:{"var(--ink)" if m["sender"]=="member" else "#fff"};">'
            f'<div style="font-size:10.5px; opacity:0.75; margin-bottom:3px;">'
            f'{esc(member.get("name") or "Member") if m["sender"]=="member" else "You"} · {fmt_datetime(m.get("created_at"))}</div>'
            f'{esc(m["body"])}</div></div>'
            for m in recent
        )
    else:
        thread_preview = '<p style="color:var(--ink-dim); font-size:13px; margin:0; text-align:center;">No messages yet.</p>' 

    if member.get("status") != "approved":
        code_delivery_note = ('<p class="hint" style="margin: 0 0 14px; color: var(--red);">'
                              'Not approved yet. Approve them first and the code goes out automatically.</p>')
    elif member.get("chat_id"):
        code_delivery_note = ('<p class="hint" style="margin: 0 0 14px;">Goes straight to them on Telegram.</p>')
    else:
        code_delivery_note = ('<p class="hint" style="margin: 0 0 14px; color: var(--red);">'
                              'No Telegram chat linked, so nothing can be sent automatically. '
                              'Copy the code above and pass it on yourself.</p>')
    tg = (member.get("telegram_username") or "").lstrip("@")
    tg_line = (f'<a href="https://t.me/{esc(tg)}" target="_blank" rel="noopener" class="inline-link">@{esc(tg)}</a>'
               if tg else '<span style="color: var(--ink-dim);">none on file</span>')

    content = f"""
<section style="padding: 40px 0 70px;">
  <div class="wrap">
    <a href="/admin" class="inline-link" style="font-size: 13px;">← Back to members</a>

    <div style="margin: 16px 0 8px; display: flex; flex-wrap: wrap; gap: 14px; align-items: baseline;">
      <h1 style="font-size: 30px; margin: 0;">{esc(member.get('title') or '')} {esc(member.get('name') or 'Unnamed member')}</h1>
      <span class="adm-phone" style="font-size: 18px;">{esc(pretty_phone(member.get('phone')))}</span>
    </div>
    <p style="color: var(--ink-dim); font-size: 13.5px; margin: 0 0 24px;">
      Member #{member['id']} · joined {fmt_date(member.get('created_at'))} ·
      access code <span class="mono">{esc(member.get('access_code') or 'not issued')}</span>
    </p>

    {flash_banner()}
    {merged_notice}

    <div class="adm-grid">
      <div>
        <div class="adm-panel" style="{'border-color:var(--red);' if member.get('unread_count') else ''}">
          <h3>Messages {unread_pill}</h3>
          <p class="hint">Replies also go to them on Telegram when a chat is linked.</p>
          <div style="max-height:260px; overflow-y:auto; background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:14px; margin-bottom:14px;">
            {thread_preview}
          </div>
          <form method="POST" action="/admin/messages/{member_id}">
            <input type="hidden" name="back" value="/admin/member/{member_id}">
            <input type="text" name="body" placeholder="Write a reply..." required
                   style="width:100%; background:var(--bg); border:1px solid var(--line); color:var(--ink); padding:11px 13px; border-radius:9px;">
            <button type="submit" style="margin-top:10px;">Send reply</button>
          </form>
          <p class="hint" style="margin:12px 0 0;">
            <a href="/admin/messages/{member_id}" class="inline-link">Open the full thread</a>
          </p>
        </div>

        <div class="adm-panel">
          <h3>Access</h3>
          <p class="hint">Tick to give access, untick to take it away. Changes apply the moment you save, no need for them to log out and back in.</p>
          <form method="POST" action="/admin/member/{member_id}/access">
            {ticks}
            <label class="tick" style="border-style: dashed;">
              <input type="checkbox" name="notify" value="1" checked>
              <span><b>Send them their links</b><small>Telegram message with the groups for whatever you just unlocked, plus their access code</small></span>
            </label>
            <button type="submit" style="margin-top: 14px;">Save access</button>
          </form>
        </div>

        {dupe_panel}
        {absorbed_panel}
      </div>

      <div>
        <div class="adm-panel">
          <h3>Person</h3>
          <div class="adm-field"><span>Phone (identifier)</span><strong class="adm-phone">{esc(pretty_phone(member.get('phone')))}</strong></div>
          <div class="adm-field"><span>Email</span><strong>{esc(member.get('email') or 'not set')}</strong></div>
          <div class="adm-field"><span>Telegram</span><strong>{tg_line}</strong></div>
          <div class="adm-field"><span>Telegram linked</span><strong>{'yes' if member.get('chat_id') else 'no chat linked'}</strong></div>
          <div class="adm-field"><span>Tier</span><strong>{esc(member.get('tier') or 'not set')}</strong></div>
          <div class="adm-field"><span>Status</span><strong>{esc(member.get('status') or 'not set')}</strong></div>
          <div class="adm-field"><span>Approved</span><strong>{fmt_date(member.get('approved_at')) or 'not set'}</strong></div>
        </div>

        <div class="adm-panel">
          <h3>Gold signals form</h3>
          <div class="adm-field"><span>Broker account</span><strong>{esc(member.get('account_number') or 'not submitted')}</strong></div>
          <div class="adm-field"><span>Deposit</span><strong>{esc(member.get('deposit_amount') or 'not submitted')}</strong></div>
          <div class="adm-field"><span>Verification code</span><strong>{esc(member.get('verification_code') or 'not set')}</strong></div>
          <div class="adm-field"><span>Submitted</span><strong>{fmt_date(member.get('created_at')) or 'not set'}</strong></div>
        </div>

        <div class="adm-panel" style="{'' if has_currency else 'opacity:0.75;'}">
          <h3>Extra signals form</h3>
          <div class="adm-field"><span>PU Prime account</span><strong>{esc(member.get('currency_account_number') or 'not submitted')}</strong></div>
          <div class="adm-field"><span>Deposit</span><strong>{esc(member.get('currency_deposit_amount') or 'not submitted')}</strong></div>
          <div class="adm-field"><span>Referred by</span><strong>{esc(member.get('referred_by') or 'not set')}</strong></div>
          <div class="adm-field"><span>Submitted</span><strong>{fmt_date(member.get('currency_submitted_at')) or 'not submitted'}</strong></div>
          <div class="adm-field"><span>Access</span><strong>{'granted' if 'signals_currency' in granted else 'not granted'}</strong></div>
        </div>

        <div class="adm-panel">
          <h3>Access code</h3>
          <p class="hint">This is what they type at /unlock. It's only sent once during onboarding, so resend it whenever they lose it.</p>
          <div style="background: var(--bg); border: 1px solid var(--line); border-radius: 10px; padding: 16px; text-align: center; margin-bottom: 14px;">
            <span class="mono" style="font-size: 21px; letter-spacing: .06em;">{esc(member.get('access_code') or 'no code issued yet')}</span>
          </div>
          {code_delivery_note}
          <form method="POST" action="/admin/member/{member_id}/resend-code">
            <button type="submit">Resend this code</button>
          </form>
          <form method="POST" action="/admin/member/{member_id}/new-code" style="margin-top: 10px;"
                onsubmit="return confirm('Issue a new code? Their current code stops working straight away.');">
            <button type="submit" class="btn btn-ghost" style="width: 100%;">Issue a new code instead</button>
          </form>
          <p class="hint" style="margin: 12px 0 0;">Only issue a new code if the old one has been shared around. It stops the old one working immediately.</p>
        </div>

        <div class="adm-panel adm-edit">
          <h3>Edit details</h3>
          <p class="hint">Correcting a phone number here also updates who this account matches for duplicates.</p>
          <form method="POST" action="/admin/member/{member_id}/update">
            <label>Phone number</label>
            <input type="tel" name="phone" value="{esc(member.get('phone') or '')}" placeholder="07700 900123">
            <label>Email</label>
            <input type="email" name="email" value="{esc(member.get('email') or '')}" placeholder="them@example.com">
            <label>Name</label>
            <input type="text" name="name" value="{esc(member.get('name') or '')}">
            <label>Title</label>
            <input type="text" name="title" value="{esc(member.get('title') or '')}">
            <label>Gold broker account number</label>
            <input type="text" name="account_number" value="{esc(member.get('account_number') or '')}">
            <label>Gold deposit</label>
            <input type="text" name="deposit_amount" value="{esc(member.get('deposit_amount') or '')}">
            <label>Extra signals (PU Prime) account number</label>
            <input type="text" name="currency_account_number" value="{esc(member.get('currency_account_number') or '')}">
            <label>Extra signals deposit</label>
            <input type="text" name="currency_deposit_amount" value="{esc(member.get('currency_deposit_amount') or '')}">
            <label>Telegram username</label>
            <input type="text" name="telegram_username" value="{esc(member.get('telegram_username') or '')}">
            <label>Tier</label>
            <select name="tier">{tier_options}</select>
            <label>Status</label>
            <select name="status">{status_options}</select>
            <label>Private notes</label>
            <textarea name="admin_notes" placeholder="Anything you want to remember about this member">{esc(member.get('admin_notes') or '')}</textarea>
            <button type="submit" style="margin-top: 16px;">Save details</button>
          </form>
        </div>

        <div class="adm-panel" style="border-color:var(--red);">
          <h3 style="color:var(--red);">Delete this profile</h3>
          <p class="hint">Removes the account, its messages and its access for good. Use it for test data. This cannot be undone.</p>
          <form method="POST" action="/admin/member/{member_id}/delete"
                onsubmit="return confirm('Delete {esc(member.get('name') or 'this member')} permanently? This cannot be undone.');">
            <button type="submit" class="btn btn-danger" style="width:100%;">Delete permanently</button>
          </form>
        </div>

        <div class="adm-panel">
          <h3>History</h3>
          <ul class="adm-log" style="padding: 0; margin: 0;">{log_html}</ul>
        </div>
      </div>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout(member.get("name") or "Member", content, ""))


@app.route("/admin/member/<int:member_id>/access", methods=["POST"])
def admin_member_access(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    wanted = set(request.form.getlist("sections"))
    granted, revoked = set_member_sections(member_id, wanted, actor="admin")

    # Giving someone access should also give them the means to use it. If they
    # have no code yet, mint one and approve them, the way Mark Paid does,
    # so nobody is left with access they can't log in to reach.
    code_note = ""
    if granted and request.form.get("notify"):
        code_note = " " + deliver_sections(member_id, set(granted))

    if not granted and not revoked:
        return redirect(f"/admin/member/{member_id}?ok=Nothing+changed")

    if revoked and request.form.get("notify"):
        member = get_member_by_id(member_id)
        if member and member.get("chat_id"):
            send_telegram_message(
                member["chat_id"],
                "Your Inner Circle access has been updated.\n\nAccess removed for: "
                + ", ".join(SECTION_LABELS[s] for s in revoked))

    bits = []
    if granted:
        bits.append("added " + ", ".join(SECTION_LABELS[s] for s in granted))
    if revoked:
        bits.append("removed " + ", ".join(SECTION_LABELS[s] for s in revoked))
    return redirect(f"/admin/member/{member_id}?ok=" + quote("Access saved: " + "; ".join(bits) + code_note))


@app.route("/admin/member/<int:member_id>/update", methods=["POST"])
def admin_member_update(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    raw_phone = request.form.get("phone", "").strip()
    if raw_phone and not normalize_phone(raw_phone):
        return redirect(f"/admin/member/{member_id}?err=" +
                        quote("That phone number doesn't look complete, so nothing was saved."))

    update_member_details(member_id, request.form.to_dict())
    return redirect(f"/admin/member/{member_id}?ok=" + quote("Details saved"))


@app.route("/admin/duplicates")
def admin_duplicates():
    if not session.get("admin"):
        return redirect(url_for("admin"))

    groups = find_all_duplicate_groups()
    leftover = 0
    conn = get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM members WHERE merged_into IS NOT NULL")
                leftover = int(cur.fetchone()[0])
        except Exception:
            leftover = 0
        finally:
            conn.close()

    cleanup = ""
    if leftover:
        cleanup = f"""
      <div class="adm-panel" style="border-color: var(--red);">
        <h3>Leftover merged records ({leftover})</h3>
        <p class="hint">Empty shells left behind by merging. Everything they held is already on the profiles they were folded into, so these can go.</p>
        <form method="POST" action="/admin/clear-merged"
              onsubmit="return confirm('Delete all {leftover} leftover records for good?');">
          <button type="submit" class="btn btn-danger">Delete all {leftover} leftover records</button>
        </form>
      </div>"""

    if not groups:
        body = cleanup + """
      <div class="adm-panel">
        <h3>All clear</h3>
        <p class="hint" style="margin: 0;">Every phone number on the books belongs to exactly one account.</p>
      </div>"""
    else:
        blocks = []
        for grp in groups:
            rows, options = [], []
            for i, m in enumerate(grp["members"]):
                sections = get_member_sections(m["id"])
                pills = "".join(f'<span class="pill on">{SECTION_LABELS[s]}</span>'
                                for s in SECTION_KEYS if s in sections) or '<span class="pill">no access yet</span>'
                rows.append(
                    f'<div class="dupe-row"><div class="grow">'
                    f'<a href="/admin/member/{m["id"]}" class="inline-link"><strong>#{m["id"]} {esc(m.get("name") or "unnamed")}</strong></a>'
                    f'<div class="adm-meta">{esc(m.get("tier") or "")} · {esc(m.get("status") or "")} · '
                    f'code {esc(m.get("access_code") or "none")} · created {fmt_date(m.get("created_at"))}</div>'
                    f'<div>{pills}</div></div>'
                    f'<label style="font-size:13px; display:flex; gap:7px; align-items:center;">'
                    f'<input type="checkbox" name="duplicate_ids" value="{m["id"]}" checked '
                    f'style="width:17px;height:17px;accent-color:var(--red);"> merge in</label>'
                    f'</div>'
                )
                options.append(
                    f'<option value="{m["id"]}" {"selected" if i == 0 else ""}>'
                    f'#{m["id"]} {esc(m.get("name") or "unnamed")} · {esc(m.get("status") or "")} · '
                    f'code {esc(m.get("access_code") or "none")}</option>'
                )
            blocks.append(f"""
      <div class="adm-panel">
        <h3>{esc(grp['phone'])}</h3>
        <p class="hint">{len(grp['members'])} accounts share this number.</p>
        <form method="POST" action="/admin/merge">
          {''.join(rows)}
          <div class="adm-edit" style="margin-top: 16px;">
            <label>Keep this profile</label>
            <select name="primary_id">{''.join(options)}</select>
          </div>
          <p class="hint" style="margin: 12px 0 0;">The profile you keep gets every unlocked section, the full message history, and its own access code. The others are hidden but kept, so this can be undone.</p>
          <button type="submit" style="margin-top: 12px;">Merge selected into kept profile</button>
        </form>
      </div>""")
        body = cleanup + "".join(blocks)

    if groups:
        total = sum(len(g["members"]) - 1 for g in groups)
        merge_all_button = f"""
    <div class="adm-panel" style="border-color: var(--gold);">
      <h3>Merge everything in one go</h3>
      <p class="hint">Folds all {len(groups)} groups at once, {total} duplicate record{'' if total == 1 else 's'} in total.
      Each person keeps their oldest approved record, with all access and messages combined. Reversible.</p>
      <form method="POST" action="/admin/merge-all"
            onsubmit="return confirm('Merge all {total} duplicate records now?');">
        <button type="submit">Merge all duplicates</button>
      </form>
    </div>"""
    else:
        merge_all_button = ""

    content = f"""
<section style="padding: 48px 0 70px;">
  <div class="wrap">
    <a href="/admin" class="inline-link" style="font-size: 13px;">← Back to members</a>
    <h1 style="font-size: 30px; margin: 16px 0 8px;">Duplicate accounts</h1>
    <p style="color: var(--ink-dim); margin-bottom: 20px;">Grouped by phone number, since that's what identifies a member.</p>
    {flash_banner()}
    {merge_all_button}
    {body}
  </div>
</section>
"""
    return render_template_string(base_layout("Duplicates", content, ""))


@app.route("/admin/merge-all", methods=["POST"])
def admin_merge_all():
    """
    Fold every duplicate group in one go. Each group keeps its oldest approved
    record, or its oldest record if none are approved. Reversible, same as any
    other merge.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    groups = find_all_duplicate_groups()
    merged_people, merged_records = 0, 0
    for grp in groups:
        ids = [m["id"] for m in grp["members"]]
        if len(ids) < 2:
            continue
        primary, dups = ids[0], ids[1:]
        if merge_members(primary, dups, actor="merge all"):
            merged_people += 1
            merged_records += len(dups)

    if not merged_people:
        return redirect("/admin/duplicates?ok=" + quote("Nothing to merge."))
    return redirect("/admin/duplicates?ok=" +
                    quote(f"Merged {merged_records} duplicate record"
                          f"{'' if merged_records == 1 else 's'} across "
                          f"{merged_people} {'person' if merged_people == 1 else 'people'}."))


@app.route("/admin/merge", methods=["POST"])
def admin_merge():
    if not session.get("admin"):
        return redirect(url_for("admin"))

    try:
        primary_id = int(request.form.get("primary_id"))
    except (TypeError, ValueError):
        return redirect("/admin/duplicates?err=" + quote("Pick which profile to keep first."))

    dup_ids = [int(d) for d in request.form.getlist("duplicate_ids") if str(d).isdigit()]
    dup_ids = [d for d in dup_ids if d != primary_id]
    if not dup_ids:
        return redirect("/admin/duplicates?err=" + quote("Tick at least one account to merge in."))

    result = merge_members(primary_id, dup_ids, actor="admin")
    if not result:
        return redirect("/admin/duplicates?err=" + quote("That merge didn't go through. Refresh and try again."))

    n = len(dup_ids)
    return redirect(f"/admin/member/{primary_id}?ok=" +
                    quote(f"Merged {n} account{'' if n == 1 else 's'} in. Messages and access were combined."))


@app.route("/admin/unmerge/<int:member_id>", methods=["POST"])
def admin_unmerge(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    unmerge_member(member_id)
    return redirect(f"/admin/member/{member_id}?ok=" +
                    quote("Separated back out. It's in the waiting list on its own."))


@app.route("/admin/messages/<int:member_id>", methods=["GET", "POST"])
def admin_messages(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    member = get_member_by_id(member_id)
    if not member:
        return redirect(url_for("admin"))

    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if body:
            add_message(member_id, "admin", body)
            if member.get("chat_id"):
                send_telegram_message(
                    member["chat_id"],
                    f"New message from Inner Circle:\n\n{body}\n\n"
                    f"Reply at https://innercircletrading.co/messages"
                )
        back = request.form.get("back") or request.referrer or ""
        if "/admin/member/" in back:
            return redirect(f"/admin/member/{member_id}?ok=" + quote("Reply sent"))
        return redirect(url_for("admin_messages", member_id=member_id))

    mark_messages_read(member_id)
    msgs = get_messages(member_id)
    if msgs:
        thread = "".join(
            f'<div style="margin-bottom: 18px; text-align: {"left" if m["sender"]=="member" else "right"};">'
            f'<div style="display: inline-block; max-width: 80%; text-align: left; padding: 12px 16px; border-radius: 14px; '
            f'background: {"var(--bg-alt-2)" if m["sender"]=="member" else "var(--gold)"}; '
            f'color: {"var(--ink)" if m["sender"]=="member" else "var(--bg)"};">'
            f'<div style="font-size: 11px; opacity: 0.7; margin-bottom: 4px;">{member.get("name","Member") if m["sender"]=="member" else "You"}</div>'
            f'{m["body"]}</div></div>'
            for m in msgs
        )
    else:
        thread = '<p style="color: var(--ink-dim); text-align: center; padding: 30px 0;">No messages yet.</p>'

    tg = member.get("telegram_username") or ""
    tg_line = f'<a href="https://t.me/{tg.lstrip("@")}" target="_blank" class="inline-link">Message on Telegram</a>' if tg else '<span style="color: var(--ink-dim);">No Telegram username on file, use this portal to reach them</span>'

    content = f"""
<section style="padding: 60px 0;">
  <div class="wrap" style="max-width: 640px;">
    <a href="/admin/member/{member_id}" class="inline-link" style="font-size: 13px;">← Back to profile</a>
    <h1 style="font-size: 26px; margin: 16px 0 6px;">{esc(member.get('title') or '')} {esc(member.get('name') or 'Member')}</h1>
    <p style="color: var(--ink-dim); font-size: 14px; margin-bottom: 8px;">
      <span class="adm-phone">{esc(pretty_phone(member.get('phone')))}</span> ·
      {esc(member.get('tier') or '')} · Acct {esc(member.get('account_number') or 'n/a')}
    </p>
    <p style="font-size: 13px; margin-bottom: 28px;">{tg_line}</p>
    <div class="form-panel" style="max-width: 100%; margin-bottom: 22px;">{thread}</div>
    <div class="form-panel" style="max-width: 100%;">
      <form method="POST">
        <label>Your reply</label>
        <input type="text" name="body" placeholder="Type your reply..." required>
        <button type="submit">Send Reply</button>
      </form>
      <p style="color: var(--ink-dim); font-size: 12px; margin-top: 12px;">They'll get this in the portal, and on Telegram too if we have their chat linked.</p>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Messages", content, ""))


@app.route("/admin/send-code", methods=["POST"])
def admin_send_code():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    target = request.form.get("target", "").strip().lstrip("@")
    if not target:
        return redirect(url_for("admin"))

    chat_id = None
    if target.lstrip("-").isdigit():
        chat_id = int(target)
    else:
        conn = get_db()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT chat_id FROM bot_contacts WHERE username = %s", (target.lower(),))
                    row = cur.fetchone()
                    if row:
                        chat_id = row[0]
            finally:
                conn.close()

    if chat_id:
        code = "IC-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
        save_verification_code(code, chat_id, target, 2)
        send_telegram_message(
            chat_id,
            f"Great news, your screenshots have been checked and everything looks right!\n\n"
            f"Your verification code is: {code}\n\n"
            f"Pop that into the form on the website along with your details and we'll get you approved."
        )
    else:
        notify_admin(f"Couldn't find a chat for '{target}'. They need to message the bot first.")
    return redirect(url_for("admin"))


@app.route("/admin/approve/<int:member_id>", methods=["POST"])
def admin_approve(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    pre = get_member_by_id(member_id)
    if pre and pre.get("tier") == "community":
        member = grant_community(member_id)
        if member:
            msg = (
                "You're in! Welcome to Wealth Circle \U0001F90D\n\n"
                "Here's your invite to our private women-only community:\n"
                "https://t.me/+TWaAqQlTTuU1OGU0\n\n"
                "You've also unlocked Female Wealth on the website, your private space with masterclasses, "
                "personal development and the group link any time you need it:\n"
                "https://innercircletrading.co/community\n\n"
                + (f"Log in with your access code: {member.get('access_code')}\n"
                   f"https://innercircletrading.co/unlock\n\n" if member.get('access_code') else "")
                + "If you're already logged in on the website, just log out and back in to see it appear."
            )
            if member.get("chat_id"):
                send_telegram_message(member["chat_id"], msg)
            else:
                notify_admin(f"Could not auto-message community member {member_id}. Send manually:\n\n{msg}")
        return redirect(url_for("admin"))

    member = approve_member(member_id)
    if member:
        if member["tier"] == "gold":
            msg = (
                f"You're approved! Welcome to Inner Circle.\n\n"
                f"Your gold signals Telegram group:\n{GOLD_GROUP_LINK}\n\n"
                f"And the Inner Circle group, where we share results and the trades "
                f"we've personally taken:\n{INNER_CIRCLE_GROUP_LINK}\n\n"
                f"Your website access code: {member['access_code']}\n\n"
                f"All your group links live here, so you can find them again any time:\n"
                f"https://innercircletrading.co/my-signals\n\n"
                f"To unlock your Education access, go to:\n"
                f"https://innercircletrading.co/unlock\n\n"
                f"Enter your code there and you're in. Keep this code safe, you'll need it again if you "
                f"switch phone or clear your browser.\n\n"
                f"Keep us updated with your results! Just message \"share results\" here and send your "
                f"screenshots, we love seeing how everyone is getting on."
            )
        else:
            msg = CURRENCY_APPROVED_MESSAGE
        if member.get("chat_id"):
            send_telegram_message(member["chat_id"], msg)
        else:
            notify_admin(f"⚠️ Could not auto-message member {member_id}, no chat_id on file. Send manually: {msg}")
    return redirect(url_for("admin"))


@app.route("/admin/grant-community/<int:member_id>", methods=["POST"])
def admin_grant_community(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    member = grant_community(member_id)
    if member:
        msg = (
            "You're in! Welcome to Female Wealth \U0001F90D\n\n"
            "Here's your invite to our private women-only community:\n"
            "https://t.me/+TWaAqQlTTuU1OGU0\n\n"
            "You've also unlocked Female Wealth on the website, your private space with masterclasses, "
            "mindset work and the group link any time you need it:\n"
            "https://innercircletrading.co/community\n\n"
            "Log in with your usual access code. If you're already logged in, just log out and back in "
            "to see it appear."
        )
        if member.get("chat_id"):
            send_telegram_message(member["chat_id"], msg)
        else:
            notify_admin(f"Could not auto-message member {member_id}. Send manually:\n\n{msg}")
    return redirect(url_for("admin"))


@app.route("/admin/mark-paid/<int:member_id>", methods=["POST"])
def admin_mark_paid(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    member = mark_paid(member_id)
    if member and member.get("chat_id"):
        send_telegram_message(member["chat_id"], "You're unlocked for Advanced Chart Reading! Head to the site and re-enter your access code if it's not already showing as unlocked.")
    return redirect(url_for("admin"))


@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    error = None
    if request.method == "POST":
        code = request.form.get("access_code", "").strip().upper()
        member = find_member_by_access_code(code)
        if member:
            session.permanent = True
            load_access_into_session(member)
            return redirect(url_for("account"))
        error = "That code wasn't recognised, double check it or contact us for help."

    content = f"""
<section style="padding: 90px 0;">
  <div class="wrap" style="max-width: 440px;">
    <div class="form-panel">
      <h3 style="font-size: 22px; margin-bottom: 10px;">Log in</h3>
      <p style="color: var(--ink-dim); font-size: 14px; margin: 0 0 20px;">Enter the access code we sent you on Telegram after your approval. It looks like AC-XXXXXXX.</p>
      <form method="POST">
        <input type="text" name="access_code" placeholder="AC-XXXXXXX" style="width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink); padding: 13px 16px; border-radius: 10px; text-transform: uppercase;">
        <button type="submit" style="margin-top: 16px;">Log In</button>
      </form>
      {f'<p style="color: var(--red); font-size: 13px; margin-top: 14px;">{error}</p>' if error else ''}
      <p style="color: var(--ink-dim); font-size: 13px; margin-top: 20px; border-top: 1px solid var(--line); padding-top: 20px;">
        Not got a code yet? You'll get one once your onboarding is approved.
        <br><a href="/onboarding" class="inline-link">Start onboarding →</a>
        <br><br>Lost your code? <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">Message us on Telegram</a> and we'll resend it.
      </p>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Log In", content, ""))


# ---------------------------------------------------------------------------
# TELEGRAM BOT WEBHOOK (merged in, was a separate service)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# BOT CONVERSATION STATE
# ---------------------------------------------------------------------------
# Kept in the database, not in memory. Render restarts on every deploy, and
# losing someone's place mid-onboarding is exactly the sort of dead end that
# makes people give up.

def get_bot_state(chat_id):
    if not chat_id:
        return {}
    conn = get_db()
    if not conn:
        return {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bot_state WHERE chat_id=%s", (chat_id,))
            return cur.fetchone() or {}
    except Exception:
        return {}
    finally:
        conn.close()


def set_bot_state(chat_id, state=None, photo_count=None, greeted=None,
                  tips_sent=None, pending_intent=None, stuck_count=None):
    if not chat_id:
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO bot_state
                             (chat_id, state, photo_count, greeted, tips_sent, pending_intent,
                              stuck_count, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                           ON CONFLICT (chat_id) DO UPDATE SET
                             state = COALESCE(%s, bot_state.state),
                             photo_count = COALESCE(%s, bot_state.photo_count),
                             greeted = COALESCE(%s, bot_state.greeted),
                             tips_sent = COALESCE(%s, bot_state.tips_sent),
                             pending_intent = COALESCE(%s, bot_state.pending_intent),
                             stuck_count = COALESCE(%s, bot_state.stuck_count),
                             updated_at = NOW()""",
                        (chat_id, state, photo_count or 0, bool(greeted), bool(tips_sent),
                         pending_intent, stuck_count or 0,
                         state, photo_count, greeted, tips_sent, pending_intent, stuck_count))
    except Exception:
        pass
    finally:
        conn.close()


def clear_bot_state(chat_id):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE bot_state SET state=NULL, photo_count=0, pending_intent=NULL, stuck_count=0,
                           updated_at=NOW() WHERE chat_id=%s""", (chat_id,))
    except Exception:
        pass
    finally:
        conn.close()


# legacy in-memory counters, kept so older code paths don't break
_photo_counts = {}
_payment_pending = {}
_results_pending = {}


def upsert_bot_contact(username, chat_id):
    if not username:
        return
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_contacts (username, chat_id, last_seen) VALUES (%s, %s, NOW())
                ON CONFLICT (username) DO UPDATE SET chat_id = EXCLUDED.chat_id, last_seen = NOW()
            """, (username.lstrip("@").lower(), chat_id))
    finally:
        conn.close()


def save_verification_code(code, chat_id, username, photo_count):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO verifications (code, chat_id, username, photo_count) VALUES (%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
            """, (code, chat_id, username, photo_count))
    finally:
        conn.close()


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

BOT_KNOWLEDGE = """
WHAT THE FREE COURSE COVERS (know this so you can tease it, don't teach it):
- Section 1, Placing a Trade: TP and SL, lot size, entry, placing your first trade, demo accounts, buy vs sell, reading and copying a signal, setting TP/SL, closing a trade, a pre-trade checklist.
- Section 2, Terminology: candles and wicks, bulls vs bears, pips, leverage, margin, order types, drawdown, retrace, trader types.
- Section 3, Advancing Your Trade: trailing stops, breakeven, partials, layering, averaging in, liquidity, volatility, funded accounts.
- Section 4, Trading Mindset: over-leveraging, emotions, revenge trading, discipline.
- Section 5, More to Know: risk, common mistakes, market hours, troubleshooting.
41 lessons total, all free, with diagrams throughout. You can mention these topics by name to show the depth, but don't explain them.

INNER CIRCLE, KEY FACTS

Joining:
- Joining Inner Circle is completely free.
- The only money involved is the member's own trading deposit, which goes into their own broker account. It stays theirs and they can withdraw it.
- Minimum deposit is £300. Whatever they deposit, the broker adds 50% on top as credit, free of charge (deposit £1,000, get £500 added).
- Our broker for gold signals is Kudo. Registration link is on the onboarding page of the website.

Onboarding steps (gold signals):
1. Register with Kudo via the link on the website.
2. Verify identity, check email from broker, upload proof of age and address.
3. Deposit (minimum £300).
4. Link the account in MT5: menu > Manage Accounts > '+' > Login to an existing account.
Then activation:
5. Add EUR/USD to watchlist (Quotes tab, search icon, type EUR/USD).
6. Place 10 trades at 0.01 volume: 5 Sell by Market, 5 Buy by Market. Trade tab > '+' top right > check it says EUR/USD > set middle lot number to 0.01 > press Sell or Buy by Market.
7. Close all 10: Trade tab, press and hold each, tap the orange Close banner.
8. Screenshot the History tab showing all 10 closed, plus the deposit confirmation screenshot, and send both to this bot to get a verification code.
9. Enter that code plus their details on the website form.
The 10 trades are purely for setup, to confirm the account is connected and they know how to open and close. They are not meant to hold them open.

Extra Signals (currency, PU Prime):
- A separate PU Prime account unlocks currency signals and more groups.
- They tap their referrer's name on the Extra Signals page, register, deposit (100% deposit match credit from broker), link in MT5.
- No activation trades needed for this one.
- They can use the new account just for currency signals, or for all their signals if they prefer one account.
- Already have an active PU Prime account? They should contact the team instead of registering again.

Education:
- Free course: Trading Fundamentals, 41 lessons across 5 sections. Unlocks once approved.
- Paid course: Advanced Chart Reading & Technical Analysis, £99 one-time, 23 lessons. Unlocks separately once paid.

Community:
- Wealth Circle is the private community, women only.
- Signals, education and support are open to everyone; only Wealth Circle is female-only.

Lot sizing rough guide (gold): about 0.01 lots per £100 of account balance. £300 = 0.03, £1,000 = 0.10, £5,000 = 0.50. This is a rough guide only, not advice.

Approval:
- After submitting the form, the team manually checks it, then sends their Telegram group link and a website access code.
- They enter that access code on the /unlock page of the website to unlock Education.
"""

BOT_SYSTEM_PROMPT = f"""You are the support assistant for Inner Circle, a trading signals and education community.

YOUR VOICE (this applies to every single reply, no exceptions):
- Warm, friendly and natural. Write like a real person messaging someone, not like a corporate bot.
- Use contractions and relaxed openers. "Good question", "Honestly", "So", "Absolutely", "No worries at all", "Ah I see" all work well.
- Never use em dashes. Use commas or full stops instead.
- Use proper punctuation and full sentences. Break longer answers into short paragraphs with a blank line between them, or a numbered list. It needs to be easy to read on a phone.
- Be encouraging without hype. No exclamation marks everywhere, no emoji spam. One emoji occasionally is fine.
- Never make someone feel like their question was too basic. Most people asking are complete beginners and nervous about it.
- End with a question or a next step where it makes sense, keep the conversation moving forward.
- Never sound like a FAQ page. Even a simple factual answer should sound like a person saying it.

More examples of the right tone:
Them: "is it free?"
You: "Yeah, joining is completely free. The only money involved is your own trading deposit, and that goes into your own broker account, so it stays yours and you can withdraw it whenever you like.

Minimum is £300 to get started, and the broker adds 50% on top as credit. Want me to run you through how to get set up?"

Them: "how much can I make?"
You: "Honestly, I can't give you a number on that, and I'd be wary of anyone who does. It depends on your account size, the market, and how closely you stick to the risk management side.

What I can tell you is you'll get copy and paste signals and the full beginners course, so you're not guessing at any of it. The course covers the risk side properly too, which is genuinely the part that matters most early on."

Them: "i've never traded before"
You: "That's completely fine, honestly most people joining are in exactly the same spot. That's the whole reason the beginners course exists.

It starts right at the beginning, what a trade actually is, how to place one, what all the terms mean. And the signals are copy and paste, so you're not left trying to analyse charts on day one. Would you like the steps to get started?"

HONESTY RULES (these matter):
- If someone asks whether you're a bot, a real person, or who you are: tell them honestly that you're the Inner Circle support assistant, an automated helper, and offer to pass them to a real team member.
- Never claim to be a specific human, never invent a name, age, gender, or personal trading history.
- Never say things like "I've been trading for years" or reference personal experience you don't have.

HOW YOU HANDLE TRADING QUESTIONS (this is important):
Our free beginners course is the product, and the real hook is that members get copy and paste signals. Don't teach the course away in chat. When someone asks how trading works, how to place a trade, what a pip is, what lot size to use, or anything the course covers, do this instead:

1. Warm, brief acknowledgement that shows you know the answer. One or two sentences, enough to show there's substance. Don't teach the steps.
2. Mention that members get copy and paste signals, so they're not left working it out alone.
3. Tell them the free course covers it properly.
4. Ask if they'd like the steps to get access.

Example of the right shape:
Them: "how do I trade?"
You: "Good question, and honestly it's simpler than most people expect once someone actually shows you. You pick what you're trading, choose your direction, set your size and your safety levels, then place it.

The bit most people don't realise is you don't have to figure out the what and when on your own. Our signals are copy and paste, we send the pair, the direction and the levels, you just enter them exactly as they are.

Our free beginners course walks you through the whole thing step by step with screenshots, so you know exactly what you're doing before you place anything. Would you like the steps to get access?"

Then if they say yes, or ask how to join:
"Absolutely, it's completely free to join. Here's how it works:

1. Register with our broker using this link: https://go.kudo.com/visit/?bta=35562&brand=kudotrade
2. Verify your ID, they'll email you asking for proof of age and address.
3. Deposit, minimum is £300, and whatever you put in the broker adds 50% on top as credit. So £1,000 in becomes £1,500.
4. Link the account in MT5.

Then there's a quick setup check and you're in, with the copy and paste signals and the full course. All the steps are on the website too if you'd rather follow along there.

Worth saying, the deposit goes into your own broker account. It's your money, it stays yours, and you can withdraw it whenever."

Never dump the full course content into a message. Tease, mention the signals, then offer the route in.

WHAT YOU SHOULD ALWAYS ANSWER FULLY (not course content, just help):
- Anything about joining, pricing, deposits, what's included, how approval works.
- Where they are in the process, what's next, what they're waiting on.
- Technical problems getting set up, or if something isn't working.
- Their verification code, their access code, group links.

WHAT YOU MUST NOT DO:
- Don't tell someone what specific trade to take, when to enter or exit a live position, or how much of their money to risk.
- Don't predict market movements or promise profits, returns, or win rates.
- Don't guarantee results. Trading carries real risk and losses are normal.
- Don't invent facts, links, prices or policies. If you genuinely don't know something specific about Inner Circle, say so and offer to pass it to the team.

KNOWLEDGE BASE (only use facts from here for specifics):
{BOT_KNOWLEDGE}
"""


_conversations = {}

GETTING_STARTED = """Absolutely, it's completely free to join. Here's how it works:

1. Register with our broker using this link: https://go.kudo.com/visit/?bta=35562&brand=kudotrade
2. Verify your ID, they'll email you asking for proof of age and address.
3. Deposit, minimum is £300, and whatever you put in the broker adds 50% on top as credit. So £1,000 in becomes £1,500.
4. Link the account in MT5.

Then there's a quick setup check and you're in, with the copy and paste signals and the full beginners course.

Worth saying, the deposit goes into your own broker account. It's your money, it stays yours, and you can withdraw it whenever."""


# Each entry: (list of trigger keywords, reply, whether it offers the steps next)
KEYWORD_REPLIES = [
    (["menu", "options", "what can you do", "what can you help", "list"],
     """Here's what I can point you to:

🚀 ONBOARDING, getting set up with your account and signals
📚 EDUCATION, our free beginners course, 41 lessons
📈 ADVANCED EDUCATION, chart reading, £99 one-time
👭 COMMUNITY, Wealth Circle, our private women-only group
➕ EXTRA SIGNALS, more groups and more coverage

Just say the word and I'll take you through it.""",
     False),

    (["what is a pip", "whats a pip", "what's a pip", "define pip", "explain pip",
      "what does pip mean", "pips mean", "what are pips"],
     """A pip is just the smallest standard price movement, it's how traders measure how far price has moved. So if someone says a trade made 20 pips, that's the distance it travelled in your favour.

That's exactly the sort of thing our free beginners course covers properly, all the terminology explained in plain English with diagrams. Would you like the steps to get access?""",
     True),

    (["who are you", "what are you", "are you a bot", "are you real", "are you human",
      "am i talking to a person", "is this a bot", "real person", "your name"],
     """I'm the Inner Circle support assistant, an automated helper here to answer questions and get you set up.

If you'd rather speak to someone from the team directly, just say and I'll pass you over. What can I help with?""",
     False),

    (["what is inner circle", "what do you do", "tell me about", "what is this",
      "whats this about", "what's this about", "explain inner circle", "about you"],
     """So Inner Circle is a trading signals and education community. Three main parts to it:

1. Copy and paste signals, we send the pair, direction and levels, you enter them exactly as they are.
2. A free beginners course, 41 lessons taking you from complete beginner to placing trades with confidence.
3. Wealth Circle, our private women-only community.

Joining is completely free. Would you like the steps to get started?""",
     True),

    (["advanced course", "chart reading", "technical analysis", "advanced training",
      "paid course", "99", "£99", "advanced access", "unlock advanced", "advanced"],
     """The Advanced Chart Reading course is £99 one-time, 23 lessons on candlesticks, market structure, liquidity, Fibonacci and building your own strategy.

Quick check first though, have you already been through our free beginners course? It's worth doing that one first as Advanced builds straight on top of it.

If you're ready, head to innercircletrading.co/education/advanced and there's a payment link right there.""",
     False),

    (["fundamentals", "free course", "beginners course", "basics course", "enrol", "enroll",
      "how do i access the course", "access the course", "start the course"],
     """The free beginners course unlocks once you've completed onboarding and been approved.

If you've already got your access code, just head to innercircletrading.co/unlock and pop it in.

Not started yet? Would you like the steps to get set up?""",
     True),

    (["join community", "wealth circle", "womens group", "women's group", "female group",
      "join the community", "community access", "request community"],
     """Wealth Circle is our private women-only community. It's a supportive space to ask questions and share wins without the noise you get in most trading groups.

You'll need to have completed onboarding first, then you can request to join at innercircletrading.co/community and we'll review it personally.

Want the steps to get onboarded first?""",
     True),

    (["where do i go", "which page", "what page", "where is", "how do i find",
      "cant find the page", "can't find the page", "website", "the site", "link to site"],
     """Everything lives at innercircletrading.co. Quick map:

Onboarding, /onboarding
Log in with your code, /unlock
Courses, /education
Community request, /community
Extra signals, /signals

What were you trying to find?""",
     False),

    (["extra signals", "more signals", "other signals", "currency signals",
      "additional signals", "pu prime", "puprime", "second account"],
     """Extra signals unlock more groups, currency pairs, different sessions and different styles.

Quick check though, have you already completed the main onboarding and got your gold signals running? It's worth having that sorted first.

If you're all set there, head to innercircletrading.co/signals and the steps are laid out. You'll register a PU Prime account, then fill in the form and we'll approve you.""",
     False),

    (["my code", "access code", "lost my code", "forgot my code", "code not working",
      "cant log in to the site", "wont accept my code", "invalid code"],
     """No problem. Your access code looks like AC-XXXXXXX and you enter it at innercircletrading.co/unlock.

If it's not being accepted, double check for spaces at the start or end. Still stuck? Let me know and I'll get the team to resend it.""",
     False),

    (["thanks", "thank you", "cheers", "appreciate it", "ta ", "thankyou", "nice one"],
     """You're very welcome! Anything else you need, just ask.""",
     False),

    (["bye", "goodbye", "see you", "later", "cya", "speak soon"],
     """No worries, speak soon! I'm here whenever you need anything.""",
     False),

    (["how do i trade", "how to trade", "how does trading work", "how do you trade",
      "how does it work", "how do i place", "how to place", "teach me"],
     """Good question, and honestly it's simpler than most people expect once someone actually shows you. You pick what you're trading, choose your direction, set your size and your safety levels, then place it.

The bit most people don't realise is you don't have to work out the what and when on your own. Our signals are copy and paste, we send you the pair, the direction and the levels, and you just enter them exactly as they are.

Our free beginners course walks you through the whole thing step by step with screenshots, so you know exactly what you're doing before you place anything. Would you like the steps to get access?""",
     True),

    (["how do i start", "how can i start", "how to start", "how do i join", "how to join",
      "want to join", "sign up", "get started", "getting started", "how do i get in",
      "onboarding", "onboard", "set up", "setup", "register"],
     GETTING_STARTED, False),

    (["is it free", "how much does it cost", "what does it cost", "is there a fee",
      "do i have to pay", "cost to join", "free to join"],
     """Yeah, joining is completely free. The only money involved is your own trading deposit, and that goes into your own broker account, so it stays yours and you can withdraw it whenever you like.

Minimum is £300 to get started, and the broker adds 50% on top as credit. You also get the copy and paste signals and the full beginners course included.

Want me to run you through how to get set up?""",
     True),

    (["never traded", "no experience", "complete beginner", "total beginner", "new to this",
      "new to trading", "beginner", "don't know anything", "dont know anything"],
     """That's completely fine, honestly most people joining are in exactly the same spot. That's the whole reason the beginners course exists.

It starts right at the beginning, what a trade actually is, how to place one, what all the terms mean. And the signals are copy and paste, so you're not left trying to analyse charts on day one.

Would you like the steps to get started?""",
     True),

    (["how do i stop a trade", "how do i close", "how to close", "stop a trade",
      "close a trade", "close my trade", "get out of a trade", "exit a trade",
      "how do i end", "cancel a trade"],
     """So to close one, head to your Trade tab, press and hold the trade you want to close, then tap the orange Close banner that comes up. That's it, it'll move over to your History tab.

The course covers this properly with screenshots, including when it makes sense to close early versus letting your levels do the work. Would you like the steps to get access?""",
     True),

    (["can't deposit", "cant deposit", "deposit not working", "deposit failed",
      "payment not working", "card declined", "won't let me deposit", "wont let me deposit",
      "problem depositing", "issue with deposit", "trouble depositing"],
     """Ah that's frustrating, sorry about that. Deposit issues are usually something on the broker's side rather than anything to do with us, so a few things worth trying first.

Check your card allows international or online payments, some banks block these by default. It's also worth trying a different payment method if they offer one, and making sure your account is fully verified with them first.

If none of that sorts it, the broker's own support team can see exactly what's blocking it. Let me know how you get on and I'll get someone from the team to help if you're still stuck.""",
     False),

    (["more signals", "extra signals", "other signals", "another group", "more groups",
      "currency signals", "additional signals", "pu prime", "puprime"],
     """Yeah, there's extra signals available on top of your gold ones. It's a second account with a different broker which unlocks the currency signals and a few more groups, some focused on different styles or different sessions through the day.

There's no extra activation needed for it either, since you already know how to place and close a trade by that point.

It's all laid out on the Extra Signals page of the website. Want me to point you there?""",
     False),

    (["trades aren't good", "trades arent good", "bad trades", "losing", "lost money",
      "down this week", "not going well", "keep losing", "in a loss", "bad week",
      "not working out", "disappointed"],
     """Sorry to hear that, and honestly, losing trades are a normal part of this. Nobody wins every trade, and anyone telling you otherwise isn't being straight with you.

What usually matters more than any single trade is the risk management side, keeping your lot sizes sensible for your balance and letting your stop losses do their job. The course covers that properly and it's genuinely the part that makes the difference long term.

Stick with it, and don't be tempted to size up to win it back. That's the one thing that turns a rough patch into a real problem.""",
     False),

    (["brill", "brilliant", "great week", "good week", "smashed it", "made money",
      "profit this week", "going well", "loving it", "thank you", "thanks", "amazing"],
     """Love to hear that! Genuinely pleased it's going well for you.

Keep doing what you're doing, stick to your risk management and don't be tempted to size up too quickly after a good run, that's usually where people come unstuck.

Anything you need, just shout.""",
     False),

    (["forgot password", "forgot my password", "forgotten password", "forgotten my password",
      "reset password", "reset my password", "lost my password", "can't log in",
      "cant log in", "can't login", "cant login", "won't let me log in", "locked out",
      "invalid account", "wrong password", "login failed"],
     """No worries, that one's usually straightforward. Your MT5 login details come from the email the broker sent you when you registered, so it's worth digging that out first, the password there is different from your broker website password.

If you've genuinely lost it, the broker can reset it for you from their client portal or their support team.

One other thing worth checking, make sure you've selected the right server when logging in. Picking the wrong one is the most common reason it says invalid account when everything else is correct.""",
     False),

    (["can't find the pair", "cant find the pair", "can't find eur", "cant find eur",
      "pair not showing", "can't find gold", "cant find gold", "xauusd not showing",
      "symbol not found", "can't find the symbol"],
     """Ah, that usually just means it hasn't been added to your watchlist yet. Head to the Quotes tab, tap the search icon at the top, type the pair you're after, then tap it in the results and it'll get added.

Worth knowing some brokers name things slightly differently, gold might show as XAUUSD, and some pairs have a suffix on the end. If you search the first few letters it should come up.

Still not finding it? Let me know which pair and I'll check for you.""",
     False),

    (["not enough money", "insufficient margin", "no money", "not enough funds",
      "margin error", "trade rejected", "won't let me place", "wont let me place",
      "can't place a trade", "cant place a trade"],
     """That usually means the lot size is too big for what's in the account, rather than anything actually being wrong.

Try dropping it right down to 0.01, which is the smallest size, and see if it goes through. If it does, that confirms it was just a sizing thing.

As a rough guide, around 0.01 lots per £100 in your account is a sensible starting point. The course covers lot sizing properly if you want the full picture.""",
     False),

    (["market closed", "market is closed", "can't trade right now", "nothing happening",
      "prices not moving", "frozen", "not updating"],
     """That'll be the market being closed. Forex and gold trade Monday through Friday and shut over the weekend, so nothing moves until it reopens Sunday evening into Monday.

If it's mid-week and prices genuinely aren't updating, try closing the app fully and reopening it, that usually sorts a stuck connection.""",
     False),

    (["didn't get the email", "didnt get the email", "no email", "email not arrived",
      "haven't received", "havent received", "waiting for email", "no verification email"],
     """First thing worth doing is checking your spam or junk folder, broker emails end up in there more often than you'd think.

If it's genuinely not arrived after a little while, the broker can resend it from their client portal, or their support team can trigger it manually.

Double check the email address you registered with too, a typo there is the other common culprit.""",
     False),

    (["how do i withdraw", "withdraw my money", "take money out", "get my money out",
      "cash out"],
     """Withdrawals go through the broker directly, since that's who holds your account. You'd request it from their client portal and it goes back to the same method you deposited with.

Timing varies by broker and payment method, usually a few working days. Your money is yours throughout, we never hold or touch it.

If you're having trouble with a specific withdrawal, the broker's support team can see exactly what's happening with it.""",
     False),

    (["app crashing", "app keeps closing", "app not working", "mt5 not working",
      "app frozen", "keeps crashing", "app won't open", "app wont open"],
     """Annoying when that happens. Usual fixes in order: close the app fully and reopen it, then restart your phone if that doesn't do it, then check the app store for an update.

If it's still playing up after all that, deleting and reinstalling MT5 is safe to do. Your account details live with the broker, not the app, so you'd just log back in with the same details and nothing is lost.""",
     False),

    (["wrong trade", "placed the wrong", "made a mistake", "opened by accident",
      "wrong direction", "bought instead of sold", "sold instead of bought"],
     """No panic, easily fixed. Go to your Trade tab, press and hold the trade you didn't mean to open, and tap the orange Close banner. That closes it out.

Catching it straight away usually means the difference is tiny. It happens to most people at least once, especially early on.""",
     False),

    (["missed the entry", "missed the signal", "too late", "price moved", "price already moved",
      "signal already gone", "can i still enter", "still take it", "late to the trade"],
     """Happens all the time, don't stress it. If price has moved well past the entry, the risk to reward isn't the same anymore, so chasing it usually isn't worth it.

Better to let that one go and wait for the next. There'll always be another signal, but you don't get the money back from a bad entry.

If it's only moved a little and still sits inside the entry range, you're generally fine to take it.""",
     False),

    (["stopped out", "hit my stop", "hit sl", "stop loss hit", "wicked out", "wicked me out",
      "took my stop", "stopped me out"],
     """Frustrating one, especially when it reverses right after. That's a wick taking out your stop, and it's a normal part of trading rather than anything going wrong.

It's worth knowing that if your stop sits exactly on an obvious level, it's more likely to get caught by a wick before price moves the way you expected. Some traders leave a small buffer beyond those levels for that reason.

The course covers this properly in the wicks lesson, and it's genuinely worth a read if it keeps happening.""",
     False),

    (["should i close", "should i hold", "what do i do with", "shall i close",
      "do i close it", "hold or close", "should i take profit", "should i cut"],
     """I can't tell you what to do with a live position, that has to be your call. What I can say is that if you set your TP and SL when you entered, they're already doing their job.

The trouble usually starts when people override their own levels partway through because of how a trade feels in the moment. That's covered properly in the mindset section of the course.

If you're unsure about your setup generally rather than this specific trade, happy to help there.""",
     False),

    (["moved my stop", "moved my sl", "removed my stop", "no stop loss", "didn't set a stop",
      "didnt set a stop", "forgot my stop", "trade running away"],
     """Right, get a stop on it if you can. Open the trade from your Trade tab, edit it and add a Stop Loss at a level you're genuinely comfortable losing to.

Running without one is how small losses turn into account-ending ones, so it's worth doing now rather than hoping it comes back.

Going forward, set your SL at the same time you place the trade, before you're emotionally invested in the outcome.""",
     False),

    (["over leveraged", "overleveraged", "too big", "lot size too big", "risked too much",
      "margin call", "account blown", "blew my account", "lost it all"],
     """That's a rough one and I'm sorry. It's almost always position size rather than bad luck, a lot size too big for the balance means normal market movement can wipe you out.

The guide we use is roughly 0.01 lots per £100 in your account, so £300 is around 0.03. It feels slow, but it's what keeps you in the game long enough to actually learn.

The over-leveraging lesson in the course goes into this properly, and honestly it's the most important one in there.""",
     False),

    (["revenge trading", "trying to win it back", "win it back", "chasing losses",
      "keep entering", "overtrading", "trading too much", "can't stop trading"],
     """Recognising it is genuinely the hard part, so credit for that. Trying to win it back is where most people do their real damage, because the trades stop being about the setup and start being about the loss.

Best thing you can do is stop for the day. Not forever, just today. Come back tomorrow with a clear head and normal position sizes.

There's a whole section on this in the course, revenge trading and managing emotions, and it's worth going through properly.""",
     False),

    (["spread", "why is my trade negative", "started negative", "opened in loss",
      "instantly down", "straight into loss", "why am i down straight away"],
     """That's the spread, and it's completely normal. Every pair has a small gap between the buy price and the sell price, so every trade opens slightly negative and needs to move a little in your favour before it turns positive.

It's not a fee we charge, it's how the market works everywhere. Spreads do widen around big news though, so it can look worse at those times.""",
     False),

    (["what pair", "which pair", "what should i trade", "what to trade", "which market",
      "gold or", "what do you trade"],
     """Our main signals are on gold, which is XAUUSD in your app. There are currency signals available too through the extra signals side of things.

You don't need to pick markets yourself to get started though, that's the point of the copy and paste signals. We send the pair along with everything else.""",
     False),

    (["how many trades", "how often", "how many signals", "how many a day",
      "when do you send", "what time", "signal times"],
     """It varies day to day depending on what the market's actually doing, rather than forcing a set number out. Some days there are several, some days it's quiet.

They tend to come through around the main trading sessions. The extra signals groups cover different sessions through the day if you want more coverage.""",
     False),

    (["news", "nfp", "high impact", "big news", "should i trade the news",
      "economic calendar", "cpi"],
     """Big news releases make prices move fast and unpredictably, and spreads widen right around them, so it's a risky time to have positions open if you're newer to this.

Plenty of traders just stay out around major releases and pick things back up after. MT5 has an economic calendar built in so you can see what's coming.

The course covers news risk properly in the market hours section.""",
     False),

    (["how much can i make", "how much will i make", "how much profit", "what returns",
      "win rate", "guaranteed", "how much money"],
     """Honestly, I can't give you a number on that, and I'd be wary of anyone who does. It depends on your account size, the market, and how closely you stick to the risk management side.

What I can tell you is you'll get copy and paste signals and the full beginners course, so you're not guessing at any of it. The course covers the risk side properly too, which is genuinely the part that matters most early on.""",
     False),

    (["how much do i need", "minimum deposit", "min deposit", "how much to start",
      "300", "£300", "deposit amount"],
     """Minimum is £300 to get started. Whatever you deposit, the broker adds 50% on top as credit, so £1,000 in becomes £1,500.

That money goes into your own broker account by the way, it's yours, and you can withdraw it whenever you want. We never hold your funds.

Want the steps to get set up?""",
     True),

    (["what is a signal", "what are signals", "copy and paste", "how do signals work",
      "what do i get", "what's included", "whats included"],
     """So a signal is basically the trade laid out for you. We send the pair, whether it's a buy or a sell, and the levels to set.

You copy those straight into your app exactly as they are, that's genuinely it. No chart reading needed to get started.

You also get our free beginners course, 41 lessons covering everything from the basics through to managing your trades properly. Would you like the steps to get access?""",
     True),

    (["is it safe", "is this legit", "scam", "can i trust", "is it real",
      "withdraw", "get my money back"],
     """Completely fair question to ask. Your deposit goes into your own account with the broker, in your name. We never hold or touch your money, and you can withdraw it whenever you like.

Worth being straight with you though, trading itself carries real risk. Prices move both ways and losses are a normal part of it, which is exactly why the course spends so much time on risk management.

Anything specific you'd like to know?""",
     False),

    (["mt5", "metatrader", "what app", "which app", "download"],
     """That's MetaTrader 5, or MT5 for short. It's a free app, you just download it from your phone's app store and log in with the details the broker emails you.

That's where you'll place your trades. The course walks through the whole setup with screenshots so you're not guessing at any of it.

Would you like the steps to get started?""",
     True),

    (["community", "group", "women", "female", "wealth circle"],
     """So there's the signals groups which everyone gets access to, and then there's Wealth Circle, which is our private community specifically for women.

It's a supportive space to ask questions and share wins without the noise you get in most trading groups. Everything else, the signals, the course, the support, is open to anyone.

Want me to run you through how to join?""",
     True),

    (["course", "education", "learn", "training", "lessons"],
     """Our free beginners course is 41 lessons across 5 sections. It covers placing your first trade, all the terminology, managing trades properly, the mindset side, and the practical stuff most guides skip.

There's also an advanced chart reading course for £99 one-time if you want to go further and learn to read charts yourself, but that's completely optional.

The beginners course is free and included. Would you like the steps to get access?""",
     True),

    (["verification code", "my code", "verify", "screenshot"],
     """Send me both of your screenshots here, your deposit confirmation and your closed trades history, and I'll generate your verification code straight away.

Once you've got it, pop it into the form on the website along with your details and we'll get you approved.""",
     False),

    (["how long", "how quick", "when will i", "waiting", "approved yet"],
     """Approvals are usually done within 24 hours, often quicker. Once you're approved you'll get your group link and your website access code sent straight to you here.

If it's been longer than that, let me know and I'll chase it up for you.""",
     False),

    (["stuck", "not working", "problem", "issue", "error", "help me", "confused"],
     """No worries at all, happens to loads of people. Tell me what step you're on and what's happening and I'll see if I can sort it, and if not I'll get someone from the team onto it.""",
     False),
]

YES_WORDS = ["yes", "yeah", "yep", "yh", "ok", "okay", "sure", "please", "go on",
             "sounds good", "id like", "i'd like", "definitely", "yes please", "go ahead"]


def keyword_reply(user_message: str, chat_id=None):
    """Returns (reply_text, matched_bool). Handles yes/no follow-ups too."""
    text = (user_message or "").lower().strip()

    # Handle a "yes" to a previous offer of the steps
    if chat_id and _conversations.get(chat_id) == "offered_steps":
        if any(text.startswith(w) or text == w for w in YES_WORDS):
            _conversations.pop(chat_id, None)
            return GETTING_STARTED, True

    def hit(kw, haystack):
        """
        Short keywords must match whole words. Without this "hi" matches inside
        "nothing" and "this", which is how the bot ended up greeting people who
        had asked a real question.
        """
        kw = kw.strip()
        if len(kw) <= 4 and " " not in kw:
            return re.search(rf"\b{re.escape(kw)}\b", haystack) is not None
        return kw in haystack

    for keywords, reply_text, offers_steps in KEYWORD_REPLIES:
        for kw in keywords:
            if hit(kw, text):
                if chat_id:
                    if offers_steps:
                        _conversations[chat_id] = "offered_steps"
                    else:
                        _conversations.pop(chat_id, None)
                return reply_text, True

    return None, False


# ---------------------------------------------------------------------------
# WHAT THE BOT UNDERSTANDS
# ---------------------------------------------------------------------------
# One entry per thing someone might want. Phrases are matched against the whole
# message; the intent with the most specific match wins, and if two are close
# the bot asks which one they meant rather than guessing wrong.

SITE = "https://innercircletrading.co"

# Short plain English explanations of the things people ask about most. These
# go to members who already have the course, so they get a real answer rather
# than only a link. Everyone else is pointed at the course itself.
FUNDAMENTALS_OVERVIEWS = {
    "pip": ("A pip is the smallest standard price move, and it's how traders measure distance. "
            "On gold, a 100 pip move is $1.00 of price. If a trade makes 20 pips, that's how far "
            "it went your way."),
    "lot size": ("Lot size is how much each pip is worth to you, so it decides how big your wins "
                 "and losses are. The guide we use is roughly 0.01 lots per £100 in the account, "
                 "so £300 is about 0.03. Small feels slow, but it keeps you in the game."),
    "stop loss": ("A stop loss is the price where you accept the trade is wrong and get out "
                  "automatically. It's set when you open the trade, not after, and it's the "
                  "single thing that stops one bad trade doing real damage."),
    "take profit": ("Take profit is the price where the trade closes in your favour automatically. "
                    "Setting it up front means you don't have to watch the chart or talk yourself "
                    "out of a good exit."),
    "spread": ("The spread is the small gap between the buy and sell price, and it's how the broker "
               "gets paid. It's why a trade opens slightly in the red. It widens around news."),
    "leverage": ("Leverage lets you control a bigger position than your balance would allow. It "
                 "magnifies both directions, which is why position size matters far more than the "
                 "leverage number itself."),
    "trailing": ("Trailing a stop means moving your stop loss along behind the price as the trade "
                 "goes your way, so you lock in more of the move. Move it in steps, and only "
                 "behind real structure, not tick by tick."),
    "partials": ("Taking partials means closing part of the position at a target and letting the "
                 "rest run. You bank something real while keeping upside, and it makes holding a "
                 "winner much easier psychologically."),
    "support": ("Support is a price level where buyers have stepped in before, resistance is where "
                "sellers have. They're areas rather than exact lines, and they matter because "
                "price tends to react around them again."),
    "drawdown": ("Drawdown is how far your account has fallen from its high point. Every strategy "
                 "has it. Managing it is about position size, not about avoiding losing trades."),
    "risk": ("Risk management is deciding what you can lose before you enter, sizing the trade so "
             "that loss is survivable, and accepting it when it happens. It matters more than "
             "entries do."),
}


BOT_INTENTS = [
    # name,            phrases people actually use
    ("paid_advanced", [
        "i've paid the advanced", "ive paid the advanced", "paid the advanced",
        "advanced paid", "paid advanced", "paid for advanced", "paid for the advanced",
        "payment for advanced", "advanced payment", "advanced course paid",
        "proof of payment", "payment proof", "paid £99", "paid 99", "i've paid", "ive paid",
        "just paid", "i have paid", "payment sent", "sent payment", "paid it",
    ]),
    ("advanced_course", [
        "unlock advanced", "advanced course", "paid course", "chart reading", "advanced education",
        "technical analysis course", "advanced", "buy the course", "the £99", "the 99 course",
    ]),
    ("onboarding", [
        "onboarding", "onboard", "how do i start", "how do i join", "how to join",
        "sign up", "signup", "get started", "getting started", "become a member",
        "want to join", "how do i get signals", "get signals", "how do i get in",
        "join inner circle", "how does this work",
    ]),
    ("activate", [
        "activate", "activated", "activation done", "done the steps", "completed the steps",
        "finished onboarding", "done onboarding", "all done", "i've done it", "ive done it",
        "finished the steps",
    ]),
    ("approve", [
        "approve", "approved", "am i approved", "has it been approved", "approval",
        "waiting for approval", "how long for approval", "when will i be approved",
    ]),
    ("lost_links", [
        "lost signal links", "lost the signal links", "i've lost signal links",
        "lost my links", "lost the links", "lost links", "lost the link", "lost my link",
        "left the group", "left group", "removed from the group", "kicked from the group",
        "group link", "signals link", "can't find the group", "cant find the group",
        "rejoin", "join the group again", "send me the links", "resend links",
        "my signals", "my signal group", "my signal groups", "my groups", "my telegram groups",
        "signal links", "signals links", "my links", "where are my links",
        "my signals group", "get my links", "send my links",
    ]),
    ("lost_code", [
        "can't get into my account", "cant get into my account", "can't get in my account",
        "my account code", "lost my code", "lost code", "lost my password", "lost password",
        "forgot my code", "forgot code", "forgot my password", "forgotten my code",
        "forgotten my password", "can't log in", "cant log in", "cannot log in",
        "can't login", "cant login", "access code", "login details", "log in details",
        "what's my code", "whats my code", "locked out",
        "my login", "my log in", "lost login", "lost log in", "lost my login",
        "lost my log in", "my login details", "login", "log in", "sign in",
        "cant sign in", "can't sign in", "my access code", "resend my code",
    ]),
    ("extra_signals", [
        "get more signals", "more signals please", "unlock extra signals",
        "how do i access extra signals", "access extra signals", "i only want extra signals",
        "only want extra signals", "extra signals", "more signals", "other signals",
        "currency signals", "additional signals", "pu prime", "puprime", "second account",
    ]),
    ("my_account", [
        "my account", "account page", "where's my account", "wheres my account",
        "my membership", "what do i have access to", "what have i got access to",
        "my profile", "account page", "my stuff", "my dashboard",
    ]),
    ("community", [
        "community", "female wealth", "wealth circle", "womens group", "women's group",
        "women only", "ladies group", "the female one",
    ]),
    ("new_here", [
        "getting started", "get started", "how does this work", "how it works",
        "i don't know anything", "i dont know anything", "i'm new to this", "im new to this",
        "new to this", "new to trading", "complete beginner", "total beginner",
        "never traded", "i know nothing", "where do i even start", "i'm a beginner",
        "im a beginner",
    ]),
    ("learn_to_trade", [
        "learn to trade", "learn trading", "teach me to trade", "want to learn",
        "how do i learn", "trading course", "beginners course", "free course",
        "fundamentals", "education", "the course", "courses",
    ]),
    ("lot_size", [
        "lot size", "lot sizes", "what lot size", "how do i know the lot size",
        "position size", "how much should i risk", "risk per trade", "how many lots",
    ]),
    ("account_number", [
        "where do i find my account number", "find my account number", "my account number",
        "broker account number", "where is my account number", "what's my account number",
        "whats my account number", "mt5 number", "login number",
    ]),
    ("trailing_stop", [
        "trail a stop", "trailing stop", "trail my stop", "how do i trail",
        "move my stop", "move stop loss", "trail the stop",
    ]),
    ("partials", [
        "partials", "what's partials", "whats partials", "partial close", "take partials",
        "closing partials", "part close", "taking profit early",
    ]),
    ("signals_general", [
        "signals", "the signals", "how do signals work", "when are signals",
        "what time are signals", "signal times",
    ]),
    ("share_results", [
        "share results", "my results", "share my results", "sharing results",
        "here are my results", "my profit", "my wins",
    ]),
]


def detect_intent(text):
    """
    Work out what someone wants. Returns (best_intent, close_runner_up_or_None).

    Longer phrase matches beat shorter ones, so "i've paid the advanced" is read
    as a payment rather than as a question about the course. When two different
    intents match equally well the caller asks which was meant, because guessing
    wrong sends someone down the wrong path entirely.
    """
    t = (text or "").lower().strip()
    if not t:
        return None, None

    scored = []
    for name, phrases in BOT_INTENTS:
        best = 0
        for p in phrases:
            if len(p) <= 4 and " " not in p:
                if re.search(rf"\b{re.escape(p)}\b", t):
                    best = max(best, len(p))
            elif p in t:
                best = max(best, len(p))
        if best:
            scored.append((best, name))

    if not scored:
        return None, None

    scored.sort(reverse=True)
    top_score, top_name = scored[0]
    for score, name in scored[1:]:
        # A clearly weaker match isn't a real rival, but a near-equal one is.
        if score >= top_score - 2 and name != top_name:
            return top_name, name
    return top_name, None


INTENT_LABELS = {
    "paid_advanced": "you've paid for the Advanced course",
    "advanced_course": "the Advanced Chart Reading course",
    "onboarding": "getting onboarded and on the signals",
    "activate": "sending your activation screenshots",
    "approve": "where your approval is up to",
    "lost_links": "getting your group links back",
    "lost_code": "getting back into your account",
    "extra_signals": "the extra signals",
    "my_account": "your account",
    "community": "Female Wealth, our women only community",
    "new_here": "getting started from scratch",
    "learn_to_trade": "learning to trade",
    "lot_size": "lot sizes",
    "account_number": "finding your account number",
    "trailing_stop": "trailing a stop loss",
    "partials": "taking partials",
    "signals_general": "how the signals work",
    "share_results": "sharing your results",
}


def bot_answer(intent, member, is_member, granted):
    """
    The reply for each intent, written the way a person would say it and always
    pointing at the specific place on the site that deals with it.

    Returns (text, new_state) where new_state moves the conversation on, or None.
    """
    has = lambda k: k in granted

    if intent == "onboarding":
        if is_member:
            return (f"You're already set up with us, so there's nothing more to do there.\n\n"
                    f"If you've lost your login, your access code is {member['access_code']} "
                    f"and you can log in at {SITE}/unlock\n\n"
                    f"All your group links live at {SITE}/my-signals"), None
        return (
            "Happy to walk you through it. It's three steps and they're all on the website.\n\n"
            f"1. Go to {SITE}/onboarding and follow the steps there. You'll open your broker "
            "account, make your deposit, and place your 10 activation trades.\n\n"
            "2. When all three are done, come back here and message me the word:\n\n"
            "   activate\n\n"
            "3. I'll tell you exactly which two screenshots to send. Once they're checked you'll "
            "get your access code and your gold signals group, usually well within 24 hours.\n\n"
            "Start with step 1 and message me when you're done."
        ), "awaiting_activation"

    if intent == "activate":
        if is_member:
            return ("You're already activated and on the signals, so you're all set. If something "
                    "isn't working, tell me what's happening and I'll sort it."), None
        return (
            "Perfect, last bit and you're in. I need two screenshots, sent here one after the other.\n\n"
            "Screenshot 1: your deposit confirmation, showing the money landed in your broker account.\n\n"
            "Screenshot 2: your closed trades history, showing your 10 activation trades are done.\n\n"
            "Send the first one whenever you're ready and I'll prompt you for the second."
        ), "awaiting_onboarding_photos"

    if intent == "approve":
        if is_member:
            return (f"You're approved and active, all good.\n\n"
                    f"Your access code is {member['access_code']} if you need it, and your groups "
                    f"are all at {SITE}/my-signals"), None
        if member:
            return ("You're in the queue. Our admin team checks screenshots by hand, usually well "
                    "within 24 hours, and the moment you're approved I'll message you here with "
                    "your access code and your group link.\n\n"
                    "If you haven't sent your two screenshots yet, message me \"activate\" and "
                    "I'll talk you through it."), None
        return ("I can't match this chat to an account yet, so I can't check where you're up to.\n\n"
                "If you've onboarded, send me the phone number you signed up with and I'll look "
                "you up. If you haven't started yet, message me \"onboarding\"."), "awaiting_phone"

    if intent == "paid_advanced":
        return (
            "Great, thanks for letting me know.\n\n"
            "Send a screenshot of your payment over here and I'll get it straight to our admin "
            "team. Once they've confirmed it your Advanced Chart Reading access is unlocked and "
            "I'll message you here the moment it's live."
        ), "awaiting_payment_photo"

    if intent == "advanced_course":
        if has("advanced"):
            return (f"You've already got Advanced Chart Reading unlocked. Pick up where you left "
                    f"off here:\n{SITE}/education/advanced"), None
        return (
            "Advanced Chart Reading is our paid course, £99 one time and yours for good. 23 lessons "
            "on reading a chart yourself: candlestick patterns, market structure, support and "
            "resistance, liquidity, and building your own strategy.\n\n"
            f"1. Pay here: {SITE}/education/advanced\n\n"
            "2. Once you've paid, message me the word:\n\n"
            "   paid\n\n"
            "3. I'll ask for a screenshot, then our admin team unlocks it and I'll message you here."
        ), "offered_advanced"

    if intent == "extra_signals":
        if has("signals_currency"):
            return (f"You've already got the extra signals. All your groups are here:\n"
                    f"{SITE}/my-signals\n\nIf you've lost a link just say \"lost links\"."), None
        return (
            "Extra signals are the additional groups on top of your gold ones: currency pairs, "
            "different sessions through the day, and a few different styles.\n\n"
            "It works by opening a second broker account with PU Prime, then using it for those "
            "signals. Everything you need is here:\n\n"
            f"{SITE}/signals\n\n"
            "Fill the form in at the bottom when your account is open and funded, and we'll approve "
            "you and send the group links straight here."
        ), None

    if intent == "lost_code":
        if is_member:
            return (f"No problem, here it is.\n\n"
                    f"Your access code: {member['access_code']}\n"
                    f"Log in at {SITE}/unlock\n\n"
                    f"It keeps you signed in on that device, so you shouldn't need it often. Worth "
                    f"saving somewhere though."), None
        if member:
            return ("I can see your account, but it hasn't been approved yet so there's no code on "
                    "it so far. As soon as your screenshots are checked I'll send it straight here."), None
        return ("I can't match this Telegram chat to an account, which usually means you signed up "
                "before we'd spoken here.\n\n"
                "Send me the phone number you signed up with and I'll find you and send your code "
                "straight back."), "awaiting_phone"

    if intent == "lost_links":
        return None, "send_links"  # handled by the caller, which knows their groups

    if intent == "my_account":
        if is_member:
            bits = [SECTION_LABELS[k] for k in SECTION_KEYS if k in granted]
            have = ", ".join(bits) if bits else "nothing unlocked yet"
            return (f"Here's where you stand.\n\n"
                    f"You've got: {have}\n\n"
                    f"Your account: {SITE}/account\n"
                    f"All your group links: {SITE}/my-signals\n"
                    f"Your access code: {member['access_code']}"), None
        return (f"You can see everything you have access to at {SITE}/account once you're logged in.\n\n"
                f"If you can't get in, send me the phone number you signed up with and I'll send "
                f"your code back."), "awaiting_phone"

    if intent == "community":
        if has("her"):
            return (f"You're already in Female Wealth. Your masterclasses and mindset lessons are "
                    f"here:\n{SITE}/her\n\nAnd your group link is at {SITE}/my-signals"), None
        return (
            "Female Wealth is our women only side of Inner Circle. It's a private space to learn, "
            "ask questions and chat, with masterclasses and mindset lessons alongside the group.\n\n"
            f"You can request access here:\n{SITE}/community\n\n"
            "Use the same phone number you signed up with and it unlocks on your existing account, "
            "with the code you already have."
        ), None

    if intent == "new_here":
        return (
            "You're in the right place, and there's nothing to know beforehand. Plenty of people "
            "here started from zero.\n\n"
            "Two things worth knowing:\n\n"
            f"Our free Trading Fundamentals course explains everything from the ground up, 41 "
            f"lessons in plain English:\n{SITE}/education/fundamentals\n\n"
            f"And onboarding gets you onto the signals themselves. Message me \"onboarding\" and "
            f"I'll walk you through it step by step.\n\n"
            "Ask me anything as you go, no question is too basic."
        ), None

    if intent == "learn_to_trade":
        if has("fundamentals"):
            return (f"You've already got our free Trading Fundamentals course, 41 lessons covering "
                    f"everything from the ground up:\n{SITE}/education/fundamentals\n\n"
                    f"When you want to go deeper into reading charts yourself, Advanced Chart "
                    f"Reading picks up from there: {SITE}/education/advanced"), None
        return (
            "Our free Trading Fundamentals course is the place to start. 41 lessons, plain English, "
            "everything from what a pip is through to placing your first trade properly.\n\n"
            f"{SITE}/education/fundamentals\n\n"
            "It's free and you get it as soon as you're onboarded. Message me \"onboarding\" and "
            "I'll get you set up."
        ), None

    if intent == "lot_size":
        where = (f"{SITE}/education/fundamentals" if has("fundamentals")
                 else f"{SITE}/education/fundamentals")
        extra = ("" if has("fundamentals") else
                 "\n\nYou get the course free once you're onboarded. Message me \"onboarding\" "
                 "to get set up.")
        return (
            "Lot size comes down to your account size and how much you're willing to risk on one "
            "trade, so there isn't a single right number I can give you.\n\n"
            "It's covered properly in the risk management section of our free Trading Fundamentals "
            f"course, which walks through working it out for your own account:\n\n{where}{extra}\n\n"
            "Worth doing before you take a signal, so you're sizing to your account rather than "
            "copying someone else's."
        ), None

    if intent == "account_number":
        return (
            "Your broker account number is in your MT5 app. Open it, go to the menu and it's shown "
            "at the top next to your name, and it's also in the email your broker sent when you "
            "opened the account.\n\n"
            "It's the number we ask for during onboarding so we can check your deposit and your "
            "activation trades.\n\n"
            f"Setting MT5 up is walked through step by step in our free course:\n"
            f"{SITE}/education/fundamentals"
        ), None

    if intent == "trailing_stop":
        if has("advanced"):
            deeper = (f"\n\nManaging a trade once it's running is covered in your Advanced Chart "
                      f"Reading course:\n{SITE}/education/advanced")
        else:
            deeper = (f"\n\nTrade management like this is covered in depth in Advanced Chart "
                      f"Reading, £99 one time:\n{SITE}/education/advanced")
        return (
            "Trailing a stop means moving your stop loss along as the trade goes your way, so "
            "you lock in more of the move if it turns around.\n\n"
            "In MT5 you do it by holding the trade line on the chart and dragging your stop to the "
            "new level, or long press the position and edit the stop loss.\n\n"
            f"The basics of stops are in the free Fundamentals course:\n"
            f"{SITE}/education/fundamentals{deeper}"
        ), None

    if intent == "partials":
        if has("advanced"):
            deeper = (f"\n\nWhen and where to take them is covered in your Advanced Chart Reading "
                      f"course:\n{SITE}/education/advanced")
        else:
            deeper = (f"\n\nWhen and where to take them is covered in Advanced Chart Reading, "
                      f"£99 one time:\n{SITE}/education/advanced")
        return (
            "Taking partials means closing part of a trade and letting the rest run. So you might "
            "close half at the first target and leave the other half going for the second.\n\n"
            "It banks some profit while still leaving something in if the move keeps going.\n\n"
            f"The basics are in the free Fundamentals course:\n"
            f"{SITE}/education/fundamentals{deeper}"
        ), None

    if intent == "signals_general":
        if has("signals_gold") or has("signals_currency"):
            return (f"Your signals come through on Telegram in the groups you've got access to. "
                    f"All your links are here so you can always find them:\n\n{SITE}/my-signals\n\n"
                    f"If a link has stopped working just say \"lost links\" and I'll send them again."), None
        return (
            "Our main signals are on gold, XAUUSD, and they come through on Telegram. There are "
            "extra signals available too covering currency pairs and different sessions.\n\n"
            "To get on them you need to onboard first. Message me \"onboarding\" and I'll walk you "
            f"through it, or start here:\n{SITE}/onboarding"
        ), None

    if intent == "share_results":
        return ("Love it, send your screenshots over and I'll pass them straight to the team. "
                "Always good to see how everyone's getting on."), "awaiting_results_photos"

    return None, None


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return "ok"

    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username")
    first_name = (message.get("from", {}).get("first_name") or "").strip()
    raw_text = (message.get("text") or "").strip()
    text = raw_text.lower()
    caption = (message.get("caption") or "").lower().strip()
    photos = message.get("photo")
    photo_file_id = (photos[-1].get("file_id") if photos else None)
    msg_id = message.get("message_id")

    if chat_id:
        upsert_bot_contact(username, chat_id)

    if not TELEGRAM_API:
        return "ok"

    def reply(msg):
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage",
                          json={"chat_id": chat_id, "text": msg}, timeout=10)
        except Exception:
            pass

    # Telegram can send a contact card. That, or a typed number, is what lets
    # the bot recognise the same person again without a @username.
    contact = message.get("contact") or {}
    shared_phone = contact.get("phone_number")

    state_row = get_bot_state(chat_id)
    state = state_row.get("state")
    already_greeted = bool(state_row.get("greeted"))
    member = member_for_chat(chat_id, username)
    is_member = bool(member and member.get("status") == "approved"
                     and (member.get("access_code") or "").strip())
    # Worked out up front because the menu is built from it.
    granted = get_member_sections(member["id"]) if member else set()

    def link_by_phone(raw):
        """Tie this chat to the account on that number, so we know them next time."""
        found = find_member_by_phone(raw)
        if not found:
            return None
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET chat_id=%s,
                                   telegram_username=COALESCE(NULLIF(telegram_username,''), %s),
                                   updated_at=NOW() WHERE id=%s""",
                                (chat_id, username, found["id"]))
            finally:
                conn.close()
        audit(found["id"], "Telegram linked", "matched by phone number in chat")
        return get_member_by_id(found["id"])

    def contact_block():
        who = f"@{username}" if username else (first_name or "Unknown")
        lines = [f"From: {who}"]
        if member:
            lines.append(f"Account: #{member['id']} {member.get('name') or ''} "
                         f"{pretty_phone(member.get('phone'))}")
        else:
            lines.append("No account matched to this chat yet.")
        if username:
            lines.append(f"Reply: https://t.me/{username}")
        else:
            lines.append(f"No @username set. Chat ID: {chat_id}. Reply to their forwarded photo, "
                         f"or message them on the site at /admin/inbox.")
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Set pieces
    # -----------------------------------------------------------------
    def build_menu(intro):
        """
        The menu is built from what this person actually has. A brand new
        visitor has no use for "lost links", and a member doesn't need telling
        how to onboard, so neither of them sees the other's options.
        """
        lines = [intro, "", "Just message me any of these:", ""]

        if not is_member:
            lines += [
                "🚀  \"getting started\"  -  how it all works, from scratch",
                "📝  \"onboarding\"  -  the steps to get set up",
                "✅  \"activate\"  -  once you've done those steps",
                "📚  \"how to trade\"  -  our free beginners course",
                "📈  \"advanced\"  -  the £99 chart reading course",
                "👭  \"female wealth\"  -  our women only community",
                "",
                f"Start here: {SITE}/onboarding",
            ]
        else:
            g = granted
            lines.append("🔑  \"my login\"  -  your access code, sent straight back")
            if any(k in g for k in ("signals_gold", "signals_currency")):
                lines.append("🔗  \"my signals\"  -  every group link you have")
            if "fundamentals" in g:
                lines.append("📚  \"my course\"  -  your Trading Fundamentals lessons")
            if "advanced" in g:
                lines.append("📈  \"advanced\"  -  your Advanced Chart Reading lessons")
            else:
                lines.append("📈  \"unlock advanced\"  -  the £99 chart reading course")
            if "signals_currency" not in g:
                lines.append("➕  \"get more signals\"  -  add the extra signals groups")
            if "her" in g:
                lines.append("👭  \"female wealth\"  -  your women only community")
            else:
                lines.append("👭  \"female wealth\"  -  our women only community")
            lines.append("🎉  \"share results\"  -  send us how you're getting on")
            lines += [
                "",
                f"Your account: {SITE}/account",
                f"Your group links: {SITE}/my-signals",
            ]

        lines += [
            "",
            "Or just ask me a question in your own words. If I can't answer it, one of the "
            "team will, and they'll come back to you on the website.",
        ]
        return "\n".join(lines)

    WELCOME_MENU = build_menu("Hey, welcome to Inner Circle! 👋")

    ONBOARDING_STEPS = (
        "Great, let's get you set up. It's three steps and it's all done on the website.\n\n"
        "1️⃣ Go to https://innercircletrading.co/onboarding and follow the steps there.\n"
        "   You'll open your broker account, make your deposit, and place your 10 activation trades.\n\n"
        "2️⃣ Once you've done all three, come back here and message me the word:\n\n"
        "   activate\n\n"
        "3️⃣ I'll then tell you exactly which screenshots to send, and once they're checked "
        "you'll get your access code and your signals group.\n\n"
        "Take your time with step 1, and just message \"activate\" here when it's done."
    )

    ACTIVATE_STEPS = (
        "Perfect, last bit and you're in. I need two screenshots, sent here one after the other.\n\n"
        "📸 Screenshot 1: your deposit confirmation\n"
        "   Showing the money landed in your broker account.\n\n"
        "📸 Screenshot 2: your closed trades history\n"
        "   Showing your 10 activation trades have been completed.\n\n"
        "Send the first one whenever you're ready. I'll tell you when I've got it and prompt you "
        "for the second.\n\n"
        "Once our admin team has checked them both, usually well within 24 hours, I'll send your "
        "access code straight here."
    )

    ADVANCED_STEPS = (
        "Advanced Chart Reading is our paid course, £99 one time and it's yours for good. "
        "23 lessons on reading charts yourself: candlestick patterns, market structure, support "
        "and resistance, liquidity, and building your own strategy.\n\n"
        "Here's how to get it:\n\n"
        "1️⃣ Pay here: https://innercircletrading.co/education/advanced\n\n"
        "2️⃣ Once you've paid, come back and message me:\n\n"
        "   paid\n\n"
        "3️⃣ I'll ask for a screenshot of your payment, then our admin team unlocks your access "
        "and I'll message you here the moment it's live."
    )

    def links_for_member():
        """Only the groups they actually have, never a link they can't use."""
        if not member:
            return None
        rows = [(n, u) for sec, n, _b, u in SIGNAL_GROUPS if sec in granted]
        if not rows:
            return None
        listed = "\n\n".join(f"{n}\n{u}" for n, u in rows)
        return (f"Here are all the groups you have access to:\n\n{listed}\n\n"
                f"They're always saved on your account too:\n"
                f"https://innercircletrading.co/my-signals")

    # -----------------------------------------------------------------
    # 1. Commands and greetings. The welcome only goes out here, never as
    #    a catch-all reply to something the bot didn't understand.
    # -----------------------------------------------------------------
    HELP_TRIGGERS = (
        "/start", "/menu", "/help", "/options", "menu", "options", "help", "help me",
        "what can you do", "what can you help with", "what can you help me with",
        "what do you do", "what can i ask", "what can i ask you", "how can you help",
        "how can you help me", "what are my options", "list", "commands", "start",
        "what else", "anything else", "i'm lost", "im lost", "lost", "confused",
        "not sure", "dont know", "don't know", "?", "??",
    )
    if text in HELP_TRIGGERS or text.rstrip("?!. ") in HELP_TRIGGERS:
        reply(WELCOME_MENU)
        set_bot_state(chat_id, greeted=True)
        return "ok"

    GREETINGS = ("hi", "hey", "hello", "yo", "hiya", "heya", "good morning", "good afternoon",
                 "good evening", "hi there", "hey there", "hello there", "morning", "evening",
                 "hi!", "hey!", "hello!", "hiya!")
    if text in GREETINGS and not photos:
        if already_greeted:
            name_bit = f" {first_name}" if first_name else ""
            reply(f"Hey{name_bit}, what can I help you with?")
        else:
            reply(WELCOME_MENU)
            set_bot_state(chat_id, greeted=True)
        return "ok"

    # -----------------------------------------------------------------
    # 1b. A phone number, shared or typed, links them to their account
    # -----------------------------------------------------------------
    typed_phone = None
    if raw_text and not photos:
        digits = "".join(ch for ch in raw_text if ch.isdigit())
        if len(digits) >= 9 and len(digits) <= 15 and len(digits) >= len(raw_text) - 5:
            typed_phone = raw_text

    if shared_phone or (typed_phone and (state == "awaiting_phone" or not member)):
        found = link_by_phone(shared_phone or typed_phone)
        if found:
            member = found
            is_member = bool(found.get("status") == "approved"
                             and (found.get("access_code") or "").strip())
            clear_bot_state(chat_id)
            granted_now = get_member_sections(found["id"])
            if is_member:
                lines = [f"Found you, thanks. You're {found.get('name') or 'all set'} on our system.",
                         "",
                         f"Your access code: {found['access_code']}",
                         f"Log in at {SITE}/unlock"]
                rows = [(n_, u) for sec, n_, _b, u in SIGNAL_GROUPS if sec in granted_now]
                if rows:
                    lines += ["", "And here are your group links:", ""]
                    lines += [f"{n_}\n{u}" for n_, u in rows]
                lines += ["", f"They're always saved at {SITE}/my-signals",
                          "", "Anything else I can help with?"]
                reply("\n".join(lines))
            else:
                reply(f"Found you, thanks. Your account is on our system but it hasn't been "
                      f"approved yet.\n\nAs soon as your screenshots are checked I'll message you "
                      f"here with your code and your group link. If you haven't sent them yet, "
                      f"message me \"activate\" and I'll talk you through it.")
            return "ok"
        if state == "awaiting_phone":
            clear_bot_state(chat_id)
            reply("I can't find an account on that number, sorry.\n\n"
                  "It might be worth double checking it, or if you signed up with a different "
                  f"number just tell me and I'll try again.\n\n"
                  f"If you'd rather speak to one of the team directly, message us on the website "
                  f"at {SITE}/messages and someone will pick it up.")
            return "ok"

    # -----------------------------------------------------------------
    # 1c. Closing off politely when they're done
    # -----------------------------------------------------------------
    NO_WORDS = ("no", "no thanks", "nope", "no thank you", "that's all", "thats all",
                "all good", "i'm good", "im good", "nothing else", "that's it", "thats it",
                "no im good", "no i'm good", "all sorted", "sorted")
    THANKS_WORDS = ("thanks", "thank you", "cheers", "ta", "thanx", "thankyou", "much appreciated",
                    "great thanks", "perfect thanks", "brilliant")
    stripped = text.rstrip("!. ")
    if stripped in NO_WORDS or stripped in THANKS_WORDS:
        tips_done = bool(state_row.get("tips_sent"))
        closing = "No problem at all. "
        if stripped in THANKS_WORDS:
            closing = "You're welcome. "
        if not tips_done:
            # The reminder of what I can do goes out once, not every time.
            closing += ("Any time you need me, I can sort your access code, your group links, "
                        "onboarding, the courses, or the community. Just say the word.\n\n"
                        "Have a good one.")
            set_bot_state(chat_id, tips_sent=True)
        else:
            closing += "Have a good one."
        clear_bot_state(chat_id)
        reply(closing)
        return "ok"

    # -----------------------------------------------------------------
    # 1d. Answering a "did you mean" question
    # -----------------------------------------------------------------
    pending = state_row.get("pending_intent")
    if pending and stripped in ("1", "2", "first", "second", "the first", "the second",
                                "yes", "yeah", "yep"):
        options = pending.split("|")
        pick = options[0] if stripped in ("1", "first", "the first", "yes", "yeah", "yep") else (
            options[1] if len(options) > 1 else options[0])
        set_bot_state(chat_id, pending_intent="")
        answer, new_state = bot_answer(pick, member, is_member, get_member_sections(member["id"]) if member else set())
        if new_state == "send_links":
            answer, new_state = (links_for_member() or
                                 f"I can't see any groups on your account yet. Once you're approved "
                                 f"they'll all be at {SITE}/my-signals"), None
        if answer:
            if new_state:
                set_bot_state(chat_id, state=new_state)
            reply(answer + "\n\nAnything else I can help with?")
            return "ok"

    # -----------------------------------------------------------------
    # 2. Where we're mid-conversation, that comes first
    # -----------------------------------------------------------------

    # They sent a photo but we don't know what it's for, and they've just told us.
    def claim_parked_photos(new_kind):
        """Relabel anything they sent before we knew what it was, then forward it."""
        conn = get_db()
        moved = 0
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""SELECT message_id FROM photo_submissions
                                   WHERE chat_id=%s AND kind='unknown' AND status='pending'""",
                                (chat_id,))
                    ids = [r[0] for r in cur.fetchall() if r[0]]
                    cur.execute("""UPDATE photo_submissions SET kind=%s
                                   WHERE chat_id=%s AND kind='unknown' AND status='pending'""",
                                (new_kind, chat_id))
                    moved = len(ids)
            except Exception:
                ids = []
            finally:
                conn.close()
            for mid_ in ids:
                forward_photo_to_admin(chat_id, mid_)
        return moved

    if state == "awaiting_photo_purpose" and not photos:
        if any(w in text for w in ("onboard", "verif", "activation", "deposit", "trades", "1", "one")):
            got = claim_parked_photos("onboarding")
            set_bot_state(chat_id, state="awaiting_onboarding_photos", photo_count=got)
            if got >= 2:
                clear_bot_state(chat_id)
                notify_admin(f"📸 ONBOARDING, READY TO REVIEW\n\n{contact_block()}\n\n"
                             f"Tick to approve: {SITE}/admin/photos")
                reply("Perfect, that's both of them, thank you. They've gone to our admin team to "
                      "check and I'll send your access code here as soon as they're happy.")
            elif got == 1:
                notify_admin(f"📸 ONBOARDING 1 of 2, DEPOSIT CONFIRMATION\n\n{contact_block()}\n\n"
                             f"Waiting on their closed trades history.")
                reply("Lovely, I've logged that as your deposit confirmation.\n\n"
                      "Now send your closed trades history, showing your 10 activation trades, "
                      "and that's you done.")
            else:
                reply(ACTIVATE_STEPS)
            return "ok"
        if any(w in text for w in ("payment", "paid", "advanced", "course", "2", "two")):
            got = claim_parked_photos("payment")
            if got:
                clear_bot_state(chat_id)
                notify_admin(f"💷 ADVANCED COURSE PAYMENT\n\n{contact_block()}\n\n"
                             f"Tick to approve: {SITE}/admin/photos")
                reply("Got it, thank you. That's gone to our admin team as your Advanced course "
                      "payment and they'll unlock your access, usually well within 24 hours. "
                      "I'll message you here the moment it's live.")
            else:
                set_bot_state(chat_id, state="awaiting_payment_photo")
                reply("Got it, that's your Advanced course payment. Send the payment screenshot "
                      "over and I'll pass it straight to our admin team.")
            return "ok"
        if any(w in text for w in ("result", "share", "win", "profit", "3", "three")):
            got = claim_parked_photos("results")
            if got:
                clear_bot_state(chat_id)
                notify_admin(f"🎉 MEMBER RESULTS\n\n{contact_block()}")
                reply("Brilliant, thanks for sharing! Passed straight to the team.")
            else:
                set_bot_state(chat_id, state="awaiting_results_photos")
                reply("Lovely, send your results screenshots over and I'll pass them to the team.")
            return "ok"

    # -----------------------------------------------------------------
    # 3. Photos
    # -----------------------------------------------------------------
    payment_words = ["advanced paid", "paid advanced", "paid for advanced", "payment for advanced",
                     "advanced payment", "paid the advanced", "advanced course paid",
                     "paid for the advanced", "proof of payment", "payment proof",
                     "paid £99", "paid 99", "i've paid", "ive paid", "just paid"]
    results_words = ["share results", "my results", "share my results", "sharing results",
                     "here are my results", "results from", "my profit", "my wins"]

    if photos:
        is_payment_ctx = (state == "awaiting_payment_photo"
                          or any(w in caption for w in payment_words))
        is_results_ctx = (state == "awaiting_results_photos"
                          or any(w in caption for w in results_words))
        is_onboarding_ctx = (state == "awaiting_onboarding_photos"
                             or any(w in caption for w in ("deposit", "activation", "trades",
                                                           "onboarding", "verification")))

        if is_payment_ctx:
            clear_bot_state(chat_id)
            upsert_photo_submission(chat_id, username, first_name, "payment", 1, msg_id)
            add_photo_file_id(chat_id, "payment", photo_file_id)
            forward_photo_to_admin(chat_id, msg_id)
            notify_admin(f"💷 ADVANCED COURSE PAYMENT\n\n{contact_block()}\n\n"
                         f"Tick to approve: https://innercircletrading.co/admin/photos\n\nScreenshot below:")
            reply("Thanks, got your payment screenshot and it's gone straight to our admin team.\n\n"
                  "They'll check it and unlock your Advanced Chart Reading access, usually well "
                  "within 24 hours. I'll message you here the moment it's live.")
            return "ok"

        if is_results_ctx:
            clear_bot_state(chat_id)
            forward_photo_to_admin(chat_id, msg_id)
            notify_admin(f"🎉 MEMBER RESULTS\n\n{contact_block()}\n\nScreenshot below:")
            reply("Brilliant, thanks for sharing! Passed straight to the team. "
                  "Always good to see how everyone's getting on.")
            return "ok"

        if is_onboarding_ctx:
            count = int(state_row.get("photo_count") or 0) + 1
            set_bot_state(chat_id, state="awaiting_onboarding_photos", photo_count=count)
            forward_photo_to_admin(chat_id, msg_id)
            upsert_photo_submission(chat_id, username, first_name, "onboarding", count, msg_id)
            add_photo_file_id(chat_id, "onboarding", photo_file_id)
            if count == 1:
                notify_admin(f"📸 ONBOARDING 1 of 2, DEPOSIT CONFIRMATION\n\n{contact_block()}\n\n"
                             f"Image below. Waiting on their closed trades history.")
                reply("Got your first screenshot, thank you.\n\n"
                      "Now send the second one over: your closed trades history, showing your "
                      "10 activation trades.")
            else:
                clear_bot_state(chat_id)
                notify_admin(
                    f"📸 ONBOARDING 2 of 2, CLOSED TRADES, READY TO REVIEW\n\n{contact_block()}\n\n"
                    f"Image below. Check the deposit confirmation and the 10 closed trades, then "
                    f"tick to approve: https://innercircletrading.co/admin/photos")
                reply("Both screenshots received, thank you!\n\n"
                      "They've gone to our admin team to check. Once they've confirmed everything "
                      "looks right, usually well within 24 hours, I'll send your access code "
                      "straight here along with your signals group link.")
            return "ok"

        # A photo out of the blue. Ask what it's for before sending anything
        # on, otherwise the admin chat fills up with screenshots that turn out
        # to be nothing.
        set_bot_state(chat_id, state="awaiting_photo_purpose")
        # Parked as "unknown" in the database, so it survives a restart and can
        # be relabelled the moment they tell us what it is.
        upsert_photo_submission(chat_id, username, first_name, "unknown", 1, msg_id)
        add_photo_file_id(chat_id, "unknown", photo_file_id)
        reply("Thanks for that! Quick check so it goes to the right place, what's this screenshot for?\n\n"
              "1️⃣ Onboarding verification (your deposit and your 10 activation trades, 2 photos)\n"
              "2️⃣ Advanced course payment (1 photo)\n"
              "3️⃣ Results you're sharing with the community\n\n"
              "Just reply 1, 2 or 3 and I'll sort it.")
        return "ok"

    # -----------------------------------------------------------------
    # 4. Work out what they want, and say so if it's ambiguous
    # -----------------------------------------------------------------
    granted = get_member_sections(member["id"]) if member else set()
    intent, rival = detect_intent(raw_text)

    # Some intents are broad enough that matching one isn't the same as knowing
    # what someone needs. "something's wrong with my account" matches "my
    # account", but they could mean their code, their links, or their approval.
    # Ask, rather than answer confidently and send them the wrong way.
    BROAD = {"my_account": ("lost_code", "lost_links"),
             "signals_general": ("lost_links", "onboarding"),
             "community": ("community", "lost_links")}
    VAGUE = ("thing", "something", "stuff", "issue", "problem", "wrong", "not working",
             "doesnt work", "doesn't work", "isnt working", "isn't working", "wont work",
             "won't work", "help with", "sort out", "need the", "need my", "cant get",
             "can't get", "trouble", "broken", "stuck")
    if intent in BROAD and not rival and any(v in text for v in VAGUE):
        a, b = BROAD[intent]
        if a != b:
            intent, rival = a, b

    if intent and rival:
        # Two things match equally well. Ask rather than guess wrong.
        set_bot_state(chat_id, pending_intent=f"{intent}|{rival}")
        reply(f"Happy to help, just so I point you the right way, did you mean:\n\n"
              f"1. {INTENT_LABELS.get(intent, intent)}\n"
              f"2. {INTENT_LABELS.get(rival, rival)}\n\n"
              f"Reply 1 or 2 and I'll sort it.")
        return "ok"

    if intent:
        answer, new_state = bot_answer(intent, member, is_member, granted)

        if new_state == "send_links":
            links = links_for_member()
            new_state = None
            if links:
                answer = links
            elif member:
                answer = ("Your account isn't approved yet, so there aren't any group links on it "
                          "so far. The moment it is, I'll send them straight here.")
            else:
                answer = ("I can't match this chat to an account, so I can't tell which groups are "
                          "yours.\n\nSend me the phone number you signed up with and I'll find you "
                          "and send them over.")
                new_state = "awaiting_phone"

        if answer:
            if new_state:
                set_bot_state(chat_id, state=new_state)
            follow_up = "" if new_state else "\n\nAnything else I can help with?"
            reply(answer + follow_up)
            if intent == "lost_code" and is_member:
                audit(member["id"], "code resent by bot", "they asked for it on Telegram")
            return "ok"

    # -----------------------------------------------------------------
    # 5. Nothing matched. Older answers, then a real person.
    # -----------------------------------------------------------------
    if raw_text:
        answer, matched = keyword_reply(raw_text, chat_id=chat_id)
        if matched:
            reply(answer + "\n\nAnything else I can help with?")
            return "ok"

        TRADING_WORDS = ["pip", "leverage", "stop loss", "take profit", "spread", "candlestick",
                         "support", "resistance", "risk", "chart", "indicator", "moving average",
                         "rsi", "fibonacci", "trend", "entry", "how do i trade", "how to trade",
                         "margin", "drawdown", "scalping", "swing", "analysis",
                         "buy or sell", "long or short"]
        if any(w in text for w in TRADING_WORDS):
            if "fundamentals" in granted:
                # They've paid their dues and have the course, so give them a
                # straight answer here rather than only a link.
                overview = next((v for k, v in FUNDAMENTALS_OVERVIEWS.items() if k in text), None)
                if overview:
                    reply(f"{overview}\n\n"
                          f"It's covered in more depth in your Trading Fundamentals course:\n"
                          f"{SITE}/education/fundamentals\n\n"
                          f"If you want it explained properly by one of the team, message us on "
                          f"the website at {SITE}/messages and someone will talk it through with "
                          f"you.\n\nAnything else I can help with?")
                else:
                    reply(f"That one's covered properly in your Trading Fundamentals course, and "
                          f"in more depth than I can manage in a message:\n\n"
                          f"{SITE}/education/fundamentals\n\n"
                          f"If it's still not clear after the lesson, message us on the website at "
                          f"{SITE}/messages and one of the team will explain it properly."
                          f"\n\nAnything else I can help with?")
            else:
                reply(f"Good question, and our free Trading Fundamentals course covers it properly. "
                      f"You get it as soon as you're onboarded.\n\n"
                      f"Message me \"onboarding\" and I'll walk you through getting set up, or have "
                      f"a look here: {SITE}/education/fundamentals\n\n"
                      f"Anything else I can help with?")
            return "ok"

        # Genuinely stuck. Offer the list once, then hand to a real person.
        if state == "offered_help":
            clear_bot_state(chat_id)
            reply(f"I'm still not getting it, sorry, and I'd rather not send you round in circles.\n\n"
                  f"Message us on the website and one of the team will pick it up and answer you "
                  f"properly:\n\n{SITE}/messages\n\n"
                  f"They can see your account, so they'll be able to sort whatever it is.")
            notify_admin(f"❓ BOT COULDN'T HELP, SENT THEM TO THE SITE\n\n{contact_block()}\n\n"
                         f"Their message:\n{raw_text}\n\nReply here: {SITE}/admin/inbox")
            return "ok"

        set_bot_state(chat_id, state="offered_help")
        reply(build_menu("I'm not quite sure what you're after there, sorry.") +
              "\n\nIf none of those are it, tell me again in your own words and I'll get one of "
              "the team on it.")
        notify_admin(f"❓ UNANSWERED MESSAGE\n\n{contact_block()}\n\nTheir message:\n{raw_text}\n\n"
                     f"Reply here: {SITE}/admin/inbox")

    return "ok"


@app.route("/account")
def account():
    if not is_verified():
        return redirect(url_for("unlock"))

    name = session.get("member_name", "")
    granted = current_sections()
    me = get_member_by_id(session.get("member_id")) or {}
    access_code = me.get("access_code") or ""

    # key -> (unlocked blurb, open link, locked blurb, locked link)
    ROWS = {
        "signals_gold":     ("Approved and active", None,
                             "Not on the gold signals", None),
        "signals_currency": ("Approved and active", ("View Extra Signals", "/signals"),
                             "Add a second account for currency signals", ("View Extra Signals", "/signals")),
        "fundamentals":     ("Free course, 41 lessons", ("Open course", "/education/fundamentals/0"),
                             "Not unlocked yet", None),
        "advanced":         ("Unlocked, 23 lessons", ("Open course", "/education/advanced/0"),
                             "£99 one-time, not yet unlocked", ("Unlock it", "/education/advanced/0")),
        "her":              ("Unlocked, masterclasses, mindset lessons and your group link", ("Open Female Wealth", "/her"),
                             "Women-only space, request access", ("Request to join", "/community")),
    }

    rows = []
    for key, label, _ in SECTIONS:
        on_blurb, on_link, off_blurb, off_link = ROWS[key]
        unlocked = key in granted
        blurb, link = (on_blurb, on_link) if unlocked else (off_blurb, off_link)
        colour = "var(--rose)" if (unlocked and key == "her") else "var(--green)"
        head = (f'<strong style="color: {colour};">✓ {label}</strong>' if unlocked
                else f'<span style="color: var(--ink-dim);">🔒 {label}</span>')
        cta = (f'<br><a href="{link[1]}" class="inline-link" style="font-size: 13px;">{link[0]} →</a>'
               if link else "")
        rows.append(
            f'<li style="padding: 14px 0; border-bottom: 1px solid var(--line);">{head}'
            f'<br><span style="color: var(--ink-dim); font-size: 13px;">{blurb}</span>{cta}</li>'
        )
    rows_html = "".join(rows)

    content = f"""
<section style="padding: 70px 0;">
  <div class="wrap" style="max-width: 560px;">
    <span class="eyebrow">Your account</span>
    <h1 style="font-size: 30px; margin: 10px 0 8px;">Hi{', ' + esc(name) if name else ''}</h1>
    <p style="color: var(--ink-dim); margin-bottom: 36px;">Here's what you've got access to.</p>

    <div class="form-panel">
      <ul style="list-style: none; padding: 0; margin: 0;">
        {rows_html}
      </ul>
    </div>

    <a href="/my-signals" class="btn btn-primary" style="margin-top: 22px; width: 100%; text-align: center;">
      All your signals and group links
    </a>

    <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:30px;">
      <div class="form-panel" style="max-width:100%; text-align:center;">
        <h3 style="font-size:17px; margin:0 0 8px;">Share your results</h3>
        <p style="color:var(--ink-dim); font-size:14px;">Message "share results" to our bot and send your screenshots. Wins, lessons, progress, all welcome.</p>
        <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link" style="font-size:13px;">Share now →</a>
      </div>
      <div class="form-panel" style="max-width:100%; text-align:center;">
        <h3 style="font-size:17px; margin:0 0 8px;">Need support?</h3>
        <p style="color:var(--ink-dim); font-size:14px;">Message us privately any time. No question is too small, genuinely.</p>
        <a href="/messages" class="inline-link" style="font-size:13px;">Message us →</a>
      </div>
    </div>

    <div class="callout" style="margin-top: 26px;">
      <strong>Your access code is {esc(access_code) if access_code else 'on your account'}</strong>
      <br><span style="font-size:13.5px;">We'll keep you signed in on this device. Lost it anyway? Ask our bot to resend it, or
      <a href="/messages" class="inline-link">message us here</a>.</span>
    </div>

    <p style="color: var(--ink-dim); font-size: 13px; margin-top: 28px;">
      Need help? <a href="/messages" class="inline-link">Message us here</a>, or on
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">Telegram</a>,
      where you can say <strong>help</strong> to see everything our bot can sort out, or
      <strong>lost links</strong> to get your groups sent back to you.
      <br><a href="/logout" class="inline-link">Log out</a>
    </p>
  </div>
</section>
"""
    return render_template_string(base_layout("My Account", content, ""))


@app.route("/messages", methods=["GET", "POST"])
def member_messages():
    if not is_verified():
        return redirect(url_for("unlock"))

    member_id = session.get("member_id")

    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if body:
            add_message(member_id, "member", body)
            notify_admin(
                f"💬 NEW MESSAGE FROM MEMBER\n\n"
                f"{session.get('member_name','Member')} (ID {member_id})\n\n"
                f"{body}\n\n"
                f"Reply at https://innercircletrading.co/admin/messages/{member_id}"
            )
        return redirect(url_for("member_messages"))

    msgs = get_messages(member_id)
    if msgs:
        thread = "".join(
            f'<div style="margin-bottom: 18px; text-align: {"right" if m["sender"]=="member" else "left"};">'
            f'<div style="display: inline-block; max-width: 80%; text-align: left; padding: 12px 16px; border-radius: 14px; '
            f'background: {"var(--bg-alt-2)" if m["sender"]=="member" else "var(--gold)"}; '
            f'color: {"var(--ink)" if m["sender"]=="member" else "var(--bg)"};">'
            f'<div style="font-size: 11px; opacity: 0.7; margin-bottom: 4px;">{"You" if m["sender"]=="member" else "Inner Circle"}</div>'
            f'{m["body"]}</div></div>'
            for m in msgs
        )
    else:
        thread = '<p style="color: var(--ink-dim); text-align: center; padding: 30px 0;">No messages yet. Send us anything you need below.</p>'

    content = f"""
<section style="padding: 60px 0;">
  <div class="wrap" style="max-width: 620px;">
    <a href="/account" class="inline-link" style="font-size: 13px;">← Back to my account</a>
    <h1 style="font-size: 28px; margin: 16px 0 8px;">Messages</h1>
    <p style="color: var(--ink-dim); margin-bottom: 30px;">Message us directly here, we'll reply as soon as we can.</p>
    <div class="form-panel" style="max-width: 100%; margin-bottom: 22px;">{thread}</div>
    <div class="form-panel" style="max-width: 100%;">
      <form method="POST">
        <label>Your message</label>
        <input type="text" name="body" placeholder="Type your message..." required>
        <button type="submit">Send</button>
      </form>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Messages", content, ""))


@app.route("/my-signals")
def my_signals():
    """
    Every Telegram group this member has access to, in one place, so a lost
    link is never a problem. Groups they haven't unlocked show as locked with
    a route to unlocking them, rather than being hidden.
    """
    if not is_verified():
        return redirect(url_for("unlock"))

    granted = current_sections()

    unlocked, locked = [], []
    for section, name, blurb, url in SIGNAL_GROUPS:
        if section in granted:
            unlocked.append(
                f'<div class="benefit" style="text-align:left; border-color:var(--green);">'
                f'<strong style="font-size:16px;">{name}</strong>'
                f'<p style="color:var(--ink-dim); font-size:14px; margin:6px 0 12px;">{blurb}</p>'
                f'<a href="{url}" target="_blank" rel="noopener" class="btn btn-primary" '
                f'style="padding:9px 20px; font-size:13px;">Open in Telegram</a></div>'
            )
        else:
            if section == "signals_currency":
                cta, cta_url = "Unlock more signals", "/signals"
            elif section == "her":
                cta, cta_url = "Request access", "/community"
            else:
                cta, cta_url = "Start onboarding", "/onboarding"
            locked.append(
                f'<div class="benefit" style="text-align:left; opacity:0.72;">'
                f'<strong style="font-size:16px; color:var(--ink-dim);">🔒 {name}</strong>'
                f'<p style="color:var(--ink-dim); font-size:14px; margin:6px 0 12px;">{blurb}</p>'
                f'<a href="{cta_url}" class="inline-link" style="font-size:13px;">{cta} →</a></div>'
            )

    unlocked_html = "".join(unlocked) or (
        '<p style="color:var(--ink-dim);">Nothing unlocked yet. Once your onboarding is approved '
        'your groups appear here.</p>')
    locked_html = (
        f'<h2 style="font-size:20px; margin:44px 0 16px;">Not unlocked yet</h2>'
        f'<div class="grid5" style="grid-template-columns:1fr;">{"".join(locked)}</div>'
        if locked else "")

    content = f"""
<section style="padding: 60px 0;">
  <div class="wrap" style="max-width: 760px;">
    <a href="/account" class="inline-link" style="font-size: 13px;">← Back to my account</a>
    <span class="eyebrow" style="margin-top: 20px;">Your groups</span>
    <h1 style="font-size: 32px; margin: 10px 0 10px;">Your signals and links</h1>
    <p style="color: var(--ink-dim); margin-bottom: 34px;">
      Every group you have access to, in one place. Lost a link, or left a group by accident?
      Open it again from here any time.
    </p>

    <h2 style="font-size: 20px; margin: 0 0 16px;">Open to you now</h2>
    <div class="grid5" style="grid-template-columns:1fr;">{unlocked_html}</div>
    {locked_html}

    <p style="color: var(--ink-dim); font-size: 13px; margin-top: 34px;">
      Request to join each group and we'll approve you shortly. Turn notifications on so you
      don't miss anything.
      <br>Something not working? <a href="/messages" class="inline-link">Message us here</a>, or say
      <strong>lost links</strong> to
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">our bot</a>
      and it'll send them all again.
    </p>
  </div>
</section>
"""
    return render_template_string(base_layout("Your Signals", content, "signals"))


@app.route("/results")
def results():
    """
    Results gallery. Screenshots and member feedback get added to RESULTS_ITEMS
    and MEMBER_FEEDBACK below as they come in, so nothing here is invented.
    """
    if RESULTS_ITEMS:
        gallery = "".join(
            f'<div class="benefit" style="text-align:left; padding:0; overflow:hidden;">'
            f'<img src="{r["image"]}" alt="{esc(r.get("caption") or "Result")}" '
            f'style="width:100%; display:block;">'
            f'<div style="padding:16px 18px;"><p style="margin:0; font-size:14px; color:var(--ink-dim);">'
            f'{esc(r.get("caption") or "")}</p></div></div>'
            for r in RESULTS_ITEMS
        )
    else:
        gallery = (
            '<div class="callout" style="grid-column:1/-1;">'
            'The gallery is being put together. Results are posted openly in the signals groups '
            'in the meantime, wins and losses both.</div>'
        )

    if MEMBER_FEEDBACK:
        feedback = "".join(
            f'<div class="benefit"><div class="icon">"</div>'
            f'<p style="color:var(--ink-dim); font-size:15px; line-height:1.7; margin-bottom:14px;">'
            f'{esc(f_["quote"])}</p>'
            f'<p style="font-size:13px; color:var(--gold); margin:0;">{esc(f_.get("who") or "Member")}</p></div>'
            for f_ in MEMBER_FEEDBACK
        )
        feedback_block = f"""
<section>
  <div class="wrap">
    <div class="section-head"><span class="eyebrow">From members</span><h2>What people say</h2></div>
    <div class="grid5" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));">{feedback}</div>
  </div>
</section>"""
    else:
        feedback_block = ""

    content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr; max-width: 820px; text-align: center;">
    <div>
      <span class="eyebrow">Results</span>
      <h1>The wins and<br>the losses.</h1>
      <p class="lede">Posted openly. Trading carries risk and no result here is a promise of a future one.</p>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="grid5" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));">{gallery}</div>
  </div>
</section>
{feedback_block}

<section>
  <div class="wrap" style="max-width: 680px; text-align: center;">
    <h2 style="font-size: 26px; margin-bottom: 14px;">Got results to share?</h2>
    <p style="color: var(--ink-dim); margin-bottom: 24px;">Message "share results" to our bot and send your screenshots. Wins, lessons, progress, all welcome.</p>
    <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="btn btn-primary">Share your results</a>
  </div>
</section>
"""
    return render_template_string(base_layout("Results", content, ""))


@app.route("/logout")
def logout():
    session.pop("member_id", None)
    session.pop("member_tier", None)
    session.pop("member_paid", None)
    session.pop("member_community", None)
    session.pop("member_sections", None)
    session.pop("member_phone", None)
    session.pop("member_name", None)
    return redirect(url_for("home"))


def is_verified():
    return bool(session.get("member_id"))


def current_sections():
    """
    Read the member's access fresh once per request, so a tick box change in
    admin applies straight away instead of waiting for them to log out and in.
    Falls back to what was stored at login if the database is unreachable.
    """
    if not session.get("member_id"):
        return set()
    cached = getattr(g, "_member_sections", None)
    if cached is not None:
        return cached
    try:
        sections = get_member_sections(session["member_id"])
        if sections:
            session["member_sections"] = sorted(sections)
    except Exception:
        sections = set(session.get("member_sections") or [])
    g._member_sections = sections
    return sections


def has_access(section):
    return section in current_sections()


def is_paid():
    return has_access("advanced")


def is_community():
    return has_access("her")


def locked_page(reason_text, cta_text, cta_url):
    content = f"""
<section style="padding: 100px 0; text-align: center;">
  <div class="wrap" style="max-width: 480px;">
    <div class="ring-mark" style="margin: 0 auto 24px;"><span>🔒</span></div>
    <h1 style="font-size: 28px; margin-bottom: 16px;">This is locked</h1>
    <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 32px;">{reason_text}</p>
    <a href="{cta_url}" class="btn btn-primary">{cta_text}</a>
    <p style="color: var(--ink-dim); font-size: 14px; margin-top: 28px;">
      Already approved? <a href="/unlock" class="inline-link">Log in with your access code</a>
    </p>
  </div>
</section>
"""
    return render_template_string(base_layout("Locked", content, ""))


@app.route("/")
def home():
    instagram_button = (
        f'<a href="{INSTAGRAM_URL}" target="_blank" rel="noopener" class="btn btn-ghost">Follow on Instagram</a>'
        if INSTAGRAM_URL else "")
    content = f"""
<section class="hero" style="padding-bottom: 60px;">
  <div class="wrap" style="grid-template-columns: 1fr; max-width: 820px; text-align: center;">
    <div>
      <div class="ring-mark" style="margin: 0 auto 28px;"><span>IC</span></div>
      <span class="eyebrow">Welcome to Inner Circle</span>
      <h1 style="margin: 14px 0 24px;">Building wealth,<br>creating <em>freedom.</em></h1>
      <p class="lede" style="max-width: 640px; margin: 0 auto 20px;">
        Inner Circle exists because most trading spaces hand you a signal and leave you to figure out the rest.
        We do it differently. Clear signals, honest education, and real support, open to everyone.
      </p>
      <p class="lede" style="max-width: 640px; margin: 0 auto 36px; color: var(--rose);">
        Alongside it sits Wealth Circle, our private community built exclusively for women. Signals, education,
        and support are for anyone, Wealth Circle itself is female-only.
      </p>
      <div class="cta-row" style="justify-content: center;">
        <a href="/onboarding" class="btn btn-primary">Start Onboarding</a>
        <a href="/education" class="btn btn-ghost">See the Curriculum</a>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width: 760px; text-align: center;">
    <span class="eyebrow">What Inner Circle is</span>
    <h2 style="font-size: 32px; margin: 14px 0 24px;">More than signals</h2>
    <p style="color: var(--ink-dim); font-size: 17px; line-height: 1.8;">
      You start with a clear, guided onboarding, learn the fundamentals properly through our education library,
      and get direct support the whole way, before you're ever expected to trade on instinct alone.
    </p>
    <p style="color: var(--ink-dim); font-size: 17px; line-height: 1.8; margin-top: 20px;">
      Each stage takes you a level further: onboarding gets you set up and verified. Our free Trading
      Fundamentals course gets you trading with genuine understanding. Our paid Advanced Chart Reading
      course takes you further still, learning to read the market yourself, not just follow along.
    </p>
  </div>
</section>

<section class="editorial" style="border-bottom: none;">
  <div class="wrap">
    <div class="ring-mark"><span>IC</span></div>
    <blockquote>"What you're not changing, <em>you're choosing.</em>"</blockquote>
    <cite>, A thought worth sitting with if you're ready to start</cite>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">Why join</span>
      <h2>Inside you'll get</h2>
    </div>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 48px; align-items: center; margin-bottom: 40px;">
      <div class="ticket">
        <div class="ticket-head">
          <span class="ticket-pair">XAU/USD</span>
          <span class="tag-buy">BUY</span>
        </div>
        <div class="ticket-row"><span>Entry</span><span>2021.50 – 2024.50</span></div>
        <div class="ticket-row tp"><span>TP</span><span>2032.00</span></div>
        <div class="ticket-row sl"><span>SL</span><span>2016.00</span></div>
        <div class="ticket-row"><span>Volume</span><span>0.01 lots</span></div>
        <div class="ticket-foot">Illustrative example, not a live signal</div>
      </div>
      <div>
        <h3 style="font-size: 22px; margin-bottom: 12px;">This is what a signal looks like</h3>
        <p style="color: var(--ink-dim); font-size: 16px; line-height: 1.75;">
          Pair, direction, entry range, take profit, and stop loss, laid out so there's no guessing. Copy it
          straight into MT5. You'll also learn what each part means in our free course, so it stops being a
          mystery and starts being something you actually understand.
        </p>
      </div>
    </div>
    <div class="grid5">
      <div class="benefit"><div class="icon">✂</div><h3>Copy & Paste Signals</h3><p>Simple, actionable trades you can follow.</p></div>
      <div class="benefit"><div class="icon">◈</div><h3>Full Trading Guidance</h3><p>Step-by-step support to help you grow with confidence.</p></div>
      <div class="benefit"><div class="icon">○</div><h3>Exclusive Female Community</h3><p>Wealth Circle, a supportive space to learn, share and grow together, women only.</p></div>
      <div class="benefit"><div class="icon">★</div><h3>Trade Ideas From Experts</h3><p>Leverage the experience of professionals.</p></div>
      <div class="benefit"><div class="icon">♥</div><h3>Ongoing Support</h3><p>Learn, ask, and level up every single day.</p></div>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width: 820px;">
    <div class="section-head" style="max-width: 100%;">
      <span class="eyebrow">In detail</span>
      <h2>What you actually get</h2>
      <p>Not just a Telegram channel, a full path, explained properly.</p>
    </div>

    <div style="display: flex; flex-direction: column; gap: 44px;">
      <div>
        <h3 style="font-size: 20px; margin-bottom: 10px;">A real education, not an afterthought</h3>
        <p style="color: var(--ink-dim); font-size: 16px; line-height: 1.75;">
          Our free Trading Fundamentals course covers everything from what a candle is to how to manage your
          own emotions mid-trade, 41 lessons, built for genuine beginners. When you're ready to go further,
          Advanced Chart Reading teaches you to read a chart yourself, so you're not permanently dependent on
          someone else's call.
        </p>
      </div>
      <div>
        <h3 style="font-size: 20px; margin-bottom: 10px;">A community that's actually supportive</h3>
        <p style="color: var(--ink-dim); font-size: 16px; line-height: 1.75;">
          Wealth Circle is private and female-only, a space to ask a "silly" question, share a win, or talk
          through a loss without the usual noise that trading spaces are known for. You're never learning alone.
        </p>
      </div>
      <div>
        <h3 style="font-size: 20px; margin-bottom: 10px;">Direct support, always</h3>
        <p style="color: var(--ink-dim); font-size: 16px; line-height: 1.75;">
          Stuck on a step? Message us directly on Telegram or use the chat on this site. You'll always get an answer, never left to figure it out yourself.
        </p>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width: 820px;">
    <div class="section-head" style="max-width: 100%;">
      <span class="eyebrow">Why Inner Circle</span>
      <h2>Why this, and not just another signals group</h2>
    </div>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 32px;">
      <div>
        <h3 style="font-size: 17px; margin-bottom: 8px; color: var(--gold);">Most groups</h3>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7;">
          Hand you a signal, expect you to know what to do with it, and disappear when you have questions.
          No structure, no education, no real community behind it.
        </p>
      </div>
      <div>
        <h3 style="font-size: 17px; margin-bottom: 8px; color: var(--gold);">Inner Circle</h3>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7;">
          Walks you through onboarding step by step, teaches you the fundamentals before you're ever
          expected to trade alone, and makes sure you always get an answer.
        </p>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">How it works</span>
      <h2>Three steps to get started</h2>
    </div>
    <div class="process">
      <div class="step"><div class="num">01</div><h3>Register</h3><p>Open a broker account through our link and make your first deposit, we match it 50%.</p></div>
      <div class="step"><div class="num">02</div><h3>Verify</h3><p>Place a few practice trades in MT5 and confirm your setup with us via Telegram.</p></div>
      <div class="step"><div class="num">03</div><h3>Unlock</h3><p>Get access to daily signals, the full education library, and the private community.</p></div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">Proof, not promises</span>
      <h2>Results & feedback</h2>
      <p>See it for yourself rather than take our word for it.</p>
    </div>
    <div class="grid5" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));">
      <div class="benefit">
        <div class="icon">"</div>
        <h3>Member feedback</h3>
        <p>Real messages from the community, shared here as we collect them.</p>
      </div>
      <div class="benefit">
        <div class="icon">📈</div>
        <h3>Results gallery</h3>
        <p>Every signal outcome posted openly, wins and losses both. Updated as results come in.</p>
        <a href="/results" class="inline-link" style="display: inline-block; margin-top: 10px;">View the results gallery →</a>
      </div>
    </div>
  </div>
</section>

<section id="support">
  <div class="wrap" style="max-width: 820px;">
    <div class="section-head" style="max-width:100%; text-align:center;">
      <span class="eyebrow">Need support?</span>
      <h2>You'll always get an answer</h2>
      <p>Stuck on a step, or not sure what's next? Ask us. You'll always get an answer, never left to figure it out yourself.</p>
    </div>
    <div class="cta-row" style="justify-content:center;">
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="btn btn-primary">Message us on Telegram</a>
      <a href="/messages" class="btn btn-ghost">Message us on the site</a>
      {instagram_button}
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="community-panel">
      <div>
        <span class="eyebrow">Wealth Circle, Women Only</span>
        <h2>A circle built for women in trading</h2>
        <p>Trading spaces are loud and male-dominated by default. Ours isn't. Wealth Circle is a private, supportive space to learn, ask questions, and grow, without the noise. Everything else on Inner Circle, signals, education, and support, is open to everyone.</p>
        <a href="/community" class="btn btn-primary" style="margin-top: 24px;">Visit Community</a>
      </div>
      <ul class="community-list">
        <li><span>,</span>Share wins and questions freely</li>
        <li><span>,</span>Weekly community threads</li>
        <li><span>,</span>Direct access to the team</li>
        <li><span>,</span>No judgement, ever</li>
      </ul>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">Education</span>
      <h2>Learn properly, not by accident</h2>
      <p>Two courses, one free, one for members who want to go further.</p>
    </div>
    <div class="courses">
      <div class="course-card">
        <div class="price">FREE</div>
        <h3>Trading Fundamentals</h3>
        <p>Everything you need before your first trade.</p>
        <ul>
          <li>Key terms, TP/SL, risk basics</li>
          <li>Full MT5 walkthrough</li>
          <li>Trading psychology & mindset</li>
        </ul>
        <a href="/education/fundamentals" class="btn btn-ghost">View Curriculum</a>
      </div>
      <div class="course-card">
        <div class="price">£99 · ONE-TIME</div>
        <h3>Advanced Chart Reading</h3>
        <p>Read charts yourself instead of relying only on signals.</p>
        <ul>
          <li>Candlestick patterns, in depth</li>
          <li>Trend structure & support/resistance</li>
          <li>Building your own strategy</li>
        </ul>
        <a href="/education/advanced" class="btn btn-ghost">View Curriculum</a>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width: 700px;">
    <span class="eyebrow">Please read</span>
    <h2 style="font-size: 24px; margin: 12px 0 18px;">A few honest disclaimers</h2>
    <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.8;">
      Inner Circle is educational content and community support, it is not financial advice. Nothing shared
      here, including signals, is a guarantee of results. Trading carries real risk, and past performance is
      never a guarantee of future performance. No strategy or signal provider wins on every trade. You are
      responsible for your own trading decisions, always do your own research, and never risk money you
      can't afford to lose.
    </p>
  </div>
</section>
"""
    return render_template_string(base_layout("Home", content, "home"))


@app.route("/signals")
def signals():
    # Once extra signals are unlocked this page stops being a sales page and
    # becomes their signals hub: every group they have, on one page. Female
    # Wealth is deliberately not listed here, it lives on its own page.
    if has_access("signals_currency"):
        granted = current_sections()
        blocks = []
        for section, heading in (("signals_gold", "Gold signals"),
                                 ("signals_currency", "Extra signals")):
            if section not in granted:
                continue
            cards = "".join(
                f'<div class="benefit" style="text-align:left;">'
                f'<strong style="font-size:16px;">{name}</strong>'
                f'<p style="color:var(--ink-dim); font-size:14px; margin:6px 0 12px;">{blurb}</p>'
                f'<a href="{url}" target="_blank" rel="noopener" class="btn btn-primary" '
                f'style="padding:9px 20px; font-size:13px;">Open in Telegram</a></div>'
                for sec, name, blurb, url in SIGNAL_GROUPS if sec == section
            )
            blocks.append(
                f'<h2 style="font-size:22px; margin:38px 0 16px;">{heading}</h2>'
                f'<div class="grid5" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));">{cards}</div>'
            )

        content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr; max-width: 900px;">
    <div>
      <span class="eyebrow">Unlocked</span>
      <h1>All your<br>signals.</h1>
      <p class="lede">Every group you have access to, in one place. Lost a link or left a group by accident? Open it again from here any time.</p>
    </div>
  </div>
</section>

<section style="padding-top:0;">
  <div class="wrap" style="max-width: 900px;">
    {"".join(blocks)}
    <p style="color: var(--ink-dim); font-size: 13px; margin-top: 36px;">
      Request to join each group and we'll approve you shortly. Turn notifications on so you don't miss a signal.
      <br>A link not working? <a href="/messages" class="inline-link">Message us here</a>.
    </p>
  </div>
</section>
"""
        return render_template_string(base_layout("All Your Signals", content, "signals"))

    content = """
<section class="hero" style="padding-bottom: 40px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <div class="ring-divider">+</div>
      <span class="eyebrow">Extra Signals</span>
      <h1>Unlock more<br>signals <em>too.</em></h1>
      <p class="lede">Extra signals, more groups, more access. Available any time. Create a new PU Prime account, then use it just for these new signals, or use it for all your signals if you'd rather keep things to one account.</p>
      <div class="callout">To unlock and use these signals, you must use your registered account.</div>
    </div>
  </div>
</section>

<section style="padding-top: 40px;">
  <div class="wrap" style="max-width: 760px;">

    <div class="ob-step">
      <div class="ob-num">01</div>
      <div>
        <h3>Let us know who referred you</h3>
        <p>Tap the name. A link will open taking you to create your PU Prime account. Don't close this page, you'll need to come back to it.</p>
        <div style="display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px;">
          <a href="https://t.me/Innercircleverifybot" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Charlotte</a>
          <a href="https://t.me/Innercircleverifybot" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Beth</a>
          <a href="https://t.me/Innercircleverifybot" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Robbie</a>
          <a href="https://t.me/Innercircleverifybot" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Lucy</a>
          <a href="https://t.me/Innercircleverifybot" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Lydia</a>
        </div>
        <div class="callout">Tapping a name will connect you with us to get your personal registration link. Once you've registered, come straight back to this page, the next steps are right here below.</div>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">02</div>
      <div>
        <h3>Deposit & get your bonus</h3>
        <p>Fill in your details, verify your identity, and deposit into your new PU Prime account. You'll get a 100% deposit match in credit from the broker automatically.</p>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">03</div>
      <div>
        <h3>Link your account in MT5</h3>
        <p>Open MT5, tap the menu → Manage Accounts → '+' → Login to an existing account. Search for PU Prime as the broker/server, enter your new account number and password, and confirm it connects and shows your balance correctly.</p>
      </div>
    </div>

    <p style="color: var(--ink-dim); font-size: 14px; margin-top: 24px;">Already have an open and active PU Prime account? Please contact us instead of registering a new one.</p>

  </div>

  <div class="wrap" style="max-width: 760px; margin-top: 48px;">
    <div class="form-panel">
      <h3 style="font-size: 20px; margin-bottom: 6px;">Submit for review</h3>
      <p style="color: var(--ink-dim); font-size: 14px; margin: 0;">Once you've completed all 3 steps, fill this in and we'll verify and approve your extra signals access. Your group links get sent to you and saved on your account.</p>
      <form method="POST" action="/signals/submit">
        <label>Title</label>
        <select name="title" required style="width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink); padding: 13px 16px; border-radius: 10px; font-family: 'Inter'; font-size: 15px;">
          <option value="">Select</option>
          <option>Mr</option>
          <option>Mrs</option>
          <option>Miss</option>
          <option>Ms</option>
        </select>
        <label>Full name</label>
        <input type="text" name="name" required>
        <label>PU Prime account number</label>
        <input type="text" name="account_number" required>
        <label>Deposit amount</label>
        <input type="text" name="deposit_amount" required>
        <label>Phone number</label>
        <input type="tel" name="phone" required>
        <label>Email address</label>
        <input type="email" name="email" placeholder="you@example.com" required>
        <p style="font-size: 12px; color: var(--ink-dim); margin: 4px 0 0;">Message @Innercircleverifybot once (just say hi) so we can send you your group link once approved.</p>
        <label>Who did you tap above?</label>
        <select name="referred_by" required style="width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink); padding: 13px 16px; border-radius: 10px; font-family: 'Inter'; font-size: 15px;">
          <option value="">Select a name</option>
          <option>Charlotte</option>
          <option>Beth</option>
          <option>Robbie</option>
          <option>Lucy</option>
          <option>Lydia</option>
        </select>
        <button type="submit">Submit for Review</button>
      </form>
    </div>
  </div>

  <div class="wrap" style="max-width: 760px; margin-top: 32px;">
    <div class="callout">No extra activation trades needed here, we already know you know how to place and close a trade by now 😉</div>
    <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-top: 20px;">This unlocks a few different trading groups, not just one, some may focus on different trading styles or different sessions throughout the day. Have a look through the groups first and pick which ones you'd like to trade alongside your original gold signals.</p>
  </div>
</section>
"""
    return render_template_string(base_layout("Extra Signals", content, "signals"))


@app.route("/signals/submit", methods=["POST"])
def signals_submit():
    title = request.form.get("title", "")
    name = request.form.get("name", "")
    account_number = request.form.get("account_number", "")
    deposit_amount = request.form.get("deposit_amount", "")
    phone = request.form.get("phone", "")
    email = request.form.get("email", "")
    telegram_username = request.form.get("telegram_username", "")
    referred_by = request.form.get("referred_by", "")

    # Same rule as the community form: attach to the account they already have
    # rather than creating a second one. The PU Prime details are a different
    # broker account from their gold one, so they get their own fields and sit
    # alongside the original onboarding details instead of overwriting them.
    member = match_existing_member(phone=phone, account_number=account_number, email=email)

    if member:
        member_id = member["id"]
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE members SET currency_account_number=%s, currency_deposit_amount=%s, "
                        "currency_submitted_at=NOW(), updated_at=NOW(), "
                        "title=COALESCE(NULLIF(title,''), %s), "
                        "name=COALESCE(NULLIF(name,''), %s), "
                        "email=COALESCE(email, %s), "
                        "telegram_username=COALESCE(NULLIF(telegram_username,''), %s), "
                        "referred_by=COALESCE(NULLIF(referred_by,''), %s) WHERE id=%s",
                        (account_number, deposit_amount, title, name, clean_email(email),
                         telegram_username, referred_by, member_id))
            finally:
                conn.close()
        how = "they were logged in" if session.get("member_id") else "their phone number"
        audit(member_id, "extra signals requested",
              f"matched to this account by {how}, PU Prime account {account_number}")
        linked_note = (f"MATCHED to their existing account (#{member_id}) by {how}. "
                       f"Their PU Prime details are on that profile, no second account made.")
    else:
        member_id = create_pending_member(
            tier="currency", title=title, name=name, account_number=account_number,
            deposit_amount=deposit_amount, phone=phone, email=email,
            telegram_username=telegram_username, referred_by=referred_by
        )
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET currency_account_number=%s,
                                   currency_deposit_amount=%s, currency_submitted_at=NOW()
                                   WHERE id=%s""", (account_number, deposit_amount, member_id))
            finally:
                conn.close()
        linked_note = "No existing account on that number, so this is a new record."

    notify_admin(
        "New EXTRA SIGNALS (PU Prime) submission:\n\n"
        f"Title: {title}\n"
        f"Name: {name}\n"
        f"PU Prime account #: {account_number}\n"
        f"Deposit: {deposit_amount}\n"
        f"Phone: {pretty_phone(phone)}\n"
        f"Email: {email or '(none)'}\n"
        f"Telegram: {telegram_username or '(none)'}\n"
        f"Referred by: {referred_by or '(none)'}\n"
        f"{linked_note}\n\n"
        f"Review and approve at https://innercircletrading.co/admin/member/{member_id}"
    )

    content = """
<section>
  <div class="wrap" style="max-width: 640px; text-align: center; padding: 60px 0;">
    <span class="eyebrow">Submitted</span>
    <h1 style="font-size: 36px; margin-bottom: 20px;">You're in the queue.</h1>
    <p style="color: var(--ink-dim); font-size: 17px;">We're verifying your details. You'll get the currency signals Telegram group link once approved, sent straight to you on Telegram.</p>
    <a href="/" class="btn btn-primary" style="margin-top: 32px;">Back to Home</a>
  </div>
</section>
"""
    return render_template_string(base_layout("Submitted", content, "signals"))


@app.route("/onboarding")
def onboarding():
    content = """
<section class="hero" style="padding-bottom: 40px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">Start Your Journey</span>
      <h1>Get into the<br>signals. <em>Free.</em></h1>
      <p class="lede">Joining Inner Circle costs nothing. The only money involved is your own trading deposit, which goes straight into your own broker account, not to us, and stays yours the whole time. Follow these four steps to get set up.</p>
    </div>
  </div>
</section>

<section style="padding-top: 40px;">
  <div class="wrap" style="max-width: 760px;">

    <div class="ob-step">
      <div class="ob-num">01</div>
      <div>
        <h3>Make your account</h3>
        <p>Click the link below and fill in your details to register with our broker.</p>
        <a href="https://go.kudo.com/visit/?bta=35562&brand=kudotrade" class="inline-link" target="_blank" rel="noopener">Register with Kudo →</a>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">02</div>
      <div>
        <h3>Verify your identity</h3>
        <p>Check your email from the broker and upload your proof of age and address when asked.</p>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">03</div>
      <div>
        <h3>Deposit</h3>
        <p>Minimum deposit is £300, this goes into your own broker account, it's your money and you can withdraw it any time. Whatever you deposit, the broker adds 50% on top, free of charge, deposit £1,000 and they add £500.</p>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">04</div>
      <div>
        <h3>Link your account in MT5</h3>
        <p>Open the MT5 app, tap the menu → Manage Accounts → '+' → Login to an existing account, then enter your new account number and password.</p>
      </div>
    </div>

  </div>

  <div class="wrap" style="max-width: 760px; margin-top: 32px;">
    <div class="form-panel" style="text-align: center;">
      <label style="display: flex; align-items: flex-start; gap: 12px; text-align: left; margin: 0 0 24px; cursor: pointer;">
        <input type="checkbox" id="confirm-check" onchange="toggleActivate()" style="width: 20px; height: 20px; margin-top: 2px; accent-color: var(--gold); flex-shrink: 0;">
        <span style="font-size: 14px; color: var(--ink-dim);">I confirm I've registered, verified my identity, deposited, and linked my account in MT5.</span>
      </label>
      <button id="activate-btn" disabled onclick="window.location.href='/onboarding/activate'" class="btn btn-primary" style="opacity: 0.4; cursor: not-allowed; border: none; width: 100%;">Activate My Account</button>
    </div>
  </div>
</section>

<script>
function toggleActivate() {
  const btn = document.getElementById('activate-btn');
  const checked = document.getElementById('confirm-check').checked;
  btn.disabled = !checked;
  btn.style.opacity = checked ? '1' : '0.4';
  btn.style.cursor = checked ? 'pointer' : 'not-allowed';
}
</script>
"""
    return render_template_string(base_layout("Onboarding", content, "onboarding"))


@app.route("/onboarding/activate")
def onboarding_activate():
    content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">Activation</span>
      <h1>Final step ,<br><em>prove it works.</em></h1>
      <p class="lede">10 quick practice trades confirm your account is set up correctly. All at 0.01 volume, the smallest size, so this stays low-risk.</p>
    </div>
  </div>
</section>

<section style="padding-top: 20px;">
  <div class="wrap" style="max-width: 760px;">

    <div class="callout" style="margin-bottom: 40px;">Made it here without completing account setup? Head back to <a href="/onboarding" class="inline-link">Step 1–4</a> first.</div>

    <div class="ob-step">
      <div class="ob-num">05</div>
      <div>
        <h3>Add EUR/USD to your watchlist</h3>
        <p>In MT5, tap the search icon in the Quotes tab, type EUR/USD, and tap it to add it.</p>
        <div class="diagram-wrap">{ADDPAIR_SVG}</div>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">06</div>
      <div>
        <h3>Place 10 trades to activate your account</h3>
        <p>This is a live account, but these 10 trades are purely for set up, to prove it's connected properly and that you know how to open and close a trade. You're not holding these open or actually trading with them.</p>
        <ol style="color: var(--ink-dim); font-size: 15px; line-height: 2; padding-left: 22px; margin: 16px 0;">
          <li>Tap <strong>Trade</strong> at the bottom, then the <strong>+</strong> in the top right.</li>
          <li>Check the middle of the screen says <strong>EUR/USD</strong>.</li>
          <li>Set the lot number in the middle to <strong>0.01</strong>.</li>
          <li>Press <strong>Sell by Market</strong>.</li>
          <li>Repeat 4 more times, 5 sells in total.</li>
          <li>Now do the same again, but press <strong>Buy by Market</strong> instead, 5 times in total.</li>
          <li>Head to the <strong>Trade</strong> tab, you'll see all 10 sitting open.</li>
          <li>Press and hold each one, then press the orange <strong>Close</strong> banner, for all 10.</li>
        </ol>
        <p>This confirms your account is genuinely connected and that you know the open/close mechanics, all in one go. Since this is for set up purposes only, close each one straight after opening it, don't leave them running.</p>
        <div class="diagram-wrap">{TICKET_SVG}</div>
        <div class="diagram-wrap">{CLOSE_SVG}</div>
      </div>
    </div>

    <div class="ob-step">
      <div class="ob-num">07</div>
      <div>
        <h3>Screenshot & verify on Telegram</h3>
        <p>Go to your <strong>History</strong> tab and take one screenshot showing all 10 closed trades together. Send it, plus your deposit confirmation screenshot, to <strong>@Innercircleverifybot</strong>. The bot replies with a verification code.</p>
        <a href="https://t.me/Innercircleverifybot" class="inline-link" target="_blank" rel="noopener">Open @Innercircleverifybot →</a>
      </div>
    </div>

    <div class="form-panel" style="margin-top: 40px;">
      <h3 style="font-size: 20px; margin-bottom: 6px;">Submit for review</h3>
      <p style="color: var(--ink-dim); font-size: 14px; margin: 0;">Once you've completed every step, send us your details and we'll verify and approve your access.</p>
      <form method="POST" action="/onboarding/submit">
        <label>Title</label>
        <select name="title" required style="width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink); padding: 13px 16px; border-radius: 10px; font-family: 'Inter'; font-size: 15px;">
          <option value="">Select</option>
          <option>Mr</option>
          <option>Mrs</option>
          <option>Miss</option>
          <option>Ms</option>
        </select>
        <label>Full name</label>
        <input type="text" name="name" required>
        <label>Broker account number</label>
        <input type="text" name="account_number" required>
        <label>Deposit amount</label>
        <input type="text" name="deposit_amount" required>
        <label>Phone number</label>
        <input type="tel" name="phone" required>
        <label>Email address</label>
        <input type="email" name="email" placeholder="you@example.com" required>
        <label>Telegram verification code</label>
        <input type="text" name="code" placeholder="e.g. IC-7X4K9" required>
        <button type="submit">Submit for Review</button>
      </form>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Activation", content, "onboarding"))


@app.route("/onboarding/submit", methods=["POST"])
def onboarding_submit():
    title = request.form.get("title", "")
    name = request.form.get("name", "")
    account_number = request.form.get("account_number", "")
    deposit_amount = request.form.get("deposit_amount", "")
    phone = request.form.get("phone", "")
    email = request.form.get("email", "")
    code = request.form.get("code", "")

    member = match_existing_member(phone=phone, account_number=account_number, email=email)

    # Already approved and onboarding again? They've most likely lost their code.
    # Tell them so, and send it again rather than quietly filing another request.
    if member and member.get("status") == "approved" and (member.get("access_code") or "").strip():
        _m, code, sent = resend_access_code(member["id"])
        audit(member["id"], "onboarded again", "already approved, code resent instead")
        content = f"""
<section style="padding: 80px 0;">
  <div class="wrap" style="max-width: 560px; text-align: center;">
    <div class="ring-mark" style="margin: 0 auto 24px;"><span>✓</span></div>
    <span class="eyebrow">You're already in</span>
    <h1 style="font-size: 30px; margin: 12px 0 18px;">You already have an account</h1>
    <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 20px;">
      No need to onboard again, you're already approved. It looks like you've lost your access code,
      so here it is.
    </p>
    <div class="callout" style="text-align:center;">
      <span class="mono" style="font-size: 22px; letter-spacing: .06em;">{esc(code or 'ask us and we will resend it')}</span>
    </div>
    <p style="color: var(--ink-dim); font-size: 14px; margin: 20px 0 28px;">
      {"We've sent it to you on Telegram as well." if sent else "Write it down somewhere safe."}
    </p>
    <div class="cta-row" style="justify-content:center;">
      <a href="/unlock" class="btn btn-primary">Log in now</a>
      <a href="/messages" class="btn btn-ghost">Something's not right</a>
    </div>
  </div>
</section>
"""
        return render_template_string(base_layout("You already have an account", content, ""))

    if member:
        member_id = member["id"]
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE members SET updated_at=NOW(), "
                        "account_number=COALESCE(NULLIF(account_number,''), %s), "
                        "deposit_amount=COALESCE(NULLIF(deposit_amount,''), %s), "
                        "verification_code=COALESCE(NULLIF(%s,''), verification_code), "
                        "title=COALESCE(NULLIF(title,''), %s), "
                        "name=COALESCE(NULLIF(name,''), %s), "
                        "email=COALESCE(email, %s), "
                        "phone=COALESCE(NULLIF(phone,''), %s), "
                        "phone_normalized=COALESCE(phone_normalized, %s), "
                        "tier=COALESCE(NULLIF(tier,''), 'gold') WHERE id=%s",
                        (account_number, deposit_amount, code, title, name, clean_email(email),
                         phone, normalize_phone(phone), member_id))
            finally:
                conn.close()
        audit(member_id, "onboarding submitted", "matched to this existing account")
        linked_note = f"MATCHED to their existing account (#{member_id}), no second account made."
    else:
        member_id = create_pending_member(
            tier="gold", title=title, name=name, account_number=account_number,
            deposit_amount=deposit_amount, phone=phone, email=email, verification_code=code
        )
        linked_note = "New record."

    notify_admin(
        "New GOLD onboarding submission:\n\n"
        f"Title: {title}\n"
        f"Name: {name}\n"
        f"Account #: {account_number}\n"
        f"Deposit: {deposit_amount}\n"
        f"Phone: {phone}\n"
        f"Email: {email or '(none)'}\n"
        f"Code: {code}\n"
        f"{linked_note}\n"
        f"Member ID: {member_id}\n\n"
        "Review and approve at /admin"
    )

    content = """
<section>
  <div class="wrap" style="max-width: 640px; text-align: center; padding: 60px 0;">
    <span class="eyebrow">Submitted</span>
    <h1 style="font-size: 36px; margin-bottom: 20px;">You're in the queue.</h1>
    <p style="color: var(--ink-dim); font-size: 17px;">We're checking your submission against your Telegram verification code. You'll get your Telegram group link and website access code once approved, sent straight to you on Telegram.</p>
    <a href="/" class="btn btn-primary" style="margin-top: 32px;">Back to Home</a>
  </div>
</section>
"""
    return render_template_string(base_layout("Submitted", content, "onboarding"))


def parse_course(md_text):
    """Splits course markdown into a list of {section, title, body} lessons."""
    lines = md_text.split("\n")
    lessons = []
    current_section = None
    current_title = None
    current_body = []

    def flush():
        if current_title is not None:
            body = "\n".join(current_body).strip()
            body_lines = body.split("\n")
            while body_lines and body_lines[-1].strip() == "---":
                body_lines.pop()
                while body_lines and body_lines[-1].strip() == "":
                    body_lines.pop()
            body = "\n".join(body_lines).strip()
            lessons.append({
                "section": current_section,
                "title": current_title,
                "body": body,
            })

    for line in lines:
        if line.startswith("## Disclaimer"):
            flush()
            current_section = None
            current_title = "Disclaimer"
            current_body = []
        elif line.startswith("## SECTION"):
            flush()
            current_title = None
            current_body = []
            current_section = line.replace("## ", "").strip()
        elif line.startswith("### "):
            flush()
            current_title = line.replace("### ", "").strip()
            current_body = []
        else:
            current_body.append(line)
    flush()
    return lessons


def render_diagrams(html, diagrams, prefix):
    for num, svg in diagrams.items():
        html = html.replace(f"<p>[[{prefix}:{num}]]</p>", f'<div class="diagram-wrap">{svg}</div>')
    return html


def lesson_page(course_slug, course_title, lessons, idx, diagrams, diagram_prefix, back_url, unlock_label, unlock_url):
    idx = max(0, min(idx, len(lessons) - 1))
    lesson = lessons[idx]
    body_html = md_lib.markdown(lesson["body"], extensions=["tables"])
    body_html = render_diagrams(body_html, diagrams, diagram_prefix)

    progress_pct = round(((idx + 1) / len(lessons)) * 100)
    section_label = lesson["section"] or ""

    prev_html = f'<a href="/education/{course_slug}/{idx-1}" class="btn btn-ghost">← Back</a>' if idx > 0 else '<span></span>'
    next_html = (
        f'<a href="/education/{course_slug}/{idx+1}" class="btn btn-primary">Next →</a>'
        if idx < len(lessons) - 1
        else (f'<a href="{unlock_url}" class="btn btn-primary">{unlock_label}</a>'
              if unlock_label else
              f'<a href="{back_url}" class="btn btn-primary">Finish, back to overview</a>')
    )

    content = f"""
<section style="padding: 60px 0 40px;">
  <div class="wrap" style="max-width: 720px;">
    <a href="{back_url}" class="inline-link" style="font-size: 13px;">← {course_title} overview</a>
    <a href="/education/{course_slug}/contents" class="inline-link" style="font-size: 13px; margin-left: 20px;">Contents</a>
    <div style="height: 4px; background: var(--line); border-radius: 4px; margin: 24px 0 8px; overflow: hidden;">
      <div style="height: 100%; width: {progress_pct}%; background: var(--gold); border-radius: 4px;"></div>
    </div>
    <p style="font-size: 12px; color: var(--ink-dim); margin: 0;">Lesson {idx+1} of {len(lessons)}</p>
    {f'<span class="eyebrow" style="margin-top: 28px;">{section_label}</span>' if section_label else ''}
    <h1 style="font-size: 30px; margin: 10px 0 30px;">{lesson["title"]}</h1>
    <div class="course-content" style="max-width: 100%;">
      {body_html}
    </div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 56px; padding-top: 32px; border-top: 1px solid var(--line);">
      {prev_html}
      {next_html}
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout(lesson["title"], content, "education"))


def contents_page(course_slug, course_title, lessons, back_url):
    sections = {}
    for i, l in enumerate(lessons):
        key = l["section"] or ""
        sections.setdefault(key, []).append((i, l["title"]))

    blocks = []
    for section, items in sections.items():
        rows = "".join(
            f'<a href="/education/{course_slug}/{i}" style="display: block; padding: 14px 0; border-bottom: 1px solid var(--line); color: var(--ink);">'
            f'<span style="color: var(--ink-dim); font-family: \'IBM Plex Mono\', monospace; font-size: 12px; margin-right: 12px;">{i+1:02d}</span>{title}</a>'
            for i, title in items
        )
        if section:
            blocks.append(f'<h2 style="font-size: 20px; margin: 40px 0 12px;">{section}</h2>{rows}')
        else:
            blocks.append(rows)

    content = f"""
<section style="padding: 60px 0;">
  <div class="wrap" style="max-width: 680px;">
    <a href="{back_url}" class="inline-link" style="font-size: 13px;">← Back to course</a>
    <span class="eyebrow" style="margin-top: 24px;">Contents</span>
    <h1 style="font-size: 32px; margin: 10px 0 40px;">{course_title}</h1>
    {"".join(blocks)}
  </div>
</section>
"""
    return render_template_string(base_layout(f"{course_title}, Contents", content, "education"))


@app.route("/education/fundamentals/contents")
def education_fundamentals_contents():
    # Non-members see the overview only, never the full lesson list.
    if not has_access("fundamentals"):
        return redirect(url_for("education_fundamentals"))
    lessons = parse_course(FUNDAMENTALS_MD)
    return contents_page("fundamentals", "Trading Fundamentals", lessons, "/education/fundamentals")


@app.route("/education/fundamentals")
def education_fundamentals():
    lessons = parse_course(FUNDAMENTALS_MD)
    if has_access("fundamentals"):
        fund_ctas = ('<a href="/education/fundamentals/0" class="btn btn-primary">Start Course</a>'
                     '<a href="/education/fundamentals/contents" class="btn btn-ghost">All lessons</a>')
    else:
        fund_ctas = ('<a href="/onboarding" class="btn btn-primary">Start Onboarding</a>'
                     '<a href="/unlock" class="btn btn-ghost">I have an access code</a>')
    content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">Free · 5 sections · {len(lessons) - 1} lessons</span>
      <h1>Trading<br>Fundamentals</h1>
      <p class="lede">Everything before and around your first trade, one lesson at a time.</p>
      <div class="cta-row">{fund_ctas}</div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">From members</span>
      <h2>What people say</h2>
    </div>
    <div class="grid5" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));">
      <div class="benefit">
        <div class="icon">"</div>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-bottom: 16px;">"I'd tried following signals before with zero idea what TP or SL even meant. This actually explained it properly."</p>
        <p style="font-size: 13px; color: var(--gold); margin: 0;">Placeholder review, swap for a real one</p>
      </div>
      <div class="benefit">
        <div class="icon">"</div>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-bottom: 16px;">"Broken down lesson by lesson so I never felt overwhelmed. Could actually follow along on my phone."</p>
        <p style="font-size: 13px; color: var(--gold); margin: 0;">Placeholder review, swap for a real one</p>
      </div>
      <div class="benefit">
        <div class="icon">"</div>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-bottom: 16px;">"The MT5 walkthrough alone saved me hours of confusion getting set up."</p>
        <p style="font-size: 13px; color: var(--gold); margin: 0;">Placeholder review, swap for a real one</p>
      </div>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Trading Fundamentals", content, "fundamentals"))


@app.route("/education/fundamentals/<int:idx>")
def education_fundamentals_lesson(idx):
    if not is_verified():
        return locked_page(
            "Free course lessons unlock once you've completed onboarding and been approved. "
            "Already approved? Enter your access code to unlock it.",
            "Start Onboarding", "/onboarding"
        )
    lessons = parse_course(FUNDAMENTALS_MD)
    return lesson_page(
        "fundamentals", "Trading Fundamentals", lessons, idx,
        FUND_DIAGRAMS, "FDIAGRAM",
        "/education/fundamentals", "Unlock Advanced Chart Reading (£99)", "/education/advanced/0"
    )


@app.route("/education/advanced/contents")
def education_advanced_contents():
    if not has_access("advanced"):
        return redirect(url_for("education_advanced"))
    lessons = parse_course(ADVANCED_MD)
    return contents_page("advanced", "Advanced Chart Reading", lessons, "/education/advanced")


@app.route("/education/advanced")
def education_advanced():
    lessons = parse_course(ADVANCED_MD)
    if has_access("advanced"):
        adv_ctas = ('<a href="/education/advanced/0" class="btn btn-primary">Start Course</a>'
                    '<a href="/education/advanced/contents" class="btn btn-ghost">All lessons</a>')
    else:
        adv_ctas = ('<a href="/education/advanced/0" class="btn btn-primary">Unlock for £99</a>'
                    '<a href="/onboarding" class="btn btn-ghost">Start with the free course</a>')
    content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">£99 · One-time · 3 sections · {len(lessons) - 1} lessons</span>
      <h1>Advanced Chart<br>Reading</h1>
      <p class="lede">Learn to read a chart yourself, not just follow along, one lesson at a time.</p>
      <div class="cta-row">{adv_ctas}</div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">From members</span>
      <h2>What people say</h2>
    </div>
    <div class="grid5" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));">
      <div class="benefit">
        <div class="icon">"</div>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-bottom: 16px;">"First time I've actually understood what a Fair Value Gap is instead of just hearing the term thrown around."</p>
        <p style="font-size: 13px; color: var(--gold); margin: 0;">Placeholder review, swap for a real one</p>
      </div>
      <div class="benefit">
        <div class="icon">"</div>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-bottom: 16px;">"The XAUUSD walkthrough made the whole thing click. Worth it just for that lesson alone."</p>
        <p style="font-size: 13px; color: var(--gold); margin: 0;">Placeholder review, swap for a real one</p>
      </div>
      <div class="benefit">
        <div class="icon">"</div>
        <p style="color: var(--ink-dim); font-size: 15px; line-height: 1.7; margin-bottom: 16px;">"Honest about win rates instead of promising the world, that alone made me trust it more."</p>
        <p style="font-size: 13px; color: var(--gold); margin: 0;">Placeholder review, swap for a real one</p>
      </div>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Advanced Chart Reading", content, "advanced"))


@app.route("/education/advanced/<int:idx>")
def education_advanced_lesson(idx):
    if not is_paid():
        price = f"£{ADVANCED_PRICE_PENCE / 100:.0f}"
        if card_payments_on() and is_verified():
            # Card payment unlocks by itself, so this is the quickest route.
            cta = (f'<form method="POST" action="/advanced/buy">'
                   f'<button type="submit" class="btn btn-primary" '
                   f'style="font-size:16px; padding:18px 40px; width:auto;">'
                   f'Pay {price} by card and unlock now</button></form>'
                   f'<p style="color:var(--ink-dim); font-size:13.5px; margin-top:14px;">'
                   f'Access switches on the moment your payment goes through. No waiting, no screenshots.</p>')
            next_steps = """
    <div class="form-panel" style="margin-top: 36px; text-align: left;">
      <h3 style="font-size: 16px; margin-bottom: 12px;">What happens next</h3>
      <ol style="color: var(--ink-dim); font-size: 14px; line-height: 1.9; padding-left: 20px; margin: 0;">
        <li>Pay by card on the next page.</li>
        <li>You come straight back here and the course is open. That's it.</li>
        <li>Nothing to send us, and nothing to wait for.</li>
      </ol>
      <p style="color: var(--ink-dim); font-size: 13.5px; margin: 16px 0 0; padding-top: 16px; border-top: 1px solid var(--line);">
        Rather use PayPal? <a href="{paypal}" target="_blank" rel="noopener" class="inline-link">Pay here</a>,
        then send the receipt to <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">our bot</a>
        and we'll unlock it by hand, usually well within 24 hours.
      </p>
    </div>""".replace("{paypal}", PAYPAL_LINK)
        elif card_payments_on():
            cta = ('<a href="/unlock" class="btn btn-primary" style="font-size:16px; padding:18px 40px;">'
                   'Log in to buy it</a>'
                   '<p style="color:var(--ink-dim); font-size:13.5px; margin-top:14px;">'
                   "Log in first so it unlocks on your account the second you've paid.</p>")
            next_steps = ""
        else:
            cta = (f'<a href="{PAYPAL_LINK}" target="_blank" rel="noopener" class="btn btn-primary" '
                   f'style="font-size: 16px; padding: 18px 40px;">Pay {price} &amp; Unlock</a>')
            next_steps = """
    <div class="form-panel" style="margin-top: 40px; text-align: left;">
      <h3 style="font-size: 16px; margin-bottom: 12px;">What happens next</h3>
      <ol style="color: var(--ink-dim); font-size: 14px; line-height: 1.9; padding-left: 20px; margin: 0;">
        <li>Complete your payment through the link above.</li>
        <li><strong>Important:</strong> use the same name as your Inner Circle account so we can match it up.</li>
        <li>Please give our admin team up to 24 hours to review your payment.</li>
        <li>You'll get a message on Telegram once it's live, then just refresh this page.</li>
      </ol>
      <p style="color: var(--gold); font-size: 14px; line-height: 1.7; margin: 16px 0 0; padding-top: 16px; border-top: 1px solid var(--line);">
        Want it sorted quicker? Message
        <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">our bot on Telegram</a>
        saying <strong>"advanced paid"</strong> and send a screenshot of your payment. It goes straight to our admin team for review.
      </p>
    </div>"""

        content = f"""
<section style="padding: 80px 0;">
  <div class="wrap" style="max-width: 560px; text-align: center;">
    <div class="ring-mark" style="margin: 0 auto 24px;"><span>🔒</span></div>
    <span class="eyebrow">{price} · One-time payment</span>
    <h1 style="font-size: 30px; margin: 12px 0 18px;">Advanced Chart Reading</h1>
    <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 32px;">
      23 lessons teaching you to read charts yourself, candlestick patterns, market structure,
      support and resistance, liquidity, and building your own strategy. One payment, yours for good.
    </p>

    {cta}
    {next_steps}
  </div>
</section>
"""
        return render_template_string(base_layout("Unlock Advanced", content, "education"))
    lessons = parse_course(ADVANCED_MD)
    return lesson_page(
        "advanced", "Advanced Chart Reading", lessons, idx,
        CHART_DIAGRAMS, "DIAGRAM",
        "/education/advanced", "", ""
    )


@app.route("/education")
def education():
    content = """
<section class="hero" style="padding-bottom: 40px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">Education</span>
      <h1>Learn it <em>properly.</em></h1>
      <p class="lede">Two courses. One free foundation, one advanced track for members who want to read charts themselves.</p>
    </div>
  </div>
</section>

<section style="padding-top: 40px;">
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">Free</span>
      <h2>Trading Fundamentals</h2>
      <p>5 sections, 41 lessons, everything before and around your first trade.</p>
    </div>
    <div class="process" style="grid-template-columns: repeat(3, 1fr);">
      <div class="step"><div class="num">01</div><h3>Placing a Trade</h3><p>TP/SL, lot size, entry, your first trade, demo accounts, reading a signal, the pre-trade checklist.</p></div>
      <div class="step"><div class="num">02</div><h3>Terminology</h3><p>Candles & wicks, bulls vs bears, pips, leverage, margin, order types, drawdown, trader types.</p></div>
      <div class="step"><div class="num">03</div><h3>Advancing Your Trade</h3><p>Trailing stops, partials, layering, liquidity, volatility, funded accounts.</p></div>
    </div>
    <div class="process" style="grid-template-columns: repeat(2, 1fr); margin-top: 40px;">
      <div class="step"><div class="num">04</div><h3>Trading Mindset</h3><p>Over-leveraging, common emotions, building discipline.</p></div>
      <div class="step"><div class="num">05</div><h3>More to Know</h3><p>Risk, common mistakes, market hours, troubleshooting, and the bridge into Advanced.</p></div>
    </div>
    <a href="/education/fundamentals" class="btn btn-primary" style="margin-top: 40px;">View Full Curriculum</a>
  </div>
</section>

<section id="advanced">
  <div class="wrap">
    <div class="section-head">
      <span class="eyebrow">£99 · One-time</span>
      <h2>Advanced Chart Reading & Technical Analysis</h2>
      <p>3 sections, 23 lessons, for members who want to form their own view of the market.</p>
    </div>
    <div class="process">
      <div class="step"><div class="num">01</div><h3>Understanding Candlesticks</h3><p>Anatomy, bullish/bearish, single and multi-candle patterns.</p></div>
      <div class="step"><div class="num">02</div><h3>Reading Price Movement</h3><p>Trends, structure, trend lines, support/resistance, supply/demand, liquidity, FVG, Fibonacci, volume, timeframes.</p></div>
      <div class="step"><div class="num">03</div><h3>Building Your Own Strategy</h3><p>What makes a strategy, backtesting, an honest look at win rates, journaling.</p></div>
    </div>
    <a href="/education/advanced" class="btn btn-primary" style="margin-top: 40px;">View Full Curriculum</a>
  </div>
</section>
"""
    return render_template_string(base_layout("Education", content, "education"))


HER_MASTERCLASSES = [
    ("Becoming HER: The Foundation", """This is where it starts, and honestly it has very little to do with money at first.

Becoming HER means deciding you're someone who handles her own finances. Not someone who's going to, one day, when things calm down. Someone who does, now, imperfectly.

That decision comes before the skill. The skill follows the decision, not the other way around.

**What we're doing in this masterclass**

Most women were never taught to see themselves as someone who invests. We were taught to save, to budget, to be careful, to not take up too much space. Useful things, but they're defensive. They keep you where you are.

Building wealth is offensive. It requires you to take up space on purpose.

**The three shifts**

1. From "I'm not good with money" to "I haven't been taught this yet." One is an identity, the other is a gap. Only one of them can be closed.
2. From secrecy to clarity. Knowing exactly what you have, what you owe, what comes in. Most people avoid this because it feels shameful. Clarity is what makes everything else possible.
3. From permission to decision. You don't need anyone's approval to build this.

**Your work this week**

Write down, honestly and privately, what you actually have. Every account, every debt, every number. Don't judge it, just see it.

Then write one sentence: "I am someone who..." and finish it with who you're becoming."""),

    ("Becoming HER: Believing You're Capable", """Before anything changes on the outside, something has to shift in what you believe is available to you.

Not in a woo way. In a practical, evidence-based way.

**The gap between capable and believing you're capable**

Most women reading this are already capable. You manage far more than you give yourself credit for. You've handled harder things than reading a chart.

The gap isn't ability. It's that nobody ever reflected your capability back to you in this particular area, so you assumed it wasn't yours.

**Where "I can't" actually comes from**

Usually one of three places:

1. Someone told you once, and you believed them.
2. You tried something adjacent, it went badly, and you generalised.
3. You've never seen a woman like you do it, so it doesn't feel available.

None of those are evidence about your ability. They're evidence about your exposure.

**Building belief on purpose**

Belief follows proof. So build small proof deliberately:

- Do one thing you said you couldn't. Small. Place one 0.01 trade correctly.
- Note it. Actually write it down, or the brain discards it.
- Repeat until the story starts to argue back.

**Your work this week**

Write: "I used to think I couldn't ______, and now I ______."

Fill it in with anything from your life. Driving. A job. Leaving something. You've done this before. This is the same process pointed at money."""),

    ("Manifestation, Done Properly", """Let's be honest about what this is and isn't.

Manifestation isn't wishing and waiting. Nothing arrives because you thought about it hard enough.

What it actually is: getting specific about what you want, so your attention and decisions start organising around it. That's not magic, that's how focus works. You notice what you're primed to notice.

**Why vague goals fail**

"I want more money" gives your brain nothing to act on. "I want £500 a month extra by summer so I can stop dreading the school holidays" is a target. One of those changes what you do on a Tuesday.

**The three parts**

1. **Specific.** Amount, timeframe, and what it's actually for. The 'for' matters most, it's what keeps you going when it's boring.
2. **Emotional.** Not the number, the feeling underneath it. Safety? Freedom? Not having to ask? Get honest, most money goals are really about something else.
3. **Behavioural.** What does someone who has that already do daily? Start doing one of those things now.

**The uncomfortable part**

Manifestation without action is just a nice mood. The action is what actually moves it. What this does is make the action obvious and make you want to do it.

**Your work this week**

Write your specific want. Amount, date, purpose. Then write the one behaviour you'd need to be consistent at. Then do that behaviour this week, once."""),

    ("Visualisation: Who Are You Becoming?", """This is the fun one, and it's more useful than it sounds.

**Meet her**

Picture the version of you who's already there. Two years ahead, financially independent, calm about money.

Get specific. Genuinely specific.

- What does her morning look like? What time does she get up, and why?
- What is she wearing? Not designer necessarily, but how does she dress for herself?
- How does she talk about money? Casually? Without apologising?
- What does she say no to?
- What's she stopped worrying about entirely?
- How does she carry herself walking into a room?
- What does she do when something goes wrong?

Write it out properly. Not bullet points, actual description. The detail is what makes it usable.

**Why this works**

You can't move toward something you can't picture. Vague ambition produces vague action.

Once she's clear, every decision gets a filter: *would she do this?* That's a far better guide than motivation, which comes and goes.

**The bit people skip**

She isn't only richer. She has different habits, different boundaries, different self-talk. The money is downstream of those, not the other way round.

**Your work this week**

Write her out. A full page. Then read it back and underline the three things you could start doing this week."""),

    ("Believe You're Her Before You Become Her", """This is the whole thing, really.

**Embodiment over aspiration**

Aspiration keeps her in the future. "One day I'll be that woman." Which means today you're still the woman who isn't.

Embodiment collapses the gap. You start behaving like her now, in small ways, and the identity catches up.

**What this looks like practically**

Not pretending you have money you don't. That's fantasy and it ends badly.

It's the behaviours:

- She checks her accounts without dread. So you check yours, weekly, even when it's uncomfortable.
- She doesn't apologise for asking about money. So you ask the question.
- She takes her own goals seriously. So you put the twenty minutes in your calendar like it's a meeting.
- She doesn't panic after one bad day. So you follow your rule and stop for the day.

Each one feels small. Each one is you being her, briefly. Do it enough and there's no transition moment, you just are.

**The identity question**

Stop asking "how do I get there?" and ask "what would she do right now?"

Different question, different answer, immediately actionable.

**On the days you don't feel like her**

You won't, often. Do it anyway. Feelings follow action far more reliably than the reverse.

**Your work this week**

Pick one behaviour of hers. Just one. Do it every day this week, especially on the days you don't feel like it. That's the work."""),

    ("Mindset Work That Actually Sticks", """Mindset work fails when it's just positive thinking. Telling yourself you're abundant while avoiding your bank balance doesn't do anything.

**What actually shifts things**

1. **Notice the thought.** "I'm rubbish with money." Catch it in the moment.
2. **Ask if it's true.** Not "is it nice", is it *true*? Usually it's an old story, not a fact.
3. **Find the counter-evidence.** You've managed something hard before. Name it.
4. **Replace with something believable.** Not "I'm amazing with money", your brain will reject that. Try "I'm learning this, and I'm further than I was."

Believable beats impressive. A reframe you don't buy does nothing.

**The daily practice**

Five minutes. Genuinely five.

- One thing that went well, however small.
- One thought you caught and questioned.
- One thing you'll do tomorrow.

That's it. Consistency beats intensity here too.

**When it gets hard**

It will. You'll have a losing week and every old belief will show up loudly, saying it was never for you.

That's the moment the work matters. Not when things are going well.

**Your work this week**

Do the five minutes daily. Write it somewhere. At the end of the week read it back, you'll see the pattern in your own thinking, which is the point."""),

    ("Knowing Your Worth", """Most women undercharge, under-ask, and over-explain. Not because we don't know our value, but because we were taught that naming it is arrogant.

**Where this shows up**

- Accepting less than you're worth because asking felt uncomfortable.
- Explaining a price instead of just saying it.
- Feeling guilty about money you've earned.
- Saying "it's only a bit" about something you worked hard for.

**The reframe**

Your worth isn't up for negotiation based on how comfortable other people are with it. Someone else's discomfort with your ambition is not information about whether your ambition is reasonable.

**In trading specifically**

Undervaluing yourself shows up as closing winners early. You take the small profit because part of you doesn't quite believe you're allowed the bigger one. Then you sit and watch it run to where your target was.

That's not a strategy problem. That's a worth problem wearing a strategy costume.

**Your work this week**

Notice one moment where you shrink. A price, a request, a boundary, a target. Just notice it. Then next time, say the thing without the explanation after it."""),

    ("The People Around You", """Not everyone will be pleased for you. That's worth preparing for.

**What tends to happen**

When you start changing, people who knew the old version get uncomfortable. Not because they're bad people, usually because your growth quietly asks a question of their own choices.

You'll hear things like: "Isn't that risky?" "Must be nice." "Don't get carried away." Often from people who love you.

**How to handle it**

- **You don't owe everyone the details.** Sharing less isn't dishonesty, it's boundaries.
- **Distinguish concern from projection.** Concern asks questions. Projection makes statements about what you can't do.
- **Find people who are doing it.** That's what the group is for. It's much harder to believe something's impossible when you're watching women do it weekly.

**On partners and family**

Money is loaded in relationships. If you're building something and it's causing friction, that friction is usually about something older than money.

You're still allowed to build it.

**Your work this week**

Name one person who genuinely supports this, and one whose opinion you've been over-weighting. Adjust accordingly."""),

    ("Habits That Build Her", """Motivation is unreliable. Habits are what actually carry you.

**Why this matters more than it sounds**

Every woman who's built financial independence did it in ordinary weeks. Not dramatic ones. Ordinary Tuesdays where she did the small thing again.

**The four that matter here**

1. **Check in with your money weekly.** Same day, ten minutes. Accounts, positions, where you are. Familiarity kills the dread.
2. **Learn in small doses.** One lesson, twenty minutes. Not four hours on a Sunday you'll never repeat.
3. **Review your trades.** What you did, why, what happened. This is where actual improvement comes from.
4. **Protect one thing daily.** A walk, a page, five quiet minutes. You cannot build from empty.

**Making them stick**

- Attach it to something you already do. After the school run. With your morning coffee.
- Make it embarrassingly small to start. Five minutes counts.
- Miss one day, fine. Never miss two.

**Your work this week**

Pick one. Just one. Attach it to something existing and do it for seven days."""),

    ("Goal Setting That Actually Works", """Most goals fail because they're wishes with a deadline attached.

**The problem with "I want to make £1,000 a month"**

It's an outcome, and outcomes aren't fully in your control. The market does what it does. Chasing an outcome you can't control leads to forcing trades, which loses money.

**Set process goals instead**

- "I'll follow my risk rules on every trade this month."
- "I'll complete one lesson a week."
- "I'll journal every trade."

You control all of those completely. And they're the things that actually produce the outcome.

**The structure**

1. **One year:** where do you want to be? Keep it honest, not fantasy.
2. **Ninety days:** what needs to be true by then?
3. **This week:** what's the one thing?

Most people set the year and skip the week. The week is the only part that does anything.

**Reviewing without self-flagellation**

Monthly, ask three questions: what worked, what didn't, what am I changing? That's it. No moral judgement, you're gathering data, not building a case against yourself.

**Your work this week**

Write your one-year, ninety-day and this-week. One line each. Put the weekly one somewhere you'll see it."""),

    ("Money Stories & Where They Came From", """Every one of us is running money software we didn't write.

Maybe you watched a parent stress about bills. Maybe money was never discussed at all. Maybe you were praised for being the one who never asked for anything.

None of that was your choice. All of it is still shaping how you handle money now.

**Common ones worth spotting**

*"It's selfish to want more."* Usually learned from someone who never got to want more themselves.

*"I'm rubbish with numbers."* Usually said once, by someone else, and then carried for twenty years.

*"Someone else deals with that."* Often true, and often the reason women end up financially exposed when circumstances change.

*"I'll sort it when things settle down."* Things don't settle down. That's the whole point.

**Why this matters for trading**

Your money story shows up in your trades. Fear of taking up space becomes closing winners too early. Feeling undeserving becomes self-sabotage after a good run. Scarcity becomes over-leveraging to catch up.

You can learn every technical skill going, and old beliefs will still override them under pressure.

**Your work this week**

Name one money belief you inherited. Write where it came from. Then write what you'd rather believe instead."""),

    ("Building Financial Independence", """Independence isn't a number in an account. It's the number of choices available to you.

**What it actually looks like**

- Being able to leave a job, a relationship, a situation, without financial panic making the decision for you.
- Saying no without calculating the cost.
- Helping someone you love without checking your balance first.
- Not needing to explain your spending to anyone.

That's the real goal. Wealth is just the mechanism.

**The layers, in order**

1. **A buffer.** Even a small one. Three hundred pounds you don't touch changes how you sleep.
2. **Cover.** Enough to handle a few months if something goes wrong. This is the layer that buys you the ability to walk away.
3. **Growth.** Money that works while you're doing other things. This is where trading and investing sit.
4. **Freedom.** Enough that work becomes a choice.

Most people try to skip to layer three or four. That's usually why it falls apart, no buffer means one bad month wipes out months of progress.

**Where trading fits**

Trading is a growth tool. It's a bad emergency fund and a worse rescue plan. Money you need next month has no business in the market.

**Your work this week**

Identify which layer you're genuinely on. Not where you'd like to be. Then name the single next step, not the whole ladder."""),

    ("Confidence That Doesn't Need Permission", """Confidence isn't a feeling you wait for. It's the evidence you build.

**The trap**

We tell ourselves we'll feel ready once we know enough. So we read more, watch more, prepare more, and never start. Preparation becomes a very respectable form of hiding.

The knowing comes from doing. Always has.

**Competence stacking**

Every small completed thing is evidence. You placed a trade. You closed it properly. You stuck to your stop loss when everything in you wanted to move it.

None of those feel like much alone. Stack thirty of them and you're a different person, because now you have proof rather than hope.

**On being the only woman in the room**

You may often be. Trading spaces are loud and male-dominated by default, which is exactly why Wealth Circle exists.

You don't need to be louder to belong. Competence is quieter than confidence and lasts considerably longer.

**Your work this week**

Write down three things you can do now that you couldn't six months ago. Anything. Read them back when you're doubting yourself."""),

    ("Handling Fear, Loss & Getting Back Up", """You will lose money. Not might. Will.

Anyone who tells you otherwise is either selling something or hasn't been at this long enough.

**Why losses hurt more than they should**

A £20 loss rarely feels like £20. It feels like proof, that you're not good at this, that you shouldn't have tried, that everyone else has it figured out.

That's not the loss talking. That's the money story from masterclass two.

**Separating the outcome from the decision**

A good decision can produce a bad outcome. You can follow your plan exactly and still lose. That's not failure, that's probability.

The only real question after a loss: did I follow my process? If yes, it was a good trade with a bad outcome. If no, that's the thing to fix, not the market.

**The spiral, and how to stop it**

Loss, then panic, then a bigger trade to win it back, then a bigger loss. This is where accounts die. Not from one bad trade, from the reaction to it.

The circuit breaker is boring and it works: stop for the day. Not forever, just today.

**Your work this week**

Decide your rule now, while you're calm. Mine might be: two losses in a day and I'm done until tomorrow. Write yours down before you need it."""),

    ("Your Wealth Plan", """Everything so far comes together here.

**Three questions**

1. **What is this actually for?** Not "more money". What does it buy you? Options? Time? Security? Get specific, vague goals don't survive difficult weeks.
2. **What can you genuinely commit?** Money and time, honestly. Twenty minutes a day you'll actually do beats two hours you won't.
3. **What's your line?** The amount you won't go past, the risk you won't take, the rule you won't break.

**Building it**

- **Capital:** what you're starting with, and what you'd add monthly if anything.
- **Risk:** your lot size per trade based on your balance, not on how confident you feel.
- **Learning:** which course, how often, when.
- **Review:** a set day each month to look at what actually happened.
- **Boundaries:** your stop-for-the-day rule, and what you won't do even when tempted.

**On patience**

This is slower than social media suggests. Compounding is unglamorous for a long time and then suddenly isn't. The women who get there are rarely the cleverest, they're the ones still going in year three.

**Your work this week**

Write your plan. One page, plain language, no jargon. Something you could hand to a friend and have her understand it.

Then come share it in the group. That's what it's there for."""),
]


HER_CATEGORIES = [
    ("Becoming Her",
     "Belief, identity and stepping into the woman you're building toward.",
     ["Becoming HER: The Foundation",
      "Becoming HER: Believing You're Capable",
      "Believe You're Her Before You Become Her",
      "Visualisation: Who Are You Becoming?"]),
    ("Manifesting & Goal Setting",
     "Getting specific about what you want, then building the path to it.",
     ["Manifestation, Done Properly",
      "Goal Setting That Actually Works",
      "Habits That Build Her"]),
    ("Money Mindset",
     "The stories underneath how you handle money, and how to rewrite them.",
     ["Money Stories & Where They Came From",
      "Mindset Work That Actually Sticks",
      "Knowing Your Worth"]),
    ("Independence & Wealth",
     "The practical side of building something that's genuinely yours.",
     ["Building Financial Independence",
      "Your Wealth Plan"]),
    ("Confidence & Resilience",
     "Holding your nerve, handling setbacks, and the people around you.",
     ["Confidence That Doesn't Need Permission",
      "Handling Fear, Loss & Getting Back Up",
      "The People Around You"]),
]

HER_LESSONS = [
    ("Money Mindset", """Most of us grew up absorbing messages about money that were never really ours. That it's rude to talk about, that wanting more is greedy, that someone else handles it.

None of that is true, and none of it has to stay.

Building wealth starts with giving yourself permission to want it. Not apologising for it, not shrinking it down to something more palatable. Just wanting it, plainly.

**Something to sit with this week:** what's one belief about money you picked up from someone else that you've never actually questioned?"""),

    ("Confidence Is Built, Not Born", """Nobody starts confident. That's the bit nobody tells you.

Confidence isn't something you wait to feel before you act. It's the thing that shows up afterwards, once you've done the hard thing badly a few times and survived it.

Your first trades will feel uncomfortable. Your first loss will sting more than the numbers justify. That's not a sign you're not cut out for this, it's just what the beginning feels like for everyone.

**Something to sit with:** where in your life have you already done this? Started something scary, been rubbish at it, and got good anyway?"""),

    ("Handling a Loss Without Spiralling", """A losing trade is information. It isn't a verdict on you.

The danger isn't the loss itself, it's the story we attach to it. "I'm bad at this." "I should have known." "Everyone else is doing better."

None of that is analysis. It's just noise, and it's the thing that leads to revenge trading and blown accounts.

The traders who last are the ones who can look at a loss, note what happened, and move on without making it mean something about their worth.

**Something to sit with:** what do you normally say to yourself after something goes wrong? Would you say it to a friend?"""),

    ("Comparison Is a Thief", """Someone in the group will post a bigger win than yours. Someone will seem to pick it up faster.

You have no idea what account size they're working with, how long they've been at it, or what they're not posting.

Your only real competition is who you were three months ago. That's the only comparison that tells you anything useful.

**Something to sit with:** what's one thing you understand now that you didn't at the start?"""),

    ("Consistency Over Intensity", """Big bursts of effort feel productive. They rarely are.

Someone who studies twenty minutes a day for a year will end up further ahead than someone who binges for a weekend and then disappears for a month.

The same is true of trading. Small, boring, repeatable beats dramatic every time.

**Something to sit with:** what's the smallest version of showing up that you could genuinely do every day?"""),

    ("Boundaries Around Your Money", """Building wealth is as much about what you protect as what you earn.

That includes protecting your capital with proper risk management. It also includes protecting your time, your headspace, and your energy from people who drain it.

You're allowed to keep your finances private. You're allowed to say no. You're allowed to prioritise your own growth without justifying it.

**Something to sit with:** where do you need a firmer boundary right now?"""),

    ("Independence Is the Real Goal", """Money isn't the point. Options are.

The ability to leave a situation that isn't working. To say no. To take a risk without it being catastrophic. To help someone you love without checking your balance first.

That's what this is actually about. Keep that in view when the day to day feels slow.

**Something to sit with:** what would having genuine options change for you?"""),
]

HER_QUOTES = [
    ("What you're not changing, you're choosing.", ""),
    ("She remembered who she was and the game changed.", "Lalah Delia"),
    ("A woman with a voice is, by definition, a strong woman.", "Melinda Gates"),
    ("Doubt kills more dreams than failure ever will.", "Suzy Kassem"),
    ("You do not need permission to take up space.", ""),
    ("The question isn't who is going to let me, it's who is going to stop me.", "Ayn Rand"),
    ("Small steps, taken consistently, beat big steps taken once.", ""),
    ("You didn't come this far to only come this far.", ""),
]


@app.route("/her")
def her():
    if not has_access("her"):
        return redirect(url_for("community"))

    quotes_preview = "".join(
        f'<div class="her-quote"><p>"{q}"</p>{f"<cite>{a}</cite>" if a else ""}</div>'
        for q, a in HER_QUOTES[:5])

    content = f"""
<section class="her-hero">
  <div class="wrap" style="max-width:760px; text-align:center;">
    <div class="her-mark">FEMALE<br>WEALTH</div>
    <span class="eyebrow" style="color:var(--rose);">Members only</span>
    <h1 style="margin:16px 0 18px; font-size:48px;">Welcome to <em style="color:var(--rose);">Female Wealth.</em></h1>
    <p class="lede" style="max-width:580px; margin:0 auto;">Your private space. Masterclasses on becoming her, mindset work, and a circle of women building exactly what you're building.</p>
  </div>
</section>

<section style="padding-top:60px;">
  <div class="wrap" style="max-width:680px;">
    <div class="her-panel" style="margin-bottom:16px;">
      <span class="eyebrow" style="color:var(--rose);">Your group</span>
      <h3 style="font-size:22px; margin:8px 0 10px;">The community on Telegram</h3>
      <p style="color:var(--ink-dim); font-size:14px; margin:0 0 20px;">Saved here so you never lose it. This is where the day to day happens.</p>
      <a href="https://t.me/+TWaAqQlTTuU1OGU0" target="_blank" rel="noopener" class="btn btn-primary" style="width:100%; text-align:center;">Open the group</a>
    </div>

    <a href="/her/masterclasses" class="her-card">
      <span class="her-num">01</span>
      <span class="her-title">Masterclasses to become her<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">{len(HER_MASTERCLASSES)} sessions across 5 areas</span></span>
      <span class="her-arrow">→</span>
    </a>

    <a href="/her/mindset" class="her-card">
      <span class="her-num">02</span>
      <span class="her-title">Mindset &amp; growth<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">{len(HER_LESSONS)} short reads for when you need a reset</span></span>
      <span class="her-arrow">→</span>
    </a>
  </div>
</section>

<section style="padding-top:20px;">
  <div class="wrap" style="max-width:860px;">
    <div class="section-head" style="max-width:100%; text-align:center; margin-bottom:26px;">
      <span class="eyebrow" style="color:var(--rose);">For the harder days</span>
      <h2 style="font-size:28px;">Words to come back to</h2>
    </div>
    <div class="her-quotes">{quotes_preview}</div>
    <div style="text-align:center; margin-top:26px;">
      <a href="/her/words" class="inline-link" style="color:var(--rose);">See them all →</a>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width:680px;">
    <a href="/her/share" class="her-card">
      <span class="her-num">03</span>
      <span class="her-title">Share &amp; support<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">Share your results or get help any time</span></span>
      <span class="her-arrow">→</span>
    </a>
  </div>
</section>
"""
    return render_template_string(base_layout("Female Wealth", content, "community"))


@app.route("/her/masterclasses")
def her_masterclasses():
    if not has_access("her"):
        return redirect(url_for("community"))

    title_to_idx = {t: i for i, (t, _) in enumerate(HER_MASTERCLASSES)}
    blocks = []
    for cat_name, cat_desc, titles in HER_CATEGORIES:
        items = "".join(
            f'<a href="/her/masterclass/{title_to_idx[t]}" class="her-card small">'
            f'<span class="her-title">{t}</span><span class="her-arrow">→</span></a>'
            for t in titles if t in title_to_idx)
        blocks.append(
            f'<div style="margin-bottom:44px;">'
            f'<h2 style="font-family:\'Fraunces\',serif; font-size:24px; margin:0 0 6px; color:var(--rose);">{cat_name}</h2>'
            f'<p style="color:var(--ink-dim); font-size:14px; margin:0 0 18px;">{cat_desc}</p>'
            f'{items}</div>')

    content = f"""
<section style="padding:60px 0 40px;">
  <div class="wrap" style="max-width:680px;">
    <a href="/community" class="inline-link" style="font-size:13px; color:var(--rose);">← Back to Female Wealth</a>
    <div class="section-head" style="max-width:100%; margin:24px 0 40px;">
      <span class="eyebrow" style="color:var(--rose);">Members only</span>
      <h1 style="font-size:34px; margin:10px 0 12px;">Masterclasses to become her</h1>
      <p>{len(HER_MASTERCLASSES)} sessions on independence, stepping into your best self, and building real confidence with money.</p>
    </div>
    {"".join(blocks)}
  </div>
</section>
"""
    return render_template_string(base_layout("Masterclasses", content, "community"))


@app.route("/her/mindset")
def her_mindset():
    if not has_access("her"):
        return redirect(url_for("community"))
    items = "".join(
        f'<a href="/her/lesson/{i}" class="her-card small"><span class="her-num">{i+1:02d}</span>'
        f'<span class="her-title">{t}</span><span class="her-arrow">→</span></a>'
        for i, (t, _) in enumerate(HER_LESSONS))
    content = f"""
<section style="padding:60px 0 40px;">
  <div class="wrap" style="max-width:680px;">
    <a href="/community" class="inline-link" style="font-size:13px; color:var(--rose);">← Back to Female Wealth</a>
    <div class="section-head" style="max-width:100%; margin:24px 0 34px;">
      <span class="eyebrow" style="color:var(--rose);">Short reads</span>
      <h1 style="font-size:34px; margin:10px 0 12px;">Mindset &amp; growth</h1>
      <p>Quick lessons for when you need a reset.</p>
    </div>
    {items}
  </div>
</section>
"""
    return render_template_string(base_layout("Mindset", content, "community"))


@app.route("/her/words")
def her_words():
    if not has_access("her"):
        return redirect(url_for("community"))
    quotes = "".join(
        f'<div class="her-quote"><p>"{q}"</p>{f"<cite>{a}</cite>" if a else ""}</div>'
        for q, a in HER_QUOTES)
    content = f"""
<section style="padding:60px 0 40px;">
  <div class="wrap" style="max-width:860px;">
    <a href="/community" class="inline-link" style="font-size:13px; color:var(--rose);">← Back to Female Wealth</a>
    <div class="section-head" style="max-width:100%; margin:24px 0 34px; text-align:center;">
      <span class="eyebrow" style="color:var(--rose);">For the harder days</span>
      <h1 style="font-size:34px; margin:10px 0 12px;">Words to come back to</h1>
    </div>
    <div class="her-quotes">{quotes}</div>
  </div>
</section>
"""
    return render_template_string(base_layout("Words", content, "community"))


@app.route("/her/share")
def her_share():
    if not has_access("her"):
        return redirect(url_for("community"))
    content = """
<section style="padding:60px 0 40px;">
  <div class="wrap" style="max-width:680px;">
    <a href="/community" class="inline-link" style="font-size:13px; color:var(--rose);">← Back to Female Wealth</a>
    <div class="section-head" style="max-width:100%; margin:24px 0 34px; text-align:center;">
      <span class="eyebrow" style="color:var(--rose);">Share &amp; support</span>
      <h1 style="font-size:34px; margin:10px 0 12px;">How are you getting on?</h1>
    </div>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:18px;">
      <div class="her-panel" style="text-align:center;">
        <div class="her-icon">★</div>
        <h3 style="font-size:17px; margin-bottom:8px;">Share your results</h3>
        <p style="color:var(--ink-dim); font-size:14px;">Message "share results" to our bot and send your screenshots. Wins, lessons, progress, all welcome.</p>
        <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link" style="font-size:13px;">Share now →</a>
      </div>
      <div class="her-panel" style="text-align:center;">
        <div class="her-icon">♥</div>
        <h3 style="font-size:17px; margin-bottom:8px;">Need support?</h3>
        <p style="color:var(--ink-dim); font-size:14px;">Message us privately any time. No question is too small in here, genuinely.</p>
        <a href="/messages" class="inline-link" style="font-size:13px;">Message us →</a>
      </div>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Share & Support", content, "community"))


@app.route("/her/masterclass/<int:idx>")
def her_masterclass(idx):
    if not has_access("her"):
        return locked_page("HER is our private space for Wealth Circle members.", "Request to Join", "/community")
    idx = max(0, min(idx, len(HER_MASTERCLASSES) - 1))
    title, body = HER_MASTERCLASSES[idx]
    body_html = md_lib.markdown(body)
    prev_html = f'<a href="/her/masterclass/{idx-1}" class="btn btn-ghost">← Back</a>' if idx > 0 else '<span></span>'
    next_html = (f'<a href="/her/masterclass/{idx+1}" class="btn btn-primary">Next →</a>'
                 if idx < len(HER_MASTERCLASSES) - 1 else '<a href="/her" class="btn btn-primary">Finish</a>')
    content = f"""
<section style="padding:60px 0;">
  <div class="wrap" style="max-width:660px;">
    <a href="/her" class="inline-link" style="font-size:13px; color:var(--rose);">← Back to HER</a>
    <div style="height:4px; background:var(--line); border-radius:4px; margin:24px 0 8px; overflow:hidden;">
      <div style="height:100%; width:{round(((idx+1)/len(HER_MASTERCLASSES))*100)}%; background:var(--rose); border-radius:4px;"></div>
    </div>
    <p style="font-size:12px; color:var(--ink-dim); margin:0;">Masterclass {idx+1} of {len(HER_MASTERCLASSES)}</p>
    <h1 style="font-size:34px; margin:22px 0 28px;">{title}</h1>
    <div class="course-content" style="max-width:100%;">{body_html}</div>
    <div style="display:flex; justify-content:space-between; margin-top:50px; padding-top:30px; border-top:1px solid var(--line);">
      {prev_html}{next_html}
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout(title, content, "community"))


@app.route("/her/lesson/<int:idx>")
def her_lesson(idx):
    if not has_access("her"):
        return locked_page(
            "HER is our private space for Wealth Circle members.",
            "Request to Join", "/community"
        )
    idx = max(0, min(idx, len(HER_LESSONS) - 1))
    title, body = HER_LESSONS[idx]
    body_html = md_lib.markdown(body)

    prev_html = f'<a href="/her/lesson/{idx-1}" class="btn btn-ghost">← Back</a>' if idx > 0 else '<span></span>'
    next_html = (f'<a href="/her/lesson/{idx+1}" class="btn btn-primary">Next →</a>'
                 if idx < len(HER_LESSONS) - 1
                 else '<a href="/her" class="btn btn-primary">Finish</a>')

    content = f"""
<section style="padding:60px 0;">
  <div class="wrap" style="max-width:680px;">
    <a href="/her" class="inline-link" style="font-size:13px;">← Back to HER</a>
    <div style="height:4px; background:var(--line); border-radius:4px; margin:24px 0 8px; overflow:hidden;">
      <div style="height:100%; width:{round(((idx+1)/len(HER_LESSONS))*100)}%; background:var(--rose); border-radius:4px;"></div>
    </div>
    <p style="font-size:12px; color:var(--ink-dim); margin:0;">Lesson {idx+1} of {len(HER_LESSONS)}</p>
    <h1 style="font-size:30px; margin:22px 0 26px;">{title}</h1>
    <div class="course-content" style="max-width:100%;">{body_html}</div>
    <div style="display:flex; justify-content:space-between; margin-top:50px; padding-top:30px; border-top:1px solid var(--line);">
      {prev_html}{next_html}
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout(title, content, "community"))


@app.route("/community")
def community():
    if has_access("her"):
        return her()
    login_prompt = ("" if session.get("member_id") else
        '<div class="callout" style="margin-bottom:20px;">Not logged in? '
        '<a href="/unlock" class="inline-link">Log in with your access code first</a> '
        'so this unlocks on your existing account.</div>')
    content = f"""
<section class="her-hero">
  <div class="wrap" style="max-width:760px; text-align:center;">
    <div class="her-mark">FEMALE<br>WEALTH</div>
    <span class="eyebrow" style="color:var(--rose);">Women only</span>
    <h1 style="margin:16px 0 18px; font-size:46px;">A space built for <em style="color:var(--rose);">her.</em></h1>
    <p class="lede" style="max-width:600px; margin:0 auto 34px;">Trading spaces are loud and male-dominated by default. This one isn't. Female Wealth is our private community for women, plus a members-only library you won't find anywhere else on the site.</p>
    <a href="#request" class="btn btn-primary">Request Access</a>
  </div>
</section>

<section>
  <div class="wrap" style="max-width:720px;">
    <div class="section-head" style="max-width:100%; text-align:center; margin-bottom:34px;">
      <span class="eyebrow" style="color:var(--rose);">What's inside</span>
      <h2 style="font-size:32px;">Once you're in</h2>
      <p>Here's what unlocks the moment your request is approved.</p>
    </div>

    <div class="her-card" style="cursor:default;">
      <span class="her-num">01</span>
      <span class="her-title">The private community<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">A women-only group to ask anything, share wins, and grow together</span></span>
      <span class="her-arrow">🔒</span>
    </div>

    <div class="her-card" style="cursor:default;">
      <span class="her-num">02</span>
      <span class="her-title">Masterclasses to become her<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">15 members-only sessions across 5 areas, not available anywhere else</span></span>
      <span class="her-arrow">🔒</span>
    </div>

    <div class="her-card" style="cursor:default;">
      <span class="her-num">03</span>
      <span class="her-title">Mindset &amp; growth library<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">Short reads for the days you need a reset</span></span>
      <span class="her-arrow">🔒</span>
    </div>

    <div class="her-card" style="cursor:default;">
      <span class="her-num">04</span>
      <span class="her-title">Share &amp; support<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">Share your results and get help privately, any time</span></span>
      <span class="her-arrow">🔒</span>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width:720px;">
    <div class="section-head" style="max-width:100%; text-align:center; margin-bottom:30px;">
      <span class="eyebrow" style="color:var(--rose);">The masterclasses cover</span>
      <h2 style="font-size:30px;">Five areas, built around you</h2>
    </div>
    <div class="her-quotes">
      <div class="her-quote"><p style="font-size:16px;">Becoming Her</p><cite>Belief, identity and stepping into who you're building toward</cite></div>
      <div class="her-quote"><p style="font-size:16px;">Manifesting &amp; Goal Setting</p><cite>Getting specific about what you want, then building the path</cite></div>
      <div class="her-quote"><p style="font-size:16px;">Money Mindset</p><cite>The stories underneath how you handle money</cite></div>
      <div class="her-quote"><p style="font-size:16px;">Independence &amp; Wealth</p><cite>Building something that's genuinely yours</cite></div>
      <div class="her-quote"><p style="font-size:16px;">Confidence &amp; Resilience</p><cite>Holding your nerve, and the people around you</cite></div>
    </div>
  </div>
</section>

<section id="request">
  <div class="wrap" style="max-width:560px;">
    <div class="section-head" style="max-width:100%; text-align:center; margin-bottom:30px;">
      <span class="eyebrow" style="color:var(--rose);">Join us</span>
      <h2 style="font-size:30px;">Request your access</h2>
      <p>Log in with your access code first, then request below, that way it unlocks on your existing account rather than creating a second one. We review every request personally.</p>
    </div>
    <div class="her-panel">
      {login_prompt}
      <form method="POST" action="/community/request">
        <label>Title</label>
        <select name="title" required style="width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink); padding: 13px 16px; border-radius: 10px; font-family: 'Inter'; font-size: 15px;">
          <option value="">Select</option>
          <option>Mrs</option>
          <option>Miss</option>
          <option>Ms</option>
        </select>
        <label>Full name</label>
        <input type="text" name="name" required>
        <label>Your phone number</label>
        <input type="tel" name="phone" placeholder="07700 900123" required>
        <label>Email address (optional)</label>
        <input type="email" name="email" placeholder="you@example.com">
        <p style="color: var(--ink-dim); font-size: 12.5px; margin: 6px 0 0;">
          Use the same number you signed up with and this unlocks on your existing account, with the access code you already have.
        </p>
        <label>Anything you'd like us to know? (optional)</label>
        <input type="text" name="note" placeholder="Optional">
        <button type="submit">Request Access</button>
      </form>
      <p style="color: var(--ink-dim); font-size: 13px; margin-top: 18px;">
        Female Wealth is a women-only space. Everything else on Inner Circle, signals, education and support, is open to everyone.
      </p>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Female Wealth", content, "community"))


@app.route("/community/request", methods=["POST"])
def community_request():
    title = request.form.get("title", "")
    name = request.form.get("name", "")
    phone = request.form.get("phone", "")
    email = request.form.get("email", "")
    note = request.form.get("note", "")

    # Find the account this request belongs to. Being logged in wins, otherwise
    # match on phone number, which is what identifies a member. Either way we
    # flag the account they already have rather than creating a second one, so
    # approving unlocks Female Wealth on the access code they already hold.
    member = match_existing_member(phone=phone, email=email)

    if member:
        member_id = member["id"]
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE members SET community_requested=TRUE, updated_at=NOW(), "
                        "title=COALESCE(NULLIF(%s,''), title), "
                        "name=COALESCE(NULLIF(name,''), %s), "
                        "phone=COALESCE(NULLIF(phone,''), %s), "
                        "email=COALESCE(email, %s), "
                        "phone_normalized=COALESCE(phone_normalized, %s) WHERE id=%s",
                        (title, name, phone, clean_email(email), normalize_phone(phone), member_id))
            finally:
                conn.close()
        how = "they were logged in" if session.get("member_id") else "their phone number"
        audit(member_id, "Female Wealth requested", f"matched to this account by {how}")
        linked_note = (f"MATCHED to their existing account (#{member_id}) by {how}. "
                       f"Approving unlocks Female Wealth on the code they already have, no new code.")
    else:
        # Nobody on that number yet, so this really is a new person.
        member_id = create_pending_member(
            tier="community", title=title, name=name, account_number="",
            deposit_amount="", phone=phone, email=email, referred_by=note
        )
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("UPDATE members SET community_requested=TRUE WHERE id=%s", (member_id,))
            finally:
                conn.close()
        linked_note = ("No existing account on that number, so this is a new record. "
                       "They'll get a code when you approve them.")

    notify_admin(
        f"\U0001F46D FEMALE WEALTH REQUEST\n\n"
        f"Title: {title}\nName: {name}\nPhone: {pretty_phone(phone)}\n"
        f"Note: {note or '(none)'}\nMember ID: {member_id}\n{linked_note}\n\n"
        f"Review and approve at https://innercircletrading.co/admin/member/{member_id}"
    )

    content = """
<section>
  <div class="wrap" style="max-width: 620px; text-align: center; padding: 70px 0;">
    <span class="eyebrow" style="color: var(--rose);">Request sent</span>
    <h1 style="font-size: 32px; margin: 12px 0 18px;">We've got your request.</h1>
    <p style="color: var(--ink-dim); font-size: 16px;">We review each one personally. You'll get your Wealth Circle invite on Telegram once approved, usually within 24 hours.</p>
    <a href="/" class="btn btn-primary" style="margin-top: 30px;">Back to Home</a>
  </div>
</section>
"""
    return render_template_string(base_layout("Request Sent", content, "community"))


if __name__ == "__main__":
    app.run(debug=True)
