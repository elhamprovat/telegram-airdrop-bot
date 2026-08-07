"""
app.py
Flask entrypoint for Render Web Service.

Render start command (unchanged, as required): gunicorn app:app

The Telegram bot itself runs on a dedicated background thread with its
own asyncio event loop (see runner.py), started exactly once when this
module is imported by gunicorn. This keeps a single long-lived HTTP
process bound to $PORT (what Render's free Web Service tier needs) while
the bot polls Telegram independently in the background.

Only one worker should ever run this app (gunicorn's default is already
1 worker when no --workers flag is passed, which is the case for the
required `gunicorn app:app` command) - running more than one worker would
start more than one bot polling instance and Telegram would reject the
extra getUpdates calls with a "terminated by other getUpdates request"
conflict.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys

from flask import Flask, jsonify

from runner import is_running, start_bot_in_background, stop_bot_gracefully

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "Telegram Airdrop Bot is Running ✅"


@app.route("/health")
def health():
    return jsonify(bot_running=is_running())


# Start the Telegram bot exactly once when this module is imported.
# Wrapped in try/except so a bot/config problem (e.g. a missing env var)
# never prevents Flask itself from binding to $PORT - Render still sees a
# healthy web process, and /health reports the bot as not running so the
# issue is easy to spot in the logs and in that endpoint.
try:
    start_bot_in_background()
except Exception:
    logger.exception("Telegram bot failed to start; Flask will still serve requests.")

atexit.register(stop_bot_gracefully)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
