"""
scraper.py
Smart, resilient web scraper used to turn any public project link into
structured, factual data for the AI post generator.

Supported input categories (auto-detected from the URL):
    telegram_bot, telegram_group, telegram_channel, telegram_post,
    twitter, discord, medium, github, coinmarketcap, coingecko,
    whitepaper_or_docs, website

Design goals (per project spec):
    - Never hallucinate: every field returned here comes directly from the
      page's own HTML (OpenGraph tags, <title>, <meta name="description">,
      visible text, favicon, logo). If a field can't be found, it's simply
      left as None / empty - it is ai.py's job to render that as
      "Not specified", never to invent a value.
    - Never crash: every network call and parse step is wrapped so a single
      broken/unreachable site can never take down the bot. All failures are
      logged and degrade gracefully to partial data.
    - Lightweight: uses `requests` + `BeautifulSoup` (html.parser, no lxml
      dependency) instead of a headless browser. Render's free Web Service
      tier has no Chromium/Playwright browser binaries installed and very
      limited memory/build minutes, so a full browser automation stack
      (Playwright/Selenium) is intentionally avoided here to keep the
      existing `gunicorn app:app` deployment fast, small and reliable.
      Nearly all of the target sites (Telegram, X/Twitter, Discord,
      Medium, GitHub, CoinGecko, CoinMarketCap, generic websites) server-
      render OpenGraph/meta tags for link-preview/SEO purposes, so this
      approach covers the vast majority of real-world links.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_VISIBLE_TEXT_CHARS = 6000

URL_PATTERN = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.?=&%#~+:]*",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# URL detection
# --------------------------------------------------------------------------

def find_url(text: str) -> Optional[str]:
    """Return the first URL found in a block of text, or None."""
    if not text:
        return None
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def detect_category(url: str) -> str:
    """Classify a URL into one of the supported project-link categories.
    Best-effort heuristic - never raises."""
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().lstrip("www.")
        path = (parsed.path or "").strip("/")
        segments = [s for s in path.split("/") if s]

        if host in ("t.me", "telegram.me", "telegram.dog"):
            first = segments[0] if segments else ""
            if first in ("joinchat",) or first.startswith("+"):
                return "telegram_group"
            if len(segments) >= 2 and segments[1].isdigit():
                return "telegram_post"
            if first.lower().endswith("bot"):
                return "telegram_bot"
            if first:
                return "telegram_channel"
            return "telegram_channel"

        if host in ("twitter.com", "x.com", "mobile.twitter.com"):
            return "twitter"

        if host in ("discord.gg",) or (host == "discord.com" and segments[:1] == ["invite"]):
            return "discord"

        if host.endswith("medium.com"):
            return "medium"

        if host == "github.com":
            return "github"

        if host.endswith("coinmarketcap.com"):
            return "coinmarketcap"

        if host.endswith("coingecko.com"):
            return "coingecko"

        if "whitepaper" in host or "whitepaper" in path.lower() or "docs" in host.split(".")[0:1]:
            return "whitepaper_or_docs"
        if path.lower().startswith("docs") or "/docs" in path.lower():
            return "whitepaper_or_docs"

        return "website"
    except Exception:
        logger.exception("Failed to classify URL: %s", url)
        return "website"


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch_html(url: str) -> Optional[str]:
    """Fetch raw HTML for a URL. Returns None (never raises) on any failure."""
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            # Still try to parse - some servers omit/mislabel content-type.
            pass
        resp.encoding = resp.encoding or "utf-8"
        return resp.text
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


# --------------------------------------------------------------------------
# Extraction helpers (all pure, all defensive)
# --------------------------------------------------------------------------

def _abs_url(base_url: str, maybe_relative: Optional[str]) -> Optional[str]:
    if not maybe_relative:
        return None
    try:
        return urljoin(base_url, maybe_relative.strip())
    except Exception:
        return None


def extract_opengraph(soup: BeautifulSoup, base_url: str) -> dict:
    """Pull OpenGraph / Twitter-card / plain meta tags. Missing fields are None."""
    data = {
        "title": None,
        "description": None,
        "image": None,
        "site_name": None,
    }
    try:
        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        og_image = soup.find("meta", property="og:image")
        og_site = soup.find("meta", property="og:site_name")

        tw_title = soup.find("meta", attrs={"name": "twitter:title"})
        tw_desc = soup.find("meta", attrs={"name": "twitter:description"})
        tw_image = soup.find("meta", attrs={"name": "twitter:image"})

        meta_desc = soup.find("meta", attrs={"name": "description"})
        title_tag = soup.find("title")

        def content(tag):
            return tag.get("content").strip() if tag and tag.get("content") else None

        data["title"] = content(og_title) or content(tw_title) or (
            title_tag.get_text(strip=True) if title_tag else None
        )
        data["description"] = content(og_desc) or content(tw_desc) or content(meta_desc)
        image_val = content(og_image) or content(tw_image)
        data["image"] = _abs_url(base_url, image_val) if image_val else None
        data["site_name"] = content(og_site)
    except Exception:
        logger.exception("Failed to extract OpenGraph data from %s", base_url)
    return data


def extract_favicon(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    try:
        for rel in ("icon", "shortcut icon", "apple-touch-icon"):
            link = soup.find("link", rel=lambda v: v and rel in v.lower())
            if link and link.get("href"):
                return _abs_url(base_url, link["href"])
        # Fallback to the conventional /favicon.ico location.
        return _abs_url(base_url, "/favicon.ico")
    except Exception:
        logger.exception("Failed to extract favicon from %s", base_url)
        return None


def extract_logo(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Best-effort search for a site logo image, used only as a fallback
    when no OpenGraph image is available (per the requested image priority
    order: OG image -> Telegram image -> Twitter image -> website logo ->
    favicon)."""
    try:
        candidates = soup.find_all("img", limit=200)
        for img in candidates:
            haystack = " ".join(
                filter(
                    None,
                    [
                        img.get("alt", ""),
                        img.get("id", ""),
                        " ".join(img.get("class", []) if isinstance(img.get("class"), list) else [img.get("class", "")]),
                        img.get("src", ""),
                    ],
                )
            ).lower()
            if "logo" in haystack:
                src = img.get("src") or img.get("data-src")
                if src:
                    return _abs_url(base_url, src)
        return None
    except Exception:
        logger.exception("Failed to extract logo from %s", base_url)
        return None


SOCIAL_DOMAIN_MAP = {
    "telegram": ("t.me", "telegram.me", "telegram.dog"),
    "twitter": ("twitter.com", "x.com"),
    "discord": ("discord.gg", "discord.com"),
    "medium": ("medium.com",),
    "github": ("github.com",),
}


def extract_outbound_links(soup: BeautifulSoup, base_url: str) -> dict:
    """Scan all <a href> tags on the page and bucket them into known social
    platforms, so e.g. a project website automatically surfaces its
    Telegram/Twitter/Discord/Medium/GitHub links."""
    found: dict = {}
    try:
        for a in soup.find_all("a", href=True, limit=500):
            href = _abs_url(base_url, a["href"])
            if not href:
                continue
            host = (urlparse(href).netloc or "").lower().lstrip("www.")
            for key, domains in SOCIAL_DOMAIN_MAP.items():
                if key in found:
                    continue
                if any(host == d or host.endswith("." + d) for d in domains):
                    found[key] = href
    except Exception:
        logger.exception("Failed to extract outbound links from %s", base_url)
    return found


def extract_visible_text(soup: BeautifulSoup) -> Optional[str]:
    """Grab a clean, truncated slab of visible body text for the AI to read
    factual details (token, chain, reward, tasks, referral, dates) from -
    never fabricated, only what's literally on the page."""
    try:
        body = soup.find("body")
        if not body:
            return None
        for tag in body.find_all(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = body.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None
        return text[:MAX_VISIBLE_TEXT_CHARS]
    except Exception:
        logger.exception("Failed to extract visible text")
        return None


# --------------------------------------------------------------------------
# Telegram-specific helper
# --------------------------------------------------------------------------

def _telegram_preview_url(url: str) -> Optional[str]:
    """For a public t.me channel/bot URL, Telegram also serves a crawler-
    friendly '/s/' preview page with richer server-rendered OG data. Only
    applicable to channel/bot-style links (a single path segment)."""
    try:
        parsed = urlparse(url)
        segments = [s for s in (parsed.path or "").strip("/").split("/") if s]
        if not segments:
            return None
        username = segments[0]
        if username.startswith("+") or username == "joinchat":
            return None
        return f"https://t.me/s/{username}"
    except Exception:
        return None


# --------------------------------------------------------------------------
# Master entry point
# --------------------------------------------------------------------------

def scrape_project(url: str) -> dict:
    """Scrape whatever data is publicly available for a project URL.
    Always returns a dict - never raises. Any field that couldn't be
    determined is left as None (or an empty dict/list), NEVER guessed."""
    result = {
        "url": url,
        "category": "website",
        "title": None,
        "description": None,
        "image": None,
        "favicon": None,
        "site_name": None,
        "raw_text": None,
        "links": {},
        "errors": [],
    }

    category = detect_category(url)
    result["category"] = category

    html = fetch_html(url)

    # Telegram channel/bot links: if the direct fetch didn't yield a
    # description (Telegram sometimes serves a stripped-down page to
    # non-browser clients), fall back to the crawler-friendly /s/ preview.
    if html:
        soup = BeautifulSoup(html, "html.parser")
        og = extract_opengraph(soup, url)
        if category in ("telegram_channel", "telegram_bot", "telegram_post") and not og.get("description"):
            preview_url = _telegram_preview_url(url)
            if preview_url and preview_url != url:
                preview_html = fetch_html(preview_url)
                if preview_html:
                    preview_soup = BeautifulSoup(preview_html, "html.parser")
                    preview_og = extract_opengraph(preview_soup, preview_url)
                    og = {k: (v or preview_og.get(k)) for k, v in og.items()}
                    soup = preview_soup  # richer page for text/links too
    else:
        result["errors"].append(f"Could not fetch {url}")
        soup = None
        og = {"title": None, "description": None, "image": None, "site_name": None}

        # Even if the direct URL failed, still try the Telegram preview page.
        if category in ("telegram_channel", "telegram_bot", "telegram_post"):
            preview_url = _telegram_preview_url(url)
            if preview_url:
                preview_html = fetch_html(preview_url)
                if preview_html:
                    soup = BeautifulSoup(preview_html, "html.parser")
                    og = extract_opengraph(soup, preview_url)

    result["title"] = og.get("title")
    result["description"] = og.get("description")
    result["image"] = og.get("image")
    result["site_name"] = og.get("site_name")

    if soup is not None:
        if not result["image"]:
            result["image"] = extract_logo(soup, url)
        result["favicon"] = extract_favicon(soup, url)
        result["links"] = extract_outbound_links(soup, url)
        result["raw_text"] = extract_visible_text(soup)

    return result


def get_best_image(scraped: dict) -> Optional[str]:
    """Apply the requested image priority (excluding the user-uploaded-photo
    case, which bot.py handles separately since it always wins outright):
        OpenGraph image (covers Telegram image / Twitter image / website
        social image, since all of those are served via og:image) ->
        website logo -> favicon.
    """
    if not scraped:
        return None
    return scraped.get("image") or scraped.get("favicon")
