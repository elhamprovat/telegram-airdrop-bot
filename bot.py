"""
Telegram bot handlers and application setup.
Uses python-telegram-bot v22 (fully async).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from telegram import Update, Message
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import Config
from ai import generate_airdrop_post

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.?=&%#]*",
    re.IGNORECASE,
)


def _is_authorized(user_id: Optional[int]) -> bool:
    if not Config.ALLOWED_USER_IDS:
        return True
    return user_id is not None and user_id in Config.ALLOWED_USER_IDS


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    if not _is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    await update.message.reply_text(
        "👋 *Airdrop Post Bot*\n\n"
        "Send me an airdrop / claim link and I will:\n"
        "1. Generate a professional Telegram post with Gemini 2.5 Flash\n"
        "2. Publish it automatically to your channel\n\n"
        "Just paste the link here.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "📌 *How to use*\n\n"
        "1. Send any airdrop / claim URL.\n"
        "2. The bot generates a polished post.\n"
        "3. The post is automatically sent to the configured channel.\n\n"
        "Commands:\n"
        "/start – Welcome message\n"
        "/help – This help\n"
        "/status – Quick health check",
        parse_mode=ParseMode.MARKDOWN,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    if not _is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    channel = Config.TELEGRAM_CHANNEL_ID
    await update.message.reply_text(
        f"✅ Bot is running\n"
        f"📢 Channel: `{channel}`\n"
        f"🤖 Model: `{Config.GEMINI_MODEL}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _publish_to_channel(context: ContextTypes.DEFAULT_TYPE, post_text: str) -> None:
    """Publish the generated post to the channel.

    Gemini is asked for Markdown-formatted text, but AI-generated Markdown can
    end up with unbalanced/malformed entities (e.g. a stray '*' or '_') that
    Telegram's legacy Markdown parser rejects with "Can't parse entities".
    Rather than losing the post over a formatting glitch, retry once as plain
    text (no parse_mode) so publishing still succeeds.
    """
    try:
        await context.bot.send_message(
            chat_id=Config.TELEGRAM_CHANNEL_ID,
            text=post_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
    except BadRequest as parse_exc:
        if "can't parse entities" not in str(parse_exc).lower():
            raise
        logger.warning(
            "Markdown parsing failed for generated post; resending as plain text: %s",
            parse_exc,
        )
        await context.bot.send_message(
            chat_id=Config.TELEGRAM_CHANNEL_ID,
            text=post_text,
            parse_mode=None,
            disable_web_page_preview=False,
        )


async def handle_airdrop_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message: Optional[Message] = update.message
    if not message or not message.text or not update.effective_user:
        return

    user_id = update.effective_user.id
    if not _is_authorized(user_id):
        await message.reply_text("⛔ You are not authorized to use this bot.")
        return

    text = message.text.strip()
    match = URL_PATTERN.search(text)
    if not match:
        await message.reply_text(
            "❌ Please send a valid airdrop / claim URL (starting with http:// or https://)."
        )
        return

    airdrop_link = match.group(0)

    status_msg = await message.reply_text("⏳ Generating professional post with Gemini…")
    await context.bot.send_chat_action(
        chat_id=message.chat_id, action=ChatAction.TYPING
    )

    try:
        post_text = await generate_airdrop_post(airdrop_link)

        await _publish_to_channel(context, post_text)

        await status_msg.edit_text(
            "✅ Post generated and published to the channel successfully!"
        )
        logger.info(
            "Published airdrop post for user %s | link=%s",
            user_id,
            airdrop_link,
        )

    except Exception as exc:
        logger.exception("Failed to process airdrop link")
        await status_msg.edit_text(
            f"❌ Failed to generate or publish the post.\n\nError: {exc}"
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Please try again later."
            )
        except Exception:
            pass


def create_application() -> Application:
    Config.validate()

    application = (
        Application.builder()
        .token(Config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_airdrop_link)
    )

    application.add_error_handler(error_handler)

    return application
