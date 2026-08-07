"""
main.py
Standalone entrypoint for local development/testing WITHOUT Flask/gunicorn.

Not used on Render - Render's start command is `gunicorn app:app`
(see app.py + runner.py for the production lifecycle used there).

This runs in the main thread of the main interpreter, so it's safe to
call Application.run_polling() directly here: its signal-handler setup
(the same thing that crashes with "set_wakeup_fd only works in main
thread" when called from a background thread) works fine on the main
thread, which is exactly where this executes.
"""

from __future__ import annotations

import logging
import sys

from bot import create_application

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting Telegram Airdrop Bot (standalone polling mode)...")
    application = create_application()
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
    )


if __name__ == "__main__":
    main()
