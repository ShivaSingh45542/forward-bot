"""Quick checks that need no Telegram. Run with:  python run_tests.py   (from the project folder)."""
import asyncio, os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), "stubs"))     # fake telethon first
sys.path.insert(0, os.getcwd())
import forward_bot_multi as fb

passed = failed = 0
def ok(cond, name):
    global passed, failed
    if cond: passed += 1; print("PASS", name)
    else: failed += 1; print("FAIL", name)
def run(c): return asyncio.new_event_loop().run_until_complete(c)

# --- plans and limits
ok(set(fb.PLANS) == {"trial", "basic", "pro", "unlimited"}, "4 plans exist")
ok(fb.plan_info(fb.OWNER_ID)["tasks"] is None and fb.is_premium(fb.OWNER_ID), "owner is unlimited + premium")
ok(fb.PLANS["pro"]["tasks"] == 10 and fb.PLANS["basic"]["price"] == 99, "plan numbers match the plan sheet")
# --- link tools
A, B = "https://t.me/mine1", "https://t.me/+Mine2"
post = "Video: https://pdisk.example/abc\nHow: https://t.me/oldchan/10\nJoin: https://t.me/+OldInvite"
r = fb.apply_link_map(post, [f"tg1 => {A}", f"tg2 => {B}"])
ok(A in r and B in r and "oldchan" not in r and "pdisk.example/abc" in r, "Link Map: tg1/tg2 replaced, other link kept")
ok(fb.replace_tg_links("a t.me/x and https://t.me/y", A).count(A) == 2, "Replace TG links swaps every Telegram link")
ok(fb.apply_link_map("see https://t.me/a", ["tg1 => remove"]) == "see", "Link Map can remove a link")
# --- category routing + info template
ok("route" in fb.default_settings() and "template" in fb.default_settings(), "default settings have route + template")
post_info = "TITLE : Ramayana\nYEAR : 2026\nQuality : 1080p\nSize : 3.5GB"
_, info = fb.parse_template("{title}", post_info)
ok(info["title"] == "Ramayana" and info["year"] == "2026" and info["quality"] == "1080p" and info["size"] == "3.5GB",
   "info template parses title/year/quality/size")
ok(fb.parse_template("🎬 {title} ({year})", "TITLE : Ramayana\nYEAR : 2026\nQuality : 1080p\nSize : 3.5GB")[0] == "🎬 Ramayana (2026)", "info template fills in the post")
ok(fb.parse_template("no info here", "nothing here")[1] == {}, "info template leaves unknown keys as-is")
ok(fb.parse_route(["anime => -1001", "movie => -1002"]) == [("anime", -1001), ("movie", -1002)], "route rules parse")
ok(fb.parse_route(["anime => @mychannel"]) == [("anime", "@mychannel")], "route target can be an @username")
try:
    fb.parse_route(["bad line"]); ok(False, "bad route line raises")
except ValueError: ok(True, "bad route line raises")
try:
    fb.parse_route(["x => notanumber"]); ok(False, "route target must be a number")
except ValueError: ok(True, "route target must be a number")
# --- filters
class M: out = False
class O: out = True
ok(fb.direction_ok("incoming", [O()]) is False and fb.direction_ok("incoming", [M()]) is True, "Incoming-only skips my own messages")
# --- task + screens
tid = fb.next_tid()
fb.db["tasks"][tid] = {"owner": fb.OWNER_ID, "name": "Smoke", "source": -1, "target": -2, "running": False, "queue": [],
                       "stats": fb.default_stats(), "settings": fb.default_settings(), "source_protected": False,
                       "extra_targets": [], "extra_sources": []}
t = fb.db["tasks"][tid]
text, btns = fb.task_view(tid)
ok("Smoke" in text and len(btns) <= 8, "task screen renders and stays compact")
for key in fb.SECTIONS:
    txt, b = fb.section_view(tid, key)
    ok(fb.SECTIONS[key] in txt and b, f"section renders: {fb.SECTIONS[key]}")
t["settings"]["blacklist"] = ["spam", "scam"]
items = fb.list_items(t["settings"], "blacklist")
ok(items == ["spam", "scam"], "list manager lists items")
ok(fb.list_remove(t, "blacklist", 0, fb._tag("spam")) == "spam" and t["settings"]["blacklist"] == ["scam"], "remove one item")
ok(fb.list_remove(t, "blacklist", 0, "bad") is None, "stale remove is refused")
# --- category routing
ok(fb.pick_route_target(t, fb.default_settings(), "watch this anime now") is None, "no routing when rules are empty")
t["settings"]["route"] = [["anime", -1001], ["movie", -1002]]
ok(fb.pick_route_target(t, t["settings"], "new anime episode") == -1001, "route sends anime to its channel")
ok(fb.pick_route_target(t, t["settings"], "latest MOVIE out") == -1002, "route is case-insensitive")
ok(fb.pick_route_target(t, t["settings"], "just a song") is None, "route does not match unrelated posts")
items = fb.list_items(t["settings"], "route")
ok(items == ["anime => -1001", "movie => -1002"], "list manager lists route rules")
ok(fb.list_remove(t, "route", 0, fb._tag(items[0])) == items[0] and t["settings"]["route"] == [["movie", -1002]],
   "list manager can remove one route rule")
txt, b = fb.section_view(tid, "st")
ok("Category routing" in txt, "source/target section shows routing")
t["settings"]["template"] = "🎬 {title} ({year})"
txt, b = fb.section_view(tid, "tx")
ok("Info Template" in txt, "text section shows info template")
ok(fb.apply_input(t, "template", "🎬 {title}", "🎬 {title}") is None and t["settings"]["template"] == "🎬 {title}",
   "template input is saved")
t["settings"]["template"] = ""
ok(fb.apply_input(t, "route", "anime => -1009", "anime => -1009") is None and t["settings"]["route"] == [["anime", -1009]],
   "route input is saved")
ok("My Tasks" in str(fb.main_view(fb.OWNER_ID)[1]) or "Login" in str(fb.main_view(fb.OWNER_ID)[1]), "main menu renders")
# --- buttons
kb = fb.kb_json([[fb.Button.inline("▶️ Start", b"go:1")]])
ok(kb and kb["inline_keyboard"][0][0].get("style") == "success", "Start button is green")
# --- safety
ok(fb.loop_conflict(t, "source", -2) is not None and fb.loop_conflict(t, "source", -9) is None, "source can't equal target")
if fb.CRYPTO_OK:
    enc = fb.encrypt_sessions({"1": "SECRET"})
    ok(enc["1"].startswith("enc:") and fb.decrypt_sessions(enc)["1"] == "SECRET", "sessions encrypt/decrypt")
else:
    print("SKIP session encryption (install 'cryptography')")
d = fb.normalize({"allowed": [5], "tasks": {"9": {"settings": {}}}})
ok(d["users"]["5"]["plan"] == "unlimited" and d["tasks"]["9"]["settings"]["direction"] == "all", "normalize fills defaults and migrates old users")
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
