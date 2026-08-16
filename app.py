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
import secrets
import string
import markdown as md_lib
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, request, render_template_string, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
DATABASE_URL = os.environ.get("DATABASE_URL")
GOLD_GROUP_LINK = os.environ.get("GOLD_GROUP_LINK", "https://t.me/+etxMbgmMTW1mYTlk")
CURRENCY_GROUP_LINK = os.environ.get("CURRENCY_GROUP_LINK", "https://t.me/+your_currency_group_invite")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


def notify_admin(text: str) -> None:
    if not TELEGRAM_API or not ADMIN_CHAT_ID:
        return
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": int(ADMIN_CHAT_ID), "text": text}, timeout=10)
    except Exception:
        pass


def send_telegram_message(chat_id, text: str) -> bool:
    if not TELEGRAM_API or not chat_id:
        return False
    try:
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": int(chat_id), "text": text}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


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
                    created_at TIMESTAMP DEFAULT NOW(),
                    approved_at TIMESTAMP
                );
            """)
    finally:
        conn.close()


try:
    init_db()
except Exception:
    pass


def gen_access_code():
    return "AC-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(7))


def create_pending_member(tier, title, name, account_number, deposit_amount, phone,
                           telegram_username=None, verification_code=None, referred_by=None):
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
            cur.execute("""
                INSERT INTO members (tier, title, name, account_number, deposit_amount, phone,
                                      telegram_username, verification_code, referred_by, chat_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (tier, title, name, account_number, deposit_amount, phone,
                  telegram_username, verification_code, referred_by, chat_id))
            new_id = cur.fetchone()[0]
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
                UPDATE members SET status='approved', access_code=%s, approved_at=NOW()
                WHERE id=%s RETURNING *
            """, (code, member_id))
            row = cur.fetchone()
            return row
    finally:
        conn.close()


def mark_paid(member_id):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE members SET paid=TRUE WHERE id=%s RETURNING *", (member_id,))
            return cur.fetchone()
    finally:
        conn.close()


def get_pending_members():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE status='pending' ORDER BY created_at DESC")
            return cur.fetchall()
    finally:
        conn.close()


def get_approved_members():
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE status='approved' ORDER BY approved_at DESC")
            return cur.fetchall()
    finally:
        conn.close()


def find_member_by_access_code(code):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM members WHERE access_code=%s AND status='approved'", (code,))
            return cur.fetchone()
    finally:
        conn.close()


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
.member-bar {
  background: var(--bg-alt);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 72px;
  z-index: 45;
}
.member-bar .wrap {
  display: flex;
  align-items: center;
  gap: 22px;
  padding-top: 12px;
  padding-bottom: 12px;
  overflow-x: auto;
  white-space: nowrap;
  scrollbar-width: none;
}
.member-bar .wrap::-webkit-scrollbar { display: none; }
.member-bar-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--gold);
  flex-shrink: 0;
}
.member-bar a {
  font-size: 14px;
  color: var(--ink-dim);
  flex-shrink: 0;
  transition: color 0.15s ease;
}
.member-bar a:hover { color: var(--gold); }

@media (max-width: 860px) {
  .member-bar { top: 0; position: relative; }
  .member-bar .wrap { gap: 18px; }
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
"""

FONT_LINK = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">"""


def base_layout(title: str, content: str, active: str = "") -> str:
    def nav_class(key):
        return "active" if key == active else ""

    try:
        logged_in = bool(session.get("member_id"))
    except Exception:
        logged_in = False

    if logged_in:
        nav_cta = '<a href="/account" class="nav-cta">My Account</a>'
        member_bar = """
<div class="member-bar">
  <div class="wrap">
    <span class="member-bar-label">Your access</span>
    <a href="/account">My Account</a>
    <a href="/education/fundamentals/0">Fundamentals</a>
    <a href="/education/fundamentals/contents">All Lessons</a>
    <a href="/education/advanced/0">Advanced</a>
    <a href="/signals">Extra Signals</a>
    <a href="/community">Community</a>
  </div>
</div>
"""
    else:
        nav_cta = '<a href="/unlock" class="nav-cta">Log In</a>'
        member_bar = ""

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
      <a href="/onboarding">Onboarding</a>
      <a href="/education">Education</a>
      <a href="/community">Community</a>
      <a href="/signals">Extra Signals</a>
      <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" style="color: var(--gold);">Support</a>
    </div>
    {nav_cta}
  </div>
</nav>
{member_bar}
{content}
<a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="support-float">💬 Need help?</a>
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

    pending = get_pending_members()
    approved = get_approved_members()

    def row(m, show_approve=True, show_paid=False):
        fields = f"{m.get('title','')} {m['name']} · {m['tier']} · Acct {m['account_number']} · £{m['deposit_amount']} · {m['phone']}"
        extra = ""
        if m['tier'] == 'gold':
            extra = f"<br><span style='color: var(--ink-dim); font-size: 12px;'>Code entered: {m.get('verification_code','') or '(none)'}, chat linked: {'yes' if m.get('chat_id') else 'NO chat_id found'}</span>"
        else:
            extra = f"<br><span style='color: var(--ink-dim); font-size: 12px;'>Referred by: {m.get('referred_by','')}, Telegram: {m.get('telegram_username','')}, chat linked: {'yes' if m.get('chat_id') else 'NO chat_id found'}</span>"
        actions = ""
        if show_approve:
            actions = f'<form method="POST" action="/admin/approve/{m["id"]}" style="margin-top:10px;"><button type="submit" class="btn btn-primary" style="padding: 8px 18px; font-size: 13px;">Approve & Send</button></form>'
        if show_paid and not m.get('paid'):
            actions += f'<form method="POST" action="/admin/mark-paid/{m["id"]}" style="margin-top:10px; display:inline-block; margin-left:10px;"><button type="submit" class="btn btn-ghost" style="padding: 8px 18px; font-size: 13px;">Mark Paid (Advanced)</button></form>'
        return f'<div class="benefit" style="text-align:left;">{fields}{extra}{actions}</div>'

    pending_html = "".join(row(m) for m in pending) or "<p style='color: var(--ink-dim);'>Nothing pending.</p>"
    approved_html = "".join(row(m, show_approve=False, show_paid=True) for m in approved) or "<p style='color: var(--ink-dim);'>No approved members yet.</p>"

    content = f"""
<section style="padding: 60px 0;">
  <div class="wrap">
    <span class="eyebrow">Admin</span>
    <h1 style="font-size: 30px; margin: 10px 0 40px;">Pending & Members</h1>
    <h2 style="font-size: 20px; margin-bottom: 16px;">Pending ({len(pending)})</h2>
    <div class="grid5" style="grid-template-columns: 1fr; margin-bottom: 48px;">{pending_html}</div>
    <h2 style="font-size: 20px; margin-bottom: 16px;">Approved ({len(approved)})</h2>
    <div class="grid5" style="grid-template-columns: 1fr;">{approved_html}</div>
  </div>
</section>
"""
    return render_template_string(base_layout("Admin", content, ""))


@app.route("/admin/approve/<int:member_id>", methods=["POST"])
def admin_approve(member_id):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    member = approve_member(member_id)
    if member:
        group_link = GOLD_GROUP_LINK if member["tier"] == "gold" else CURRENCY_GROUP_LINK
        tier_label = "gold signals" if member["tier"] == "gold" else "currency signals"
        msg = (
            f"You're approved! Welcome to Inner Circle.\n\n"
            f"Your {tier_label} Telegram group:\n{group_link}\n\n"
            f"Your website access code: {member['access_code']}\n\n"
            f"To unlock your Education access, go to:\n"
            f"https://innercircletrading.co/unlock\n\n"
            f"Enter your code there and you're in. Keep this code safe, you'll need it again if you "
            f"switch phone or clear your browser."
        )
        if member.get("chat_id"):
            send_telegram_message(member["chat_id"], msg)
        else:
            notify_admin(f"⚠️ Could not auto-message member {member_id}, no chat_id on file. Send manually: {msg}")
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
            session["member_id"] = member["id"]
            session["member_tier"] = member["tier"]
            session["member_paid"] = member.get("paid", False)
            session["member_name"] = member.get("name", "")
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

# in-memory per-chat photo counter for the current verification attempt
_photo_counts = {}
_payment_pending = {}


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
    (["how do i trade", "how to trade", "how does trading work", "how do you trade",
      "how does it work", "how do i place", "how to place", "teach me"],
     """Good question, and honestly it's simpler than most people expect once someone actually shows you. You pick what you're trading, choose your direction, set your size and your safety levels, then place it.

The bit most people don't realise is you don't have to work out the what and when on your own. Our signals are copy and paste, we send you the pair, the direction and the levels, and you just enter them exactly as they are.

Our free beginners course walks you through the whole thing step by step with screenshots, so you know exactly what you're doing before you place anything. Would you like the steps to get access?""",
     True),

    (["how do i start", "how can i start", "how to start", "how do i join", "how to join",
      "want to join", "sign up", "get started", "getting started", "how do i get in"],
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

    for keywords, reply_text, offers_steps in KEYWORD_REPLIES:
        for kw in keywords:
            if kw in text:
                if chat_id:
                    if offers_steps:
                        _conversations[chat_id] = "offered_steps"
                    else:
                        _conversations.pop(chat_id, None)
                return reply_text, True

    return None, False


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return "ok"

    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username")
    text = (message.get("text") or "").strip().lower()
    photos = message.get("photo")

    if chat_id:
        upsert_bot_contact(username, chat_id)

    if not TELEGRAM_API:
        return "ok"

    def reply(msg):
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": msg}, timeout=10)
        except Exception:
            pass

    if text == "/start":
        reply(
            "Hey, welcome to Inner Circle! 👋\n\n"
            "If you're going through onboarding, send me your deposit confirmation and your closed trades screenshots "
            "and I'll get your verification code sorted.\n\n"
            "Joining Extra Signals instead? Just saying hi here is enough, that links your account so we can send your "
            "group link once you're approved.\n\n"
            "Any questions at all, just ask."
        )
        return "ok"

    if text == "/help":
        reply("Ask me anything about getting set up, deposits, MT5, the courses, or your verification code. If I can't help, I'll pass you to the team.")
        return "ok"

    # Detect someone flagging an Advanced course payment
    caption = (message.get("caption") or "").lower()
    payment_words = ["advanced paid", "paid advanced", "paid for advanced", "payment for advanced",
                     "advanced payment", "paid the advanced", "advanced course paid",
                     "paid for the advanced", "proof of payment", "payment proof", "paid £99", "paid 99"]

    is_payment_text = any(w in text for w in payment_words)
    is_payment_caption = any(w in caption for w in payment_words)

    if photos and (is_payment_caption or _payment_pending.get(chat_id)):
        _payment_pending.pop(chat_id, None)
        _photo_counts[chat_id] = 0
        reply(
            "Thanks, got your payment screenshot. I've sent it straight over to our admin team.\n\n"
            "They'll review it and unlock your Advanced Chart Reading access, usually well within 24 hours. "
            "You'll get a message here the moment it's live."
        )
        if username:
            contact_line = f"From: @{username}\nReply: https://t.me/{username}"
        else:
            contact_line = f"From: chat ID {chat_id} (no @username set on their account)"
        notify_admin(
            f"💷 ADVANCED COURSE PAYMENT SCREENSHOT\n\n{contact_line}\n\n"
            f"Caption: {message.get('caption') or '(none)'}\n\n"
            f"Check the screenshot in your Telegram chat with them, then Mark Paid at /admin."
        )
        return "ok"

    if is_payment_text and not photos:
        _payment_pending[chat_id] = True
        reply(
            "Great, thanks for letting me know. Could you send a screenshot of your payment over here "
            "and I'll get it straight to our admin team to review?\n\n"
            "Once they've confirmed it, your Advanced Chart Reading access gets unlocked and I'll message you here."
        )
        return "ok"

    if photos:
        _photo_counts.setdefault(chat_id, 0)
        _photo_counts[chat_id] += 1
        count = _photo_counts[chat_id]
        if count == 1:
            reply("Got your first screenshot! Send the second one over too, we need your deposit confirmation and your closed trades history.")
        elif count >= 2:
            code = "IC-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
            save_verification_code(code, chat_id, username, count)
            reply(f"Both screenshots received, thank you! Your verification code is: {code}\n\nPop that into the onboarding form on the website and you're all set.")
            _photo_counts[chat_id] = 0
        return "ok"

    if text:
        original_text = message.get("text", "")
        answer, matched = keyword_reply(original_text, chat_id=chat_id)

        if matched:
            reply(answer)
        else:
            reply("Good question, let me get someone from the team to answer that one properly for you. They'll be in touch shortly.")
            if username:
                contact_line = f"From: @{username}\nReply: https://t.me/{username}"
            else:
                contact_line = (
                    f"From: chat ID {chat_id} (no @username set on their account)\n"
                    f"They'll need to set a Telegram username, or you can reply via the bot."
                )
            notify_admin(
                f"Unanswered bot message\n\n{contact_line}\n\nTheir message:\n{original_text}"
            )

    return "ok"


@app.route("/account")
def account():
    if not is_verified():
        return redirect(url_for("unlock"))

    name = session.get("member_name", "")
    tier = session.get("member_tier", "gold")
    paid = session.get("member_paid", False)

    tier_label = "Gold signals" if tier == "gold" else "Currency signals"

    advanced_row = (
        '<li style="padding: 14px 0; border-bottom: 1px solid var(--line);">'
        '<strong style="color: var(--green);">✓ Advanced Chart Reading</strong>'
        '<br><span style="color: var(--ink-dim); font-size: 13px;">Unlocked, 23 lessons</span>'
        '<br><a href="/education/advanced/0" class="inline-link" style="font-size: 13px;">Open course →</a></li>'
        if paid else
        '<li style="padding: 14px 0; border-bottom: 1px solid var(--line);">'
        '<span style="color: var(--ink-dim);">🔒 Advanced Chart Reading</span>'
        '<br><span style="color: var(--ink-dim); font-size: 13px;">£99 one-time, not yet unlocked</span>'
        '<br><a href="/education/advanced/0" class="inline-link" style="font-size: 13px;">Unlock it →</a></li>'
    )

    content = f"""
<section style="padding: 70px 0;">
  <div class="wrap" style="max-width: 560px;">
    <span class="eyebrow">Your account</span>
    <h1 style="font-size: 30px; margin: 10px 0 8px;">Hi{', ' + name if name else ''}</h1>
    <p style="color: var(--ink-dim); margin-bottom: 36px;">Here's what you've got access to.</p>

    <div class="form-panel">
      <ul style="list-style: none; padding: 0; margin: 0;">
        <li style="padding: 14px 0; border-bottom: 1px solid var(--line);">
          <strong style="color: var(--green);">✓ {tier_label}</strong>
          <br><span style="color: var(--ink-dim); font-size: 13px;">Approved and active</span>
        </li>
        <li style="padding: 14px 0; border-bottom: 1px solid var(--line);">
          <strong style="color: var(--green);">✓ Trading Fundamentals</strong>
          <br><span style="color: var(--ink-dim); font-size: 13px;">Free course, 41 lessons</span>
          <br><a href="/education/fundamentals/0" class="inline-link" style="font-size: 13px;">Open course →</a>
        </li>
        {advanced_row}
        <li style="padding: 14px 0;">
          <strong>Extra Signals</strong>
          <br><span style="color: var(--ink-dim); font-size: 13px;">Add a second account for currency signals</span>
          <br><a href="/signals" class="inline-link" style="font-size: 13px;">View Extra Signals →</a>
        </li>
      </ul>
    </div>

    <p style="color: var(--ink-dim); font-size: 13px; margin-top: 28px;">
      Need help? Message us on <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">Telegram</a>.
      <br><a href="/logout" class="inline-link">Log out</a>
    </p>
  </div>
</section>
"""
    return render_template_string(base_layout("My Account", content, ""))


@app.route("/logout")
def logout():
    session.pop("member_id", None)
    session.pop("member_tier", None)
    session.pop("member_paid", None)
    session.pop("member_name", None)
    return redirect(url_for("home"))


def is_verified():
    return bool(session.get("member_id"))


def is_paid():
    return bool(session.get("member_paid"))


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
    content = """
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
          Stuck on a step? Message us directly on Telegram or use the chat on this site. You'll always get a real
          answer from a real person, never left to figure it out alone.
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
          expected to trade alone, and keeps a real person on the other end whenever you need one.
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
        <h3>Live results channel</h3>
        <p>Every signal outcome posted openly, wins and losses both, on Telegram.</p>
        <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link" style="display: inline-block; margin-top: 10px;">View Results on Telegram →</a>
      </div>
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
    content = """
<section class="hero" style="padding-bottom: 40px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <div class="ring-divider">+</div>
      <span class="eyebrow">Extra Signals</span>
      <h1>Unlock currency<br>signals <em>too.</em></h1>
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
          <a href="#" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Charlotte</a>
          <a href="#" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Beth</a>
          <a href="#" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Robbie</a>
          <a href="#" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Lucy</a>
          <a href="#" class="btn btn-ghost" style="padding: 12px 24px;" target="_blank" rel="noopener">Lydia</a>
        </div>
        <div class="callout">⚠️ Once you've registered, come straight back to this page, the next steps are right here below.</div>
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
      <p style="color: var(--ink-dim); font-size: 14px; margin: 0;">Once you've completed all 3 steps, let us know so we can verify and approve your access to the currency signals group.</p>
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
        <label>Your Telegram username</label>
        <input type="text" name="telegram_username" placeholder="@yourusername" required>
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
    telegram_username = request.form.get("telegram_username", "")
    referred_by = request.form.get("referred_by", "")

    member_id = create_pending_member(
        tier="currency", title=title, name=name, account_number=account_number,
        deposit_amount=deposit_amount, phone=phone,
        telegram_username=telegram_username, referred_by=referred_by
    )

    notify_admin(
        "New CURRENCY (PU Prime) submission:\n\n"
        f"Title: {title}\n"
        f"Name: {name}\n"
        f"Account #: {account_number}\n"
        f"Deposit: {deposit_amount}\n"
        f"Phone: {phone}\n"
        f"Telegram: {telegram_username}\n"
        f"Referred by: {referred_by}\n"
        f"Member ID: {member_id}\n\n"
        "Review and approve at /admin"
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
    code = request.form.get("code", "")

    member_id = create_pending_member(
        tier="gold", title=title, name=name, account_number=account_number,
        deposit_amount=deposit_amount, phone=phone, verification_code=code
    )

    notify_admin(
        "New GOLD onboarding submission:\n\n"
        f"Title: {title}\n"
        f"Name: {name}\n"
        f"Account #: {account_number}\n"
        f"Deposit: {deposit_amount}\n"
        f"Phone: {phone}\n"
        f"Code: {code}\n"
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
        else f'<a href="{unlock_url}" class="btn btn-primary">{unlock_label}</a>'
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
    lessons = parse_course(FUNDAMENTALS_MD)
    return contents_page("fundamentals", "Trading Fundamentals", lessons, "/education/fundamentals")


@app.route("/education/fundamentals")
def education_fundamentals():
    lessons = parse_course(FUNDAMENTALS_MD)
    content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">Free · 5 sections · {len(lessons) - 1} lessons</span>
      <h1>Trading<br>Fundamentals</h1>
      <p class="lede">Everything before and around your first trade, one lesson at a time.</p>
      <div class="cta-row">
        <a href="/education/fundamentals/0" class="btn btn-primary">Start Course</a>
        <a href="/education/fundamentals/contents" class="btn btn-ghost">Browse All Lessons</a>
      </div>
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
    return render_template_string(base_layout("Trading Fundamentals", content, "education"))


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
        "/education/fundamentals", "Start Onboarding to Unlock", "/onboarding"
    )


@app.route("/education/advanced/contents")
def education_advanced_contents():
    lessons = parse_course(ADVANCED_MD)
    return contents_page("advanced", "Advanced Chart Reading", lessons, "/education/advanced")


@app.route("/education/advanced")
def education_advanced():
    lessons = parse_course(ADVANCED_MD)
    content = f"""
<section class="hero" style="padding-bottom: 30px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow">£99 · One-time · 3 sections · {len(lessons) - 1} lessons</span>
      <h1>Advanced Chart<br>Reading</h1>
      <p class="lede">Learn to read a chart yourself, not just follow along, one lesson at a time.</p>
      <div class="cta-row">
        <a href="/education/advanced/0" class="btn btn-primary">Start Course</a>
        <a href="/education/advanced/contents" class="btn btn-ghost">Browse All Lessons</a>
      </div>
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
    return render_template_string(base_layout("Advanced Chart Reading", content, "education"))


@app.route("/education/advanced/<int:idx>")
def education_advanced_lesson(idx):
    if not is_paid():
        content = """
<section style="padding: 80px 0;">
  <div class="wrap" style="max-width: 560px; text-align: center;">
    <div class="ring-mark" style="margin: 0 auto 24px;"><span>🔒</span></div>
    <span class="eyebrow">£99 · One-time payment</span>
    <h1 style="font-size: 30px; margin: 12px 0 18px;">Advanced Chart Reading</h1>
    <p style="color: var(--ink-dim); font-size: 16px; margin-bottom: 32px;">
      23 lessons teaching you to read charts yourself, candlestick patterns, market structure,
      support and resistance, liquidity, and building your own strategy. One payment, yours for good.
    </p>

    <a href="https://www.paypal.com/ncp/payment/JMNWH9XAF6PXL" target="_blank" rel="noopener" class="btn btn-primary" style="font-size: 16px; padding: 18px 40px;">Pay £99 &amp; Unlock</a>

    <div class="form-panel" style="margin-top: 40px; text-align: left;">
      <h3 style="font-size: 16px; margin-bottom: 12px;">What happens next</h3>
      <ol style="color: var(--ink-dim); font-size: 14px; line-height: 1.9; padding-left: 20px; margin: 0;">
        <li>Complete your payment through the link above.</li>
        <li><strong>Important:</strong> use the same name as your Inner Circle account, or add your Telegram username in the notes.</li>
        <li>Please give our admin team up to 24 hours to review your payment.</li>
        <li>You'll get a message on Telegram once it's live, then just refresh this page.</li>
      </ol>
      <p style="color: var(--gold); font-size: 14px; line-height: 1.7; margin: 16px 0 0; padding-top: 16px; border-top: 1px solid var(--line);">
        Want it sorted quicker? Message
        <a href="https://t.me/Innercircleverifybot" target="_blank" rel="noopener" class="inline-link">our bot on Telegram</a>
        saying <strong>"advanced paid"</strong> and send a screenshot of your payment. It goes straight to our admin team for review.
      </p>
    </div>
  </div>
</section>
"""
        return render_template_string(base_layout("Unlock Advanced", content, "education"))
    lessons = parse_course(ADVANCED_MD)
    return lesson_page(
        "advanced", "Advanced Chart Reading", lessons, idx,
        CHART_DIAGRAMS, "DIAGRAM",
        "/education/advanced", "Unlock After Purchase", "/onboarding"
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


@app.route("/community")
def community():
    content = """
<section class="hero" style="padding-bottom: 40px;">
  <div class="wrap" style="grid-template-columns: 1fr;">
    <div>
      <span class="eyebrow" style="color: var(--rose);">Wealth Circle</span>
      <h1>A trading space<br>built for <em style="color: var(--rose);">women.</em></h1>
      <p class="lede">Trading communities default to loud and male-dominated. This one doesn't. It's private, supportive, and genuinely welcoming, whatever stage you're at.</p>
      <div class="cta-row">
        <a href="/onboarding" class="btn btn-primary">Start Onboarding</a>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="grid5" style="grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));">
      <div class="benefit"><div class="icon" style="border-color: var(--rose); color: var(--rose);">○</div><h3>Ask anything</h3><p>No question is too basic. Ever.</p></div>
      <div class="benefit"><div class="icon" style="border-color: var(--rose); color: var(--rose);">♥</div><h3>Share wins</h3><p>Celebrate progress, big or small.</p></div>
      <div class="benefit"><div class="icon" style="border-color: var(--rose); color: var(--rose);">◈</div><h3>Weekly threads</h3><p>Regular check-ins and discussion.</p></div>
      <div class="benefit"><div class="icon" style="border-color: var(--rose); color: var(--rose);">★</div><h3>Direct access</h3><p>To Charlotte and the team.</p></div>
    </div>
  </div>
</section>

<section>
  <div class="wrap" style="text-align: center; max-width: 600px;">
    <h2 style="font-size: 30px; margin-bottom: 20px;">Community access opens on request</h2>
    <p style="color: var(--ink-dim); margin-bottom: 32px;">Complete onboarding first, then request to join, we'll add you to the private Wealth Circle group on Telegram.</p>
    <div style="display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;">
      <a href="/onboarding" class="btn btn-ghost">Start Onboarding</a>
      <a href="https://t.me/Innercircleverifybot" class="btn btn-primary" target="_blank" rel="noopener">Request to Join on Telegram</a>
    </div>
  </div>
</section>
"""
    return render_template_string(base_layout("Community", content, "community"))


if __name__ == "__main__":
    app.run(debug=True)
