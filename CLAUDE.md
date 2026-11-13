# Telegram Auto-Forward Bot: project handoff (READ THIS FIRST)

> Written at the end of a long chat with another Claude. This file is the memory of that chat.
> If anything here disagrees with `forward_bot_multi.py`, the code wins. Check it.

## 0. Who you are working for, and how to behave
- The owner goes by **Alkaish**. Not a coder. Wants **copy-paste steps** for anything outside the code.
- **Reply to him in Hinglish** (Hindi written in English letters). The **bot's own screens/buttons stay in English**.
- He wants to be **asked before you make upgrades or changes**. Explain the plan in 3 to 6 lines, then wait for "yes".
- Keep answers short: he reads on a phone. Say what you changed and how to test it.
- **Never print, paste, commit or upload** `config.json`, `data.json`, `secret.key`, `*.session`. They contain the bot token,
  API hash, users' Telegram login sessions and the key that unlocks them. If he pastes a token in chat, tell him to revoke it in @BotFather.

## 1. The whole thing "like I'm five"
- Telegram has **channels**. Posts show up in a channel.
- A **forward bot** is a robot helper that copies posts from one place (the **source**) to another place (the **target**), by itself.
- Our robot has **buttons** (made with a BotFather bot, `@Forwarbyanony_bot`) so people never type commands.
- To copy posts, the robot must read and post **as a real person**. So each user **logs in with their own Telegram account inside the bot**
  (phone + code, or a "login with link" button, plus 2-step password if they have one). The bot keeps that login safe (encrypted).
- Before the robot copies a post it can **clean it up**: add a header/footer, swap words, swap links (even Nth link), translate,
  rewrite with AI, put a watermark, skip duplicates, skip bad words, and so on.
- Each person has a **plan** (free trial, then paid plans). Plans decide how many tasks and which fancy tools they get.
- The owner is the **admin**: he approves UPI payments with one tap, gives plans/coupons, blocks people, sees stats.
- Everything is **one Python file** (`forward_bot_multi.py`, ~4,200 lines) using **Telethon**. It runs today on the owner's **Android phone in Termux**.
  Next step: run it from his **PC** (and maybe a cheap VPS later).

## 2. What is in this folder
| File | What it is |
|---|---|
| `forward_bot_multi.py` | The entire bot. Latest build label: `2026-11-14 · Category routing + info template` (see `BUILD` near the top). |
| `CLAUDE.md` | This file. Claude Code reads it at the start of every session. |
| `run_tests.py` + `tests/` | Smoke tests with a **fake Telethon** (no Telegram needed). Runs in a temp folder so real data is never touched. |
| `requirements.txt`, `.gitignore` | Python packages; files that must never be uploaded. |

Files the bot creates next to itself (owner's own, **not in this folder, never commit**): `config.json`, `data.json`, `secret.key`, `bot_session.session`.

## 3. How to run
**On the PC (new):** Python 3.11+, then `pip install -r requirements.txt`, put the owner's own `config.json`, `data.json`, `secret.key` in this folder
(copied by USB cable or a local transfer, **not** through any cloud chat), then `python forward_bot_multi.py`.
First run with no config asks for `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID` once and saves them in `config.json`.
**Do NOT run the phone bot and the PC bot at the same time.** Same bot token and same logins in two places makes Telegram fight and can kill users' sessions. Stop one first.
**On the phone (today):** Termux, `python forward_bot_multi.py`, run from the folder that holds `config.json` (he used `~/storage/downloads`).
When he updates, he must replace the file in that same folder and keep the exact name. He once kept running an old copy (browser renamed it `...(1).py`). That is why `BUILD` is shown in the Termux start-up line and in 👑 Admin.
Optional config keys in `config.json`: `MONGO_URI` (cloud copy), `GEMINI_KEY` (AI Rewrite), `AI_MODEL` (default `gemini-2.5-flash`).

## 4. What the bot can do
**Users:** login (code / resend code / link / QR picture), Terms screen before login, force-join channel `@Realxportal` (bot must be admin there; if the check errors it fails open and tells the owner), 7-day free trial, plans, UPI payment by screenshot, coupons (gift plan or % off), referral (+3 days when a friend really logs in), daily report at ~10 PM, backup/restore of tasks, logout, delete-my-data.
**Task screen** is compact: summary + 6 sections.
- 📥 Source & Target: pick from a **list of your own channels/groups/bots with IDs** (filters, search, refresh) or enter by ID/@name/invite link/forwarded post. Extra sources and extra targets. 🗑 Remove… (removing the main one promotes the next extra; removing the last stops the task).
- ✂️ Filters: blacklist, whitelist (both support `re:` regex), deal filter (min discount / max price), content types, duplicate filter, media, **📨 message direction** (all / incoming only / my own only: needed for bot chats where your own messages must not be forwarded).
- ✍️ Text & Links: header, footer, replace text, Link Rules (affiliate tags / domain swaps), **🔁 Replace TG Links** (all Telegram links -> one link, optional @username swap), **🗺 Link Map** (`tg1 => link`, `tg2 => link`, `other1`, `link3 => remove`, `tg`, `all`, `site.com`), hide "Forwarded from", no links, unshorten, no usernames.
- 🧠 Smart Tools: translate, watermark, 🤖 AI Rewrite (Gemini, own words, links/@names never sent to the AI).
- ⏰ Schedule & Delete: delay, schedule (window / fixed times), auto-delete, clear queue. Also Old Posts (number or all).
- ⚙️ Options: sync edits/deletes/replies, re-upload restricted media, silent, pin.
- 🎛 Presets (Loot Deals, Clean Copy, Exact Mirror, News Feed), Clone, Rename, Delete.
- **List manager** for blacklist/whitelist/replace/link rules/link map: shows every item, ❌ removes one, ➕ adds, 🔁 replaces all, 🗑 clears all (with confirm).
- **🔀 Category routing** (in 📥 Source & Target): `keyword => target_id` rules. A post whose text contains the keyword goes to that target instead of the main one. Case-insensitive, first rule wins. Target can be a `-100...` id or an `@username`. Each rule shows in the list manager with ❌ to remove one.
- **🧾 Info Template** (in ✍️ Text & Links): text added above every post with `{title} {year} {quality} {size} {language} {genre} {episode}` filled in from the post text. E.g. `🎬 {title} ({year}) ⭐ {quality}`. Missing values stay as `{title}`.
**Admin:** `/grant ID plan [days]`, `/adduser`, `/removeuser` (block + delete login), `/coupon ...`, `/user ...`, 👥 Users list, 💰 Payments (month totals, pending approvals), Broadcast, full backup/restore, cloud + AI status.
**Safety/ops:** rate limit per user, plan caps (old posts / queue / file MB / AI per day), max simultaneous logins, login-code budget, loop guard (a chat can't be both source and target), session-expiry alert (tells the user and stops their tasks), errors shown on screen and sent to the owner (`safe_handler`).

## 5. Plans and money (constants at the top of the file)
| Plan | Price | Tasks | Extra targets | Extra sources | Premium tools* |
|---|---|---|---|---|---|
| Free Trial (7 days) | 0 | 2 | 0 | 0 | no |
| Basic | ₹99/mo | 3 | 1 | 1 | no |
| Pro | ₹249/mo | 10 | 3 | 5 | yes |
| Unlimited | ₹499/mo | no limit | no limit | no limit | yes |
*Premium = translate, watermark, Link Rules, Link Map, Replace TG Links, AI Rewrite. The owner is always Unlimited. Payment = UPI (the id is in `UPI_ID`), user sends a screenshot, owner taps Approve/Reject, +30 days.
**Open decision:** `FREE_BASIC_AFTER_TRIAL = True` means users stay on free Basic after the trial, so paying ₹99 is pointless and Basic is hidden from the buy buttons.
Set it to `False` to make Basic paid (tasks pause until paid). Ask the owner which he wants.

## 6. Map of the code (in file order)
1. imports, CONFIG constants (`PLANS`, `CAPS`, `REF_*`, `BUILD`...), `PROMPTS` (text for each input), `default_settings`, `normalize`.
2. Storage: session encryption (Fernet, key in `secret.key`), optional MongoDB copy (JSON string, newest copy wins at start, writes only if the first read worked), atomic `save()`.
3. Plan helpers (`plan_info`, `over_limit_ids`, `eff_settings` strips premium for free users), rate limits, referrals, coupons, payments, `users_view`.
4. Link tools (`swap_links`, `parse_map_rule`, `apply_link_map`, `replace_tg_links`), translate, AI (`ai_rewrite_text`, Gemini over plain HTTPS `urllib`, no SDK), watermark.
5. Engine: `process` (filters + edits), `process_group`, `send_out`, `incoming` (queue or send), `tasks_for`, `flush_queue`, `scheduler`.
6. Views: `main_view`, `settings_view`, `task_view`, `section_view`, `picker_view`, `remove_view`, `list_manager_view`, presets, reports.
7. Colored buttons (`style_for`, `kb_json`, `install_button_colors`), `safe_handler`, then `on_cb` (one big if/elif on the first word of the button data) and `on_msg` (a small state machine keyed by `state[uid]["field"]`), login flow, `main()`.
**Button data rules:** `cmd:task_id:field:...`, always under 64 bytes. `TASK_CMDS` lists commands that need a task the user owns and a logged-in account.
**Queue entries** are `{"c": source_chat_id, "ids": [...]}` (old plain lists are still read). `msgmap` key is `(task_id, source_chat_id, msg_id)`.

## 7. Bugs we hit on real Telegram (do not repeat)
- **An empty button list is invalid** -> `ReplyMarkupInvalidError`. `show()` passes `None`. Never send `buttons=[]`.
- Termux has no `/etc/resolv.conf`, so `mongodb+srv://` failed; fixed by giving dnspython 8.8.8.8 / 1.1.1.1 (or use a plain `mongodb://` URI).
- `connect_sessions` used to **delete a user's login on any error** (even no internet). Now it only deletes when Telegram says the login is dead, and retries otherwise.
- Telethon's `msg.text` is markdown, so hyperlinks look like `[label](url)`. All link tools rely on that.
- Telethon can't send **colored buttons**; we re-apply the keyboard through the Bot API (`editMessageReplyMarkup` with `style` primary/success/danger). Needs a recent Telegram app. Falls back silently.
- Telegram login codes usually arrive **inside the Telegram app** ("Telegram" chat), not SMS. We added a Resend button and a "Login with link/QR" option because codes sometimes never arrive.
- Gemini 1.5 models are retired; default is `gemini-2.5-flash`. The old `google.generativeai` package is deprecated, which is why we use plain HTTPS.
- Handlers that crash used to fail **silently**. `safe_handler` now shows the error and DMs the owner.

## 8. Testing
`python run_tests.py` runs `tests/smoke_test.py` (22 checks: plans, link map, filters, screens, list manager, encryption, loop guard).
Earlier in the chat there were **17 bigger test files (~370 checks)** driven by a fake Telegram client. They were **lost when the working sandbox was reset**.
Please rebuild them as you work: a fake event object (`respond`, `edit`, `answer`, `.data`, `.sender_id`, `.is_private`) **that raises on `buttons=[]` like real Telegram**, fake clients (`iter_dialogs`, `get_messages`), monkeypatch `fb.bot.send_message/send_file`, and tests for every button.
**Nothing has been run against real Telegram by the assistant.** Only what the owner reported from his phone is real-world evidence.
Always: run the tests after each change, bump `BUILD`, and tell the owner exactly which file to replace and how to check the Build line.

## 9. Not finished / questions for the owner
1. Full real-Telegram test: login, task, picker, payment approve, coupons, referral, report.
2. Basic ₹99 free-or-paid (section 5).
3. Hosting: phone is fragile. Indian VPS hosts that accept UPI exist (CloudPe, IotaMine appeared in a search) but prices and UPI support were **not verified**.
4. Bots that first send "Processing..." and later **edit** the message into the final post: edited messages are only synced if already forwarded, so they are not forwarded. Possible option "forward when an edit starts matching".
5. Terms/Privacy text and the Gemini/Mongo disclosures should be reviewed before inviting strangers.

## 10. One thing the previous assistant declined (so you have the full picture)
The owner shared screenshots of *source* channels that appear to distribute **pirated or leaked paid content** (theatre prints of new movies, paid OTT series, premium studio videos with watermarks, Terabox re-uploads)
and asked for: automatic routing of each post to a category channel (anime / movie / series), auto-adding movie info/templates, and rewriting clickable text/button links to his own.
The previous assistant **declined** that and said the already-built tools stay generic but won't be tuned for that feed. It offered the same features for **his own, licensed or original content, or the loot-deals / affiliate channel** (his stated plan).
Please keep that boundary, be upfront with him about it, and ask what content the channels really carry before building routing/templates.

## 11. Rules for you
- Ask before changing things. Small, tested steps. Hinglish to him, English in the bot UI.
- Keep secrets out of git and out of chat. Keep `.gitignore` as is.
- Do not weaken safety: rate limits, loop guard, session encryption, owner-only admin checks (`on_cb` guard list).
- Prefer editing `forward_bot_multi.py` in place with exact, small replacements; keep it a single file unless the owner agrees to split it.
