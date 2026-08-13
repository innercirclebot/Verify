"""
Inner Circle Verify Bot — webhook version
------------------------------------------
This version runs as a small website instead of a constantly-running
script, so it works on a free Render web service.

Setup:
1. Create a free GitHub account if you don't have one, and a new
   repository (e.g. "inner-circle-bot"). Upload this file and
   requirements.txt to it using GitHub's "Add file -> Upload files"
   button in the browser — no command line needed.
2. Give Claude the repository's URL. From there, Claude creates the
   Render web service, sets the BOT_TOKEN environment variable, and
   points Telegram's webhook at it automatically.

Storage: submissions are logged to submissions.csv in the same folder
as this file, one row per code issued. Note: on Render's free tier,
this file resets whenever the service restarts (e.g. after a period of
inactivity) — for now, treat it as a short-term log and not permanent
storage.
"""


import csv
import os
import random
import string
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

# Your personal Telegram numeric chat ID, so unanswered questions get
# forwarded to you. Optional — if not set, forwarding is just skipped.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

app = Flask(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# 1 deposit screenshot + 1 screenshot showing all 10 closed trades = 2
REQUIRED_PHOTOS = 2

LOG_FILE = Path(__file__).parent / "submissions.csv"

# In-memory count of photos received per chat since their last issued code.
# Note: this resets if the web app restarts/reloads. For a low-volume bot
# this is rarely an issue in practice, since clients usually send both
# screenshots back to back in one sitting.
photo_counts: dict[int, int] = {}


def generate_code() -> str:
    chars = string.ascii_uppercase.replace("O", "").replace("I", "") + string.digits.replace("0", "").replace("1", "")
    suffix = "".join(random.choices(chars, k=5))
    return f"IC-{suffix}"


def log_submission(code: str, user_id: int, username: str, photo_count: int) -> None:
    file_exists = LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["code", "telegram_user_id", "telegram_username", "photo_count", "timestamp_utc"])
        writer.writerow([code, user_id, username or "(no username set)", photo_count, datetime.now(timezone.utc).isoformat()])


def send_message(chat_id: int, text: str) -> None:
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)


def forward_to_admin(username: str, user_id: int, text: str) -> None:
    if not ADMIN_CHAT_ID:
        return
    who = f"@{username}" if username else f"user {user_id}"
    send_message(int(ADMIN_CHAT_ID), f"Unanswered question from {who}:\n\n{text}")


# Simple keyword -> answer FAQ. Checked in order; first match wins.
# Add more (keywords, answer) pairs here as you learn what people get stuck on.
FAQ = [
    (["deposit", "fund", "funding", "£300", "300", "minimum"],
     "Minimum deposit is £300. Whatever you deposit, we match 50% on top for free "
     "(e.g. deposit £1,000, we add £500). Fund through the broker's app/site after registering."),
    (["eur/usd", "eurusd", "pair", "quotes", "watchlist", "can't find", "cant find"],
     "In MT5, tap the search icon at the top of the Quotes tab, type EUR/USD, and tap it — "
     "it'll be added to your list."),
    (["volume", "lot", "0.01"],
     "Set Volume to 0.01 for every trade — tap the Volume field on the trade ticket and type it in."),
    (["buy", "sell", "how many trades", "10 trades"],
     "Place 10 trades total: 5 Buy and 5 Sell on EUR/USD, each at 0.01 volume. "
     "Tap 'Buy by Market' or 'Sell by Market' on the trade ticket."),
    (["close", "closing", "history"],
     "Go to the Trade tab, press and hold each open trade, then tap Close. Once all 10 are closed, "
     "take one screenshot showing all 10 in your closed trades / History list."),
    (["code", "verification code"],
     "You'll get your verification code here after sending both required screenshots "
     "(deposit confirmation + the closed-trades screenshot)."),
    (["mt5", "metatrader", "login", "log in", "password"],
     "Use the login details from your broker confirmation email to log into the MT5 app."),
    (["kudo", "broker", "register", "sign up", "signup"],
     "Register with the broker using the link on the website, then check your email for next steps."),
    (["stuck", "help", "confused", "problem", "issue"],
     "No worries — a team member will get back to you here shortly. You can also use the chat "
     "button on the website if you'd rather ask there."),
]


def find_faq_answer(text: str) -> str | None:
    lowered = text.lower()
    for keywords, answer in FAQ:
        if any(k in lowered for k in keywords):
            return answer
    return None


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message")
    if not message:
        return "ok"

    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = user.get("id")
    username = user.get("username")

    if "text" in message and message["text"] == "/start":
        send_message(
            chat_id,
            "Welcome to Inner Circle Verify.\n\n"
            "Send 2 screenshots here: (1) your deposit confirmation, and (2) your "
            "trade history screen showing all 10 closed trades (5 buy, 5 sell, "
            "0.01 lots). Once I've received both, I'll send you a verification "
            "code — enter that code in the form on the website to finish signing up.\n\n"
            "Stuck at any point? Just type your question and I'll try to help, "
            "or send /help for a quick topic list."
        )
        return "ok"

    if "text" in message and message["text"] == "/help":
        send_message(
            chat_id,
            "Ask me about: deposit/funding, finding EUR/USD, volume/lot size, "
            "placing the 10 trades, closing trades, MT5 login, or registering with the broker.\n\n"
            "Just type your question in plain English and I'll do my best to answer."
        )
        return "ok"

    if "photo" in message:
        photo_counts[chat_id] = photo_counts.get(chat_id, 0) + 1
        count = photo_counts[chat_id]

        if count < REQUIRED_PHOTOS:
            remaining = REQUIRED_PHOTOS - count
            send_message(chat_id, f"Got it ({count}/{REQUIRED_PHOTOS}). Send {remaining} more screenshot{'s' if remaining != 1 else ''}.")
            return "ok"

        code = generate_code()
        log_submission(code, user_id, username, count)
        photo_counts[chat_id] = 0
        send_message(
            chat_id,
            f"All {REQUIRED_PHOTOS} screenshots received.\n\n"
            f"Your verification code is: {code}\n\n"
            "Enter this exact code in the 'Verification Code' field on the "
            "Inner Circle sign-up form to complete your submission."
        )
        return "ok"

    if "text" in message:
        answer = find_faq_answer(message["text"])
        if answer:
            send_message(chat_id, answer)
        else:
            forward_to_admin(username, user_id, message["text"])
            send_message(
                chat_id,
                "Thanks for your message — a team member will get back to you here shortly. "
                "You can also use the chat button on the website. In the meantime, type /help "
                "for a list of topics I can answer instantly, or send your 2 screenshots to continue verification."
            )
        return "ok"

    # Any other message type (sticker, voice note, etc.) gets a nudge back to sending photos.
    send_message(
        chat_id,
        f"Please send your screenshots as photos (not text or files). "
        f"I need {REQUIRED_PHOTOS} total: 1 deposit screenshot + 1 screenshot showing all 10 closed trades."
    )
    return "ok"


@app.route("/", methods=["GET"])
def home():
    return "Inner Circle Verify bot is running."
