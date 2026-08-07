# Telegram Airdrop Post Bot

A Telegram bot that accepts an airdrop link, generates a professional post with
Gemini 2.5 Flash, and publishes it to your channel.

## Files
- `app.py` — Flask entrypoint. **This is what Render/gunicorn runs.** Starts the
  Telegram bot in the background when the process boots.
- `runner.py` — Manages the bot's async lifecycle safely on a background thread
  (`initialize → start → updater.start_polling`, and the matching shutdown).
- `bot.py` — Bot handlers and `Application` setup (unchanged).
- `ai.py` — Gemini post generation (unchanged).
- `config.py` — Environment variable loading/validation (unchanged).
- `main.py` — Optional standalone runner for local testing *without* Flask.
  Not used on Render.
- `requirements.txt`, `render.yaml`, `runtime.txt`, `.env.example`

## Architecture (why it's structured this way)

Render's **Free Web Service** plan needs a single process bound to `$PORT` — it
does not support Background Worker services on the free tier. That process here
is Flask, run via gunicorn. The Telegram bot needs to run *inside* that same
process, so `app.py` starts it on a dedicated background thread as soon as the
module is imported.

`python-telegram-bot`'s `Application.run_polling()` is convenient, but it also
registers OS signal handlers (`signal.signal`, `signal.set_wakeup_fd`) for
graceful Ctrl+C/SIGTERM shutdown — and Python only allows registering signal
handlers on the **main thread of the main interpreter**. Since gunicorn owns the
main thread and the bot has to run on a background thread alongside it, calling
`run_polling()` there crashes with:

```
RuntimeError: set_wakeup_fd only works in main thread of the main interpreter
```

`runner.py` avoids this entirely by using the manual PTB v22 lifecycle instead:

```python
await application.initialize()
await application.start()
await application.updater.start_polling(...)
# ... running ...
await application.updater.stop()
await application.stop()
await application.shutdown()
```

This never touches signal handlers, so it's safe to run from a background
thread. The thread gets its own `asyncio` event loop (`asyncio.new_event_loop()`),
kept alive with `loop.run_forever()`, and is stopped cleanly via
`loop.call_soon_threadsafe(loop.stop)` on process exit (`atexit`).

`start_bot_in_background()` is idempotent and guarded by a lock, so only one
`Application` instance is ever created per process. Combined with gunicorn's
default of **1 worker** for the required `gunicorn app:app` command (no
`--workers` flag), this guarantees exactly one bot polling instance overall —
running more than one worker/process would make Telegram reject the extra
`getUpdates` calls with a "terminated by other getUpdates request" conflict, so
don't add `--workers` > 1 to the start command.

## Deploy on Render (Free Web Service)

1. Push this repo to GitHub.
2. In Render, **New → Blueprint**, point it at the repo — `render.yaml` sets it
   up as a Web Service on the free plan automatically.
3. Set the env vars marked `sync: false` in the Render dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`
   - `GEMINI_API_KEY`
   - `ALLOWED_USER_IDS`
4. Start command (fixed, required): `gunicorn app:app`
5. Deploy, then check the **Logs** tab for `Telegram bot polling started.` to
   confirm the bot came up alongside Flask.

If setting the service up manually instead of via Blueprint: **Web Service**,
runtime Python 3, build command `pip install -r requirements.txt`, start command
`gunicorn app:app`, same env vars as above.

### Free plan note

Render's free Web Services spin down after a period of inactivity and spin back
up on the next incoming HTTP request. Since the bot uses polling (not a
webhook), it needs the process to stay alive to keep receiving Telegram
updates — a free-tier idle spin-down will pause polling until Render wakes the
service back up on the next request. This is a platform limitation of the free
plan, not a bug in this code; if you need guaranteed always-on polling, a paid
plan (or switching the bot to a webhook endpoint on this same Flask app) avoids
it. Pinging `/` periodically from an external uptime monitor is a common
workaround to keep a free service awake.

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your values

# Option A: run exactly like production (Flask + bot together)
python app.py

# Option B: run just the bot, no Flask, for quick iteration
python main.py
```

## Troubleshooting

- **`Missing required environment variables`** — check `.env` (local) or the
  Render dashboard (prod) against `.env.example`.
- **`set_wakeup_fd only works in main thread`** — this is fixed by the
  `runner.py` lifecycle above; if you see it again, something is calling
  `Application.run_polling()` from a non-main thread — don't call it from
  inside `app.py`, only `main.py` (standalone) should ever use it.
- **Telegram "terminated by other getUpdates request" conflict** — more than
  one bot instance is polling with the same token at once. Make sure the
  gunicorn start command has no `--workers` flag greater than 1, and that
  you don't also have `main.py` running somewhere at the same time.
- **`/health` shows `bot_running: false`** — check the logs right after
  startup; `app.py` logs the bot's startup exception (e.g. a bad
  `TELEGRAM_BOT_TOKEN`) without crashing the Flask process.
