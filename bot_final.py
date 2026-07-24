from __future__ import annotations
import os
import re
import html
import hashlib
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
import traceback

import aiohttp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("moviebot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_META = "https://archive.org/metadata/"
ARCHIVE_THUMB = "https://archive.org/services/img/"
RESULTS_PER_PAGE = 5
MAX_RESULTS = 25

# In-memory cache for pagination queries
QUERY_CACHE = {}

def cache_query(query):
    token = hashlib.md5(query.encode()).hexdigest()[:8]
    QUERY_CACHE[token] = query
    return token

def lookup_query(token):
    return QUERY_CACHE.get(token)

# ─── Health-check server (for Render) ─────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - movie bot is running")

    def log_message(self, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

# ─── Archive.org helpers ──────────────────────────────────────────────────────
async def archive_search(session, query, page=0, rows=RESULTS_PER_PAGE, sort="downloads desc"):
    # runtime:[1800 TO 14400] filters for 30 mins to 4 hours (in seconds)
    quality_query = f'({query}) AND mediatype:(movies) AND runtime:[1800 TO 14400]'
    
    params = {
        "q": quality_query,
        "fl[]": "identifier,title,year,downloads,description,creator,runtime",
        "sort[]": sort,
        "rows": rows,
        "page": page,
        "output": "json",
    }
    async with session.get(ARCHIVE_SEARCH, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        return await r.json()

async def archive_metadata(session, identifier):
    async with session.get(f"{ARCHIVE_META}{identifier}", timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status != 200:
            return None
        return await r.json()

def find_mp4_files(metadata):
    files = metadata.get("files", [])
    mp4s = []
    for f in files:
        name = f.get("name", "")
        fmt = f.get("format", "")
        if fmt in ("MPEG4", "h.264", "H.264") or name.lower().endswith(".mp4"):
            size = f.get("size", "0")
            try:
                size_mb = int(size) / (1024 * 1024)
                size_str = f"{size_mb:.0f} MB" if size_mb > 1 else f"{int(size)/1024:.0f} KB"
            except (ValueError, TypeError):
                size_str = ""
                size_mb = 0
            mp4s.append({"name": name, "size": size_str, "size_mb": size_mb})

    mp4s.sort(key=lambda x: x["size_mb"], reverse=True)
    return mp4s

def truncate(text, limit=300):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "…" if len(text) > limit else text

# ─── Message formatting ───────────────────────────────────────────────────────
def format_movie_card(doc):
    title = html.escape(doc.get("title", "Untitled"))
    year = doc.get("year", "")
    creator = html.escape(doc.get("creator", "") or "")
    downloads = doc.get("downloads", 0)
    desc = html.escape(truncate(doc.get("description", ""), 280))
    identifier = doc.get("identifier", "")
    
    lines = [f"🎬 <b>{title}</b>"]
    meta_parts = []
    if year:
        meta_parts.append(f"📅 {html.escape(str(year))}")
    if creator:
        meta_parts.append(f"🎭 {creator[:60]}")
    if downloads:
        meta_parts.append(f"⬇️ {downloads:,} downloads")
    if meta_parts:
        lines.append(" ".join(meta_parts))
    if desc:
        lines.append(f"\n📝 {desc}")
    lines.append(f"\n🔗 <a href=\"https://archive.org/details/{identifier}\">View on Archive.org</a>")
    return "\n".join(lines)

def build_results_keyboard(docs, query, page, total):
    buttons = []
    token = cache_query(query)
    
    for d in docs:
        title = d.get("title", "Untitled")[:45]
        ident = d["identifier"]
        buttons.append([InlineKeyboardButton(f"▶ {title}", callback_data=f"detail:{ident}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page:{page - 1}:{token}"))
    nav.append(InlineKeyboardButton(f"📄 {page + 1}/{max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)}", callback_data="noop"))
    if (page + 1) * RESULTS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:{page + 1}:{token}"))
    if nav:
