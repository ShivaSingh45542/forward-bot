"""Quick checks for edit forwarding."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "stubs"))
sys.path.insert(0, os.getcwd())
import forward_bot_multi as fb

passed = failed = 0
def ok(cond, name):
    global passed, failed
    if cond: passed += 1; print("PASS", name)
    else: failed += 1; print("FAIL", name)
def run(c): return asyncio.new_event_loop().run_until_complete(c)

# Setup mock data
uid = fb.OWNER_ID
tid = "test_task"
fb.db["users"][str(uid)] = {"plan": "unlimited"}
fb.db["tasks"][tid] = {
    "uid": uid,
    "owner": uid,
    "running": True,
    "settings": {"direction": "all", "forward_edited": True, "sync_edit": True, "target": -100},
    "queue": []
}

class Event:
    def __init__(self, msg, chat_id=None):
        self.message = msg
        self.chat_id = chat_id
class Msg:
    def __init__(self, text, id, out=False):
        self.text = text
        self.id = id
        self.out = out
        self.grouped_id = None
        self.media = None
        self.file = None
        self.photo = None
        self.sticker = None
        self.gif = None
        self.voice = None
        self.audio = None
        self.video = None
        self.document = None
        self.reply_to = None

from telethon import TelegramClient
cl = TelegramClient("test", 1, "hash")
on_new, on_album, on_edit, on_delete = fb.attach(uid, cl)

from unittest.mock import AsyncMock

# Test 1: Forward un-sent post
fb.msgmap.clear()
e = Event(Msg("Initial text", 101), chat_id=1)
# Mock a mock return for send_out to satisfy remember()
class MockSent:
    def __init__(self, id): self.id = id

fb.send_out = AsyncMock(return_value=(MockSent(201), None, []))

run(on_edit(e))
ok(101 in [m[2] for m in fb.msgmap.keys()], "edit forwards un-sent post")

# Test 2: Skip un-sent post when disabled
fb.db["tasks"][tid]["settings"]["forward_edited"] = False
e2 = Event(Msg("Edited text", 102), chat_id=1)
run(on_edit(e2))
ok(102 not in [m[2] for m in fb.msgmap.keys()], "edit skips un-sent post when disabled")

# Test 3: Sync if already forwarded
fb.db["tasks"][tid]["settings"]["forward_edited"] = True
fb.db["tasks"][tid]["settings"]["sync_edit"] = True
run(on_edit(Event(Msg("Updated text", 101), chat_id=1)))
ok(101 in [m[2] for m in fb.msgmap.keys()], "edit still syncs if already forwarded")

print(f"\nResult: {passed} passed, {failed} failed")
