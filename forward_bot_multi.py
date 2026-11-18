"""
Multi-user Auto Forward Bot (button UI) - advanced version

Features
- PUBLIC bot: anyone can use it after joining the force-join channel. 7-day free
  trial, then plans (Basic / Pro / Unlimited) paid by UPI screenshot + owner approval.
  Each user logs in with their own account inside the bot and manages their own tasks.
- Per task: filters, header/footer, replacements, link rules (affiliate tags,
  replace links), unshorten links, translate, image watermark, schedule
  (time window or fixed times), duplicate filter, edit/delete/reply sync.
- Forward old posts, stats and logs, backups/restore, admin broadcast.
- Extra targets, auto-delete, deal filter (discount/price), content-type filter,
  regex filters, silent/pin, long-text splitting, error alerts, clone task.
- No built-in limits (tasks, old posts, queue, file size): only Telegram's own apply.

Setup:  pip install -U telethon        (optional: pkg install python-pillow  for watermark)
Run:    python forward_bot_multi.py
WARNING: data.json stores login sessions of your users' accounts.
         Keep it private. Anyone with that file can access those accounts.
"""
import asyncio
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict, defaultdict, deque
from datetime import datetime, timedelta, timezone

from telethon import Button, TelegramClient, events, utils
from telethon.errors import (FloodWaitError, MessageNotModifiedError,
                             PasswordHashInvalidError, PhoneCodeExpiredError,
                             PhoneCodeInvalidError, SessionPasswordNeededError,
                             UserNotParticipantError)
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ResendCodeRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import (Channel, ChannelParticipantBanned, ChannelParticipantLeft, Chat,
                               ChatInviteAlready, MessageMediaWebPage, PeerChannel, User)

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except Exception:          # Pillow not installed: watermark is simply unavailable
    PIL_OK = False

# ===================== CONFIG =====================
# Limits: None = unlimited (Telegram's own limits still apply)
# ---- public bot / plans ----
CHANNEL = "@Realxportal"         # users must join this channel (the bot must be its admin)
UPI_ID = "anonymousopen@upi"     # shown on the payment screen
TRIAL_DAYS = 7
PLAN_DAYS = 30                   # days added by each approved payment
REMIND_DAYS = 2                  # expiry reminder this many days before the end
# True : after the trial (or an expired plan) users stay on Basic limits for free.
# False: Basic itself must be bought (Rs 99); until then their tasks are paused.
FREE_BASIC_AFTER_TRIAL = True
PLANS = {
    # "xsrc" = extra SOURCES per task (many source channels -> one target)
    "trial":     {"name": "Free Trial", "price": 0,   "tasks": 2,    "extra": 0,    "xsrc": 0,    "premium": False},
    "basic":     {"name": "Basic",      "price": 99,  "tasks": 3,    "extra": 1,    "xsrc": 1,    "premium": False},
    "pro":       {"name": "Pro",        "price": 249, "tasks": 10,   "extra": 3,    "xsrc": 5,    "premium": True},
    "unlimited": {"name": "Unlimited",  "price": 499, "tasks": None, "extra": None, "xsrc": None, "premium": True},
}
REF_BONUS_DAYS = 3               # days added to the referrer's plan per friend who joins
REF_MAX = 10                     # max rewarded friends per user
# ---- abuse protection (the owner is never limited) ----
RATE_LIMIT = 25                  # max taps/messages per user ...
RATE_WINDOW = 10                 # ... within this many seconds
MAX_LOGINS_AT_ONCE = 5           # people logging in at the same moment
CODE_REQUESTS_PER_HOUR = 6       # login-code requests per user (protects your API_ID from Telegram limits)
# Per-plan caps. None = unlimited. Lower plans are capped so one user can't overload the bot.
CAPS = {
    "trial":     {"old": 200,  "queue": 200,  "mb": 50,   "ai": 0},
    "basic":     {"old": 200,  "queue": 200,  "mb": 50,   "ai": 0},
    "pro":       {"old": 5000, "queue": 2000, "mb": 500,  "ai": 150},   # AI rewrites per day
    "unlimited": {"old": None, "queue": None, "mb": None, "ai": None},
}
PREMIUM_FIELDS = ("translate", "watermark", "linkrules", "aiprompt", "tglink", "linkmap")   # Translate, Watermark, Link swap, AI Rewrite
MAX_OLD_POSTS = None             # max old posts per "Old Posts" run
MAX_QUEUE = None                 # max held messages per task (schedule)
MAX_REUPLOAD_MB = None           # largest file re-uploaded from a protected channel
REPORT_HOUR = 22                 # daily report is sent after this hour (local time, see UTC_OFFSET_MINUTES)
BUILD = "2026-11-15 · Forward edited posts"      # shown in 👑 Admin so you can see which file is running
POST_GAP = 1.5                   # seconds between old/queued posts (0 = none; very low risks FloodWait)
CAPTION_LIMIT = 1024             # Telegram caption limit; longer text goes in a separate message
TEXT_LIMIT = 4000                # Telegram text limit is 4096; longer text is split
ALERT_COOLDOWN = 600             # seconds between error alerts per task
TMP_DIR = "tmp_media"            # temporary folder used while re-uploading
UTC_OFFSET_MINUTES = 330         # schedule timezone: 330 = India (UTC+5:30)
DATA_FILE = "data.json"
CONFIG_FILE = "config.json"      # created on first run; keeps your keys safe
# ==================================================


def load_config():
    """Read API_ID, API_HASH, BOT_TOKEN, OWNER_ID from config.json.
    Anything missing is asked once in the terminal and then saved, so you
    never have to re-enter it when you replace this script with a new version."""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    questions = [
        ("API_ID", "API_ID (from my.telegram.org, digits only): ", True),
        ("API_HASH", "API_HASH (from my.telegram.org): ", False),
        ("BOT_TOKEN", "BOT_TOKEN (from @BotFather): ", False),
        ("OWNER_ID", "OWNER_ID (your numeric Telegram ID from @userinfobot): ", True),
    ]
    changed = False
    for key, question, numeric in questions:
        while True:
            val = str(cfg.get(key, "")).strip()
            if val and (not numeric or val.lstrip("-").isdigit()):
                cfg[key] = int(val) if numeric else val
                break
            cfg[key] = input(question).strip()
            changed = True
    if changed:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
        print(f"Saved to {CONFIG_FILE}. You won't be asked again.")
    return cfg


_cfg = load_config()
API_ID = _cfg["API_ID"]
API_HASH = _cfg["API_HASH"]
BOT_TOKEN = _cfg["BOT_TOKEN"]
OWNER_ID = _cfg["OWNER_ID"]

bot = TelegramClient("bot_session", API_ID, API_HASH)
clients = {}                                  # uid -> logged-in user client
state = {}                                    # pending input per uid
msgmap = OrderedDict()                        # (task_id, source_chat_id, src_msg_id) -> target_msg_id
recent = defaultdict(lambda: deque(maxlen=200))        # duplicate filter
logs = defaultdict(lambda: deque(maxlen=60))           # uid -> recent activity
oldjobs = {}                                  # task_id -> {"stop": bool}
pending_bc = {}                               # admin broadcast text awaiting confirm
last_flush = {}                               # task_id -> "YYYYmmddHHMM"
flags = {"dirty": False}
alerts = {}                                   # task_id -> last alert time

TOGGLES = [
    ("copy", "Hide 'Forwarded from'"), ("media", "Media"),
    ("no_links", "No Links"), ("unshorten", "Unshorten Links"),
    ("no_usernames", "No Usernames"), ("dedupe", "Duplicate Filter"),
    ("sync_edit", "Sync Edits"), ("sync_delete", "Sync Deletes"),
    ("sync_reply", "Sync Replies"), ("reupload", "Re-upload Restricted"),
    ("silent", "Silent (no sound)"), ("pin", "Pin Posts"),
    ("ai_rewrite", "🤖 AI Rewrite"),
    ("tg_mentions", "Replace @usernames too"),
    ("forward_edited", "Forward Edited Posts"),
]

TYPES = ("text", "photo", "video", "gif", "file", "audio", "voice", "sticker", "poll")

SHORTENERS = {"amzn.to", "amzn.in", "a.co", "fkrt.cc", "fkrt.it", "fkrt.to", "bit.ly",
              "tinyurl.com", "cutt.ly", "t.co", "goo.gl", "ow.ly", "is.gd", "myntr.it",
              "ajiio.in", "tinyurl.in", "shorturl.at", "rb.gy"}


def tz_label():
    m = UTC_OFFSET_MINUTES
    sign = "+" if m >= 0 else "-"
    m = abs(m)
    return f"UTC{sign}{m // 60}:{m % 60:02d}"


PROMPTS = {
    "name": "Send the new task name.",
    "source": "Send the SOURCE channel: its -100... ID, @username, a private invite link (t.me/+...), or forward any post from it here.\n\nPrivate channel: your account must be a member (an invite link joins it for you).",
    "target": "Send the TARGET channel: its -100... ID, @username, a private invite link (t.me/+...), or forward any post from it here.",
    "delay": "How many seconds should it wait before posting? (0 = instant)",
    "header": "Send the HEADER text added above every message. Send 'clear' to remove it.",
    "footer": "Send the FOOTER text added below every message. Send 'clear' to remove it.",
    "replace": "Send replacements, one per line:\n<code>old text =&gt; new text</code>\nSend 'clear' to remove all.",
    "route": (
        "Send routing rules, one per line:\n"
        "<code>keyword =&gt; target_id</code>\n\n"
        "A post whose text contains the keyword goes to that target instead of the main one.\n"
        "First matching rule wins. Example:\n"
        "<code>anime =&gt; -1001111111111</code>\n"
        "<code>web series =&gt; -1002222222222</code>\n\n"
        "The bot must be able to post there. Send 'clear' to remove all."
    ),
    "template": (
        "Send the INFO TEMPLATE added above every post. These fill in by themselves:\n"
        "<code>{title} {year} {quality} {size} {language} {genre} {episode}</code>\n\n"
        "Example:\n"
        "<code>🎬 {title} ({year})\n⭐ Quality: {quality}\n💾 Size: {size}</code>\n\n"
        "Missing values stay as {title} etc. Send 'clear' to remove it."
    ),
    "blacklist": "Send words/phrases (comma or new line separated). Messages containing any of them are SKIPPED. This replaces the current list. 'clear' = empty it.\nRegex: put an entry on its own line starting with <code>re:</code>, e.g. <code>re:\\d+% off</code>",
    "whitelist": "Send words/phrases (comma or new line separated). ONLY messages containing one of them are forwarded. This replaces the current list. 'clear' = empty it.\nRegex: put an entry on its own line starting with <code>re:</code>",
    "linkrules": (
        "Send link rules, one per line:\n"
        "<code>amazon.in ? tag=mytag-21</code>  sets/overrides a URL parameter (affiliate tag)\n"
        "<code>flipkart.com ? affid=myid</code>\n"
        "<code>example.com =&gt; https://my.link</code>  replaces the whole link\n"
        "<code>* =&gt; https://t.me/mychannel</code>  replaces every link\n\n"
        "Short links (amzn.to, bit.ly, ...) can only be tagged if <b>Unshorten Links</b> is ON.\n"
        "Send 'clear' to remove all rules."),
    "translate": ("Send a language code to translate every message into it, e.g. "
                  "<code>en</code>, <code>hi</code>, <code>bn</code>, <code>ta</code>, <code>te</code>, "
                  "<code>mr</code>, <code>gu</code>, <code>ar</code>, <code>es</code>.\n"
                  "Send 'off' to disable."),
    "watermark": "Send the watermark text for photos, e.g. <code>@mychannel</code>. Send 'clear' to remove it.",
    "sched": ("Send one of:\n"
              "<code>off</code>  post instantly\n"
              "<code>window 09:00-22:00</code>  post only inside this window; messages outside wait\n"
              "<code>fixed 09:00,13:00,18:00</code>  hold everything and post it at these times\n\n"
              f"Times use {tz_label()}."),
    "old": "How many of the latest source posts should I forward now? Send a number like <code>50</code>, or <code>all</code> for the whole channel.",
    "extratarget": ("Send an extra TARGET channel to also post to (-100... ID, @username, invite link, or forward a post). "
                    "Repeat to add more. Send 'clear' to remove all extra targets.\n"
                    "Edit/delete sync only follows the main target."),
    "delafter": ("Send how long posts stay in the target before they are deleted, e.g. "
                 "<code>30m</code>, <code>6h</code>, <code>2d</code>. Send 'off' to keep them forever.\n"
                 "Your account needs delete rights in the target."),
    "dealfilter": ("Skip weak deals. Send for example:\n"
                   "<code>discount 50</code>  only posts with at least 50% off\n"
                   "<code>price 999</code>  only posts priced \u20b9999 or less\n"
                   "<code>discount 50 price 999</code>  both\n"
                   "<code>off</code>  disable\n\n"
                   "Prices and discounts are read from the text (\u20b9, Rs, %), so this is approximate. "
                   "Posts where none is found are NOT skipped."),
    "types": ("Send the content types to forward, comma separated: "
              "<code>text, photo, video, gif, file, audio, voice, sticker, poll</code>.\n"
              "Send 'all' to forward everything."),
    "tglink": ("Send <b>YOUR</b> channel link, e.g. <code>https://t.me/mychannel</code> (or <code>@mychannel</code>).\n"
               "<b>Every</b> Telegram link in the posts (channels, groups, posts, invites and bots) is replaced with "
               "this one link. Other links stay as they are. To give each link a different replacement use "
               "🗺 Link Map. Send 'clear' to turn it off."),
    "linkmap": ("🗺 <b>Link Map</b>: say which link becomes what. One rule per line:\n"
                "<code>tg1 =&gt; https://t.me/mychannel</code>  (1st Telegram link)\n"
                "<code>tg2 =&gt; https://t.me/+MyInvite</code>  (2nd Telegram link)\n"
                "<code>other1 =&gt; https://my.site/x</code>  (1st non-Telegram link)\n"
                "<code>link3 =&gt; remove</code>  (3rd link of any kind, deleted)\n"
                "<code>tg =&gt; https://t.me/mychannel</code>  (all Telegram links)\n"
                "<code>pdiskvideo.in =&gt; https://other.in/v</code>  (any link from that site)\n\n"
                "Telegram links = channels, groups, posts, invites and bots. The first rule that fits a link wins. "
                "Send 'clear' to remove all rules."),
    "aiprompt": ("Send the instruction for 🤖 AI Rewrite, e.g. <code>Rewrite in simple Hindi, keep it short</code>.\n"
                 "Send 'clear' for the default (rewrite in your own words, same meaning). "
                 "Links, @usernames, numbers and emojis are always kept."),
    "extrasource": ("Send an extra SOURCE channel (its posts also go to this task's target). Repeat to add more. "
                    "You can also forward a post from it. Send 'clear' to remove all extra sources."),
    "broadcast": "Send the message to broadcast to all users. /cancel to abort.",
}


# ---------------- storage ----------------
def default_sched():
    return {"mode": "off", "start": "09:00", "end": "22:00", "times": []}


def default_stats():
    return {"forwarded": 0, "skipped": 0, "errors": 0, "last": ""}


def default_settings():
    return {
        "copy": True, "media": True, "no_links": False, "unshorten": False,
        "no_usernames": False, "dedupe": True, "reupload": True,
        "sync_edit": True, "sync_delete": True, "sync_reply": True,
        "delay": 0, "header": "", "footer": "",
        "replace": {}, "blacklist": [], "whitelist": [],
        "route": [],          # keyword => target_id: a post matching the keyword is sent to that target instead
        "template": "",       # text added above every post; {title} {year} {quality} {size} are filled in
        "link_rules": [], "translate": "", "watermark": "",
        "sched": default_sched(),
        "silent": False, "pin": False, "delete_after": 0,
        "min_discount": 0, "max_price": 0, "types": [],
        "ai_rewrite": False, "ai_prompt": "",
        "tg_link": "", "tg_mentions": False, "link_map": [],
        "direction": "all",          # all | incoming (only messages from others/a bot) | outgoing (only my own)
        "forward_edited": False,     # forward a post on its first edit even if it was never sent before
    }


def normalize(d):
    d.setdefault("next_id", 1)
    d.setdefault("tasks", {})
    d.setdefault("allowed", [])
    d.setdefault("sessions", {})
    d.setdefault("expiry", [])
    d.setdefault("users", {})        # uid -> {plan, expires, trial_used, joined, warned}
    d.setdefault("payments", {})
    d.setdefault("next_pay", 1)
    d.setdefault("banned", [])
    d.setdefault("coupons", {})
    d.setdefault("noreport", [])     # users who switched the daily report off
    d.setdefault("report_day", "")
    d.setdefault("report_base", {})  # task id -> [forwarded, skipped, errors] at the last report      # CODE -> {type, plan, days, pct, max, used[], active}
    for u in d["allowed"]:           # old approved friends keep working with no limits
        d["users"].setdefault(str(u), {"plan": "unlimited", "expires": 0, "trial_used": True,
                                       "joined": time.time(), "warned": False})
    for t in d["tasks"].values():
        t["settings"] = {**default_settings(), **t.get("settings", {})}
        t["settings"]["sched"] = {**default_sched(), **t["settings"]["sched"]}
        t["stats"] = {**default_stats(), **t.get("stats", {})}
        t.setdefault("owner", OWNER_ID)
        t.setdefault("queue", [])
        t.setdefault("source", None)
        t.setdefault("target", None)
        t.setdefault("source_title", "")
        t.setdefault("target_title", "")
        t.setdefault("source_protected", False)
        t.setdefault("extra_targets", [])
        t.setdefault("extra_sources", [])
        t.setdefault("running", False)
    ids = [int(k) for k in d["tasks"] if str(k).isdigit()]
    d["next_id"] = max(int(d["next_id"]), max(ids, default=0) + 1)   # never reuse a task id
    return d


# ---------------- session encryption ----------------
# Login sessions are stored encrypted in data.json. The key lives in a SEPARATE file
# (secret.key), so a leaked data.json / backup alone is useless. Keep secret.key private
# and keep a copy: without it, saved logins cannot be decrypted (users must log in again).
KEY_FILE = "secret.key"
try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_OK = True
except Exception:
    CRYPTO_OK = False


try:
    import io
    import qrcode
    QR_OK = True
except Exception:
    QR_OK = False


def _fernet():
    if not CRYPTO_OK:
        return None
    if not os.path.exists(KEY_FILE):
        with open(KEY_FILE, "wb") as f:
            f.write(Fernet.generate_key())
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    with open(KEY_FILE, "rb") as f:
        return Fernet(f.read().strip())


def encrypt_sessions(sessions):
    f = _fernet()
    if not f:
        return dict(sessions)
    return {k: (v if str(v).startswith("enc:") else "enc:" + f.encrypt(v.encode()).decode())
            for k, v in sessions.items()}


def decrypt_sessions(sessions):
    out = {}
    f = None
    for k, v in sessions.items():
        if not str(v).startswith("enc:"):
            out[k] = v                      # old unencrypted entry: encrypted on the next save
            continue
        if f is None:
            f = _fernet()
        if f is None:
            raise SystemExit("data.json has encrypted logins but the 'cryptography' package is missing.\n"
                             "Run:  pkg install python-cryptography   then start the bot again.")
        try:
            out[k] = f.decrypt(v[4:].encode()).decode()
        except Exception:
            print(f"Could not decrypt the saved login of user {k} (wrong or missing secret.key). "
                  "That user must log in again.")
    return out


# Optional cloud copy: put "MONGO_URI" in config.json to also keep your data in MongoDB Atlas.
# Without it the bot works exactly as before (data.json only). Needs: pip install "pymongo[srv]"
MONGO_URI = str(_cfg.get("MONGO_URI") or "").strip()
GEMINI_KEY = str(_cfg.get("GEMINI_KEY") or "").strip()
AI_MODEL = str(_cfg.get("AI_MODEL") or "gemini-2.5-flash").strip()
try:
    import pymongo
    PYMONGO_OK = True
except Exception:
    PYMONGO_OK = False
_cloud = {"col": None, "on": False, "state": "off", "latest": None, "busy": False,
          "lock": threading.Lock(), "fails": 0, "last": 0, "warned_big": False}


def cloud_connect():
    """('off'|'ok'|'fail', saved_json_or_None)."""
    if not MONGO_URI:
        return "off", None
    if not PYMONGO_OK:
        print('MONGO_URI is set but pymongo is missing. Run: pip install "pymongo[srv]"  (cloud copy is OFF)')
        return "fail", None
    if MONGO_URI.startswith("mongodb+srv"):
        try:                                   # Termux has no /etc/resolv.conf: give dnspython public DNS servers
            import dns.resolver
            r = dns.resolver.Resolver(configure=False)
            r.nameservers = ["8.8.8.8", "1.1.1.1"]
            dns.resolver.default_resolver = r
        except Exception as e:
            print(f"DNS helper not available ({e!r}); install it with: pip install dnspython")
    try:
        cli = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000, socketTimeoutMS=15000)
        col = cli["forward_bot"]["state"]
        doc = col.find_one({"_id": "main"})
        _cloud["col"] = col
        return "ok", (doc or {}).get("json")
    except Exception as e:
        print(f"Cloud copy unavailable: {e!r}")
        return "fail", None


def load():
    local = None
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            local = json.load(f)
    chosen = local or {}
    _cloud["on"] = False
    st, payload = cloud_connect()
    _cloud["state"] = st
    if st == "ok":
        _cloud["on"] = True                  # only now is it safe to write to the cloud
        cloud = None
        if payload:
            try:
                cloud = json.loads(payload)
            except ValueError:
                cloud = None
        local_ts = (local or {}).get("_saved_at") or (os.path.getmtime(DATA_FILE) if local else 0)
        if cloud and cloud.get("_saved_at", 0) > local_ts:
            chosen = cloud
            print("Loaded your data from the cloud copy (it was newer than data.json).")
    chosen["sessions"] = decrypt_sessions(chosen.get("sessions", {}))
    return normalize(chosen)


db = load()


def _cloud_worker():
    while True:
        with _cloud["lock"]:
            payload, _cloud["latest"] = _cloud["latest"], None
            if payload is None:
                _cloud["busy"] = False
                return
        try:
            _cloud["col"].replace_one({"_id": "main"}, {"_id": "main", "json": payload, "t": time.time()},
                                      upsert=True)
            _cloud["fails"] = 0
            _cloud["last"] = time.time()
        except Exception as e:
            _cloud["fails"] += 1
            print(f"Cloud copy failed: {e!r}")
            if _cloud["fails"] < 5:
                with _cloud["lock"]:
                    if _cloud["latest"] is None:
                        _cloud["latest"] = payload       # retry; a newer save() replaces it
                time.sleep(5)


def cloud_push(payload):
    if not _cloud["on"]:
        return
    if len(payload) > 15_000_000:
        if not _cloud["warned_big"]:
            _cloud["warned_big"] = True
            print("Cloud copy skipped: data is bigger than MongoDB's 16 MB document limit.")
        return
    with _cloud["lock"]:
        _cloud["latest"] = payload
        if _cloud["busy"]:
            return
        _cloud["busy"] = True
    threading.Thread(target=_cloud_worker).start()


def cloud_status():
    if _cloud["state"] == "off":
        return "off"
    if not _cloud["on"]:
        return "⚠️ not connected"
    if _cloud["fails"] >= 3:
        return "⚠️ failing"
    when = datetime.fromtimestamp(_cloud["last"], timezone(timedelta(minutes=UTC_OFFSET_MINUTES))).strftime("%H:%M") \
        if _cloud["last"] else "-"
    return f"ON (last sync {when})"


def save():
    payload = json.dumps({**db, "sessions": encrypt_sessions(db["sessions"]), "_saved_at": time.time()},
                         ensure_ascii=False)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, DATA_FILE)               # never leaves a half-written data.json
    flags["dirty"] = False
    cloud_push(payload)


def mark_dirty():
    flags["dirty"] = True


def next_tid():
    """New unique task id (never reuses an existing one)."""
    ids = [int(k) for k in db["tasks"] if str(k).isdigit()]
    n = max(int(db["next_id"]), max(ids, default=0) + 1)
    db["next_id"] = n + 1
    return str(n)


def allowed(uid):
    """Public bot: everyone may use it unless the owner blocked them."""
    return uid == OWNER_ID or uid not in db["banned"]


def esc(x):
    return html.escape(str(x))


def now():
    return datetime.now(timezone(timedelta(minutes=UTC_OFFSET_MINUTES)))


def log(uid, tid, text):
    name = db["tasks"].get(tid, {}).get("name", "?")
    logs[uid].append(f"{now():%H:%M:%S} [{name}] {text}")


def bump(t, key):
    t["stats"][key] += 1
    t["stats"]["last"] = now().strftime("%d %b %H:%M")
    mark_dirty()


# ---------------- plans / public access ----------------
joined_cache = {}                       # uid -> time until which "is a member" is trusted
flags["member_err"] = False


def user_rec(uid):
    return db["users"].get(str(uid))


def is_locked(uid):
    """Only when FREE_BASIC_AFTER_TRIAL is False: Basic without payment = paused."""
    if FREE_BASIC_AFTER_TRIAL or uid == OWNER_ID:
        return False
    u = user_rec(uid)
    return bool(u and u["plan"] == "basic" and not u.get("expires"))


def plan_key(uid):
    if uid == OWNER_ID:
        return "unlimited"
    u = user_rec(uid)
    return u["plan"] if u and u["plan"] in PLANS else "basic"


def plan_info(uid):
    if is_locked(uid):
        return {**PLANS["basic"], "name": "Basic (payment needed)", "tasks": 0, "extra": 0, "premium": False}
    return PLANS[plan_key(uid)]


def is_premium(uid):
    return bool(plan_info(uid)["premium"])


def over_limit_ids(uid):
    """Task ids beyond the plan's task limit (the oldest tasks stay usable)."""
    lim = plan_info(uid)["tasks"]
    if lim is None:
        return set()
    ids = sorted(int(k) for k, t in db["tasks"].items() if t["owner"] == uid and str(k).isdigit())
    return {str(i) for i in ids[lim:]}


def enforce_limits(uid):
    over = over_limit_ids(uid)
    for tid in over:
        db["tasks"][tid]["running"] = False


def task_cap_msg(uid):
    lim = plan_info(uid)["tasks"]
    if lim is None:
        return None
    count = sum(1 for t in db["tasks"].values() if t["owner"] == uid)
    if count >= lim:
        return (f"Your plan allows {lim} task(s). Tap 💎 My Plan to upgrade." if lim
                else "Your plan has no active tasks. Tap 💎 My Plan to upgrade.")
    return None


def eff_settings(uid, t):
    """Task settings with premium features switched off if the owner's plan lacks them."""
    s = {**t["settings"], "_mb": plan_cap(uid, "mb"), "_uid": uid}
    if not is_premium(uid):
        s.update(translate="", watermark="", link_rules=[], ai_rewrite=False, tg_link="", link_map=[])
    return s


_rate = {}              # uid -> recent action times
_rate_warned = {}
_code_hits = {}         # uid -> recent login-code request times


def rate_ok(uid):
    """False if the user is sending too many taps/messages (owner exempt)."""
    if uid == OWNER_ID:
        return True
    nowt = time.time()
    q = _rate.setdefault(uid, deque())
    while q and q[0] < nowt - RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(nowt)
    if len(_rate) > 5000:
        _rate.clear()
    return True


def rate_warn(uid):
    """True at most once per 30 s, so the 'slow down' note itself isn't spam."""
    nowt = time.time()
    if _rate_warned.get(uid, 0) > nowt - 30:
        return False
    _rate_warned[uid] = nowt
    return True


def plan_cap(uid, key):
    if uid == OWNER_ID:
        return None
    pk = "basic" if is_locked(uid) else plan_key(uid)
    return CAPS.get(pk, CAPS["basic"])[key]


def logins_busy():
    return sum(1 for s in state.values() if s.get("client")) >= MAX_LOGINS_AT_ONCE


def code_budget(uid):
    """Allow only a few login-code requests per hour per user (owner exempt)."""
    if uid == OWNER_ID:
        return True
    nowt = time.time()
    q = _code_hits.setdefault(uid, deque())
    while q and q[0] < nowt - 3600:
        q.popleft()
    if len(q) >= CODE_REQUESTS_PER_HOUR:
        return False
    q.append(nowt)
    return True


def claim_referral(uid):
    """A referral is rewarded when the friend really logs in an account (not just joins)."""
    r = user_rec(uid)
    if r and r.get("referred_by") and not r.get("ref_paid"):
        r["ref_paid"] = True
        reward_referrer(r["referred_by"])
        save()


def month_key(ts):
    d = datetime.fromtimestamp(ts, timezone(timedelta(minutes=UTC_OFFSET_MINUTES)))
    return d.year * 12 + d.month - 1


def pay_caption(pid, p):
    u = db["users"].get(str(p["uid"]), {})
    un = f" @{u['username']}" if u.get("username") else ""
    return (f"💳 <b>Payment #{pid}</b>\nUser: {esc(u.get('name') or 'User')}{esc(un)} (<code>{p['uid']}</code>)\n"
            f"Plan: {PLANS[p['plan']]['name']} – ₹{p['amount']} / {PLAN_DAYS} days"
            f"{' (coupon ' + esc(p['disc']) + ')' if p.get('disc') else ''}\n\n"
            f"Check your UPI app for ₹{p['amount']} before approving.")


def pay_buttons(pid):
    return [[Button.inline("✅ Approve", f"pay:ok:{pid}".encode()),
             Button.inline("❌ Reject", f"pay:no:{pid}".encode())]]


def pays_view():
    items = list(db["payments"].items())
    done = [p for _, p in items if p["status"] == "approved"]
    when = lambda p: p.get("done") or p.get("time", 0)
    cur = month_key(time.time())

    def part(k):
        sel = [p for p in done if month_key(when(p)) == k]
        return sum(p.get("amount", 0) for p in sel), len(sel)
    (a, an), (b, bn) = part(cur), part(cur - 1)
    by = Counter(PLANS[p["plan"]]["name"] for p in done)
    soon = sum(1 for u in db["users"].values()
               if u.get("expires") and u.get("plan") != "trial" and 0 < u["expires"] - time.time() <= 3 * 86400)
    pending = [(pid, p) for pid, p in items if p["status"] == "pending"]
    lines = ["💰 <b>Payments</b>", "",
             f"This month: <b>₹{a}</b> ({an} payment(s))", f"Last month: ₹{b} ({bn})",
             f"All time: ₹{sum(p.get('amount', 0) for p in done)} ({len(done)})",
             "By plan: " + (", ".join(f"{k} {v}" for k, v in by.items()) or "-"),
             f"Paid plans ending within 3 days: {soon}", f"Pending approval: {len(pending)}", "", "<b>Latest</b>"]
    icon = {"approved": "✅", "rejected": "❌", "pending": "⏳", "failed": "⚠️"}
    for pid, p in sorted(items, key=lambda kv: kv[1].get("time", 0), reverse=True)[:10]:
        nm = db["users"].get(str(p["uid"]), {}).get("name") or str(p["uid"])
        lines.append(f"{icon.get(p['status'], '•')} #{pid} • {esc(nm)} • {PLANS[p['plan']]['name']} "
                     f"₹{p['amount']} • {fmt_ts(p['time'])[:6]}")
    if not items:
        lines.append("No payments yet.")
    lines += ["", "Totals count approved UPI payments only (not /grant or coupons)."]
    btns = [[Button.inline(f"⏳ #{pid} • ₹{p['amount']} {PLANS[p['plan']]['name']}", f"payshow:{pid}".encode())]
            for pid, p in pending[:8]]
    btns.append([Button.inline("◀️ Admin", b"admin")])
    return "\n".join(lines), btns


pending_ref = {}        # uid -> referrer uid (from a /start ref_<id> link, until the user registers)
PLAN_RANK = {"trial": 0, "basic": 1, "pro": 2, "unlimited": 3}


def give_bonus(uid, days):
    """Add bonus days to a user's plan. Returns False if there is nothing to extend."""
    u = user_rec(uid)
    if not u:
        return False
    exp = u.get("expires", 0)
    if u.get("plan") == "unlimited" and not exp:
        return False
    if exp and exp > time.time():
        u["expires"] = exp + days * 86400           # trial or paid plan: just add days
    else:
        u["plan"], u["expires"] = "pro", time.time() + days * 86400   # free Basic: Pro for a few days
    u["warned"] = False
    return True


def reward_referrer(ref):
    r = user_rec(ref)
    if ref == OWNER_ID or not r:
        return
    r["refs"] = r.get("refs", 0) + 1
    if r["refs"] > REF_MAX:
        return
    if give_bonus(ref, REF_BONUS_DAYS):
        asyncio.create_task(tell(ref, f"🎉 A friend joined with your link! <b>+{REF_BONUS_DAYS} days</b> "
                                      "were added to your plan."))


def ensure_user(uid, name="", username=""):
    """Create the 7-day trial record on first use. Returns True if the user is new."""
    if uid == OWNER_ID or str(uid) in db["users"]:
        return False
    rec = {"plan": "trial", "expires": time.time() + TRIAL_DAYS * 86400, "trial_used": True,
           "joined": time.time(), "warned": False, "name": name, "username": username}
    ref = pending_ref.pop(uid, None)
    if ref and ref != uid and allowed(ref) and (ref == OWNER_ID or str(ref) in db["users"]):
        rec["referred_by"] = ref
    db["users"][str(uid)] = rec
    save()
    return True


async def reg(event, uid):
    """ensure_user + remember the user's name/@username (also fills it in for older users)."""
    if uid == OWNER_ID:
        return False
    rec = user_rec(uid)
    if rec and rec.get("name") is not None:
        return False
    try:
        s = await event.get_sender()
        name, un = utils.get_display_name(s), getattr(s, "username", "") or ""
    except Exception:
        name, un = "", ""
    if rec:
        rec["name"], rec["username"] = name, un
        mark_dirty()
        return False
    return ensure_user(uid, name, un)


def activate_plan(uid, plan, days):
    u = db["users"].setdefault(str(uid), {"trial_used": True, "joined": time.time()})
    base = time.time()
    if u.get("plan") == plan and u.get("expires", 0) > base:
        base = u["expires"]                 # renewing the same plan adds to the time left
    u["plan"] = plan
    u["expires"] = base + days * 86400 if days else 0
    u["warned"] = False
    enforce_limits(uid)
    save()
    return u["expires"]


def fmt_ts(ts):
    return datetime.fromtimestamp(ts, timezone(timedelta(minutes=UTC_OFFSET_MINUTES))).strftime("%d %b %Y, %H:%M")


def days_left(ts):
    return max(0, int((ts - time.time() + 86399) // 86400))


def plan_short(uid):
    info = plan_info(uid)
    u = user_rec(uid) or {}
    exp = 0 if uid == OWNER_ID else u.get("expires", 0)
    return f"{info['name']} • {days_left(exp)} day(s) left" if exp else info["name"]


def buyable_plans():
    return [k for k in ("basic", "pro", "unlimited") if not (k == "basic" and FREE_BASIC_AFTER_TRIAL)]


def broadcast_ids():
    return {OWNER_ID, *(int(k) for k in db["users"])}


def lim_text(v):
    return "unlimited" if v is None else str(v)


def plan_desc(key):
    p = PLANS[key]
    price = f"₹{p['price']}/month" if p["price"] else f"{TRIAL_DAYS} days free"
    return (f"<b>{p['name']}</b> – {price}\n"
            f"   {lim_text(p['tasks'])} tasks • {lim_text(p['extra'])} extra target(s) • "
            f"{lim_text(p['xsrc'])} extra source(s) • "
            f"{'premium features' if p['premium'] else 'no premium features'}")


def terms_view():
    return ("🔒 <b>Privacy &amp; Terms</b>\n\n"
            "To forward for you, this bot logs in to <b>your Telegram account</b> and reads/posts on your behalf.\n\n"
            "• <b>Stored:</b> your login session (encrypted), your task settings and your plan info.\n"
            "• Your 2-step password and login code are used once and <b>not</b> stored.\n"
            "• The bot owner could technically use a stored session, so continue only if you trust this bot.\n"
            "• <b>🚪 Logout</b> or <b>🗑 Delete My Data</b> removes your login and also ends the session on "
            "Telegram. You can check it in Telegram → Settings → Devices.\n"
            "• If you switch on <b>🤖 AI Rewrite</b>, the text of your posts (without links and @usernames) is "
            "sent to Google's Gemini API to be rewritten.\n"
            + ("• A private cloud copy of your data (your login stays encrypted) is kept in the owner's "
               "MongoDB.\n" if MONGO_URI else "")
            + "• You are responsible for what you forward. Respect copyright and Telegram's rules.\n"
            "• Never share your login code with anyone.",
            [[Button.inline("✅ I Agree & Continue", b"loginok")],
             [Button.inline("Cancel", b"main")]])


async def wipe_user(uid):
    """Delete everything stored about a user (keeps only the plan/trial + payment records)."""
    await drop_user(uid)
    gone = {tid for tid, t in db["tasks"].items() if t["owner"] == uid}
    for tid in gone:
        db["tasks"].pop(tid, None)
        oldjobs.pop(tid, None)
        recent.pop(tid, None)
        last_flush.pop(tid, None)
        alerts.pop(tid, None)
    for k in [k for k in msgmap if k[0] in gone]:
        msgmap.pop(k, None)
    db["expiry"] = [e for e in db["expiry"] if e["uid"] != uid]
    logs.pop(uid, None)
    u = user_rec(uid)
    if u:
        u["agreed"] = False
    save()


def redeem_coupon(uid, raw):
    code = raw.strip().upper()
    c = db["coupons"].get(code)
    u = user_rec(uid)
    if uid == OWNER_ID:
        return "You're the owner 🙂 — no coupon needed."
    if not c or not c.get("active", True):
        return "❌ Invalid coupon code."
    if uid in c["used"]:
        return "❌ You already used this coupon."
    if c.get("max") and len(c["used"]) >= c["max"]:
        return "❌ This coupon has reached its limit."
    if not u:
        return "❌ Please send /start first."
    if c["type"] == "pct":
        if u.get("discount"):
            return "❌ You already have a discount waiting. Use it on your next purchase first."
        u["discount"] = {"pct": c["pct"], "code": code}
        c["used"].append(uid)
        save()
        return f"✅ <b>{c['pct']}% off</b> applied to your next purchase."
    plan, days = c["plan"], c["days"]
    if PLAN_RANK[plan_key(uid)] > PLAN_RANK[plan]:
        return "❌ Your current plan is already higher than this coupon's plan."
    exp = activate_plan(uid, plan, days)
    c["used"].append(uid)
    save()
    return f"✅ <b>{PLANS[plan]['name']}</b> plan activated for {days} day(s), until {fmt_ts(exp)}."


def price_for(uid, key):
    """(amount, discount_code) for buying plan `key`, applying a waiting discount."""
    price = PLANS[key]["price"]
    d = (user_rec(uid) or {}).get("discount")
    if d:
        return max(1, round(price * (100 - d["pct"]) / 100)), d["code"]
    return price, None


def bot_link(uid):
    return f"https://t.me/{flags.get('bot_username') or 'Forwarbyanony_bot'}?start=ref_{uid}"


def ref_view(uid):
    n = (user_rec(uid) or {}).get("refs", 0)
    link = bot_link(uid)
    share = ("https://t.me/share/url?url=" + urllib.parse.quote(link)
             + "&text=" + urllib.parse.quote("Auto-forward Telegram posts between channels. Free 7-day trial!"))
    return (f"🎁 <b>Refer &amp; Earn</b>\n\nShare your link. For every friend who joins the channel, starts "
            f"the bot and logs in their account you get <b>+{REF_BONUS_DAYS} days</b> on your plan (up to {REF_MAX} friends).\n\n"
            f"Your link:\n<code>{link}</code>\n\n"
            f"Friends joined: {n} • Rewarded: {min(n, REF_MAX)} / {REF_MAX}",
            [[Button.url("📤 Share Link", share)], [Button.inline("◀️ My Plan", b"plan")]])


def users_view(page=0):
    per = 8
    rows = sorted(db["users"].items(), key=lambda kv: kv[1].get("joined", 0), reverse=True)
    pages = max(1, (len(rows) + per - 1) // per)
    page = min(max(page, 0), pages - 1)
    lines = [f"👥 <b>Users</b> ({len(rows)}) • page {page + 1}/{pages}", ""]
    for k, u in rows[page * per:(page + 1) * per]:
        uid = int(k)
        un = f" @{u['username']}" if u.get("username") else ""
        n = sum(1 for t in db["tasks"].values() if t["owner"] == uid)
        lines.append(f"{'🚫 ' if uid in db['banned'] else ''}<b>{esc(u.get('name') or '?')}</b>{esc(un)}\n"
                     f"   <code>{uid}</code> • {esc(plan_short(uid))} • {n} task(s)"
                     f"{' • 🟢' if uid in clients else ''}")
    if not rows:
        lines.append("No users yet.")
    lines += ["", "Search: <code>/user ID, @username or name</code>"]
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ Prev", f"users:{page - 1}".encode()))
    if page < pages - 1:
        nav.append(Button.inline("Next ▶️", f"users:{page + 1}".encode()))
    btns = ([nav] if nav else []) + [[Button.inline("◀️ Admin", b"admin")]]
    return "\n".join(lines), btns


def user_card(uid_s, u):
    uid = int(uid_s)
    un = f" @{u['username']}" if u.get("username") else ""
    mine = [t for t in db["tasks"].values() if t["owner"] == uid]
    paid = sum(1 for p in db["payments"].values() if p["uid"] == uid and p["status"] == "approved")
    return (f"👤 <b>{esc(u.get('name') or '?')}</b>{esc(un)}\nID: <code>{uid}</code>\n"
            f"Plan: {esc(plan_short(uid))}\nJoined: {fmt_ts(u.get('joined', 0)) if u.get('joined') else '?'}\n"
            f"Tasks: {len(mine)} ({sum(1 for t in mine if t['running'])} running) • "
            f"Logged in: {'yes' if uid in clients else 'no'}\n"
            f"Referrals: {u.get('refs', 0)} • Paid plans: {paid}"
            f"{' • 🚫 blocked' if uid in db['banned'] else ''}")


async def handle_coupon_cmd(event, raw):
    b = raw.split()
    sub = b[1].lower() if len(b) > 1 else ""
    usage = ("🎟 <b>Coupons</b>\n"
             "<code>/coupon plan CODE pro 7 [max_uses]</code>  gift a plan for N days\n"
             "<code>/coupon pct CODE 20 [max_uses]</code>  % off the user's next purchase\n"
             "<code>/coupon list</code>\n<code>/coupon off CODE</code> / <code>on CODE</code> / <code>del CODE</code>")
    ok_code = lambda c: re.fullmatch(r"[A-Z0-9]{3,20}", c) is not None
    reply = lambda t: event.respond(t, parse_mode="html")
    cps = db["coupons"]
    if sub == "list":
        if not cps:
            return await reply("No coupons yet.\n\n" + usage)
        lines = []
        for code, c in cps.items():
            what = f"{c['pct']}% off" if c["type"] == "pct" else f"{PLANS[c['plan']]['name']} {c['days']}d"
            lines.append(f"<code>{code}</code> • {what} • used {len(c['used'])}/{c.get('max') or '∞'} • "
                         f"{'on' if c.get('active', True) else 'off'}")
        return await reply("🎟 <b>Coupons</b>\n" + "\n".join(lines))
    if sub in ("on", "off", "del") and len(b) == 3:
        code = b[2].upper()
        if code not in cps:
            return await reply("Coupon not found.")
        if sub == "del":
            cps.pop(code)
        else:
            cps[code]["active"] = sub == "on"
        save()
        return await reply(f"✅ {code}: {sub}")
    if sub == "plan" and len(b) in (5, 6) and b[3].lower() in ("basic", "pro", "unlimited") \
            and b[4].isdigit() and int(b[4]) >= 1 and (len(b) == 5 or b[5].isdigit()):
        code = b[2].upper()
        if not ok_code(code):
            return await reply("Code must be 3-20 letters/digits.")
        if code in cps:
            return await reply("That code already exists. Delete it first: /coupon del " + code)
        cps[code] = {"type": "plan", "plan": b[3].lower(), "days": int(b[4]),
                     "max": int(b[5]) if len(b) == 6 else 0, "used": [], "active": True}
        save()
        return await reply(f"✅ Coupon <code>{code}</code> created: {PLANS[b[3].lower()]['name']} for {b[4]} day(s).")
    if sub == "pct" and len(b) in (4, 5) and b[3].isdigit() and 1 <= int(b[3]) <= 99 \
            and (len(b) == 4 or b[4].isdigit()):
        code = b[2].upper()
        if not ok_code(code):
            return await reply("Code must be 3-20 letters/digits.")
        if code in cps:
            return await reply("That code already exists. Delete it first: /coupon del " + code)
        cps[code] = {"type": "pct", "pct": int(b[3]), "max": int(b[4]) if len(b) == 5 else 0,
                     "used": [], "active": True}
        save()
        return await reply(f"✅ Coupon <code>{code}</code> created: {b[3]}% off the next purchase.")
    return await reply(usage)



def join_view():
    return ("📢 <b>One quick step</b>\n\n"
            "Join our channel to unlock the bot. You'll get updates and support there.\n\n"
            "1️⃣ Tap <b>Join Channel</b>\n2️⃣ Come back and tap <b>✅ I've Joined</b>",
            [[Button.url("📢 Join Channel", f"https://t.me/{CHANNEL.lstrip('@')}")],
             [Button.inline("✅ I've Joined", b"chk")]])


def welcome_text():
    p = PLANS["trial"]
    return (f"🎉 <b>Welcome!</b> Your {TRIAL_DAYS}-day free trial has started.\n"
            f"You get {p['tasks']} tasks to try everything except the premium tools.\n\n"
            "👉 Tap <b>🔐 Login Account</b>, then create your first task.\n\n")


def plan_view(uid):
    info = plan_info(uid)
    u = user_rec(uid) or {}
    exp = 0 if uid == OWNER_ID else u.get("expires", 0)
    used = sum(1 for t in db["tasks"].values() if t["owner"] == uid)
    lines = ["💎 <b>My Plan</b>", "", f"Plan: <b>{esc(info['name'])}</b>"]
    if exp:
        lines.append(f"Ends: {fmt_ts(exp)} ({days_left(exp)} day(s) left)")
    lines += [f"Tasks: {used} / {lim_text(info['tasks'])}",
              f"Extra targets per task: {lim_text(info['extra'])}",
              "Premium (Translate, Watermark, Link Rules, AI Rewrite): "
              + ("✅ included" if info["premium"] else "🔒 not included")]
    if is_locked(uid):
        lines += ["", "⚠️ Your tasks are paused until you buy a plan."]
    d = u.get("discount")
    if d:
        lines.append(f"🎟 Discount waiting: {d['pct']}% off your next purchase")
    return "\n".join(lines), [[Button.inline("⬆️ Upgrade / Renew", b"plans")],
                              [Button.inline("🎁 Refer & Earn", b"ref")],
                              [Button.inline("🏠 Main Menu", b"main")]]


def plans_view():
    lines = ["⬆️ <b>Plans</b>", ""]
    for k in ("trial", "basic", "pro", "unlimited"):
        lines.append(plan_desc(k))
    lines += ["", "Premium features = Translate, Watermark, Link Rules (link swap), AI Rewrite."]
    if FREE_BASIC_AFTER_TRIAL:
        lines.append("After the free trial you stay on Basic.")
    lines.append(f"Each payment adds {PLAN_DAYS} days.")
    btns = [[Button.inline(f"Buy {PLANS[k]['name']} – ₹{PLANS[k]['price']}", f"buy:{k}".encode())]
            for k in buyable_plans()]
    btns.append([Button.inline("🎟 Have a coupon?", b"cpn")])
    btns.append([Button.inline("🏠 Main Menu", b"main")])
    return "\n".join(lines), btns


async def is_member(uid):
    """True if the user joined CHANNEL (bot must be admin there). Cached for 5 minutes."""
    if uid == OWNER_ID:
        return True
    if joined_cache.get(uid, 0) > time.time():
        return True
    ok = True
    try:
        perm = await bot.get_permissions(CHANNEL, uid)
        part = getattr(perm, "participant", None)
        if perm is None or isinstance(part, (ChannelParticipantLeft, ChannelParticipantBanned)):
            ok = False
    except UserNotParticipantError:
        ok = False
    except Exception as e:
        # most likely the bot is not an admin of the channel: don't lock everyone out
        print(f"Force-join check failed: {e!r}")
        if not flags["member_err"]:
            flags["member_err"] = True
            try:
                await bot.send_message(OWNER_ID, f"⚠️ Force-join check failed: {esc(repr(e)[:150])}\n"
                                       f"Make sure the bot is an ADMIN of {CHANNEL}. Until then the check is skipped.",
                                       parse_mode="html")
            except Exception:
                pass
        ok = True
    if ok:
        joined_cache[uid] = time.time() + 300
    else:
        joined_cache.pop(uid, None)
    return ok


async def tell(uid, text, buttons=None):
    try:
        await bot.send_message(uid, text, buttons=buttons, parse_mode="html")
    except Exception as e:
        print(f"Notify {uid} failed: {e!r}")


async def check_plans():
    """Expire plans (-> Basic), pause over-limit tasks, send reminders."""
    nowts = time.time()
    for uid_s, u in list(db["users"].items()):
        exp = u.get("expires", 0)
        if not exp:
            continue
        uid = int(uid_s)
        up = [[Button.inline("⬆️ Upgrade", b"plans")]]
        if exp <= nowts:
            was = PLANS.get(u["plan"], PLANS["basic"])["name"]
            u["plan"], u["expires"], u["warned"] = "basic", 0, False
            enforce_limits(uid)
            save()
            msg = (f"⌛ <b>Your {esc(was)} plan has ended.</b>\n\n")
            if is_locked(uid):
                msg += "Your tasks are paused. Buy a plan to continue."
            else:
                b = PLANS["basic"]
                msg += (f"You've been moved to <b>Basic</b> ({b['tasks']} tasks, {b['extra']} extra target, "
                        "no premium features). Tasks over this limit are paused.\n\n"
                        "Want more tasks, extra targets and premium features? Upgrade 👇")
            await tell(uid, msg, up)
        elif exp - nowts <= REMIND_DAYS * 86400 and not u.get("warned"):
            u["warned"] = True
            mark_dirty()
            name = PLANS.get(u["plan"], PLANS["basic"])["name"]
            await tell(uid, f"⏰ Your <b>{esc(name)}</b> plan ends in {days_left(exp)} day(s) "
                            f"({fmt_ts(exp)}). Renew to keep your limits.", up)


# ---------------- schedule helpers ----------------
def hm(x):
    h, m = x.split(":")
    return int(h) * 60 + int(m)


def in_window(sc, dt):
    cur = dt.hour * 60 + dt.minute
    a, b = hm(sc["start"]), hm(sc["end"])
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def should_hold(t):
    sc = t["settings"]["sched"]
    if sc["mode"] == "window":
        return not in_window(sc, now())
    return sc["mode"] == "fixed"


def valid_time(x):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", x.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"'{x.strip()}' is not a valid time. Use HH:MM, e.g. 09:30")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_schedule(raw):
    raw = raw.strip().lower()
    if raw in ("off", "clear"):
        return {"mode": "off"}
    m = re.fullmatch(r"window\s+(\S+?)\s*-\s*(\S+)", raw)
    if m:
        return {"mode": "window", "start": valid_time(m.group(1)), "end": valid_time(m.group(2))}
    m = re.fullmatch(r"fixed\s+(.+)", raw)
    if m:
        times = sorted({valid_time(x) for x in m.group(1).split(",") if x.strip()})
        if not times:
            raise ValueError("Send at least one time, e.g. fixed 09:00,18:00")
        return {"mode": "fixed", "times": times}
    raise ValueError("Use: off, window 09:00-22:00, or fixed 09:00,13:00,18:00")


# ---------------- link rules / unshorten ----------------
MDLINK = re.compile(r"\[([^\]]*)\]\((?:https?://|tg://)[^)]*\)")
URL = re.compile(r"(?:https?://|t\.me/)\S+", re.I)
LINK = re.compile(r"https?://[^\s)\]>]+", re.I)
USERNAME = re.compile(r"(?<!\w)@\w{4,}")


def parse_rule(line):
    """'domain => url' replaces the whole link; 'domain ? key=value' sets a query parameter."""
    line = line.strip()
    if "=>" in line:
        d, v = line.split("=>", 1)
        if d.strip() and v.strip():
            return ("replace", d.strip().lower(), v.strip())
    elif "?" in line:
        d, v = line.split("?", 1)
        k, _, val = v.strip().partition("=")
        if d.strip() and k.strip():
            return ("param", d.strip().lower(), (k.strip(), val.strip()))
    return None


def apply_rules(url, rules):
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    changed = False
    for kind, dom, val in rules:
        if dom != "*" and not (host == dom or host.endswith("." + dom)):
            continue
        if kind == "replace":
            return val
        q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
        q[val[0]] = val[1]
        p = p._replace(query=urllib.parse.urlencode(q))
        changed = True
    return urllib.parse.urlunparse(p) if changed else url


def _resolve_sync(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        return e.url or url


async def resolve_url(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in SHORTENERS:
        return url
    try:
        return await asyncio.to_thread(_resolve_sync, url)
    except Exception:
        return url


async def swap_links(text, s):
    rules = [r for r in (parse_rule(x) for x in s["link_rules"]) if r]
    mapping = {}
    for raw in dict.fromkeys(LINK.findall(text)):
        url = raw.rstrip(".,;:!?")
        tail = raw[len(url):]
        new = await resolve_url(url) if s["unshorten"] else url
        new = apply_rules(new, rules)
        if new != url:
            mapping[raw] = new + tail
    return LINK.sub(lambda m: mapping.get(m.group(0), m.group(0)), text) if mapping else text


# ---------------- translate ----------------
TG_URL = re.compile(r"(?<![\w@.\-/])(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s)\]>]*"
                    r"|tg://[^\s)\]>]*", re.I)
TG_HEAD = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/", re.I)
TITLE_RE = re.compile(r"\b(?:TITLE|Name)\s*[:\-]\s*(.+)", re.I)
YEAR_RE = re.compile(r"\bYEAR\s*[:\-]\s*(\d{4})\b", re.I)
QUALITY_RE = re.compile(r"\b(?:Quality|Resolution)\s*[:\-]\s*([0-9]{3,4}p)\b", re.I)
SIZE_RE = re.compile(r"\bSize\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB))", re.I)


def parse_tg_link(raw):
    r = raw.strip()
    if r.lower() in ("", "off", "clear"):
        return ""
    m = re.fullmatch(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/"
                     r"(\+[\w-]+|joinchat/[\w-]+|[A-Za-z0-9_]{4,})/?", r, re.I)
    if m:
        return "https://t.me/" + m.group(1)
    m = re.fullmatch(r"@([A-Za-z][A-Za-z0-9_]{3,31})", r)
    if m:
        return "https://t.me/" + m.group(1)
    raise ValueError("Send your channel link like https://t.me/mychannel (or @mychannel, or an invite link "
                     "https://t.me/+AbCd...). Send 'clear' to turn it off.")


def replace_tg_links(text, link, mentions=False):
    """Swap EVERY Telegram link (channels, groups, posts, invites, bots) for `link`. Optionally @usernames too."""
    def sub(m):
        raw = m.group(0)
        url = raw.rstrip(".,;:!?")
        return link + raw[len(url):]
    text = TG_URL.sub(sub, text)
    if mentions:
        path = link.split("t.me/", 1)[-1]
        own = link if path.startswith(("+", "joinchat")) else "@" + path
        text = USERNAME.sub(lambda m: m.group(0) if m.group(0).lower().endswith("bot") else own, text)
    return text


ANY_URL = re.compile(r"(?:https?://|tg://)[^\s)\]>]+|(?<![\w@.\-/])(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s)\]>]*",
                     re.I)
MAP_SEL = re.compile(r"(tg|link|other|all)(\d+)?", re.I)


def _is_tg(url):
    return bool(TG_HEAD.match(url)) or url.lower().startswith("tg://")


def parse_map_rule(line):
    """'tg1 => https://t.me/x' -> ('tg', 1, 'https://t.me/x'); 'site.in => remove' -> ('match', 'site.in', None)."""
    m = re.fullmatch(r"\s*(\S+)\s*(?:=>|->)\s*(\S+)\s*", line)
    if not m:
        raise ValueError(f"'{line.strip()}' is not a rule. Use: selector => new link")
    sel, rep_ = m.group(1), m.group(2)
    if rep_.lower() in ("remove", "delete", "del"):
        repl = None
    elif re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", rep_):
        repl = "https://t.me/" + rep_[1:]
    elif re.match(r"(?:https?://|tg://)\S+", rep_, re.I):
        repl = rep_
    elif TG_HEAD.match(rep_):
        repl = "https://" + re.sub(r"^(?:www\.)?", "", rep_, flags=re.I)
    else:
        raise ValueError(f"'{rep_}' must be a link (https://...), an @username, or the word remove.")
    sm = MAP_SEL.fullmatch(sel)
    if sm:
        kind, n = sm.group(1).lower(), (int(sm.group(2)) if sm.group(2) else None)
        if kind == "link" and n is None:
            raise ValueError("'link' needs a number, e.g. link2. Use 'all' for every link.")
        if kind == "all" and n is not None:
            raise ValueError("'all' has no number. Use link2, tg2 or other2 for one link.")
        if n == 0:
            raise ValueError("Numbers start at 1.")
        return (kind, n, repl)
    if "." in sel or "/" in sel:
        return ("match", re.sub(r"^(?:https?://)?(?:www\.)?", "", sel.lower()).rstrip("/"), repl)
    raise ValueError(f"'{sel}' is not a selector. Use tg1, tg2, other1, link3, tg, all, or a site like example.com")


INFO_KEYS = {"title", "year", "quality", "size", "genre", "language", "episode"}


def parse_template(tpl, text):
    """Fill the template's {title} {year} {quality} {size} ... from the POST text.
    Unknown keys are left as-is. Returns (filled_template, info_dict)."""
    info = {}
    if not text:
        return tpl, info
    m = TITLE_RE.search(text)
    if m:
        info["title"] = m.group(1).strip()
    m = YEAR_RE.search(text)
    if m:
        info["year"] = m.group(1).strip()
    m = QUALITY_RE.search(text)
    if m:
        info["quality"] = m.group(1).strip()
    m = SIZE_RE.search(text)
    if m:
        info["size"] = m.group(1).strip()
    m = re.search(r"\b(?:Language|Lang)\s*[:\-]\s*([A-Za-z][A-Za-z0-9 \-]{1,30})", text, re.I)
    if m:
        info["language"] = m.group(1).strip()
    m = re.search(r"\b(?:Genres?|Genre)\s*[:\-]\s*([A-Za-z][A-Za-z0-9 \-]{1,30})", text, re.I)
    if m:
        info["genre"] = m.group(1).strip()
    m = re.search(r"\b(S\d{1,2}-Ep\d{1,3}|S\d{1,2}\s*-\s*Ep\d{1,3}|Episode\s*\d+|Ep\s*\d+)", text, re.I)
    if m:
        info["episode"] = m.group(0).strip()
    out = tpl
    for k in INFO_KEYS:
        out = out.replace("{" + k + "}", info.get(k, "{" + k + "}"))
    return out, info


def parse_route(lines):
    """'keyword => -1001234567' -> [(keyword, target_id)]. Bad lines raise ValueError."""
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if "=>" not in ln:
            raise ValueError(f"'{ln}' is not a rule. Use: keyword => target_id")
        kw, tgt = ln.split("=>", 1)
        kw, tgt = kw.strip(), tgt.strip()
        if not kw or not tgt:
            raise ValueError(f"'{ln}' needs a keyword and a target id")
        if re.fullmatch(r"-?\d+", tgt):
            out.append((kw.lower(), int(tgt)))
        elif re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", tgt):
            out.append((kw.lower(), tgt))
        else:
            raise ValueError(f"'{tgt}' is not a channel. Use the -100... id or an @username.")
    return out


def pick_route_target(t, s, text):
    """Return the target id this post should go to, or None to use the normal target."""
    rules = s.get("route") or []
    if not rules:
        return None
    low = text.lower()
    for kw, tid in rules:
        if kw and kw in low:
            return tid if isinstance(tid, int) else tid
    return None


def apply_link_map(text, lines):
    rules = []
    for ln in lines:
        try:
            rules.append(parse_map_rule(ln))
        except ValueError:
            continue
    ms = list(ANY_URL.finditer(text))
    if not ms or not rules:
        return text
    out, last, removed = [], 0, False
    counts = {"tg": 0, "other": 0, "link": 0}
    for m in ms:
        raw = m.group(0)
        url = raw.rstrip(".,;:!?")
        tail = raw[len(url):]
        kind = "tg" if _is_tg(url) else "other"
        counts[kind] += 1
        counts["link"] += 1
        norm = re.sub(r"^(?:https?://)?(?:www\.)?", "", url.lower()).rstrip("/")
        for sel, n, repl in rules:
            if sel == "match":
                hit_ = norm == n or norm.startswith(n.rstrip("/") + "/") or norm.startswith(n + "?") \
                    or (n.count("/") == 0 and norm.split("/")[0] == n)
            elif sel == "all":
                hit_ = True
            else:
                hit_ = (kind == sel or sel == "link") and (n is None or counts[sel] == n)
            if hit_:
                out.append(text[last:m.start()])
                out.append("" if repl is None else repl + tail)
                removed = removed or repl is None
                last = m.end()
                break
    out.append(text[last:])
    res = "".join(out)
    if removed:
        res = re.sub(r"\[([^\]]*)\]\(\)", r"\1", res)
        res = re.sub(r"[ \t]+\n", "\n", res)
        res = re.sub(r"\n{3,}", "\n\n", res).strip()
    return res


PROTECT = re.compile(r"https?://\S+|t\.me/\S+|@\w{4,}")


def _translate_chunk_sync(chunk, lang):
    q = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": lang, "dt": "t", "q": chunk})
    req = urllib.request.Request("https://translate.googleapis.com/translate_a/single?" + q,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def split_chunks(text, limit=1500):
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur)
            cur = ""
        cur = cur + "\n" + line if cur else line
    if cur:
        chunks.append(cur)
    return chunks


async def translate_text(text, lang):
    saved = []

    def hide(m):
        saved.append(m.group(0))
        return f"ZQX{len(saved) - 1}XQZ"

    hidden = PROTECT.sub(hide, text)
    try:
        out = []
        for chunk in split_chunks(hidden):
            out.append(await asyncio.to_thread(_translate_chunk_sync, chunk, lang)
                       if chunk.strip() else chunk)
    except Exception as e:
        print(f"Translate failed: {e!r}")
        return text
    res = "\n".join(out)
    return re.sub(r"ZQX\s*(\d+)\s*XQZ",
                  lambda m: saved[int(m.group(1))] if int(m.group(1)) < len(saved) else "",
                  res, flags=re.I)


# ---------------- AI rewrite (Gemini) ----------------
AI_DEFAULT_PROMPT = ("Rewrite the following Telegram post in your own words. Keep the same meaning, language and "
                     "tone. Keep emojis, numbers, prices and every token like ZQX0XQZ exactly as they are. "
                     "Output only the rewritten text.")
_ai_used = {}
_ai_sem = []


def ai_budget(uid):
    """Counts one AI rewrite against the user's daily cap. False when the cap is reached."""
    cap = plan_cap(uid, "ai")
    if cap is None:
        return True
    day = now().strftime("%Y%m%d")
    if len(_ai_used) > 5000:
        _ai_used.clear()
    n = _ai_used.get((uid, day), 0)
    if n >= cap:
        return False
    _ai_used[(uid, day)] = n + 1
    return True


def _ai_call_sync(text, prompt):
    body = json.dumps({"systemInstruction": {"parts": [{"text": prompt}]},
                       "contents": [{"role": "user", "parts": [{"text": text}]}],
                       "generationConfig": {"temperature": 0.7}}).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent", data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:
            detail = ""
        raise RuntimeError(f"Gemini error {e.code}: {detail[:100]}")
    cands = data.get("candidates") or []
    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
    out = "".join(p.get("text", "") for p in parts).strip()
    if not out:
        raise RuntimeError("Gemini returned no text (blocked or empty)")
    return out


async def ai_rewrite_text(text, prompt):
    """Rewrite with Gemini. Links and @usernames never go to the AI; if it loses one, we refuse the result."""
    saved = []

    def hide(m):
        saved.append(m.group(0))
        return f"ZQX{len(saved) - 1}XQZ"

    hidden = PROTECT.sub(hide, text)
    if not _ai_sem:
        _ai_sem.append(asyncio.Semaphore(2))     # at most 2 AI calls at the same time
    async with _ai_sem[0]:
        out = await asyncio.to_thread(_ai_call_sync, hidden, prompt)
    found = sorted(int(x) for x in re.findall(r"ZQX\s*(\d+)\s*XQZ", out))
    if found != list(range(len(saved))):
        raise RuntimeError("the AI changed or dropped a link/username")
    return re.sub(r"ZQX\s*(\d+)\s*XQZ", lambda m: saved[int(m.group(1))], out)


# ---------------- watermark ----------------
def _watermark_sync(data, text):
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = img.size
    size = max(14, w // 22)
    font = None
    for path in ("/system/fonts/Roboto-Regular.ttf", "/system/fonts/DroidSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x, y = max(0, w - tw - size), max(0, h - th - size)
    d.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 160))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 215))
    out = Image.alpha_composite(img, layer).convert("RGB")
    bio = io.BytesIO()
    out.save(bio, "JPEG", quality=90)
    bio.name = "photo.jpg"
    bio.seek(0)
    return bio


def is_restricted_error(err):
    return "RESTRICT" in (err or "").upper()


async def media_for(cl, msg, s, tmp, force=False):
    """Media to send for a message (None = no media).
    Applies the watermark to photos, and re-uploads media from protected
    (forwarding-restricted) channels instead of re-sending the original."""
    if not msg.media or isinstance(msg.media, MessageMediaWebPage) or not s["media"]:
        return None
    if s["watermark"] and PIL_OK and msg.photo:
        try:
            data = await cl.download_media(msg, file=bytes)
            return await asyncio.to_thread(_watermark_sync, data, s["watermark"])
        except Exception as e:
            print(f"Watermark failed: {e!r}")
    if s["reupload"] and (force or getattr(msg, "noforwards", False)):
        size = msg.file.size if msg.file and msg.file.size else 0
        cap_mb = s.get("_mb") or MAX_REUPLOAD_MB
        if cap_mb and size > cap_mb * 1024 * 1024:
            raise ValueError(f"file too large to re-upload on this plan ({size // 1048576} MB, limit {cap_mb} MB)")
        os.makedirs(TMP_DIR, exist_ok=True)
        path = await cl.download_media(
            msg, file=os.path.join(TMP_DIR, f"{abs(msg.chat_id or 0)}_{msg.id}"))
        if not path:
            raise RuntimeError("download failed")
        tmp.append(path)
        return path
    return msg.media


# ---------------- text pipeline ----------------
def hit(text, patterns):
    """True if any pattern matches. Entries starting with re: are regular expressions."""
    low = text.lower()
    for p in patterns:
        if p.lower().startswith("re:"):
            try:
                if re.search(p[3:], text, re.I):
                    return True
            except re.error:
                continue
        elif p.lower() in low:
            return True
    return False


PRICE_RE = re.compile(r"(?:\u20b9|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def deal_info(text):
    """Approximate (price, discount%) read from a deal text; None when not found."""
    prices = []
    for x in PRICE_RE.findall(text):
        try:
            prices.append(float(x.replace(",", "")))
        except ValueError:
            pass
    pcts = [float(x) for x in PCT_RE.findall(text) if 0 < float(x) < 100]
    price = min(prices) if prices else None
    disc = max(pcts) if pcts else None
    if disc is None and len(prices) >= 2 and max(prices) > min(prices):
        disc = (max(prices) - min(prices)) / max(prices) * 100
    return price, disc


async def process(text, s):
    """Edited text, or None if the message must be skipped."""
    text = text or ""
    if s["blacklist"] and hit(text, s["blacklist"]):
        return None
    if s["whitelist"] and not hit(text, s["whitelist"]):
        return None
    if s["min_discount"] or s["max_price"]:
        price, disc = deal_info(text)
        if s["min_discount"] and disc is not None and disc < s["min_discount"]:
            return None
        if s["max_price"] and price is not None and price > s["max_price"]:
            return None
    if s["no_links"]:
        text = URL.sub("", MDLINK.sub(r"\1", text))
    elif s["unshorten"] or s["link_rules"]:
        text = await swap_links(text, s)
    if s["no_usernames"]:
        text = USERNAME.sub("", text)
    for old, new in s["replace"].items():
        text = re.sub(re.escape(old), lambda m, n=new: n, text, flags=re.I)
    text = text.strip()
    if s.get("tg_link") and text:
        text = replace_tg_links(text, s["tg_link"], s.get("tg_mentions"))
    if s.get("link_map") and text:
        text = apply_link_map(text, s["link_map"])
    if s["translate"] and text:
        text = await translate_text(text, s["translate"])
    if s.get("ai_rewrite") and text:
        try:
            if not GEMINI_KEY:
                raise RuntimeError("AI is not set up on this bot")
            if len(text) > 8000:
                raise RuntimeError("the post is too long for AI")
            if not ai_budget(s.get("_uid", OWNER_ID)):
                raise RuntimeError("your daily AI limit is reached")
            text = await ai_rewrite_text(text, s.get("ai_prompt") or AI_DEFAULT_PROMPT)
        except Exception as e:
            s["_ai_fail"] = str(e)[:120]              # the original text is posted instead
    if s.get("template"):
        tpl, _ = parse_template(s["template"], text)
        text = (tpl + "\n\n" if text else tpl) + text
    if s["header"]:
        text = s["header"] + ("\n\n" + text if text else "")
    if s["footer"]:
        text = (text + "\n\n" if text else "") + s["footer"]
    return text


def msg_type(m):
    if getattr(m, "photo", None):
        return "photo"
    if getattr(m, "sticker", None):
        return "sticker"
    if getattr(m, "gif", None):
        return "gif"
    if getattr(m, "voice", None):
        return "voice"
    if getattr(m, "audio", None):
        return "audio"
    if getattr(m, "video", None) or getattr(m, "video_note", None):
        return "video"
    if getattr(m, "poll", None):
        return "poll"
    if m.media and not isinstance(m.media, MessageMediaWebPage):
        return "file"
    return "text"


# ---------------- forwarding engine ----------------
async def call(fn):
    """Run a send call. Returns (result, error_text)."""
    try:
        return await fn(), None
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            return await fn(), None
        except Exception as e2:
            return None, repr(e2)
    except Exception as e:
        return None, repr(e)


def sources_of(t, uid=None):
    """Primary source + extra sources (cut to what the owner's plan allows)."""
    out = [t["source"]] if t.get("source") else []
    extras = [e["id"] for e in t.get("extra_sources", [])]
    if uid is not None:
        lim = plan_info(uid)["xsrc"]
        if lim is not None:
            extras = extras[:lim]
    return out + [x for x in extras if x not in out]


def tasks_for(uid, chat_id):
    c = [(tid, t) for tid, t in db["tasks"].items()
         if t["owner"] == uid and t["running"]
         and chat_id in sources_of(t, uid) and t.get("target")]
    if not c:
        return c
    over = over_limit_ids(uid)
    return [(tid, t) for tid, t in c if tid not in over]


def remember(tid, chat_id, src_id, sent):
    if sent is not None and hasattr(sent, "id"):
        msgmap[(tid, chat_id, src_id)] = sent.id
        while len(msgmap) > 10000:
            msgmap.popitem(last=False)


def ids_of(sent):
    if sent is None:
        return []
    if isinstance(sent, (list, tuple)):
        return [x.id for x in sent if hasattr(x, "id")]
    return [sent.id] if hasattr(sent, "id") else []


async def alert(uid, tid, text):
    """Tell the task owner about a problem (at most once per ALERT_COOLDOWN per task)."""
    if time.time() - alerts.get(tid, 0) < ALERT_COOLDOWN:
        return
    alerts[tid] = time.time()
    name = db["tasks"].get(tid, {}).get("name", "?")
    try:
        await bot.send_message(uid, f"⚠️ <b>{esc(name)}</b>: {esc(text)}", parse_mode="html")
    except Exception:
        pass


async def send_copy(cl, tgt, text, medias, reply_to, s):
    """Send text/media to one target. Long text is split; long captions go in a separate message."""
    silent = s["silent"]
    if medias:
        files = medias if len(medias) > 1 else medias[0]
        if len(text) > CAPTION_LIMIT:
            sent, err = await call(lambda: cl.send_file(tgt, files, reply_to=reply_to, silent=silent))
            for chunk in split_chunks(text, TEXT_LIMIT):
                if err:
                    break
                _, err = await call(lambda: cl.send_message(tgt, chunk, silent=silent, link_preview=False))
            return sent, err
        return await call(lambda: cl.send_file(tgt, files, caption=text, reply_to=reply_to, silent=silent))
    sent = err = None
    for i, chunk in enumerate(split_chunks(text, TEXT_LIMIT) or [""]):
        res, e = await call(lambda: cl.send_message(
            tgt, chunk, reply_to=reply_to if i == 0 else None, silent=silent, link_preview=False))
        if i == 0:
            sent = res
        if e:
            return sent, e
    return sent, err


async def send_out(cl, uid, tid, t, msgs, text, force):
    """Send one message/album to the main target and every extra target.
    Returns (sent_to_main_target, error_text, delivered=[(target, [ids])])."""
    s = eff_settings(uid, t)
    extras = t.get("extra_targets", [])
    extra_lim = plan_info(uid)["extra"]
    if extra_lim is not None:
        extras = extras[:extra_lim]
    targets = [t["target"]] + [e["id"] for e in extras]
    routed = pick_route_target(t, s, text)
    if routed is not None and routed not in targets:
        targets = [routed] + [x for x in targets if x != routed]
    tmp = []
    delivered = []
    primary = (None, None)
    try:
        forward_mode = not s["copy"] and not force
        medias = []
        if not forward_mode:
            medias = [x for x in [await media_for(cl, m, s, tmp, force) for m in msgs] if x]
        first = msgs[0]
        reply_to = None
        if s["sync_reply"] and first.reply_to and first.reply_to.reply_to_msg_id:
            reply_to = msgmap.get((tid, getattr(first, "chat_id", None), first.reply_to.reply_to_msg_id))
        for i, tgt in enumerate(targets):
            if forward_mode:
                sent, err = await call(lambda: cl.forward_messages(
                    tgt, msgs if len(msgs) > 1 else msgs[0], silent=s["silent"]))
            else:
                sent, err = await send_copy(cl, tgt, text, medias, reply_to if i == 0 else None, s)
            if i == 0:
                primary = (sent, err)
            elif err:
                log(uid, tid, f"ERROR extra target {tgt}: {err[:80]}")
            if not err:
                delivered.append((tgt, ids_of(sent)))
        return primary[0], primary[1], delivered
    except Exception as e:
        return None, repr(e), delivered
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


async def process_group(cl, uid, tid, t, msgs, min_delay=0):
    """Filter, edit and send one message or one album (list of messages)."""
    s = eff_settings(uid, t)
    if not direction_ok(s.get("direction", "all"), msgs):
        return
    if s["types"] and not ({msg_type(m) for m in msgs} & set(s["types"])):
        bump(t, "skipped")
        log(uid, tid, f"skipped (type {msg_type(msgs[0])}) msg {msgs[0].id}")
        return
    caption = next((m.text for m in msgs if m.text), "")
    text = await process(caption, s)
    ai_fail = s.pop("_ai_fail", None)
    if ai_fail:
        log(uid, tid, f"AI Rewrite failed: {ai_fail}")
        await alert(uid, tid, f"AI Rewrite failed ({ai_fail}). The original text was posted.")
    if text is None:
        bump(t, "skipped")
        log(uid, tid, f"skipped (filter) msg {msgs[0].id}")
        return
    has_media = s["media"] and any(
        m.media and not isinstance(m.media, MessageMediaWebPage) for m in msgs)
    if not text and not has_media:
        return
    if s["dedupe"]:
        size = sum(m.file.size for m in msgs if m.file and m.file.size)
        sig = hashlib.md5(f"{text}|{size}".encode()).hexdigest()
        if sig in recent[tid]:
            bump(t, "skipped")
            log(uid, tid, f"skipped (duplicate) msg {msgs[0].id}")
            return
        recent[tid].append(sig)
    delay = max(s["delay"], min_delay)
    if delay:
        await asyncio.sleep(delay)
    sent, err, delivered = await send_out(cl, uid, tid, t, msgs, text, force=False)
    if err and s["reupload"] and is_restricted_error(err):
        log(uid, tid, f"source is protected, re-uploading msg {msgs[0].id}")
        sent, err, delivered = await send_out(cl, uid, tid, t, msgs, text, force=True)
    if err:
        bump(t, "errors")
        log(uid, tid, f"ERROR msg {msgs[0].id}: {err[:100]}")
        await alert(uid, tid, f"couldn't send msg {msgs[0].id}: {err[:150]}")
        return
    bump(t, "forwarded")
    log(uid, tid, f"forwarded msg {msgs[0].id}" + (f" (+{len(msgs) - 1} album)" if len(msgs) > 1 else "")
        + (f" to {len(delivered)} targets" if len(delivered) > 1 else ""))
    if len(msgs) == 1:
        remember(tid, getattr(msgs[0], "chat_id", None), msgs[0].id, sent)
    if s["delete_after"]:
        due = time.time() + s["delete_after"] * 60
        for tgt, ids in delivered:
            if ids:
                db["expiry"].append({"due": due, "uid": uid, "target": tgt, "ids": ids})
        mark_dirty()
    if s["pin"] and delivered and delivered[0][1]:
        try:
            await cl.pin_message(delivered[0][0], delivered[0][1][0], notify=False)
        except Exception as e:
            log(uid, tid, f"pin failed: {repr(e)[:80]}")


DIR_ORDER = ("all", "incoming", "outgoing")
DIR_LABEL = {"all": "📨 All messages", "incoming": "📥 Incoming only", "outgoing": "📤 My own only"}


def direction_ok(direction, msgs):
    """'incoming' skips messages sent by your own account (e.g. what you type to a bot); 'outgoing' the opposite."""
    out = bool(getattr(msgs[0], "out", False))
    return not ((direction == "incoming" and out) or (direction == "outgoing" and not out))


async def incoming(cl, uid, tid, t, msgs):
    if not direction_ok(t["settings"].get("direction", "all"), msgs):
        return
    if should_hold(t):
        t["queue"].append({"c": getattr(msgs[0], "chat_id", None) or t.get("source"), "ids": [m.id for m in msgs]})
        qcap = plan_cap(uid, "queue") or MAX_QUEUE
        if qcap:
            del t["queue"][:-qcap]
        mark_dirty()
        log(uid, tid, f"queued msg {msgs[0].id} (schedule)")
        return
    await process_group(cl, uid, tid, t, msgs)


def attach(uid, cl):
    async def on_new(event):
        msg = event.message
        if msg.grouped_id:          # albums are handled by on_album
            return
        for tid, t in tasks_for(uid, event.chat_id):
            await incoming(cl, uid, tid, t, [msg])

    async def on_album(event):
        for tid, t in tasks_for(uid, event.chat_id):
            await incoming(cl, uid, tid, t, list(event.messages))

    async def on_edit(event):
        msg = event.message
        for tid, t in tasks_for(uid, event.chat_id):
            s = eff_settings(uid, t)
            tmid = msgmap.get((tid, event.chat_id, msg.id))
            if tmid:
                if not s["sync_edit"]:
                    continue
                text = await process(msg.text, s)
                if not text:
                    continue
                try:
                    await cl.edit_message(t["target"], tmid, text, link_preview=False)
                    log(uid, tid, f"edit synced msg {msg.id}")
                except Exception as e:
                    print(f"Edit sync failed: {e!r}")
            elif s.get("forward_edited"):
                text = await process(msg.text, s)
                if text:
                    await process_group(cl, uid, tid, t, [msg])
                    log(uid, tid, f"forwarded edit {msg.id}")

    async def on_delete(event):
        if not event.chat_id:
            return
        for tid, t in tasks_for(uid, event.chat_id):
            if not t["settings"]["sync_delete"]:
                continue
            ids = [msgmap.pop((tid, event.chat_id, i)) for i in event.deleted_ids if (tid, event.chat_id, i) in msgmap]
            if ids:
                try:
                    await cl.delete_messages(t["target"], ids)
                    log(uid, tid, f"delete synced ({len(ids)})")
                except Exception as e:
                    print(f"Delete sync failed: {e!r}")

    cl.add_event_handler(on_new, events.NewMessage())
    cl.add_event_handler(on_album, events.Album())
    cl.add_event_handler(on_edit, events.MessageEdited())
    cl.add_event_handler(on_delete, events.MessageDeleted())
    return on_new, on_album, on_edit, on_delete


# ---------------- schedule queue worker ----------------
async def flush_queue(cl, uid, tid, t):
    sc = t["settings"]["sched"]
    while t["queue"] and t["running"]:
        if sc["mode"] == "window" and not in_window(sc, now()):
            break
        entry = t["queue"].pop(0)
        mark_dirty()
        chat, ids = (entry["c"], entry["ids"]) if isinstance(entry, dict) else (t["source"], entry)   # old queue format
        try:
            msgs = [m for m in await cl.get_messages(chat, ids=ids) if m]
            if msgs:
                await process_group(cl, uid, tid, t, msgs, min_delay=POST_GAP)
        except Exception as e:
            bump(t, "errors")
            log(uid, tid, f"ERROR queue flush: {repr(e)[:100]}")


async def flush_due():
    dt = now()
    stamp = dt.strftime("%Y%m%d%H%M")
    for tid, t in list(db["tasks"].items()):
        if not t["running"] or not t["queue"]:
            continue
        uid = t["owner"]
        cl = clients.get(uid)
        if not cl or tid in over_limit_ids(uid):
            continue
        sc = t["settings"]["sched"]
        due = False
        if sc["mode"] == "off":
            due = True
        elif sc["mode"] == "window":
            due = in_window(sc, dt)
        elif sc["mode"] == "fixed" and dt.strftime("%H:%M") in sc["times"] \
                and last_flush.get(tid) != stamp:
            last_flush[tid] = stamp
            due = True
        if due:
            await flush_queue(cl, uid, tid, t)


async def flush_expired():
    """Delete posts whose auto-delete time has come."""
    nowts = time.time()
    for e in [e for e in db["expiry"] if e["due"] <= nowts]:
        cl = clients.get(e["uid"])
        if not cl:
            continue                         # owner not connected yet; try again later
        try:
            await cl.delete_messages(e["target"], e["ids"])
        except Exception as ex:
            print(f"Auto-delete failed: {ex!r}")
        try:
            db["expiry"].remove(e)
        except ValueError:
            pass
        mark_dirty()


async def scheduler():
    while True:
        await asyncio.sleep(20)
        try:
            await flush_due()
            await flush_expired()
            await check_plans()
            if time.time() - flags.get("health_at", 0) > 600:
                flags["health_at"] = time.time()
                await check_sessions()
            today = now().strftime("%Y%m%d")
            if now().hour >= REPORT_HOUR and db.get("report_day") != today:
                db["report_day"] = today
                await send_reports()
            if flags["dirty"]:
                save()
        except Exception as e:
            print(f"Scheduler error: {e!r}")


# ---------------- old posts ----------------
async def iter_groups(cl, source, n):
    """Yield old posts oldest first, albums grouped. n=None means the whole channel."""
    async def gen():
        if n is None:
            async for m in cl.iter_messages(source, reverse=True):
                yield m
        else:
            msgs = [m async for m in cl.iter_messages(source, limit=n)]
            for m in reversed(msgs):
                yield m

    cur = []
    async for m in gen():
        if getattr(m, "action", None):
            continue
        if cur and m.grouped_id and cur[0].grouped_id == m.grouped_id:
            cur.append(m)
            continue
        if cur:
            yield cur
        cur = [m]
    if cur:
        yield cur


async def run_old(cl, uid, tid, t, n, note):
    job = oldjobs[tid] = {"stop": False}
    done = 0
    try:
        for src_chat in sources_of(t, uid):            # every source, one after the other
            async for g in iter_groups(cl, src_chat, n):
                if job["stop"]:
                    break
                await process_group(cl, uid, tid, t, g, min_delay=POST_GAP)
                done += 1
                if done % 10 == 0:
                    try:
                        await note.edit(f"📜 Old posts: {done} sent…",
                                        buttons=[[Button.inline("⏹ Stop", f"oldstop:{tid}".encode())]])
                    except Exception:
                        pass
            if job["stop"]:
                break
        result = f"📜 Old posts finished: {done} sent" + (" (stopped)" if job["stop"] else ".")
    except Exception as e:
        result = f"❌ Old posts failed after {done}: {esc(repr(e)[:150])}"
    finally:
        oldjobs.pop(tid, None)
        save()
    try:
        await note.edit(result, buttons=[[Button.inline("◀️ Back to Task", f"t:{tid}".encode())]])
    except Exception:
        pass


# ---------------- sessions ----------------
retry_connect = set()    # users whose saved login could not be reached (network); retried later


async def session_ended(uid):
    """Telegram ended this user's login: stop their tasks (kept) and tell them once."""
    for t in db["tasks"].values():
        if t["owner"] == uid:
            t["running"] = False
    cl = clients.pop(uid, None)
    if cl:
        try:
            await cl.disconnect()
        except Exception:
            pass
    db["sessions"].pop(str(uid), None)
    retry_connect.discard(uid)
    save()
    await tell(uid, "⚠️ <b>Your Telegram login has ended.</b>\n\nTelegram closed the session (for example you "
                    "ended it in Settings → Devices, or it expired). Your tasks are saved but stopped.\n\n"
                    "Log in again, then press ▶️ Start on your tasks.", [[Button.inline("🔐 Login Again", b"login")]])


async def try_connect(uid, sess):
    """True = connected, False = Telegram says the login is dead, None = network problem (keep the login)."""
    cl = TelegramClient(StringSession(sess), API_ID, API_HASH)
    try:
        await cl.connect()
        authorized = await cl.is_user_authorized()
    except Exception as e:
        print(f"User {uid}: could not reach Telegram ({e!r}); will retry")
        try:
            await cl.disconnect()
        except Exception:
            pass
        return None
    if not authorized:
        try:
            await cl.disconnect()
        except Exception:
            pass
        return False
    clients[uid] = cl
    attach(uid, cl)
    try:
        await cl.get_dialogs()
    except Exception as e:
        print(f"User {uid}: dialogs failed ({e!r})")
    print(f"User {uid} connected")
    return True


async def connect_sessions():
    for uid_s, sess in list(db["sessions"].items()):
        uid = int(uid_s)
        r = await try_connect(uid, sess)
        if r is None:
            retry_connect.add(uid)             # e.g. the phone had no internet: do NOT delete the login
        elif r is False:
            await session_ended(uid)


async def check_sessions():
    """About every 10 minutes: retry unreachable logins and notice logins Telegram has ended."""
    for uid in list(retry_connect):
        sess = db["sessions"].get(str(uid))
        if not sess:
            retry_connect.discard(uid)
            continue
        r = await try_connect(uid, sess)
        if r is None:
            continue
        retry_connect.discard(uid)
        if r is False:
            await session_ended(uid)
    for uid, cl in list(clients.items()):
        try:
            if not cl.is_connected():
                await cl.connect()
            ok = await cl.is_user_authorized()
        except Exception:
            continue                           # network trouble: try again next time
        if not ok:
            await session_ended(uid)


async def reconnect_all():
    for cl in list(clients.values()):
        try:
            await cl.disconnect()
        except Exception:
            pass
    clients.clear()
    await connect_sessions()


async def drop_user(uid):
    """Stop tasks, log the user's account out of this bot, delete session."""
    for t in db["tasks"].values():
        if t["owner"] == uid:
            t["running"] = False
    cl = clients.pop(uid, None)
    if cl:
        try:
            await cl.log_out()
        except Exception:
            pass
        try:
            await cl.disconnect()
        except Exception:
            pass
    db["sessions"].pop(str(uid), None)
    dlg_cache.pop(uid, None)
    pick_q.pop(uid, None)
    save()


async def clear_state(uid):
    st = state.pop(uid, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass


# ---------------- views ----------------
def main_view(uid):
    mine_t = [t for t in db["tasks"].values() if t["owner"] == uid]
    running = sum(1 for t in mine_t if t["running"])
    logged = uid in clients
    lines = ["🤖 <b>Auto Forward Bot</b>", "",
             f"{'🟢 Connected' if logged else '🔴 Not logged in'}  •  💎 {esc(plan_short(uid))}",
             f"📋 Tasks: {len(mine_t)}" + (f"  ({running} running)" if mine_t else "")]
    if not logged:
        lines += ["", "👉 Log in with your Telegram account to start forwarding."]
    elif not mine_t:
        lines += ["", "👉 Tap <b>➕ New Task</b> to create your first task."]
    if logged:
        btns = [[Button.inline("📋 My Tasks", b"list"), Button.inline("➕ New Task", b"new")],
                [Button.inline("📊 Stats", b"stats"), Button.inline("💎 My Plan", b"plan")],
                [Button.inline("⚙️ Settings", b"settings"), Button.inline("❓ Help", b"help")]]
    else:
        btns = [[Button.inline("🔐 Login Account", b"login")],
                [Button.inline("💎 My Plan", b"plan"), Button.inline("❓ Help", b"help")],
                [Button.inline("⚙️ Settings", b"settings")]]
    if uid == OWNER_ID:
        btns.append([Button.inline("👑 Admin", b"admin")])
    return "\n".join(lines), btns


def settings_view(uid):
    off = uid in db["noreport"]
    lines = ["⚙️ <b>Settings</b>", "",
             f"🔔 Daily report: <b>{'OFF' if off else 'ON'}</b>  (a short summary every night)",
             f"🔐 Account: {'🟢 connected' if uid in clients else '🔴 not logged in'}"]
    btns = [[Button.inline(f"🔔 Daily Report: {'OFF' if off else 'ON'}", b"rep")],
            [Button.inline("🎁 Refer & Earn", b"ref")]]
    if uid in clients:
        btns.append([Button.inline("💾 Backup My Tasks", b"backupme"), Button.inline("🚪 Logout", b"logout")])
    btns.append([Button.inline("🗑 Delete My Data", b"wipe")])
    btns.append([Button.inline("🏠 Main Menu", b"main")])
    return "\n".join(lines), btns


def help_view():
    text = (
        "❓ <b>Help</b>\n\n"
        "<b>🚀 Quick start</b>\n"
        "1. 🔐 Login Account\n"
        "2. ➕ New Task, give it a name\n"
        "3. 📥 Source &amp; Target: pick the channel to read from and the one to post to\n"
        "4. ▶️ Start\n\n"
        "<b>🧰 Inside a task</b>\n"
        "• ✂️ Filters: blacklist, whitelist, deal filter, content types\n"
        "• ✍️ Text &amp; Links: header, footer, replace, link rules\n"
        "• 🧠 Smart Tools: translate, watermark, AI rewrite (Pro)\n"
        "• ⏰ Schedule &amp; Delete: delay, schedule, auto-delete\n"
        "• ⚙️ Options: sync edits/deletes, silent, pin\n"
        "• 🎛 Presets: ready-made setups in one tap\n\n"
        "<b>📌 Good to know</b>\n"
        "• Your account must be a member of the source and allowed to post in the target.\n"
        "• To restore a task backup, send the .json file with the caption /restore.\n"
        "• /menu opens the main menu, /cancel stops any pending input."
    )
    return text, [[Button.inline("💎 My Plan", b"plan"), Button.inline("🏠 Main Menu", b"main")]]


def admin_view():
    tasks = list(db["tasks"].values())
    by = Counter(u.get("plan", "basic") for u in db["users"].values())
    pending = sum(1 for p in db["payments"].values() if p["status"] == "pending")
    lines = ["👑 <b>Admin</b>", f"Build: {BUILD}", "",
             f"Users: {len(db['users'])} • Logged in: {len(clients)} • Blocked: {len(db['banned'])}",
             "Plans: " + (", ".join(f"{PLANS[k]['name']} {v}" for k, v in by.items() if k in PLANS) or "-"),
             f"Tasks: {len(tasks)} • Running: {sum(1 for t in tasks if t['running'])}",
             f"Forwarded in total: {sum(t['stats']['forwarded'] for t in tasks)}",
             f"Pending payments: {pending} • Coupons: {len(db['coupons'])}",
             f"Cloud copy: {cloud_status()} • AI: {'ready (' + AI_MODEL + ')' if GEMINI_KEY else 'no key'}", "",
             "<code>/grant ID basic|pro|unlimited [days]</code>  give a plan (default 30 days, 0 = forever)",
             "<code>/removeuser ID</code>  block a user and delete their login",
             "<code>/adduser ID</code>  unblock a user",
             "<code>/user ID|@name</code>  user details",
             "<code>/coupon</code>  create / list coupons", "",
             "Full backup includes login sessions. To restore it, send the file with caption /restore."]
    return "\n".join(lines), [
        [Button.inline("👥 Users", b"users:0"), Button.inline("📢 Broadcast", b"bcast")],
        [Button.inline("💰 Payments", b"pays"), Button.inline("💾 Full Backup", b"backup")],
        [Button.inline("🏠 Main Menu", b"main")]]


def stats_view(uid):
    mine_t = [t for t in db["tasks"].values() if t["owner"] == uid]
    f = sum(t["stats"]["forwarded"] for t in mine_t)
    sk = sum(t["stats"]["skipped"] for t in mine_t)
    er = sum(t["stats"]["errors"] for t in mine_t)
    lines = ["📊 <b>Stats</b>", "", f"✅ Forwarded {f}  •  ⏭ Skipped {sk}  •  ❌ Errors {er}"]
    if mine_t:
        lines += ["", "<b>By task</b>"]
    for t in mine_t:
        st = t["stats"]
        q = f"  🕒 {len(t['queue'])} waiting" if t["queue"] else ""
        lines.append(f"{'🟢' if t['running'] else '🔴'} <b>{esc(t['name'])}</b>\n"
                     f"   ✅{st['forwarded']}  ⏭{st['skipped']}  ❌{st['errors']}{q}  • last: {esc(st['last'] or '-')}")
    lines += ["", "<b>Recent activity</b>"]
    recent_logs = list(logs[uid])[-12:][::-1]
    lines += [esc(x) for x in recent_logs] or ["Nothing yet since the bot started."]
    return "\n".join(lines), [[Button.inline("🔄 Refresh", b"stats"), Button.inline("🏠 Main Menu", b"main")]]


def list_view(uid):
    over = over_limit_ids(uid)
    mine_t = [(tid, t) for tid, t in db["tasks"].items() if t["owner"] == uid]
    lines = ["📋 <b>My Tasks</b>", ""]
    row, btns = [], []
    for tid, t in mine_t:
        on = t["running"]
        state_icon = "🔒" if tid in over else ("🟢" if on else "🔴")
        lines.append(f"{state_icon} <b>{esc(t['name'])}</b>\n"
                     f"   {esc(t.get('source_title') or '?')} → {esc(t.get('target_title') or '?')}  "
                     f"• ✅{t['stats']['forwarded']}")
        row.append(Button.inline(f"{state_icon} {t['name'][:18]}", f"t:{tid}".encode()))
        if len(row) == 2:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    if not mine_t:
        lines.append("No tasks yet. Tap <b>➕ New Task</b> to create one.")
    else:
        lines += ["", "🟢 running  🔴 stopped  🔒 over your plan's limit"]
    btns.append([Button.inline("➕ New Task", b"new")])
    btns.append([Button.inline("🔄 Refresh", b"list"), Button.inline("🏠 Main Menu", b"main")])
    return "\n".join(lines), btns


def dur_text(m):
    if not m:
        return "off"
    if m % 1440 == 0:
        return f"{m // 1440}d"
    return f"{m // 60}h" if m % 60 == 0 else f"{m}m"


def deal_text(s):
    parts = []
    if s["min_discount"]:
        parts.append(f"discount ≥{s['min_discount']:g}%")
    if s["max_price"]:
        parts.append(f"price ≤₹{s['max_price']:g}")
    return ", ".join(parts) or "off"


def sched_text(sc):
    if sc["mode"] == "window":
        return f"window {sc['start']}-{sc['end']}"
    if sc["mode"] == "fixed":
        return "fixed " + ", ".join(sc["times"])
    return "off"


def active_chips(t):
    s = t["settings"]
    c = []
    if s["header"]: c.append("Header")
    if s["footer"]: c.append("Footer")
    if s["replace"]: c.append(f"Replace {len(s['replace'])}")
    if s["blacklist"]: c.append(f"Blacklist {len(s['blacklist'])}")
    if s["whitelist"]: c.append(f"Whitelist {len(s['whitelist'])}")
    if s["link_rules"]: c.append(f"Link rules {len(s['link_rules'])}")
    if s["translate"]: c.append(f"Translate {s['translate']}")
    if s["watermark"]: c.append("Watermark")
    if s.get("ai_rewrite"): c.append("AI rewrite")
    if s.get("direction", "all") != "all": c.append(DIR_LABEL[s["direction"]].split(" ", 1)[1])
    if s.get("tg_link"): c.append("TG links → mine")
    if s.get("link_map"): c.append(f"Link map {len(s['link_map'])}")
    if s["delay"]: c.append(f"Delay {s['delay']:g}s")
    if s["sched"]["mode"] != "off": c.append("Schedule")
    if s["delete_after"]: c.append(f"Auto-delete {dur_text(s['delete_after'])}")
    if s["min_discount"] or s["max_price"]: c.append("Deal filter")
    if s["types"]: c.append("Content types")
    for key, label in (("no_links", "No links"), ("unshorten", "Unshorten"), ("no_usernames", "No usernames"),
                       ("dedupe", "No duplicates"), ("silent", "Silent"), ("pin", "Pin")):
        if s.get(key): c.append(label)
    return c


def _with_more(title, n):
    return esc(title) + (f"  <i>(+{n} more)</i>" if n else "")


def task_view(tid):
    t = db["tasks"][tid]
    st = t["stats"]
    owner = t["owner"]
    locked_task = tid in over_limit_ids(owner)
    status = "🔒 Locked (over your plan limit)" if locked_task else ("🟢 Running" if t["running"] else "🔴 Stopped")
    ns, nt = len(t.get("extra_sources", [])), len(t.get("extra_targets", []))
    lines = [f"📋 <b>{esc(t['name'])}</b>", status, "",
             f"📥 <b>From:</b> {_with_more(t.get('source_title') or 'not set', ns)}"
             + (" 🔒" if t.get("source_protected") else ""),
             f"📤 <b>To:</b> {_with_more(t.get('target_title') or 'not set', nt)}", "",
             f"📊 Sent {st['forwarded']} • Skipped {st['skipped']} • Errors {st['errors']}"
             + (f" • 🕒 {len(t['queue'])} waiting" if t["queue"] else "")]
    chips = active_chips(t)
    if chips:
        shown = ", ".join(chips[:9]) + (f" +{len(chips) - 9} more" if len(chips) > 9 else "")
        lines += ["", f"⚙️ <b>Active:</b> {esc(shown)}"]
    if not t.get("source") or not t.get("target"):
        lines += ["", "👉 Open <b>Source &amp; Target</b>, pick both, then press Start."]
    sec = lambda label, k: Button.inline(label, f"sec:{tid}:{k}".encode())
    btns = [
        [Button.inline("⏸ Stop" if t["running"] else "▶️ Start",
                       f"{'stop' if t['running'] else 'go'}:{tid}".encode())],
        [sec("📥 Source & Target", "st"), sec("✂️ Filters", "ft")],
        [sec("✍️ Text & Links", "tx"), sec("🧠 Smart Tools", "sm")],
        [sec("⏰ Schedule & Delete", "sc"), sec("⚙️ Options", "op")],
        [Button.inline("🎛 Presets", f"pre:{tid}".encode()), Button.inline("📜 Old Posts", f"in:{tid}:old".encode())],
        [Button.inline("✏️ Rename", f"in:{tid}:name".encode()), Button.inline("📑 Clone", f"clone:{tid}".encode()),
         Button.inline("🗑 Delete", f"del:{tid}".encode())],
        [Button.inline("◀️ Back to Tasks", b"list")],
    ]
    return "\n".join(lines), btns


SECTIONS = {"st": "📥 Source & Target", "ft": "✂️ Filters", "tx": "✍️ Text & Links", "sm": "🧠 Smart Tools",
            "sc": "⏰ Schedule & Delete", "op": "⚙️ Options"}
TOGGLE_SECTION = {"dedupe": "ft", "media": "ft", "copy": "tx", "no_links": "tx", "unshorten": "tx",
                  "no_usernames": "tx", "tg_mentions": "tx", "ai_rewrite": "sm", "sync_edit": "op", "sync_delete": "op",
                  "sync_reply": "op", "reupload": "op", "silent": "op", "pin": "op",
                  "forward_edited": "op"}
FIELD_SECTION = {"source": "st", "target": "st", "extrasource": "st", "extratarget": "st",
                 "blacklist": "ft", "whitelist": "ft", "dealfilter": "ft", "types": "ft",
                 "header": "tx", "footer": "tx", "replace": "tx", "linkrules": "tx", "tglink": "tx", "linkmap": "tx",
                 "translate": "sm", "watermark": "sm", "aiprompt": "sm",
                 "delay": "sc", "sched": "sc", "delafter": "sc"}


def back_view(tid, field):
    if field in LIST_FIELDS:
        return list_manager_view(tid, field)
    return section_view(tid, FIELD_SECTION[field]) if field in FIELD_SECTION else task_view(tid)


def back_cb(tid, field):
    if field in LIST_FIELDS:
        return f"lm:{tid}:{field}".encode()
    return f"sec:{tid}:{FIELD_SECTION[field]}".encode() if field in FIELD_SECTION else f"t:{tid}".encode()


def _names(items, limit=4):
    names = [esc(e.get("title") or e["id"]) for e in items[:limit]]
    return ", ".join(names) + (f" +{len(items) - limit} more" if len(items) > limit else "")


def section_view(tid, key):
    t = db["tasks"][tid]
    s = t["settings"]
    prem = is_premium(t["owner"])
    lock = "" if prem else "🔒 "
    labels = dict(TOGGLES)
    inl = lambda label, field: Button.inline(label, f"in:{tid}:{field}".encode())

    def toggles(keys):
        rows, row = [], []
        for k in keys:
            ico = "🔒" if (k == "ai_rewrite" and not prem) else ("✅" if s.get(k) else "❌")
            row.append(Button.inline(f"{ico} {labels[k]}", f"tg:{tid}:{k}".encode()))
            if len(row) == 2:
                rows.append(row)
                row = []
        return rows + ([row] if row else [])

    onoff = lambda v: "✅ on" if v else "❌ off"
    lines = [f"{SECTIONS[key]}  •  <b>{esc(t['name'])}</b>", ""]
    btns = []
    if key == "st":
        src = f"{esc(t.get('source_title') or 'not set')}" + (f" (<code>{t['source']}</code>)" if t.get("source") else "")
        tgt = f"{esc(t.get('target_title') or 'not set')}" + (f" (<code>{t['target']}</code>)" if t.get("target") else "")
        lines += [f"📥 <b>Source:</b> {src}" + (" 🔒 protected (media is re-uploaded)" if t.get("source_protected") else "")]
        if t.get("extra_sources"):
            lines.append(f"➕ <b>Extra sources:</b> {_names(t['extra_sources'])}")
        lines.append(f"📤 <b>Target:</b> {tgt}")
        if t.get("extra_targets"):
            lines.append(f"🎯 <b>Extra targets:</b> {_names(t['extra_targets'])}")
        if s.get("route"):
            lines.append(f"🔀 <b>Category routing:</b> {len(s['route'])} rule(s)")
        lines += ["", "Tap a button to pick from your channels, groups and bots."]
        btns += [[inl("📥 Source", "source"), inl("📤 Target", "target")],
                 [inl("➕ More Sources", "extrasource"), inl("🎯 More Targets", "extratarget")]]
        if has_removable(t):
            btns.append([Button.inline("🗑 Remove…", f"rm:{tid}".encode())])
        btns.append([Button.inline(f"🔀 Category routing: {len(s['route'])} rule(s)", f"lm:{tid}:route".encode())])
    elif key == "ft":
        lines += [f"🚫 Blacklist: {len(s['blacklist'])} • ✅ Whitelist: {len(s['whitelist'])}",
                  f"💰 Deal filter: {esc(deal_text(s))}",
                  f"🧩 Content types: {esc(', '.join(s['types']) or 'all')}",
                  f"♻️ Duplicate filter: {onoff(s['dedupe'])} • 🖼 Media: {onoff(s['media'])}",
                  f"📨 Messages: <b>{DIR_LABEL[s.get('direction', 'all')]}</b>"
                  + ("  (messages you send yourself are ignored)" if s.get("direction") == "incoming" else
                     "  (only messages you send yourself)" if s.get("direction") == "outgoing" else ""),
                  "", "Posts that don't pass are skipped."]
        btns += [[inl("🚫 Blacklist", "blacklist"), inl("✅ Whitelist", "whitelist")],
                 [inl("💰 Deal Filter", "dealfilter"), inl("🧩 Content Types", "types")],
                 [Button.inline(DIR_LABEL[s.get("direction", "all")] + "  (tap to change)", f"dir:{tid}".encode())]] \
            + toggles(["dedupe", "media"])
    elif key == "tx":
        lines += [f"📝 Header: {onoff(s['header'])} • 📄 Footer: {onoff(s['footer'])}",
                  f"🧾 Info Template: {onoff(s.get('template'))}",
                  f"✏️ Replacements: {len(s['replace'])} • 🔗 Link rules: {len(s['link_rules'])}" + ("" if prem else "  🔒 Pro"),
                  f"🔁 Telegram links → {esc(s['tg_link']) if s.get('tg_link') else 'off'}" + ("" if prem else "  🔒 Pro"),
                  f"🗺 Link map: {len(s.get('link_map', []))} rule(s)" + ("" if prem else "  🔒 Pro"),
                  f"👤 Hide 'Forwarded from': {onoff(s['copy'])} • 🚫 No links: {onoff(s['no_links'])}",
                  f"🔓 Unshorten links: {onoff(s['unshorten'])} • 🙈 No usernames: {onoff(s['no_usernames'])}"]
        btns += [[inl("📝 Header", "header"), inl("📄 Footer", "footer")],
                 [inl(f"🧾 Info Template ({'on' if s['template'] else 'off'})", "template")],
                 [inl("✏️ Replace Text", "replace"), inl(f"{lock}🔗 Link Rules", "linkrules")],
                 [inl(f"{lock}🔁 Replace TG Links", "tglink"), inl(f"{lock}🗺 Link Map", "linkmap")]] \
            + toggles(["copy", "no_links", "unshorten", "no_usernames", "tg_mentions"])
    elif key == "sm":
        wm = esc(s["watermark"]) if s["watermark"] else "off"
        if s["watermark"] and not PIL_OK:
            wm += " ⚠️ Pillow not installed"
        lines += [f"🌐 Translate: {esc(s['translate']) if s['translate'] else 'off'}",
                  f"🖼 Watermark: {wm}",
                  f"🤖 AI Rewrite: {onoff(s.get('ai_rewrite'))}" + (" (custom prompt)" if s.get("ai_prompt") else "")]
        if not prem:
            lines += ["", "🔒 These tools are on the Pro and Unlimited plans. Tap 💎 My Plan to upgrade."]
        btns += [[inl(f"{lock}🌐 Translate", "translate"), inl(f"{lock}🖼 Watermark", "watermark")]] \
            + toggles(["ai_rewrite"]) + [[inl(f"{lock}🤖 AI Prompt", "aiprompt")]]
    elif key == "sc":
        lines += [f"⏱ Delay: {s['delay']:g}s • 🗓 Schedule: {esc(sched_text(s['sched']))}",
                  f"🕒 Auto-delete: {dur_text(s['delete_after'])} • 📥 Waiting in queue: {len(t['queue'])}"]
        btns += [[inl("⏱ Delay", "delay"), inl("🗓 Schedule", "sched")],
                 [inl("🕒 Auto-Delete", "delafter"), Button.inline("🧹 Clear Queue", f"clq:{tid}".encode())]]
    else:  # op
        lines += [f"✏️ Sync edits: {onoff(s['sync_edit'])} • 🗑 Sync deletes: {onoff(s['sync_delete'])}",
                  f"↩️ Sync replies: {onoff(s['sync_reply'])} • 📤 Re-upload restricted: {onoff(s['reupload'])}",
                  f"🔕 Silent: {onoff(s['silent'])} • 📌 Pin posts: {onoff(s['pin'])}",
                  f"📝 Forward edited posts: {onoff(s.get('forward_edited'))}"]
        btns += toggles(["sync_edit", "sync_delete", "sync_reply", "reupload", "silent", "pin", "forward_edited"])
    btns.append([Button.inline("◀️ Back to Task", f"t:{tid}".encode())])
    return "\n".join(lines), btns


# ---------------- colored buttons ----------------
# Telegram lets bots color inline buttons (blue / green / red). Telethon cannot send that yet,
# so after a message with buttons is sent/edited, the same buttons are re-applied once through
# the plain Bot API with a "style". If anything fails the buttons simply stay uncolored.
COLOR_BUTTONS = True
_color = {"fails": 0, "locks": {}, "seq": {}}
DANGER_HEADS = {"wipe", "wipec", "logout", "logoutc", "del", "delc", "oldstop", "stop"}
SUCCESS_HEADS = {"go", "bcyes", "chk", "loginok", "buy"}


def style_for(text, data):
    """'primary' (blue), 'success' (green), 'danger' (red) or None (normal)."""
    if data.startswith("pay:no"):
        return "danger"
    if data.startswith("pay:ok"):
        return "success"
    head = data.split(":")[0]
    if head == "tg":                                   # feature switches: green when ON
        return "success" if text.startswith("✅") else None
    if head in DANGER_HEADS:
        return "danger"
    if head in SUCCESS_HEADS:
        return "success"
    if head == "plans" and text.startswith("⬆️ Upgrade"):
        return "primary"
    if head in ("new", "login") or (head == "list" and text.startswith("📋")):
        return "primary"
    if data == "\0url" and "Join" in text:
        return "primary"
    return None


def kb_json(buttons):
    """Telethon buttons -> Bot API inline_keyboard JSON (None if nothing is colored/unknown)."""
    if not buttons:
        return None
    rows = buttons if isinstance(buttons[0], (list, tuple)) else [buttons]
    out, styled = [], False
    for row in rows:
        r = []
        for b in row:
            text = getattr(b, "text", None)
            if text is None:
                return None
            if hasattr(b, "data"):
                d = b.data.decode() if isinstance(b.data, (bytes, bytearray)) else str(b.data)
                item, st = {"text": text, "callback_data": d}, style_for(text, d)
            elif hasattr(b, "url"):
                item, st = {"text": text, "url": b.url}, style_for(text, "\0url")
            else:
                return None
            if st:
                item["style"] = st
                styled = True
            r.append(item)
        out.append(r)
    return {"inline_keyboard": out} if styled else None


def _bot_api(method, payload):
    req = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "description": str(e)}
    except Exception as e:
        return {"ok": False, "description": repr(e)}


async def apply_style(msg, kb, seq):
    key = (msg.chat_id, msg.id)
    lock = _color["locks"].setdefault(key, asyncio.Lock())
    async with lock:
        if _color["seq"].get(key) != seq:              # a newer edit came in: skip the stale one
            return
        res = await asyncio.get_running_loop().run_in_executor(
            None, _bot_api, "editMessageReplyMarkup",
            {"chat_id": msg.chat_id, "message_id": msg.id, "reply_markup": kb})
    if len(_color["locks"]) > 1000:
        _color["locks"].clear()
        _color["seq"].clear()
    if res.get("ok") or "not modified" in str(res.get("description", "")):
        _color["fails"] = 0
        return
    _color["fails"] += 1
    print(f"Colored buttons: {res.get('description')}")
    if _color["fails"] >= 5:
        print("Colored buttons turned off after repeated errors (buttons still work).")


def install_button_colors(client):
    def wrap(orig):
        async def wrapper(*a, **k):
            msg = await orig(*a, **k)
            try:
                if COLOR_BUTTONS and _color["fails"] < 5 and k.get("buttons") and hasattr(msg, "chat_id"):
                    kb = kb_json(k["buttons"])
                    if kb:
                        key = (msg.chat_id, msg.id)
                        _color["seq"][key] = _color["seq"].get(key, 0) + 1
                        asyncio.create_task(apply_style(msg, kb, _color["seq"][key]))
            except Exception as e:
                print(f"Colored buttons error: {e!r}")
            return msg
        return wrapper
    for name in ("send_message", "edit_message", "send_file"):
        setattr(client, name, wrap(getattr(client, name)))


async def show(event, view):
    text, btns = view
    try:
        # an EMPTY list is rejected by Telegram (ReplyMarkupInvalidError); None removes the buttons
        await event.edit(text, buttons=btns or None, parse_mode="html")
    except MessageNotModifiedError:
        pass


def mine(uid, tid):
    t = db["tasks"].get(tid)
    return t if t and t["owner"] == uid else None


TASK_CMDS = ("t", "sec", "rm", "rmx", "dir", "ina", "lm", "lmx", "lmc", "lmcy", "tg", "go", "stop", "in", "inm", "pre", "prea", "pk", "pkr", "pks", "ps", "del", "delc", "clq", "clone", "oldstop")
LOGIN_CMDS = ("list", "new") + TASK_CMDS


# ---------------- chat picker (Source / Target) ----------------
PICK_FIELDS = ("source", "target", "extratarget", "extrasource")
PICK_PER_PAGE = 8
KIND_ICON = {"channel": "📢", "group": "👥", "bot": "🤖"}
dlg_cache = {}          # uid -> (time, [chat dicts])
pick_q = {}             # uid -> current search text


def dialog_kind(ent):
    if isinstance(ent, Channel):
        return "channel" if getattr(ent, "broadcast", False) else "group"
    if isinstance(ent, Chat):
        return None if getattr(ent, "deactivated", False) else "group"
    if isinstance(ent, User):
        return "bot" if getattr(ent, "bot", False) else None
    return None


def can_post(ent):
    """Best guess whether the account may post in this chat."""
    if isinstance(ent, User) or getattr(ent, "creator", False):
        return True
    ar = getattr(ent, "admin_rights", None)
    if isinstance(ent, Channel) and getattr(ent, "broadcast", False):
        return bool(ar and getattr(ar, "post_messages", False))
    if ar:
        return True
    dbr = getattr(ent, "default_banned_rights", None)
    return not (dbr and getattr(dbr, "send_messages", False))


async def get_dialog_list(uid, cl, force=False):
    c = dlg_cache.get(uid)
    if c and not force and time.time() - c[0] < 120:
        return c[1]
    out = []
    async for d in cl.iter_dialogs():
        ent = d.entity
        kind = dialog_kind(ent)
        if not kind:
            continue
        out.append({"id": utils.get_peer_id(ent), "name": utils.get_display_name(ent) or "?", "kind": kind,
                    "prot": bool(getattr(ent, "noforwards", False)), "post": can_post(ent),
                    "un": getattr(ent, "username", "") or ""})
    dlg_cache[uid] = (time.time(), out)
    return out


def picker_view(uid, tid, field, kind, page, items):
    t = db["tasks"][tid]
    q = pick_q.get(uid, "")
    ql = q.lower().lstrip("@")
    sel = [x for x in items if (kind == "all" or x["kind"] == kind)
           and (not q or ql in x["name"].lower() or ql in x["un"].lower() or q in str(x["id"]))]
    pages = max(1, (len(sel) + PICK_PER_PAGE - 1) // PICK_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = sel[page * PICK_PER_PAGE:(page + 1) * PICK_PER_PAGE]
    if field == "extratarget":
        cur = {e["id"] for e in t.get("extra_targets", [])} | {t.get("target")}
    elif field == "extrasource":
        cur = {e["id"] for e in t.get("extra_sources", [])} | {t.get("source")}
    else:
        cur = {t.get(field)}
    title = {"source": "📥 Choose the SOURCE", "target": "📤 Choose the TARGET",
             "extratarget": "🎯 Add an EXTRA TARGET", "extrasource": "➕ Add an EXTRA SOURCE"}[field]
    lines = [f"<b>{title}</b>",
             f"{len(sel)} chat(s)" + (f" matching “{esc(q)}”" if q else "") + f" • page {page + 1}/{pages}", ""]
    for i, x in enumerate(chunk, 1):
        un = f" @{x['un']}" if x["un"] else ""
        warn = " ⛔ can't post" if field in ("target", "extratarget") and not x["post"] else ""
        lines.append(f"{i}. {KIND_ICON[x['kind']]} {'✅ ' if x['id'] in cur else ''}<b>{esc(x['name'])}</b>{esc(un)}\n"
                     f"    ID: <code>{x['id']}</code>{warn}")
    if not chunk:
        lines.append("Nothing found. Try 🔄 Refresh or ✍️ Enter manually.")
    lines += ["", "Tap a name below to select it."]
    keep = "1" if q else "0"
    base = f"{tid}:{field}"
    btns = [[Button.inline(f"{KIND_ICON[x['kind']]} {x['name'][:24]}", f"ps:{base}:{x['id']}".encode())]
            for x in chunk]
    btns.append([Button.inline(("• " if kind == k else "") + lab, f"pk:{base}:{k}:0:{keep}".encode())
                 for k, lab in (("all", "All"), ("channel", "📢"), ("group", "👥"), ("bot", "🤖"))])
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ Prev", f"pk:{base}:{kind}:{page - 1}:{keep}".encode()))
    if page < pages - 1:
        nav.append(Button.inline("Next ▶️", f"pk:{base}:{kind}:{page + 1}:{keep}".encode()))
    if nav:
        btns.append(nav)
    row = [Button.inline("🔎 Search", f"pks:{base}:{kind}".encode()),
           Button.inline("🔄 Refresh", f"pkr:{base}:{kind}".encode())]
    if q:
        row.append(Button.inline("✖ Clear", f"pk:{base}:{kind}:0:0".encode()))
    btns.append(row)
    btns.append([Button.inline("✍️ Enter manually", f"inm:{base}".encode())])
    btns.append([Button.inline("◀️ Back", f"t:{tid}".encode())])
    return "\n".join(lines), btns


async def open_picker(event, uid, tid, field, kind="all", page=0, keepq=False, force=False):
    if not keepq:
        pick_q.pop(uid, None)
    cl = clients[uid]
    c = dlg_cache.get(uid)
    if force or not c or time.time() - c[0] >= 120:
        await show(event, ("⏳ Loading your channels, groups and bots…", []))
    try:
        items = await asyncio.wait_for(get_dialog_list(uid, cl, force), 90)
    except Exception as e:
        why = str(e) or ("it took too long" if isinstance(e, asyncio.TimeoutError) else type(e).__name__)
        print(f"Picker could not load chats for {uid}: {e!r}")
        return await show(event, (f"❌ Couldn't load your chats: {esc(why)}",
                                  [[Button.inline("✍️ Enter manually", f"inm:{tid}:{field}".encode())],
                                   [Button.inline("◀️ Back", f"t:{tid}".encode())]]))
    await show(event, picker_view(uid, tid, field, kind, page, items))


async def pick_chat(event, uid, tid, field, cid):
    """A chat was tapped in the picker. Returns the notice text, or None if it was refused (alert shown)."""
    items = (dlg_cache.get(uid) or (0, []))[1]
    x = next((i for i in items if i["id"] == cid), None)
    if x is None:
        items = await get_dialog_list(uid, clients[uid], True)
        x = next((i for i in items if i["id"] == cid), None)
    if x is None:
        await event.answer("Chat not found. Tap 🔄 Refresh.", alert=True)
        return None
    t = db["tasks"][tid]
    if field in ("target", "extratarget") and not x["post"]:
        await event.answer("⛔ Your account can't post in this chat.", alert=True)
        return None
    conflict = loop_conflict(t, field, cid)
    if conflict:
        await event.answer(conflict, alert=True)
        return None
    if field == "extrasource":
        extras = t.setdefault("extra_sources", [])
        lim = plan_info(uid)["xsrc"]
        if cid == t.get("source") or any(e["id"] == cid for e in extras):
            await event.answer("Already added.", alert=True)
            return None
        if cid == t.get("target") or any(e["id"] == cid for e in t.get("extra_targets", [])):
            await event.answer("That chat is already a target of this task.", alert=True)
            return None
        if lim is not None and len(extras) >= lim:
            await event.answer(f"🔒 Your plan allows {lim} extra source(s) per task.", alert=True)
            return None
        extras.append({"id": cid, "title": x["name"]})
        what = "Extra source added"
    elif field == "extratarget":
        extras = t.setdefault("extra_targets", [])
        lim = plan_info(uid)["extra"]
        if cid == t.get("target") or any(e["id"] == cid for e in extras):
            await event.answer("Already added.", alert=True)
            return None
        if lim is not None and len(extras) >= lim:
            await event.answer(f"🔒 Your plan allows {lim} extra target(s) per task.", alert=True)
            return None
        extras.append({"id": cid, "title": x["name"]})
        what = "Extra target added"
    else:
        t[field] = cid
        t[field + "_title"] = x["name"]
        t[field + "_protected"] = x["prot"]
        what = "Source set" if field == "source" else "Target set"
    save()
    return f"✅ {what}: <b>{esc(x['name'])}</b> (<code>{cid}</code>)\n\n"


# ---------------- loop guard + removing sources/targets ----------------
def loop_conflict(t, field, cid):
    """A chat must never be both a source and a target of the same task (posts would repeat forever)."""
    srcs = {t.get("source")} | {e["id"] for e in t.get("extra_sources", [])}
    tgts = {t.get("target")} | {e["id"] for e in t.get("extra_targets", [])}
    if field in ("source", "extrasource") and cid in tgts:
        return ("That chat is already a target of this task. A chat can't be both source and target "
                "(it would repeat posts forever).")
    if field in ("target", "extratarget") and cid in srcs:
        return ("That chat is already a source of this task. A chat can't be both source and target "
                "(it would repeat posts forever).")
    return None


def remove_view(tid, note=""):
    t = db["tasks"][tid]
    btns, lines = [], [f"🗑 <b>Remove</b>  •  <b>{esc(t['name'])}</b>", "", note + "Tap what you want to remove."]
    if t.get("source"):
        btns.append([Button.inline(f"❌ 📥 {(t.get('source_title') or str(t['source']))[:26]}", f"rmx:{tid}:src".encode())])
    for e in t.get("extra_sources", []):
        btns.append([Button.inline(f"❌ ➕ {str(e.get('title') or e['id'])[:26]}", f"rmx:{tid}:xs:{e['id']}".encode())])
    if t.get("target"):
        btns.append([Button.inline(f"❌ 📤 {(t.get('target_title') or str(t['target']))[:26]}", f"rmx:{tid}:tgt".encode())])
    for e in t.get("extra_targets", []):
        btns.append([Button.inline(f"❌ 🎯 {str(e.get('title') or e['id'])[:26]}", f"rmx:{tid}:xt:{e['id']}".encode())])
    if len(t.get("extra_sources", [])) > 1:
        btns.append([Button.inline("❌ All extra sources", f"rmx:{tid}:allxs".encode())])
    if len(t.get("extra_targets", [])) > 1:
        btns.append([Button.inline("❌ All extra targets", f"rmx:{tid}:allxt".encode())])
    if not btns:
        lines[-1] = note + "Nothing to remove."
    lines += ["", "📥 main source  •  ➕ extra source  •  📤 main target  •  🎯 extra target"]
    btns.append([Button.inline("◀️ Back", f"sec:{tid}:st".encode())])
    return "\n".join(lines), btns


def has_removable(t):
    return bool(t.get("source") or t.get("target") or t.get("extra_sources") or t.get("extra_targets"))


def do_remove(t, kind, cid=None):
    """Remove a source/target. Returns (notice, stopped). Posts waiting from a removed source are dropped."""
    def purge(ids, drop_old=False):
        # old-format entries (plain id lists) belong to the main source: kept unless the main source is removed
        t["queue"] = [q for q in t["queue"] if (not drop_old if not isinstance(q, dict) else q.get("c") not in ids)]
    note = "✅ Removed.\n\n"
    if kind == "src":
        old, name = t.get("source"), t.get("source_title") or str(t.get("source"))
        ex = t.setdefault("extra_sources", [])
        if ex:
            nxt = ex.pop(0)
            t["source"], t["source_title"], t["source_protected"] = nxt["id"], nxt.get("title", ""), False
            note = f"✅ Removed <b>{esc(name)}</b>. <b>{esc(nxt.get('title') or nxt['id'])}</b> is now the main source.\n\n"
        else:
            t["source"], t["source_title"], t["source_protected"] = None, "", False
            note = f"✅ Removed the source <b>{esc(name)}</b>.\n\n"
        purge({old}, drop_old=True)
    elif kind == "tgt":
        name = t.get("target_title") or str(t.get("target"))
        ex = t.setdefault("extra_targets", [])
        if ex:
            nxt = ex.pop(0)
            t["target"], t["target_title"], t["target_protected"] = nxt["id"], nxt.get("title", ""), False
            note = f"✅ Removed <b>{esc(name)}</b>. <b>{esc(nxt.get('title') or nxt['id'])}</b> is now the main target.\n\n"
        else:
            t["target"], t["target_title"], t["target_protected"] = None, "", False
            note = f"✅ Removed the target <b>{esc(name)}</b>.\n\n"
    elif kind == "xs":
        t["extra_sources"] = [e for e in t.get("extra_sources", []) if e["id"] != cid]
        purge({cid})
    elif kind == "xt":
        t["extra_targets"] = [e for e in t.get("extra_targets", []) if e["id"] != cid]
    elif kind == "allxs":
        purge({e["id"] for e in t.get("extra_sources", [])})
        t["extra_sources"] = []
        note = "✅ All extra sources removed.\n\n"
    elif kind == "allxt":
        t["extra_targets"] = []
        note = "✅ All extra targets removed.\n\n"
    stopped = False
    if t["running"] and (not t.get("source") or not t.get("target")):
        t["running"], stopped = False, True
        note += "⏸ The task was stopped because it needs a source and a target.\n\n"
    return note, stopped


# ---------------- presets ----------------
PRESETS = {
    "loot": {"label": "🛍 Loot Deals",
             "desc": "Hide 'Forwarded from', remove @usernames, unshorten links, skip duplicates and invite-link ads.",
             "set": {"copy": True, "media": True, "no_usernames": True, "unshorten": True, "dedupe": True,
                     "no_links": False, "sync_edit": True, "sync_delete": True, "sync_reply": True},
             "blacklist": [r"re:t\.me/\+", "re:joinchat"]},
    "clean": {"label": "🧹 Clean Copy",
              "desc": "Remove all links and @usernames, skip duplicates, hide 'Forwarded from'.",
              "set": {"copy": True, "media": True, "no_links": True, "no_usernames": True, "dedupe": True}},
    "mirror": {"label": "🪞 Exact Mirror",
               "desc": "Copy everything as it is and keep edits, deletes and replies in sync.",
               "set": {"copy": True, "media": True, "no_links": False, "no_usernames": False, "unshorten": False,
                       "dedupe": False, "sync_edit": True, "sync_delete": True, "sync_reply": True}},
    "news": {"label": "📰 News Feed",
             "desc": "Hide 'Forwarded from', skip duplicates, keep links, sync edits and deletes.",
             "set": {"copy": True, "media": True, "no_links": False, "no_usernames": False, "dedupe": True,
                     "sync_edit": True, "sync_delete": True}},
}


def presets_view(tid):
    lines = ["🎛 <b>Presets</b>", "", "One tap sets up the switches below. Your header, footer, replacements "
             "and schedule are not touched.", ""]
    for p in PRESETS.values():
        lines.append(f"<b>{p['label']}</b>\n   {esc(p['desc'])}")
    btns = [[Button.inline(p["label"], f"prea:{tid}:{k}".encode())] for k, p in PRESETS.items()]
    btns.append([Button.inline("◀️ Back", f"t:{tid}".encode())])
    return "\n".join(lines), btns


def apply_preset(t, key):
    p = PRESETS[key]
    t["settings"].update(p["set"])
    for w in p.get("blacklist", []):
        if w not in t["settings"]["blacklist"]:
            t["settings"]["blacklist"].append(w)
    return p["label"]


# ---------------- daily report ----------------
async def send_reports():
    """One summary per user (changes since the previous report). The first run only sets the baseline."""
    base = db["report_base"]
    per_user = defaultdict(list)
    for tid, t in db["tasks"].items():
        st = t["stats"]
        cur = [st["forwarded"], st["skipped"], st["errors"]]
        old = base.get(tid)
        base[tid] = cur
        if old is None:
            continue
        per_user[t["owner"]].append((t["name"], [max(0, a - b) for a, b in zip(cur, old)], len(t["queue"])))
    for gone in [k for k in base if k not in db["tasks"]]:
        base.pop(gone)
    save()
    for uid, rows in per_user.items():
        if uid in db["noreport"]:
            continue
        tf, ts, te = (sum(r[1][i] for r in rows) for i in range(3))
        if not (tf or ts or te):
            continue
        lines = [f"📊 <b>Daily report</b> – {now():%d %b}", "",
                 f"✅ Forwarded: {tf} • ⏭ Skipped: {ts} • ❌ Errors: {te}", ""]
        for name, d, q in rows:
            if any(d):
                lines.append(f"• {esc(name)}: ✅{d[0]} ⏭{d[1]} ❌{d[2]}" + (f" • queue {q}" if q else ""))
        if te:
            lines.append("\n⚠️ Some posts failed. Open 📊 Stats & Logs to see why.")
        await tell(uid, "\n".join(lines), [[Button.inline("📊 Stats & Logs", b"stats")],
                                           [Button.inline("🔕 Turn off report", b"rep")]])
        await asyncio.sleep(0.05)


# ---------------- list manager (see, add and remove single items) ----------------
LIST_FIELDS = {"blacklist": "blacklist", "whitelist": "whitelist", "replace": "replace",
               "route": "route", "linkrules": "link_rules", "linkmap": "link_map"}
LIST_TITLE = {"blacklist": "🚫 Blacklist", "whitelist": "✅ Whitelist", "replace": "✏️ Replace Text",
              "route": "🔀 Category routing", "linkrules": "🔗 Link Rules", "linkmap": "🗺 Link Map"}
LIST_PER_PAGE = 8


def list_items(s, field):
    data = s.get(LIST_FIELDS[field]) or ([] if field != "replace" else {})
    if field == "route":
        return [f"{kw} => {cid}" for kw, cid in data]
    return [f"{k} => {v}" for k, v in data.items()] if isinstance(data, dict) else list(data)


def _tag(text):
    return format(zlib.crc32(text.encode("utf-8")) & 0xFFFF, "x")


def list_manager_view(tid, field, page=0, note=""):
    t = db["tasks"][tid]
    items = list_items(t["settings"], field)
    pages = max(1, (len(items) + LIST_PER_PAGE - 1) // LIST_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    start = page * LIST_PER_PAGE
    chunk = items[start:start + LIST_PER_PAGE]
    lines = [f"{LIST_TITLE[field]}  •  <b>{esc(t['name'])}</b>", "",
             note + (f"{len(items)} item(s)" + (f" • page {page + 1}/{pages}" if pages > 1 else "") + ":" if items
                     else "Nothing added yet. Tap ➕ Add.")]
    for i, it in enumerate(chunk, start + 1):
        lines.append(f"{i}. <code>{esc(it[:90])}</code>")
    if items:
        lines += ["", "Tap ❌ to remove just that one."]
    base = f"{tid}:{field}"
    btns = [[Button.inline(f"❌ {i}. {it[:24]}", f"lmx:{base}:{i - 1}:{_tag(it)}".encode())]
            for i, it in enumerate(chunk, start + 1)]
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ Prev", f"lm:{base}:{page - 1}".encode()))
    if page < pages - 1:
        nav.append(Button.inline("Next ▶️", f"lm:{base}:{page + 1}".encode()))
    if nav:
        btns.append(nav)
    btns.append([Button.inline("➕ Add", f"ina:{base}".encode()), Button.inline("🔁 Replace all", f"inm:{base}".encode())])
    if items:
        btns.append([Button.inline("🗑 Clear all", f"lmc:{base}".encode())])
    btns.append([Button.inline("◀️ Back", back_cb(tid, field))])
    return "\n".join(lines), btns


def list_remove(t, field, idx, tag):
    """Remove item number idx (0-based) if it is still the item the user saw. Returns the removed text or None."""
    s = t["settings"]
    items = list_items(s, field)
    if not (0 <= idx < len(items)) or _tag(items[idx]) != tag:
        return None
    data = s[LIST_FIELDS[field]]
    if isinstance(data, dict):
        data.pop(list(data.keys())[idx])
    else:
        data.pop(idx)
    return items[idx]


def apply_add(t, field, raw, fmt):
    """Same parsing/validation as 'Replace all', but the new items are ADDED to the existing ones."""
    s = t["settings"]
    key = LIST_FIELDS[field]
    cur = s.get(key)
    tmp = {"settings": {**s, key: ({} if isinstance(cur, dict) else [])}}
    apply_input(tmp, field, raw, fmt)
    new = tmp["settings"][key]
    before = len(cur)
    if isinstance(cur, dict):
        cur.update(new)
    else:
        s[key] = list(cur) + [x for x in new if x not in cur]
    added = len(s[key]) - before
    if added <= 0 and not (isinstance(cur, dict) and new):
        raise ValueError("Nothing new to add (those are already in the list).")
    return added


def export_tasks(uid):
    items = []
    for t in db["tasks"].values():
        if t["owner"] == uid:
            items.append({
                "name": t["name"], "source": t.get("source"), "source_title": t.get("source_title", ""),
                "target": t.get("target"), "target_title": t.get("target_title", ""),
                "settings": t["settings"], "source_protected": t.get("source_protected", False),
                "extra_targets": t.get("extra_targets", []), "extra_sources": t.get("extra_sources", [])})
    return {"type": "tasks_export", "tasks": items}


def as_file(obj, name):
    bio = io.BytesIO(json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))
    bio.name = name
    return bio


# ---------------- button handler ----------------
_err_seen = {}
_STOP = getattr(events, "StopPropagation", None)


def safe_handler(handler):
    """Never fail silently: show the user a short error and send the details to the owner."""
    async def wrapper(event):
        try:
            return await handler(event)
        except Exception as e:
            if _STOP is not None and isinstance(e, _STOP):
                raise
            import traceback
            tb = traceback.format_exc()
            print(tb)
            msg = f"{type(e).__name__}: {e}"[:160]
            try:
                if getattr(event, "data", None) is not None:       # a button press
                    await event.answer(f"⚠️ Something went wrong: {msg}", alert=True)
                else:
                    await event.respond(f"⚠️ Something went wrong.\n<code>{esc(msg)}</code>\n"
                                        "Please try again, or send /start.", parse_mode="html")
            except Exception:
                pass
            key = msg[:80]
            if time.time() - _err_seen.get(key, 0) > 300:          # tell the owner, at most every 5 min per error
                _err_seen[key] = time.time()
                try:
                    await bot.send_message(OWNER_ID, f"⚠️ <b>Bot error</b> (user <code>{getattr(event, 'sender_id', '?')}</code>)\n"
                                                     f"<code>{esc(tb[-700:])}</code>", parse_mode="html")
                except Exception:
                    pass
    wrapper.__name__ = handler.__name__
    return wrapper


@bot.on(events.CallbackQuery)
@safe_handler
async def on_cb(event):
    uid = event.sender_id
    if not allowed(uid):
        return await event.answer("You are blocked from this bot", alert=True)
    if not rate_ok(uid):
        return await event.answer("⏳ Too many taps. Slow down a little.")
    parts = event.data.decode().split(":")
    cmd = parts[0]
    tid = parts[1] if len(parts) > 1 else None
    if cmd == "resend":
        return await do_resend(event, uid)
    await clear_state(uid)
    if not await is_member(uid):
        if cmd == "chk":
            return await event.answer("You haven't joined yet. Join the channel, then tap again.", alert=True)
        await event.answer("Join the channel first", alert=True)
        return await show(event, join_view())
    is_new = await reg(event, uid)

    if cmd in TASK_CMDS and not mine(uid, tid):
        await event.answer("Task not found", alert=True)
        return await show(event, list_view(uid))
    if cmd in ("admin", "backup", "bcast", "bcyes", "bcno", "pay", "users", "pays", "payshow") and uid != OWNER_ID:
        return await event.answer("Admin only", alert=True)
    if cmd in LOGIN_CMDS and uid not in clients:
        await event.answer("Log in first", alert=True)
        return await show(event, main_view(uid))

    if cmd == "chk":
        text, btns = main_view(uid)
        await show(event, ((welcome_text() if is_new else "✅ Verified!\n\n") + text, btns))
    elif cmd == "main":
        await show(event, main_view(uid))
    elif cmd == "help":
        await show(event, help_view())
    elif cmd == "plan":
        await show(event, plan_view(uid))
    elif cmd == "plans":
        await show(event, plans_view())
    elif cmd == "ref":
        await show(event, ref_view(uid))
    elif cmd == "cpn":
        state[uid] = {"field": "coupon"}
        await show(event, ("🎟 Send your coupon code.", [[Button.inline("Cancel", b"plans")]]))
    elif cmd == "pays":
        await show(event, pays_view())
    elif cmd == "payshow":
        p = db["payments"].get(parts[1] if len(parts) > 1 else "")
        if not p or p["status"] != "pending":
            return await event.answer("Already handled", alert=True)
        cap = pay_caption(parts[1], p)
        try:
            if p.get("fid"):
                await bot.send_file(OWNER_ID, p["fid"], caption=cap, buttons=pay_buttons(parts[1]), parse_mode="html")
            else:
                raise ValueError("no saved screenshot")
        except Exception:
            await bot.send_message(OWNER_ID, cap + "\n\n(Screenshot not available here, check the earlier message.)",
                                   buttons=pay_buttons(parts[1]), parse_mode="html")
        await event.answer("Sent below ⬇️")
    elif cmd == "users":
        await show(event, users_view(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0))
    elif cmd == "buy":
        key = parts[1] if len(parts) > 1 else ""
        if key not in buyable_plans():
            return await event.answer("This plan can't be bought", alert=True)
        if any(p["uid"] == uid and p["status"] == "pending" for p in db["payments"].values()):
            return await event.answer("You already have a payment waiting for approval.", alert=True)
        p = PLANS[key]
        amount, dcode = price_for(uid, key)
        state[uid] = {"field": "paysshot", "plan": key, "amount": amount, "disc": dcode}
        price_txt = (f"<s>₹{p['price']}</s> ₹{amount} (coupon {esc(dcode)})" if dcode else f"₹{amount}")
        await show(event, (
            f"💳 <b>Pay for {p['name']} – {price_txt} / {PLAN_DAYS} days</b>\n\n"
            f"1. Pay <b>₹{amount}</b> by UPI to:\n<code>{esc(UPI_ID)}</code>\n"
            "2. Take a screenshot of the successful payment.\n"
            "3. Send the screenshot here as a photo.\n\n"
            "I'll check it and activate your plan. Send /cancel to stop.",
            [[Button.inline("Cancel", b"plans")]]))
    elif cmd == "pay":
        act, pid = parts[1], parts[2]
        p = db["payments"].get(pid)
        if not p or p["status"] != "pending":
            return await event.answer("Already handled", alert=True)
        pl = PLANS[p["plan"]]
        if act == "ok":
            exp = activate_plan(p["uid"], p["plan"], PLAN_DAYS)
            p["status"] = "approved"
            p["done"] = time.time()
            if p.get("disc"):
                (user_rec(p["uid"]) or {}).pop("discount", None)      # the discount was used
            save()
            await tell(p["uid"], f"✅ <b>Payment approved!</b>\n\nYour <b>{pl['name']}</b> plan is active until "
                                 f"{fmt_ts(exp)}.", [[Button.inline("🏠 Main Menu", b"main")]])
            mark = "✅ Approved"
        else:
            p["status"] = "rejected"
            p["done"] = time.time()
            save()
            await tell(p["uid"], "❌ <b>Your payment couldn't be verified.</b>\n\nIf you already paid, tap "
                                 "⬆️ Upgrade and send a clear screenshot again, or contact the admin.",
                       [[Button.inline("⬆️ Upgrade", b"plans")]])
            mark = "❌ Rejected"
        await show(event, (f"💳 <b>Payment #{pid}</b> • user <code>{p['uid']}</code> • {pl['name']} "
                           f"₹{p.get('amount', pl['price'])}\n{mark}", []))
    elif cmd == "stats":
        await show(event, stats_view(uid))
    elif cmd == "backupme":
        await bot.send_file(uid, as_file(export_tasks(uid), "my_tasks_backup.json"),
                            caption="💾 Your tasks backup. To restore, send this file back with the caption /restore")
        return await event.answer("Backup sent")
    elif cmd == "backup":
        save()
        await bot.send_file(uid, DATA_FILE,
                            caption="💾 Full backup. ⚠️ Logins inside are encrypted, but keep it private and also "
                                    "keep a copy of secret.key (needed to restore them). "
                                    "Restore by sending it back with the caption /restore")
        return await event.answer("Backup sent")
    elif cmd == "admin":
        await show(event, admin_view())
    elif cmd == "bcast":
        state[uid] = {"field": "broadcast"}
        await show(event, (f"📢 {PROMPTS['broadcast']}", [[Button.inline("Cancel", b"admin")]]))
    elif cmd == "bcno":
        pending_bc.pop(uid, None)
        await show(event, admin_view())
    elif cmd == "bcyes":
        text = pending_bc.pop(uid, None)
        if not text:
            return await event.answer("Nothing to send", alert=True)
        await show(event, ("📢 Sending…", []))
        ok = fail = 0
        for u in broadcast_ids():
            try:
                await bot.send_message(u, "📢 **Message from admin**\n\n" + text)
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.2)
        await show(event, (f"📢 Broadcast done: {ok} delivered, {fail} failed "
                           f"(users must have started the bot).",
                           [[Button.inline("◀️ Admin", b"admin")]]))
    elif cmd == "wipe":
        await show(event, ("🗑 <b>Delete all my data?</b>\n\nThis logs your account out of this bot and deletes your "
                           "login, tasks and settings. Only your plan/trial record is kept (so the free trial "
                           "can't be reused). This cannot be undone.",
                           [[Button.inline("Yes, delete everything", b"wipec"), Button.inline("No", b"main")]]))
    elif cmd == "wipec":
        await wipe_user(uid)
        await show(event, ("✅ All your data was deleted and your account was logged out.\n\n" + main_view(uid)[0],
                           main_view(uid)[1]))
    elif cmd in ("login", "loginok"):
        u = user_rec(uid)
        if cmd == "login" and uid != OWNER_ID and not (u and u.get("agreed")):
            return await show(event, terms_view())
        if cmd == "loginok" and u:
            u["agreed"] = True
            save()
        state[uid] = {"step": "phone"}
        await show(event, ("🔐 Send your phone number with country code, e.g. "
                           "<code>+919XXXXXXXXX</code>\n\n"
                           "Not getting the code? Use the link login below, no code is needed.",
                           [[Button.inline("📱 Login with link (no code)", b"qrlogin")],
                            [Button.inline("Cancel", b"main")]]))
    elif cmd == "qrlogin":
        await start_qr_login(event, uid)
    elif cmd == "logout":
        await show(event, ("🚪 Log out? All your tasks will stop and "
                           "your account will be disconnected from this bot.",
                           [[Button.inline("Yes, log out", b"logoutc"),
                             Button.inline("No", b"main")]]))
    elif cmd == "logoutc":
        await drop_user(uid)
        await show(event, main_view(uid))
    elif cmd == "list":
        await show(event, list_view(uid))
    elif cmd == "new":
        cap = task_cap_msg(uid)
        if cap:
            return await event.answer(cap, alert=True)
        state[uid] = {"field": "new"}
        await show(event, ("➕ Send a name for the new task.", [[Button.inline("Cancel", b"list")]]))
    elif cmd == "t":
        await show(event, task_view(tid))
    elif cmd == "sec" and len(parts) >= 3 and parts[2] in SECTIONS:
        await show(event, section_view(tid, parts[2]))
    elif cmd == "settings":
        await show(event, settings_view(uid))
    elif cmd == "tg":
        s = db["tasks"][tid]["settings"]
        if parts[2] == "ai_rewrite" and not s.get("ai_rewrite"):
            if not is_premium(uid):
                return await event.answer("🔒 Premium feature (Pro and Unlimited plans). Tap 💎 My Plan to upgrade.",
                                          alert=True)
            if not GEMINI_KEY:
                return await event.answer("AI Rewrite is not set up on this bot yet.", alert=True)
        s[parts[2]] = not s.get(parts[2], False)
        save()
        await show(event, section_view(tid, TOGGLE_SECTION.get(parts[2], "op")))
    elif cmd == "go":
        t = db["tasks"][tid]
        if tid in over_limit_ids(uid):
            return await event.answer("🔒 This task is over your plan's limit. Upgrade to use it.", alert=True)
        if not t.get("source") or not t.get("target"):
            return await event.answer("Set source and target first", alert=True)
        t["running"] = True
        save()
        await show(event, task_view(tid))
    elif cmd == "stop":
        db["tasks"][tid]["running"] = False
        save()
        await show(event, task_view(tid))
    elif cmd == "clq":
        db["tasks"][tid]["queue"] = []
        save()
        await show(event, section_view(tid, "sc"))
    elif cmd == "clone":
        cap = task_cap_msg(uid)
        if cap:
            return await event.answer(cap, alert=True)
        src_t = db["tasks"][tid]
        new_id = next_tid()
        c = json.loads(json.dumps({
            "source": src_t.get("source"), "source_title": src_t.get("source_title", ""),
            "source_protected": src_t.get("source_protected", False),
            "target": src_t.get("target"), "target_title": src_t.get("target_title", ""),
            "extra_targets": src_t.get("extra_targets", []), "extra_sources": src_t.get("extra_sources", []),
            "settings": src_t["settings"]}))
        c.update({"owner": uid, "name": (src_t["name"] + " copy")[:40], "running": False,
                  "queue": [], "stats": default_stats()})
        db["tasks"][new_id] = c
        save()
        await show(event, task_view(new_id))
    elif cmd == "oldstop":
        job = oldjobs.get(tid)
        if job:
            job["stop"] = True
        return await event.answer("Stopping…")
    elif cmd in ("in", "inm", "ina"):
        field = parts[2]
        if field in PREMIUM_FIELDS and not is_premium(uid):
            return await event.answer("🔒 Premium feature (Pro and Unlimited plans). Tap 💎 My Plan to upgrade.",
                                      alert=True)
        if field == "extratarget" and plan_info(uid)["extra"] == 0:
            return await event.answer("🔒 Your plan has no extra targets. Tap 💎 My Plan to upgrade.", alert=True)
        if field == "extrasource" and plan_info(uid)["xsrc"] == 0:
            return await event.answer("🔒 Your plan has no extra sources. Tap 💎 My Plan to upgrade.", alert=True)
        if cmd == "in" and field in PICK_FIELDS:
            return await open_picker(event, uid, tid, field)
        if cmd == "in" and field in LIST_FIELDS:
            return await show(event, list_manager_view(tid, field))
        prompt = PROMPTS[field]
        if cmd == "ina" and field in LIST_FIELDS:
            prompt = "➕ <b>These will be ADDED</b> to your current list.\n\n" + prompt
        if field == "watermark" and not PIL_OK:
            prompt += "\n\n⚠️ Pillow isn't installed, so watermarking won't work yet. In Termux run: <code>pkg install python-pillow</code>"
        state[uid] = {"tid": tid, "field": field}
        if cmd == "ina" and field in LIST_FIELDS:
            state[uid]["mode"] = "add"
        await show(event, (f"✍️ {prompt}", [[Button.inline("Cancel", back_cb(tid, field))]]))
    elif cmd in ("pk", "pkr") and len(parts) >= 4 and parts[2] in PICK_FIELDS:
        kind = parts[3] if parts[3] in ("all", "channel", "group", "bot") else "all"
        if cmd == "pkr":
            await open_picker(event, uid, tid, parts[2], kind, 0, keepq=True, force=True)
        else:
            page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            await open_picker(event, uid, tid, parts[2], kind, page, keepq=(len(parts) > 5 and parts[5] == "1"))
    elif cmd == "lm" and len(parts) >= 3 and parts[2] in LIST_FIELDS:
        await show(event, list_manager_view(tid, parts[2], int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0))
    elif cmd == "lmx" and len(parts) >= 5 and parts[2] in LIST_FIELDS and parts[3].isdigit():
        gone = list_remove(db["tasks"][tid], parts[2], int(parts[3]), parts[4])
        save()
        if gone is None:
            await show(event, list_manager_view(tid, parts[2], note="⚠️ The list had changed, so nothing was removed.\n\n"))
        else:
            await show(event, list_manager_view(tid, parts[2], note=f"✅ Removed <code>{esc(gone[:60])}</code>\n\n"))
    elif cmd == "lmc" and len(parts) >= 3 and parts[2] in LIST_FIELDS:
        n = len(list_items(db["tasks"][tid]["settings"], parts[2]))
        await show(event, (f"🗑 Remove <b>all {n}</b> item(s) from {LIST_TITLE[parts[2]]}?",
                           [[Button.inline("Yes, remove all", f"lmcy:{tid}:{parts[2]}".encode()),
                             Button.inline("No", f"lm:{tid}:{parts[2]}".encode())]]))
    elif cmd == "lmcy" and len(parts) >= 3 and parts[2] in LIST_FIELDS:
        s = db["tasks"][tid]["settings"]
        s[LIST_FIELDS[parts[2]]] = {} if parts[2] == "replace" else []
        save()
        await show(event, list_manager_view(tid, parts[2], note="✅ All removed.\n\n"))
    elif cmd == "dir":
        s = db["tasks"][tid]["settings"]
        s["direction"] = DIR_ORDER[(DIR_ORDER.index(s.get("direction", "all")) + 1) % len(DIR_ORDER)]
        save()
        await show(event, section_view(tid, "ft"))
    elif cmd == "rm":
        await show(event, remove_view(tid))
    elif cmd == "rmx" and len(parts) >= 3 and parts[2] in ("src", "tgt", "xs", "xt", "allxs", "allxt"):
        cid = int(parts[3]) if len(parts) > 3 and re.fullmatch(r"-?\d+", parts[3]) else None
        t = db["tasks"][tid]
        note, _ = do_remove(t, parts[2], cid)
        save()
        if has_removable(t):
            await show(event, remove_view(tid, note))
        else:
            text, btns = section_view(tid, "st")
            await show(event, (note + text, btns))
    elif cmd == "pre":
        await show(event, presets_view(tid))
    elif cmd == "prea" and len(parts) >= 3 and parts[2] in PRESETS:
        label = apply_preset(db["tasks"][tid], parts[2])
        save()
        text, btns = task_view(tid)
        await show(event, (f"✅ Preset applied: <b>{label}</b>\n\n" + text, btns))
    elif cmd == "rep":
        if uid in db["noreport"]:
            db["noreport"].remove(uid)
        else:
            db["noreport"].append(uid)
        save()
        await show(event, settings_view(uid))
    elif cmd == "pks" and len(parts) >= 4 and parts[2] in PICK_FIELDS:
        state[uid] = {"tid": tid, "field": "pksearch", "pf": parts[2],
                      "kind": parts[3] if parts[3] in ("all", "channel", "group", "bot") else "all"}
        await show(event, ("🔎 Send a name (or part of it), @username or ID to search your chats.",
                           [[Button.inline("Cancel", f"pk:{tid}:{parts[2]}:all:0:0".encode())]]))
    elif cmd == "ps" and len(parts) >= 4 and parts[2] in PICK_FIELDS and re.fullmatch(r"-?\d+", parts[3]):
        note = await pick_chat(event, uid, tid, parts[2], int(parts[3]))
        if note is None:
            return
        text, btns = section_view(tid, "st")
        await show(event, (note + text, btns))
    elif cmd == "del":
        await show(event, (f"🗑 Delete task <b>{esc(db['tasks'][tid]['name'])}</b>?",
                           [[Button.inline("Yes, delete", f"delc:{tid}".encode()),
                             Button.inline("No", f"t:{tid}".encode())]]))
    elif cmd == "delc":
        db["tasks"].pop(tid, None)
        save()
        await show(event, list_view(uid))
    await event.answer()


# ---------------- message handler ----------------
INVITE = re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)([\w-]+)", re.I)


async def resolve_chat(cl, text, msg):
    """Find a chat from a forwarded post, an invite link, an ID or an @username.
    Returns (marked_id, title, is_protected) or (None, error_text, False)."""
    try:
        invite = INVITE.search(text)
        if msg.fwd_from and msg.fwd_from.from_id:
            ent = await cl.get_entity(msg.fwd_from.from_id)
        elif invite:
            info = await cl(CheckChatInviteRequest(invite.group(1)))
            if isinstance(info, ChatInviteAlready):
                ent = info.chat                      # already a member
            else:
                upd = await cl(ImportChatInviteRequest(invite.group(1)))   # joins the chat
                ent = upd.chats[0]
        else:
            t = text.strip()
            if re.fullmatch(r"-?\d+", t):
                t = int(t)
            try:
                ent = await cl.get_entity(t)
            except ValueError:
                await cl.get_dialogs()               # refresh the chat cache once
                try:
                    ent = await cl.get_entity(t)
                except ValueError:
                    if isinstance(t, int) and t > 0:
                        ent = await cl.get_entity(PeerChannel(t))
                    else:
                        raise
        return utils.get_peer_id(ent), utils.get_display_name(ent), bool(getattr(ent, "noforwards", False))
    except Exception as e:
        return None, str(e), False


def split_words(raw):
    """Words/phrases separated by commas or new lines. A line starting with re: is one regex."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line.lower().startswith("re:"):
            try:
                re.compile(line[3:])
            except re.error as e:
                raise ValueError(f"Invalid regex '{line}': {e}")
            out.append(line)
        else:
            out += [w.strip() for w in line.split(",") if w.strip()]
    return out


def parse_duration(raw):
    raw = raw.strip().lower()
    if raw in ("off", "clear", "0"):
        return 0
    m = re.fullmatch(r"(\d+)\s*(m|min|mins|h|hr|hrs|d|day|days)", raw)
    if not m:
        raise ValueError("Send a time like 30m, 6h or 2d, or 'off'.")
    return int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)[0]]


def parse_deal(raw):
    raw = raw.strip().lower().replace(",", "")
    if raw in ("off", "clear", "0"):
        return 0.0, 0.0
    d = re.search(r"discount\s*(\d+(?:\.\d+)?)", raw)
    p = re.search(r"price\s*(\d+(?:\.\d+)?)", raw)
    if not d and not p:
        raise ValueError("Use: discount 50, price 999, discount 50 price 999, or off.")
    return (float(d.group(1)) if d else 0.0), (float(p.group(1)) if p else 0.0)


def apply_input(t, field, raw, fmt):
    s = t["settings"]
    clear = raw.strip().lower() == "clear"
    if field == "name":
        t["name"] = raw.strip()[:40]
    elif field == "delay":
        try:
            s["delay"] = max(0.0, float(raw))
        except ValueError:
            raise ValueError("Please send a number, e.g. 5")
    elif field in ("header", "footer"):
        s[field] = "" if clear else fmt.strip()
    elif field in ("blacklist", "whitelist"):
        s[field] = [] if clear else split_words(raw)
    elif field == "replace":
        if clear:
            s["replace"] = {}
        else:
            for line in fmt.splitlines():
                if "=>" in line:
                    old, new = line.split("=>", 1)
                    if old.strip():
                        s["replace"][old.strip()] = new.strip()
    elif field == "linkrules":
        if clear:
            s["link_rules"] = []
        else:
            good = [ln.strip() for ln in raw.splitlines() if parse_rule(ln)]
            if not good:
                raise ValueError("No valid rules. Use 'domain ? key=value' or 'domain => https://link'.")
            s["link_rules"] = good
    elif field == "translate":
        code = raw.strip()
        if code.lower() in ("off", "clear"):
            s["translate"] = ""
        elif re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{2,4})?", code):
            s["translate"] = code
        else:
            raise ValueError("Send a language code like en, hi, bn or off.")
    elif field == "watermark":
        s["watermark"] = "" if clear else raw.strip()[:60]
    elif field == "sched":
        s["sched"] = {**default_sched(), **parse_schedule(raw)}
    elif field == "delafter":
        s["delete_after"] = parse_duration(raw)
    elif field == "dealfilter":
        s["min_discount"], s["max_price"] = parse_deal(raw)
    elif field == "aiprompt":
        s["ai_prompt"] = "" if clear else raw.strip()[:500]
    elif field == "tglink":
        s["tg_link"] = parse_tg_link(raw)
    elif field == "route":
        if clear:
            s["route"] = []
        else:
            if not t.get("target"):
                raise ValueError("Set the main TARGET first, then add routing rules.")
            routes = parse_route(raw.splitlines())
            if not routes:
                raise ValueError("Send at least one rule, e.g. anime => -1001234567890")
            s["route"] = [[kw, cid] for kw, cid in routes]
    elif field == "template":
        if clear:
            s["template"] = ""
        else:
            s["template"] = fmt.strip()[:1000]
    elif field == "linkmap":
        if clear:
            s["link_map"] = []
        else:
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            if not lines:
                raise ValueError("Send at least one rule, e.g. tg1 => https://t.me/mychannel")
            for ln in lines:
                parse_map_rule(ln)                 # raises a clear ValueError for a bad line
            s["link_map"] = lines
    elif field == "types":
        if raw.strip().lower() in ("all", "clear", "off"):
            s["types"] = []
        else:
            wanted = [x for x in re.split(r"[,\s]+", raw.lower()) if x]
            bad = [x for x in wanted if x not in TYPES]
            if bad or not wanted:
                raise ValueError("Valid types: " + ", ".join(TYPES) + " (or 'all').")
            s["types"] = list(dict.fromkeys(wanted))


class _Chat:
    """Minimal event stand-in so background tasks can reply to a user."""
    def __init__(self, uid):
        self.sender_id = uid

    async def respond(self, *a, **k):
        return await bot.send_message(self.sender_id, *a, **k)


def qr_text(url=None):
    t = ("📱 <b>Log in with a link (no code needed)</b>\n\n"
         "1. Tap <b>🔓 Tap to Log In</b> below. Do this on the phone where Telegram is logged in "
         "to this number.\n"
         "2. Telegram asks you to confirm the login. Confirm it.\n\n"
         "The link renews itself every ~30 seconds. If you have a 2-step password I'll ask for it after.")
    if url:
        t += f'\n\nButton not working? <a href="{esc(url)}">Tap this link</a>.'
    return t


def qr_png(url):
    buf = io.BytesIO()
    qrcode.make(url).save(buf, "PNG")
    buf.seek(0)
    buf.name = "login_qr.png"
    return buf


async def send_qr_photo(uid, url):
    """Send the login QR as a picture (None if the 'qrcode' package is missing or sending fails)."""
    if not QR_OK:
        return None
    try:
        return await bot.send_file(uid, qr_png(url), caption=(
            "📷 <b>Scan this QR</b> with Telegram on <b>another device</b>: "
            "Settings → Devices → Link Desktop Device.\nIt renews every ~30 seconds."), parse_mode="html")
    except Exception as e:
        print(f"QR picture failed: {e!r}")
        return None


async def drop_msg(m):
    if m is not None:
        try:
            await m.delete()
        except Exception:
            pass


def qr_buttons(url):
    return [[Button.url("🔓 Tap to Log In", url)], [Button.inline("Cancel", b"main")]]


def _qr_secs(qr):
    try:
        exp = qr.expires
        now = datetime.now(exp.tzinfo) if exp.tzinfo else datetime.utcnow()
        return max(5, int((exp - now).total_seconds()))
    except Exception:
        return 25


async def qr_wait(uid, cl, qr, msg, photo=None):
    """Wait (up to ~4 minutes) until the user confirms the login in Telegram."""
    def mine():
        return state.get(uid, {}).get("client") is cl
    try:
        for _ in range(8):
            if not mine():
                return
            try:
                await qr.wait(timeout=_qr_secs(qr))
            except asyncio.TimeoutError:
                try:
                    await qr.recreate()
                    await bot.edit_message(msg, qr_text(qr.url), buttons=qr_buttons(qr.url), parse_mode="html")
                except Exception as e:
                    if "not modified" not in str(e).lower():
                        print(f"QR refresh: {e!r}")
                if QR_OK and mine():                     # always show a fresh QR, never a stale one
                    await drop_msg(photo)
                    photo = await send_qr_photo(uid, qr.url)
                continue
            except SessionPasswordNeededError:
                if mine():
                    state[uid]["step"] = "password"
                    await bot.send_message(uid, "🔑 Send your 2-step verification password "
                                                "(the message will be deleted right away).")
                return
            except Exception as e:
                if mine():
                    await clear_state(uid)
                    await bot.send_message(uid, f"❌ {esc(e)}\nTap Login Account to try again.", parse_mode="html")
                return
            if mine():
                await finish_login(_Chat(uid), uid, cl)
            return
        if mine():
            await clear_state(uid)
            await bot.send_message(uid, "⌛ The login link expired. Tap Login Account to try again.")
    finally:
        await drop_msg(photo)


async def start_qr_login(event, uid):
    if uid != OWNER_ID and logins_busy():
        return await show(event, ("⏳ Many people are logging in right now. Try again in a minute.",
                                  [[Button.inline("◀️ Back", b"login")]]))
    cl = TelegramClient(StringSession(), API_ID, API_HASH)
    await cl.connect()
    try:
        qr = await cl.qr_login()
    except Exception as e:
        await cl.disconnect()
        return await show(event, (f"❌ Couldn't start link login: {esc(e)}",
                                  [[Button.inline("◀️ Back", b"login")]]))
    state[uid] = {"step": "qr", "client": cl}
    try:
        msg = await event.edit(qr_text(), buttons=qr_buttons(qr.url), parse_mode="html")
    except Exception as e:
        if "not modified" in str(e).lower():
            msg = None
        else:                                    # the button was refused: fall back to a text link
            msg = await event.edit(qr_text(qr.url), buttons=[[Button.inline("Cancel", b"main")]],
                                   parse_mode="html")
    photo = await send_qr_photo(uid, qr.url)
    asyncio.create_task(qr_wait(uid, cl, qr, msg or await event.get_message(), photo))


async def finish_login(event, uid, cl):
    db["sessions"][str(uid)] = cl.session.save()
    save()
    old = clients.pop(uid, None)
    if old:
        try:
            await old.disconnect()
        except Exception:
            pass
    clients[uid] = cl
    attach(uid, cl)
    claim_referral(uid)
    await cl.get_dialogs()
    state.pop(uid, None)
    text, btns = main_view(uid)
    await event.respond("✅ Logged in!\n\n" + text, buttons=btns, parse_mode="html")


def code_via(t):
    """Human text for where Telegram sent the login code (t = SentCodeType object or None)."""
    n = type(t).__name__ if t is not None else ""
    if "App" in n:
        return "your <b>Telegram app</b>: open the chat named <b>“Telegram”</b> on a device where this number is logged in"
    if "Sms" in n:
        return "<b>SMS</b> to your phone number (can take a few minutes)"
    if "Call" in n or "Missed" in n:
        return "a <b>phone call</b> to your number"
    if "Email" in n:
        return "your <b>email</b>"
    return "Telegram (check your Telegram app, SMS and calls)"


def code_next_name(t):
    n = type(t).__name__ if t is not None else ""
    if not n:
        return None
    if "Sms" in n:
        return "SMS"
    if "Call" in n or "Missed" in n:
        return "a phone call"
    if "App" in n:
        return "your Telegram app"
    return "another method"


def code_msg(sent, resent=False):
    nxt = code_next_name(getattr(sent, "next_type", None))
    wait = getattr(sent, "timeout", None)
    text = ("📩 <b>Code sent again</b> via " if resent else "📩 <b>Code sent</b> via ") + code_via(getattr(sent, "type", None)) + ".\n\n"
    text += ("Send the code <b>with spaces</b>, e.g. <code>1 2 3 4 5</code> "
             "(Telegram blocks the code if it is sent without spaces).\n\n")
    if nxt:
        text += (f"Didn't get it? " + (f"Wait about {wait} seconds, then tap " if wait else "Tap ")
                 + f"<b>📩 Resend code</b>. Next method: {nxt}.")
    else:
        text += "Didn't get it? Wait 10-15 minutes, then start the login again."
    return text


def code_buttons(sent):
    row = [[Button.inline("📩 Resend code", b"resend")]] if getattr(sent, "next_type", None) is not None else []
    return row + [[Button.inline("📱 Login with link instead", b"qrlogin")],
                  [Button.inline("Cancel", b"main")]]


async def do_resend(event, uid):
    st = state.get(uid)
    if not st or st.get("step") != "code":
        return await event.answer("No login in progress. Tap Login Account again.", alert=True)
    if not st.get("can_resend"):
        return await event.answer("Telegram has no other way to send the code. Wait 10-15 minutes, "
                                  "then start the login again.", alert=True)
    if not code_budget(uid):
        return await event.answer("Too many code requests. Wait about an hour or use the link login.", alert=True)
    try:
        sent = await st["client"](ResendCodeRequest(st["phone"], st["hash"]))
    except Exception as e:
        return await event.answer(f"Couldn't resend: {str(e)[:150]}", alert=True)
    st["hash"] = sent.phone_code_hash
    st["can_resend"] = getattr(sent, "next_type", None) is not None
    await event.answer("Code sent again")
    await event.respond(code_msg(sent, resent=True), buttons=code_buttons(sent), parse_mode="html")


async def login_step(event, uid, st, raw):
    step = st["step"]
    if step == "qr":
        return await event.respond("Tap <b>🔓 Tap to Log In</b> in the message above, or send /cancel.",
                                   parse_mode="html")
    if step == "phone":
        phone = re.sub(r"[^\d+]", "", raw)
        if uid != OWNER_ID and logins_busy():
            return await event.respond("⏳ Many people are logging in right now. Please try again in a minute.")
        if not code_budget(uid):
            return await event.respond("⏳ Too many code requests. Please wait about an hour, "
                                       "or use 📱 Login with link (no code).")
        cl = TelegramClient(StringSession(), API_ID, API_HASH)
        await cl.connect()
        try:
            sent = await cl.send_code_request(phone)
        except Exception as e:
            await cl.disconnect()
            return await event.respond(f"❌ {esc(e)}\nSend the number again or /cancel.", parse_mode="html")
        state[uid] = {"step": "code", "client": cl, "phone": phone, "hash": sent.phone_code_hash,
                      "can_resend": getattr(sent, "next_type", None) is not None}
        return await event.respond(code_msg(sent), buttons=code_buttons(sent), parse_mode="html")
    cl = st["client"]
    if step == "code":
        try:
            await event.delete()
        except Exception:
            pass
        code = re.sub(r"\D", "", raw)
        try:
            await cl.sign_in(st["phone"], code, phone_code_hash=st["hash"])
        except SessionPasswordNeededError:
            st["step"] = "password"
            return await event.respond("🔑 Send your 2-step verification password (the message will be deleted right away).")
        except PhoneCodeInvalidError:
            return await event.respond("❌ Wrong code. Send it again (with spaces).")
        except PhoneCodeExpiredError:
            await clear_state(uid)
            return await event.respond("⌛ The code expired. Send /start and log in again.")
        except Exception as e:
            await clear_state(uid)
            return await event.respond(f"❌ {esc(e)}\nSend /start and try again.", parse_mode="html")
        return await finish_login(event, uid, cl)
    if step == "password":
        try:
            await event.delete()
        except Exception:
            pass
        try:
            await cl.sign_in(password=raw)
        except PasswordHashInvalidError:
            return await event.respond("❌ Wrong password. Send it again.")
        except Exception as e:
            await clear_state(uid)
            return await event.respond(f"❌ {esc(e)}\nSend /start and try again.", parse_mode="html")
        return await finish_login(event, uid, cl)


async def handle_restore(event, uid):
    """A .json file sent with the caption /restore."""
    if (event.message.file.size or 0) > 5_000_000:
        return await event.respond("❌ File too large.")
    try:
        data = json.loads((await event.message.download_media(file=bytes)).decode("utf-8"))
        assert isinstance(data, dict)
    except Exception:
        return await event.respond("❌ That isn't a valid backup file.")

    if data.get("type") == "tasks_export":
        items = data.get("tasks", [])
        lim = plan_info(uid)["tasks"]
        if lim is not None:
            free = lim - sum(1 for t in db["tasks"].values() if t["owner"] == uid)
            items = items[:max(free, 0)]
        added = 0
        for item in items:
            tid = next_tid()
            db["tasks"][tid] = {
                "owner": uid, "name": str(item.get("name", "Task"))[:40],
                "source": item.get("source"), "source_title": item.get("source_title", ""),
                "target": item.get("target"), "target_title": item.get("target_title", ""),
                "running": False, "queue": [], "stats": default_stats(),
                "settings": item.get("settings", {}),
                "source_protected": item.get("source_protected", False),
                "extra_targets": item.get("extra_targets", []),
                "extra_sources": item.get("extra_sources", []),
            }
            added += 1
        normalize(db)
        save()
        skipped = len(data.get("tasks", [])) - added
        extra = f" ({skipped} skipped: over your plan's task limit)" if skipped else ""
        return await event.respond(f"✅ Restored {added} task(s){extra}. They are stopped, open each and press Start.")

    if "sessions" in data and "tasks" in data:
        if uid != OWNER_ID:
            return await event.respond("⛔ Only the admin can restore a full backup.")
        if os.path.exists(DATA_FILE):
            os.replace(DATA_FILE, DATA_FILE + ".bak")
        db.clear()
        data["sessions"] = decrypt_sessions(data.get("sessions", {}))
        db.update(normalize(data))
        save()
        await reconnect_all()
        return await event.respond(f"✅ Full backup restored. {len(clients)} account(s) reconnected. "
                                   f"(Previous data kept as {DATA_FILE}.bak)")
    await event.respond("❌ Unrecognized backup file.")


@bot.on(events.NewMessage(incoming=True))
@safe_handler
async def on_msg(event):
    uid = event.sender_id
    raw = (event.raw_text or "").strip()
    if not event.is_private:
        return

    if not allowed(uid):
        if raw.startswith("/start"):
            await event.respond("⛔ You are blocked from using this bot.")
        return
    if not rate_ok(uid):
        if rate_warn(uid):
            await event.respond("⏳ You're sending too fast. Please slow down.")
        return
    w = raw.split()
    if len(w) == 2 and w[0] == "/start" and w[1].startswith("ref_") and w[1][4:].isdigit():
        ref = int(w[1][4:])
        if ref != uid and uid != OWNER_ID and str(uid) not in db["users"]:
            pending_ref[uid] = ref                  # rewarded once the user joins and registers
    if not await is_member(uid):
        text, btns = join_view()
        return await event.respond(text, buttons=btns, parse_mode="html")
    if await reg(event, uid):
        await event.respond(welcome_text(), parse_mode="html")

    if event.message.document and raw.lower().startswith("/restore"):
        return await handle_restore(event, uid)

    # ---- admin commands
    if uid == OWNER_ID and raw.startswith("/coupon"):
        return await handle_coupon_cmd(event, raw)
    if uid == OWNER_ID and raw.startswith("/user "):
        q = raw[6:].strip().lower().lstrip("@")
        hits = [(k, u) for k, u in db["users"].items()
                if q == k or (q and (q in (u.get("name") or "").lower() or q in (u.get("username") or "").lower()))]
        if not hits:
            return await event.respond("No user found.")
        return await event.respond("\n\n".join(user_card(k, u) for k, u in hits[:5])
                                   + ("\n\n…more matches, be more specific." if len(hits) > 5 else ""),
                                   parse_mode="html")
    if uid == OWNER_ID and raw.startswith("/grant"):
        bits = raw.split()
        if (len(bits) not in (3, 4) or not bits[1].isdigit() or bits[2].lower() not in ("basic", "pro", "unlimited")
                or (len(bits) == 4 and not bits[3].isdigit())):
            return await event.respond("Use: /grant USER_ID basic|pro|unlimited [days]\n"
                                       f"Days default to {PLAN_DAYS}; 0 = never expires.")
        target, plan = int(bits[1]), bits[2].lower()
        days = int(bits[3]) if len(bits) == 4 else PLAN_DAYS
        exp = activate_plan(target, plan, days)
        await event.respond(f"✅ {target} is now on {PLANS[plan]['name']}"
                            + (f" until {fmt_ts(exp)}." if exp else " (no expiry)."))
        await tell(target, f"🎁 You've been given the <b>{PLANS[plan]['name']}</b> plan"
                           + (f" until {fmt_ts(exp)}." if exp else "."))
        return
    if uid == OWNER_ID and raw.startswith(("/adduser", "/removeuser")):
        bits = raw.split()
        if len(bits) != 2 or not bits[1].lstrip("-").isdigit():
            return await event.respond("Use: /removeuser 123456789 (block)  or  /adduser 123456789 (unblock)")
        target = int(bits[1])
        if raw.startswith("/adduser"):
            if target in db["banned"]:
                db["banned"].remove(target)
                save()
            await event.respond(f"✅ {target} unblocked.")
        else:
            if target not in db["banned"] and target != OWNER_ID:
                db["banned"].append(target)
            await clear_state(target)
            await drop_user(target)
            await event.respond(f"🗑 {target} blocked. Their login was deleted and their tasks stopped.")
        return
    if uid == OWNER_ID and raw == "/backup":
        save()
        return await bot.send_file(uid, DATA_FILE,
                                   caption="💾 Full backup. ⚠️ Keep it private and keep a copy of secret.key (needed to restore logins).")

    if raw.split()[:1] and raw.split()[0] in ("/start", "/menu", "/cancel"):
        await clear_state(uid)
        text, btns = main_view(uid)
        return await event.respond(text, buttons=btns, parse_mode="html")

    st = state.get(uid)
    if not st:
        return
    if "step" in st:
        return await login_step(event, uid, st, raw)

    field = st["field"]

    if field == "coupon":
        state.pop(uid, None)
        return await event.respond(redeem_coupon(uid, raw), parse_mode="html",
                                   buttons=[[Button.inline("⬆️ Plans", b"plans")]])

    if field == "paysshot":
        m = event.message
        if not (m.photo or (m.document and (m.file.mime_type or "").startswith("image/"))):
            return await event.respond("Please send the payment screenshot as a photo, or /cancel.")
        key = st["plan"]
        pl = PLANS[key]
        pid = str(db["next_pay"])
        db["next_pay"] += 1
        amount = st.get("amount", pl["price"])
        db["payments"][pid] = {"uid": uid, "plan": key, "amount": amount, "status": "pending",
                               "time": time.time(), "disc": st.get("disc")}
        save()
        state.pop(uid, None)
        try:
            try:
                db["payments"][pid]["fid"] = utils.pack_bot_file_id(m.media)   # lets /payments re-show it later
            except Exception:
                pass
            await bot.send_file(OWNER_ID, m.media, parse_mode="html",
                                caption=pay_caption(pid, db["payments"][pid]), buttons=pay_buttons(pid))
            save()
        except Exception as e:
            db["payments"][pid]["status"] = "failed"
            save()
            print(f"Could not send payment to owner: {e!r}")
            return await event.respond("❌ Couldn't send your screenshot to the admin. Please try again later.")
        return await event.respond("✅ Screenshot received. You'll get a message here as soon as it's checked.")

    if field == "broadcast" and uid == OWNER_ID:
        if not raw:
            return await event.respond("Send the message as text.")
        pending_bc[uid] = event.text
        state.pop(uid, None)
        n = len(broadcast_ids())
        return await event.respond(
            f"📢 Preview:\n\n{event.text}\n\nSend this to {n} user(s)?",
            buttons=[[Button.inline("✅ Send", b"bcyes"), Button.inline("❌ Cancel", b"bcno")]])

    cl = clients.get(uid)
    if cl is None:
        state.pop(uid, None)
        return await event.respond("Please log in first. /start")

    if field == "new":
        if not raw:
            return await event.respond("Please send a text name.")
        tid = next_tid()
        db["tasks"][tid] = {"owner": uid, "name": raw[:40], "source": None, "source_title": "",
                            "target": None, "target_title": "", "running": False, "queue": [],
                            "source_protected": False, "extra_targets": [], "extra_sources": [],
                            "stats": default_stats(), "settings": default_settings()}
        save()
        state.pop(uid)
        text, btns = task_view(tid)
        return await event.respond(text, buttons=btns, parse_mode="html")

    tid = st["tid"]
    t = mine(uid, tid)
    if not t:
        state.pop(uid, None)
        return await event.respond("That task no longer exists. /start")

    if field == "pksearch":
        if uid not in clients:
            state.pop(uid, None)
            return await event.respond("Log in first. /start")
        q = "" if raw.strip().lower() == "clear" else raw.strip()[:40]
        pick_q[uid] = q
        state.pop(uid, None)
        items = await get_dialog_list(uid, clients[uid])
        text, btns = picker_view(uid, tid, st["pf"], st["kind"], 0, items)
        return await event.respond(text, buttons=btns, parse_mode="html")

    if field == "old":
        if tid in over_limit_ids(uid):
            state.pop(uid, None)
            return await event.respond("🔒 This task is over your plan's limit. Upgrade to use it.")
        if raw.lower() == "all":
            n = None
        else:
            try:
                n = int(raw)
            except ValueError:
                n = 0
            if n < 1 or (MAX_OLD_POSTS and n > MAX_OLD_POSTS):
                return await event.respond("Send a number like 50, or 'all'.")
        state.pop(uid, None)
        ocap = plan_cap(uid, "old")
        capped = bool(ocap and (n is None or n > ocap))
        if capped:
            n = ocap
        if not t.get("source") or not t.get("target"):
            return await event.respond("Set the source and target first.")
        if tid in oldjobs:
            return await event.respond("An old-posts run is already in progress for this task.")
        what = ("ALL posts" if n is None else f"the latest {n} posts") + (" (your plan's limit)" if capped else "")
        note = await event.respond(f"📜 Forwarding {what}…",
                                   buttons=[[Button.inline("⏹ Stop", f"oldstop:{tid}".encode())]])
        asyncio.create_task(run_old(cl, uid, tid, t, n, note))
        return

    if field == "extrasource":
        if raw.lower() == "clear":
            t["extra_sources"] = []
        else:
            lim = plan_info(uid)["xsrc"]
            if lim is not None and len(t.get("extra_sources", [])) >= lim:
                return await event.respond(f"🔒 Your plan allows {lim} extra source(s) per task. "
                                           "Tap 💎 My Plan to upgrade.")
            cid, title, _ = await resolve_chat(cl, raw, event.message)
            if cid is None:
                return await event.respond(f"❌ Couldn't find that chat: {esc(title)}", parse_mode="html")
            conflict = loop_conflict(t, field, cid)
            if conflict:
                return await event.respond("❌ " + conflict)
            extras = t.setdefault("extra_sources", [])
            if cid != t.get("source") and all(e["id"] != cid for e in extras):
                extras.append({"id": cid, "title": title})
        save()
        state.pop(uid, None)
        text, btns = back_view(tid, field)
        return await event.respond(text, buttons=btns, parse_mode="html")

    if field == "extratarget":
        if raw.lower() == "clear":
            t["extra_targets"] = []
        else:
            lim = plan_info(uid)["extra"]
            if lim is not None and len(t.get("extra_targets", [])) >= lim:
                return await event.respond(f"🔒 Your plan allows {lim} extra target(s) per task. "
                                           "Tap 💎 My Plan to upgrade.")
            cid, title, _ = await resolve_chat(cl, raw, event.message)
            if cid is None:
                return await event.respond(f"❌ Couldn't find that chat: {esc(title)}", parse_mode="html")
            conflict = loop_conflict(t, field, cid)
            if conflict:
                return await event.respond("❌ " + conflict)
            extras = t.setdefault("extra_targets", [])
            if cid != t.get("target") and all(e["id"] != cid for e in extras):
                extras.append({"id": cid, "title": title})
        save()
        state.pop(uid, None)
        text, btns = back_view(tid, field)
        return await event.respond(text, buttons=btns, parse_mode="html")

    if field in ("source", "target"):
        cid, title, protected = await resolve_chat(cl, raw, event.message)
        if cid is None:
            return await event.respond(
                f"❌ Couldn't find that chat: {esc(title)}\n"
                "Your account must be a member of it. Try the -100... ID, @username, "
                "the invite link, or forward a post from it.",
                parse_mode="html")
        conflict = loop_conflict(t, field, cid)
        if conflict:
            return await event.respond("❌ " + conflict)
        t[field] = cid
        t[field + "_title"] = title
        t[field + "_protected"] = protected
    else:
        try:
            if st.get("mode") == "add" and field in LIST_FIELDS:
                apply_add(t, field, raw, event.text or "")
            else:
                apply_input(t, field, raw, event.text or "")
        except ValueError as e:
            return await event.respond(f"❌ {esc(e)}", parse_mode="html")
    save()
    state.pop(uid, None)
    text, btns = back_view(tid, field)
    await event.respond(text, buttons=btns, parse_mode="html")


# ---------------- run ----------------
async def main():
    await bot.start(bot_token=BOT_TOKEN)
    install_button_colors(bot)
    try:
        flags["bot_username"] = (await bot.get_me()).username
    except Exception:
        pass
    if not CRYPTO_OK:
        print("WARNING: logins are stored UNENCRYPTED. Run: pkg install python-cryptography")
        try:
            await bot.send_message(OWNER_ID, "⚠️ Logins are stored unencrypted. In Termux run "
                                   "<code>pkg install python-cryptography</code> and restart the bot.",
                                   parse_mode="html")
        except Exception:
            pass
    if not QR_OK:
        print("NOTE: QR picture for link login is off. To enable it run: pip install qrcode")
    print(f"Build: {BUILD}")
    if _cloud["state"] == "fail":
        try:
            await bot.send_message(OWNER_ID, "⚠️ Cloud copy (MongoDB) could not connect, so it is OFF for this run. "
                                   "Your data is still saved in data.json. Check MONGO_URI and, in MongoDB Atlas, "
                                   "Network Access.")
        except Exception:
            pass
    if not GEMINI_KEY:
        print("NOTE: AI Rewrite is off (no GEMINI_KEY in config.json).")
    await connect_sessions()
    save()                                  # encrypts any old plain sessions right away
    asyncio.create_task(scheduler())
    print("Running. Open your bot and send /start")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
