"""
runner.py
Manages the Telegram bot Application lifecycle on a dedicated background
thread with its own asyncio event loop, using the manual python-telegram-bot
v22 lifecycle:

    initialize() -> start() -> updater.start_polling()
    ... running ...
    updater.stop() -> stop() -> shutdown()

instead of Application.run_polling().

WHY NOT run_polling():
Application.run_polling() installs OS signal handlers (signal.signal /
signal.set_wakeup_fd) so that Ctrl+C / SIGTERM stop it gracefully. Signal
handlers can only be registered on the main thread of the main interpreter.
Under gunicorn, the Flask app runs as the "main" object of a worker process,
but any thread we spawn from it (to run the bot alongside Flask) is NOT
that main thread - so calling run_polling() there raises:

    RuntimeError: set_wakeup_fd only works in main thread of the main interpreter

The manual lifecycle below never touches signal handlers at all, so it is
safe to run from a background thread.

SINGLE INSTANCE GUARANTEE:
Only one Application is ever created per process, guarded by a lock, and
start_bot_in_background() is idempotent (safe to call more than once).
Combined with gunicorn's default of 1 worker for `gunicorn app:app` (no
--workers flag), this guarantees exactly one bot polling instance overall.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from telegram.ext import Application

from bot import create_application

logger = logging.getLogger(__name__)

_application: Optional[Application] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


async def _start(application: Application) -> None:
    """Manual PTB v22 startup lifecycle (no signal handlers touched)."""
    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
    )
    logger.info("Telegram bot polling started.")


async def _stop(application: Application) -> None:
    """Manual PTB v22 shutdown lifecycle, mirroring _start()."""
    logger.info("Stopping Telegram bot...")
    try:
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()
        logger.info("Telegram bot stopped cleanly.")
    except Exception:
        logger.exception("Error while stopping the Telegram bot")


def _thread_main(application: Application) -> None:
    """Entry point for the background thread: owns its own event loop for
    the entire lifetime of the bot, so nothing here ever touches the
    process-wide/main-thread event loop that gunicorn or Flask may use."""
    global _loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _loop = loop

    try:
        loop.run_until_complete(_start(application))
    except Exception:
        logger.exception("Telegram bot failed to start")
        loop.close()
        return

    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(_stop(application))
        loop.close()
        logger.info("Telegram bot event loop closed.")


def start_bot_in_background() -> None:
    """Create the single Application instance and start polling on a
    dedicated background thread. Safe to call multiple times: only the
    first call (per process) has any effect."""
    global _application, _thread

    with _lock:
        if _thread is not None and _thread.is_alive():
            logger.warning("Telegram bot already running; ignoring duplicate start request.")
            return

        _application = create_application()
        _thread = threading.Thread(
            target=_thread_main,
            args=(_application,),
            name="telegram-bot-loop",
            daemon=True,
        )
        _thread.start()
        logger.info("Telegram bot thread launched.")


def stop_bot_gracefully(timeout: float = 10.0) -> None:
    """Stop the bot's event loop and wait for the background thread to exit.
    Safe to call even if the bot never started."""
    global _thread

    if _loop is None or not _loop.is_running():
        return

    _loop.call_soon_threadsafe(_loop.stop)

    if _thread is not None:
        _thread.join(timeout=timeout)
        _thread = None


def is_running() -> bool:
    return (
        _application is not None
        and _application.updater is not None
        and _application.updater.running
    )
