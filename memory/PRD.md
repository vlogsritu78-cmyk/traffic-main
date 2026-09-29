# Telegram Redirect Bot for TrafficStars Ads — PRD

## Original problem statement
Modern Telegram bot to redirect TrafficStars ad traffic to a main Telegram account.
`/start` -> bilingual (Tamil+English) welcome message + single inline button
"💬 Message Official Account" opening the official account. Tracks conversions via
TrafficStars S2S postback. Cyberpunk/Retro-Futurism dashboard. PIN-protected admin
panel so friends can customize their own redirect username + welcome messages
without touching code.

## Architecture
- Backend: FastAPI (`/app/backend/server.py`) + MongoDB (Motor).
- Bot: `/app/backend/bot.py` — python-telegram-bot, long-polling.
  - As of 2026-08-08: the poller runs **inside the FastAPI backend process**
    (`bot.start_bot()`/`bot.stop_bot()` called from server.py's startup/shutdown
    events) instead of a separate supervisor worker — guarantees it's alive for
    as long as "backend" is up, which every deployment (preview + production)
    always runs. The old standalone `/etc/supervisor/conf.d/telegram_bot.conf`
    was removed (it was never part of git / the deployed image).
  - `drop_pending_updates=False` — any /start that arrives while briefly
    offline (restart/deploy) is processed on reconnect instead of dropped.
  - `post_init(app)` is called explicitly in `start_bot()` (PTB only auto-calls
    it from `run_polling()`/`run_webhook()`, not from manual `initialize()`/`start()`).
- **BOT_POLLING_ENABLED env var** (backend/.env): Telegram allows only ONE
  active long-poll connection per bot token. Since preview and production run
  the same code + same TELEGRAM_BOT_TOKEN, both would otherwise fight over the
  connection and split real conversions across two separate Mongo databases
  (this caused the reported "61 vs 25 conversions" mismatch). Preview's
  `backend/.env` now has `BOT_POLLING_ENABLED="false"` so **only production
  polls Telegram**. Defaults to enabled if unset (so production, whose .env is
  separate/inaccessible to this agent, is unaffected).
  - `/api/bot/status` returns `polling_enabled` + `online` (= has booted AND
    polling_enabled) so the dashboard doesn't lie about being live.
  - Frontend shows amber "[ dev mode — live on production ]" pill in preview
    instead of a misleading red "[ offline ]".
- Bridge (`/api/r`): TrafficStars `click_id` can be 100+ chars; Telegram
  `/start` payload is capped at 64 chars. Bridge stores the long click_id in
  `bot_clicks` keyed by a short token, redirects to
  `https://t.me/<bot>?start=<token>`. `bot.resolve_click_id()` looks the token
  back up to get the original click_id for the S2S postback.
- Admin auth: PIN -> bcrypt hash in `bot_settings._id="config".pin_hash` ->
  `POST /api/admin/unlock` returns JWT -> `Authorization: Bearer <token>` on
  writes (`PUT /api/settings`, `POST /api/admin/pin`). Per-IP + global
  brute-force lockout (`admin_attempts` collection, 5/20 attempts, 15 min).

## Key DB collections
- `bot_events`, `bot_postbacks`, `bot_clicks`, `bot_settings` (`_id="config"`),
  `bot_meta` (`_id="runtime"`), `admin_attempts`.

## Production
- Deployed at https://traffic-to-account.emergent.host — separate DB from
  preview. This is the environment that should receive all real ad traffic.
- User's TrafficStars campaign Target URL (as of 2026-08-08) is now set to:
  `https://traffic-to-account.emergent.host/api/r?click_id={click_id}`
  (previously it was misconfigured — either bypassing the bridge, or pointed
  at preview — causing "clickid is short" postback failures and split
  conversion counts between environments).

## Status as of 2026-08-08 (latest session)
- Bot uptime issue: FIXED (embedded poller + drop_pending_updates) — verified
  via testing_agent iterations 7, 9.
- Admin PIN mismatch: FIXED (pin_hash reset to match documented 246810,
  admin_attempts cleared) — verified iteration 8.
- Preview/production split-brain polling + conversion count mismatch: FIXED
  (BOT_POLLING_ENABLED) — verified iteration 10.
- Production confirmed healthy via direct curl check: online:true,
  postbacks_failed:0, bridge returning correct short-token redirects.
- All fixes tested via testing_agent_v4 (iterations 7-10), 95-100% pass rates,
  no unresolved regressions.

## Known non-issues (documented, not bugs)
- `/api/r` with no click_id falls back to a plain bot link (302) — intentional
  graceful degradation.
- `PUT /api/settings` is a full-replace, not a partial PATCH — by design.

## Backlog (not yet built)
- P1: "Bot Token" field in admin panel so friends can inject their own bot
  token from the UI (originally proposed, not yet confirmed/started).
- P2: Per-campaign/creative conversion breakdown on the dashboard.

## Update 2026-08-10: Broadcast feature added
- New `POST /api/admin/broadcast` (admin-JWT protected, multipart/form-data:
  `message` text + optional `file`) in `server.py`. Validates message-or-file
  required, 45MB file size cap, maps bot-not-running to a clean 503.
- New `bot.broadcast_message(text, media)` in `bot.py`: sends to every
  user_id in `bot_users`; picks send_photo/send_video/send_document by
  content-type; uploads the file once and reuses the returned `file_id` for
  all other recipients (avoids re-uploading large media per user); per-user
  try/except so one blocked/failed user doesn't abort the batch; small sleep
  between sends to respect Telegram rate limits; returns {sent, failed, total}.
- Frontend: new "// broadcast to all users" section added directly inside the
  existing admin ConfigPanel (no new page) — message textarea, attach
  photo/video/any-file button, filename chip with remove, send button
  (disabled until message or file present), inline result/error status that
  does not clear the draft on failure.
- Tested via testing_agent iteration 11: 100% pass (12/12 backend, all
  frontend UI/UX). Actual message delivery could only be verified via a
  mocked unit test in preview (preview doesn't run the live poller by
  design — see BOT_POLLING_ENABLED note above); real send must be verified
  on production directly.

## Update 2026-08-10 (major): Long-polling -> Webhook migration
- ROOT CAUSE of production outage: redeploying this app copies backend/.env
  verbatim into production. The earlier BOT_POLLING_ENABLED="false" fix
  (meant for preview only) got carried into production too, disabling the
  bot in BOTH environments simultaneously (confirmed via deployment_agent).
- PERMANENT FIX: replaced Telegram long-polling entirely with **webhooks**.
  - `bot.init_bot()` runs unconditionally at startup in every environment —
    it only initializes the bot client (no getUpdates call), so it's safe to
    run in both preview and production at once, no conflict possible.
  - New `POST /api/admin/telegram/activate` (admin-JWT protected): derives
    the caller's own public URL from the incoming request's Host header (NOT
    from .env — immune to future .env-sync issues) and calls Telegram's
    `setWebhook` pointing at itself. Setting a webhook always atomically
    replaces whichever URL was previously registered, so exactly one
    environment is ever "live" — no race condition on restarts/redeploys.
  - New public `POST /api/telegram/webhook/{secret}` receives updates,
    validated by both a path secret AND Telegram's `X-Telegram-Bot-Api-
    Secret-Token` header (`TELEGRAM_WEBHOOK_SECRET` in .env).
  - `GET /api/bot/status` now returns `is_live_here` / `webhook_active`
    instead of the removed `polling_enabled`.
  - Frontend: amber "[ not live here ]" pill + "[ make this the live bot ]"
    button (admin-only) appears whenever the current environment isn't the
    active webhook target; clicking it activates it immediately.
  - `broadcast_message()` now works in ANY environment (no longer tied to
    which one is polling), since sending messages is a plain outbound API
    call independent of webhook/polling mode.
- Verified via testing_agent iteration 12: 100% pass (13/13 backend +
  full frontend activation flow), no bugs found.
- **ACTION REQUIRED FROM USER (one-time, only they can do this):** after
  redeploying this code to production, open the production dashboard
  (https://traffic-to-account.emergent.host), unlock with the admin PIN, and
  click "[ make this the live bot ]" ONE TIME. This makes production the
  permanent live receiver — it will NOT be affected by any future redeploy
  or by preview's state, since Telegram's webhook registration lives on
  Telegram's servers, not in our .env.

## Update 2026-08-10: Broadcast Cloudflare 524 timeout fixed
- Bug: sending a broadcast to production's ~770 users took minutes (one
  Telegram API call per user, sequentially), exceeding the platform's
  gateway timeout -> Cloudflare 524 "origin did not respond".
- Fix: `POST /api/admin/broadcast` now validates + queues the send via
  FastAPI `BackgroundTasks` and responds in <1s with `{queued: true,
  total: N}`. The actual send loop runs after the response, writing final
  `{sent, failed, total, completed_at}` to `bot_meta.runtime.last_broadcast`
  (surfaced in `GET /api/bot/status`). Frontend shows "queued — sending to
  N users now" immediately instead of waiting for completion.
- Verified via testing_agent iteration 13: 100% pass, no regressions.
- Confirmed production is now fully live post-webhook-migration + user's
  manual activation: is_live_here:true, 823 postbacks sent / only 1 failed
  out of 924 total conversions.

## Update 2026-08-10: Broadcast rewritten for 100k-scale + crash resilience
- User asked for: broadcast up to 100,000 users, 50MB file support, and the
  bot must never stop (even mid-update). Researched and confirmed Telegram's
  Bot API hard-caps bot-sent files at 50MB (150MB would need a separate
  self-hosted "Local Bot API Server" — user chose not to pursue that;
  50MB kept as the real, final limit).
- Broadcast is now a persistent, resumable JOB (new `bot_broadcasts`
  collection: {text, media_kind/path/filename/file_id, total, sent, failed,
  processed_user_ids: [], status, created_at, completed_at}) instead of a
  one-shot in-memory send:
  - `POST /api/admin/broadcast` saves any uploaded file to disk, inserts a
    job doc, launches `asyncio.create_task(run_broadcast_job)`, returns
    instantly with `{queued, job_id, total}`.
  - `run_broadcast_job()` sends with bounded concurrency (semaphore=12,
    batches of 300), checkpointing sent/failed/processed_user_ids after each
    batch, handles Telegram `RetryAfter` rate-limit responses with a sleep
    + one retry. Uploads media once, reuses the returned `file_id` for
    everyone else. Deletes the temp file once the whole job is done.
  - **Resumable across restarts**: on backend startup,
    `resume_pending_broadcasts()` finds any job still `status="running"`
    (meaning the process died/redeployed mid-send) and automatically
    relaunches it, skipping everyone already in `processed_user_ids` — no
    duplicate messages, nobody missed. Independently verified twice (main
    agent + testing_agent iteration 14): simulated a restart mid-job with
    27/29 users pre-processed, confirmed it resumed and finished with
    exactly 29 unique processed entries, no duplicates.
  - New `GET /api/admin/broadcast/latest` (admin-protected) for progress
    polling; frontend polls it every 3s after queuing, showing
    "sending... X/Y" then "done — sent X/Y", stops polling once done.
- Verified via testing_agent iteration 14: 100% pass (12/12 backend +
  frontend), no bugs found.
- Clarified "never stop the bot" scope: already true for all admin panel
  actions (settings, PIN, broadcast — pure DB writes, no restart). The only
  unavoidable gap is a few seconds during an actual code deploy, which
  Telegram's webhook retry mechanism already covers.

## Update 2026-08-10: Broadcast failure diagnostics
- User saw a production broadcast finish with 208/810 failed and asked "what
  happened" -- the reason was only in ephemeral server logs, unreachable on a
  deployed environment, so there was no way to explain it.
- Fix: `_send_to_user()` now categorizes each failure (`blocked_bot`,
  `invalid_chat`, `rate_limited`, `network_timeout`, `other`) instead of just
  ok/not-ok. `run_broadcast_job()` accumulates these into an `error_counts`
  dict on the job doc (checkpointed per batch via `$inc`), exposed in
  `GET /api/admin/broadcast/latest`. Frontend shows a friendly breakdown,
  e.g. "done — sent 602/810 — 208 failed (190 blocked the bot, 18 chat not
  found)". A high block-rate on a redirect-only bot is normal audience
  attrition, not a bug — this makes that visible/explainable going forward.
- Verified via testing_agent iteration 15: 100% pass (4/4 pytest + frontend
  UI), invariant `sum(error_counts) == failed` holds for text-only and
  media-attached broadcasts alike.

## Update 2026-08-10: Broadcast skips blocked/deleted users
- User asked: avoid broadcasting to users who deleted their account or
  blocked the bot.
- `_mark_blocked(uid, reason)` in bot.py sets `{blocked: true,
  blocked_reason, blocked_at}` on a user's `bot_users` doc whenever a send
  fails with category `blocked_bot` (Forbidden) or `invalid_chat`
  (BadRequest) — NOT for transient categories (rate_limited,
  network_timeout, other), which shouldn't permanently exclude someone.
- `run_broadcast_job()`'s recipient query and `POST /api/admin/broadcast`'s
  upfront `total` count both now filter `blocked: {$ne: true}`, so future
  broadcasts skip known-unreachable users automatically (and the admin sees
  an accurate expected-recipient count before sending).
- Self-healing: `track()` sets `blocked: false` on every interaction — if a
  previously-blocked user starts the bot again, they're automatically
  eligible for broadcasts again.
- `GET /api/bot/status` adds `blocked_users` count; dashboard's "unique
  users" stat shows a small "N blocked" sub-label.
- Verified via testing_agent iteration 16: 100% pass, confirmed end-to-end
  (58 users → 18 blocked after one broadcast → next broadcast's total
  correctly dropped to 40 → completed with 0 failures; un-block-on-interact
  confirmed via simulated webhook /start from a blocked user id).

## Update 2026-09-02: K8s deploy hardening + Object Storage for broadcast files
- `bot.init_bot()` startup call in `server.py` is wrapped in try/except so a
  bad/revoked Telegram token no longer crashes the whole backend — bridge,
  admin panel and settings API stay up even if the bot client fails to init.
- Broadcast file uploads moved from local pod disk to Emergent Object
  Storage (`_init_storage()` / `put_broadcast_file()` in bot.py) — local
  disk isn't reliable/persistent in a deployed pod.

## Update 2026-09-02: Broadcast History UI (P1 backlog item, done)
- New `GET /api/admin/broadcast/history` (admin-JWT protected): last 20
  `bot_broadcasts` jobs, newest first — id, text_preview (80 chars),
  media_filename, status, sent, failed, total, error_counts, created_at,
  completed_at. Uses `.get()` with defaults so a legacy/partial doc can't
  500 the endpoint.
- Frontend: `ConfigPanel.js` "broadcast history" collapsible section below
  the existing broadcast form — expandable rows show failure breakdown per
  job; stacks vertically on mobile widths. New `broadcastHistory()` helper
  in `App.js` wired as `onBroadcastHistory` prop.
- Verified via testing_agent iteration 17: 100% pass (9/9 backend incl.
  auth/shape/ordering, full frontend flow desktop+mobile). Fixed post-test:
  a JSX literal-escape cosmetic bug (`\u00b7` rendered as literal text
  instead of a middot), missing error state (API failure now shows
  `broadcast-history-error` instead of silently looking like "no
  broadcasts"), and updated `test_credentials.md` bot handle
  (`@tamil_best_service_bot` -> `@TRUSTEDNO1_BOT`).

## Known housekeeping (not a product bug, not yet done)
- `/app/backend/tests/backend_test.py` has ~9 stale assertions from the
  pre-webhook/pre-rename era (old bot username, polling-based checks). Not
  fixed this session (test-hygiene only, zero user impact) — prune/update
  next time that file needs touching.

## Update 2026-09-02: Fixed raw 500 on "make this the live bot" + surfaced real cause
- Bug report: clicking "[ make this the live bot ]" on **production** showed
  "REQUEST FAILED WITH STATUS CODE 500" and the header still showed the old
  bot handle `@tamil_best_service_bot` with a "[ not live here ]" pill.
- Root cause: production's `bot_status`/header shows `bot_meta.runtime.
  bot_username`, a value CACHED from the last time `init_bot()` succeeded
  there — meaning production's current `TELEGRAM_BOT_TOKEN` is invalid/
  stale (the token swap to `@TRUSTEDNO1_BOT` was only ever applied to
  preview's `.env`; production's `.env` is separate and not editable by
  this agent). Because the token is invalid, `init_bot()` fails at startup
  (caught gracefully — no crash, per the K8s fix) so `_app` stays `None`.
  `POST /api/admin/telegram/activate` then called `bot_module.
  activate_webhook()` which raised a raw `RuntimeError` when `_app is
  None` — uncaught, so FastAPI returned a bare 500 instead of an
  explanatory error.
- Fix (code, done): `/api/admin/telegram/activate` now checks `bot_module.
  is_ready()` first and returns a clean `503` with detail "Bot isn't
  initialized in this environment — TELEGRAM_BOT_TOKEN is likely missing
  or invalid here..." instead of an unhandled 500.
- **ACTION REQUIRED FROM USER**: this code fix only makes the error
  message clear — it does NOT fix production's actual bot connectivity.
  On production: update `TELEGRAM_BOT_TOKEN` to the current
  `@TRUSTEDNO1_BOT` token in production's environment variables, redeploy/
  restart, then click "[ make this the live bot ]" again from the
  production dashboard.

## Update 2026-09-03: Production incident — bot fully down, resolution path given
- User reported "bot not working" on real ad traffic. Confirmed via direct
  curl to production (`https://traffic-to-account.emergent.host/api/bot/
  status`): `online:false`, `webhook_active:false`, stale
  `bot_username:"tamil_best_service_bot"` despite 9,386 real users /
  11,966 historical starts in production's DB — production's
  `TELEGRAM_BOT_TOKEN` was never updated after the `@TRUSTEDNO1_BOT` swap
  (only preview's `.env` got it), and that old bot's Telegram account was
  later deleted by the user, so there's no way back to it.
- User decision (confirmed): abandon the old bot/history entirely, run
  production on `@TRUSTEDNO1_BOT` starting from zero users. User still
  needs to: update `TELEGRAM_BOT_TOKEN` in production's deployment
  settings (Home tab → deployment → env vars, per support_agent), redeploy,
  then click "[ make this the live bot ]" on the production dashboard.
- Separately diagnosed (via support_agent): user had pointed real
  TrafficStars ad traffic at **preview**, not production. Preview sleeps
  when idle (by design, tied to the agent chat session) — this, not a code
  bug, was why the bot "only worked when I opened Emergent". Real traffic
  must always target the production deployment, never preview.

## Update 2026-09-03: Advertisement image on /start (new feature)
- User request (Tamil): send an ad-style image as its own message right
  before the existing bilingual welcome text + contact button on every
  /start, with an admin-panel upload option.
- Backend: `POST /api/admin/welcome-image` (admin-JWT, multipart `file`,
  image/* only, 10MB cap) uploads to Object Storage
  (`trafficstars-bot/welcome_image/...`, reusing the existing
  put_broadcast_file/get_broadcast_file helpers) and stores
  `welcome_image_path`/`welcome_image_content_type` on the settings doc.
  `DELETE /api/admin/welcome-image` unsets it. `GET /api/welcome-image`
  (public) streams the bytes for both the admin-panel preview and is not
  used by the bot itself (bot reads object storage directly). `GET
  /api/settings` now exposes `has_welcome_image` (never leaks the raw
  storage path).
- `bot.py`'s `reply()` now sends `reply_photo()` first (if an image is
  set) then the existing `reply_text()` welcome+button — as two separate
  messages (Telegram photo captions cap at 1024 chars, too short for the
  combined bilingual text), with graceful fallback to text-only if the
  image fetch fails.
- Frontend: new "// advertisement image" section in `ConfigPanel.js`
  (preview always visible; upload/change/remove controls only when
  unlocked).
- Verified via testing_agent iteration 18: 100% pass (14/14 backend incl.
  bot.reply() ordering unit tests, full frontend upload→preview→reload→
  remove cycle). Fixed 2 minor post-test polish items: `GET
  /api/welcome-image` now returns a clean 502 instead of a raw 500 if
  object storage is unreachable; the upload/remove status message no
  longer auto-clears on error (still auto-clears after 4s on success).

## Update 2026-09-03: Fixed "Save Payload" failing with a username error
- Bug report: saving the welcome text (Tamil/English) failed with
  "Telegram username must be 5-32 characters..."; ALSO the reverse —
  saving the account username failed with "Both the Tamil and English
  blocks are required." Neither field was touched by the user in either
  case — the two saves were cross-contaminating each other's validation.
- Root cause: `PUT /api/settings` was a full-replace by design — every
  save (whether editing welcome text or the account username) had to
  resend + re-validate ALL THREE fields together. The frontend
  reconstructed the "unchanged" fields from local React `settings` state,
  which could occasionally be stale/incomplete, so an edit to one field
  could send an invalid/empty value for a field the user never touched,
  and the resulting error message was about the WRONG field — confusing
  and, worse, blocking legitimate saves.
- **Permanent fix (not a patch): `PUT /api/settings` is now a true partial
  update.** `SettingsUpdate`'s fields are all `Optional` — only fields
  actually present in the request body are validated and written;
  anything omitted is left completely untouched in the DB, no
  reconstruction needed. `App.js`'s `saveSettings()` now sends only the
  patch object directly (no more merging with local state). Verified via
  curl both directions: sending only `welcome_ta`/`welcome_en` leaves
  `official_username` byte-for-byte unchanged, and sending only
  `official_username` leaves `welcome_ta`/`welcome_en` byte-for-byte
  unchanged. This eliminates the entire bug class, not just one direction
  of it.

## Update 2026-09-03: Independent stats reset (Conversions / Unique Users / Daily)
- User request: separately resettable Conversions, Unique Users, and
  Daily counters on the dashboard to "start fresh" whenever needed.
- `POST /api/admin/stats/reset` (admin-JWT, body `{metric}`) is a SOFT
  reset — stores a cutoff ISO timestamp per metric in
  `bot_settings.config.stats_reset`, never deletes any `bot_events`/
  `bot_users`/`bot_postbacks` documents. `GET /api/bot/status` filters its
  counts by that cutoff per metric. Resetting "conversions" zeroes
  total_starts/attributed/unattributed/postbacks_sent/failed AND today's
  count (today can't exceed the lifetime total post-reset — intentional).
  Resetting "unique_users" zeroes unique_users/blocked_users only.
  Resetting "daily" zeroes only today's count. Each is otherwise fully
  independent of the other two.
- Frontend: each stat card (`stat-starts`/`stat-users`/`stat-today`) has a
  reset control visible only when unlocked — two-step confirm (click →
  red "confirm?" for ~4s → click again to actually reset), auto-cancels
  if not confirmed in time.
- Verified via testing_agent iteration 20: 100% pass (8/8 backend incl.
  independence + non-destructive doc-count checks + post-reset new-
  activity simulation; full frontend confirm/cancel/reset flow).
