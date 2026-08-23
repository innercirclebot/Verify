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
from datetime import datetime, timedelta, date
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
    ("signals_currency", "Digital Wealth VIP",           "The original group. Recommended.",
     "https://t.me/+9Tzll-11uW9jNjlk"),
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

Digital Wealth VIP (recommended)
Our original group.
https://t.me/+9Tzll-11uW9jNjlk

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
# ---------------------------------------------------------------------------
# REAL MEMBER CONTENT
# ---------------------------------------------------------------------------
# Everything here is real: signals actually posted in the groups, and things
# members actually said. Nothing is invented. Because it's real it also has to
# carry a proper disclaimer, which RESULTS_DISCLAIMER below does, and which is
# rendered next to every block that shows a result or a quote.

RESULTS_DISCLAIMER = (
    "Past results are not a guide to future results. These are real trades and real "
    "messages from our groups, but they show what happened on those days, not what "
    "will happen on yours. Trading carries risk and you can lose money, including more "
    "than you expect if you trade at a size you cannot afford. Nothing here is financial "
    "advice or a recommendation to trade. Outcomes vary from person to person depending "
    "on entry, size, timing and how the trade is managed. Never trade with money you "
    "cannot afford to lose."
)

# Signals exactly as they went out, with what the group posted afterwards.
SIGNAL_EXAMPLES = [
    {
        "group": "Digital Wealth Premium",
        "call": "Buy gold 4585 - 4588, SL 4581, TP 4593 / 4597 / 4602",
        "outcome": ["TP1 hit, +50 pips", "TP2 hit, +90 pips", "TP3 hit, +140 pips"],
    },
    {
        "group": "Digital Wealth VIP",
        "call": "Buy gold 4584 - 4579, TP 4586 / 4588",
        "outcome": ["Move to risk free", "TP1 hit", "TP2 hit", "TP3 hit",
                    "Closed out at +180 pips"],
    },
    {
        "group": "Digital Wealth Premium",
        "call": "Buy gold 4505 - 4510, SL 4499, TP 4520 / 4530",
        "outcome": ["Ran to +1000 pips"],
    },
    {
        "group": "Vaulted Pips Educational Gold",
        "call": "Gold long, managed live in the group",
        "outcome": ["Running 110+ pips",
                    "Take partials, let's hunt TP2",
                    "Ran 130+ pips, back to entry",
                    "140 pips",
                    "TP2 hit for anyone holding smaller positions"],
    },
]

# Member screenshots, served from the static/results folder in the repo.
# Deliberately a mixed picture: big days, small days, and a losing trade left
# in, because a page of nothing but wins would be misleading.
RESULTS_ITEMS = [
    {"image": "/static/results/result-1727.jpg",
     "caption": "Closed positions totalling +1,727.51",
     "alt": "MT5 history showing closed positions with 1,727.51 profit"},
    {"image": "/static/results/result-509.jpg",
     "caption": "Five gold buys closed the same morning, +509.62",
     "alt": "MT5 history showing five XAUUSD buy positions totalling 509.62"},
    {"image": "/static/results/result-173.jpg",
     "caption": "A run of gold trades, +173.29 closed, one of them a loss",
     "alt": "MT5 history showing gold trades totalling 173.29 including a losing trade"},
    {"image": "/static/results/result-101.jpg",
     "caption": "Sell positions closed for +101.15",
     "alt": "MT5 history showing sell positions totalling 101.15"},
    {"image": "/static/results/result-57.jpg",
     "caption": "Six gold trades, +57.86, including one that lost",
     "alt": "MT5 history showing six gold trades totalling 57.86"},
    {"image": "/static/results/result-28.jpg",
     "caption": "A member's best day so far on 0.01 lots, +28.72",
     "alt": "MT5 history showing small lot trades totalling 28.72"},
    {"image": "/static/results/result-small-account.jpg",
     "caption": "A small account: +11.87 profit, less £1.89 commission",
     "alt": "MT5 history on a small account showing 11.87 profit after commission"},
]

# Word for word from members. Split by space so each sits where it belongs.
MEMBER_FEEDBACK = [
    {"quote": "If this continues I can retire soon", "who": "Signals member"},
    {"quote": "Amazing week, I did my first withdrawal.", "who": "Signals member"},
    {"quote": "I wish I got into this sooner, I didn't realise how easy it was to follow.",
     "who": "Signals member"},
    {"quote": "I got into the extra signals because I wanted to trade more a day and I'm so "
              "pleased being able to catch more trades and choose which group to use.",
     "who": "Extra signals member"},
    {"quote": "I love the community, I love making money on my terms too, but the community "
              "is something I needed.", "who": "Member"},
    {"quote": "The beginners course is so useful. Being brand new I didn't know where to "
              "start and it's made everything make so much more sense. I've started on 0.01 "
              "while I get the hang of it.", "who": "Fundamentals student"},
    {"quote": "I started with £300 and already have £1000 in a few weeks while following the "
              "risk management, I'm so happy.", "who": "Signals member"},
    {"quote": "These trades are so good, let's go!", "who": "Signals member"},
]

# Course reviews, word for word. These replaced the placeholders that shipped
# with the original build.
FUNDAMENTALS_REVIEWS = [
    {"quote": "This helped me understand without guessing. Every question I had was answered "
              "through this course.", "who": "Fundamentals student"},
    {"quote": "I had no clue how to even place or read a signal. This actually explained it.",
     "who": "Fundamentals student"},
    {"quote": "Real support that actually got me understanding, which I've never had before. "
              "Thank you!", "who": "Fundamentals student"},
    {"quote": "Even to understand how to take partials and trail a stop loss, this is gold!",
     "who": "Fundamentals student"},
]

ADVANCED_REVIEWS = [
    {"quote": "After the knowledge I gained from the beginners course I had to get the "
              "advanced. I understand it takes time to learn, but this is so helpful and "
              "such a good price.", "who": "Advanced student"},
    {"quote": "I kept hearing the words fair value gap, and so many other words, which I "
              "never understood until now.", "who": "Advanced student"},
    {"quote": "Brilliant course. Topped off my own trading journey even when following the "
              "copy signals, as I now know what to look for more.", "who": "Advanced student"},
]

# Training videos for Female Wealth. Videos are hosted on YouTube (unlisted is
# fine) or Vimeo and embedded here, rather than uploaded to the site. That keeps
# the app light, gives you a proper player on every device, and means a big file
# never has to travel through Render.
#   {"title": "...", "blurb": "...", "embed": "https://www.youtube.com/embed/XXXX"}
# For an unlisted YouTube video the embed URL is youtube.com/embed/<the id>.
HER_VIDEOS = []

# From the women in the Circle, kept separate so they show on Female Wealth.
COMMUNITY_FEEDBACK = [
    {"quote": "When I heard about this I needed in, I'm excited for it to get started.",
     "who": "Wealth Circle member"},
    {"quote": "I love the idea of the female community. I needed a place to connect with "
              "others over interests and make new friends.", "who": "Wealth Circle member"},
    {"quote": "I can't wait for this to get started, I love being connected with other hustlers.",
     "who": "Wealth Circle member"},
    {"quote": "I've been trading now for two months using these signals, and when they "
              "announced this community I instantly wanted to be a part of it. It's just "
              "what's missing.", "who": "Wealth Circle member"},
]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


def notify_admin(text: str):
    """
    Message the admin chat. Returns the sent message id, which is what lets an
    admin reply "approve" to a specific alert and have it act on the right one.
    """
    if not TELEGRAM_API or not ADMIN_CHAT_ID:
        return None
    try:
        r = requests.post(f"{TELEGRAM_API}/sendMessage",
                          json={"chat_id": int(ADMIN_CHAT_ID), "text": text}, timeout=10)
        return (r.json().get("result") or {}).get("message_id")
    except Exception:
        return None


def is_admin_chat(chat_id):
    """Only the configured admin chat can approve anything from Telegram."""
    if not ADMIN_CHAT_ID or chat_id is None:
        return False
    try:
        return int(chat_id) == int(ADMIN_CHAT_ID)
    except (TypeError, ValueError):
        return False


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
                CREATE TABLE IF NOT EXISTS hub_posts (
                    id SERIAL PRIMARY KEY,
                    space TEXT DEFAULT 'main',
                    video_url TEXT,
                    locked BOOLEAN DEFAULT FALSE,
                    member_id INT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    pinned BOOLEAN DEFAULT FALSE,
                    hidden BOOLEAN DEFAULT FALSE,
                    reply_count INT DEFAULT 0,
                    last_activity TIMESTAMP DEFAULT NOW(),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hub_replies (
                    id SERIAL PRIMARY KEY,
                    post_id INT NOT NULL,
                    member_id INT,
                    body TEXT NOT NULL,
                    from_team BOOLEAN DEFAULT FALSE,
                    hidden BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id SERIAL PRIMARY KEY,
                    member_id INT,
                    chat_id BIGINT,
                    kind TEXT DEFAULT 'feedback',
                    section TEXT,
                    body TEXT,
                    file_ids TEXT,
                    status TEXT DEFAULT 'pending',
                    source TEXT DEFAULT 'bot',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    decided_at TIMESTAMPTZ
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal (
                    id SERIAL PRIMARY KEY,
                    member_id INT NOT NULL,
                    day DATE NOT NULL,
                    prompt TEXT,
                    body TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (member_id, day)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS her_videos (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    blurb TEXT,
                    embed TEXT NOT NULL,
                    position INT DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
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

            try:
                cur.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS checkin_sent_at TIMESTAMPTZ;")
            except Exception:
                pass

            for col, coltype in [("space", "TEXT DEFAULT 'main'"),
                                 ("locked", "BOOLEAN DEFAULT FALSE"),
                                 ("video_url", "TEXT"),
                                 ("video_ok", "BOOLEAN DEFAULT FALSE")]:
                try:
                    cur.execute(f"ALTER TABLE hub_posts ADD COLUMN IF NOT EXISTS {col} {coltype};")
                except Exception:
                    pass
            try:
                # Admin posts as the team and has no member row of their own.
                cur.execute("ALTER TABLE hub_posts ALTER COLUMN member_id DROP NOT NULL;")
            except Exception:
                pass

            for col, coltype in [("admin_msg_id", "BIGINT"),
                                 ("file_ids", "TEXT"),
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
                "CREATE INDEX IF NOT EXISTS idx_hub_posts_activity ON hub_posts (last_activity DESC);",
                "CREATE INDEX IF NOT EXISTS idx_hub_replies_post ON hub_replies (post_id);",
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


# One question a day, on a loop. Written to be answerable in two minutes on a
# bad day and to open something up on a good one.
JOURNAL_PROMPTS = [
    "What do you want to achieve today? One thing, not a list.",
    "What made you smile today?",
    "What is your income goal this month, and what would it change?",
    "What did you do today that the old you wouldn't have?",
    "Who are you becoming? Describe her in three lines.",
    "What are you grateful for right now, that you had nothing to do with?",
    "What did you avoid today, and what was underneath it?",
    "Where did you shrink yourself today? What could you have said instead?",
    "What is working that you haven't given yourself credit for?",
    "If money were not the issue, what would you be doing this week?",
    "What did you learn about yourself in the market this week?",
    "Who in your life is lifting you, and who is quietly costing you?",
    "What would you tell a friend who was talking to herself the way you have been?",
    "What does a good week look like, specifically, seven days from now?",
    "Take thirty minutes this week for your goals. When are you doing it? Write the time.",
    "What are you proud of that nobody else knows about?",
    "What is the story you tell about money, and is it still true?",
    "What would she do next? Just the next thing, not the whole plan.",
    "What went wrong recently, and what did it actually teach you?",
    "What do you want more of, and what are you willing to give up for it?",
    "Where are you waiting for permission that is never coming?",
    "What is one boundary you need to hold this week?",
    "What did you do this week that moved you closer, however small?",
    "What are you afraid people would say if you succeeded? Whose voice is that?",
    "What is enough, for you? Not for anyone else.",
    "Who do you want to be in a year? Write it in the present tense.",
    "What is the kindest true thing you can say about yourself today?",
    "What have you outgrown?",
    "What is the one thing that, if it were handled, would make everything easier?",
    "Write tomorrow's version of you a note. What does she need to hear?",
    "What did you handle this month that would have floored you last year?",
]


def prompt_for(day):
    """Same question for everyone on a given day, rotating through the list."""
    return JOURNAL_PROMPTS[day.toordinal() % len(JOURNAL_PROMPTS)]


def get_journal_entry(member_id, day):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT body FROM journal WHERE member_id=%s AND day=%s",
                        (member_id, day.isoformat()))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def save_journal_entry(member_id, day, prompt, body):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO journal (member_id, day, prompt, body)
                           VALUES (%s,%s,%s,%s)
                           ON CONFLICT (member_id, day)
                           DO UPDATE SET body=EXCLUDED.body, updated_at=NOW()""",
                        (member_id, day.isoformat(), prompt, (body or "").strip()[:6000]))
    except Exception:
        pass
    finally:
        conn.close()


def journal_written_days(member_id, limit=400):
    conn = get_db()
    if not conn:
        return set()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT day FROM journal
                           WHERE member_id=%s AND body IS NOT NULL AND body <> ''
                           ORDER BY day DESC LIMIT %s""", (member_id, limit))
            out = set()
            for (d,) in cur.fetchall():
                out.add(d if isinstance(d, str) else d.isoformat())
            return out
    except Exception:
        return set()
    finally:
        conn.close()


def get_her_videos():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, title, blurb, embed FROM her_videos
                           ORDER BY position ASC, id DESC""")
            return [dict(zip(("id", "title", "blurb", "embed"), r)) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def add_her_video(title, blurb, raw_url):
    """Only YouTube and Vimeo links are accepted, same guard as the board."""
    embed = video_embed_url(raw_url)
    if not embed or not (title or "").strip():
        return None
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO her_videos (title, blurb, embed)
                           VALUES (%s,%s,%s) RETURNING id""",
                        ((title or "").strip()[:160], (blurb or "").strip()[:400], embed))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def delete_her_video(vid):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM her_videos WHERE id=%s", (vid,))
    except Exception:
        pass
    finally:
        conn.close()


def send_due_checkins(limit=25):
    """
    A week after someone is approved, ask how they're getting on. Runs off the
    back of ordinary bot traffic rather than a scheduler, and marks each member
    as asked so nobody is ever chased twice.
    """
    conn = get_db()
    if not conn:
        return 0
    sent = 0
    try:
        with conn, conn.cursor() as cur:
            # cutoff worked out in Python rather than SQL, so this behaves the
            # same on any database rather than relying on INTERVAL syntax
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            cur.execute("""SELECT id, name, chat_id FROM members
                           WHERE status='approved'
                             AND chat_id IS NOT NULL
                             AND checkin_sent_at IS NULL
                             AND approved_at IS NOT NULL
                             AND approved_at < %s
                           LIMIT %s""", (cutoff, limit))
            due = cur.fetchall()
        for mid, name, chat_id in due:
            who = (name or "").strip().split(" ")[0]
            ok = send_telegram_message(
                chat_id,
                f"Hi{(' ' + who) if who else ''}, you've been with us a week now. How's it going?\n\n"
                "I'd genuinely like to know, good or bad. If something isn't working I'd rather hear it.\n\n"
                "If you're happy to share, you can send me either:\n\n"
                "💬  a line about how you're finding it\n"
                "📸  a screenshot of your results\n\n"
                "Anything you send, we'll ask before it goes anywhere near the website. "
                "Or just reply \"no thanks\" and I won't ask again."
            )
            with conn, conn.cursor() as cur:
                cur.execute("UPDATE members SET checkin_sent_at=NOW() WHERE id=%s", (mid,))
            if ok:
                sent += 1
                set_bot_state(chat_id, state="awaiting_checkin")
    except Exception:
        pass
    finally:
        conn.close()
    return sent


def add_submission(member_id, chat_id, kind, body="", file_ids=None, source="bot", section=None):
    """
    Something a member sent us that might go on the site: a line of feedback or
    a screenshot of their results. Nothing is published until it's approved.
    """
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO submissions
                           (member_id, chat_id, kind, body, file_ids, source, section)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (member_id, chat_id, kind, (body or "").strip()[:2000],
                         ",".join(file_ids or []) or None, source, section))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_submissions(status="pending", limit=200):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.*, m.name AS member_name, m.phone AS member_phone
                           FROM submissions s
                           LEFT JOIN members m ON m.id = s.member_id
                           WHERE s.status = %s
                           ORDER BY s.created_at DESC LIMIT %s""", (status, limit))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def get_submission(sub_id):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.*, m.name AS member_name FROM submissions s
                           LEFT JOIN members m ON m.id = s.member_id
                           WHERE s.id = %s""", (sub_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip([c[0] for c in cur.description], row))
    except Exception:
        return None
    finally:
        conn.close()


def decide_submission(sub_id, status, kind=None, section=None):
    """Accept or decline. Accepting is what puts it on the site."""
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE submissions
                           SET status=%s,
                               kind=COALESCE(%s, kind),
                               section=COALESCE(%s, section),
                               decided_at=NOW()
                           WHERE id=%s""", (status, kind, section, sub_id))
    except Exception:
        pass
    finally:
        conn.close()


def published(kind, section=None, limit=40):
    """Accepted submissions, which is what the public pages render."""
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            if section:
                cur.execute("""SELECT s.*, m.name AS member_name FROM submissions s
                               LEFT JOIN members m ON m.id = s.member_id
                               WHERE s.status='accepted' AND s.kind=%s AND s.section=%s
                               ORDER BY s.decided_at DESC LIMIT %s""", (kind, section, limit))
            else:
                cur.execute("""SELECT s.*, m.name AS member_name FROM submissions s
                               LEFT JOIN members m ON m.id = s.member_id
                               WHERE s.status='accepted' AND s.kind=%s
                               ORDER BY s.decided_at DESC LIMIT %s""", (kind, limit))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []
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
    if not session.get("member_id"):
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


# ---------------------------------------------------------------------------
# COMMUNITY HUB
# ---------------------------------------------------------------------------
# A place for questions and proper answers. In a Telegram group a good question
# scrolls away in an hour, so this keeps them where they can be found and
# answered once rather than twenty times.

def hub_display_name(member):
    """First name only. People are asking beginner questions in public."""
    if not member or not (member.get("name") or "").strip():
        return "Inner Circle"
    return (member["name"]).strip().split(" ")[0]


def video_embed_url(raw):
    """
    Turn a YouTube or Vimeo link into its embed form. Anything else is
    rejected rather than dropped into an iframe, because letting members embed
    arbitrary URLs would let someone put anything at all on the page.
    """
    if not raw:
        return None
    u = str(raw).strip()
    m = re.search(r"(?:youtube\.com/(?:watch\?v=|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{6,20})", u)
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}"
    m = re.search(r"vimeo\.com/(?:video/)?(\d{6,12})", u)
    if m:
        return f"https://player.vimeo.com/video/{m.group(1)}"
    return None


def create_hub_post(member_id, title, body, space="her", video_url=None):
    title = (title or "").strip()[:160]
    body = (body or "").strip()[:4000]
    if not title or not body:
        return None
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO hub_posts (member_id, title, body, space, video_url)
                           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                        (member_id, title, body, space, video_embed_url(video_url)))
            return cur.fetchone()[0]
    except Exception:
        return None
    finally:
        conn.close()


def add_hub_reply(post_id, member_id, body, from_team=False):
    body = (body or "").strip()[:4000]
    if not body:
        return None
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO hub_replies (post_id, member_id, body, from_team)
                           VALUES (%s,%s,%s,%s) RETURNING id""",
                        (post_id, member_id, body, from_team))
            new_id = cur.fetchone()[0]
            cur.execute("""UPDATE hub_posts
                           SET reply_count = (SELECT COUNT(*) FROM hub_replies
                                              WHERE post_id=%s AND hidden=FALSE),
                               last_activity = NOW()
                           WHERE id=%s""", (post_id, post_id))
            return new_id
    except Exception:
        return None
    finally:
        conn.close()


def get_hub_posts(limit=60, include_hidden=False, space="her"):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT p.*, m.name AS author_name
                FROM hub_posts p
                LEFT JOIN members m ON m.id = p.member_id
                WHERE COALESCE(p.space,'her') = %s
                  {"" if include_hidden else "AND p.hidden = FALSE"}
                ORDER BY p.pinned DESC, p.last_activity DESC
                LIMIT %s
            """, (space, limit))
            return cur.fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def get_hub_post(post_id, include_hidden=False):
    conn = get_db()
    if not conn:
        return None, []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT p.*, m.name AS author_name FROM hub_posts p
                           LEFT JOIN members m ON m.id = p.member_id
                           WHERE p.id=%s""", (post_id,))
            post = cur.fetchone()
            if not post or (post.get("hidden") and not include_hidden):
                return None, []
            cur.execute(f"""SELECT r.*, m.name AS author_name FROM hub_replies r
                            LEFT JOIN members m ON m.id = r.member_id
                            WHERE r.post_id=%s {"" if include_hidden else "AND r.hidden = FALSE"}
                            ORDER BY r.created_at ASC""", (post_id,))
            return post, cur.fetchall()
    except Exception:
        return None, []
    finally:
        conn.close()


def set_hub_hidden(kind, item_id, hidden=True):
    table = "hub_posts" if kind == "post" else "hub_replies"
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET hidden=%s WHERE id=%s", (hidden, item_id))
    except Exception:
        pass
    finally:
        conn.close()


def delete_hub_item(kind, item_id):
    """
    Remove a post or a reply for good. Hiding is usually enough, but if
    something genuinely shouldn't exist it needs to actually go.
    """
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            if kind == "post":
                cur.execute("DELETE FROM hub_replies WHERE post_id=%s", (item_id,))
                cur.execute("DELETE FROM hub_posts WHERE id=%s", (item_id,))
            else:
                cur.execute("SELECT post_id FROM hub_replies WHERE id=%s", (item_id,))
                row = cur.fetchone()
                cur.execute("DELETE FROM hub_replies WHERE id=%s", (item_id,))
                if row:
                    cur.execute("""UPDATE hub_posts SET reply_count =
                                   (SELECT COUNT(*) FROM hub_replies
                                    WHERE post_id=%s AND hidden=FALSE)
                                   WHERE id=%s""", (row[0], row[0]))
    except Exception:
        pass
    finally:
        conn.close()


def set_hub_locked(post_id, locked=True):
    """Close a thread to new comments without removing what's already there."""
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE hub_posts SET locked=%s WHERE id=%s", (locked, post_id))
    except Exception:
        pass
    finally:
        conn.close()


def get_hub_author(kind, item_id):
    """Which member wrote this, so admin can go straight to their profile."""
    table = "hub_posts" if kind == "post" else "hub_replies"
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT member_id FROM {table} WHERE id=%s", (item_id,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def set_hub_pinned(post_id, pinned=True):
    conn = get_db()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE hub_posts SET pinned=%s WHERE id=%s", (pinned, post_id))
    except Exception:
        pass
    finally:
        conn.close()


def time_ago(when):
    """Friendlier than a timestamp for a discussion feed."""
    try:
        delta = datetime.now(when.tzinfo) - when
    except Exception:
        return ""
    secs = delta.total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        hours = int(secs // 3600)
        return f"{hours} hour{'' if hours == 1 else 's'} ago"
    days = int(secs // 86400)
    if days < 30:
        return f"{days} day{'' if days == 1 else 's'} ago"
    return fmt_date(when)


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

Once you're past the initial 0.01-lot verification stage, your lot size should scale with your account, not stay fixed and not be guessed.

The rule we use is **risk 1% to 3% of your account on any single trade.** Smaller accounts can sit nearer the 3% end, because 1% of a small balance is too little to be worth trading. Larger accounts should sit nearer 1%, because you have more to protect and no need to push.

| Account | Risk per trade (1% to 3%) | Typical lot size |
|---|---|---|
| £300 | £3 to £9 | 0.01 to 0.02 |
| £500 | £5 to £15 | 0.01 to 0.03 |
| £1,000 | £10 to £30 | 0.02 to 0.06 |
| £2,500 | £25 to £75 | 0.05 to 0.15 |
| £5,000 | £50 to £150 | 0.10 to 0.30 |
| £10,000 | £100 to £300 | 0.20 to 0.60 |
| £25,000 | £250 to £750 | 0.50 to 1.50 |
| £50,000 | £500 to £1,500 | 1.00 to 3.00 |
| £100,000 | £1,000 to £3,000 | 2.00 to 6.00 |

**Why it's a range and not one number.** Your lot size isn't really decided by your balance, it's decided by your balance *and how far away your stop is*. The lot sizes above assume a stop around $5 from entry, which is typical on our gold signals.

If the stop is further away, the same lot risks more, so the lot has to come down. **Double the stop distance, halve the lot size.** A £1,000 account taking a trade with a $10 stop should be at the bottom of its range, not the top.

**Working it out yourself:**
1. Decide your risk in pounds. On £1,000 at 2%, that's £20.
2. Look at the signal and count how far the stop is from entry. Say $5.
3. On gold, 0.01 lots loses about $1 for every $1 the price moves against you. A $5 stop on 0.01 lots risks about $5.
4. £20 of risk divided by £5 per 0.01 lot = 0.04 lots.

**How to use the table:**
- Find your balance and start at the lower end of the range while you're learning.
- As the balance genuinely grows over time, not after one good trade, move up.
- If the balance drops, move back down. Your size should reflect what's in the account now, not what used to be.

**Important:** this is general guidance for people learning, not a guaranteed-safe number and not financial advice. Volatility, spread and your own risk tolerance all matter. When a signal specifies its own volume, follow the signal rather than this table. Only ever risk what you are genuinely happy to lose.

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

1. **Move SL to breakeven, plus a point**, once the trade has moved a reasonable amount in your favour, move your Stop Loss past your entry and slightly into profit. Not exactly to entry, just past it.

Why "plus a point" and not entry exactly: your entry price and your exit price are not the same price. You buy at the ask and sell at the bid, and the gap between them is the spread. If you park your Stop Loss exactly on your entry, the spread can still take you out a few pence down, and that's before any commission. Setting it a point into profit covers the spread so a stop out really is free.

**On a buy**, you entered at 4585. Price runs to 4595. Move your Stop Loss to 4586, one point above entry. If it reverses and stops you out, you close in profit, not at a loss.

**On a sell**, you entered at 4585. Price drops to 4575. Move your Stop Loss to 4584, one point below entry. Remember a sell profits as price falls, so protecting profit means moving the stop **down**, not up.

Once that's done the trade genuinely cannot cost you money. The worst case is a small win. That is the whole point of going risk free: you've taken the loss off the table and you're now only deciding how much profit to keep.

One caveat worth knowing: in very fast markets price can gap straight past your stop, and a trade held overnight can pay swap. Both are uncommon on quick scalps, but they're the reason to move to breakeven early rather than sit in an open trade hoping.
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


# ---------------------------------------------------------------------------
# MT5 SCREEN MOCKUPS
# ---------------------------------------------------------------------------
# Drawn to match what the MT5 iOS app actually looks like: white background,
# grey section rows, blue and red action bars, the real row order and wording.
# The earlier versions used the site's cream palette, which meant people were
# looking for a screen that doesn't exist.
#
# Colours lifted from the app itself:
#   text        #1C1C1E     muted     #8E8E93
#   separator   #E5E5EA     row fill  #F2F2F7
#   blue        #0B63CE     red       #D93025     orange #F5A623

# The hero artwork. Not decoration: it's a real trade shape, entry, stop and
# target marked, so the first thing on the page is the thing we actually do.
FW_MARK_SVG = """<svg class="fw-mark" viewBox="0 0 320 320" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Female Wealth, Inner Circle">
  <defs>
    <path id="fwArc" d="M 30 160 a 130 130 0 0 0 260 0" fill="none"/>
  </defs>

  <g fill="none" stroke="currentColor">
    <circle cx="160" cy="160" r="150" stroke-width="1.6"/>
    <circle cx="160" cy="160" r="140" stroke-width="1.1" opacity=".75"/>
  </g>

  <!-- the swash behind the monogram, as on the printed mark -->
  <g fill="none" stroke="currentColor" stroke-width="1.4" opacity=".65">
    <path d="M92 118 C 122 88, 200 88, 232 120"/>
    <path d="M88 140 C 120 178, 202 178, 234 138"/>
  </g>

  <text x="160" y="150" text-anchor="middle" font-family="Fraunces, Georgia, serif"
        font-size="82" fill="currentColor" letter-spacing="-2">FW</text>

  <text x="160" y="196" text-anchor="middle" font-family="Fraunces, Georgia, serif"
        font-size="19" fill="currentColor" letter-spacing="5.5">FEMALE WEALTH</text>

  <line x1="96" y1="210" x2="224" y2="210" stroke="currentColor" stroke-width=".9" opacity=".6"/>

  <text x="160" y="228" text-anchor="middle" font-family="Inter, Arial, sans-serif"
        font-size="10.5" fill="currentColor" letter-spacing="4.5" opacity=".9">INNER CIRCLE</text>

  <path d="M160 250 l -6 -6 a 4 4 0 0 1 6 -4.6 a 4 4 0 0 1 6 4.6 z"
        fill="currentColor" opacity=".85"/>

  <text font-family="Inter, Arial, sans-serif" font-size="10" fill="currentColor"
        letter-spacing="2.6" opacity=".82">
    <textPath href="#fwArc" startOffset="50%" text-anchor="middle">BUILDING WEALTH &#183; CREATING FREEDOM</textPath>
  </text>
</svg>"""


HER_ART_SVG = """<svg class="her-art" viewBox="0 0 1440 820" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="herSky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#26121C"/>
      <stop offset="50%" stop-color="#3A1A2A"/>
      <stop offset="100%" stop-color="#1A0C13"/>
    </linearGradient>
    <linearGradient id="herFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#E89BAE" stop-opacity=".28"/>
      <stop offset="100%" stop-color="#E89BAE" stop-opacity="0"/>
    </linearGradient>
    <radialGradient id="herGlow" cx="50%" cy="44%" r="58%">
      <stop offset="0%" stop-color="#1A0C13" stop-opacity=".84"/>
      <stop offset="100%" stop-color="#1A0C13" stop-opacity=".08"/>
    </radialGradient>
  </defs>
  <rect width="1440" height="820" fill="url(#herSky)"/>
  <g stroke="#E89BAE" stroke-width=".6" opacity=".12">
    <line x1="0" y1="0" x2="1440" y2="0"/><line x1="0" y1="68" x2="1440" y2="68"/><line x1="0" y1="136" x2="1440" y2="136"/><line x1="0" y1="204" x2="1440" y2="204"/><line x1="0" y1="272" x2="1440" y2="272"/><line x1="0" y1="340" x2="1440" y2="340"/><line x1="0" y1="408" x2="1440" y2="408"/><line x1="0" y1="476" x2="1440" y2="476"/><line x1="0" y1="544" x2="1440" y2="544"/><line x1="0" y1="612" x2="1440" y2="612"/><line x1="0" y1="680" x2="1440" y2="680"/><line x1="0" y1="748" x2="1440" y2="748"/><line x1="0" y1="816" x2="1440" y2="816"/>
  </g>
  <polygon points="0,820 0.0,571.5 12.1,566.6 24.2,562.5 36.3,556.5 48.4,550.1 60.5,547.0 72.6,544.0 84.7,535.3 96.8,530.2 108.9,525.1 121.0,514.8 133.1,507.8 145.2,498.3 157.3,491.1 169.4,482.8 181.5,477.7 193.6,469.5 205.7,459.9 217.8,452.6 229.9,444.1 242.0,436.3 254.1,432.6 266.2,424.6 278.3,418.1 290.4,413.7 302.5,411.4 314.6,403.9 326.7,399.4 338.8,393.6 350.9,387.1 363.0,382.0 375.1,375.9 387.2,373.6 399.3,368.9 411.4,366.9 423.5,361.9 435.6,357.5 447.7,358.4 459.8,359.3 471.9,359.8 484.0,355.5 496.1,354.7 508.2,352.7 520.3,352.9 532.4,351.7 544.5,351.1 556.6,350.7 568.7,348.6 580.8,346.3 592.9,341.7 605.0,338.3 617.1,333.1 629.2,328.0 641.3,321.7 653.4,317.2 665.5,315.6 677.6,309.2 689.7,301.7 701.8,294.3 713.9,288.8 726.1,281.9 738.2,278.1 750.3,269.9 762.4,263.1 774.5,257.9 786.6,254.0 798.7,244.7 810.8,234.4 822.9,229.8 835.0,220.5 847.1,213.8 859.2,208.7 871.3,202.7 883.4,193.6 895.5,184.0 907.6,180.0 919.7,172.4 931.8,168.7 943.9,160.9 956.0,155.9 968.1,147.5 980.2,138.8 992.3,133.5 1004.4,125.4 1016.5,122.1 1028.6,120.6 1040.7,116.1 1052.8,115.7 1064.9,114.5 1077.0,112.2 1089.1,108.9 1101.2,108.9 1113.3,109.9 1125.4,105.6 1137.5,105.3 1149.6,100.8 1161.7,96.9 1173.8,96.5 1185.9,95.6 1198.0,94.3 1210.1,92.2 1222.2,90.3 1234.3,88.6 1246.4,86.4 1258.5,81.8 1270.6,80.0 1282.7,78.4 1294.8,74.6 1306.9,73.7 1319.0,72.2 1331.1,65.8 1343.2,62.1 1355.3,57.9 1367.4,56.9 1379.5,52.9 1391.6,47.4 1403.7,45.3 1415.8,41.0 1427.9,41.0 1440.0,41.0 1440,820" fill="url(#herFill)"/>
  <polyline points="0.0,571.5 12.1,566.6 24.2,562.5 36.3,556.5 48.4,550.1 60.5,547.0 72.6,544.0 84.7,535.3 96.8,530.2 108.9,525.1 121.0,514.8 133.1,507.8 145.2,498.3 157.3,491.1 169.4,482.8 181.5,477.7 193.6,469.5 205.7,459.9 217.8,452.6 229.9,444.1 242.0,436.3 254.1,432.6 266.2,424.6 278.3,418.1 290.4,413.7 302.5,411.4 314.6,403.9 326.7,399.4 338.8,393.6 350.9,387.1 363.0,382.0 375.1,375.9 387.2,373.6 399.3,368.9 411.4,366.9 423.5,361.9 435.6,357.5 447.7,358.4 459.8,359.3 471.9,359.8 484.0,355.5 496.1,354.7 508.2,352.7 520.3,352.9 532.4,351.7 544.5,351.1 556.6,350.7 568.7,348.6 580.8,346.3 592.9,341.7 605.0,338.3 617.1,333.1 629.2,328.0 641.3,321.7 653.4,317.2 665.5,315.6 677.6,309.2 689.7,301.7 701.8,294.3 713.9,288.8 726.1,281.9 738.2,278.1 750.3,269.9 762.4,263.1 774.5,257.9 786.6,254.0 798.7,244.7 810.8,234.4 822.9,229.8 835.0,220.5 847.1,213.8 859.2,208.7 871.3,202.7 883.4,193.6 895.5,184.0 907.6,180.0 919.7,172.4 931.8,168.7 943.9,160.9 956.0,155.9 968.1,147.5 980.2,138.8 992.3,133.5 1004.4,125.4 1016.5,122.1 1028.6,120.6 1040.7,116.1 1052.8,115.7 1064.9,114.5 1077.0,112.2 1089.1,108.9 1101.2,108.9 1113.3,109.9 1125.4,105.6 1137.5,105.3 1149.6,100.8 1161.7,96.9 1173.8,96.5 1185.9,95.6 1198.0,94.3 1210.1,92.2 1222.2,90.3 1234.3,88.6 1246.4,86.4 1258.5,81.8 1270.6,80.0 1282.7,78.4 1294.8,74.6 1306.9,73.7 1319.0,72.2 1331.1,65.8 1343.2,62.1 1355.3,57.9 1367.4,56.9 1379.5,52.9 1391.6,47.4 1403.7,45.3 1415.8,41.0 1427.9,41.0 1440.0,41.0" fill="none" stroke="#F0B8C4" stroke-width="2.6" opacity=".9" stroke-linejoin="round"/>
  <rect width="1440" height="820" fill="url(#herGlow)"/>
</svg>"""


HERO_CHART_SVG = """<svg class="hero-chart" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <filter id="bloom" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="14" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="b"/></feMerge>
    </filter>
    <radialGradient id="washTeal" cx="18%" cy="80%" r="52%">
      <stop offset="0%" stop-color="#2E7F68" stop-opacity=".42"/>
      <stop offset="100%" stop-color="#2E7F68" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="washRose" cx="84%" cy="18%" r="50%">
      <stop offset="0%" stop-color="#A8524E" stop-opacity=".38"/>
      <stop offset="100%" stop-color="#A8524E" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="washGold" cx="62%" cy="94%" r="46%">
      <stop offset="0%" stop-color="#B08F5E" stop-opacity=".34"/>
      <stop offset="100%" stop-color="#B08F5E" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="readGlow" cx="50%" cy="46%" r="56%">
      <stop offset="0%" stop-color="#0E0A08" stop-opacity=".9"/>
      <stop offset="52%" stop-color="#0E0A08" stop-opacity=".62"/>
      <stop offset="100%" stop-color="#0E0A08" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="edgeFade" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0E0A08" stop-opacity=".55"/>
      <stop offset="20%" stop-color="#0E0A08" stop-opacity="0"/>
      <stop offset="88%" stop-color="#0E0A08" stop-opacity="0"/>
      <stop offset="100%" stop-color="#0E0A08" stop-opacity=".82"/>
    </linearGradient>
  </defs>

  <rect width="1440" height="900" fill="url(#washTeal)"/>
  <rect width="1440" height="900" fill="url(#washRose)"/>
  <rect width="1440" height="900" fill="url(#washGold)"/>

  <g stroke="#B08F5E" stroke-width=".6" opacity=".09">
    <line x1="0" y1="0" x2="1440" y2="0"/><line x1="0" y1="75" x2="1440" y2="75"/><line x1="0" y1="150" x2="1440" y2="150"/><line x1="0" y1="225" x2="1440" y2="225"/><line x1="0" y1="300" x2="1440" y2="300"/><line x1="0" y1="375" x2="1440" y2="375"/><line x1="0" y1="450" x2="1440" y2="450"/><line x1="0" y1="525" x2="1440" y2="525"/><line x1="0" y1="600" x2="1440" y2="600"/><line x1="0" y1="675" x2="1440" y2="675"/><line x1="0" y1="750" x2="1440" y2="750"/><line x1="0" y1="825" x2="1440" y2="825"/>
    <line x1="0" y1="0" x2="0" y2="900"/><line x1="120" y1="0" x2="120" y2="900"/><line x1="240" y1="0" x2="240" y2="900"/><line x1="360" y1="0" x2="360" y2="900"/><line x1="480" y1="0" x2="480" y2="900"/><line x1="600" y1="0" x2="600" y2="900"/><line x1="720" y1="0" x2="720" y2="900"/><line x1="840" y1="0" x2="840" y2="900"/><line x1="960" y1="0" x2="960" y2="900"/><line x1="1080" y1="0" x2="1080" y2="900"/><line x1="1200" y1="0" x2="1200" y2="900"/><line x1="1320" y1="0" x2="1320" y2="900"/>
  </g>

  <g filter="url(#bloom)" opacity=".5"><rect x="1028.2" y="500.1" width="12" height="52.19999999999993" rx="1.5" fill="#3FA987"/><rect x="1054.4" y="476.1" width="12" height="24.0" rx="1.5" fill="#3FA987"/><rect x="1080.5" y="434.5" width="12" height="41.60000000000002" rx="1.5" fill="#3FA987"/><rect x="1106.7" y="423.7" width="12" height="10.800000000000011" rx="1.5" fill="#3FA987"/><rect x="1159.1" y="421.2" width="12" height="3.0" rx="1.5" fill="#3FA987"/><rect x="1185.3" y="362.3" width="12" height="58.89999999999998" rx="1.5" fill="#3FA987"/><rect x="1211.5" y="348.3" width="12" height="14.0" rx="1.5" fill="#3FA987"/><rect x="1237.6" y="343.8" width="12" height="4.5" rx="1.5" fill="#3FA987"/><rect x="1263.8" y="314.4" width="12" height="29.400000000000034" rx="1.5" fill="#3FA987"/><rect x="1290.0" y="311.3" width="12" height="3.099999999999966" rx="1.5" fill="#3FA987"/><rect x="1316.2" y="255.6" width="12" height="55.70000000000002" rx="1.5" fill="#3FA987"/><rect x="1342.4" y="191.1" width="12" height="64.5" rx="1.5" fill="#3FA987"/><rect x="1368.5" y="156.1" width="12" height="35.0" rx="1.5" fill="#3FA987"/><rect x="1394.7" y="89.6" width="12" height="66.5" rx="1.5" fill="#3FA987"/></g>
  <g class="hero-candles" opacity=".9"><line x1="13.1" y1="146.0" x2="13.1" y2="241.4" stroke="#C4645E" stroke-width="2"/><rect x="7.1" y="161.6" width="12" height="46.0" rx="1.5" fill="#C4645E"/><line x1="39.3" y1="178.1" x2="39.3" y2="295.3" stroke="#C4645E" stroke-width="2"/><rect x="33.3" y="207.6" width="12" height="64.29999999999998" rx="1.5" fill="#C4645E"/><line x1="65.5" y1="243.4" x2="65.5" y2="348.7" stroke="#C4645E" stroke-width="2"/><rect x="59.5" y="271.9" width="12" height="65.30000000000001" rx="1.5" fill="#C4645E"/><line x1="91.6" y1="324.6" x2="91.6" y2="388.8" stroke="#C4645E" stroke-width="2"/><rect x="85.6" y="337.2" width="12" height="38.19999999999999" rx="1.5" fill="#C4645E"/><line x1="117.8" y1="335.3" x2="117.8" y2="428.8" stroke="#C4645E" stroke-width="2"/><rect x="111.8" y="375.4" width="12" height="38.700000000000045" rx="1.5" fill="#C4645E"/><line x1="144.0" y1="381.3" x2="144.0" y2="512.0" stroke="#C4645E" stroke-width="2"/><rect x="138.0" y="414.1" width="12" height="53.39999999999998" rx="1.5" fill="#C4645E"/><line x1="170.2" y1="443.0" x2="170.2" y2="540.7" stroke="#C4645E" stroke-width="2"/><rect x="164.2" y="467.5" width="12" height="27.69999999999999" rx="1.5" fill="#C4645E"/><line x1="196.4" y1="454.0" x2="196.4" y2="582.0" stroke="#C4645E" stroke-width="2"/><rect x="190.4" y="495.2" width="12" height="66.19999999999999" rx="1.5" fill="#C4645E"/><line x1="222.5" y1="547.0" x2="222.5" y2="641.8" stroke="#C4645E" stroke-width="2"/><rect x="216.5" y="561.4" width="12" height="59.10000000000002" rx="1.5" fill="#C4645E"/><line x1="248.7" y1="603.8" x2="248.7" y2="662.1" stroke="#C4645E" stroke-width="2"/><rect x="242.7" y="620.5" width="12" height="10.399999999999977" rx="1.5" fill="#C4645E"/><line x1="274.9" y1="607.2" x2="274.9" y2="684.1" stroke="#C4645E" stroke-width="2"/><rect x="268.9" y="630.9" width="12" height="23.200000000000045" rx="1.5" fill="#C4645E"/><line x1="301.1" y1="641.8" x2="301.1" y2="736.7" stroke="#C4645E" stroke-width="2"/><rect x="295.1" y="654.1" width="12" height="65.0" rx="1.5" fill="#C4645E"/><line x1="327.3" y1="693.5" x2="327.3" y2="760.9" stroke="#C4645E" stroke-width="2"/><rect x="321.3" y="719.1" width="12" height="20.199999999999932" rx="1.5" fill="#C4645E"/><line x1="353.5" y1="712.8" x2="353.5" y2="787.5" stroke="#C4645E" stroke-width="2"/><rect x="347.5" y="739.3" width="12" height="27.200000000000045" rx="1.5" fill="#C4645E"/><line x1="379.6" y1="731.0" x2="379.6" y2="797.4" stroke="#C4645E" stroke-width="2"/><rect x="373.6" y="766.5" width="12" height="12.0" rx="1.5" fill="#C4645E"/><line x1="405.8" y1="746.8" x2="405.8" y2="820.3" stroke="#3FA987" stroke-width="2"/><rect x="399.8" y="776.0" width="12" height="2.5" rx="1.5" fill="#3FA987"/><line x1="432.0" y1="741.7" x2="432.0" y2="821.6" stroke="#3FA987" stroke-width="2"/><rect x="426.0" y="762.2" width="12" height="13.799999999999955" rx="1.5" fill="#3FA987"/><line x1="458.2" y1="737.0" x2="458.2" y2="830.4" stroke="#C4645E" stroke-width="2"/><rect x="452.2" y="762.2" width="12" height="30.59999999999991" rx="1.5" fill="#C4645E"/><line x1="484.4" y1="765.0" x2="484.4" y2="832.5" stroke="#C4645E" stroke-width="2"/><rect x="478.4" y="792.8" width="12" height="28.100000000000023" rx="1.5" fill="#C4645E"/><line x1="510.5" y1="773.8" x2="510.5" y2="851.8" stroke="#3FA987" stroke-width="2"/><rect x="504.5" y="811.6" width="12" height="9.299999999999955" rx="1.5" fill="#3FA987"/><line x1="536.7" y1="765.8" x2="536.7" y2="846.9" stroke="#3FA987" stroke-width="2"/><rect x="530.7" y="787.3" width="12" height="24.300000000000068" rx="1.5" fill="#3FA987"/><line x1="562.9" y1="752.2" x2="562.9" y2="814.0" stroke="#3FA987" stroke-width="2"/><rect x="556.9" y="783.4" width="12" height="3.8999999999999773" rx="1.5" fill="#3FA987"/><line x1="589.1" y1="717.3" x2="589.1" y2="810.7" stroke="#3FA987" stroke-width="2"/><rect x="583.1" y="761.7" width="12" height="21.699999999999932" rx="1.5" fill="#3FA987"/><line x1="615.3" y1="740.3" x2="615.3" y2="797.2" stroke="#3FA987" stroke-width="2"/><rect x="609.3" y="752.7" width="12" height="9.0" rx="1.5" fill="#3FA987"/><line x1="641.5" y1="687.2" x2="641.5" y2="792.6" stroke="#3FA987" stroke-width="2"/><rect x="635.5" y="733.3" width="12" height="19.40000000000009" rx="1.5" fill="#3FA987"/><line x1="667.6" y1="709.2" x2="667.6" y2="774.6" stroke="#C4645E" stroke-width="2"/><rect x="661.6" y="733.3" width="12" height="6.900000000000091" rx="1.5" fill="#C4645E"/><line x1="693.8" y1="713.4" x2="693.8" y2="782.3" stroke="#C4645E" stroke-width="2"/><rect x="687.8" y="740.2" width="12" height="25.899999999999977" rx="1.5" fill="#C4645E"/><line x1="720.0" y1="753.8" x2="720.0" y2="823.1" stroke="#C4645E" stroke-width="2"/><rect x="714.0" y="766.1" width="12" height="19.100000000000023" rx="1.5" fill="#C4645E"/><line x1="746.2" y1="766.1" x2="746.2" y2="827.6" stroke="#C4645E" stroke-width="2"/><rect x="740.2" y="785.2" width="12" height="18.09999999999991" rx="1.5" fill="#C4645E"/><line x1="772.4" y1="754.7" x2="772.4" y2="829.7" stroke="#3FA987" stroke-width="2"/><rect x="766.4" y="767.7" width="12" height="35.59999999999991" rx="1.5" fill="#3FA987"/><line x1="798.5" y1="713.3" x2="798.5" y2="807.5" stroke="#3FA987" stroke-width="2"/><rect x="792.5" y="755.4" width="12" height="12.300000000000068" rx="1.5" fill="#3FA987"/><line x1="824.7" y1="700.2" x2="824.7" y2="780.6" stroke="#3FA987" stroke-width="2"/><rect x="818.7" y="720.4" width="12" height="35.0" rx="1.5" fill="#3FA987"/><line x1="850.9" y1="656.6" x2="850.9" y2="765.2" stroke="#3FA987" stroke-width="2"/><rect x="844.9" y="698.8" width="12" height="21.600000000000023" rx="1.5" fill="#3FA987"/><line x1="877.1" y1="675.7" x2="877.1" y2="717.3" stroke="#3FA987" stroke-width="2"/><rect x="871.1" y="692.2" width="12" height="6.599999999999909" rx="1.5" fill="#3FA987"/><line x1="903.3" y1="651.9" x2="903.3" y2="723.6" stroke="#3FA987" stroke-width="2"/><rect x="897.3" y="679.6" width="12" height="12.600000000000023" rx="1.5" fill="#3FA987"/><line x1="929.5" y1="654.7" x2="929.5" y2="704.9" stroke="#3FA987" stroke-width="2"/><rect x="923.5" y="664.9" width="12" height="14.700000000000045" rx="1.5" fill="#3FA987"/><line x1="955.6" y1="611.9" x2="955.6" y2="709.6" stroke="#3FA987" stroke-width="2"/><rect x="949.6" y="642.5" width="12" height="22.399999999999977" rx="1.5" fill="#3FA987"/><line x1="981.8" y1="568.1" x2="981.8" y2="675.0" stroke="#3FA987" stroke-width="2"/><rect x="975.8" y="596.9" width="12" height="45.60000000000002" rx="1.5" fill="#3FA987"/><line x1="1008.0" y1="540.2" x2="1008.0" y2="639.6" stroke="#3FA987" stroke-width="2"/><rect x="1002.0" y="552.3" width="12" height="44.60000000000002" rx="1.5" fill="#3FA987"/><line x1="1034.2" y1="458.3" x2="1034.2" y2="591.3" stroke="#3FA987" stroke-width="2"/><rect x="1028.2" y="500.1" width="12" height="52.19999999999993" rx="1.5" fill="#3FA987"/><line x1="1060.4" y1="451.5" x2="1060.4" y2="514.0" stroke="#3FA987" stroke-width="2"/><rect x="1054.4" y="476.1" width="12" height="24.0" rx="1.5" fill="#3FA987"/><line x1="1086.5" y1="422.1" x2="1086.5" y2="488.6" stroke="#3FA987" stroke-width="2"/><rect x="1080.5" y="434.5" width="12" height="41.60000000000002" rx="1.5" fill="#3FA987"/><line x1="1112.7" y1="407.7" x2="1112.7" y2="456.9" stroke="#3FA987" stroke-width="2"/><rect x="1106.7" y="423.7" width="12" height="10.800000000000011" rx="1.5" fill="#3FA987"/><line x1="1138.9" y1="413.5" x2="1138.9" y2="439.8" stroke="#C4645E" stroke-width="2"/><rect x="1132.9" y="423.7" width="12" height="2.2" rx="1.5" fill="#C4645E"/><line x1="1165.1" y1="397.9" x2="1165.1" y2="435.3" stroke="#3FA987" stroke-width="2"/><rect x="1159.1" y="421.2" width="12" height="3.0" rx="1.5" fill="#3FA987"/><line x1="1191.3" y1="329.9" x2="1191.3" y2="436.7" stroke="#3FA987" stroke-width="2"/><rect x="1185.3" y="362.3" width="12" height="58.89999999999998" rx="1.5" fill="#3FA987"/><line x1="1217.5" y1="325.6" x2="1217.5" y2="385.6" stroke="#3FA987" stroke-width="2"/><rect x="1211.5" y="348.3" width="12" height="14.0" rx="1.5" fill="#3FA987"/><line x1="1243.6" y1="302.9" x2="1243.6" y2="394.4" stroke="#3FA987" stroke-width="2"/><rect x="1237.6" y="343.8" width="12" height="4.5" rx="1.5" fill="#3FA987"/><line x1="1269.8" y1="286.7" x2="1269.8" y2="357.0" stroke="#3FA987" stroke-width="2"/><rect x="1263.8" y="314.4" width="12" height="29.400000000000034" rx="1.5" fill="#3FA987"/><line x1="1296.0" y1="288.8" x2="1296.0" y2="334.1" stroke="#3FA987" stroke-width="2"/><rect x="1290.0" y="311.3" width="12" height="3.099999999999966" rx="1.5" fill="#3FA987"/><line x1="1322.2" y1="239.7" x2="1322.2" y2="322.3" stroke="#3FA987" stroke-width="2"/><rect x="1316.2" y="255.6" width="12" height="55.70000000000002" rx="1.5" fill="#3FA987"/><line x1="1348.4" y1="161.9" x2="1348.4" y2="271.1" stroke="#3FA987" stroke-width="2"/><rect x="1342.4" y="191.1" width="12" height="64.5" rx="1.5" fill="#3FA987"/><line x1="1374.5" y1="145.0" x2="1374.5" y2="220.4" stroke="#3FA987" stroke-width="2"/><rect x="1368.5" y="156.1" width="12" height="35.0" rx="1.5" fill="#3FA987"/><line x1="1400.7" y1="48.2" x2="1400.7" y2="191.5" stroke="#3FA987" stroke-width="2"/><rect x="1394.7" y="89.6" width="12" height="66.5" rx="1.5" fill="#3FA987"/></g>

  <g opacity=".75">
    <line x1="0" y1="755.4" x2="1440" y2="755.4" stroke="#E0BE86" stroke-width="1.5" stroke-dasharray="11 10"/>
    <line x1="0" y1="156.1" x2="1440" y2="156.1" stroke="#5FBF9C" stroke-width="1.5" stroke-dasharray="11 10"/>
    <line x1="0" y1="887.2" x2="1440" y2="887.2" stroke="#D2736C" stroke-width="1.5" stroke-dasharray="4 12"/>
  </g>

  <rect width="1440" height="900" fill="url(#readGlow)"/>
  <rect width="1440" height="900" fill="url(#edgeFade)"/>
</svg>"""


MT5_QUOTES_SVG = """<svg viewBox="0 0 700 520" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, Segoe UI, Arial, sans-serif">
  <rect width="700" height="520" fill="#F1E8DA" rx="16"/>
  <text x="350" y="32" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">The Quotes Screen</text>
  <text x="350" y="52" text-anchor="middle" font-size="12" fill="#8A7563">Where you add and watch a pair. Tap the magnifier to add EUR/USD.</text>

  <!-- phone -->
  <rect x="205" y="66" width="290" height="430" rx="30" fill="#1C1C1E"/>
  <rect x="212" y="73" width="276" height="416" rx="25" fill="#FFFFFF"/>
  <rect x="300" y="79" width="100" height="18" rx="9" fill="#1C1C1E"/>

  <!-- status bar -->
  <text x="232" y="93" font-size="11" font-weight="600" fill="#1C1C1E">7:37</text>
  <text x="452" y="93" font-size="11" fill="#1C1C1E" text-anchor="end">▮▮▯ ᯤ ▰</text>

  <!-- title row -->
  <text x="350" y="120" text-anchor="middle" font-size="14" font-weight="600" fill="#1C1C1E">Quotes</text>
  <circle cx="240" cy="116" r="12" fill="#F2F2F7"/>
  <text x="240" y="120" text-anchor="middle" font-size="10" fill="#1C1C1E">☰</text>
  <circle cx="437" cy="116" r="12" fill="#F2F2F7"/>
  <text x="437" y="121" text-anchor="middle" font-size="11" fill="#1C1C1E">✎</text>
  <circle cx="464" cy="116" r="12" fill="#F2F2F7"/>
  <text x="464" y="120" text-anchor="middle" font-size="11" fill="#1C1C1E">🔍</text>
  <line x1="212" y1="134" x2="488" y2="134" stroke="#E5E5EA"/>

  <!-- BTCUSD -->
  <text x="228" y="154" font-size="12" font-weight="600" fill="#1C1C1E">BTCUSD</text>
  <text x="228" y="169" font-size="9" fill="#8E8E93">21:37:42  ⊢ 268</text>
  <text x="472" y="154" font-size="14" font-weight="600" fill="#0B63CE" text-anchor="end">72509.29</text>
  <text x="472" y="169" font-size="9" fill="#8E8E93" text-anchor="end">L: 68896.26   H: 72932.51</text>
  <line x1="212" y1="180" x2="488" y2="180" stroke="#E5E5EA"/>

  <!-- XAUUSD, highlighted as the one that matters -->
  <rect x="212" y="180" width="276" height="46" fill="#F2F2F7"/>
  <text x="228" y="200" font-size="12" font-weight="600" fill="#1C1C1E">XAUUSD</text>
  <text x="228" y="215" font-size="9" fill="#8E8E93">21:37:42  ⊢ 19</text>
  <text x="472" y="200" font-size="14" font-weight="600" fill="#0B63CE" text-anchor="end">4522.30</text>
  <text x="472" y="215" font-size="9" fill="#8E8E93" text-anchor="end">L: 4450.58   H: 4540.81</text>
  <line x1="212" y1="226" x2="488" y2="226" stroke="#E5E5EA"/>

  <!-- EURUSD -->
  <text x="228" y="246" font-size="12" font-weight="600" fill="#1C1C1E">EURUSD</text>
  <text x="228" y="261" font-size="9" fill="#8E8E93">21:37:42  ⊢ 4</text>
  <text x="472" y="246" font-size="14" font-weight="600" fill="#0B63CE" text-anchor="end">1.16739</text>
  <text x="472" y="261" font-size="9" fill="#8E8E93" text-anchor="end">L: 1.16689   H: 1.17106</text>
  <line x1="212" y1="272" x2="488" y2="272" stroke="#E5E5EA"/>

  <!-- bottom tab bar -->
  <rect x="212" y="440" width="276" height="49" fill="#F8F8F8"/>
  <line x1="212" y1="440" x2="488" y2="440" stroke="#E5E5EA"/>
  <text x="240" y="463" text-anchor="middle" font-size="13" fill="#0B63CE">↓↑</text>
  <text x="240" y="478" text-anchor="middle" font-size="8" fill="#0B63CE">Quotes</text>
  <text x="295" y="463" text-anchor="middle" font-size="13" fill="#8E8E93">▯▮</text>
  <text x="295" y="478" text-anchor="middle" font-size="8" fill="#8E8E93">Chart</text>
  <text x="350" y="463" text-anchor="middle" font-size="13" fill="#8E8E93">◲</text>
  <text x="350" y="478" text-anchor="middle" font-size="8" fill="#8E8E93">Trade</text>
  <text x="405" y="463" text-anchor="middle" font-size="13" fill="#8E8E93">◷</text>
  <text x="405" y="478" text-anchor="middle" font-size="8" fill="#8E8E93">History</text>
  <text x="460" y="463" text-anchor="middle" font-size="13" fill="#8E8E93">⚙</text>
  <text x="460" y="478" text-anchor="middle" font-size="8" fill="#8E8E93">Settings</text>

  <!-- callout -->
  <text x="520" y="200" font-size="11" fill="#9C7A4E">← the pair, then</text>
  <text x="520" y="216" font-size="11" fill="#9C7A4E">   sell / buy price</text>
  <text x="520" y="120" font-size="11" fill="#9C7A4E">tap 🔍 to add a pair</text>
</svg>"""


MT5_TICKET_SVG = """<svg viewBox="0 0 700 620" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, Segoe UI, Arial, sans-serif">
  <rect width="700" height="620" fill="#F1E8DA" rx="16"/>
  <text x="350" y="32" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">The Trade Ticket</text>
  <text x="350" y="52" text-anchor="middle" font-size="12" fill="#8A7563">Trade tab, then + in the top right. This is the screen you'll use 10 times.</text>

  <!-- phone -->
  <rect x="205" y="66" width="290" height="530" rx="30" fill="#1C1C1E"/>
  <rect x="212" y="73" width="276" height="516" rx="25" fill="#FFFFFF"/>
  <rect x="300" y="79" width="100" height="18" rx="9" fill="#1C1C1E"/>

  <text x="232" y="93" font-size="11" font-weight="600" fill="#1C1C1E">8:50</text>
  <text x="470" y="93" font-size="11" fill="#1C1C1E" text-anchor="end">▮▮▯ ᯤ ▰</text>

  <!-- header -->
  <circle cx="235" cy="118" r="13" fill="#F2F2F7"/>
  <text x="235" y="123" text-anchor="middle" font-size="12" fill="#1C1C1E">‹</text>
  <text x="350" y="115" text-anchor="middle" font-size="13" font-weight="600" fill="#1C1C1E">EURUSD ⌄</text>
  <text x="350" y="130" text-anchor="middle" font-size="9" fill="#8E8E93">Euro vs US Dollar</text>
  <line x1="212" y1="142" x2="488" y2="142" stroke="#E5E5EA"/>

  <!-- execution type -->
  <text x="228" y="162" font-size="11" fill="#1C1C1E">Market Execution</text>
  <text x="472" y="162" font-size="11" fill="#8E8E93" text-anchor="end">⌄</text>
  <line x1="212" y1="174" x2="488" y2="174" stroke="#E5E5EA"/>

  <!-- volume row, the one people get wrong -->
  <rect x="212" y="174" width="276" height="34" fill="#FFF8E8"/>
  <text x="236" y="196" font-size="11" fill="#0B63CE">-0.5</text>
  <text x="283" y="196" font-size="11" fill="#0B63CE">-0.1</text>
  <text x="350" y="197" text-anchor="middle" font-size="14" font-weight="700" fill="#1C1C1E">0.01</text>
  <text x="418" y="196" font-size="11" fill="#0B63CE">+0.1</text>
  <text x="466" y="196" font-size="11" fill="#0B63CE">+0.5</text>
  <line x1="212" y1="208" x2="488" y2="208" stroke="#E5E5EA"/>

  <!-- SL / TP -->
  <text x="228" y="230" font-size="11" fill="#1C1C1E">Stop Loss</text>
  <text x="386" y="230" font-size="12" fill="#0B63CE" text-anchor="middle">−</text>
  <text x="428" y="230" font-size="11" fill="#8E8E93" text-anchor="middle">not set</text>
  <text x="472" y="230" font-size="13" fill="#0B63CE" text-anchor="end">+</text>
  <line x1="212" y1="242" x2="488" y2="242" stroke="#E5E5EA"/>

  <text x="228" y="264" font-size="11" fill="#1C1C1E">Take Profit</text>
  <text x="386" y="264" font-size="12" fill="#0B63CE" text-anchor="middle">−</text>
  <text x="428" y="264" font-size="11" fill="#8E8E93" text-anchor="middle">not set</text>
  <text x="472" y="264" font-size="13" fill="#0B63CE" text-anchor="end">+</text>
  <line x1="212" y1="276" x2="488" y2="276" stroke="#E5E5EA"/>

  <text x="228" y="298" font-size="11" fill="#1C1C1E">Fill Policy</text>
  <text x="472" y="298" font-size="11" fill="#1C1C1E" text-anchor="end">Fill or Kill</text>
  <line x1="212" y1="310" x2="488" y2="310" stroke="#E5E5EA"/>

  <!-- live prices -->
  <rect x="212" y="310" width="276" height="36" fill="#F2F2F7"/>
  <text x="278" y="335" text-anchor="middle" font-size="16" font-weight="700" fill="#0B63CE">1.16739</text>
  <text x="422" y="335" text-anchor="middle" font-size="16" font-weight="700" fill="#0B63CE">1.16743</text>

  <!-- the two action bars -->
  <rect x="212" y="346" width="138" height="38" fill="#D93025"/>
  <text x="281" y="370" text-anchor="middle" font-size="12" font-weight="600" fill="#FFFFFF">Sell by Market</text>
  <rect x="350" y="346" width="138" height="38" fill="#0B63CE"/>
  <text x="419" y="370" text-anchor="middle" font-size="12" font-weight="600" fill="#FFFFFF">Buy by Market</text>

  <text x="350" y="404" text-anchor="middle" font-size="8.5" fill="#8E8E93">Attention! The trade will be executed at market conditions,</text>
  <text x="350" y="416" text-anchor="middle" font-size="8.5" fill="#8E8E93">difference with requested price may be significant!</text>

  <!-- keypad, exactly as it appears -->
  <rect x="212" y="430" width="276" height="159" fill="#F2F2F7"/>
  <g font-size="15" fill="#1C1C1E" text-anchor="middle">
    <rect x="222" y="438" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="263" y="459">1</text>
    <rect x="309" y="438" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="350" y="459">2</text>
    <rect x="396" y="438" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="437" y="459">3</text>
    <rect x="222" y="476" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="263" y="497">4</text>
    <rect x="309" y="476" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="350" y="497">5</text>
    <rect x="396" y="476" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="437" y="497">6</text>
    <rect x="222" y="514" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="263" y="535">7</text>
    <rect x="309" y="514" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="350" y="535">8</text>
    <rect x="396" y="514" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="437" y="535">9</text>
    <rect x="309" y="552" width="82" height="32" rx="5" fill="#FFFFFF"/><text x="350" y="573">0</text>
  </g>

  <!-- callouts -->
  <text x="510" y="190" font-size="11" font-weight="600" fill="#9C7A4E">← set this to 0.01</text>
  <text x="510" y="205" font-size="10" fill="#8A7563">the smallest size</text>
  <text x="510" y="240" font-size="10" fill="#8A7563">← leave both</text>
  <text x="510" y="254" font-size="10" fill="#8A7563">   as "not set"</text>
  <text x="30" y="365" font-size="11" font-weight="600" fill="#9C7A4E">5 sells, then</text>
  <text x="30" y="380" font-size="11" font-weight="600" fill="#9C7A4E">5 buys →</text>
</svg>"""


MT5_CLOSE_SVG = """<svg viewBox="0 0 700 560" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, Segoe UI, Arial, sans-serif">
  <rect width="700" height="560" fill="#F1E8DA" rx="16"/>
  <text x="350" y="32" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">Closing a Trade</text>
  <text x="350" y="52" text-anchor="middle" font-size="12" fill="#8A7563">Trade tab. Press and hold a position, then tap the orange Close banner.</text>

  <rect x="205" y="66" width="290" height="470" rx="30" fill="#1C1C1E"/>
  <rect x="212" y="73" width="276" height="456" rx="25" fill="#FFFFFF"/>
  <rect x="300" y="79" width="100" height="18" rx="9" fill="#1C1C1E"/>

  <text x="232" y="93" font-size="11" font-weight="600" fill="#1C1C1E">8:48</text>
  <text x="470" y="93" font-size="11" fill="#1C1C1E" text-anchor="end">▮▮▯ ᯤ ▰</text>

  <!-- account summary, the top of the Trade tab -->
  <text x="228" y="120" font-size="10.5" fill="#1C1C1E">Balance:</text>
  <text x="472" y="120" font-size="10.5" fill="#1C1C1E" text-anchor="end">10 166.71</text>
  <text x="228" y="138" font-size="10.5" fill="#1C1C1E">Equity:</text>
  <text x="472" y="138" font-size="10.5" fill="#1C1C1E" text-anchor="end">10 303.91</text>
  <text x="228" y="156" font-size="10.5" fill="#1C1C1E">Margin:</text>
  <text x="472" y="156" font-size="10.5" fill="#1C1C1E" text-anchor="end">1 126.41</text>
  <text x="228" y="174" font-size="10.5" fill="#1C1C1E">Free Margin:</text>
  <text x="472" y="174" font-size="10.5" fill="#1C1C1E" text-anchor="end">9 177.50</text>
  <text x="228" y="192" font-size="10.5" fill="#1C1C1E">Margin Level (%):</text>
  <text x="472" y="192" font-size="10.5" fill="#1C1C1E" text-anchor="end">914.76</text>

  <rect x="212" y="202" width="276" height="22" fill="#F2F2F7"/>
  <text x="228" y="217" font-size="10" font-weight="600" fill="#1C1C1E">Positions</text>
  <text x="472" y="217" font-size="10" fill="#8E8E93" text-anchor="end">•••</text>

  <!-- held position showing the action buttons -->
  <rect x="212" y="224" width="276" height="44" fill="#EAF2FD"/>
  <text x="228" y="242" font-size="11" fill="#1C1C1E">EURUSD <tspan fill="#0B63CE" font-weight="600">buy 0.01</tspan></text>
  <text x="228" y="257" font-size="9.5" fill="#8E8E93">1.16739 → 1.16743</text>
  <circle cx="392" cy="246" r="13" fill="#B8C4D4"/><text x="392" y="250" text-anchor="middle" font-size="10" fill="#FFF">•••</text>
  <circle cx="424" cy="246" r="13" fill="#3B3BCF"/><text x="424" y="251" text-anchor="middle" font-size="10" fill="#FFF">✎</text>
  <circle cx="456" cy="246" r="13" fill="#F5A623"/><text x="456" y="251" text-anchor="middle" font-size="11" fill="#FFF">✓</text>
  <line x1="212" y1="268" x2="488" y2="268" stroke="#E5E5EA"/>

  <!-- the orange close banner -->
  <rect x="212" y="268" width="276" height="30" fill="#F5A623"/>
  <text x="350" y="288" text-anchor="middle" font-size="10" font-weight="600" fill="#FFFFFF">Close #10108962629 buy 0.01 by Market</text>

  <text x="228" y="322" font-size="11" fill="#1C1C1E">EURUSD <tspan fill="#0B63CE" font-weight="600">buy 0.01</tspan></text>
  <text x="228" y="337" font-size="9.5" fill="#8E8E93">1.16736 → 1.16743</text>
  <line x1="212" y1="348" x2="488" y2="348" stroke="#E5E5EA"/>

  <text x="228" y="372" font-size="11" fill="#1C1C1E">EURUSD <tspan fill="#D93025" font-weight="600">sell 0.01</tspan></text>
  <text x="228" y="387" font-size="9.5" fill="#8E8E93">1.16741 → 1.16739</text>
  <line x1="212" y1="398" x2="488" y2="398" stroke="#E5E5EA"/>

  <!-- tab bar -->
  <rect x="212" y="480" width="276" height="49" fill="#F8F8F8"/>
  <line x1="212" y1="480" x2="488" y2="480" stroke="#E5E5EA"/>
  <text x="240" y="503" text-anchor="middle" font-size="13" fill="#8E8E93">↓↑</text>
  <text x="240" y="518" text-anchor="middle" font-size="8" fill="#8E8E93">Quotes</text>
  <text x="295" y="503" text-anchor="middle" font-size="13" fill="#8E8E93">▯▮</text>
  <text x="295" y="518" text-anchor="middle" font-size="8" fill="#8E8E93">Chart</text>
  <text x="350" y="503" text-anchor="middle" font-size="13" fill="#0B63CE">◲</text>
  <text x="350" y="518" text-anchor="middle" font-size="8" fill="#0B63CE">Trade</text>
  <text x="405" y="503" text-anchor="middle" font-size="13" fill="#8E8E93">◷</text>
  <text x="405" y="518" text-anchor="middle" font-size="8" fill="#8E8E93">History</text>
  <text x="460" y="503" text-anchor="middle" font-size="13" fill="#8E8E93">⚙</text>
  <text x="460" y="518" text-anchor="middle" font-size="8" fill="#8E8E93">Settings</text>

  <text x="512" y="246" font-size="10" fill="#9C7A4E">press and hold</text>
  <text x="512" y="260" font-size="10" fill="#9C7A4E">a position →</text>
  <text x="512" y="288" font-size="10" font-weight="600" fill="#9C7A4E">then tap the</text>
  <text x="512" y="302" font-size="10" font-weight="600" fill="#9C7A4E">orange banner</text>
</svg>"""


MT5_HISTORY_SVG = """<svg viewBox="0 0 700 540" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, Segoe UI, Arial, sans-serif">
  <rect width="700" height="540" fill="#F1E8DA" rx="16"/>
  <text x="350" y="32" text-anchor="middle" font-size="17" font-weight="bold" fill="#3B2E26">The History Screen</text>
  <text x="350" y="52" text-anchor="middle" font-size="12" fill="#8A7563">This is the screenshot we need: all 10 closed trades in one shot.</text>

  <rect x="205" y="66" width="290" height="450" rx="30" fill="#1C1C1E"/>
  <rect x="212" y="73" width="276" height="436" rx="25" fill="#FFFFFF"/>
  <rect x="300" y="79" width="100" height="18" rx="9" fill="#1C1C1E"/>

  <text x="232" y="93" font-size="11" font-weight="600" fill="#1C1C1E">7:38</text>
  <text x="470" y="93" font-size="11" fill="#1C1C1E" text-anchor="end">▮▮▯ ᯤ ▰</text>

  <!-- Positions / Orders / Deals -->
  <rect x="250" y="106" width="180" height="24" rx="12" fill="#F2F2F7"/>
  <rect x="250" y="106" width="60" height="24" rx="12" fill="#FFFFFF" stroke="#E5E5EA"/>
  <text x="280" y="122" text-anchor="middle" font-size="10" font-weight="600" fill="#1C1C1E">Positions</text>
  <text x="340" y="122" text-anchor="middle" font-size="10" fill="#8E8E93">Orders</text>
  <text x="400" y="122" text-anchor="middle" font-size="10" fill="#8E8E93">Deals</text>
  <circle cx="455" cy="118" r="12" fill="#F2F2F7"/>
  <text x="455" y="122" text-anchor="middle" font-size="10" fill="#1C1C1E">◷</text>
  <line x1="212" y1="140" x2="488" y2="140" stroke="#E5E5EA"/>

  <g font-size="10.5">
    <text x="228" y="160" fill="#1C1C1E">EURUSD <tspan fill="#D93025" font-weight="600">sell 0.01</tspan></text>
    <text x="228" y="173" font-size="9" fill="#8E8E93">1.16741 → 1.16739</text>
    <text x="472" y="160" font-size="12" font-weight="600" fill="#0B63CE" text-anchor="end">0.02</text>
    <line x1="212" y1="182" x2="488" y2="182" stroke="#E5E5EA"/>

    <text x="228" y="202" fill="#1C1C1E">EURUSD <tspan fill="#D93025" font-weight="600">sell 0.01</tspan></text>
    <text x="228" y="215" font-size="9" fill="#8E8E93">1.16744 → 1.16740</text>
    <text x="472" y="202" font-size="12" font-weight="600" fill="#0B63CE" text-anchor="end">0.04</text>
    <line x1="212" y1="224" x2="488" y2="224" stroke="#E5E5EA"/>

    <text x="228" y="244" fill="#1C1C1E">EURUSD <tspan fill="#D93025" font-weight="600">sell 0.01</tspan></text>
    <text x="228" y="257" font-size="9" fill="#8E8E93">1.16739 → 1.16742</text>
    <text x="472" y="244" font-size="12" font-weight="600" fill="#D93025" text-anchor="end">-0.03</text>
    <line x1="212" y1="266" x2="488" y2="266" stroke="#E5E5EA"/>

    <text x="228" y="286" fill="#1C1C1E">EURUSD <tspan fill="#0B63CE" font-weight="600">buy 0.01</tspan></text>
    <text x="228" y="299" font-size="9" fill="#8E8E93">1.16736 → 1.16743</text>
    <text x="472" y="286" font-size="12" font-weight="600" fill="#0B63CE" text-anchor="end">0.07</text>
    <line x1="212" y1="308" x2="488" y2="308" stroke="#E5E5EA"/>

    <text x="228" y="328" fill="#1C1C1E">EURUSD <tspan fill="#0B63CE" font-weight="600">buy 0.01</tspan></text>
    <text x="228" y="341" font-size="9" fill="#8E8E93">1.16739 → 1.16741</text>
    <text x="472" y="328" font-size="12" font-weight="600" fill="#0B63CE" text-anchor="end">0.02</text>
    <line x1="212" y1="350" x2="488" y2="350" stroke="#E5E5EA"/>

    <text x="228" y="370" fill="#1C1C1E">EURUSD <tspan fill="#0B63CE" font-weight="600">buy 0.01</tspan></text>
    <text x="228" y="383" font-size="9" fill="#8E8E93">1.16740 → 1.16744</text>
    <text x="472" y="370" font-size="12" font-weight="600" fill="#0B63CE" text-anchor="end">0.04</text>
    <line x1="212" y1="392" x2="488" y2="392" stroke="#E5E5EA"/>
  </g>

  <text x="350" y="412" text-anchor="middle" font-size="9" fill="#8E8E93">and so on, all 10</text>

  <rect x="212" y="460" width="276" height="49" fill="#F8F8F8"/>
  <line x1="212" y1="460" x2="488" y2="460" stroke="#E5E5EA"/>
  <text x="240" y="483" text-anchor="middle" font-size="13" fill="#8E8E93">↓↑</text>
  <text x="240" y="498" text-anchor="middle" font-size="8" fill="#8E8E93">Quotes</text>
  <text x="295" y="483" text-anchor="middle" font-size="13" fill="#8E8E93">▯▮</text>
  <text x="295" y="498" text-anchor="middle" font-size="8" fill="#8E8E93">Chart</text>
  <text x="350" y="483" text-anchor="middle" font-size="13" fill="#8E8E93">◲</text>
  <text x="350" y="498" text-anchor="middle" font-size="8" fill="#8E8E93">Trade</text>
  <text x="405" y="483" text-anchor="middle" font-size="13" fill="#0B63CE">◷</text>
  <text x="405" y="498" text-anchor="middle" font-size="8" fill="#0B63CE">History</text>
  <text x="460" y="483" text-anchor="middle" font-size="13" fill="#8E8E93">⚙</text>
  <text x="460" y="498" text-anchor="middle" font-size="8" fill="#8E8E93">Settings</text>

  <text x="512" y="118" font-size="10" font-weight="600" fill="#9C7A4E">← tap History,</text>
  <text x="512" y="132" font-size="10" fill="#8A7563">   then Positions</text>
  <text x="30" y="240" font-size="10" fill="#9C7A4E">profit or loss</text>
  <text x="30" y="254" font-size="10" fill="#9C7A4E">on each trade →</text>
</svg>"""


# names the pages already use
TICKET_SVG = MT5_TICKET_SVG
CLOSE_SVG = MT5_CLOSE_SVG
QUOTES_SVG = MT5_QUOTES_SVG
HISTORY_SVG = MT5_HISTORY_SVG


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
    # 1. Bulls vs bears. Drawn as real candles rather than coloured blocks,
    #    because the point is to recognise the shape on a chart.
    1: """<svg viewBox="0 0 700 400" xmlns="http://www.w3.org/2000/svg" font-family="Inter, Arial, sans-serif">
  <rect width="700" height="400" fill="#F1E8DA" rx="16"/>
  <text x="350" y="36" text-anchor="middle" font-size="18" font-weight="bold" fill="#3B2E26">Bulls vs Bears</text>
  <text x="350" y="58" text-anchor="middle" font-size="12.5" fill="#8A7563">Who is winning right now, buyers or sellers</text>

  <rect x="46" y="84" width="290" height="272" rx="12" fill="#FBF7F1" stroke="#DCCDBA"/>
  <text x="191" y="112" text-anchor="middle" font-size="13" font-weight="700" fill="#4B7A5E" letter-spacing="1.5">BULLISH</text>
  <text x="191" y="132" text-anchor="middle" font-size="11.5" fill="#8A7563">Buyers in control, price pushing up</text>
  <g>
    <line x1="130" y1="300" x2="130" y2="182" stroke="#5B7A5E" stroke-width="2.4"/>
    <rect x="120" y="200" width="20" height="82" rx="2" fill="#5B7A5E"/>
    <line x1="175" y1="276" x2="175" y2="158" stroke="#5B7A5E" stroke-width="2.4"/>
    <rect x="165" y="176" width="20" height="82" rx="2" fill="#5B7A5E"/>
    <line x1="220" y1="252" x2="220" y2="134" stroke="#5B7A5E" stroke-width="2.4"/>
    <rect x="210" y="152" width="20" height="82" rx="2" fill="#5B7A5E"/>
  </g>
  <path d="M 258 268 L 292 176" stroke="#5B7A5E" stroke-width="2.4" stroke-linecap="round"/>
  <path d="M 292 176 l -11 3 l 6 8 z" fill="#5B7A5E"/>
  <text x="191" y="332" text-anchor="middle" font-size="13.5" font-weight="700" fill="#3B2E26">You would BUY</text>

  <rect x="364" y="84" width="290" height="272" rx="12" fill="#FBF7F1" stroke="#DCCDBA"/>
  <text x="509" y="112" text-anchor="middle" font-size="13" font-weight="700" fill="#9C5B52" letter-spacing="1.5">BEARISH</text>
  <text x="509" y="132" text-anchor="middle" font-size="11.5" fill="#8A7563">Sellers in control, price pushing down</text>
  <g>
    <line x1="448" y1="140" x2="448" y2="258" stroke="#9C5B52" stroke-width="2.4"/>
    <rect x="438" y="158" width="20" height="82" rx="2" fill="#9C5B52"/>
    <line x1="493" y1="164" x2="493" y2="282" stroke="#9C5B52" stroke-width="2.4"/>
    <rect x="483" y="182" width="20" height="82" rx="2" fill="#9C5B52"/>
    <line x1="538" y1="188" x2="538" y2="306" stroke="#9C5B52" stroke-width="2.4"/>
    <rect x="528" y="206" width="20" height="82" rx="2" fill="#9C5B52"/>
  </g>
  <path d="M 576 172 L 610 264" stroke="#9C5B52" stroke-width="2.4" stroke-linecap="round"/>
  <path d="M 610 264 l -5 -10 l -9 4 z" fill="#9C5B52"/>
  <text x="509" y="332" text-anchor="middle" font-size="13.5" font-weight="700" fill="#3B2E26">You would SELL</text>
</svg>""",

    # 2. TP and SL, drawn against a price line so the levels sit where they
    #    actually sit relative to entry.
    2: """<svg viewBox="0 0 700 420" xmlns="http://www.w3.org/2000/svg" font-family="Inter, Arial, sans-serif">
  <rect width="700" height="420" fill="#F1E8DA" rx="16"/>
  <text x="350" y="36" text-anchor="middle" font-size="18" font-weight="bold" fill="#3B2E26">Take Profit &amp; Stop Loss</text>
  <text x="350" y="58" text-anchor="middle" font-size="12.5" fill="#8A7563">Set both when you open the trade, not after</text>

  <line x1="70" y1="112" x2="640" y2="112" stroke="#5B7A5E" stroke-width="1.6" stroke-dasharray="7 6"/>
  <text x="70" y="102" font-size="12" font-weight="700" fill="#5B7A5E">TAKE PROFIT</text>
  <text x="640" y="102" font-size="11.5" fill="#5B7A5E" text-anchor="end">closes in profit, automatically</text>

  <line x1="70" y1="240" x2="640" y2="240" stroke="#9C7A4E" stroke-width="1.6"/>
  <text x="70" y="230" font-size="12" font-weight="700" fill="#9C7A4E">ENTRY</text>
  <text x="640" y="230" font-size="11.5" fill="#9C7A4E" text-anchor="end">the price you got in at</text>

  <line x1="70" y1="340" x2="640" y2="340" stroke="#9C5B52" stroke-width="1.6" stroke-dasharray="7 6"/>
  <text x="70" y="330" font-size="12" font-weight="700" fill="#9C5B52">STOP LOSS</text>
  <text x="640" y="330" font-size="11.5" fill="#9C5B52" text-anchor="end">closes the loss before it grows</text>

  <polyline points="150,240 190,228 230,246 270,214 310,226 350,190 390,204 430,168 470,150 510,124 550,116"
            fill="none" stroke="#3B2E26" stroke-width="2.2" stroke-linejoin="round" opacity=".65"/>
  <circle cx="150" cy="240" r="5" fill="#9C7A4E"/>
  <circle cx="550" cy="116" r="5" fill="#5B7A5E"/>

  <path d="M 596 240 L 596 122" stroke="#5B7A5E" stroke-width="1.6"/>
  <path d="M 596 118 l -4 10 l 8 0 z" fill="#5B7A5E"/>
  <text x="606" y="185" font-size="11.5" fill="#5B7A5E">your win</text>

  <path d="M 596 250 L 596 334" stroke="#9C5B52" stroke-width="1.6"/>
  <path d="M 596 338 l -4 -10 l 8 0 z" fill="#9C5B52"/>
  <text x="606" y="296" font-size="11.5" fill="#9C5B52">your risk</text>

  <text x="350" y="392" text-anchor="middle" font-size="12.5" fill="#8A7563">Both are set in MT5 before you press Buy or Sell. The trade then manages itself.</text>
</svg>""",

    # 3. Lot size. The maths shown is for a standard FX pair, which is what
    #    the activation trades use, so the numbers match what people will see.
    # 3. Lot size as a risk range, not a fixed number. Sizing follows the
    #    risk percentage and the stop distance, which is why it's a range.
    3: """<svg viewBox="0 0 700 470" xmlns="http://www.w3.org/2000/svg" font-family="Inter, Arial, sans-serif">
  <rect width="700" height="470" fill="#F1E8DA" rx="16"/>
  <text x="350" y="34" text-anchor="middle" font-size="18" font-weight="bold" fill="#3B2E26">Lot Size by Account</text>
  <text x="350" y="56" text-anchor="middle" font-size="12.5" fill="#8A7563">Risk 1% to 3% of your account per trade. Smaller accounts sit nearer 3%, larger nearer 1%.</text>

  <line x1="48" y1="76" x2="652" y2="76" stroke="#DCCDBA"/>
  <text x="62"  y="96" font-size="11.5" font-weight="700" fill="#9C7A4E">ACCOUNT</text>
  <text x="250" y="96" font-size="11.5" font-weight="700" fill="#9C7A4E">RISK PER TRADE</text>
  <text x="452" y="96" font-size="11.5" font-weight="700" fill="#9C7A4E">TYPICAL LOT SIZE</text>
  <line x1="48" y1="106" x2="652" y2="106" stroke="#DCCDBA"/>

  <g font-size="13.5" fill="#3B2E26" font-family="IBM Plex Mono, monospace">
    <text x="62" y="130">£300</text>      <text x="250" y="130" fill="#8A7563">£3 - £9</text>       <text x="452" y="130" fill="#5B7A5E">0.01 - 0.02</text>
    <text x="62" y="158">£500</text>      <text x="250" y="158" fill="#8A7563">£5 - £15</text>      <text x="452" y="158" fill="#5B7A5E">0.01 - 0.03</text>
    <text x="62" y="186">£1,000</text>    <text x="250" y="186" fill="#8A7563">£10 - £30</text>     <text x="452" y="186" fill="#5B7A5E">0.02 - 0.06</text>
    <text x="62" y="214">£2,500</text>    <text x="250" y="214" fill="#8A7563">£25 - £75</text>     <text x="452" y="214" fill="#5B7A5E">0.05 - 0.15</text>
    <text x="62" y="242">£5,000</text>    <text x="250" y="242" fill="#8A7563">£50 - £150</text>    <text x="452" y="242" fill="#5B7A5E">0.10 - 0.30</text>
    <text x="62" y="270">£10,000</text>   <text x="250" y="270" fill="#8A7563">£100 - £300</text>   <text x="452" y="270" fill="#5B7A5E">0.20 - 0.60</text>
    <text x="62" y="298">£25,000</text>   <text x="250" y="298" fill="#8A7563">£250 - £750</text>   <text x="452" y="298" fill="#5B7A5E">0.50 - 1.50</text>
    <text x="62" y="326">£50,000</text>   <text x="250" y="326" fill="#8A7563">£500 - £1,500</text> <text x="452" y="326" fill="#5B7A5E">1.00 - 3.00</text>
    <text x="62" y="354">£100,000</text>  <text x="250" y="354" fill="#8A7563">£1,000 - £3,000</text><text x="452" y="354" fill="#5B7A5E">2.00 - 6.00</text>
  </g>
  <line x1="48" y1="372" x2="652" y2="372" stroke="#DCCDBA"/>

  <text x="62" y="398" font-size="12.5" font-weight="700" fill="#3B2E26">The stop decides the size, not the account.</text>
  <text x="62" y="418" font-size="12" fill="#8A7563">These assume a stop around $5 away, which is typical on our gold signals. A wider stop means</text>
  <text x="62" y="436" font-size="12" fill="#8A7563">a smaller lot for the same risk. Double the stop distance, halve the lot size.</text>
  <text x="62" y="458" font-size="11.5" fill="#9C5B52">Guidance for learning, not financial advice. Only ever risk what you are happy to lose.</text>
</svg>""",

    # 4. Bid, ask and spread, with the spread actually worked out.
    4: """<svg viewBox="0 0 700 360" xmlns="http://www.w3.org/2000/svg" font-family="Inter, Arial, sans-serif">
  <rect width="700" height="360" fill="#F1E8DA" rx="16"/>
  <text x="350" y="36" text-anchor="middle" font-size="18" font-weight="bold" fill="#3B2E26">Bid, Ask &amp; Spread</text>
  <text x="350" y="58" text-anchor="middle" font-size="12.5" fill="#8A7563">Two prices at once, and why a trade opens slightly down</text>

  <rect x="60" y="96" width="250" height="126" rx="12" fill="#FBF7F1" stroke="#9C5B52" stroke-width="1.5"/>
  <text x="185" y="126" text-anchor="middle" font-size="12.5" font-weight="700" fill="#9C5B52" letter-spacing="1.6">BID</text>
  <text x="185" y="168" text-anchor="middle" font-size="27" font-weight="700" fill="#3B2E26" font-family="IBM Plex Mono, monospace">1.08492</text>
  <text x="185" y="196" text-anchor="middle" font-size="12.5" fill="#8A7563">the price you SELL at</text>

  <rect x="390" y="96" width="250" height="126" rx="12" fill="#FBF7F1" stroke="#5B7A5E" stroke-width="1.5"/>
  <text x="515" y="126" text-anchor="middle" font-size="12.5" font-weight="700" fill="#5B7A5E" letter-spacing="1.6">ASK</text>
  <text x="515" y="168" text-anchor="middle" font-size="27" font-weight="700" fill="#3B2E26" font-family="IBM Plex Mono, monospace">1.08508</text>
  <text x="515" y="196" text-anchor="middle" font-size="12.5" fill="#8A7563">the price you BUY at</text>

  <line x1="316" y1="159" x2="384" y2="159" stroke="#9C7A4E" stroke-width="1.6"/>
  <text x="350" y="150" text-anchor="middle" font-size="11.5" font-weight="700" fill="#9C7A4E">SPREAD</text>
  <text x="350" y="178" text-anchor="middle" font-size="12" fill="#9C7A4E">1.6 pips</text>

  <text x="350" y="266" text-anchor="middle" font-size="13" fill="#3B2E26">Buy at the ask, sell at the bid. The gap is how the broker gets paid.</text>
  <text x="350" y="292" text-anchor="middle" font-size="12.5" fill="#8A7563">That gap is why a new trade shows a small loss the second you open it.</text>
  <text x="350" y="316" text-anchor="middle" font-size="12.5" fill="#8A7563">It widens around news, which is when spreads cost you most.</text>
</svg>""",

    # 5. Candle anatomy, with open, high, low and close named. The earlier
    #    version left those out, which are the four things a candle is.
    5: """<svg viewBox="0 0 700 420" xmlns="http://www.w3.org/2000/svg" font-family="Inter, Arial, sans-serif">
  <rect width="700" height="420" fill="#F1E8DA" rx="16"/>
  <text x="350" y="36" text-anchor="middle" font-size="18" font-weight="bold" fill="#3B2E26">What a Candle Shows You</text>
  <text x="350" y="58" text-anchor="middle" font-size="12.5" fill="#8A7563">Four prices in one shape: open, high, low and close</text>

  <g>
    <text x="180" y="96" text-anchor="middle" font-size="13" font-weight="700" fill="#5B7A5E" letter-spacing="1.4">PRICE WENT UP</text>
    <line x1="180" y1="118" x2="180" y2="152" stroke="#5B7A5E" stroke-width="3"/>
    <rect x="156" y="152" width="48" height="140" rx="3" fill="#5B7A5E"/>
    <line x1="180" y1="292" x2="180" y2="330" stroke="#5B7A5E" stroke-width="3"/>
    <line x1="212" y1="118" x2="272" y2="118" stroke="#8A7563" stroke-width="1"/>
    <text x="278" y="122" font-size="12" fill="#3B2E26">High</text>
    <line x1="212" y1="152" x2="272" y2="152" stroke="#8A7563" stroke-width="1"/>
    <text x="278" y="156" font-size="12" fill="#3B2E26">Close</text>
    <line x1="212" y1="292" x2="272" y2="292" stroke="#8A7563" stroke-width="1"/>
    <text x="278" y="296" font-size="12" fill="#3B2E26">Open</text>
    <line x1="212" y1="330" x2="272" y2="330" stroke="#8A7563" stroke-width="1"/>
    <text x="278" y="334" font-size="12" fill="#3B2E26">Low</text>
    <text x="180" y="360" text-anchor="middle" font-size="12" fill="#8A7563">closed above where it opened</text>
  </g>

  <g>
    <text x="470" y="96" text-anchor="middle" font-size="13" font-weight="700" fill="#9C5B52" letter-spacing="1.4">PRICE WENT DOWN</text>
    <line x1="470" y1="118" x2="470" y2="152" stroke="#9C5B52" stroke-width="3"/>
    <rect x="446" y="152" width="48" height="140" rx="3" fill="#9C5B52"/>
    <line x1="470" y1="292" x2="470" y2="330" stroke="#9C5B52" stroke-width="3"/>
    <line x1="502" y1="118" x2="562" y2="118" stroke="#8A7563" stroke-width="1"/>
    <text x="568" y="122" font-size="12" fill="#3B2E26">High</text>
    <line x1="502" y1="152" x2="562" y2="152" stroke="#8A7563" stroke-width="1"/>
    <text x="568" y="156" font-size="12" fill="#3B2E26">Open</text>
    <line x1="502" y1="292" x2="562" y2="292" stroke="#8A7563" stroke-width="1"/>
    <text x="568" y="296" font-size="12" fill="#3B2E26">Close</text>
    <line x1="502" y1="330" x2="562" y2="330" stroke="#8A7563" stroke-width="1"/>
    <text x="568" y="334" font-size="12" fill="#3B2E26">Low</text>
    <text x="470" y="360" text-anchor="middle" font-size="12" fill="#8A7563">closed below where it opened</text>
  </g>

  <text x="350" y="396" text-anchor="middle" font-size="12.5" fill="#8A7563">The thick part is the body, the thin lines are wicks. One candle covers one slice of time.</text>
</svg>""",
}


# ---------------------------------------------------------------------------
# DESIGN TOKENS / BASE CSS
# ---------------------------------------------------------------------------

BASE_CSS = """
:root {
  --bg: #12100E;
  --bg-alt: #1B1712;
  --bg-alt-2: #241E18;
  --ink: #F5F0E8;
  --ink-dim: rgba(245,240,232,.66);

  /* The reading surface. Long-form content sits on this, dark on light,
     because nobody wants to read 41 lessons in white-on-black. */
  --paper: #FAF6F0;
  --paper-alt: #F1E8DA;
  --paper-ink: #3B2E26;
  --paper-ink-dim: #8A7563;
  --paper-line: #E4D6C3;
  --gold: #C4A272;
  --gold-bright: #B08F5E;
  --rose: #D8B4AD;
  --green: #5B7A5E;
  --red: #9C5B52;
  --line: rgba(217,184,124,.20);
  --teal: #3FA987;
  --teal-deep: #2E7F68;
  --rose-pop: #C4645E;
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
  background: rgba(18,16,14,0.88);
  backdrop-filter: blur(10px);
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
  color: var(--ink);
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
.nav-cta:hover { background: var(--gold); color: #12100E; }

/* Hero */
.hero {
  padding: 120px 0 100px;
  border-bottom: 1px solid var(--line);
}

.her-hero-dark {
  position: relative; overflow: hidden; isolation: isolate;
  border-bottom: 1px solid rgba(240,184,196,.2);
}
.her-art { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 0; }
.her-hero-dark .wrap {
  position: relative; z-index: 1; max-width: 780px; text-align: center;
  padding-top: 96px; padding-bottom: 90px;
}
.her-hero-dark h1 {
  font-size: clamp(36px, 6.4vw, 66px); line-height: 1.04; letter-spacing: -.02em;
  color: #FDF4F6; margin: 24px 0 20px; text-wrap: balance;
}
.her-hero-dark h1 em { font-style: italic; color: #F0B8C4; }
.her-hero-dark .lede {
  font-size: 17.5px; line-height: 1.75; color: rgba(247,234,238,.76);
  max-width: 560px; margin: 0 auto;
}
.her-tag {
  color: #F0B8C4 !important;
  border-color: rgba(240,184,196,.4) !important;
  background: rgba(26,12,19,.6) !important;
}
@media (max-width: 640px) {
  .her-hero-dark .wrap { padding-top: 66px; padding-bottom: 60px; }
}

/* ---------- Dark hero ----------
   The market itself as the opening image. Warm near-black rather than blue,
   gold rather than neon, so it reads expensive instead of crypto. */

.hero-dark {
  position: relative;
  background: #0E0A08;
  overflow: hidden;
  border-bottom: 1px solid rgba(176,143,94,.22);
  isolation: isolate;
}
.hero-chart {
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  z-index: 0;
}
.hero-candles {
  opacity: 0;
  animation: candlesIn 1.5s cubic-bezier(.22,.7,.3,1) .15s forwards;
}
@keyframes candlesIn {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: translateY(0); }
}
.hero-dark .wrap {
  position: relative; z-index: 1;
  padding-top: 54px; padding-bottom: 58px;
}
.hero-dark .wrap { max-width: 900px; }
.hero-copy { text-align: center; }
.hero-dark .lede { margin-left: auto; margin-right: auto; }
.hero-dark .cta-row { justify-content: center; }

/* The product itself, on the page. Not a stat block: the actual shape of
   what lands in the group, so the promise and the proof are the same thing. */
.signal-showcase {
  display: grid; grid-template-columns: 0.86fr 1.14fr;
  gap: 46px; align-items: center; margin-top: 44px;
}
.signal-explain h3 { font-size: 24px; margin: 0 0 14px; }
.signal-explain p {
  color: var(--ink-dim); font-size: 15.5px; line-height: 1.8; margin: 0 0 14px;
}
.signal-explain em { color: var(--gold); font-style: italic; }
@media (max-width: 860px) {
  .signal-showcase { grid-template-columns: 1fr; gap: 30px; }
}

.signal-card {
  background: #1C140E;
  border: 1px solid rgba(217,184,124,.32);
  border-radius: 18px;
  padding: 26px 28px 24px;
  backdrop-filter: blur(14px);
  box-shadow: 0 26px 60px rgba(0,0,0,.45);
}
.signal-head {
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 14px; margin-bottom: 16px;
  border-bottom: 1px solid rgba(217,184,124,.2);
}
.signal-live {
  display: inline-flex; align-items: center; gap: 8px;
  font-family: 'IBM Plex Mono', monospace; font-size: 10.5px;
  letter-spacing: .14em; text-transform: uppercase; color: rgba(250,246,240,.6);
}
.signal-live i {
  width: 7px; height: 7px; border-radius: 50%; background: #7FB07F;
  box-shadow: 0 0 0 3px rgba(127,176,127,.18);
}
.signal-pair {
  font-family: 'IBM Plex Mono', monospace; font-size: 13px;
  letter-spacing: .1em; color: #E0BE86;
}
.signal-dir {
  font-family: 'Fraunces', serif; font-size: 42px; line-height: 1;
  color: #FAF6F0; margin-bottom: 18px; letter-spacing: -.01em;
}
.signal-levels { margin: 0 0 16px; }
.signal-levels > div {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 9px 0; border-bottom: 1px solid rgba(250,246,240,.08);
}
.signal-levels > div:last-child { border-bottom: none; }
.signal-levels dt { font-size: 13px; color: rgba(250,246,240,.6); margin: 0; }
.signal-levels dd {
  margin: 0; font-family: 'IBM Plex Mono', monospace; font-size: 14.5px;
  color: #FAF6F0; letter-spacing: .02em;
}
.signal-levels dd.is-stop { color: #C98B82; }
.signal-levels dd.is-tp { color: #9FC29F; }
.signal-note {
  font-size: 12.5px; line-height: 1.65; color: rgba(250,246,240,.52);
  margin: 0; padding-top: 14px; border-top: 1px solid rgba(217,184,124,.16);
}

@media (max-width: 860px) {
  .signal-card { max-width: 420px; }
}
.hero-tag {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11.5px; letter-spacing: .18em; text-transform: uppercase;
  color: #E0BE86;
  border: 1px solid rgba(217,184,124,.45);
  padding: 8px 18px; border-radius: 999px;
  background: rgba(21,15,11,.55);
  backdrop-filter: blur(6px);
}
.hero-dark h1 {
  font-size: clamp(38px, 5.6vw, 68px);
  line-height: 1.02;
  letter-spacing: -.022em;
  color: #FAF6F0;
  margin: 26px 0 22px;
  text-wrap: balance;
}
.hero-dark h1 em {
  font-style: italic;
  color: #E0BE86;
}
.hero-dark .lede {
  font-size: 17.5px; line-height: 1.75;
  color: rgba(250,246,240,.76);
  max-width: 520px; margin: 0 0 14px;
}
.hero-dark .lede-rose { color: var(--rose); }
.hero-dark .cta-row { margin-top: 32px; flex-wrap: wrap; }
.hero-dark .btn-primary {
  background: #D9B87C; border-color: #D9B87C; color: #150F0B; font-weight: 700;
}
.hero-dark .btn-primary:hover { background: #E8CB96; border-color: #E8CB96; }
.hero-dark .btn-ghost {
  border-color: rgba(250,246,240,.34); color: #FAF6F0; background: transparent;
}
.hero-dark .btn-ghost:hover { border-color: #D9B87C; color: #D9B87C; }

/* the strip of live-looking numbers under the fold line */
.hero-ticker {
  position: relative; z-index: 1;
  background: #0E0A08;
  border-top: 1px solid rgba(176,143,94,.18);
}
.hero-ticker span { display: inline-flex; align-items: center; gap: 9px; }
.hero-ticker span::before {
  content: ""; width: 5px; height: 5px; border-radius: 50%; background: var(--teal);
}
.hero-ticker span:nth-child(2)::before { background: #D9B87C; }
.hero-ticker span:nth-child(3)::before { background: var(--gold-bright); }
.hero-ticker span:nth-child(4)::before { background: var(--rose); }
.hero-ticker .wrap {
  display: flex; flex-wrap: wrap; gap: 34px; justify-content: center;
  padding-top: 18px; padding-bottom: 18px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; letter-spacing: .1em; text-transform: uppercase;
  color: rgba(250,246,240,.5);
}
.hero-ticker b { color: #D9B87C; font-weight: 600; }

@media (prefers-reduced-motion: reduce) {
  .hero-candles { animation: none; opacity: 1; }
}
@media (max-width: 640px) {
  .hero-dark .wrap { padding-top: 78px; padding-bottom: 70px; }
  .hero-ticker .wrap { gap: 18px; font-size: 11px; }
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
.btn-primary { background: var(--gold); color: #12100E; }
.btn-primary:hover { background: var(--gold-bright); transform: translateY(-1px); }
.btn-ghost { border-color: rgba(245,240,232,.30); color: var(--ink); }
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
  background-image:
    radial-gradient(60% 120% at 12% 100%, rgba(63,169,135,.16), transparent 70%),
    radial-gradient(55% 120% at 88% 0%, rgba(196,100,94,.14), transparent 70%);
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
/* Five cards on a four-across grid leaves a lone orphan on the second row.
   Three across gives a settled 3 + 2 instead. */
.grid5.of-five { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) { .grid5.of-five { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .grid5.of-five { grid-template-columns: 1fr; } }
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
.step:nth-child(1) .num { color: var(--teal); }
.step:nth-child(2) .num { color: var(--gold); }
.step:nth-child(3) .num { color: var(--rose-pop); }
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
  background: var(--gold);
  color: #12100E;
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
  background:
    radial-gradient(70% 90% at 50% 0%, rgba(232,155,174,.18), transparent 70%),
    linear-gradient(168deg, #4A1F33 0%, #33162473 46%, var(--bg) 100%);
  border-bottom: 1px solid rgba(240,184,196,.22);
}
.fw-mark {
  width: 168px; height: 168px; margin: 0 auto 28px; display: block;
  color: #F0B8C4;
  filter: drop-shadow(0 6px 20px rgba(0,0,0,.35));
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
  background: transparent;
}
.her-mark::before {
  content: "";
  position: absolute; inset: 7px;
  border-radius: 50%;
  border: 1px solid rgba(201,162,155,0.35);
}
.her-panel {
  background: linear-gradient(160deg, #331826, var(--bg-alt));
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
  background: #26121C;
  border: 1px solid rgba(240,184,196,.20);
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
  background: linear-gradient(160deg, #33182699, #26121C);
  border: 1px solid rgba(240,184,196,.22);
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
  font-size: 12.5px;
  line-height: 1.6;
  color: rgba(247,234,238,.7);
  display: block;
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
  background: var(--gold);
  color: #12100E;
  padding: 14px 22px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 600;
  box-shadow: 0 12px 30px rgba(0,0,0,0.45);
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

.adm-shell { min-height: 100vh; background: var(--bg); }

.adm-top { background: var(--dark); border-bottom: 1px solid rgba(255,255,255,.08); }
.adm-top .wrap {
  display: flex; align-items: center; justify-content: space-between;
  padding-top: 16px; padding-bottom: 16px; gap: 16px; flex-wrap: wrap;
}
.adm-brand {
  font-family: 'Fraunces', serif; font-size: 19px; letter-spacing: .12em;
  color: #fff; text-decoration: none;
}
.adm-brand span {
  font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: .18em;
  text-transform: uppercase; color: var(--gold-bright); margin-left: 8px;
}
.adm-top-right { display: flex; gap: 18px; align-items: center; }
.adm-top-right a { color: rgba(255,255,255,.72); text-decoration: none; font-size: 13px; }
.adm-top-right a:hover { color: #fff; }
.adm-top-right .adm-out { color: var(--gold-bright); font-weight: 600; }

.adm-nav {
  background: var(--dark-alt); overflow-x: auto; position: sticky; top: 0; z-index: 40;
}
.adm-nav::-webkit-scrollbar { display: none; }
.adm-nav .wrap {
  display: flex; gap: 2px; padding-top: 0; padding-bottom: 0; white-space: nowrap;
}
.adm-nav a {
  color: rgba(255,255,255,.66); text-decoration: none; font-size: 13.5px; font-weight: 500;
  padding: 14px 18px; border-bottom: 2px solid transparent;
}
.adm-nav a:hover { color: #fff; }
.adm-nav a.on { color: var(--gold-bright); border-bottom-color: var(--gold-bright); }

.adm-nav-site { background: #17130F; border-top: 1px solid rgba(255,255,255,.06); }
.adm-nav-site .wrap { align-items: center; }
.adm-nav-site a { font-size: 12.5px; padding: 10px 13px; color: rgba(255,255,255,.55); }
.adm-nav-site a:hover { color: var(--gold-bright); }
.adm-nav-label {
  font-family: 'IBM Plex Mono', monospace; font-size: 10.5px; letter-spacing: .14em;
  text-transform: uppercase; color: rgba(255,255,255,.32); padding-right: 8px;
  white-space: nowrap;
}

.adm-tiles {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 14px; margin-bottom: 30px;
}
.adm-tile {
  display: block; text-decoration: none; color: inherit;
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 22px 24px;
  transition: border-color .15s ease, transform .15s ease;
}
.adm-tile:hover { border-color: var(--gold); transform: translateY(-2px); }
.adm-tile b { display: block; font-family: 'Fraunces', serif; font-size: 32px; line-height: 1; }
.adm-tile span { display: block; font-size: 13px; color: var(--ink-dim); margin-top: 8px; }
.adm-tile.alert { border-color: var(--red); }
.adm-tile.alert b { color: var(--red); }

@media (max-width: 640px) {
  .adm-nav a { padding: 12px 13px; font-size: 13px; }
}

.admin-strip {
  background: #0C0A09; color: #F5F0E8;
  font-size: 13px; letter-spacing: .01em;
}
.admin-strip .wrap {
  display: flex; gap: 14px; align-items: center; justify-content: space-between;
  padding-top: 9px; padding-bottom: 9px; flex-wrap: wrap;
}
.admin-strip a { color: var(--gold-bright); text-decoration: none; font-weight: 600; }
.admin-strip a:hover { text-decoration: underline; }

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

.her-shout {
  display: block; text-decoration: none; color: inherit;
  grid-column: 1 / -1;
  background: linear-gradient(135deg, #C2657F 0%, #7A2E48 100%);
  border-radius: var(--radius); padding: 30px 32px;
  position: relative; overflow: hidden;
  transition: transform .18s ease, box-shadow .18s ease;
}
.her-shout:hover { transform: translateY(-2px); box-shadow: 0 14px 34px rgba(155,110,104,.28); }
.her-shout-tag {
  display: inline-block; background: rgba(255,255,255,.9); color: #7d4f49;
  font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
  padding: 5px 12px; border-radius: 999px; margin-bottom: 14px;
}
.her-shout h3 {
  font-family: 'Fraunces', serif; font-size: 30px; color: #fff;
  margin: 0 0 10px; line-height: 1.15;
}
.her-shout p {
  color: rgba(255,255,255,.93); font-size: 15px; line-height: 1.7;
  margin: 0 0 18px; max-width: 520px;
}
.her-shout-cta {
  display: inline-block; background: #fff; color: #7d4f49;
  font-weight: 600; font-size: 14px; padding: 12px 24px; border-radius: 999px;
}
@media (max-width: 640px) {
  .her-shout { padding: 24px 22px; }
  .her-shout h3 { font-size: 24px; }
}

/* ---------- Results, testimonials and the risk copy ----------
   The disclaimer is styled to be read, not skipped: full-width band, amber
   edge, decent type size. A page of winning trades needs it visible. */

.risk-band {
  background: rgba(196,100,94,.10);
  border-top: 1px solid rgba(196,100,94,.35);
  border-bottom: 1px solid rgba(196,100,94,.35);
}
.risk-band .wrap { padding-top: 22px; padding-bottom: 22px; }
.risk-band p {
  margin: 0; font-size: 14px; line-height: 1.75; color: rgba(245,240,232,.86);
}
.risk-band strong { color: #E08C84; }
.risk-note {
  font-size: 13px; line-height: 1.7; color: var(--ink-dim);
  border-left: 2px solid rgba(196,100,94,.5);
  padding-left: 14px; margin-top: 26px; max-width: 680px;
}

.sig-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px; margin-top: 30px;
}
.sig-example {
  background: var(--bg-alt); border: 1px solid var(--line);
  border-left: 3px solid var(--teal);
  border-radius: var(--radius); padding: 22px 24px;
}
.sig-group {
  font-family: 'IBM Plex Mono', monospace; font-size: 10.5px;
  letter-spacing: .14em; text-transform: uppercase; color: var(--gold);
}
.sig-call {
  font-family: 'IBM Plex Mono', monospace; font-size: 14px; line-height: 1.6;
  color: var(--ink); margin: 10px 0 14px;
}
.sig-out { list-style: none; padding: 0; margin: 0; }
.sig-out li {
  font-size: 13.5px; color: var(--teal); padding: 4px 0 4px 18px; position: relative;
}
.sig-out li::before {
  content: "→"; position: absolute; left: 0; opacity: .7;
}

.shots {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 16px; margin-top: 30px;
}
.shot { margin: 0; background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); overflow: hidden; }
.shot img { width: 100%; display: block; }
.shot figcaption { font-size: 12.5px; color: var(--ink-dim); padding: 12px 14px; }

.says {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px; margin-top: 30px;
}
.say {
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 24px 26px; margin: 0;
  position: relative;
}
.say p {
  font-size: 15.5px; line-height: 1.75; color: var(--ink); margin: 0 0 12px;
}
.say cite {
  font-style: normal; font-family: 'IBM Plex Mono', monospace;
  font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--gold);
}
/* ---------- The Daily Page ----------
   Deliberately not a form. Paper, a spine, ruled lines and a page turn, so
   writing in it feels like sitting down with a journal. */
.book {
  position: relative;
  background: linear-gradient(100deg, #EFE3DC 0%, #FBF3EF 12%, #FDF8F5 100%);
  border-radius: 6px 16px 16px 6px;
  box-shadow: 0 30px 60px rgba(0,0,0,.45), inset 0 0 60px rgba(180,99,122,.06);
  padding: 40px 44px 36px 58px;
  color: #2E1620;
}
.book-spine {
  position: absolute; left: 0; top: 0; bottom: 0; width: 26px;
  background: linear-gradient(90deg, rgba(46,22,32,.22), rgba(46,22,32,.04));
  border-radius: 6px 0 0 6px;
}
.book-spine::after {
  content: ""; position: absolute; left: 26px; top: 8px; bottom: 8px; width: 1px;
  background: rgba(180,99,122,.35);
}
.page-head {
  display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 1px solid rgba(46,22,32,.14); padding-bottom: 12px; margin-bottom: 22px;
}
.page-day { font-family: 'Fraunces', serif; font-size: 22px; color: #2E1620; }
.page-date {
  font-family: 'IBM Plex Mono', monospace; font-size: 11.5px;
  letter-spacing: .1em; text-transform: uppercase; color: #8B6A70;
}
.page-prompt {
  font-family: 'Fraunces', serif; font-size: 21px; line-height: 1.5;
  color: #2E1620; margin: 0 0 22px;
}
.page-lines {
  width: 100%; border: 0; resize: vertical; outline: none;
  background: repeating-linear-gradient(
    to bottom, transparent, transparent 33px, rgba(46,22,32,.13) 33px, rgba(46,22,32,.13) 34px);
  line-height: 34px; font-size: 16px; font-family: 'Inter', sans-serif;
  color: #2E1620 !important; padding: 0; min-height: 340px;
}
.page-lines::placeholder { color: #B29AA0 !important; }
.plum .book .page-lines, .book .page-lines {
  background: repeating-linear-gradient(
    to bottom, transparent, transparent 33px,
    rgba(46,22,32,.13) 33px, rgba(46,22,32,.13) 34px) !important;
  color: #2E1620 !important;
  border: 0 !important;
}
.plum .book .page-prompt, .plum .book .page-day { color: #2E1620; }
.plum .book .page-date, .plum .book .page-foot .hint { color: #8B6A70; }
.page-foot {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  margin-top: 22px; padding-top: 16px; border-top: 1px solid rgba(46,22,32,.14);
}
.page-foot .hint { color: #8B6A70; }
.book .btn-primary { background: #2E1620; border-color: #2E1620; color: #FDF2F3; }
.book .btn-primary:hover { background: #4A2233; border-color: #4A2233; }

.turn {
  display: flex; justify-content: space-between; gap: 14px; margin-top: 20px; flex-wrap: wrap;
}
.turn-btn {
  font-size: 14px; color: var(--gold); text-decoration: none;
  padding: 10px 2px; transition: opacity .15s ease;
}
.turn-btn:hover { opacity: .75; }
.turn-btn.is-off { color: var(--ink-dim); opacity: .55; }

.jdots { display: flex; gap: 6px; margin: 0 0 22px; flex-wrap: wrap; }
.jd {
  width: 12px; height: 12px; border-radius: 50%;
  border: 1px solid rgba(240,184,196,.35); display: block;
}
.jd.on { background: var(--rose); border-color: var(--rose); }
.jd.here { box-shadow: 0 0 0 3px rgba(240,184,196,.28); }

@media (max-width: 640px) {
  .book { padding: 28px 22px 26px 40px; }
  .page-prompt { font-size: 18px; }
}

.deposit-list { margin: 10px 0 0; padding-left: 20px; }
.deposit-list li {
  font-size: 15px; line-height: 1.8; color: var(--ink-dim); margin-bottom: 4px;
}
.deposit-list strong { color: var(--ink); }

.guide {
  margin: 22px 0 4px; max-width: 620px;
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 4px 20px;
}
.guide summary {
  cursor: pointer; padding: 14px 0; font-weight: 600; font-size: 14.5px;
  color: var(--gold); list-style: none;
}
.guide summary::-webkit-details-marker { display: none; }
.guide summary::before { content: "＋  "; opacity: .7; }
.guide[open] summary::before { content: "－  "; }
.guide ul { margin: 4px 0 14px; padding-left: 20px; }
.guide li {
  font-size: 14.5px; line-height: 1.75; color: var(--ink-dim); margin-bottom: 8px;
}
.guide-not {
  font-size: 13.5px; line-height: 1.7; color: var(--ink-dim);
  border-top: 1px solid var(--line); padding: 14px 0 16px; margin: 0;
}

.vid-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 22px;
}
.vid { background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); overflow: hidden; }
.vid-frame { position: relative; padding-top: 56.25%; background: #000; }
.vid-frame iframe {
  position: absolute; inset: 0; width: 100%; height: 100%; border: 0;
}
.vid h3 { font-size: 17px; margin: 16px 20px 6px; }
.vid p { font-size: 14px; line-height: 1.7; color: var(--ink-dim); margin: 0 20px 18px; }

.plum .say, .plum .sig-example { background: #26121C; border-color: rgba(240,184,196,.18); }
.plum .say cite { color: #F0B8C4; }

/* ---------- Reading surface ----------
   Lessons are long. White text on black is fine for a headline and awful for
   forty-one of them, so lesson bodies sit on paper inside the dark page. */
.reading {
  background: var(--paper);
  color: var(--paper-ink);
  border-radius: 20px;
  padding: 44px 48px;
  box-shadow: 0 24px 60px rgba(0,0,0,.4);
}
.reading h1, .reading h2, .reading h3, .reading h4, .reading strong {
  color: var(--paper-ink);
}
.reading p, .reading li, .reading td, .reading th { color: var(--paper-ink-dim); }
.reading hr, .reading table, .reading th, .reading td { border-color: var(--paper-line); }
.reading blockquote {
  border-left: 3px solid var(--gold); background: var(--paper-alt);
  padding: 14px 18px; border-radius: 8px;
}
.reading a { color: var(--teal-deep); }
.reading code {
  background: var(--paper-alt); color: var(--paper-ink); padding: 2px 6px; border-radius: 4px;
}
@media (max-width: 640px) { .reading { padding: 28px 22px; border-radius: 16px; } }

/* ---------- Female Wealth ----------
   Its own world: plum rather than the warm black used everywhere else. */
.plum {
  /* Dark plum, with the pink kept in the accents rather than the background.
     Text is #F7EAEE on #1A0C13, roughly 15:1, so it stays easy to read. */
  --bg: #1A0C13;
  --bg-alt: #26121C;
  --bg-alt-2: #331826;
  --ink: #F7EAEE;
  --ink-dim: rgba(247,234,238,.68);
  --line: rgba(240,184,196,.20);
  --gold: #E89BAE;
  --gold-bright: #F0B8C4;
  --rose: #E89BAE;
  background: var(--bg);
  color: var(--ink);
}
.plum .eyebrow { color: #F0B8C4 !important; }
.plum h1, .plum h2, .plum h3, .plum h4 { color: #FDF4F6; }
.plum p, .plum li { color: var(--ink-dim); }
.plum h1 em, .plum h2 em { color: #F0B8C4; }
.plum .btn-primary {
  background: #E89BAE; border-color: #E89BAE; color: #1A0C13;
}
.plum .btn-primary:hover { background: #F3B9C7; border-color: #F3B9C7; }
.plum .btn-ghost { border-color: rgba(247,234,238,.32); color: #F7EAEE; }
.plum .btn-ghost:hover { border-color: #F0B8C4; color: #F0B8C4; }
.plum .inline-link { color: #F0B8C4; }
.plum .topnav {
  background: rgba(26,12,19,.9);
  border-bottom: 1px solid rgba(240,184,196,.18);
}
.plum .brand { color: #FDF4F6; }
.plum .tabbar { background: #26121C; }
.plum .tabbar a { color: rgba(247,234,238,.66); }
.plum .tabbar a:hover { color: #FDF4F6; }
.plum .tabbar a.active { color: #F0B8C4; border-bottom-color: #F0B8C4; }
.plum footer { background: #140911; }
.plum footer .wrap { color: rgba(247,234,238,.6); }
.plum .benefit, .plum .form-panel, .plum .her-card, .plum .callout, .plum .hub-card {
  background: #26121C; border-color: rgba(240,184,196,.16);
}
.plum .benefit p, .plum .her-card p, .plum .hub-card p { color: var(--ink-dim); }
.plum .hub-bubble { background: #26121C; border-color: rgba(240,184,196,.16); color: var(--ink); }
.plum .hub-bubble.is-team { background: #331826; border-color: #E89BAE; }
.plum .hub-meta { color: rgba(247,234,238,.6); }
.plum .support-float { background: #E89BAE; color: #1A0C13; }
.plum input, .plum select, .plum textarea {
  background: #331826 !important; color: #F7EAEE !important;
  border-color: rgba(240,184,196,.24) !important;
}
.plum input::placeholder, .plum textarea::placeholder { color: rgba(247,234,238,.42); }
.plum .form-panel button { background: #E89BAE; color: #1A0C13; }
.plum .pill { color: rgba(247,234,238,.7); border-color: rgba(240,184,196,.24); background: #331826; }
/* the reading bubble stays paper, so masterclasses remain easy to read */

/* Every other page's hero. Same warm black and the same gold, so the site
   holds together instead of one dramatic page and the rest plain cream. */
.page-hero {
  background: #150F0B;
  background-image:
    radial-gradient(54% 130% at 6% 100%, rgba(63,169,135,.18), transparent 66%),
    radial-gradient(50% 120% at 94% 0%, rgba(196,100,94,.16), transparent 66%);
  border-bottom: 1px solid rgba(217,184,124,.22);
  padding-top: 76px;
}
.page-hero h1 { color: #FAF6F0; }
.page-hero h1 em { color: #E0BE86; font-style: italic; }
.page-hero .eyebrow { color: #D9B87C !important; }
.page-hero .lede, .page-hero p { color: rgba(250,246,240,.76); }
.page-hero .btn-primary {
  background: #D9B87C; border-color: #D9B87C; color: #150F0B;
}
.page-hero .btn-primary:hover { background: #E8CB96; border-color: #E8CB96; }
.page-hero .btn-ghost { border-color: rgba(250,246,240,.34); color: #FAF6F0; }
.page-hero .btn-ghost:hover { border-color: #D9B87C; color: #D9B87C; }
.page-hero .ring-mark { border-color: rgba(217,184,124,.5); }
.page-hero .ring-mark span { color: #E0BE86; }
.page-hero .inline-link { color: #E0BE86; }

/* The women's section gets its own dark treatment, in its own palette,
   so it reads as a distinct place rather than more of the same cream. */
.band-rose {
  background: #241019;
  background-image:
    radial-gradient(60% 120% at 88% 0%, rgba(201,162,155,.26), transparent 66%),
    radial-gradient(52% 110% at 6% 100%, rgba(126,58,74,.34), transparent 70%);
  border-top: 1px solid rgba(201,162,155,.24);
  border-bottom: 1px solid rgba(201,162,155,.24);
}
.band-rose h2, .band-rose h3 { color: #FDF7F5; }
.band-rose p, .band-rose li { color: rgba(253,247,245,.76); }
.band-rose .eyebrow { color: #E3BDB6 !important; }
.band-rose .community-panel {
  background: rgba(253,247,245,.05);
  border: 1px solid rgba(253,247,245,.12);
}
.band-rose .btn-primary {
  background: var(--rose); border-color: var(--rose); color: #241019;
}
.band-rose .btn-primary:hover { background: #DCB8B1; border-color: #DCB8B1; }
.band-rose .inline-link { color: #E3BDB6; }

/* The closing block. Ending on dark stops the page fading out on cream. */
#support.band-dark .btn-ghost {
  border-color: rgba(250,246,240,.34); color: #FAF6F0;
}
#support.band-dark .btn-ghost:hover { border-color: #D9B87C; color: #D9B87C; }
#support.band-dark .btn-primary {
  background: #D9B87C; border-color: #D9B87C; color: #150F0B;
}

/* A second dark band mid-page. Without it the cream runs for thousands of
   pixels and the hero's palette never comes back. */
.band-dark {
  background: #150F0B;
  background-image:
    radial-gradient(58% 120% at 8% 100%, rgba(63,169,135,.20), transparent 68%),
    radial-gradient(54% 120% at 92% 0%, rgba(196,100,94,.18), transparent 68%);
  border-top: 1px solid rgba(217,184,124,.2);
  border-bottom: 1px solid rgba(217,184,124,.2);
}
.band-dark h2 { color: #FAF6F0; }
.band-dark .eyebrow { color: #D9B87C !important; }
.band-dark p { color: rgba(250,246,240,.72); }

.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.compare-col {
  padding: 26px 28px; border-radius: var(--radius);
  background: rgba(250,246,240,.04);
  border: 1px solid rgba(250,246,240,.10);
}
.compare-col h3 {
  font-family: 'IBM Plex Mono', monospace; font-size: 11.5px;
  letter-spacing: .16em; text-transform: uppercase; margin: 0 0 12px;
}
.compare-col p { font-size: 15px; line-height: 1.75; margin: 0; }
.compare-col.is-them { border-left: 3px solid var(--rose-pop); }
.compare-col.is-them h3 { color: #D2837D; }
.compare-col.is-us {
  border-left: 3px solid var(--teal);
  background: rgba(63,169,135,.09);
}
.compare-col.is-us h3 { color: #6FCBA9; }
@media (max-width: 700px) { .compare { grid-template-columns: 1fr; } }

/* Components that assumed a cream page, corrected for the dark one */
.tinted { background: var(--bg-alt); }
.benefit, .form-panel, .callout, .her-card, .course-card, .step {
  color: var(--ink);
}
.btn-ghost { border-color: rgba(245,240,232,.28); color: var(--ink); }
.btn-ghost:hover { border-color: var(--gold); color: var(--gold); }
.btn-primary { background: var(--gold); border-color: var(--gold); color: #12100E; }
.btn-primary:hover { background: #D9B87C; border-color: #D9B87C; }
footer, .footer { background: #0C0A09; }
input, select, textarea { background: var(--bg-alt) !important; color: var(--ink) !important; }
input::placeholder, textarea::placeholder { color: rgba(245,240,232,.42); }

/* ---------- Colour through the page ----------
   The hero palette has to keep appearing or it reads as a one-off. These
   carry teal, gold and rose into the sections, panels and links below it. */

/* Section eyebrows cycle, so each block announces itself in a different key */
section:nth-of-type(3n+1) .eyebrow { color: var(--teal); }
section:nth-of-type(3n+2) .eyebrow { color: var(--gold); }
section:nth-of-type(3n+3) .eyebrow { color: var(--rose-pop); }

/* A hairline of colour at the top of each panel and callout */
.form-panel { border-top: 3px solid var(--gold); }
.callout {
  border-left: 3px solid var(--teal);
  background: linear-gradient(90deg, rgba(63,169,135,.07), transparent 60%);
}

/* Course cards in the education block get their own identity */
.course-card:nth-child(1) { border-top: 3px solid var(--teal); }
.course-card:nth-child(2) { border-top: 3px solid var(--gold); }

/* Links and quotes pick up the accent rather than sitting brown on cream */
.inline-link { color: var(--teal-deep); }
.inline-link:hover { color: var(--teal); }
.editorial blockquote em { color: var(--rose) !important; }

/* Feature cards in the women's section */
.her-card { border-left: 3px solid transparent; }
.her-card:hover { border-left-color: var(--rose); }

/* The results and feedback tiles */
.benefit .icon {
  border-color: currentColor;
  background: color-mix(in srgb, currentColor 14%, transparent);
  box-shadow: 0 0 0 6px color-mix(in srgb, currentColor 7%, transparent);
}

/* Section headings get a short coloured rule, so each block is marked */
.section-head .eyebrow { position: relative; padding-left: 34px; }
.section-head .eyebrow::before {
  content: ""; position: absolute; left: 0; top: 50%;
  width: 24px; height: 2px; background: currentColor; opacity: .85;
}

/* Alternating tint. Without it the page below the hero is one unbroken
   cream slab for several thousand pixels. */
.tinted { background: var(--bg-alt); }
.tinted-edge { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }

/* The benefit cards pick up the palette so the hero colours recur rather
   than appearing once and vanishing. */
.grid5 .benefit:nth-child(5n+1) .icon { color: var(--teal); }
.grid5 .benefit:nth-child(5n+2) .icon { color: var(--gold); }
.grid5 .benefit:nth-child(5n+3) .icon { color: var(--rose-pop); }
.grid5 .benefit:nth-child(5n+4) .icon { color: var(--teal-deep); }
.grid5 .benefit:nth-child(5n+5) .icon { color: var(--gold-bright); }
.grid5 .benefit { border-top: 2px solid transparent; transition: border-color .18s ease, transform .18s ease; }
.grid5 .benefit:hover { transform: translateY(-2px); }
.grid5 .benefit:nth-child(5n+1):hover { border-top-color: var(--teal); }
.grid5 .benefit:nth-child(5n+2):hover { border-top-color: var(--gold); }
.grid5 .benefit:nth-child(5n+3):hover { border-top-color: var(--rose-pop); }
.grid5 .benefit:nth-child(5n+4):hover { border-top-color: var(--teal-deep); }
.grid5 .benefit:nth-child(5n+5):hover { border-top-color: var(--gold-bright); }

/* ---------- Hub ---------- */

.hub-card {
  display: block; text-decoration: none; color: inherit;
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 22px 24px; margin-bottom: 14px;
  transition: border-color .18s ease, transform .18s ease, box-shadow .18s ease;
}
.hub-card:hover {
  border-color: var(--gold); transform: translateY(-2px);
  box-shadow: 0 10px 26px rgba(60,44,32,.09);
}
.hub-card h3 { font-family: 'Fraunces', serif; font-size: 19px; margin: 0 0 6px; line-height: 1.3; }
.hub-card p { color: var(--ink-dim); font-size: 14.5px; margin: 0 0 10px; line-height: 1.6; }

.hub-meta { font-size: 12.5px; color: var(--ink-dim); }

.hub-bubble {
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: 16px 16px 16px 4px; padding: 18px 20px; margin-bottom: 12px;
  font-size: 15px; line-height: 1.75;
}
.hub-bubble.is-team {
  background: var(--bg); border-color: var(--gold);
  border-radius: 16px 16px 4px 16px;
  box-shadow: 0 4px 14px rgba(156,122,78,.10);
}

.hub-video {
  position: relative; width: 100%; padding-bottom: 56.25%;
  border-radius: var(--radius); overflow: hidden; margin-bottom: 14px;
  border: 1px solid var(--line); background: #000;
}
.hub-video iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }

.hub-intro {
  background: var(--bg-alt); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 26px 28px; margin-bottom: 26px;
}
.hub-intro h2 { font-size: 21px; margin: 0 0 8px; }
.hub-intro p { color: var(--ink-dim); font-size: 14.5px; line-height: 1.7; margin: 0; }
.hub-count { font-size: 13px; color: var(--ink-dim); margin: 0 0 14px; }

.form-panel textarea {
  width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink);
  padding: 13px 16px; border-radius: 10px; font-family: inherit; font-size: 15px;
  resize: vertical;
}
.form-panel input[type="text"] {
  width: 100%; background: var(--bg); border: 1px solid var(--line); color: var(--ink);
  padding: 13px 16px; border-radius: 10px; font-family: inherit; font-size: 15px;
}
.form-panel label {
  display: block; font-size: 12px; color: var(--ink-dim);
  margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .05em;
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
.adm-bar a.on { background: var(--gold); color: #12100E; border-color: var(--gold); }

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


ADMIN_NAV = [
    ("/admin", "Members", "members"),
    ("/admin/photos", "Screenshots", "photos"),
    ("/admin/inbox", "Inbox", "inbox"),
    ("/admin/hub", "Board", "boards"),
    ("/admin/submissions", "Feedback", "feedback"),
    ("/admin/videos", "Videos", "videos"),
    ("/admin/duplicates", "Duplicates", "duplicates"),
]


def admin_layout(title: str, content: str, active: str = "") -> str:
    """
    The back office. Deliberately not the member site: no public tabs, no
    marketing nav, no footer. Everything an admin needs is one click away and
    it always looks like a control panel, never like the shop front.
    """
    def cls(key):
        return "on" if key == active else ""

    links = "".join(f'<a href="{href}" class="{cls(key)}">{label}</a>'
                    for href, label, key in ADMIN_NAV)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{esc(title)} · Inner Circle admin</title>
{FONT_LINK}
<style>{BASE_CSS}</style>
</head>
<body>
<div class="adm-shell">
  <header class="adm-top">
    <div class="wrap">
      <a href="/admin" class="adm-brand">INNER&middot;CIRCLE <span>admin</span></a>
      <div class="adm-top-right">
        <a href="/" target="_blank" rel="noopener">View live site &#8599;</a>
        <a href="/admin/logout" class="adm-out">Log out</a>
      </div>
    </div>
  </header>
  <nav class="adm-nav">
    <div class="wrap">{links}</div>
  </nav>
  <nav class="adm-nav adm-nav-site">
    <div class="wrap">
      <span class="adm-nav-label">Member pages</span>
      <a href="/" target="_blank" rel="noopener">Home</a>
      <a href="/onboarding" target="_blank" rel="noopener">Onboarding</a>
      <a href="/education/fundamentals" target="_blank" rel="noopener">Fundamentals</a>
      <a href="/education/advanced" target="_blank" rel="noopener">Advanced</a>
      <a href="/signals" target="_blank" rel="noopener">Signals</a>
      <a href="/results" target="_blank" rel="noopener">Results</a>
      <a href="/her" target="_blank" rel="noopener">Female Wealth</a>
      <a href="/her/courses" target="_blank" rel="noopener">Her courses</a>
      <a href="/her/hub" target="_blank" rel="noopener">Her board</a>
      <a href="/her/journal" target="_blank" rel="noopener">Daily Page</a>
      <a href="/my-signals" target="_blank" rel="noopener">Signals hub</a>
    </div>
  </nav>
  <main>{content}</main>
</div>
</body>
</html>"""


def base_layout(title: str, content: str, active: str = "", theme: str = "") -> str:
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

    if session.get("admin"):
        admin_strip = ('<div class="admin-strip"><div class="wrap">'
                       '<span>Signed in as admin. You can see everything on the site.</span>'
                       '<a href="/admin/logout">Log out of admin</a>'
                       '</div></div>')
    else:
        admin_strip = ""

    if session.get("admin"):
        # Admin sees the whole site plus the admin side of it, and stays admin
        # while moving between the two. Nothing here logs them out.
        nav_cta = '<a href="/admin" class="nav-cta">Admin</a>'
        tabs = "".join(f'<a href="{href}" class="{nav_class(key)}">{label}</a>' for href, label, key in [
            ("/admin", "Members", "adminhome"),
            ("/admin/photos", "Screenshots", "adminphotos"),
            ("/admin/inbox", "Inbox", "admininbox"),
            ("/admin/hub", "Board", "adminhub"),
            ("/her/hub", "Female Wealth", "community"),
            ("/education/fundamentals", "Foundation", "fundamentals"),
            ("/education/advanced", "Advanced", "advanced"),
            ("/signals", "Signals", "signals"),
        ])
    elif logged_in:
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
<body class="{theme}">
<nav class="topnav">
  <div class="wrap">
    <a href="/" class="brand">INNER<span class="dot">·</span>CIRCLE</a>
    <div class="navlinks">
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" style="color: var(--gold);">Support</a>
    </div>
    {nav_cta}
  </div>
</nav>
{admin_strip}
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

    <div class="adm-tiles">
      <a class="adm-tile {'alert' if counts['pending'] else ''}" href="/admin?view=pending">
        <b>{counts['pending']}</b><span>Waiting for approval</span></a>
      <a class="adm-tile {'alert' if pending_photos else ''}" href="/admin/photos">
        <b>{pending_photos}</b><span>Screenshots to check</span></a>
      <a class="adm-tile {'alert' if unread else ''}" href="/admin/inbox">
        <b>{unread}</b><span>Unread messages</span></a>
      <a class="adm-tile {'alert' if counts['community'] else ''}" href="/admin?view=community">
        <b>{counts['community']}</b><span>Female Wealth requests</span></a>
      <a class="adm-tile" href="/admin?view=approved">
        <b>{counts['approved']}</b><span>Approved members</span></a>
      <a class="adm-tile {'alert' if counts['dupes'] else ''}" href="/admin/duplicates">
        <b>{counts['dupes']}</b><span>Duplicate phone numbers</span></a>
    </div>

    <div class="adm-bar">
      <a href="/admin?view=pending" class="{'on' if view == 'pending' else ''}">Waiting ({counts['pending']})</a>
      <a href="/admin?view=approved" class="{'on' if view == 'approved' else ''}">Approved ({counts['approved']})</a>
      <a href="/admin?view=community" class="{'on' if view == 'community' else ''}">Female Wealth ({counts['community']})</a>
      <a href="/admin?view=all" class="{'on' if view == 'all' else ''}">Everyone</a>
    </div>

    {photo_panel}

    <h2 style="font-size: 19px; margin: 0 0 16px;">{heading}</h2>
    {cards}
  </div>
</section>
"""
    return render_template_string(admin_layout("Members", content, "members"))


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
    # Every row gets the picker now, because a match can be wrong and an
    # onboarder has no account to match against in the first place.
    candidates = get_all_members()[:200] if subs else []

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

        # Two very different situations, so two clearly separate actions.
        #
        # A new person onboarding has no account yet. Their screenshots earn
        # them a verification code, which they take to the form on the website,
        # and the form is what creates the account. Nothing to attach here.
        #
        # An existing member sending payment or extra signals proof already has
        # an account, so that one gets attached and unlocked.
        new_label = esc(sub.get("first_name") or sub.get("username") or "this person")
        verify_block = (
            f'<div style="border:1px solid var(--line); border-radius:10px; padding:14px; '
            f'margin-bottom:12px; background:var(--bg);">'
            f'<strong style="font-size:14.5px;">New person onboarding</strong>'
            f'<p class="hint" style="margin:4px 0 10px;">Sends {new_label} their verification code. '
            f'They fill in the form on the website with their details, and that creates the account.</p>'
            f'<form method="POST" action="/admin/photos/{sub["id"]}/verify">'
            f'<button type="submit" class="btn btn-primary btn-sm">'
            f'Accept photos and send their code</button></form></div>'
        )

        options = "".join(
            f'<option value="{c["id"]}" {"selected" if c["id"] == sub.get("member_id") else ""}>'
            f'{esc(c.get("name") or "unnamed")} · {esc(pretty_phone(c.get("phone")))} · #{c["id"]}</option>'
            for c in candidates
        )
        unlock_block = (
            f'<div style="border:1px solid var(--line); border-radius:10px; padding:14px; '
            f'background:var(--bg);">'
            f'<strong style="font-size:14.5px;">Already a member</strong>'
            f'<p class="hint" style="margin:4px 0 10px;">Attach it to their account and choose what '
            f'this unlocks.</p>'
            f'<form method="POST" action="/admin/photos/{sub["id"]}/approve">'
            f'<select name="member_id" required '
            f'style="width:100%; max-width:420px; background:var(--bg-alt); border:1px solid var(--line); '
            f'color:var(--ink); padding:10px 12px; border-radius:9px; font-size:13.5px; margin-bottom:10px;">'
            f'<option value="">Whose account is this?</option>{options}</select>'
            f'<div style="display:flex; gap:8px; flex-wrap:wrap;">'
            f'<button type="submit" name="unlock" value="advanced" class="btn btn-primary btn-sm">'
            f'Unlock Advanced course</button>'
            f'<button type="submit" name="unlock" value="extra" class="btn btn-primary btn-sm">'
            f'Unlock Extra signals</button>'
            f'<button type="submit" name="unlock" value="onboarding" class="btn btn-ghost btn-sm">'
            f'Unlock gold + Fundamentals</button>'
            f'</div></form></div>'
        )

        action = (verify_block + unlock_block if kind != "payment"
                  else unlock_block + '<div style="height:12px;"></div>' + verify_block)

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
    return render_template_string(admin_layout("Screenshots", content, "photos"))


@app.route("/admin/photos/<int:sub_id>/image/<int:idx>")
def admin_photo_image(sub_id, idx):
    """
    Stream a screenshot from Telegram through the site. Proxied rather than
    linked directly, because the direct Telegram URL contains the bot token
    and would be visible in the page source.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    sub = get_photo_submission(sub_id)
    if not sub:
        return "submission not found", 404

    ids = [f for f in (sub.get("file_ids") or "").split(",") if f]
    if idx >= len(ids):
        return "no image saved for this submission", 404

    url = telegram_file_url(ids[idx])
    if not url:
        return "Telegram would not give up the file, check it in your admin chat", 404

    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return "Telegram returned an error for that file", 404
        return Response(r.content,
                        mimetype=r.headers.get("Content-Type", "image/jpeg"),
                        headers={"Cache-Control": "private, max-age=600"})
    except Exception:
        return "could not fetch that image", 404


@app.route("/admin/photos/<int:sub_id>/approve", methods=["POST"])
def admin_photo_approve(sub_id):
    """
    Approve a screenshot. Handles the three cases that actually come up:
    the sender already has an account, the sender is brand new and onboarding
    (so the account gets created here), and choosing what the approval unlocks
    rather than guessing from the photo.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    sub = get_photo_submission(sub_id)
    if not sub:
        return redirect("/admin/photos?err=" + quote("That submission has gone."))

    unlock = request.form.get("unlock", "")
    if unlock == "advanced":
        want = {"advanced"}
    elif unlock == "onboarding":
        want = {"signals_gold", "fundamentals"}
    elif unlock == "extra":
        want = {"signals_currency", "fundamentals"}
    else:
        want = {"advanced"} if sub.get("kind") == "payment" else {"signals_gold", "fundamentals"}

    member_id = sub.get("member_id")
    picked = request.form.get("member_id", "")
    created = False

    if picked == "new_unused":
        # Onboarding creates the account. That is the whole point of it, so the
        # queue has to be able to make one rather than only attach to an
        # existing member.
        tier = "currency" if "signals_currency" in want else "gold"
        name = (sub.get("first_name") or "").strip() or "New member"
        member_id = create_pending_member(
            tier=tier, title="", name=name, account_number="",
            deposit_amount="", phone="",
            telegram_username=(f"@{sub['username']}" if sub.get("username") else None),
        )
        if member_id:
            created = True
            link_photo_to_member(sub_id, member_id)
            conn = get_db()
            if conn:
                try:
                    with conn, conn.cursor() as cur:
                        cur.execute("UPDATE members SET chat_id=%s, updated_at=NOW() WHERE id=%s",
                                    (sub.get("chat_id"), member_id))
                finally:
                    conn.close()
            audit(member_id, "account created", "created from their onboarding screenshots")
    elif picked.isdigit():
        member_id = int(picked)
        link_photo_to_member(sub_id, member_id)
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET chat_id=COALESCE(chat_id, %s),
                                   telegram_username=COALESCE(NULLIF(telegram_username,''), %s),
                                   updated_at=NOW() WHERE id=%s""",
                                (sub.get("chat_id"),
                                 (f"@{sub['username']}" if sub.get("username") else None),
                                 member_id))
            finally:
                conn.close()

    if not member_id:
        return redirect("/admin/photos?err=" +
                        quote("Choose whose account this is, or create a new one for them."))

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
    made = " New account created for them." if created else ""
    return redirect("/admin/photos?ok=" + quote(f"Approved for {labels}.{made} {summary}"))


@app.route("/admin/photos/<int:sub_id>/verify", methods=["POST"])
def admin_photo_verify(sub_id):
    """
    A brand new person onboarding. Their screenshots are good, so send them
    their verification code. They put that into the form on the website with
    their details, and that is what creates the account. Nothing is created
    here, because we don't have their name, phone or broker account yet.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    sub = get_photo_submission(sub_id)
    if not sub:
        return redirect("/admin/photos?err=" + quote("That submission has gone."))

    chat_id = sub.get("chat_id")
    if not chat_id:
        return redirect("/admin/photos?err=" +
                        quote("No Telegram chat on that one, so a code can't be sent."))

    code = gen_verification_code()
    save_verification_code(code, chat_id, sub.get("username") or "", sub.get("photo_count") or 2)
    sent = send_telegram_message(
        chat_id,
        "Great news, your screenshots have been checked and everything looks right!\n\n"
        f"Your verification code is: {code}\n\n"
        "Now head to the website and fill in the short form with your details, using that code:\n"
        f"{SITE}/onboarding/activate\n\n"
        "That sets your account up, and once it's approved I'll send your access code and your "
        "signals group link straight here."
    )
    resolve_photo_submission(sub_id, "approved")

    who = sub.get("first_name") or sub.get("username") or "them"
    if sent:
        return redirect("/admin/photos?ok=" +
                        quote(f"Photos accepted. Code {code} sent to {who}, "
                              f"they'll fill in the form next."))
    return redirect("/admin/photos?err=" +
                    quote(f"Photos accepted but the message didn't send. Their code is {code}, "
                          f"pass it on and point them at {SITE}/onboarding/activate"))


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
    return render_template_string(admin_layout("Inbox", content, "inbox"))


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
    return render_template_string(admin_layout(member.get("name") or "Member", content, "members"))


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
    return render_template_string(admin_layout("Duplicates", content, "duplicates"))


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
        code = gen_verification_code()
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


def gen_verification_code():
    """The IC- code someone types into the onboarding form on the website."""
    return "IC-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))


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
        return ("Love it, send your screenshot over.\n\n"
                "We'll ask you before anything goes on the website, and we'd only ever show the "
                "trades, never your balance or your name beyond a first name."), "awaiting_checkin"

    return None, None


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return "ok"

    try:
        last = _meta_get("checkins_last_run")
        due = True
        if last:
            due = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() > 3600
        if due:
            _meta_set("checkins_last_run", datetime.utcnow().isoformat())
            send_due_checkins()
    except Exception:
        pass

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
                "👋  \"I have an account\"  -  already a member? I'll find you",
                "🚀  \"getting started\"  -  how it all works, from scratch",
                "📝  \"onboarding\"  -  the steps to get set up",
                "✅  \"activate\"  -  once you've done those steps",
                "📚  \"how to trade\"  -  our free beginners course",
                "📈  \"advanced\"  -  the £99 chart reading course",
                "👭  \"female wealth\"  -  our women only community",
                "",
                f"New here? Start at {SITE}/onboarding",
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

    # Anything sent while we're waiting on a check-in reply becomes a
    # submission for review. Nothing reaches the site without approval.
    if state == "awaiting_checkin":
        if photos:
            ids = [p[-1].get("file_id") for p in [photos] if p] if isinstance(photos, list) else []
            file_ids = [photos[-1].get("file_id")] if isinstance(photos, list) and photos else []
            sid = add_submission(member.get("id") if member else None, chat_id,
                                 "result", raw_text or "", file_ids, source="bot")
            reply("Got it, thank you. That's gone to the team to look over.\n\n"
                  "If we'd like to use it we'll check with you first. Anything else I can help with?")
            notify_admin(f"📸 RESULTS SUBMITTED\n\n{contact_block()}\n\n"
                         f"Review it: {SITE}/admin/submissions")
            set_bot_state(chat_id, state=None)
            return "ok"
        if raw_text and len(raw_text.strip()) > 3:
            low = raw_text.strip().lower()
            if low in ("no", "no thanks", "no thank you", "nope", "not now", "nah"):
                reply("No problem at all, I won't ask again. I'm here if you need anything.")
                set_bot_state(chat_id, state=None)
                return "ok"
            sid = add_submission(member.get("id") if member else None, chat_id,
                                 "feedback", raw_text, source="bot")
            reply("Thank you, that's really useful.\n\n"
                  "If we'd like to put it on the website we'll ask you first. "
                  "Anything I can help you with while you're here?")
            notify_admin(f"💬 FEEDBACK SUBMITTED\n\n{contact_block()}\n\n"
                         f"\"{raw_text[:300]}\"\n\nReview it: {SITE}/admin/submissions")
            set_bot_state(chat_id, state=None)
            return "ok"

    GREETINGS = ("hi", "hey", "hello", "yo", "hiya", "heya", "good morning", "good afternoon",
                 "good evening", "hi there", "hey there", "hello there", "morning", "evening",
                 "hi!", "hey!", "hello!", "hiya!")
    if text in GREETINGS and not photos:
        # A member gets greeted as a member. Telling someone who has been
        # trading with us for a month to "start onboarding" is the single most
        # annoying thing this bot could do.
        if is_member:
            who = (member.get("name") or "").split(" ")[0] or first_name
            reply(f"Welcome back{', ' + who if who else ''}.\n\n"
                  f"What can I help you with?\n\n"
                  f"🔑  \"my login\"  -  your access code\n"
                  f"🔗  \"my signals\"  -  every group link you have\n"
                  f"📚  \"my course\"  -  your lessons\n"
                  f"🎉  \"share results\"  -  send us how you're getting on\n\n"
                  f"Or just ask me anything.")
            set_bot_state(chat_id, greeted=True)
            return "ok"
        if already_greeted:
            name_bit = f" {first_name}" if first_name else ""
            reply(f"Hey{name_bit}, what can I help you with?")
        else:
            reply(WELCOME_MENU)
            set_bot_state(chat_id, greeted=True)
        return "ok"

    # -----------------------------------------------------------------
    # 0. Mid-conversation onboarding questions, asked once the screenshots
    #    are in. Name has to match the broker account, and knowing how people
    #    found us is worth asking while they're still talking to us.
    # -----------------------------------------------------------------
    if state == "awaiting_onboard_name" and raw_text and not photos:
        name_given = raw_text.strip()[:80]
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE photo_submissions SET first_name=%s
                                   WHERE chat_id=%s AND status='pending'""", (name_given, chat_id))
                    cur.execute("""UPDATE members SET name=COALESCE(NULLIF(name,''), %s),
                                   updated_at=NOW() WHERE chat_id=%s""", (name_given, chat_id))
            finally:
                conn.close()
        set_bot_state(chat_id, state="awaiting_onboard_referrer")
        notify_admin(f"📝 ONBOARDING NAME\n\n{contact_block()}\n\nGave their name as: {name_given}")
        reply(f"Thanks {name_given.split(' ')[0]}.\n\n"
              "And last one: who referred you, or where did you find us? A name, an Instagram "
              "handle, wherever you saw us. If you found us yourself just say so.")
        return "ok"

    if state == "awaiting_onboard_referrer" and raw_text and not photos:
        ref = raw_text.strip()[:120]
        conn = get_db()
        if conn:
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("""UPDATE members SET referred_by=COALESCE(NULLIF(referred_by,''), %s),
                                   updated_at=NOW() WHERE chat_id=%s""", (ref, chat_id))
            finally:
                conn.close()
        clear_bot_state(chat_id)
        notify_admin(f"📝 ONBOARDING REFERRER\n\n{contact_block()}\n\nFound us through: {ref}\n\n"
                     f"Screenshots are waiting: {SITE}/admin/photos")
        reply("Brilliant, that's everything from me.\n\n"
              "Our team will check your screenshots, usually well within 24 hours, and I'll send "
              "your verification code straight here. You'll then fill in a short form on the "
              "website with your details and you're in.\n\n"
              "Anything else you need in the meantime, just ask.")
        return "ok"

    # -----------------------------------------------------------------
    # 1. Risk, money and boundaries. These come before everything else,
    #    because getting them wrong matters more than being helpful.
    # -----------------------------------------------------------------
    # Someone being abusive, or fishing for something the bot shouldn't touch.
    # Answer once, calmly, and point at a person. Never argue back.
    HOSTILE = ("stupid", "useless", "idiot", "shut up", "scam", "scammer", "fraud",
               "rip off", "ripoff", "thief", "liar", "fake", "hate you", "worthless")
    UNSAFE_ASK = ("guaranteed profit", "guarantee me", "double my money", "get rich quick",
                  "can't lose", "cant lose", "sure thing", "insider", "manipulate",
                  "launder", "tax free", "avoid tax", "hide money", "fake screenshot",
                  "fake results", "make me rich")

    if raw_text and any(w in text for w in UNSAFE_ASK):
        reply("I have to be straight with you on that one.\n\n"
              "There are no guaranteed profits in trading, and nobody can promise you'll double "
              "your money. Anyone who tells you otherwise isn't being honest with you.\n\n"
              "We never claim your results will be the same as anyone else's. All results vary. "
              "You trade your own account and keep full control of it, and every signal says so.\n\n"
              "What we actually offer is signals with the levels set out, free education so you "
              "understand them, and people to ask. Trading carries risk and you can lose money.\n\n"
              f"The free course is here: {SITE}/education/fundamentals")
        notify_admin(f"⚠️ WORTH A LOOK\n\n{contact_block()}\n\nThey asked:\n{raw_text}")
        return "ok"

    if raw_text and any(w in text for w in HOSTILE):
        # Answer the substance rather than deflecting. Someone calling it a scam
        # usually wants to know what is actually being claimed, so tell them.
        reply("Fair enough, let me be straight with you about what this is.\n\n"
              "We have never claimed your results will match anyone else's. All results vary, "
              "and they always will. You trade your own account, you keep full control of it, "
              "and every signal we send says exactly that.\n\n"
              "Trading carries risk and you can lose money. That's written on the website and "
              "I'll say it to you here too.\n\n"
              f"The education is free: {SITE}/education/fundamentals. It's free on purpose, so "
              "people trade sensibly instead of copying numbers they don't understand.\n\n"
              "Anything else you want to ask me?")
        notify_admin(f"⚠️ WORTH A LOOK\n\n{contact_block()}\n\nThey said:\n{raw_text}")
        return "ok"

    LOSS_WORDS = ("can i lose", "could i lose", "will i lose", "lose money", "lose my money",
                  "is it risky", "how risky", "is it safe", "guaranteed", "guarantee",
                  "am i guaranteed", "can i lose money", "risk of losing", "what if i lose")
    if any(w in text for w in LOSS_WORDS) and not photos:
        reply(
            "Yes. You can lose money, and you should assume you will lose on some trades.\n\n"
            "I'm not going to tell you otherwise. Nothing here is guaranteed, no signal is a "
            "prediction, and past results are not a guide to what happens next.\n\n"
            "What we do is teach you to manage it: small position sizes, a stop loss on every "
            "trade, and never risking money you need. Some of our members are up, some are "
            "down. That's the honest picture.\n\n"
            "Only ever trade with money you can genuinely afford to lose. If money is tight "
            "right now, this is not the moment to start.\n\n"
            "Nothing I say is financial advice. Your account is yours and every decision in it "
            "is yours."
        )
        return "ok"

    MONEY_WORDS = ("how much do i need", "how much money", "minimum deposit", "how much to start",
                   "what do i need to start", "minimum to join", "how much to join",
                   "starting amount", "what deposit", "how much deposit", "minimum")
    if any(w in text for w in MONEY_WORDS) and not photos:
        reply(
            "For the main gold signals, the minimum deposit is £300.\n\n"
            "The broker adds a 50% match on top, so £300 gives you around £450 to trade with. "
            "The match is the broker's, not ours.\n\n"
            "For extra signals there's no minimum and the match still applies, but we recommend "
            "at least £150 so you can size sensibly. Going in smaller than that makes proper "
            "risk management very hard.\n\n"
            "The money goes into your own broker account, not to us, and it stays yours the "
            "whole time. You control it and you can withdraw it.\n\n"
            "Only put in what you'd be genuinely comfortable losing. You can lose it, and this "
            "isn't financial advice.\n\n"
            f"The full steps are here: {SITE}/onboarding"
        )
        return "ok"

    # If someone is talking to the bot inappropriately, it does not play along.
    OUT_OF_BOUNDS = ("send nudes", "you're sexy", "youre sexy", "are you single", "kiss me",
                     "i love you baby", "sexy", "horny", "naked", "date me", "wanna hook up")
    if any(w in text for w in OUT_OF_BOUNDS) and not photos:
        reply("I'm the Inner Circle support bot, so let's keep it to trading.\n\n"
              "Anything I can help you with on the signals, the courses or your account?")
        return "ok"

    # Guaranteed-money and get-rich framing gets corrected, not encouraged.
    HYPE_WORDS = ("get rich quick", "make me rich", "double my money", "how much will i make",
                  "how much can i make", "quit my job", "become a millionaire", "easy money",
                  "free money")
    if any(w in text for w in HYPE_WORDS) and not photos:
        reply(
            "I can't tell you what you'll make, and I'd be wary of anyone who does.\n\n"
            "There's no guaranteed return here. Some weeks are good, some are not, and losing "
            "trades are part of it for everyone. Anyone promising you a number is selling "
            "something.\n\n"
            "What we can promise is that you'll be taught what you're doing rather than blindly "
            "copying, and that results get posted openly, wins and losses both.\n\n"
            "Please don't put in money you're relying on, and don't treat this as a way out of "
            "a tight spot. Nothing here is financial advice."
        )
        return "ok"

    # -----------------------------------------------------------------
    # 1a. "I already have an account". This used to fall through to the
    #     onboarding script, which is what happened on Jordan's test.
    # -----------------------------------------------------------------
    HAVE_ACCOUNT = ("i have an account", "i already have an account", "already have an account",
                    "i'm already a member", "im already a member", "already a member",
                    "i am a member", "i'm a member", "im a member", "i have account",
                    "already signed up", "i already signed up", "i'm signed up",
                    "i've onboarded", "ive onboarded", "already onboarded", "account")
    if any(w in text for w in HAVE_ACCOUNT) and not photos:
        if is_member:
            who = (member.get("name") or "").split(" ")[0] or first_name
            reply(f"You do{', ' + who if who else ''}, and I can see it here.\n\n"
                  f"Your access code: {member['access_code']}\n"
                  f"Log in: {SITE}/unlock\n\n"
                  f"All your group links: {SITE}/my-signals\n\n"
                  f"What did you need? Say \"my signals\" for your links, or just ask.")
            return "ok"
        reply("Let me find it. What phone number did you sign up with?\n\n"
              "Send it here and I'll pull your account up. If you'd rather not, message us on "
              f"the website at {SITE}/messages and we'll sort it.")
        set_bot_state(chat_id, state="awaiting_phone")
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
                set_bot_state(chat_id, state="awaiting_onboard_name")
                reply("Both screenshots received, thank you!\n\n"
                      "Two quick things while our team checks them.\n\n"
                      "First, what's your full name? It needs to match the name on your broker "
                      "account so we can tie the two together.")
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
    if session.get("admin") and not session.get("member_id"):
        # Admin has no member account of their own, so send them somewhere useful
        # rather than showing an empty one.
        return redirect(url_for("admin"))
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
    Real signals, real member results, real quotes. The disclaimer is built into
    this page rather than tucked in the footer, because a page showing winning
    trades is exactly where someone needs to read it.
    """
    signals = "".join(
        f'<div class="sig-example">'
        f'<span class="sig-group">{esc(x["group"])}</span>'
        f'<p class="sig-call">{esc(x["call"])}</p>'
        f'<ul class="sig-out">'
        + "".join(f'<li>{esc(o)}</li>' for o in x["outcome"]) +
        f'</ul></div>'
        for x in SIGNAL_EXAMPLES
    )

    # Accepted screenshots stream from Telegram, so nothing needs uploading
    shared = [{"image": f'/shared/{p["id"]}/image/0',
               "caption": (p.get("body") or "Shared by a member").strip()[:120],
               "alt": "Member submitted result"}
              for p in published("result") if (p.get("file_ids") or "").strip()]
    gallery_items = shared + RESULTS_ITEMS

    if gallery_items:
        gallery = "".join(
            f'<figure class="shot">'
            f'<img src="{esc(r["image"])}" alt="{esc(r.get("alt") or r.get("caption") or "Member result")}" loading="lazy">'
            f'<figcaption>{esc(r.get("caption") or "")}</figcaption></figure>'
            for r in gallery_items
        )
        gallery_section = f"""
<section class="tinted tinted-edge">
  <div class="wrap" style="max-width: 900px;">
    <div class="section-head" style="max-width:100%;">
      <span class="eyebrow">Member screenshots</span>
      <h2>Straight from their accounts</h2>
    </div>
    <div class="shots">{gallery}</div>
    <p class="risk-note">These are individual members' own accounts, at their own position
       sizes, over the days shown. Your results will not be the same. Some members lose money.</p>
  </div>
</section>"""
    else:
        # Nothing to show yet, so show nothing rather than an empty band.
        gallery_section = ""

    # Approved submissions sit alongside the quotes we already had, newest first
    live = [{"quote": p["body"],
             "who": (p.get("member_name") or "Member").strip().split(" ")[0] or "Member"}
            for p in published("feedback") if (p.get("body") or "").strip()]
    quotes = "".join(
        f'<blockquote class="say"><p>{esc(f_["quote"])}</p>'
        f'<cite>{esc(f_.get("who") or "Member")}</cite></blockquote>'
        for f_ in live + MEMBER_FEEDBACK
    )

    content = f"""
<section class="hero page-hero" style="padding-bottom: 40px;">
  <div class="wrap" style="grid-template-columns: 1fr; max-width: 860px;">
    <div>
      <span class="eyebrow">Results</span>
      <h1>The wins and<br>the <em>losses.</em></h1>
      <p class="lede">Real signals from the groups and real messages from members.
         Posted as they happened, not curated after the fact.</p>
    </div>
  </div>
</section>

<section class="risk-band">
  <div class="wrap" style="max-width: 860px;">
    <p><strong>Read this first.</strong> {RESULTS_DISCLAIMER}</p>
  </div>
</section>

<section>
  <div class="wrap" style="max-width: 900px;">
    <div class="section-head" style="max-width:100%;">
      <span class="eyebrow">From the groups</span>
      <h2>Signals as they went out</h2>
      <p>The call first, then what the group posted as it played out. Every one of these
         is a message that actually went to members.</p>
    </div>
    <div class="sig-grid">{signals}</div>
    <p class="risk-note">These are individual trades on particular days. Other trades on other
       days went differently, including trades that lost. A signal is not a prediction.</p>
  </div>
</section>

{gallery_section}

<section id="say">
  <div class="wrap" style="max-width: 900px;">
    <div class="section-head" style="max-width:100%;" id="say">
      <span class="eyebrow">In their words</span>
      <h2>What members say</h2>
    </div>
    <div class="says">{quotes}</div>
    <p class="risk-note">Real messages from real members, shared with permission. They describe
       one person's experience, not a typical result or anything you should expect.</p>
  </div>
</section>

<section class="band-dark">
  <div class="wrap" style="max-width: 760px; text-align: center;">
    <h2 style="font-size: 26px; margin-bottom: 14px;">Learn it before you lean on it</h2>
    <p style="margin-bottom: 24px;">The free course teaches you what the levels mean, so you're
       following a trade you understand rather than copying numbers.</p>
    <div class="cta-row" style="justify-content:center;">
      <a href="/education/fundamentals" class="btn btn-primary">Start the free course</a>
      <a href="/onboarding" class="btn btn-ghost">Get set up</a>
    </div>
  </div>
</section>

<section class="risk-band">
  <div class="wrap" style="max-width: 860px;">
    <p>{RESULTS_DISCLAIMER}</p>
  </div>
</section>
"""
    return render_template_string(base_layout("Results", content, ""))


# One board, and it belongs to the women in the Circle. Everyone else has the
# bot and the message chat on the website, which is where support already lives.
SPACES = {
    "her": {
        "path": "/her/hub",
        "nav": "community",
        "eyebrow": "Female Wealth",
        "title": "The women's board.",
        "blurb": ("Your space to post, ask and talk properly. Anything you share here stays "
                  "between the women in the Circle. The Telegram group is still there for "
                  "results and day to day chat, this is for the conversations worth keeping."),
        "guide_title": "How we use this space",
        "guide": [
            "Ask the question you think is too obvious. Every woman here started where you are, and the ones who ask are the ones who get good.",
            "Bring the wins and the bad weeks. A red week you talk through is worth more than a green one you keep quiet about.",
            "Talk money properly. Earning it, keeping it, growing it, asking for more of it. Most of us were raised not to, so we do it here.",
            "Say hello. Half of what this is for is knowing other women building the same thing.",
        ],
        "guide_not": ("What we're protecting: somewhere you can be honest without it being "
                      "repeated. So nothing here leaves, no selling to each other, no outside "
                      "signals or referral links, and no unkindness. Anything that breaks that "
                      "gets removed."),
        "prompt": "Something on your mind, a win, or a question",
        "empty": ("Nothing here yet. Start the first conversation, it can be a question, a win, "
                  "or something you're wrestling with."),
        "cta": "Start a conversation",
        "post_label": "Post",
    },
}


@app.route("/her/journal", methods=["GET", "POST"])
def her_journal():
    """
    A page a day. One question, her answer, saved and hers alone. Reads like a
    book because a form would feel like homework, and this is meant to feel
    like something you'd want to sit with.
    """
    if not is_verified():
        return redirect(url_for("unlock"))
    if not has_access("her") and not session.get("admin"):
        return redirect("/community")

    member_id = session.get("member_id")
    today = date.today()

    try:
        day = date.fromisoformat(request.values.get("day", "")) if request.values.get("day") else today
    except Exception:
        day = today
    if day > today:
        day = today

    prompt = prompt_for(day)

    if request.method == "POST":
        if member_id:
            save_journal_entry(member_id, day, prompt, request.form.get("body"))
        return redirect(f"/her/journal?day={day.isoformat()}&ok=" + quote("Saved. This page is yours."))

    body = get_journal_entry(member_id, day) if member_id else ""
    written = journal_written_days(member_id) if member_id else set()

    prev_day = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    can_next = next_day <= today

    if day == today:
        day_label = "Today"
    elif day == today - timedelta(days=1):
        day_label = "Yesterday"
    else:
        day_label = day.strftime("%A")

    nice_date = day.strftime("%-d %B %Y") if os.name != "nt" else day.strftime("%d %B %Y")

    # a short run of recent days, so she can see the streak building
    dots = ""
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        cls = "on" if d.isoformat() in written else ""
        cls += " here" if d == day else ""
        dots += f'<a href="/her/journal?day={d.isoformat()}" class="jd {cls}" title="{d.isoformat()}"></a>'

    content = f"""
<section style="padding: 40px 0 70px;">
  <div class="wrap" style="max-width: 720px;">
    <a href="/her" class="inline-link" style="font-size:13px;">← Back to Female Wealth</a>

    <div class="section-head" style="max-width:100%; margin:22px 0 10px;">
      <span class="eyebrow">The Daily Page</span>
      <h1 style="font-size:32px; margin:10px 0 10px;">One page, every day.</h1>
      <p style="max-width:520px;">One question a day. Write as much or as little as you like.
         Nobody sees this but you, not us and not the other women.</p>
    </div>

    <div class="jdots">{dots}</div>
    {flash_banner()}

    <div class="book">
      <div class="book-spine"></div>
      <div class="page">
        <div class="page-head">
          <span class="page-day">{day_label}</span>
          <span class="page-date">{nice_date}</span>
        </div>

        <p class="page-prompt">{esc(prompt)}</p>

        <form method="POST" action="/her/journal?day={day.isoformat()}">
          <textarea name="body" class="page-lines" rows="12"
                    placeholder="Start anywhere. It doesn't have to be tidy.">{esc(body or "")}</textarea>
          <div class="page-foot">
            <button type="submit" class="btn btn-primary btn-sm">Save this page</button>
            <span class="hint">Only you can read this.</span>
          </div>
        </form>
      </div>
    </div>

    <div class="turn">
      <a href="/her/journal?day={prev_day.isoformat()}" class="turn-btn">← The page before</a>
      {'<a href="/her/journal?day=' + next_day.isoformat() + '" class="turn-btn">The next page →</a>'
       if can_next else '<span class="turn-btn is-off">Tomorrow&#39;s page opens tomorrow</span>'}
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("The Daily Page", content, "community", theme="plum"))


@app.route("/her/videos")
def her_videos():
    """
    Training videos for the Circle. Embedded from YouTube or Vimeo rather than
    uploaded, so they stream properly and the app stays small.
    """
    if not is_verified():
        return redirect(url_for("unlock"))
    if not has_access("her") and not session.get("admin"):
        return redirect("/community")

    videos = get_her_videos() or HER_VIDEOS
    if videos:
        items = "".join(
            f'<div class="vid">'
            f'<div class="vid-frame"><iframe src="{esc(v["embed"])}" title="{esc(v["title"])}" '
            f'loading="lazy" allowfullscreen '
            f'allow="accelerometer; clipboard-write; encrypted-media; picture-in-picture"></iframe></div>'
            f'<h3>{esc(v["title"])}</h3>'
            + (f'<p>{esc(v["blurb"])}</p>' if v.get("blurb") else "")
            + '</div>'
            for v in videos
        )
        body = f'<div class="vid-grid">{items}</div>'
    else:
        body = ('<div class="callout">No videos up yet. When a training video is added it '
                'appears here, and only members of the Circle can see it.</div>')

    content = f"""
<section style="padding: 48px 0 70px;">
  <div class="wrap" style="max-width: 900px;">
    <a href="/her" class="inline-link" style="font-size: 13px;">← Back to Female Wealth</a>
    <span class="eyebrow" style="margin-top: 20px;">Female Wealth</span>
    <h1 style="font-size: 34px; margin: 10px 0 12px;">Training videos</h1>
    <p style="color: var(--ink-dim); margin-bottom: 30px; max-width: 560px;">
      Recorded sessions and walkthroughs, yours to watch back whenever you need them.
    </p>
    <details class="guide" style="margin-bottom:30px;">
      <summary>What's in here</summary>
      <ul>
        <li>Sessions recorded by us, walking through the things that are easier shown than written.</li>
        <li>Setups, the platform, and the mindset work, in your own time.</li>
        <li>Yours to watch back as often as you like. Nothing expires, nothing is missed.</li>
      </ul>
      <p class="guide-not">These are for the women in the Circle, so please keep the links here.
         If there's something you want covered, ask on the board and we'll record it.</p>
    </details>
    {body}
  </div>
</section>
"""
    return render_template_string(base_layout("Training videos", content, "community", theme="plum"))


@app.route("/her/hub")
def her_hub():
    """
    The women only board. Same shape as the open hub, but locked to Female
    Wealth members so it stays the private space it is meant to be.
    """
    if not is_verified():
        return redirect(url_for("unlock"))
    if not has_access("her") and not session.get("admin"):
        content = """
<section style="padding: 90px 0;">
  <div class="wrap" style="max-width: 580px; text-align: center;">
    <div class="ring-mark" style="margin: 0 auto 24px;"><span>🔒</span></div>
    <span class="eyebrow">Women only</span>
    <h1 style="font-size: 30px; margin: 12px 0 18px;">Female Wealth members only</h1>
    <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 28px;">
      This board is part of the Wealth Circle. Request access and we'll get you in.
    </p>
    <a href="/community" class="btn btn-primary">Request access</a>
  </div>
</section>
"""
        return render_template_string(base_layout("Female Wealth", content, "community", theme="plum"))
    return render_hub("her")


@app.route("/admin/hub/<int:post_id>/video/<action>", methods=["POST"])
def admin_hub_video(post_id, action):
    """Approve or strip a video a member attached to a board post."""
    if not session.get("admin"):
        return redirect(url_for("admin"))
    conn = get_db()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                if action == "approve":
                    cur.execute("UPDATE hub_posts SET video_ok=TRUE WHERE id=%s", (post_id,))
                else:
                    cur.execute("UPDATE hub_posts SET video_url=NULL, video_ok=FALSE WHERE id=%s",
                                (post_id,))
        except Exception:
            pass
        finally:
            conn.close()
    msg = "Video is live for members." if action == "approve" else "Video removed from the post."
    return redirect(f"/hub/{post_id}?ok=" + quote(msg))


@app.route("/admin/videos", methods=["GET", "POST"])
def admin_videos():
    """
    Add and remove Female Wealth training videos without touching the code.
    Paste a YouTube or Vimeo link and it appears on the members' page.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    if request.method == "POST":
        vid = add_her_video(request.form.get("title"),
                            request.form.get("blurb"),
                            request.form.get("url"))
        if vid:
            return redirect("/admin/videos?ok=" + quote("Video added, it's live for members now."))
        return redirect("/admin/videos?err=" +
                        quote("Needs a title and a YouTube or Vimeo link. Other links aren't accepted."))

    vids = get_her_videos()
    if vids:
        rows = "".join(
            f'<div class="adm-panel" style="margin-bottom:12px;">'
            f'<div class="adm-card-top"><span class="adm-card-name">{esc(v["title"])}</span></div>'
            + (f'<p class="hint" style="margin:6px 0 10px;">{esc(v["blurb"])}</p>' if v.get("blurb") else "")
            + f'<p class="hint" style="margin:0 0 12px; word-break:break-all;">{esc(v["embed"])}</p>'
            f'<form method="POST" action="/admin/videos/{v["id"]}/delete" '
            f"onsubmit=\"return confirm('Remove this video?');\">"
            f'<button type="submit" class="btn btn-danger btn-sm">Remove</button></form></div>'
            for v in vids
        )
    else:
        rows = ('<div class="adm-panel"><h3>No videos yet</h3>'
                '<p class="hint" style="margin:0;">Add one below and it appears for Female Wealth '
                'members straight away.</p></div>')

    content = f"""
<section style="padding: 44px 0 70px;">
  <div class="wrap" style="max-width: 760px;">
    <h1 style="font-size: 30px; margin: 0 0 8px;">Female Wealth videos</h1>
    <p style="color: var(--ink-dim); margin-bottom: 22px;">
      Only members of the Circle can see these. Upload to YouTube as unlisted, or Vimeo,
      then paste the link here.
    </p>
    {flash_banner()}

    <div class="form-panel" style="max-width:100%; margin-bottom:26px;">
      <h3 style="font-size:17px; margin-bottom:12px;">Add a video</h3>
      <form method="POST" action="/admin/videos">
        <label>Title</label>
        <input type="text" name="title" required maxlength="160" placeholder="Reading the morning setup">
        <label>Short description (optional)</label>
        <input type="text" name="blurb" maxlength="400" placeholder="What this one covers">
        <label>YouTube or Vimeo link</label>
        <input type="url" name="url" required placeholder="https://youtu.be/...">
        <button type="submit" style="margin-top:16px;">Add video</button>
      </form>
    </div>

    {rows}
  </div>
</section>
"""
    return render_template_string(admin_layout("Videos", content, "videos"))


@app.route("/admin/videos/<int:vid>/delete", methods=["POST"])
def admin_video_delete(vid):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    delete_her_video(vid)
    return redirect("/admin/videos?ok=" + quote("Video removed."))


@app.route("/admin/submissions")
def admin_submissions():
    """
    Everything members have sent in, waiting on a yes or no. Accepting is what
    publishes it, so nothing a member sends reaches the site by accident.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    view = request.args.get("view", "pending")
    if view not in ("pending", "accepted", "declined"):
        view = "pending"
    subs = get_submissions(view)

    SECTION_CHOICES = [
        ("signals_gold", "Gold signals"),
        ("signals_currency", "Extra signals"),
        ("fundamentals", "Trading Fundamentals"),
        ("advanced", "Advanced Chart Reading"),
        ("her", "Female Wealth"),
        ("general", "General"),
    ]

    rows = []
    for sub in subs:
        files = [f for f in (sub.get("file_ids") or "").split(",") if f]
        if files:
            shots = "".join(
                f'<a href="/admin/submissions/{sub["id"]}/image/{i}" target="_blank" rel="noopener">'
                f'<img src="/admin/submissions/{sub["id"]}/image/{i}" alt="Submitted result" '
                f'style="max-width:220px; border:1px solid var(--line); border-radius:10px;"></a>'
                for i in range(len(files))
            )
            media = f'<div style="display:flex; gap:10px; flex-wrap:wrap; margin:12px 0;">{shots}</div>'
        else:
            media = ""

        who = (f'<a href="/admin/member/{sub["member_id"]}" class="inline-link">'
               f'{esc(sub.get("member_name") or "unknown")}</a>'
               if sub.get("member_id") else "not linked to an account")

        opts = "".join(
            f'<option value="{k}" {"selected" if sub.get("section") == k else ""}>{v}</option>'
            for k, v in SECTION_CHOICES
        )

        if view == "pending":
            actions = (
                f'<form method="POST" action="/admin/submissions/{sub["id"]}/decide">'
                f'<div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:12px;">'
                f'<select name="kind" style="background:var(--bg); border:1px solid var(--line); '
                f'color:var(--ink); padding:9px 12px; border-radius:9px; font-size:13px;">'
                f'<option value="feedback" {"selected" if sub.get("kind") == "feedback" else ""}>Feedback</option>'
                f'<option value="result" {"selected" if sub.get("kind") == "result" else ""}>Result</option>'
                f'</select>'
                f'<select name="section" style="background:var(--bg); border:1px solid var(--line); '
                f'color:var(--ink); padding:9px 12px; border-radius:9px; font-size:13px;">{opts}</select>'
                f'<button type="submit" name="status" value="accepted" class="btn btn-primary btn-sm">'
                f'Accept and publish</button>'
                f'<button type="submit" name="status" value="declined" class="btn btn-ghost btn-sm">'
                f'Decline</button>'
                f'</div></form>'
            )
        else:
            label = SECTION_LABELS.get(sub.get("section") or "", sub.get("section") or "general")
            actions = (
                f'<div style="margin-top:12px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">'
                f'<span class="pill">{esc(str(sub.get("kind") or ""))}</span>'
                f'<span class="pill">{esc(str(label))}</span>'
                f'<form method="POST" action="/admin/submissions/{sub["id"]}/decide" style="display:inline;">'
                f'<input type="hidden" name="status" value="pending">'
                f'<button type="submit" class="btn btn-ghost btn-sm">Put back in the queue</button>'
                f'</form></div>'
            )

        rows.append(
            f'<div class="adm-panel" style="margin-bottom:14px;">'
            f'<div class="adm-meta">From {who} · {time_ago(sub.get("created_at"))} · '
            f'via {esc(str(sub.get("source") or "bot"))}</div>'
            + (f'<p style="font-size:15px; line-height:1.7; margin:12px 0;">'
               f'"{esc(sub.get("body") or "")}"</p>' if (sub.get("body") or "").strip() else "")
            + media + actions + '</div>'
        )

    body = "".join(rows) or (
        '<div class="adm-panel"><h3>Nothing here</h3>'
        '<p class="hint" style="margin:0;">Feedback and results appear here as members send them in.</p>'
        '</div>')

    content = f"""
<section style="padding: 44px 0 70px;">
  <div class="wrap" style="max-width: 860px;">
    <h1 style="font-size: 30px; margin: 0 0 8px;">Feedback &amp; results</h1>
    <p style="color: var(--ink-dim); margin-bottom: 22px;">
      Nothing a member sends goes on the site until you accept it here.
    </p>
    <div class="adm-bar">
      <a href="/admin/submissions?view=pending" class="{'on' if view == 'pending' else ''}">Waiting</a>
      <a href="/admin/submissions?view=accepted" class="{'on' if view == 'accepted' else ''}">Published</a>
      <a href="/admin/submissions?view=declined" class="{'on' if view == 'declined' else ''}">Declined</a>
    </div>
    {flash_banner()}
    {body}
  </div>
</section>
"""
    return render_template_string(admin_layout("Feedback", content, "feedback"))


@app.route("/admin/submissions/<int:sub_id>/decide", methods=["POST"])
def admin_submission_decide(sub_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    status = request.form.get("status", "pending")
    if status not in ("accepted", "declined", "pending"):
        status = "pending"
    decide_submission(sub_id, status,
                      kind=request.form.get("kind"),
                      section=request.form.get("section"))
    msg = {"accepted": "Published to the site.",
           "declined": "Declined, nothing published.",
           "pending": "Back in the queue."}[status]
    return redirect("/admin/submissions?ok=" + quote(msg))


@app.route("/admin/submissions/<int:sub_id>/image/<int:n>")
def admin_submission_image(sub_id, n):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    return _submission_image(sub_id, n)


@app.route("/shared/<int:sub_id>/image/<int:n>")
def shared_submission_image(sub_id, n):
    """Public, but only for submissions that have actually been accepted."""
    sub = get_submission(sub_id)
    if not sub or sub.get("status") != "accepted":
        return "", 404
    return _submission_image(sub_id, n)


def _submission_image(sub_id, n):
    sub = get_submission(sub_id)
    if not sub:
        return "", 404
    files = [f for f in (sub.get("file_ids") or "").split(",") if f]
    if n >= len(files):
        return "", 404
    url = telegram_file_url(files[n])
    if not url:
        return "", 404
    try:
        r = requests.get(url, timeout=20)
        return Response(r.content, mimetype=r.headers.get("Content-Type", "image/jpeg"))
    except Exception:
        return "", 502


@app.route("/admin/hub")
def admin_hub():
    """
    Moderation for both boards in one place. Members only ever see first names,
    but here every name links to the full profile, so if something is posted
    that shouldn't be you can go straight to that person and act on it.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    space = "her"
    cfg = SPACES[space]
    posts = get_hub_posts(limit=200, include_hidden=True, space=space)

    if posts:
        rows = []
        for p in posts:
            flags = []
            if p.get("pinned"):
                flags.append('<span class="pill gold">Pinned</span>')
            if p.get("hidden"):
                flags.append('<span class="pill warn">Hidden from members</span>')
            if p.get("locked"):
                flags.append('<span class="pill">Comments off</span>')

            author = (f'<a href="/admin/member/{p["member_id"]}" class="inline-link">'
                      f'{esc(p.get("author_name") or "unknown")}</a>'
                      if p.get("member_id") else "unknown")

            rows.append(
                f'<div class="adm-panel" style="margin-bottom:14px;">'
                f'<div class="adm-card-top">'
                f'<a href="/admin/hub/post/{p["id"]}" class="adm-card-name" style="text-decoration:none;">'
                f'{esc(p["title"])}</a></div>'
                f'<div class="adm-meta">By {author} · {time_ago(p.get("created_at"))} · '
                f'{p.get("reply_count") or 0} replies</div>'
                f'<div style="margin:6px 0 12px;">{"".join(flags)}</div>'
                f'<p style="font-size:14px; color:var(--ink-dim); margin:0 0 14px;">'
                f'{esc((p["body"] or "")[:220])}'
                + ("..." if len(p["body"] or "") > 220 else "") + '</p>'
                f'<div style="display:flex; gap:8px; flex-wrap:wrap;">'
                f'{_hub_btn(p["id"], "pin", "Unpin" if p.get("pinned") else "Pin to top", space)}'
                f'{_hub_btn(p["id"], "unhide" if p.get("hidden") else "hide", "Show to members" if p.get("hidden") else "Hide from members", space)}'
                f'{_hub_btn(p["id"], "unlock" if p.get("locked") else "lock", "Turn comments back on" if p.get("locked") else "Turn comments off", space)}'
                f'<a href="/admin/hub/post/{p["id"]}" class="btn btn-ghost btn-sm">Open thread</a>'
                f'{_hub_btn(p["id"], "delete", "Delete for good", space, danger=True)}'
                f'</div></div>'
            )
        body = "".join(rows)
    else:
        body = ('<div class="adm-panel"><h3>Nothing posted yet</h3>'
                '<p class="hint" style="margin:0;">Posts appear here as soon as members start using '
                'the board.</p></div>')

    content = f"""
<section style="padding: 48px 0 70px;">
  <div class="wrap" style="max-width: 800px;">
    <a href="/admin" class="inline-link" style="font-size: 13px;">← Back to members</a>
    <h1 style="font-size: 30px; margin: 16px 0 8px;">Board moderation</h1>
    <p style="color: var(--ink-dim); margin-bottom: 18px;">
      Members see first names only. Here every name links to their full profile.
    </p>
    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:22px;">
      <a href="/her/hub" class="btn btn-primary btn-sm">Post on the board</a>
      <a href="/her/hub" class="btn btn-ghost btn-sm" target="_blank" rel="noopener">
        See it as a member opens it &#8599;</a>
    </div>

    {flash_banner()}
    {body}
  </div>
</section>
"""
    return render_template_string(admin_layout("Boards", content, "boards"))


def _hub_btn(post_id, action, label, space, danger=False):
    confirm = (' onsubmit="return confirm(\'Delete this post and every reply on it? '
               'This cannot be undone.\');"' if action == "delete" else "")
    cls = "btn btn-danger btn-sm" if danger else "btn btn-ghost btn-sm"
    return (f'<form method="POST" action="/admin/hub/{post_id}/{action}" '
            f'style="display:inline;"{confirm}>'
            f'<input type="hidden" name="space" value="{space}">'
            f'<button type="submit" class="{cls}">{label}</button></form>')


@app.route("/admin/hub/<int:post_id>/<action>", methods=["POST"])
def admin_hub_action(post_id, action):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    space = "her"

    if action == "pin":
        post, _ = get_hub_post(post_id, include_hidden=True)
        set_hub_pinned(post_id, not (post or {}).get("pinned"))
        msg = "Pinned."
    elif action == "hide":
        set_hub_hidden("post", post_id, True)
        msg = "Hidden from members. Nothing was deleted."
    elif action == "unhide":
        set_hub_hidden("post", post_id, False)
        msg = "Visible to members again."
    elif action == "lock":
        set_hub_locked(post_id, True)
        msg = "Comments turned off for that post."
    elif action == "unlock":
        set_hub_locked(post_id, False)
        msg = "Comments turned back on."
    elif action == "delete":
        delete_hub_item("post", post_id)
        msg = "Deleted for good, along with its replies."
    else:
        msg = "Nothing changed."

    return redirect(f"/admin/hub?space={space}&ok=" + quote(msg))


@app.route("/hub/<int:post_id>/delete-reply", methods=["POST"])
def hub_delete_reply(post_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    delete_hub_item("reply", int(request.form.get("id", 0)))
    return redirect(f"/hub/{post_id}?ok=" + quote("Comment deleted."))


@app.route("/admin/hub/post/<int:post_id>")
def admin_hub_post(post_id):
    """
    Read and moderate a whole conversation without leaving the back office.
    Shows real names linked to profiles, which the member view never does.
    """
    if not session.get("admin"):
        return redirect(url_for("admin"))

    post, replies = get_hub_post(post_id, include_hidden=True)
    if not post:
        return redirect("/admin/hub?err=" + quote("That post has gone."))

    space = "her"
    cfg = SPACES["her"]

    def row(item, kind):
        author = (f'<a href="/admin/member/{item["member_id"]}" class="inline-link">'
                  f'{esc(item.get("author_name") or "unknown")}</a> '
                  f'<span class="hub-meta">#{item["member_id"]}</span>'
                  if item.get("member_id") else '<strong>Inner Circle team</strong>')
        flags = '<span class="pill warn">Hidden</span>' if item.get("hidden") else ""
        tools = ""
        if kind == "reply":
            tools = (
                f'<form method="POST" action="/admin/hub/reply/{item["id"]}/'
                f'{"unhide" if item.get("hidden") else "hide"}" style="display:inline;">'
                f'<input type="hidden" name="post_id" value="{post_id}">'
                f'<button type="submit" class="btn btn-ghost btn-sm">'
                f'{"Show" if item.get("hidden") else "Hide"}</button></form>'
                f'<form method="POST" action="/admin/hub/reply/{item["id"]}/delete" '
                f'style="display:inline; margin-left:6px;" '
                f"onsubmit=\"return confirm('Delete this comment for good?');\">"
                f'<input type="hidden" name="post_id" value="{post_id}">'
                f'<button type="submit" class="btn btn-danger btn-sm">Delete</button></form>'
            )
        return (f'<div class="hub-bubble{" is-team" if item.get("from_team") else ""}" '
                f'style="{"opacity:.55;" if item.get("hidden") else ""}">'
                f'<div class="hub-meta" style="margin-bottom:8px;">{author} · '
                f'{time_ago(item.get("created_at"))} {flags} {tools}</div>'
                f'<div>{esc(item["body"]).replace(chr(10), "<br>")}</div></div>')

    thread = row(post, "post") + "".join(row(r, "reply") for r in replies)

    content = f"""
<section style="padding: 40px 0 70px;">
  <div class="wrap" style="max-width: 780px;">
    <a href="/admin/hub?space={space}" class="inline-link" style="font-size: 13px;">
      ← Back to {esc(cfg["eyebrow"])}</a>
    <h1 style="font-size: 26px; margin: 16px 0 6px;">{esc(post["title"])}</h1>
    <p style="color: var(--ink-dim); font-size: 13.5px; margin-bottom: 22px;">
      On {esc(cfg["eyebrow"])} · {len(replies)} {"reply" if len(replies) == 1 else "replies"}
    </p>

    {flash_banner()}

    <div class="adm-panel" style="margin-bottom: 22px;">
      <h3>This post</h3>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">
        {_hub_btn(post_id, "pin", "Unpin" if post.get("pinned") else "Pin to top", space)}
        {_hub_btn(post_id, "unhide" if post.get("hidden") else "hide", "Show to members" if post.get("hidden") else "Hide from members", space)}
        {_hub_btn(post_id, "unlock" if post.get("locked") else "lock", "Comments back on" if post.get("locked") else "Turn comments off", space)}
        {_hub_btn(post_id, "delete", "Delete for good", space, danger=True)}
      </div>
    </div>

    {thread}

    <div class="form-panel" style="max-width:100%; margin-top: 26px;">
      <h3 style="font-size: 17px; margin-bottom: 12px;">Reply as Inner Circle</h3>
      <form method="POST" action="/hub/{post_id}/reply">
        <input type="hidden" name="back" value="/admin/hub/post/{post_id}">
        <textarea name="body" maxlength="4000" required rows="4"
                  placeholder="Your reply shows to members badged as the team."></textarea>
        <button type="submit" style="margin-top: 14px;">Post reply</button>
      </form>
    </div>
  </div>
</section>
"""
    return render_template_string(admin_layout("Thread", content, "boards"))


@app.route("/admin/hub/reply/<int:reply_id>/<action>", methods=["POST"])
def admin_hub_reply_action(reply_id, action):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    post_id = request.form.get("post_id", "")
    if action == "delete":
        delete_hub_item("reply", reply_id)
        msg = "Comment deleted."
    elif action == "hide":
        set_hub_hidden("reply", reply_id, True)
        msg = "Comment hidden from members."
    else:
        set_hub_hidden("reply", reply_id, False)
        msg = "Comment visible again."
    return redirect(f"/admin/hub/post/{post_id}?ok=" + quote(msg))


@app.route("/hub")
def hub():
    """
    The open board was removed. Female Wealth members go to theirs, everyone
    else to the community page, so an old link never dead ends.
    """
    if has_access("her") or session.get("admin"):
        return redirect("/her/hub")
    return redirect("/community")


def render_hub(space):
    cfg = SPACES[space]
    if cfg.get("guide"):
        items = "".join(f"<li>{esc(x)}</li>" for x in cfg["guide"])
        guide_block = (
            f'<details class="guide"><summary>{esc(cfg.get("guide_title") or "What this is for")}</summary>'
            f'<ul>{items}</ul>'
            f'<p class="guide-not">{esc(cfg.get("guide_not") or "")}</p></details>'
        )
    else:
        guide_block = ""
    posts = get_hub_posts(space=space)
    if posts:
        rows = "".join(
            f'<a class="hub-card" href="/hub/{p["id"]}">'
            + ('<span class="pill gold">Pinned</span>' if p.get("pinned") else "")
            + f'<h3>{esc(p["title"])}</h3>'
            f'<p>{esc((p["body"] or "")[:150])}'
            + ("..." if len(p["body"] or "") > 150 else "")
            + f'</p>'
            + ('<span class="pill warn">Video to check</span>'
               if p.get("video_url") and not p.get("video_ok")
               else ('<span class="pill">Video</span>' if p.get("video_url") else ""))
            + '<div class="hub-meta">' + " · ".join(filter(None, [
                esc(hub_display_name({"name": p.get("author_name")})),
                time_ago(p.get("last_activity") or p.get("created_at")),
                f'{p.get("reply_count") or 0} '
                f'{"reply" if (p.get("reply_count") or 0) == 1 else "replies"}',
              ])) + '</div>'
            f'</a>'
            for p in posts
        )
    else:
        rows = f'<div class="callout">{cfg["empty"]}</div>' 

    content = f"""
<section style="padding: 48px 0 20px;">
  <div class="wrap" style="max-width: 760px;">
    <span class="eyebrow">{cfg["eyebrow"]}</span>
    <h1 style="font-size: 34px; margin: 10px 0 12px;">{cfg["title"]}</h1>
    <p style="color: var(--ink-dim); font-size: 16px; max-width: 580px;">{cfg["blurb"]}</p>
    {guide_block}
  </div>
</section>

<section style="padding: 10px 0 70px;">
  <div class="wrap" style="max-width: 760px;">
    <div class="form-panel" style="max-width: 100%; margin-bottom: 34px;">
      <h3 style="font-size: 18px; margin-bottom: 6px;">{cfg["cta"]}</h3>
      <p style="color: var(--ink-dim); font-size: 13.5px; margin-bottom: 16px;">
        Your first name shows next to it, nothing else.
      </p>
      <form method="POST" action="/hub/new">
        <input type="hidden" name="space" value="{space}">
        <label>Title</label>
        <input type="text" name="title" maxlength="160" required
               placeholder="{cfg["prompt"]}">
        <label>Tell us a bit more</label>
        <textarea name="body" maxlength="4000" required rows="4"
                  placeholder="Any detail helps, what you've tried, what's confusing you."></textarea>
        <button type="submit" style="margin-top: 16px;">{cfg["post_label"]}</button>
      </form>
    </div>

    {flash_banner()}
    {rows}
  </div>
</section>
"""
    return render_template_string(base_layout(cfg["eyebrow"], content, cfg["nav"], theme="plum"))


@app.route("/hub/new", methods=["POST"])
def hub_new():
    if not is_verified():
        return redirect(url_for("unlock"))
    member_id = session.get("member_id")
    space = "her"
    if space == "her" and not (has_access("her") or session.get("admin")):
        return redirect("/community")
    post_id = create_hub_post(member_id, request.form.get("title"),
                              request.form.get("body"), space=space,
                              video_url=request.form.get("video_url"))
    if not post_id:
        return redirect(SPACES[space]["path"] + "?err=" +
                        quote("Add a title and a bit of detail, then post it."))
    # No alert when the team posts, we already know about it.
    if member_id and not session.get("admin"):
        member = get_member_by_id(member_id) or {}
        who = member.get("name") or "Member"
        board = SPACES[space]["eyebrow"]
        notify_admin(f"💬 NEW POST ON {board.upper()}\n\n"
                     f"From: {who} (#{member_id})\n"
                     f"{(request.form.get('title') or '')[:160]}\n\n"
                     f"Answer it: {SITE}/hub/{post_id}")
    return redirect(f"/hub/{post_id}")


@app.route("/hub/<int:post_id>")
def hub_post(post_id):
    if not is_verified():
        return redirect(url_for("unlock"))

    post, replies = get_hub_post(post_id)
    if not post:
        return redirect("/hub?err=" + quote("That question has gone."))

    is_admin = bool(session.get("admin"))
    space = "her"
    if space == "her" and not (has_access("her") or is_admin):
        return redirect("/community")
    back = SPACES["her"]

    def bubble(name, when, body, from_team, item_id=None, kind="reply", member_id=None):
        badge = ('<span class="pill on" style="margin-left:8px;">Inner Circle team</span>'
                 if from_team else "")
        # Members see a first name and nothing else. Admins get a link to the
        # profile, so anything posted can be traced back and acted on.
        who = esc(name)
        if is_admin and member_id and not from_team:
            who = (f'<a href="/admin/member/{member_id}" class="inline-link">{esc(name)}</a>'
                   f'<span class="hub-meta"> · #{member_id}</span>')
        tools = ""
        if is_admin and item_id:
            tools = (f'<form method="POST" action="/hub/{post_id}/hide" style="display:inline;">'
                     f'<input type="hidden" name="kind" value="{kind}">'
                     f'<input type="hidden" name="id" value="{item_id}">'
                     f'<button type="submit" class="btn btn-ghost btn-sm" '
                     f'style="margin-left:8px;">Hide</button></form>')
            if kind == "reply":
                tools += (f'<form method="POST" action="/hub/{post_id}/delete-reply" '
                          f'style="display:inline;" '
                          f"onsubmit=\"return confirm('Delete this comment for good?');\">"
                          f'<input type="hidden" name="id" value="{item_id}">'
                          f'<button type="submit" class="btn btn-danger btn-sm" '
                          f'style="margin-left:6px;">Delete</button></form>')
        stamp = time_ago(when)
        when_bit = f' · {stamp}' if stamp else ""
        return (f'<div class="hub-bubble{" is-team" if from_team else ""}">'
                f'<div class="hub-meta" style="margin-bottom:8px;">'
                f'<strong style="color:var(--ink);">{who}</strong>{badge}{when_bit}{tools}</div>'
                f'<div>{esc(body).replace(chr(10), "<br>")}</div></div>')

    thread = bubble(hub_display_name({"name": post.get("author_name")}),
                    post.get("created_at"), post["body"], False, post["id"], "post",
                    post.get("member_id"))
    if post.get("video_url"):
        # A member's video is checked before anyone else sees it. The link is
        # already restricted to YouTube and Vimeo, but that says nothing about
        # what's actually in the video.
        if post.get("video_ok"):
            thread += (
                f'<div class="hub-video">'
                f'<iframe src="{esc(post["video_url"])}" title="{esc(post["title"])}" loading="lazy"'
                f' allow="accelerometer; clipboard-write; encrypted-media; picture-in-picture"'
                f' referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></div>'
            )
        elif is_admin:
            thread += (
                f'<div class="callout" style="margin-bottom:14px;">'
                f'<strong>Video waiting on you.</strong> Members can\'t see it yet.<br>'
                f'<span class="hub-meta" style="word-break:break-all;">{esc(post["video_url"])}</span>'
                f'<div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:12px;">'
                f'<a href="{esc(post["video_url"])}" target="_blank" rel="noopener" '
                f'class="btn btn-ghost btn-sm">Watch it first</a>'
                f'<form method="POST" action="/admin/hub/{post_id}/video/approve" style="display:inline;">'
                f'<button type="submit" class="btn btn-primary btn-sm">Show it to members</button></form>'
                f'<form method="POST" action="/admin/hub/{post_id}/video/remove" style="display:inline;">'
                f'<button type="submit" class="btn btn-danger btn-sm">Remove it</button></form>'
                f'</div></div>')
        else:
            thread += ('<div class="callout" style="margin-bottom:14px;">'
                       'There\'s a video on this post. We check every one before it shows, '
                       'so give us a little while.</div>')
    thread += "".join(
        bubble("Inner Circle" if r.get("from_team") else
               hub_display_name({"name": r.get("author_name")}),
               r.get("created_at"), r["body"], r.get("from_team"), r["id"], "reply",
               r.get("member_id"))
        for r in replies
    )

    if post.get("locked"):
        reply_box = ('<div class="callout" style="margin-top:26px;">'
                     'Comments are closed on this one.</div>')
    else:
        reply_box = f"""
    <div class="form-panel" style="max-width: 100%; margin-top: 30px;">
      <h3 style="font-size: 17px; margin-bottom: 12px;">Add your answer</h3>
      <form method="POST" action="/hub/{post_id}/reply">
        <textarea name="body" maxlength="4000" required rows="4"
                  placeholder="Share what you know, or what worked for you."></textarea>
        <button type="submit" style="margin-top: 14px;">Post reply</button>
      </form>
    </div>"""

    admin_tools = ""
    if is_admin:
        admin_tools = (
            f'<form method="POST" action="/hub/{post_id}/pin" style="display:inline;">'
            f'<button type="submit" class="btn btn-ghost btn-sm">'
            f'{"Unpin" if post.get("pinned") else "Pin to the top"}</button></form>'
            f' <a href="/admin/hub?space={space}" class="inline-link" style="font-size:13px;">'
            f'Moderate the board</a>'
        )

    content = f"""
<section style="padding: 40px 0 70px;">
  <div class="wrap" style="max-width: 720px;">
    <a href="{back["path"]}" class="inline-link" style="font-size: 13px;">← Back to {back["eyebrow"]}</a>
    <h1 style="font-size: 28px; margin: 16px 0 6px;">{esc(post["title"])}</h1>
    <p style="color: var(--ink-dim); font-size: 13.5px; margin-bottom: 26px;">
      {post.get("reply_count") or 0} {"reply" if (post.get("reply_count") or 0) == 1 else "replies"}
      {admin_tools}
    </p>

    {flash_banner()}
    {thread}

    {reply_box}
  </div>
</section>
"""
    return render_template_string(base_layout(post["title"], content, back["nav"]))


@app.route("/hub/<int:post_id>/reply", methods=["POST"])
def hub_reply(post_id):
    if not is_verified():
        return redirect(url_for("unlock"))
    member_id = session.get("member_id")
    from_team = bool(session.get("admin"))
    post, _ = get_hub_post(post_id)
    if post and not (has_access("her") or from_team):
        return redirect("/community")
    if post and post.get("locked") and not from_team:
        return redirect(f"/hub/{post_id}?err=" + quote("Comments are closed on that one."))
    back = request.form.get("back") or f"/hub/{post_id}"
    if not add_hub_reply(post_id, member_id, request.form.get("body"), from_team):
        return redirect(f"{back}?err=" + quote("Write something first."))
    if not from_team and member_id:
        notify_admin(f"💬 NEW REPLY\n\nOn: {SITE}/hub/{post_id}")
    return redirect(back)


@app.route("/hub/<int:post_id>/hide", methods=["POST"])
def hub_hide(post_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    set_hub_hidden(request.form.get("kind", "reply"), int(request.form.get("id", 0)), True)
    return redirect(f"/hub/{post_id}?ok=" + quote("Hidden from the board."))


@app.route("/hub/<int:post_id>/pin", methods=["POST"])
def hub_pin(post_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    post, _ = get_hub_post(post_id, include_hidden=True)
    set_hub_pinned(post_id, not (post or {}).get("pinned"))
    return redirect(f"/hub/{post_id}")


@app.route("/admin/logout")
def admin_logout():
    """Leaves any member session alone, and vice versa. They're separate."""
    session.pop("admin", None)
    return redirect(url_for("home"))


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


def is_admin():
    return bool(session.get("admin"))


def is_verified():
    """
    Admin counts as verified everywhere. Being logged in as admin should never
    mean having to log in again as a member just to look at a member page.
    """
    return bool(session.get("member_id") or session.get("admin"))


def current_sections():
    """
    Read access fresh once per request, so a tick box change in admin applies
    straight away instead of waiting for someone to log out and back in.
    Admin holds every section, so nothing on the site is closed to them.
    """
    if session.get("admin"):
        return set(SECTION_KEYS)
    if not session.get("member_id"):
        return set()
    cached = getattr(g, "_member_sections", None)
    if cached is not None:
        return cached
    try:
        sections = get_member_sections(session.get("member_id"))
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
<section class="hero-dark">
  {HERO_CHART_SVG}
  <div class="wrap">
    <div class="hero-copy">
      <span class="hero-tag">Signals · Education · Support</span>
      <h1>Building wealth,<br>creating <em>freedom.</em></h1>
      <p class="lede">
        Most trading spaces hand you a signal and leave you to work out the rest.
        We do it the other way round: you learn what the trade is before you take it.
      </p>
      <p class="lede lede-rose">
        Alongside it sits Wealth Circle, our private community built for women.
      </p>
      <div class="cta-row">
        <a href="/onboarding" class="btn btn-primary">Start onboarding</a>
        <a href="/education" class="btn btn-ghost">See the curriculum</a>
      </div>
    </div>
  </div>
</section>

<section class="hero-ticker">
  <div class="wrap">
    <span><b>41</b> free lessons</span>
    <span><b>23</b> advanced lessons</span>
    <span><b>Gold</b> signals daily</span>
    <span><b>Women only</b> Wealth Circle</span>
  </div>
</section>

<section class="tinted tinted-edge">
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
      <span class="eyebrow">What's included</span>
      <h2>Inside you'll get</h2>
    </div>
    <div class="grid5 of-five">
      <div class="benefit"><div class="icon">✂</div><h3>Copy & Paste Signals</h3><p>Simple, actionable trades you can follow.</p></div>
      <div class="benefit"><div class="icon">◈</div><h3>Full Trading Guidance</h3><p>Step-by-step support to help you grow with confidence.</p></div>
      <div class="benefit"><div class="icon">○</div><h3>Exclusive Female Community</h3><p>Wealth Circle, a supportive space to learn, share and grow together, women only.</p></div>
      <div class="benefit"><div class="icon">★</div><h3>Trade Ideas From Experts</h3><p>Leverage the experience of professionals.</p></div>
      <div class="benefit"><div class="icon">📚</div><h3>Two Full Courses</h3>
        <p>Trading Fundamentals free with your account, and Advanced Chart Reading when you're ready.</p></div>
      <div class="benefit"><div class="icon">♥</div><h3>Ongoing Support</h3><p>Learn, ask, and level up every single day.</p></div>
    </div>
  </div>

  <div class="wrap" style="max-width: 980px; padding-top: 8px;">
    <div class="signal-showcase">
      <aside class="signal-card" aria-label="Example of how a signal arrives">
      <div class="signal-head">
        <span class="signal-live"><i></i>Illustrative example</span>
        <span class="signal-pair">XAUUSD</span>
      </div>
      <div class="signal-dir">BUY</div>
      <dl class="signal-levels">
        <div><dt>Entry</dt><dd>4392.20</dd></div>
        <div><dt>Stop loss</dt><dd class="is-stop">4384.60</dd></div>
        <div><dt>Take profit 1</dt><dd class="is-tp">4399.80</dd></div>
        <div><dt>Take profit 2</dt><dd class="is-tp">4406.40</dd></div>
        <div><dt>Suggested size</dt><dd>0.01 per £100</dd></div>
      </dl>
      <p class="signal-note">
        An illustrative example, not a live signal. Every real one arrives in this shape.
        Results are posted openly, wins and losses both.
      </p>
    </aside>

      <div class="signal-explain">
        <h3>This is what lands in your group</h3>
        <p>Every signal arrives with the levels already worked out: where to get in, where to get
           out if it goes wrong, and where to take profit. You copy the levels into MT5 exactly
           as they are.</p>
        <p>What the course adds is the <em>why</em>. Why that entry, why the stop sits there,
           why the size is what it is. That's the difference between following along and
           actually knowing what you're doing.</p>
        <a href="/education/fundamentals" class="inline-link">Start with the free course →</a>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="max-width: 820px;">
    <div class="section-head" style="max-width: 100%;">
      <span class="eyebrow">In detail</span>
      <h2>How each part works</h2>
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

<section class="band-dark">
  <div class="wrap" style="max-width: 900px;">
    <div class="section-head" style="max-width: 100%;">
      <span class="eyebrow">Why Inner Circle</span>
      <h2>Why this, and not just another signals group</h2>
    </div>
    <div class="compare">
      <div class="compare-col is-them">
        <h3>Most groups</h3>
        <p>Hand you a signal, expect you to know what to do with it, and disappear when you have
           questions. No structure, no education, no real community behind it.</p>
      </div>
      <div class="compare-col is-us">
        <h3>Inner Circle</h3>
        <p>Walks you through onboarding step by step, teaches you the fundamentals before you're ever
           expected to trade alone, and makes sure you always get an answer.</p>
      </div>
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
      <a class="benefit" href="/results#say" style="text-decoration:none; color:inherit;">
        <div class="icon">"</div>
        <h3>Member feedback</h3>
        <p>Real messages from members about the signals, the courses and the community.</p>
        <span class="inline-link" style="display:inline-block; margin-top:12px;">Read what members say →</span>
      </a>
      <a class="benefit" href="/results" style="text-decoration:none; color:inherit;">
        <div class="icon">📈</div>
        <h3>Signals as they went out</h3>
        <p>Real calls from the groups with what happened next, entry, stop and targets included.</p>
        <span class="inline-link" style="display:inline-block; margin-top:12px;">See the trades →</span>
      </a>
      <a class="benefit" href="/results" style="text-decoration:none; color:inherit;">
        <div class="icon">🧾</div>
        <h3>Member screenshots</h3>
        <p>Straight from their own accounts. Posted openly, wins and losses both.</p>
        <span class="inline-link" style="display:inline-block; margin-top:12px;">View the gallery →</span>
      </a>
    </div>
    <p class="risk-note">Past results are not a guide to future results. Trading carries risk and
       you can lose money.</p>
  </div>
</section>

<section class="band-rose">
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

<section id="support" class="band-dark">
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
<section class="hero page-hero" style="padding-bottom: 30px;">
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
<section class="hero page-hero" style="padding-bottom: 40px;">
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
<section class="hero page-hero" style="padding-bottom: 40px;">
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
        <p>Minimum deposit is £300. This goes into your own broker account, it's your money and you can withdraw it any time.</p>
        <p>Whatever you put in, the broker adds 50% on top as trading credit, free of charge. So:</p>
        <ul class="deposit-list">
          <li><strong>Deposit £300</strong> and you get £150 credit, leaving you £450 to trade with</li>
          <li><strong>Deposit £500</strong> and you get £250 credit, leaving you £750</li>
          <li><strong>Deposit £1,000</strong> and you get £500 credit, leaving you £1,500</li>
        </ul>
        <p class="hint" style="margin-top:10px;">The credit is there to trade with. Your own deposit stays yours and stays withdrawable.</p>
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
<section class="hero page-hero" style="padding-bottom: 30px;">
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
        <div class="diagram-wrap">{QUOTES_SVG}</div>
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
        <div class="diagram-wrap">{HISTORY_SVG}</div>
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
    <div class="reading">
      <div class="course-content" style="max-width: 100%;">
        {body_html}
      </div>
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
    course_reviews = "".join(
        f'<blockquote class="say"><p>{esc(r["quote"])}</p>'
        f'<cite>{esc(r["who"])}</cite></blockquote>' for r in FUNDAMENTALS_REVIEWS
    )
    if has_access("fundamentals"):
        fund_ctas = ('<a href="/education/fundamentals/0" class="btn btn-primary">Start Course</a>'
                     '<a href="/education/fundamentals/contents" class="btn btn-ghost">All lessons</a>')
    else:
        fund_ctas = ('<a href="/onboarding" class="btn btn-primary">Start Onboarding</a>'
                     '<a href="/unlock" class="btn btn-ghost">I have an access code</a>')
    content = f"""
<section class="hero page-hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">Free · 5 sections · {len(lessons) - 1} lessons</span>
      <h1>Trading<br>Fundamentals</h1>
      <p class="lede">Everything before and around your first trade, one lesson at a time.</p>
      <div class="cta-row">{fund_ctas}</div>
    </div>
  </div>
</section>

<section class="tinted tinted-edge">
  <div class="wrap" style="max-width: 900px;">
    <div class="section-head" style="max-width:100%;">
      <span class="eyebrow">From members</span>
      <h2>What people say</h2>
    </div>
    <div class="says">{course_reviews}</div>
    <p class="risk-note">Real reviews from students, shared with permission. They describe one
       person's experience of the course. Nothing here is financial advice, and past results are
       not a guide to future results.</p>
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
    course_reviews = "".join(
        f'<blockquote class="say"><p>{esc(r["quote"])}</p>'
        f'<cite>{esc(r["who"])}</cite></blockquote>' for r in ADVANCED_REVIEWS
    )
    if has_access("advanced"):
        adv_ctas = ('<a href="/education/advanced/0" class="btn btn-primary">Start Course</a>'
                    '<a href="/education/advanced/contents" class="btn btn-ghost">All lessons</a>')
    else:
        adv_ctas = ('<a href="/education/advanced/0" class="btn btn-primary">Unlock for £99</a>'
                    '<a href="/onboarding" class="btn btn-ghost">Start with the free course</a>')
    content = f"""
<section class="hero page-hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">£99 · One-time · 3 sections · {len(lessons) - 1} lessons</span>
      <h1>Advanced Chart<br>Reading</h1>
      <p class="lede">Learn to read a chart yourself, not just follow along, one lesson at a time.</p>
      <div class="cta-row">{adv_ctas}</div>
    </div>
  </div>
</section>

<section class="tinted tinted-edge">
  <div class="wrap" style="max-width: 900px;">
    <div class="section-head" style="max-width:100%;">
      <span class="eyebrow">From members</span>
      <h2>What people say</h2>
    </div>
    <div class="says">{course_reviews}</div>
    <p class="risk-note">Real reviews from students, shared with permission. They describe one
       person's experience of the course. Nothing here is financial advice, and past results are
       not a guide to future results.</p>
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
<section class="hero page-hero" style="padding-bottom: 40px;">
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
    ("""Believing You're Her: You Are Not Waiting""",
     """
There is a version of you who has this handled. Money sorted. Head clear. Walks into a room and doesn't make herself smaller so everyone else can relax.

She is not a fantasy. She is not something you earn after ten more years of being good. **She already exists, and the only thing standing between you and her is that you keep treating her like a someday.**

**Nobody hands it to you**

Here is what nobody says out loud: there is no moment coming where someone senior confirms you're allowed. No letter. No qualification. No sign.

Every woman you look up to decided she was her, and then behaved accordingly, long before she felt like it. That is the entire secret. It is unglamorous and it is available to you today.

You do not become her and then start acting like her. **You act like her, and that is how you become her.** In that order. Always in that order.

**Where the picture came from**

Close your eyes for a second and picture her properly. Not vaguely. What is she wearing? How does she walk into a room? What does she do with her hands?

Where do you think that picture came from? You didn't get it off Instagram. It came from you. Some part of you already knows exactly who you're supposed to be, and has known for years.

That picture is not wishful thinking. **It is a memory of something you haven't done yet.**

Most women bury her under a pile of *not yet*. Not yet qualified. Not yet earning enough. Not yet the right time, the right partner, the right year. And she sits there, fully formed, waiting on permission that is never going to come from anyone but you.

**The feeling that stops most women**

You'll start, and it will feel like acting. Like you're pretending. Like at any moment someone will notice you don't really belong.

Read this carefully because it matters more than anything else in this session: **that feeling is not evidence you're a fraud. It is the feeling of your edge moving.**

Your brain flags unfamiliar as dangerous. That is its job and it has kept humans alive for a very long time. But it cannot tell the difference between danger and growth, so it fires the same alarm for both. Every woman who has ever changed her life did it while that alarm was going off.

You are not supposed to feel ready. Ready is not a feeling that comes before. It is a feeling that comes after, months later, when you've long forgotten you were waiting for it.

**Start tomorrow, badly**

Not all of her. One thing.

Tomorrow, get dressed like her. Ask one question without the apology in front of it. Sit up straight in the meeting. Say your goal out loud once, without the little laugh at the end that takes the edge off it.

Do it enough times and one day you'll notice it doesn't feel like acting any more. That's it. That's the whole transformation. No lightning. No moment. Just a woman who kept showing up as her until she was.

**Say these until they stop feeling like a lie**

Out loud. In the mirror. Every morning. Yes, it feels ridiculous. Do it anyway, because your brain believes what it hears repeatedly, and right now it is repeating something you didn't choose.

*I am her. I'm not becoming her, I already am.*
*I don't need permission and I'm not waiting for it.*
*Money is something I handle, not something that happens to me.*
*I am allowed to want more than this.*

**Your work this week**

1. Write her out properly. Not "rich and happy". How does she dress, speak, handle a bad week, spend a Sunday, talk about money at dinner?
2. Pick **one** thing off that list and do it tomorrow. One, not five.
3. Do that one thing every day for seven days before you add a second.
4. On day seven, write down what feels different. Something will have.

**Read this**

*The Mountain Is You*, Brianna Wiest. About self-sabotage, and specifically about why we wreck the thing we say we want. If you keep starting and stopping, this book is about you.

**Put this on**

*Step Into Your Power*, Lenzspot. Play it while you get ready and actually listen, don't have it on in the background. The morning you get ready as her instead of as who you were yesterday is the morning this starts.
"""),

    ("""The Woman You Were Told To Be""",
     """
Before you can build her, you have to see who you were handed.

Because you were handed one. Nobody sat you down and explained it, which is exactly why it worked so well. You absorbed it from watching, and things absorbed from watching don't feel like beliefs. They feel like facts.

**What you actually learned**

Think about the women who raised you, or the ones around you growing up.

Who handled the money? What did the other person do while that was happening? What was said when there wasn't enough, and more importantly, what tone was it said in? When a woman in your house wanted something for herself, did she buy it, or did she explain it first, or did she quietly go without and call it being sensible?

You were not taught a rule. You were shown a pattern, hundreds of times, before you were old enough to question it.

And so a grown woman with a good salary sits down to open a trading account and finds her chest goes tight, and she decides the problem is that she's bad with numbers.

**It is not the numbers.**

**Where it came from is not your fault**

The women who raised us were mostly doing their best inside rules far tighter than ours. Their caution often made complete sense for their circumstances. It is just not a rule that has to be yours.

That distinction matters. This is not about blaming anyone. It is about noticing the difference between **what is true** and **what is simply familiar**.

Familiar feels like truth. That's the trap. Your nervous system cannot tell the difference between "this is dangerous" and "I have never done this before", so it labels both with the same feeling and you spend a decade thinking you're not the type.

**The two disguises**

This shows up in two ways and they look like opposites.

Some women handle it by refusing to look at money at all. Statements unopened. No idea what's coming in or going out. Someone else deals with it, or nobody does.

Others handle it by controlling every single penny with a rigidity that looks like competence and feels like fear. Spreadsheets, but panicked ones. Cutting back on things they can easily afford.

**Both are the same wound wearing different clothes.** If budgeting makes you anxious rather than calm, that is worth looking at properly.

**Why naming it changes something**

A belief you cannot see runs you. A belief you can see becomes optional.

The moment you can say "I learned money was something men managed, from watching, and I don't actually agree with it" is the moment it stops being a fact about the world and becomes an inherited rule. Inherited rules can be examined. Some you'll keep. Most you'll find you never chose.

You do not have to resolve this today. You have to see it. Seeing it is most of the work, and the rest follows more easily than you'd expect.

**Your work this week**

Write these. Don't just think them, because thinking lets you skim the uncomfortable ones.

1. Who handled the money where you grew up?
2. What was said when there wasn't enough? In what tone?
3. What did the women around you do when they wanted something for themselves?
4. Finish this sentence: *In my family, money was ______.*
5. Now the one that matters: **is that true, or is it just familiar?**

Then one more. Write the sentence you were never told but should have been. *Money is safe for me to handle.* *Wanting more is allowed.* Whatever the missing one is for you. Put it where you'll see it.

**Read this**

*The Psychology of Money*, Morgan Housel. Short chapters, no jargon. The whole argument is that how people handle money has almost nothing to do with intelligence and almost everything to do with what they lived through. It will make you far kinder about your own patterns, and kindness is what actually lets you change them.

**Put this on**

*Find Your Strength*, Lenzspot. For the day you do this exercise, because it will stir things up, and you should have something behind you that reminds you what the digging is for.
"""),

    ("""Your Why, And The One Underneath It""",
     """
Everyone has a surface why. It's usually money, and it never survives a hard week.

"I want to make more money" will not get you out of bed on a Tuesday in February when you're tired and something went wrong. It's too abstract. It doesn't grip.

The why that holds is always underneath the first one, and it's usually more personal than you'd say out loud.

**Keep asking why**

Take whatever you'd answer with, and ask why four more times. Properly. Write each answer down.

*I want more money.* Why?
*So I'm not worrying about it.* Why does that matter?
*Because I watched my mum worry about it my whole childhood and I could feel it in the house.* And?
*And I don't want my daughter to feel that.*

**That is the why.** That is the one that gets you up. Not the number. The child who could feel the tension in the room and didn't have words for it.

Yours will be different and it will be specific to you. It might be: I never want to be financially dependent on a man again. It might be: I want to be the one my family comes to. It might be: I want to walk out of a job I hate without needing a plan first.

Whatever it is, it is not "more money". It is the thing more money would let you stop feeling.

**Why the soft version fails**

A vague why gives your brain nothing to hold on to when things get uncomfortable, and things will get uncomfortable.

There will be a red week. Somebody will say something dismissive. You'll look at your balance and feel stupid for thinking you could do this.

In that moment, "I want more money" evaporates. But *"I refuse to have my daughter grow up watching me flinch every time a bill lands"* does not evaporate. It gets louder.

That's the difference between a goal and a reason.

**Where most women lose it**

They find the real why, feel it properly for about a day, and then never look at it again. Three weeks later they've forgotten what they were doing this for and they think they've lost motivation.

You haven't lost motivation. You've lost contact with the reason.

So the why has to live somewhere you actually see it. Not in a notebook you close. Phone lock screen. Mirror. Inside your trading journal so it's the first thing before every session.

**Your work this week**

1. Write your surface why.
2. Ask why five times, writing each answer, until one of them makes you feel something. That's the one. You'll know because there's a small flinch when you write it.
3. Write it as one sentence, present tense, in your own voice. Not a slogan. The way you'd say it to someone you trust at 11pm.
4. Put it somewhere you cannot avoid it.
5. Read it before every trading session this week. Every one, even the ten-minute ones.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki. Read it for the mindset rather than the specifics: the difference between working for money and having money work for you, and why the people who stay stuck are usually not the ones who earn least. It will reframe what you're actually building toward.

**Put this on**

*I Don't Manifest, I Decide*. The title is the entire lesson. A why is not a wish you're hoping lands. It's a decision you've already made and are now carrying out.
"""),

    ("""The Vision Board That Actually Works""",
     """
Most vision boards do nothing, and there's a reason worth understanding, because the fix is small and it changes everything.

**Why the usual version fails**

A board covered in mansions, cars and beaches gives you a hit of feeling. You look at it, you feel a flush of *yes, that's coming*, and you go about your day.

That flush is the problem. Your brain has just been given a small taste of arriving without leaving. It felt like the thing. And feeling like the thing quietly drains the urgency to go and get it.

That's why women can stare at a board for two years while nothing moves. It isn't that they didn't want it enough. It's that the board was doing the wrong job.

**What to put on it instead**

Three things, in this order.

**One: her, not it.** Not the house. Her, in the house. What is she doing on an ordinary Wednesday morning? Not the highlight reel, the dull middle of the week. That's what you're actually building.

**Two: the doing, not the having.** A picture of a woman at a laptop at 6am. Your actual account balance goal written in your handwriting, not a stock photo of cash. The specific course you're going to finish.

**Three: the obstacle, named.** This is the part nobody does and it's the part that works. Somewhere on that board, write the real reason you might not do this. *I stop when it gets hard.* *I care too much what my sister thinks.* *I go quiet when I don't understand something.*

Name it, then write what you'll do when it turns up: *when I feel stupid, I ask anyway.*

That last line is worth more than every photograph on the board.

**Make it specific enough to be uncomfortable**

"Financial freedom" is not a vision. It's a mood.

**£2,000 a month from trading by next June** is a vision. It has a number and a date, and it can be checked, which is exactly why people avoid writing it that way. A vague goal can never fail. It also never happens.

Be specific enough that you'd be a bit embarrassed to show someone. That's the correct level.

**Where it goes**

Somewhere you see it without choosing to. Not a Pinterest board you have to open. Not a folder. On the wall where you get dressed, or your phone lock screen.

You are not trying to look at it and feel good. **You are trying to see it so often it stops being aspirational and starts being obvious.** That is the whole mechanism. Repetition turns a want into an assumption, and assumptions are what people act on without arguing with themselves first.

**Your work this week**

1. Make it. Actually make it, this week, not when you've got time.
2. Her on an ordinary Wednesday. Written out or pictured.
3. Three things that are the doing, not the having.
4. One number with one date.
5. The real obstacle, named, with your if-then underneath it.
6. Put it where you'll see it before you're properly awake.

**Read this**

*The Law of Attraction*, Esther and Jerry Hicks. Read it for what it does to your expectations rather than as instructions. The useful part is how firmly it insists you are allowed to want, which is the exact permission most women were never given.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. Play it while you make the board. What you're building should be made in a good mood, because you'll feel that mood every time you look at it afterwards.
"""),

    ("""How She Shows Up""",
     """
This one is practical, and you'll catch yourself within a day of reading it.

There is a particular way women make themselves smaller, and it costs money. Not metaphorically. Actual money.

**The apology tax**

It sounds like: *"Sorry, I'm probably being silly, but could you explain what that fee is?"*

It sounds like taking the smaller share to avoid the conversation. Like saying "I might be wrong" before you say the thing you're right about. Like apologising for asking a question about your own money.

Every softener costs you something. Sometimes it's the fee you didn't query. Sometimes it's the information, because a hedged question invites a hedged answer.

Watch what happens when you take it out. *"Could you explain what that fee is?"* No preamble. No apology. It isn't rude. It simply doesn't arrive pre-loaded with the suggestion that you might not deserve an answer.

Most women find that terrifying to try once and completely unremarkable afterwards.

**Where it costs you in trading, specifically**

Very directly, and this is why it belongs here rather than in a general self-help book.

**Sizing.** Shrinking a position because a real number frightens you, when your plan said otherwise.

**Closing early.** Grabbing a small win because you don't quite feel entitled to the whole one.

**Staying quiet.** Not asking in the group when you don't understand, then guessing, then losing money you'd have kept if you'd typed one sentence.

Three habits. All the same root. All expensive.

**It isn't stupidity, it was strategy**

Be fair to yourself here. Hedging was often adaptive. Women who assert directly are frequently judged more harshly for it, and you learned to soften because softening worked in the rooms you were in.

But there is a difference between **choosing** to soften because the room needs it, and softening automatically because you've forgotten there's another option.

The first is skill. The second is a cage. The goal isn't to become abrasive. It's to make it a choice again.

**The rest of showing up**

**How she dresses.** Not expensively, deliberately. She doesn't leave in whatever was on the chair. She dresses like a woman who expects to be taken seriously, and so she is.

**How she walks into a room.** Slowly. She doesn't rush in apologising for existing. She takes the chair at the table, not the one against the wall.

**What she does with a compliment.** Says thank you. Full stop. Doesn't deflect it, doesn't explain that the dress was cheap.

**What she does when she doesn't know.** Says so. Immediately and without shame. "I don't know what that means, explain it." The women who ask are the women who get good, every time.

**Your work this week**

1. **Ask one financial question with no softener.** Bank, bill, the group, anywhere. Just the question.
2. **Notice the feeling before you send it.** That discomfort is the habit, not a warning.
3. **Notice what actually happens.** Almost always: nothing. You get an answer.
4. **Do it three more times.** Once proves nothing. Four times starts changing what normal feels like.
5. On your next trade, take the size your plan says. Not smaller because the number scares you.
6. Take one compliment this week with nothing after the thank you.

**Read this**

*Playing Big*, Tara Mohr. The chapter on hedging language alone is worth the whole book. She is precise about the exact verbal habits that shrink women's authority, and gives you replacements rather than just telling you to be more confident.

**Put this on**

*Step Into Your Power*, Lenzspot. On the morning you're going to do the no-apology question. You'll want it.
"""),

    ("""Rewiring The Old You""",
     """
You will not think your way into being different. You have already tried, which is presumably why you're here.

The old you is not a personality. **She is a set of grooves worn in by repetition**, and grooves are not changed by deciding. They're changed by doing something else, badly, on the days you don't feel like it, until the new thing wears a groove of its own.

**Why motivation keeps failing you**

Motivation is weather. It arrives, it feels wonderful, you plan everything, and then it leaves without warning and owes you nothing.

Anything built on it collapses the first grey Tuesday. That is not a character flaw. It's what happens when you build on a feeling.

What survives is much less romantic: something small enough that doing it doesn't need a decision, attached to something you already do.

**Small enough to be embarrassing**

The most reliable move is to shrink the thing until it feels pointless.

Not "study for an hour". **One lesson.** Not "journal every trade properly". **One line.** Not "meditate daily". **Three breaths before you open the charts.**

This feels like cheating. It isn't. You're not optimising for how much you do on a good day. You're optimising for **never missing**, because the real damage of a skipped day isn't the lost work. It's what it does to your sense of yourself as someone who does this.

Two weeks of one lesson a day beats one heroic Sunday and then nothing. Every time, and it isn't close.

**Bolt it to something that already happens**

Habits need a trigger, and the best triggers are things already in your day.

*After I put the kettle on, I read one lesson.* *After I close a trade, I write one line about it.* *After I sit down at my desk, I read my why.*

"When I have time" is not a trigger. There is never time. There are only other things.

**The rule that carries everything**

**Never miss twice.**

You will miss. Everyone misses. Missing once is an accident and means nothing at all. Missing twice is where the story starts to change from *I'm someone who does this and had a bad day* to *I suppose I've stopped.*

So the discipline is not perfection. It's the return. Miss Tuesday, do the two-minute version on Wednesday, even badly. The point isn't the two minutes. It's refusing to let one day become the story you tell about yourself.

**Decide the recovery now**

Not in the moment. Now, while you're calm and nothing has gone wrong.

What is the smallest possible version you'll do the day after you slip? Write it down. When you're in it, discouraged and behind, you will not be in a state to negotiate with yourself. You need it already decided.

**Your work this week**

1. Pick **one** habit. One. The urge to start five is what ended your last five attempts.
2. Shrink it until it's almost embarrassing.
3. Attach it: *after I ______, I will ______.*
4. Write your never-miss-twice recovery now.
5. Do it for fourteen days before you add anything.
6. Mark each day off somewhere you can see. The chain is more motivating than you expect.

**Read this**

*Atomic Habits*, James Clear. Go to the chapter on identity-based habits first, it's the argument of this whole section: you don't get results by setting goals, you get them by becoming someone for whom those results are ordinary. Then read the rest for the mechanics.

**Put this on**

*Find Your Strength*, Lenzspot. For day nine, when the novelty has worn off and nobody is watching. That's the day that decides it.
"""),

    ("""The People Around You""",
     """Not everyone will be pleased for you. That's worth preparing for.

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

    ("""Confidence That Doesn't Need Permission""",
     """Confidence isn't a feeling you wait for. It's the evidence you build.

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

    ("""Handling Fear, Loss & Getting Back Up""",
     """You will lose money. Not might. Will.

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

    ("""Deciding, Not Wishing""",
     """
There is a difference between wanting something and having decided it's happening, and almost everyone who says manifesting doesn't work is stuck on the wrong side of it.

Wanting is passive. It sits in you and waits. You want more money the way you want better weather, and you look up occasionally to see whether it's arrived.

Deciding is different. A decision closes the other doors. Once you've truly decided, you stop asking whether it will happen and start working out how.

**How you can tell which one you're in**

Ask yourself: what would have to be true for this not to happen?

If your honest answer is *"well, if it doesn't work out"*, you're wanting. You've left yourself an exit and you'll take it the first time something goes wrong, and you'll call it being realistic.

If your answer is *"I'd have to give up, and I'm not going to"*, you've decided.

Same goal. Completely different relationship to it. One of them survives a hard month.

**Why this comes first**

Everything else in this course sits on top of this. Vision boards, affirmations, scripting, gratitude, all of it works far better when you've genuinely decided and does almost nothing when you haven't.

Because the mechanism isn't magic. It's attention and action. A decision changes what you notice, what you say yes to, what you're willing to feel awkward doing. A wish changes nothing at all.

**Say it properly**

Notice how differently these land.

*I'd love to be earning an extra £1,000 a month.*
*I am building £1,000 a month and I'm not stopping until it's there.*

The first is a mood. The second has consequences. It implies you'll learn what you need to learn and do the parts you'd rather not.

Most women were taught to phrase their ambitions as the first one, because the second sounds arrogant. It isn't arrogant. It's clear. And clear is what actually works.

**What deciding does not mean**

It doesn't mean forcing it. It doesn't mean gripping so hard you panic every time progress is slow. That's fear wearing a determined face, and it burns people out.

Deciding is calmer than that. It's *this is happening, so I'll keep going*, said in a level voice, on a day when nothing much is working.

**Your work this week**

1. Write the thing you actually want. Specific. With a number and a date if it has one.
2. Now write it twice: once as a wish, once as a decision. Read both aloud. Feel the difference in your body.
3. Answer honestly: what would have to be true for this not to happen? If your answer includes an exit, you haven't decided yet.
4. Say the decision version out loud every morning this week. It will feel like lying at first. That fades.

**Read this**

*The Law of Attraction*, Esther and Jerry Hicks. Read it for permission rather than instruction. Whatever you make of the metaphysics, the book is relentless about your right to want things, and that permission is the exact thing most women were never handed.

**Put this on**

*I Don't Manifest, I Decide*. The title is this entire lesson. Put it on when you catch yourself hedging.
"""),

    ("""Getting Specific Enough To Be Uncomfortable""",
     """
Vague goals never fail. That's their appeal, and it's exactly why they never happen either.

"Financial freedom." "More money." "A better life." None of those can ever be checked, so none of them can ever disappoint you. They also give your brain nothing to work with.

**What specific actually means**

Not *I want to make money trading.*

**I want £1,000 a month from trading by next June, trading gold, three evenings a week, keeping my risk at 2% per trade.**

Read that again. It has a number, a date, a market, a schedule and a rule. Every single one of those is a decision you'd otherwise be making from scratch, badly, in the moment.

That's what specificity does. It isn't about ambition. It's about removing choices from your future self, who will be tired and less clear than you are right now.

**The embarrassment test**

Here's the marker: get specific enough that you'd feel slightly awkward reading it out to someone.

That flicker of embarrassment is the sign you've written something real. Vague goals never embarrass anyone because they never claim anything.

If you can say your goal in a room and nobody could later say *"well, that didn't happen"*, it isn't specific enough yet.

**Break it down until it's boring**

A goal you can't start today is still a wish.

£1,000 a month by June. What's that this month? What's it this week? What is the one thing that has to happen before Friday?

Keep dividing until you arrive at something so small it's almost dull. *Finish section two of the course.* *Set up my journal.* *Place three trades at my planned size instead of shrinking them.*

Boring is the point. Boring is doable. And doable is what happens.

**The date matters more than you think**

A goal without a date has no urgency, and things without urgency lose every fight with things that have it. Your goal will lose to the washing, the group chat, the tiredness.

Pick a real date. Not "this year". A date.

**Your work this week**

1. Take your goal and rewrite it with a number, a date, and how you'll get there.
2. Read it out loud. If you feel nothing, it's still too vague. Sharpen it.
3. Break it into this month, this week, and one thing before Friday.
4. Do the Friday thing.
5. Put the full version where your vision board is, or where you get dressed.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki. Read it for how it reframes what you're building toward: the difference between working for money and building something that works for you. It makes goals feel less like fantasies and more like decisions about which direction you're pointing.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. For the morning you write the specific version. Do it in a good mood, because you'll carry that mood every time you read it back.
"""),

    ("""Affirmations That Aren't Nonsense""",
     """
Most people try affirmations for four days, feel like an idiot, and quietly stop. Fair enough, because most affirmations are written badly.

**Why the usual ones don't stick**

*I am a millionaire.*

If your account has £312 in it, your brain does not accept that. It pushes back immediately, and the pushback is louder than the affirmation. You end up more aware of the gap than before you started.

Affirmations work when they're **believable enough to accept** and **directional enough to pull you**. Miss on either side and nothing happens.

**Write them from where you are**

Not *I am a millionaire.*

**I am becoming a woman who is genuinely good with money.**
**I am learning this and I get better every week.**
**Money is something I handle, not something that happens to me.**
**I am allowed to want more than this.**

Your brain can accept all of those. They're true or nearly true, and repeating them starts moving where "normal" sits.

**The exception worth knowing**

There's one place the bold version does work: identity statements about who you are, rather than claims about what you own.

*I am her* is fine, even now, because it's a decision rather than a fact you'd have to prove. *I have a million pounds* is a fact, and a false one, so it gets rejected.

Decide who you are. Don't lie about your balance.

**Out loud, and looking at yourself**

This is the part people skip, and it's the part that does the work.

Say them out loud. In the mirror. It is deeply uncomfortable for about a week.

Why it matters: you've spent years absorbing an internal commentary you never chose, most of it in your own voice. The only thing that competes with that is your own voice saying something else, deliberately, on purpose, often.

Thinking them isn't enough. Your thoughts are already occupied.

**When to do it**

Morning, while you get ready. Attach it to something already happening so you don't have to remember. While the kettle boils. While you do your hair.

Thirty seconds. That's genuinely all it takes, done daily, for months.

**Your work this week**

1. Write four affirmations from where you actually are. Believable, directional.
2. One of them should be an identity statement. *I am her.*
3. Say them out loud in the mirror every morning for seven days.
4. Note on day seven whether anything felt different when you said them.
5. Rewrite any that still feel like a lie. Bring them closer to true.

**Read this**

*The Mountain Is You*, Brianna Wiest. Particularly useful here on why we resist the things we say we want. If your affirmations feel like lying, this book explains what's actually happening.

**Put this on**

*Step Into Your Power*, Lenzspot. While you say them. Music makes the mirror less awkward, and awkward is the main reason people stop.
"""),

    ("""Gratitude, And Why It Isn't Soft""",
     """
Gratitude has a reputation as the nice, gentle one. It isn't. It's the most practical thing in this course, and it's the one that keeps women going through a bad month.

**What it's actually doing**

Your brain is built to scan for what's wrong. That kept your ancestors alive and it makes you miserable in a world where most things are fine.

Left alone, it will show you the gap. Always the gap. What you haven't achieved, who's further ahead, how much is left.

Gratitude is not pretending the gap isn't there. It's deliberately pointing your attention at what's already working, because your attention will not go there by itself.

**Why it matters specifically in trading**

Here's where it stops being fluffy.

You will have weeks where you're up and it doesn't feel like enough. Where you made £80 and all you can see is the woman who made £800. That feeling is what makes people size up too fast, chase losses, and blow accounts.

**Dissatisfaction is expensive.** It is directly, measurably expensive in this particular pursuit.

A woman who can look at a £40 week and feel genuinely pleased is a woman who will still be trading in a year. The one who can't will overreach, get hurt, and stop.

**Do it properly**

Three things, written, daily. Written, not thought.

The rule that makes it work: **be specific and go small.** Not "my family". *The way she laughed at that thing this morning.* Not "my job". *That my afternoon was quiet and I got the lesson done.*

Vague gratitude does nothing. It's a list you produce without feeling anything. The specific version makes you briefly re-live the thing, and that's the bit that changes your mood.

**Include the trading**

*That I stuck to my size even though I wanted to go bigger.*
*That I closed at my take profit instead of getting greedy.*
*That I asked when I didn't understand.*

You are training yourself to notice your own good behaviour, which is how it becomes more frequent. Nobody else is going to notice these things. They're invisible from outside.

**Your work this week**

1. Three specific things a day. Written down. The Daily Page is there for exactly this.
2. At least one of them about something you did, not something you received.
3. At least one about money or trading, however small.
4. On day seven, read the week back in one go. That's when it lands.

**Read this**

*The Psychology of Money*, Morgan Housel. The chapters on enough, and on why people with plenty keep reaching, are the strongest argument for gratitude you'll read anywhere, and it never once uses the word.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. Do your three while it plays. Two minutes, and it sets the tone of the whole day.
"""),

    ("""Scripting Your Life Forward""",
     """
Scripting is writing your life as though it has already happened. It sounds indulgent. It's one of the most clarifying exercises here, because it forces detail out of you that goal-setting never does.

**What it actually is**

You write a day in your life, twelve months from now, in the present tense, in detail.

Not the highlight reel. **An ordinary Tuesday.** What time you wake. What you're not worrying about any more. What you do first. What's in your account. What you say no to. How the evening ends.

**Why the ordinary day and not the big moment**

Because the big moment isn't the thing you're building. Nobody actually wants the champagne photo. They want the Tuesday where the bill lands and their chest doesn't tighten.

If you can only picture the champagne, you haven't worked out what you're for yet, and you'll get there and feel oddly flat.

Write the Tuesday. The Tuesday is the life.

**The detail is the point**

Vague scripting does nothing. *I'm rich and happy* is not scripting, it's daydreaming.

*I wake at 6.40 without an alarm because I went to bed at a reasonable hour. I make coffee, sit at the desk in the corner I set up properly, and read the signals with a clear head. I take one trade and I don't second-guess the size. By 7.30 I'm done and the day is mine.*

That's scripting. You can see it. And more importantly, you can now spot which parts of it are already available to you this week.

**The part nobody expects**

When you write the Tuesday properly, you'll usually find something surprising: a good chunk of it doesn't require money at all.

Going to bed at a reasonable hour. Setting up a proper corner to work in. Not second-guessing your size. Being done by 7.30.

That's the real payoff. Scripting shows you which parts of the life you're waiting for are already sitting there, free, waiting on a decision rather than a balance.

**Do it once, properly, then reread**

This isn't a daily practice. Write it once, at length, when you have an hour and you're in a decent mood.

Then reread it monthly. You'll notice bits quietly becoming true, and you'll notice bits you no longer want, which is just as useful.

**Your work this week**

1. Set aside an hour. Actually block it.
2. Write an ordinary Tuesday, twelve months out, present tense, in detail.
3. Include: what you're not worrying about, what's in the account, what you say no to.
4. Underline everything in it that doesn't require more money.
5. Pick one underlined thing and do it this week.

**Read this**

*The Law of Attraction*, Esther and Jerry Hicks. The chapters on deliberate creation are essentially structured scripting, and reading them first will make your own version far richer.

**Put this on**

*Find Your Strength*, Lenzspot. Write it with this on. You want to be in the mood the writing describes.
"""),

    ("""Not Everyone Will See It""",
     """
This is the lesson women tell us mattered most, and it's the one nobody warns you about.

At some point you'll tell someone what you're building, and they will not react the way you hoped.

They'll go quiet. Or ask a pointed question. Or say *"just be careful"* in a tone that means something else. Or laugh, gently, in a way that stays with you for three days.

**Understand what's happening**

Most of the time it isn't malice. Your growth holds a mirror up to their stalling, and that's uncomfortable, so the discomfort comes back at you as doubt about your plan.

Someone who has told themselves it's not possible has a lot invested in it not being possible. Your attempt threatens a story they need.

That is genuinely not your problem to solve. But it is your problem to survive, because if you don't understand what's happening you'll take it as evidence.

**The particular version women get**

Watch for the ones dressed up as care.

*"Don't get carried away."* *"Just don't put in more than you can afford."* *"Isn't that a bit risky for you?"*

Some of that is real concern and worth hearing. Some of it is discomfort with a woman wanting more, wearing concern as a costume.

You can usually tell the difference by asking what specifically they're worried about. Real concern has a specific answer. The other kind goes vague.

**What to do about it**

**Stop announcing.** The instinct to tell everyone is a bid for permission, and you already know that permission isn't coming. Tell people afterwards, if at all.

**Pick two.** You need two people who genuinely want you to win. Not ten. Two. Everyone else gets the version where you're fine and nothing much is happening.

**Find the room where this is normal.** This is exactly what the board and the community are for. In there, a woman saying *I want £2,000 a month and I'm building it* gets "how's it going?" rather than a raised eyebrow. Never underestimate what it does to be somewhere your ambition is ordinary.

**It might not happen overnight, and that's fine**

Nobody's growth is a straight line. There'll be months where nothing visibly moves and you'll wonder if you've been kidding yourself.

You haven't. Compounding is invisible right up until it isn't, and the women who get there are simply the ones who were still going when it started to show.

**Your work this week**

1. Write down who you've been telling, and how each of them responds.
2. Pick your two. The ones who actually want it for you.
3. Decide what you'll say to everyone else. Something short and boring.
4. Post once on the board. Just introduce yourself. Being in a room where this is normal changes more than you'd expect.

**Read this**

*Playing Big*, Tara Mohr. Excellent on criticism, and specifically on separating feedback worth taking from discomfort dressed up as advice.

**Put this on**

*Find Your Strength*, Lenzspot. For the day after somebody's face does the thing.
"""),

    ("""Blocking Out The Noise""",
     """
The drama is not neutral. It costs you money, and this lesson is about the mechanics of that.

**What noise actually does**

Attention is finite. Every hour spent on a group chat argument, a comparison spiral, or someone else's crisis is an hour not spent on the thing you said mattered.

That's the obvious cost. The bigger one is state. You cannot make good decisions about money while wound up. You take trades you wouldn't otherwise take. You size wrong. You chase.

**The comparison problem**

Social media will show you women who appear to be six steps ahead. What it will not show you is the account behind the screenshot, the money that came from somewhere else, or the losing months.

Comparison against an edited version of someone else's middle is the fastest way to feel behind while doing everything right.

The only comparison that means anything is you against you last month. That one is real, and you're the only person with the full picture of it.

**Practical protection**

**Mute rather than argue.** You will not win it and you'll carry it all day.

**Unfollow anyone whose posts make you feel behind.** Not because they've done anything wrong. Because you get to choose what goes into your head, and you're allowed to protect it.

**Put a wall around the hour before you trade.** No group chats, no news, no draining conversation. You want to arrive at the charts calm and not carrying anything.

**Stop explaining yourself.** Enormous amounts of energy go into justifying to people who were never going to be convinced. "It's something I'm doing" is a complete sentence.

**The hate you'll get for wanting more**

Some of it will be direct, especially as things start working. There is a particular resentment reserved for women who visibly want more and go and get it.

Expect it, so it doesn't knock you sideways when it arrives. It is not a verdict on your plan. It is information about them.

**Your work this week**

1. Unfollow or mute five accounts that make you feel behind. Do it now, not later.
2. Leave or mute one chat that drains you.
3. Set your hour before trading as protected time. Phone face down.
4. Notice one comparison spiral this week and write down what you were actually comparing.
5. Practise the boring sentence. "It's something I'm doing." Nothing after it.

**Read this**

*The Psychology of Money*, Morgan Housel. The section on how people judge their wealth relative to others rather than in absolute terms explains exactly why comparison hurts even when you're doing well.

**Put this on**

*Step Into Your Power*, Lenzspot. On the day you do the unfollowing. Make it feel like a decision rather than a sulk.
"""),

    ("""When It's Slow""",
     """
There will be a stretch where nothing appears to happen. Weeks. Possibly months. This lesson exists because that stretch is where most people quit, usually about four weeks before it would have started working.

**What slow actually looks like**

You're doing the lessons. You're keeping your size sensible. You're showing up. And the account has barely moved, and you're beginning to feel a bit stupid about the whole thing.

That is not failure. That is what the middle looks like, and nobody posts about the middle.

**Why it feels worse than it is**

Two things going on at once.

First, progress is not linear and your expectations are. You imagined a slope. What you get is a flat stretch, a jump, another flat stretch. On the flat you assume it's over.

Second, the work that pays is invisible while you're doing it. Understanding, patience, not doing the stupid thing. None of it shows in the balance for a long time, and then all of it does at once.

**The question that helps**

Not *has my balance changed?*

Instead: **am I making better decisions than I was three months ago?**

Do you understand what you're looking at? Do you size properly? Do you close early out of fear less often? Would three-months-ago you have taken that trade you skipped last week?

If those are yes, it's working. The money follows competence with a delay, and the delay is the part nobody tells you about.

**What to do while it's slow**

**Shrink the target.** When outcomes stall, measure the behaviour instead. Did you follow your plan this week? That's the win. That's within your control, and the outcome eventually isn't.

**Keep the record.** This is what the journal is for. On a flat month, reading back three months makes the change visible in a way that daily living never does.

**Do not size up to make it move faster.** This is the single most expensive mistake in trading and it is always born in a slow patch. Boredom and impatience feel like confidence. They aren't.

**Talk to someone in it.** Slowness is far heavier alone. Say it on the board. Several women will tell you they're in the same stretch, and it will halve the weight of it.

**It will happen**

Not because of magic. Because you're building something real, slowly, and real things are slow.

The women who get there are not the talented ones. They're the ones who were still going.

**Your work this week**

1. Answer honestly: am I making better decisions than three months ago? Write the evidence.
2. Set one behaviour target for this week, not an outcome. Followed my plan on every trade.
3. Read back through your journal, if you've been keeping it.
4. If you're in a slow patch, post about it on the board. That's what it's for.

**Read this**

*Atomic Habits*, James Clear. The section on the plateau of latent potential is precisely this lesson: results lag effort, and most people quit inside the lag.

**Put this on**

*Find Your Strength*, Lenzspot. For the flat week when nobody is watching and it would be very easy to stop.
"""),

    ("""Receiving It""",
     """
Here's the odd one. A lot of women are far better at wanting than at receiving, and it quietly caps them.

**What it looks like**

The win comes and you immediately deflect it. It was luck. It was a good month for gold. Anyone could have done it.

Or you get it and feel guilty. Someone you know is struggling and here you are with a green account, and something in you wants to apologise for it.

Or you get it and instantly move the target. £500 a month was the goal, you hit it, and by the following week it's not enough and you're behind again.

**Why it matters practically**

If you cannot receive, you cannot enjoy any of this, and something you never enjoy is something you eventually stop doing.

It also distorts your decisions. Women who can't accept a win tend to give it back, by overtrading, by sizing up, by finding a way to be uncomfortable again because comfortable feels unfamiliar.

**Where it comes from**

Usually the same place as everything else in this course. Somewhere along the line you learned that wanting was a bit much, that having was a bit greedy, and that a woman with money ought to be at least slightly apologetic about it.

You can hold that belief and simultaneously work extremely hard for money. Most women do. It's why the arriving feels so strange.

**How to practise**

**Say thank you and stop talking.** For compliments, for wins, for money. No explaining, no discounting, no "it was nothing".

**Mark the wins.** Not with spending. With acknowledgement. Write it down. Tell your two people. Let it be a thing that happened.

**Sit in it for a week before moving the goal.** You hit the target. Stay there. Notice it. The next goal will still be available in seven days.

**Notice the guilt without obeying it.** Your having more does not cause anyone else to have less. It's a feeling, not a fact, and you can feel it and keep the money.

**The bigger version**

This is what all of it was for. Not the number. The Tuesday where the bill lands and nothing tightens. The moment you realise you're not asking anyone.

You are allowed to have that. Not once you've earned it a bit more. Now, when it arrives.

**Your work this week**

1. Take every compliment this week with just thank you.
2. Write down one win, however small, and let it stand for seven days before you move anything.
3. If guilt shows up, write what it's actually saying. Then read it back and see if you agree.
4. Tell one of your two people something good that happened.

**Read this**

*The Psychology of Money*, Morgan Housel. On enough, and on the people who never stop reaching. The most useful antidote there is to the target that keeps moving.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. For the morning after a good week, so it registers as something that happened rather than something you rushed past.
"""),

    ("""Your Plan, In One Page""",
     """
This is the last one, and it's where the whole course turns into something you can act on.

Everything so far has been about deciding, seeing it clearly, protecting it and staying with it. Now you write it down in one place, in plain language, so on the days you're tired you don't have to work anything out.

**Why one page**

Because you'll actually read one page. You will never reread eleven.

And because forcing it onto a single page makes you choose. Anything that doesn't fit wasn't essential.

**What goes on it**

**Your why.** The real one from lesson three. The one underneath. One sentence, your own words.

**The number and the date.** Specific enough to be slightly uncomfortable.

**Your rules.** The three or four you will not break. Risk per trade. Maximum trades a day. What you do after two losses. When you stop for the day.

**Your habit.** The one small daily thing, with its trigger. *After the kettle goes on, one lesson.*

**Your two people.** Names. The ones you tell.

**The obstacle and the if-then.** The real reason you might not do this, and what you'll do when it turns up.

**Your affirmations.** Four, from where you actually are.

That's the page.

**How to use it**

Read it at the start of every trading session. It takes ninety seconds and it does two things: it puts your why in front of you before money is involved, and it makes your rules a decision you already took rather than one you're making while emotional.

Rewrite it every three months. Not because it changes much, but because rewriting makes you read it properly, and you'll notice what's quietly become true.

**Share it**

Post it on the board, or the parts you're happy to share.

There are two reasons. Saying it out loud makes it real. And someone reading yours will finally write theirs, because they've been meaning to since lesson one.

**What happens now**

You have the decision, the clarity, the practices and the protection. What's left is the unglamorous part, which is doing it on the days you don't feel like it, for longer than feels reasonable.

That's it. That's what separates the women who get there. Not talent, not luck, not a better starting balance. They were still going.

You've got the plan. Go and be one of them.

**Your work this week**

1. Write the page. One page, plain language, nothing clever.
2. Print it, or write it by hand. Somewhere physical that lives by wherever you trade.
3. Read it before every session this week.
4. Post it, or part of it, on the board.
5. Put a note in your calendar three months out that says: rewrite the page.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki, if you haven't yet. It's the right one to finish on, because it puts the whole thing back in the context of what you're actually building rather than this month's numbers.

**Put this on**

*I Don't Manifest, I Decide*. For the day you write the page. You started this course wanting something. You're finishing it having decided.
"""),

    ("""The Money Story You Inherited""",
     """
You have a story about money. You didn't choose it, you can probably recite it in one sentence, and it has been quietly running your decisions for about thirty years.

**Where it came from**

Not from anything you were told. From what you watched.

Who handled the money in your house. Who went quiet when the post came. Whether "we can't afford it" was said calmly, as arithmetic, or with a tightness that filled the room. What the women around you did when they wanted something for themselves: bought it, explained it first, or quietly went without and called it being sensible.

You absorbed all of it before you were old enough to question any of it. And things absorbed that early don't feel like beliefs. They feel like how the world is.

**Why it matters now**

Because a woman with a good salary sits down to open a trading account, feels her chest tighten, and concludes she's bad with numbers.

It is not the numbers. It has never been the numbers.

**The two disguises**

It shows up in ways that look like opposites.

Some women handle it by not looking. Statements unopened. No real idea what comes in or goes out. Someone else deals with it, or nobody does.

Others handle it by controlling every penny with a rigidity that reads as competence and feels like fear. Spreadsheets, but anxious ones. Cutting back on things they could easily afford.

**Both are the same wound in different clothes.** If budgeting makes you tense rather than calm, that's worth looking at.

**Naming it makes it optional**

A belief you can't see runs you. A belief you can see becomes a choice.

The moment you can say *"I learned money was something men handled, from watching, and I don't actually agree"* is the moment it stops being a fact and becomes an inherited rule. Rules can be examined. Most of them you never chose.

This isn't about blame. The women who raised us were doing their best inside far tighter constraints. Their caution often made complete sense for them. It just doesn't have to be yours.

**Your work this week**

Write these. Thinking lets you skip the uncomfortable ones.

1. Who handled the money where you grew up? What did the other person do?
2. What was said when there wasn't enough, and in what tone?
3. What did the women around you do when they wanted something?
4. Finish it: *In my family, money was ______.*
5. **Is that true, or is it just familiar?**

Then write the sentence you were never given. *Money is safe for me to handle.* Put it where you'll see it.

**Read this**

*The Psychology of Money*, Morgan Housel. The whole argument is that financial behaviour has little to do with intelligence and everything to do with what you lived through. It will make you kinder about your own patterns, and kindness is what lets you change them.

**Put this on**

*Find Your Strength*, Lenzspot. This lesson stirs things up. Have something behind you that reminds you what the digging is for.
"""),

    ("""What You Think You're Worth""",
     """
Your relationship with money is downstream of your relationship with your own worth, and it shows up in places you wouldn't expect.

**Where it leaks**

The pay rise you didn't ask for. The invoice you rounded down. The freelance rate you dropped before they'd even pushed back. The refund you didn't chase because it felt like a fuss.

None of those feel like a worth problem in the moment. They feel like being reasonable, or easy to work with, or not wanting to make a thing of it.

Add them up over ten years and it's a house deposit.

**The tell**

Here's the question that cuts through it: **would you accept this for a friend?**

If a friend told you she'd taken £200 less because she didn't want to seem difficult, what would you say to her? Now say that to yourself, in the same tone.

Most women are generous, accurate advocates for everyone but themselves. You already know what fair looks like. You just apply a discount when it's you.

**Where it hits your trading**

Directly, and this is the bit that costs real money.

**You undersize.** Your plan says one thing, the number frightens you, and you go smaller. That isn't caution, caution is having a plan. That's not feeling entitled to the position.

**You close early.** Small win in hand, take profit still forty pips away, and you grab it. Not because the setup changed. Because a bird in the hand feels safer than believing you deserve the whole move.

**You don't ask.** You don't understand something, and rather than type one sentence in the group you guess. Guessing costs money. The sentence is free.

Three habits, one root.

**Worth is not a feeling you wait for**

You will not wake up feeling worthy. It doesn't arrive that way.

It's built by acting as though it's already true, repeatedly, while it still feels like a stretch. You ask for the rate. You take the planned size. You let the trade run to target. Each time, the discomfort is slightly smaller, and one day you notice it's gone.

**Your work this week**

1. Write three times in the last year you accepted less than you should have.
2. For each, write what you'd have told a friend in the same position.
3. This week, take one planned position at full size. No shrinking.
4. Let one trade run to your take profit without touching it.
5. Ask one question in the group you'd normally have guessed at.

**Read this**

*Playing Big*, Tara Mohr. On the specific ways women discount themselves, especially in language. Precise, practical, and it gives you replacements rather than telling you to be more confident.

**Put this on**

*Step Into Your Power*, Lenzspot. For the day you take the full size.
"""),

    ("""Abundance Without The Nonsense""",
     """
Abundance gets sold badly. It's usually presented as pretending you have money you don't, which is neither honest nor useful.

The real version is much less dramatic and far more useful: **there is enough, and someone else having some does not mean less for you.**

**Scarcity is a way of thinking, not a bank balance**

You can spot scarcity thinking regardless of how much someone has.

It sounds like: I have to get in now or I'll miss it. She's doing well so I'm behind. If I spend on this I'll never recover it. There's only so much to go around and I'm not fast enough.

You'll notice all of those are about **speed and competition**. That's the signature. Scarcity makes everything urgent and turns other people into rivals.

**Why it costs you specifically**

Scarcity thinking is the direct cause of the most expensive behaviours in trading.

**FOMO entries.** You take a trade after the move because missing it feels unbearable. Scarcity says this is the last one. It never is. There is another setup tomorrow, and the day after.

**Sizing up to catch up.** You're behind where you wanted to be, so you double the size. That's not a strategy, it's panic with a number attached.

**Refusing to take a small loss.** Because losing anything feels like losing everything when you believe there's a fixed amount and you're running out.

An abundant mindset produces the opposite behaviour, and it's boring: *I'll take the next one. There's always another.* Boring is what makes money.

**The honest version**

Abundance isn't believing money will appear. It's believing that **opportunity is recurring**, so you don't have to grab.

That's it. That's the whole shift. Not magic, just the removal of urgency from decisions that should be made calmly.

**On other women doing well**

This is where it gets tested. Someone in the group posts a good week and something in you tightens.

Their win did not come out of your allocation. There isn't an allocation. Two women can both trade the same setup and both be paid.

If someone else's success makes you feel behind, that's not information about them, it's scarcity showing you where it lives.

**Your work this week**

1. Catch one moment of urgency this week. Write what you were afraid of missing.
2. Then ask: will there be another one of these? There always is.
3. Skip one trade you'd have chased. Note what actually happened next.
4. When someone else posts a win, reply to it. Genuinely. Notice what that does to the tightness.

**Read this**

*The Psychology of Money*, Morgan Housel. The chapter on enough is the clearest thing written on why people with plenty keep reaching, and why that reaching is what eventually costs them.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. Before the session, not after. You want calm before you're deciding with money.
"""),

    ("""Holding Your Nerve In A Red Week""",
     """
It will happen. A week where more goes wrong than right, and you sit there wondering whether you've been kidding yourself.

This lesson is about what to do in that week, because what you do there matters more than anything you do in a good one.

**What a red week actually is**

Normal. Genuinely, mathematically normal.

Any approach that wins more often than it loses will still produce losing runs, and they cluster. Not because anything has broken, but because that's how randomness distributes. Flip a coin fifty times and you'll get a run of five tails. Nothing is wrong with the coin.

The trouble is a red week doesn't feel statistical. It feels personal.

**What it does to your head**

It narrows everything. You stop seeing a run and start seeing evidence: about the strategy, about the group, about whether you're the type of person who can do this.

Then you do one of two things, both expensive. You either **stop entirely** at exactly the wrong moment, or you **push harder** to fix it, which is how a bad week becomes a bad month.

**What to actually do**

**Size down, don't stop.** Halve it. You stay in the market, you keep learning, and you cap the damage. Stopping completely means you're not there when it turns, and it always turns.

**Check the process, not the outcome.** Ask: did I follow my plan? If yes, it was a bad week, not a bad decision. Those are entirely different things and you must not confuse them. Good decisions lose sometimes. That's the deal.

**Do not change anything mid-week.** The urge to fix the strategy in the middle of a losing run is overwhelming and almost always wrong. You'd be optimising for the last five days. Change it on a calm Sunday with a month of data, or not at all.

**Say it out loud.** Post on the board. A red week held privately grows. Said out loud, three women tell you they're in the same stretch and it halves.

**The rule worth having in advance**

Decide now, while you're calm: **after two losing days in a row, I stop for the day and come back tomorrow.**

Write it into your one-page plan. In the moment you'll want to trade through it. That's exactly when the rule earns its place, because you won't be in a state to make it then.

**Your work this week**

1. Write your stop rule. Two losses, three, whatever fits you. Put it on your page.
2. Write, now, what you'll do in your next red week. Decide it while nothing is wrong.
3. Look back at your last bad run. Did you follow your plan? Answer honestly.
4. Next time you're down, post on the board before you do anything else.

**Read this**

*Atomic Habits*, James Clear. The plateau of latent potential: results lag effort, and most people quit inside the lag. A red week is the lag, felt in real time.

**Put this on**

*Find Your Strength*, Lenzspot. For the Friday of a week that went badly, when you'd very much like to stop.
"""),

    ("""Revenge Trading, And How It Starts""",
     """
Nobody sits down intending to revenge trade. That's what makes it dangerous. It arrives dressed as determination.

**How it actually goes**

You take a loss. Not a disaster, a normal one. But it stings more than usual, maybe because of what else is going on that day.

And something in you says: *I'll get that back.*

That sentence is the whole thing. Everything expensive follows from it.

You take the next setup slightly early, because waiting feels unbearable. You size up a little, because getting it back at normal size would take too long. It goes against you, and now you're properly down, and the urge doubles.

By the end you've lost several times the original amount, and none of the later trades were ones you'd have taken on a calm morning.

**Why it's so hard to spot**

Because it feels like the right response. It feels like refusing to be beaten, and you have been rewarded your whole life for refusing to be beaten.

In most of life, pushing harder when knocked back works. In markets it's the single most reliable way to turn a small problem into a serious one, because the market does not know you're behind and does not care.

**The tell**

One question, and it's reliable: **would I take this trade if the last one had won?**

If the honest answer is no, you are not trading. You are trying to feel better.

That question takes three seconds and has saved more accounts than any indicator.

**What to do instead**

**Get up.** Physically. The screen is the thing keeping the urge alive. Leave it for twenty minutes.

**Have a hard stop.** Two losses in a row and you're finished for the day, no negotiating. Not because two losses matter, but because you cannot trust your judgement in that state, and you know it in advance.

**Write the loss down.** Not the number, what happened. Naming it converts it from a feeling you're carrying into an event that happened. Feelings drive revenge. Events don't.

**Make the next trade smaller.** Not bigger. If you must trade, halve it. It breaks the mechanism because a small win won't get it back, and knowing that removes the point of the exercise.

**Be fair to yourself about it**

If you've done this, you're not weak and you're not unusual. Almost everyone does, and most people do it more than once before it stops.

What changes it isn't willpower. It's a rule you wrote down when you were calm, and being honest enough to admit which state you're in.

**Your work this week**

1. Write your hard stop and put it on your one-page plan.
2. Write the question somewhere you'll see it while trading: *would I take this if the last one had won?*
3. Next loss, write one line about what happened before you do anything else.
4. If you've revenge traded before, write what it cost you. Not to punish yourself. So it's a fact rather than a fog.

**Read this**

*The Mountain Is You*, Brianna Wiest. On self-sabotage, and why we destroy the thing we're building. Revenge trading is that pattern with money attached.

**Put this on**

*Step Into Your Power*, Lenzspot. Put it on when you get up from the desk. Change the state, not just the screen.
"""),

    ("""Sizing When You're Scared""",
     """
Fear does not make you size down carefully. It makes you size wrong in both directions, and it's worth understanding which is happening.

**The two fear responses**

**Shrinking.** Your plan says 0.05 and you take 0.01 because the number frightens you. It feels responsible. It isn't, because you've now abandoned the plan and you're deciding with your feelings.

The cost is subtle: you take the win and it's tiny, so you feel behind, so eventually you overcorrect and take a size you can't handle. Shrinking today is often what causes oversizing next month.

**Grabbing.** The other direction. You're behind, or you're certain about this one, and you go bigger than planned. Certainty is a feeling, not information. Every trader who has done real damage did it on a trade they were sure about.

**Both are the same problem: your size came from your mood.**

**The fix is that the stop decides the size**

Your lot size is not a confidence level. It's arithmetic.

Decide your risk in pounds first, from your account and your percentage. Look at how far the stop sits from entry. Divide. That's your size. Your feelings do not appear anywhere in that calculation, which is the entire point.

If the number that comes out frightens you, the problem isn't the number, it's the percentage, and you should lower the percentage on paper rather than fudging it in the moment.

**Work it out before you look at the chart**

This matters more than it sounds. Once you're looking at price moving, you are no longer neutral.

Decide the risk percentage on a Sunday. Then in the moment, all you're doing is arithmetic against the stop distance. No judgement calls while your heart rate is up.

**When fear is telling you something real**

Sometimes it isn't noise. Two cases worth respecting:

**You're trading money you can't afford to lose.** Fear here is correct and you should listen to it. The answer isn't to be braver, it's to trade less. This has to be money you're genuinely happy to lose.

**You don't understand the trade.** Fear from not understanding is useful. The answer is the course, or a question in the group, not a smaller size on a trade you can't explain.

Anything else is just unfamiliarity, and unfamiliarity fades with repetition.

**Your work this week**

1. Set your risk percentage now, away from the charts. 1% to 3%.
2. Calculate the size for your next three trades from the stop distance. Write the sum out.
3. Take the size the sum gives you. Don't adjust it because of how you feel.
4. If it frightens you, ask honestly: is this money I can afford to lose? If not, that's the thing to fix.
5. Note which way you lean under pressure, shrinking or grabbing. Knowing your direction is most of managing it.

**Read this**

*The Psychology of Money*, Morgan Housel. On risk, and on why the goal is staying in the game rather than maximising any single outcome.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. Before you sit down. Calm first, then arithmetic.
"""),

    ("""Patience Is The Whole Edge""",
     """
Nearly every expensive mistake in trading is impatience wearing a different outfit. Once you see that, a lot of separate problems turn out to be one problem.

**The list**

Entering before the setup completes, because waiting is uncomfortable. Closing early, because holding is uncomfortable. Sizing up to get there faster. Chasing a move you missed. Trading on a day with nothing there, because you showed up and want something to happen.

Every one of those is the same thing: an inability to sit still.

**Why doing nothing feels wrong**

You have been rewarded your whole life for effort. Working harder produced better outcomes at school, at work, at home.

Markets break that rule, and it's disorienting. Here, sitting on your hands through a bad morning is frequently the highest-value thing you'll do all day. It produces nothing you can point to, and it feels like failure.

**The most useful reframe you'll get**

**Not trading is a position.**

When you don't take a trade you were unsure about, you didn't miss out. You made a decision, and it was probably a good one. The absence of a loss is a real result, it just doesn't appear anywhere you can see it.

Women who count "trades I sensibly skipped" as wins last far longer than women who count only what they took.

**How to actually build it**

**Have written entry criteria.** Not a feeling about the chart. Specific conditions. If they aren't all there, there's no trade. This turns patience from willpower into a checklist, and checklists are much easier to follow than resolve.

**Cap your trades per day.** Three, say. When you know you only have three, you stop spending them on marginal setups.

**Give yourself something else to do.** Most overtrading is boredom. Have the course open. Do the journal. Anything so the screen isn't the only thing in front of you.

**Log the skips.** Write down the trades you didn't take and what happened next. Most weeks this is the most encouraging thing you'll read, because you'll see how many bullets you dodged.

**The long version**

Patience isn't only about the hour. It's about the year.

Compounding does almost nothing visible for a long time and then does a great deal quickly. The women who get the second part are the ones who stayed for the first, which is dull, unrewarding, and where nearly everybody leaves.

**Your work this week**

1. Write your entry criteria. Specific enough that someone else could apply them.
2. Set a maximum number of trades per day.
3. Log every trade you skip and what happened after.
4. At the end of the week, read the skip log. Count what it saved you.

**Read this**

*Atomic Habits*, James Clear. On systems rather than goals, and why the people who win are usually the ones doing the unremarkable thing repeatedly.

**Put this on**

*Find Your Strength*, Lenzspot. For a slow morning when there's nothing there and you're itching to make something happen.
"""),

    ("""Money Is Just A Tool""",
     """
The last one in this section, and it's the reframe that takes the heat out of all of it.

**What money actually is**

Money is a tool. It isn't a scoreboard, it isn't a verdict on you, and it doesn't say anything about your worth as a person.

That sounds obvious written down. Almost nobody lives as though it's true. We attach a great deal to it: safety, status, proof, permission. Which is why a losing week doesn't feel like a losing week, it feels like a judgement.

**Why this matters practically**

If money is a scoreboard, every loss is a personal failure, and you'll behave accordingly. You'll hide it. You'll chase it. You'll avoid looking at the account. You'll make emotional decisions about a number that is simply doing what numbers do.

If money is a tool, a loss is a cost. Unpleasant, but information rather than indictment.

The women who last are almost always the ones who've made that shift. Not because they care less, but because they've stopped confusing the balance with themselves.

**What it's a tool for**

This is worth answering properly, because "more" is not an answer and it's the one most people stop at.

More money for what? Time? Safety? The ability to leave? A childhood for someone that's different to yours?

If you can't answer, you'll never feel like you have enough, because there's no amount that satisfies an unnamed want. You'll hit the number and immediately move it, and wonder why arriving felt flat.

**The other side of the tool**

A tool cuts both ways. Money magnifies whoever you already are.

Someone anxious with £500 is usually anxious with £50,000, just about bigger things. Someone impulsive gets more impulsive with more to be impulsive with.

Which is why this whole course exists. The mindset isn't a nice extra alongside the trading. It's the thing that determines what the money does to you when it arrives.

**Where this leaves you**

You're building a skill that produces money, which buys you options. That's all, and it's enormous.

Not a different personality. Not automatic happiness. Options. The option to leave, to say no, to help someone, to stop worrying about a specific set of things.

Hold it that way and the losses stop being about you, and the wins stop having to carry more than they can.

**Your work this week**

1. Finish this: *I want more money so that I can ______.* Keep going until it's specific.
2. Write what you'd stop doing if money weren't a worry. Then check whether any of it is available now.
3. Next loss, say out loud: that was a cost, not a verdict. Notice whether it lands differently.
4. Write your number. Then write what you'd do the day after you hit it.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki. On money as something you put to work rather than something you exchange your hours for. The right one to finish this section on.

**Put this on**

*I Don't Manifest, I Decide*. You've spent this course taking the emotion out of money. This is what deciding sounds like.
"""),

    ("""The Gap Between Who You Are And Who You Could Be""",
     """
There's a distance between the woman you are on a Tuesday and the woman you know you could be. Most women live in that gap and treat it as a life sentence.

It isn't. It's a to-do list you've never written down.

**Why the gap feels permanent**

Because you experience yourself as a fixed thing. *I'm just not confident. I've never been good with money. I'm not the type.*

Every one of those is a description of your past behaviour dressed up as a fact about your nature. You are describing a pattern and calling it a personality.

Patterns change. Personalities feel like they can't. That's why the language matters more than it sounds.

**Say it differently and watch what happens**

*I'm not confident* becomes *I haven't practised being confident.*
*I'm bad with money* becomes *nobody taught me this and I'm learning it now.*
*I'm not the type* becomes *I've never done it, which is different.*

Read those pairs again. The first of each closes a door. The second one leaves it open and hands you something to do.

You have not been failing at being her. You've been describing yourself in a way that made trying feel pointless.

**The gap is instructions**

Everything in that gap is a specific, learnable thing.

She's calm about money, you're not, and calm about money is a skill built by understanding what you're looking at. She asks directly, you hedge, and asking directly is a habit built by asking directly and surviving it. She holds her nerve in a bad week, you spiral, and holding your nerve is a rule written down in advance.

None of it is character. All of it is practice.

**The uncomfortable part**

Once you see the gap as instructions, you lose the excuse. That's genuinely uncomfortable and it's why most women prefer the fixed version.

If it's your personality, you're off the hook. If it's practice, then the only reason you're not her yet is that you haven't started, and that lands differently.

Sit with that for a minute rather than skipping past it.

**Your work this week**

1. Write three sentences you say about yourself that start with *I'm not* or *I've never been*.
2. Rewrite each one as a practice sentence instead of an identity sentence.
3. Pick the one that stings most. That's the one that's been costing you.
4. Write what practising it would actually look like this week. Small.
5. Do that thing once.

**Read this**

*Mindset*, Carol Dweck. The whole book is the difference between "I am this way" and "I haven't learned this yet", and what happens to people who make that switch.

**Put this on**

*Find Your Strength*, Lenzspot. For the moment you write the sentence that stings.
"""),

    ("""What She Wears And Why It Matters""",
     """
This sounds superficial. It isn't, and the reason is worth understanding.

**What's actually happening when you get dressed**

You're not dressing for other people. You're telling yourself who you are today, before you've had a chance to argue.

Leave the house in whatever was on the chair and you've told yourself: today is a day I'm getting through. Dress deliberately and you've told yourself: today is a day I'm showing up for.

That message lands before you've spoken to anyone. It sets what you'll tolerate, what you'll ask for, how you'll sit in a room.

**This is not about money**

Nothing here requires spending anything. Deliberate is not expensive.

It's the difference between clothes that happened to you and clothes you chose. Whether it fits properly. Whether you'd be comfortable being seen. Whether you feel like yourself in it or like someone apologising.

A woman in a plain shirt that fits her properly reads completely differently to a woman in something expensive she's uncomfortable in.

**The test**

Look in the mirror before you leave and ask one question: **would she wear this?**

Not would a magazine approve. Would the version of you who has it handled walk out in this.

Some days yes. Some days you'll realise you're dressed to be overlooked, and that's information about what you were expecting from the day.

**Why it changes your behaviour**

Because how you're dressed changes how you carry yourself, and how you carry yourself changes how you're treated, and how you're treated feeds straight back into what you believe you're worth.

It's a loop, and you can get into it at the clothes end. That's the useful part. You can't decide to feel worthy, but you can decide what you put on, and the feeling follows more often than you'd expect.

**Where it shows up in the rest of it**

Women who dress like they matter negotiate differently. They interrupt less apologetically. They take the size their plan says. Not because a blazer does anything, but because they started the day having told themselves something true.

**Your work this week**

1. Three days this week, dress deliberately. Not expensively. Deliberately.
2. Before you leave, ask: would she wear this?
3. Notice how you sit, how you speak, what you ask for on those days.
4. Compare to a day you didn't. Write down what was different.
5. Get rid of one thing you keep wearing that makes you feel small.

**Read this**

*Playing Big*, Tara Mohr. On presence and how women signal authority, or fail to, before saying a word.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. While you get ready. That's exactly what it's for.
"""),

    ("""The Voice In Your Head""",
     """
You have an internal commentary running most of the day, and you didn't write it. You absorbed it, usually before you were fifteen, and you've been playing it back in your own voice ever since.

**Listen to it properly for once**

Most women have never actually listened to how they speak to themselves. It's so constant it becomes weather.

So catch it. Next time something goes wrong, notice the exact words.

*Idiot.* *Typical.* *You always do this.* *Who did you think you were.*

Would you say that to a friend who'd made the same mistake? Not a version of it. Those words, in that tone.

You wouldn't. You'd be appalled at yourself. And yet.

**Where it came from**

Somebody's voice. A parent, a teacher, someone at school, someone you loved who was careless with you.

You took it in when you were too young to evaluate it, and now it's indistinguishable from your own thinking. It feels like discernment. It feels like being realistic about yourself.

It isn't. It's a recording.

**Why it matters here**

Because that voice is what stops you asking questions in the group. It's what makes a losing week feel like a verdict. It's what makes you undersize, hedge, apologise, and stay small.

You cannot build wealth while running a commentary that tells you you're not the sort of person who does.

**How to change it**

Not by silencing it. That doesn't work and trying is exhausting.

**Notice it.** Catch the sentence. Say to yourself: that's the voice.

**Ask whose it is.** Often you'll know immediately, and the moment you can name it, it loses a lot of its authority. A recording is not the truth.

**Answer it.** Not with a slogan. With something specific and true. *I've been doing this eight weeks and I understand things I didn't in week one.* Facts work better than affirmations against a voice like this.

**Then keep going anyway.** You don't have to win the argument. You have to not obey it.

**Your work this week**

1. Catch three sentences this week. Write them exactly.
2. Next to each, write whose voice it sounds like.
3. Next to that, write what you'd say to a friend in the same situation.
4. Next time it fires, answer it out loud with one true fact.
5. Notice whether it gets quieter by the end of the week. It usually does.

**Read this**

*The Mountain Is You*, Brianna Wiest. On the internal patterns that keep us where we are, and why they're usually trying to protect us badly.

**Put this on**

*Step Into Your Power*, Lenzspot. For the morning after a day the voice won.
"""),

    ("""Acting Before You Feel Ready""",
     """
You are waiting to feel ready. It is not coming, and this lesson is about what to do with that.

**Ready is not a starting condition**

Readiness is a feeling that arrives after competence, and competence arrives after doing the thing badly for a while. So waiting to feel ready before you start is waiting for the last step to happen first.

Everyone who looks confident to you was uncomfortable at the beginning. You are comparing your inside to their outside, and their outside is several years further along.

**What the fear is actually saying**

Your brain flags unfamiliar as dangerous. It cannot distinguish between genuine risk and something you simply haven't done before, so it produces the same signal for both.

That means the feeling you're reading as *I'm not ready* is frequently just *I haven't done this yet*. Same sensation. Completely different meaning.

Learning to tell them apart is one of the most valuable things you'll do.

**How to tell the difference**

Ask: **is there a real cost here I can name?**

If yes, that's useful fear. Trading money you can't afford to lose. Taking a position you don't understand. Listen to that.

If you can't name a specific cost, and it's just discomfort, that's unfamiliarity. That one you walk through.

**Make it small enough to survive**

The way you act before you're ready isn't bravery. It's shrinking the stakes.

Not "start trading properly". Place one 0.01 trade. Not "become confident". Ask one question. Not "overhaul my finances". Open the statement.

Small enough that being wrong costs almost nothing, then be wrong early and often while it's cheap. That's not recklessness. It's the cheapest way anyone ever learns.

**The thing nobody tells you**

You will do the thing, badly, feeling like a fraud. And then it'll be done. And you'll notice you survived it.

And the next one is very slightly easier. Not because you got braver, but because your brain now has evidence, and evidence beats reassurance every time.

That's the whole method. There's nothing more sophisticated underneath it.

**Your work this week**

1. Write the thing you keep not doing because you don't feel ready.
2. Ask: can I name a real cost? Write the answer.
3. If you can't, shrink the thing until it's almost trivially small.
4. Do the small version this week.
5. Afterwards, write one line: what actually happened. Keep it. It's evidence.

**Read this**

*Atomic Habits*, James Clear. On starting absurdly small, and why that beats motivation every time.

**Put this on**

*Step Into Your Power*, Lenzspot. Before the thing. Not after.
"""),

    ("""Her Standards""",
     """
The difference between where you are and where she is isn't usually talent or luck. It's what each of you tolerates.

**Standards are what you accept, not what you want**

Everyone wants good things. That's free and it means nothing.

Standards are different. Standards are what you refuse to put up with, and they show in what's actually in your life right now.

If you tolerate being paid less than you're worth, that's a standard. If you tolerate a group chat that leaves you flat, that's a standard. If you tolerate not understanding your own money, that's a standard.

None of them were chosen. All of them are being maintained.

**Raising one changes several**

The interesting thing is standards don't sit in isolation.

Decide you will no longer trade a setup you don't understand, and you'll find you also stop nodding along in meetings when you've lost the thread. Decide you'll ask what a fee is, and you'll find it easier to ask for the rate.

They're all the same muscle. Which means you don't have to fix everything at once. Raise one properly and the others start to shift on their own.

**What hers might look like**

She doesn't trade money she can't afford to lose. Ever, not usually.

She doesn't take a position she can't explain out loud.

She doesn't stay in a conversation that's making her smaller.

She doesn't apologise for asking about her own money.

She doesn't let a bad week go unexamined, and she doesn't let it become a verdict either.

**The cost of raising one**

Be honest with yourself: raising a standard costs you something immediately and pays later.

You'll have an awkward conversation. Someone will be surprised. You'll sit out a trade everyone else took. Short term, it's uncomfortable.

That's the trade. Most women decline it and call it keeping the peace, and the peace they keep is expensive.

**Your work this week**

1. Write three things you currently tolerate that she wouldn't.
2. Pick one. The one that made you wince.
3. Write the standard as a sentence. *I don't ______ any more.*
4. Hold it once this week, and notice the discomfort without acting on it.
5. Write what it cost, and what it bought.

**Read this**

*Playing Big*, Tara Mohr. Particularly on boundaries, and on the difference between being liked and being respected.

**Put this on**

*I Don't Manifest, I Decide*. A standard is a decision, not a preference.
"""),

    ("""Becoming Is Not A Straight Line""",
     """
Last one in this section, and it's the one that keeps you going when the rest of it stops feeling true.

**You will go backwards**

You'll have a fortnight where you're doing all of it, and then a week where you don't get dressed properly, don't do the page, hedge every question and hide from the account.

That will happen, and it will feel like proof that the previous fortnight was pretending.

It wasn't. Going backwards is part of the shape, not evidence against it.

**Why it happens**

Change isn't gradual. You move forward, your nervous system notices you're somewhere unfamiliar, and it pulls you back toward what it knows. Familiar feels safe even when familiar is what you're trying to leave.

That pull is not weakness. It's a system doing its job badly, and knowing that means you can stop treating it as a personal failing.

**The bit that decides it**

Not whether you slip. Everyone slips.

**How long you stay down.**

The woman who slips for a day and comes back is on a completely different path from the woman who slips for a day, decides it proves something, and stays there for four months.

Same slip. Entirely different outcome. The only variable is the story she told about it.

**What to do on the way back**

**Make the return absurdly small.** Not "get back on it properly". One lesson. One page. One deliberate morning. You're not rebuilding, you're just refusing to let the gap widen.

**Don't audit yourself first.** The urge to work out what went wrong before restarting is another way of not restarting. Start, then reflect later if you still want to.

**Don't start Monday.** Start now, in whatever bad shape you're in. Monday is how a week becomes a month.

**Look back further than yesterday**

On a bad week your memory only reaches back about three days, and everything in that window looks like failure.

So look further. Where were you three months ago? What did you not understand then that you understand now? What would three-months-ago you have done with the situation you handled last week?

This is why the journal matters. It's evidence, and on a bad week evidence is the only thing that argues back.

**Your work this week**

1. Write what you'll do the day after a slip. Decide it now, while nothing has gone wrong.
2. Make it small enough that you'd do it on your worst day.
3. Write three things you understand now that you didn't three months ago.
4. Keep that list. Read it the next time you go backwards.

**Read this**

*Mindset*, Carol Dweck. On how people interpret setbacks, and why that interpretation predicts almost everything about what happens next.

**Put this on**

*Find Your Strength*, Lenzspot. For the day you come back.
"""),

    ("""Why Independence Is The Real Goal""",
     """
Money is not the point. What money buys you is the point, and the thing worth buying is the ability to choose.

**What independence actually means**

Not being rich. Being able to say no.

No to a job that's grinding you down. No to a relationship you're staying in because leaving costs too much. No to work you don't want, clients who treat you badly, a version of your life someone else arranged.

Every one of those is bought with money you control. That's the whole thing.

**Why this matters more for women**

Because financial dependence has historically been how women got kept somewhere they didn't want to be. Not through anything dramatic. Through arithmetic: leaving costs money, and if you don't have any, the sums don't work.

You may never need it. Most women who build it never use it that way. But the woman who could leave and chooses to stay is in a completely different position from the woman who stays because she can't, even if their lives look identical from outside.

That difference is worth building for on its own.

**The number that matters more than income**

Most people chase income. Income is only half of it.

The number that decides your independence is **how many months you could cover if the money stopped tomorrow.**

Someone earning £30,000 with a year of expenses saved is freer than someone earning £90,000 with two weeks. The second one has a bigger number and less choice, and she can't take a risk, can't walk out, can't wait for something better.

Work out your number. Not your salary. Your months.

**Where trading fits**

Trading is one route to it, and it's important to be clear about what it is and isn't.

It isn't a replacement salary from day one. It's a skill that, built properly and slowly, produces money you control, in your own account, that nobody can withdraw from you.

That's the appeal. Not the lifestyle pictures. **Money that is yours, that you built, that doesn't depend on anyone's opinion of you.**

**Your work this week**

1. Work out your months. Everything you'd need for a month, then how many months you could cover now.
2. Write the number down. Most people have never calculated it and are surprised either way.
3. Write what you'd do differently if that number were twelve.
4. Write one thing you're currently tolerating that you'd stop tolerating at twelve months.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki. The distinction between working for money and building things that produce it is the foundation of everything in this section.

**Put this on**

*I Don't Manifest, I Decide*. Independence is a decision followed by a lot of unglamorous arithmetic.
"""),

    ("""The Boring Foundations Nobody Posts About""",
     """
Before trading, before investing, before anything with upside, there are three unexciting things that decide whether any of it survives contact with real life.

Nobody makes content about these because they aren't interesting. They're also the difference between building wealth and having a good year followed by a bad one.

**One: know your numbers**

Not budgeting. Just knowing.

What comes in each month. What goes out. What the gap is. Most people cannot answer that within £200, and you cannot manage something you can't see.

You don't need an app or a system. One page, once a month, ten minutes. In, out, difference.

The reason this matters here specifically: **you cannot work out what you can afford to risk until you know what you have.** Every sizing decision downstream depends on this number, and guessing it is how people end up trading money they needed.

**Two: a buffer that isn't invested**

Cash. Boring, accessible, doing nothing.

Three months of expenses if you can, one month to start. In a separate account you don't look at.

This is not wasted money sitting idle. It's what stops a broken boiler turning into a closed trading account. Without it, the first thing that goes wrong forces you to liquidate at the worst possible moment, and that's how people end perfectly good strategies.

The buffer is what lets you hold your nerve. It's a psychological instrument as much as a financial one.

**Three: expensive debt first**

If you're carrying debt at 20% or more, paying it down beats almost any return you could realistically make trading, and it does it with no risk at all.

That's not a fun sentence. It's true, and any honest person will tell you the same.

Clear the expensive stuff, keep the cheap stuff, then build.

**Why this comes before the exciting part**

Because the sequence matters. Trading on top of no buffer, unclear numbers and expensive debt isn't building wealth, it's adding volatility to a situation that's already fragile.

Get the boring three in place and everything after it is far more likely to survive.

**Your work this week**

1. One page: in, out, difference. Ten minutes.
2. Work out your current buffer in months.
3. List any debt over about 15%, with the rate next to it.
4. Decide the order: buffer first, or debt first, based on what you've written.
5. Set the amount you'll put toward it monthly. Small and consistent beats ambitious and abandoned.

**Read this**

*The Psychology of Money*, Morgan Housel. On why room for error is what actually keeps people in the game, and why the people who survive aren't the ones who optimised hardest.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. Boring admin goes faster with something behind it.
"""),

    ("""Money That Works Without You""",
     """
There's a ceiling on any money you earn by being present, and it's the number of hours you're awake.

The shift that changes everything is going from money you swap time for, to money that keeps working when you're not.

**The two kinds**

**Time for money.** A salary, an hourly rate, most freelancing. Stop and it stops. There's a hard limit and you'll hit it.

**Money that works.** Investments, a business that runs without you in it, skills that compound. It keeps going while you sleep.

Most people spend their whole lives entirely in the first category and never quite work out why they can't get ahead despite working harder every year.

**Where trading actually sits**

Be honest about this: **trading is not passive income.** Anyone who tells you it is, is selling something.

It requires your attention, your decisions, your discipline. It's closer to a skill you practise than a machine that runs.

What it does give you is different and still valuable: **a skill that produces money from capital rather than hours.** Once you have it, it doesn't leave, it isn't tied to an employer, and it scales with capital rather than with time.

That's a real thing. It's just not the same as passive, and expecting passive is how people get disappointed and quit something that was working.

**Making it compound**

The bit most people skip: what you do with what you make.

Take out everything you earn and you've made a second wage. Leave a portion in, and the base grows, and the same percentage return produces more next year.

Nothing about that is dramatic and it's most of how wealth actually happens. Boring, slow, and then suddenly not.

Decide in advance what proportion you keep in. Write it in your plan. Otherwise you'll decide it in the moment, when you want something, and you'll always decide the same way.

**Your work this week**

1. Write what proportion of your income comes from time versus from capital. For most people it's 100% to 0%.
2. Write what you want that to be in five years.
3. Decide what proportion of trading profits stays in the account. Write it in your plan.
4. Write one thing you could build that would keep working without you.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki. Read the asset and liability chapters twice. That distinction is the whole lesson and it changes how you look at every purchase.

**Put this on**

*Find Your Strength*, Lenzspot. For the month you first leave profit in the account instead of taking it out.
"""),

    ("""Keeping It""",
     """
Making money and keeping money are different skills, and almost nobody talks about the second one because it isn't exciting.

**Why keeping is harder**

Making money takes optimism, effort and a willingness to take risk. Keeping it takes the opposite: caution, patience, and a tolerance for being boring while other people appear to be doing better.

The same qualities that get you the money can lose it. Confidence gets you in and then makes you oversize. Optimism finds the opportunity and then ignores the warning signs.

Which is why you meet people who've made money more than once and still don't have any.

**Lifestyle creep**

The most common way it goes. Income rises, spending rises to match, and you're in exactly the same position with bigger numbers.

The way out is unglamorous: **when income rises, raise your spending by less than it rose.** Not none. Less. If you make an extra £500 a month, take £150 of lifestyle and put £350 to work.

You still feel the improvement. You just don't consume all of it.

Do that consistently and the gap between what you earn and what you spend widens every year, and that gap is the entire mechanism of building wealth.

**Taking profit off the table**

Specific to trading, and it matters.

Decide in advance at what point you withdraw some. Not all, not never. A rule.

Something like: when the account passes a level, take a set percentage out and put it somewhere it can't be traded. Otherwise you'll grow it for a year and give it back in a fortnight, and you'll have nothing to show for a year of good decisions.

The women who end up with something are the ones who moved money out of reach of their own worst days.

**Not telling everyone**

A quiet point. Announcing what you've made changes how people treat you, and it invites requests, opinions and comparison you didn't ask for.

You don't owe anyone your numbers. Your two people, and that's enough.

**Your work this week**

1. Write what happened last time your income went up. Where did it go?
2. Set your split for the next rise. Some lifestyle, more to work.
3. Write your withdrawal rule for trading. A level and a percentage.
4. Put it on your one-page plan so it's decided while you're calm.

**Read this**

*The Psychology of Money*, Morgan Housel. The chapters on getting wealthy versus staying wealthy are the clearest thing on this anywhere, and they're short.

**Put this on**

*Morning Queen Energy*, Shift to Abundance. For the day you set the rule.
"""),

    ("""Your Wealth Plan""",
     """
This is the one where the whole section turns into something you can actually follow.

Not a budget. A plan: where you are, where you're going, and what happens each month to close the gap.

**One page, five parts**

**Where you are now.** Your months of buffer. Your expensive debt. Your monthly gap between in and out. Honest numbers, not aspirational ones. This is the only part people fudge and fudging it makes the rest useless.

**Where you want to be, with a date.** Not "comfortable". Twelve months of expenses covered by December next year. A number and a date.

**The order.** Buffer, then expensive debt, then building. Or debt first if the rates are punishing. Decide it once, write it down, stop relitigating it every month.

**What happens monthly.** The actual amount that moves, and where. Automatic if possible, so it doesn't depend on how you feel that month.

**Your trading rules.** Risk per trade. Withdrawal rule. What proportion of profit stays in. These belong here rather than in a separate document, because they're part of the same plan.

**Why writing it down works**

Because otherwise every month is a fresh negotiation with yourself, and you will lose most of them. A decision made once, calmly, in advance, beats twelve decisions made while tired.

It also makes progress visible. Without it, a year of small correct decisions feels like nothing happened. With it, you can see the buffer went from one month to four, and that's a completely different feeling.

**Review it quarterly, not weekly**

Weekly is anxiety. Quarterly is management.

Four times a year, sit down for half an hour, update the numbers, and notice what changed. Adjust if something isn't working. Otherwise leave it alone and let it run.

**Share the parts you can**

Post it on the board, or the bits you're comfortable with. Two reasons: saying it out loud makes it real, and someone reading yours will finally write theirs.

**What happens now**

You have the story you inherited, what you think you're worth, how to hold your nerve, and now the arithmetic underneath it.

None of it is complicated. All of it is unglamorous and repeated. That's genuinely what separates women who build something from women who meant to.

**Your work this week**

1. Write the page. All five parts. Real numbers.
2. Set up whatever moves automatically, so it doesn't need you.
3. Put a quarterly reminder in your calendar now.
4. Read it alongside your one-page trading plan. They should agree with each other.
5. Post what you're comfortable sharing on the board.

**Read this**

*Rich Dad Poor Dad*, Robert Kiyosaki, then *The Psychology of Money*, Morgan Housel. The first for direction, the second for staying power. Between them you have most of what matters.

**Put this on**

*I Don't Manifest, I Decide*. You started this course with a story you inherited. You're finishing it with a plan you wrote.
"""),

]


# course slug, title, one line, and which sections belong to it.
# Sections are defined in HER_CATEGORIES below.
HER_COURSES = [
    ("becoming-her", "Becoming Her",
     "Identity first. Believing you're her, and showing up as her, long before the money follows.",
     ["Believing You're Her", "Your Why & Vision Board", "How She Shows Up", "Rewiring The Old You"]),
    ("manifesting", "Manifesting & Goals",
     "Deciding rather than wishing. Getting specific, then building the path to it.",
     ["Manifesting & Goals"]),
    ("money-mindset", "Money Mindset",
     "The stories underneath every financial decision, and holding your nerve in the market.",
     ["Your Money Story", "Your Head In The Market", "Independence & Wealth"]),
    ("mindset-shift", "Mindset Shift",
     "How you see yourself, what you think you're worth, and the people around you.",
     ["Confidence & Resilience"]),
]


HER_CATEGORIES = [
    ("Believing You're Her",
     "Deciding you are her now, and behaving accordingly, long before it feels true.",
     ["Believing You're Her: You Are Not Waiting",
      "The Woman You Were Told To Be",
      "The Gap Between Who You Are And Who You Could Be",
      "What She Wears And Why It Matters",
      "The Voice In Your Head",
      "Acting Before You Feel Ready",
      "Her Standards",
      "Becoming Is Not A Straight Line"]),
    ("Your Why & Vision Board",
     "What you are actually building, and keeping it in front of you.",
     ["Your Why, And The One Underneath It",
      "The Vision Board That Actually Works"]),
    ("How She Shows Up",
     "The way you take up space, speak, and carry yourself in a room.",
     ["How She Shows Up"]),
    ("Rewiring The Old You",
     "Habits that survive a bad week, and the return after you slip.",
     ["Rewiring The Old You"]),
    ("Manifesting & Goals",
     "Deciding rather than wishing, then protecting it long enough to arrive.",
     ["Deciding, Not Wishing",
      "Getting Specific Enough To Be Uncomfortable",
      "Affirmations That Aren't Nonsense",
      "Gratitude, And Why It Isn't Soft",
      "Scripting Your Life Forward",
      "Not Everyone Will See It",
      "Blocking Out The Noise",
      "When It's Slow",
      "Receiving It",
      "Your Plan, In One Page"]),
    ("Your Money Story",
     "Where your beliefs about money came from, and what they are costing you.",
     ["The Money Story You Inherited",
      "What You Think You're Worth",
      "Abundance Without The Nonsense"]),
    ("Your Head In The Market",
     "Holding your nerve when it is going badly, and staying calm when it is going well.",
     ["Holding Your Nerve In A Red Week",
      "Revenge Trading, And How It Starts",
      "Sizing When You're Scared",
      "Patience Is The Whole Edge",
      "Money Is Just A Tool"]),
    ("Independence & Wealth",
     "The arithmetic underneath it. Buffers, keeping what you make, and a plan you follow.",
     ["Why Independence Is The Real Goal",
      "The Boring Foundations Nobody Posts About",
      "Money That Works Without You",
      "Keeping It",
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
<section class="her-hero-dark">
  {HER_ART_SVG}
  <div class="wrap">
    <span class="hero-tag her-tag">Members only</span>
    <h1>Welcome to<br><em>Female Wealth.</em></h1>
    <p class="lede">Your private space. Masterclasses on becoming her, mindset work,
       and a circle of women building exactly what you're building.</p>
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

    <a href="/her/hub" class="her-shout">
      <span class="her-shout-tag">New</span>
      <h3>The women's board</h3>
      <p>Post anything, get real answers from the women here, and keep the conversation
         where you can find it again. Not a group chat. Yours.</p>
      <span class="her-shout-cta">Open the board →</span>
    </a>

    <a href="/her/journal" class="her-card">
      <span class="her-num">01</span>
      <span class="her-title">The Daily Page<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">One question a day, just for you</span></span>
      <span class="her-arrow">→</span>
    </a>
    <a href="/her/courses" class="her-card">
      <span class="her-num">02</span>
      <span class="her-title">The courses<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">Becoming Her, Manifesting, Money Mindset, Mindset Shift</span></span>
      <span class="her-arrow">→</span>
    </a>
    <a href="/her/videos" class="her-card">
      <span class="her-num">03</span>
      <span class="her-title">Training videos<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">Recorded sessions and walkthroughs, watch back any time</span></span>
      <span class="her-arrow">→</span>
    </a>
    <a href="/her/share" class="her-card">
      <span class="her-num">04</span>
      <span class="her-title">Share &amp; support<br><span style="font-family:'Inter'; font-size:13px; color:var(--ink-dim);">Share your results or get help any time</span></span>
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

  </div>
</section>
"""
    return render_template_string(base_layout("Female Wealth", content, "community", theme="plum"))


@app.route("/her/courses")
def her_courses():
    """The library index. Each course opens into its own sections."""
    if not has_access("her") and not session.get("admin"):
        return redirect(url_for("community"))

    cat_titles = {c[0] for c in HER_CATEGORIES}
    cards = ""
    for slug, title, blurb, sections in HER_COURSES:
        count = sum(len(c[2]) for c in HER_CATEGORIES if c[0] in sections)
        live = any(c[0] in cat_titles for c in HER_CATEGORIES if c[0] in sections)
        meta = (f"{len(sections)} section{'s' if len(sections) != 1 else ''} · "
                f"{count} lesson{'s' if count != 1 else ''}") if count else "Coming soon"
        cards += (
            f'<a href="/her/course/{slug}" class="her-card">'
            f'<span class="her-title">{esc(title)}'
            f'<br><span style="font-family:\'Inter\'; font-size:13px; color:var(--ink-dim);">'
            f'{esc(blurb)}</span>'
            f'<br><span class="hub-meta">{meta}</span></span>'
            f'<span class="her-arrow">→</span></a>'
        )

    content = f"""
<section style="padding:52px 0 70px;">
  <div class="wrap" style="max-width:720px;">
    <a href="/her" class="inline-link" style="font-size:13px;">← Back to Female Wealth</a>
    <div class="section-head" style="max-width:100%; margin:22px 0 30px;">
      <span class="eyebrow">Members only</span>
      <h1 style="font-size:34px; margin:10px 0 12px;">The courses</h1>
      <p>Work through them in order or go straight to what you need. Nothing expires
         and nothing is locked once you're in.</p>
    </div>
    {cards}
  </div>
</section>
"""
    return render_template_string(base_layout("Courses", content, "community", theme="plum"))


@app.route("/her/course/<slug>")
def her_course(slug):
    if not has_access("her") and not session.get("admin"):
        return redirect(url_for("community"))

    course = next((c for c in HER_COURSES if c[0] == slug), None)
    if not course:
        return redirect("/her/courses")
    _, title, blurb, section_names = course

    title_to_idx = {t: i for i, (t, _) in enumerate(HER_MASTERCLASSES)}
    blocks = []
    for cat_name, cat_desc, titles in HER_CATEGORIES:
        if cat_name not in section_names:
            continue
        items = "".join(
            f'<a href="/her/masterclass/{title_to_idx[t]}" class="her-card small">'
            f'<span class="her-title">{esc(t)}</span><span class="her-arrow">→</span></a>'
            for t in titles if t in title_to_idx)
        blocks.append(
            f'<div style="margin-bottom:40px;">'
            f'<h2 style="font-family:\'Fraunces\',serif; font-size:23px; margin:0 0 6px;">{esc(cat_name)}</h2>'
            f'<p style="color:var(--ink-dim); font-size:14px; margin:0 0 16px;">{esc(cat_desc)}</p>'
            f'{items}</div>')

    body = "".join(blocks) or (
        '<div class="callout">This one is being written now. It will appear here as soon as '
        'it is ready, and you will not need to do anything to get it.</div>')

    content = f"""
<section style="padding:52px 0 70px;">
  <div class="wrap" style="max-width:720px;">
    <a href="/her/courses" class="inline-link" style="font-size:13px;">← All courses</a>
    <div class="section-head" style="max-width:100%; margin:22px 0 32px;">
      <span class="eyebrow">Female Wealth</span>
      <h1 style="font-size:32px; margin:10px 0 12px;">{esc(title)}</h1>
      <p>{esc(blurb)}</p>
    </div>
    {body}
  </div>
</section>
"""
    return render_template_string(base_layout(title, content, "community", theme="plum"))


@app.route("/her/masterclasses")
def her_masterclasses():
    """Old link, kept so nothing anyone saved breaks."""
    return redirect("/her/courses")


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
    return render_template_string(base_layout("Mindset", content, "community", theme="plum"))


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
    return render_template_string(base_layout("Share & Support", content, "community", theme="plum"))


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
    <div class="reading"><div class="course-content" style="max-width:100%;">{body_html}</div></div>
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
    <div class="reading"><div class="course-content" style="max-width:100%;">{body_html}</div></div>
    <div style="display:flex; justify-content:space-between; margin-top:50px; padding-top:30px; border-top:1px solid var(--line);">
      {prev_html}{next_html}
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout(title, content, "community"))


@app.route("/community")
def community():
    community_quotes = "".join(
        f'<blockquote class="say"><p>{esc(q["quote"])}</p>'
        f'<cite>{esc(q.get("who") or "Member")}</cite></blockquote>'
        for q in COMMUNITY_FEEDBACK
    )
    if has_access("her"):
        return her()
    login_prompt = ("" if session.get("member_id") else
        '<div class="callout" style="margin-bottom:20px;">Not logged in? '
        '<a href="/unlock" class="inline-link">Log in with your access code first</a> '
        'so this unlocks on your existing account.</div>')
    content = f"""
<section class="her-hero">
  <div class="wrap" style="max-width:760px; text-align:center;">
    {FW_MARK_SVG}
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

<section class="tinted tinted-edge">
  <div class="wrap" style="max-width:820px;">
    <div class="section-head" style="max-width:100%; text-align:center; margin-bottom:26px;">
      <span class="eyebrow" style="color:var(--rose);">From the women</span>
      <h2 style="font-size:30px;">What they said about it</h2>
    </div>
    <div class="says">{community_quotes}</div>
    <p class="risk-note" style="margin:26px auto 0;">Real messages from members, shared with
       permission. They describe one person's experience. Nothing here is financial advice, and
       past results are not a guide to future results.</p>
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
    return render_template_string(base_layout("Female Wealth", content, "community", theme="plum"))


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
