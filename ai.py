"""
AI module – generates professional Telegram airdrop posts using Gemini 2.5 Flash.
"""

from __future__ import annotations

import logging
from google import genai
from google.genai import types

from config import Config

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are an expert crypto community manager and professional Telegram content writer.
Your task is to turn an airdrop / claim link into a polished, engaging Telegram channel post.

Rules:
- Write in a professional yet exciting tone suitable for a crypto Telegram channel.
- Structure the post clearly with short paragraphs and emojis (use sparingly and tastefully).
- Always include the original link provided by the user, preferably near a clear call-to-action.
- Highlight potential benefits (tokens, rewards, eligibility) when reasonable, but never invent specific numbers, dates or guarantees.
- Keep the post concise (ideally under 400 words).
- Use Markdown-compatible formatting that works well in Telegram (bold with *text*, italic with _text_).
- Do NOT add hashtags unless they feel natural.
- Do NOT include any disclaimer about being AI-generated.
- Output ONLY the final post text – no explanations, no quotes, no extra commentary.
"""


def _get_client() -> genai.Client:
    return genai.Client(api_key=Config.GEMINI_API_KEY)


async def generate_airdrop_post(airdrop_link: str) -> str:
    client = _get_client()

    prompt = (
        f"Create a professional Telegram channel post announcing this airdrop.\n\n"
        f"Airdrop / claim link: {airdrop_link}\n\n"
        f"Make it ready to publish."
    )

    try:
        response = await client.aio.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.7,
                top_p=0.95,
                max_output_tokens=1024,
            ),
        )

        text = (response.text or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty response")

        logger.info("Successfully generated airdrop post (%d chars)", len(text))
        return text

    except Exception as exc:
        logger.exception("Failed to generate post with Gemini")
        raise RuntimeError(f"AI generation failed: {exc}") from exc
