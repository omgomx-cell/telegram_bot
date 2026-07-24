import os
import re
import html
import time
import hashlib
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

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

# In-memory cache for pagination queries to bypass Telegram's 64-byte limit
QUERY_CACHE = {}

def cache_query(query: str) -> str:
    token = hashlib.md5(query.encode()).hexdigest()[:8]
    QUERY_CACHE[token] = query
    return token

def lookup_query(token: str) -> str | None:
    return QUERY_CACHE.get(token)

# ─── Health-check server (for Render / hosting) ───────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - movie bot is running")

    def log_message(self, *_):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

# ─── Archive.org helpers ──────────────────────────────────────────────────────
async def archive_search(
    session: aiohttp.ClientSession,
    query: str,
    page: int = 0,
    rows: int = RESULTS_PER_PAGE,
    sort: str = "downloads desc",
) -> dict[str, Any]:
    """Search archive.org for feature-length movies."""
    # FIX: Archive.org uses SECONDS for runtime. 1800s = 30 mins, 14400s = 4 hours.
    # This perfectly filters out shorts, clips, and trailers.
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

async def archive_metadata(session: aiohttp.ClientSession, identifier: str) -> dict | None:
    """Fetch full metadata for a single item."""
    async with session.get(f"{ARCHIVE_META}{identifier}", timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status != 200:
            return None
        return await r.json()

def find_mp4_files(metadata: dict) -> list[dict]:
    """Extract MP4 files, sorted by size descending (highest quality first)."""
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

def truncate(text: str, limit: int = 300) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + "…" if len(text) > limit else text

# ─── Message formatting ───────────────────────────────────────────────────────
def format_movie_card(doc: dict) -> str:
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

def build_results_keyboard(docs: list[dict], query: str, page: int, total: int) -> InlineKeyboardMarkup:
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
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)

# ─── Command handlers ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 <b>Welcome to the Public-Domain Movie Bot!</b>\n\n"
        "Search thousands of free, legal films from the Internet Archive.\n\n"
        "📂 <b>Commands:</b>\n"
        "• /find <code>&lt;title&gt;</code> — Search by title\n"
        "• /random — Get a surprise random movie\n"
        "• /popular — Top downloaded films\n"
        "• /genre <code>&lt;genre&gt;</code> — Browse by genre\n"
        "• /help — Full help\n\n"
        "💡 You can also use me in <b>inline mode</b>: "
        "type <code>@botname charlie chaplin</code> in any chat!",
        parse_mode="HTML",
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 <b>Public-Domain Movie Bot — Help</b>\n\n"
        "All movies are from the Internet Archive and are free to watch, download, and share legally.\n\n"
        "📂 <b>Commands:</b>\n"
        "• <code>/find &lt;title&gt;</code> — Search movies by title\n"
        "  Example: <code>/find night of the living dead</code>\n\n"
        "• <code>/random</code> — Discover a random movie\n\n"
        "• <code>/popular</code> — See the most-downloaded films\n\n"
        "• <code>/genre &lt;genre&gt;</code> — Browse by genre\n"
        "  Example: <code>/genre comedy</code>\n"
        "  Genres: comedy, horror, sci-fi, western, animation, documentary, noir\n\n"
        "• <code>/about</code> — About this bot\n\n"
        "💡 <b>Tip:</b> Tap any movie title for direct streaming & download links.",
        parse_mode="HTML",
    )

async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 <b>Public-Domain Movie Bot</b>\n\n"
        "Powered by the <a href=\"https://archive.org\">Internet Archive</a>.\n"
        "All films are in the public domain — free to watch, download, and share.\n\n"
        "Built with python-telegram-bot & aiohttp.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(
            "Usage: <code>/find &lt;title&gt;</code>\nExample: <code>/find charlie chaplin</code>",
            parse_mode="HTML",
        )
    query = " ".join(ctx.args)
    msg = await update.message.reply_text(f"🔍 Searching for “{html.escape(query)}”…")

    try:
        async with aiohttp.ClientSession() as session:
            data = await archive_search(session, query, page=0, rows=RESULTS_PER_PAGE)
    except Exception as e:
        log.error(f"Search error: {e}")
        return await msg.edit_text("⚠️ Search failed. Please try again in a moment.")

    docs = data.get("response", {}).get("docs", [])
    total = data.get("response", {}).get("numFound", 0)

    if not docs:
        return await msg.edit_text(
            f"😕 No matches for “{html.escape(query)}”.\n"
            "Try different keywords or use /random for a surprise pick."
        )

    keyboard = build_results_keyboard(docs, query, 0, min(total, MAX_RESULTS))
    text_lines = [f"🎬 <b>{total} result(s)</b> for “{html.escape(query)}”\n"]
    for i, d in enumerate(docs, 1):
        title = html.escape(d.get("title", "Untitled"))
        year = d.get("year", "")
        year_str = f" ({year})" if year else ""
        text_lines.append(f"{i}. <b>{title}</b>{year_str}")
    text_lines.append("\n👇 Tap a title for details, stream & download links:")

    await msg.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)

async def cmd_random(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎲 Picking a random movie…")
    try:
        async with aiohttp.ClientSession() as session:
            data = await archive_search(session, "mediatype:(movies)", page=0, rows=1, sort="random")
            docs = data.get("response", {}).get("docs", [])
    except Exception as e:
        log.error(f"Random error: {e}")
        return await msg.edit_text("⚠️ Could not fetch a random movie. Try again.")

    if not docs:
        return await msg.edit_text("😕 Nothing found. Try again.")

    doc = docs[0]
    await msg.edit_text(
        format_movie_card(doc),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎬 Watch / Download", callback_data=f"detail:{doc['identifier']}"),
            InlineKeyboardButton("🎲 Another", callback_data="random"),
        ]]),
    )

async def cmd_popular(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔥 Fetching popular films…")
    try:
        async with aiohttp.ClientSession() as session:
            data = await archive_search(session, "mediatype:(movies) AND collection:(movies)", page=0, rows=RESULTS_PER_PAGE, sort="downloads desc")
            docs = data.get("response", {}).get("docs", [])
            total = data.get("response", {}).get("numFound", 0)
    except Exception as e:
        log.error(f"Popular error: {e}")
        return await msg.edit_text("⚠️ Could not fetch popular films.")

    if not docs:
        return await msg.edit_text("😕 Nothing found.")

    keyboard = build_results_keyboard(docs, "mediatype:(movies) AND collection:(movies)", 0, min(total, MAX_RESULTS))
    text_lines = ["🔥 <b>Most-Downloaded Films</b>\n"]
    for i, d in enumerate(docs, 1):
        title = html.escape(d.get("title", "Untitled"))
        dl = d.get("downloads", 0)
        text_lines.append(f"{i}. <b>{title}</b> — ⬇️ {dl:,}")
    text_lines.append("\n👇 Tap a title for details:")
    await msg.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)

GENRE_MAP = {
    "comedy": 'collection:(comedy_movies) OR subject:("comedy")',
    "horror": 'collection:(horror_movies) OR subject:("horror")',
    "sci-fi": 'subject:("sci-fi") OR subject:("science fiction")',
    "western": 'collection:(western_movies) OR subject:("western")',
    "animation": 'collection:(animation) OR subject:("animation")',
    "documentary": 'collection:(documentaries) OR subject:("documentary")',
    "noir": 'subject:("film noir")',
    "silent": 'collection:(silent_films) OR subject:("silent")',
}

async def cmd_genre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        genres = ", ".join(GENRE_MAP.keys())
        return await update.message.reply_text(
            f"Usage: <code>/genre &lt;genre&gt;</code>\n\nAvailable genres: <code>{genres}</code>",
            parse_mode="HTML",
        )
    genre = ctx.args[0].lower()
    if genre not in GENRE_MAP:
        return await update.message.reply_text(f"Unknown genre. Available: {', '.join(GENRE_MAP.keys())}")

    msg = await update.message.reply_text(f"🎭 Browsing <b>{genre}</b> films…", parse_mode="HTML")
    try:
        async with aiohttp.ClientSession() as session:
            data = await archive_search(session, GENRE_MAP[genre], page=0, rows=RESULTS_PER_PAGE, sort="downloads desc")
            docs = data.get("response", {}).get("docs", [])
            total = data.get("response", {}).get("numFound", 0)
    except Exception as e:
        log.error(f"Genre error: {e}")
        return await msg.edit_text("⚠️ Could not fetch genre films.")

    if not docs:
        return await msg.edit_text(f"😕 No films found in {genre}.")

    keyboard = build_results_keyboard(docs, GENRE_MAP[genre], 0, min(total, MAX_RESULTS))
    text_lines = [f"🎭 <b>{genre.title()} Films</b> ({total} found)\n"]
    for i, d in enumerate(docs, 1):
        title = html.escape(d.get("title", "Untitled"))
        year = d.get("year", "")
        year_str = f" ({year})" if year else ""
        text_lines.append(f"{i}. <b>{title}</b>{year_str}")
    text_lines.append("\n👇 Tap a title for details:")
    await msg.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)

# ─── Callback query handler (pagination, detail, similar) ─────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "random":
        try:
            async with aiohttp.ClientSession() as session:
                result = await archive_search(session, "mediatype:(movies)", page=0, rows=1, sort="random")
                docs = result.get("response", {}).get("docs", [])
        except Exception:
            return await query.edit_message_text("⚠️ Could not fetch a random movie.")
        if not docs:
            return
        doc = docs[0]
        await query.edit_message_text(
            format_movie_card(doc),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎬 Watch / Download", callback_data=f"detail:{doc['identifier']}"),
                InlineKeyboardButton("🎲 Another", callback_data="random"),
            ]]),
        )
        return

    if data == "noop":
        return

    # ── Pagination ──
    if data.startswith("page:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        try:
            page = int(parts[1])
        except ValueError:
            return
        token = parts[2]
        search_query = lookup_query(token)
        if not search_query:
            return await query.edit_message_text("⚠️ Search expired. Please run the command again.")

        try:
            async with aiohttp.ClientSession() as session:
                result = await archive_search(session, search_query, page=page, rows=RESULTS_PER_PAGE)
                docs = result.get("response", {}).get("docs", [])
                total = result.get("response", {}).get("numFound", 0)
        except Exception as e:
            log.error(f"Pagination error: {e}")
            return await query.edit_message_text("⚠️ Failed to load page.")

        if not docs:
            return await query.edit_message_text("No more results.")

        keyboard = build_results_keyboard(docs, search_query, page, min(total, MAX_RESULTS))
        text_lines = [f"🎬 <b>Results — Page {page + 1}</b>\n"]
        for i, d in enumerate(docs, 1):
            title = html.escape(d.get("title", "Untitled"))
            year = d.get("year", "")
            year_str = f" ({year})" if year else ""
            text_lines.append(f"{page * RESULTS_PER_PAGE + i}. <b>{title}</b>{year_str}")
        text_lines.append("\n👇 Tap a title for details:")
        await query.edit_message_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)
        return

    # ── Find Similar (by runtime) ──
    if data.startswith("similar:"):
        runtime_str = data[8:]
        await query.edit_message_text("🧩 Finding similar length movies...")
        
        try:
            rt_val = int(runtime_str)
            # Search for movies within +/- 5 minutes (300 seconds) of the runtime
            query_str = f'runtime:[{rt_val - 300} TO {rt_val + 300}]'
        except ValueError:
            return await query.edit_message_text("⚠️ Invalid runtime.")
            
        try:
            async with aiohttp.ClientSession() as session:
                result = await archive_search(session, query_str, page=0, rows=RESULTS_PER_PAGE, sort="downloads desc")
                docs = result.get("response", {}).get("docs", [])
                total = result.get("response", {}).get("numFound", 0)
        except Exception as e:
            log.error(f"Similar error: {e}")
            return await query.edit_message_text("⚠️ Could not fetch similar movies.")
            
        if not docs:
            return await query.edit_message_text("😕 No similar length movies found.")
            
        keyboard = build_results_keyboard(docs, query_str, 0, min(total, MAX_RESULTS))
        text_lines = [f"🧩 <b>Movies of similar length</b>\n"]
        for i, d in enumerate(docs, 1):
            title = html.escape(d.get("title", "Untitled"))
            year = d.get("year", "")
            year_str = f" ({year})" if year else ""
            text_lines.append(f"{i}. <b>{title}</b>{year_str}")
        text_lines.append("\n👇 Tap a title for details:")
        await query.edit_message_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)
        return

    # ── Detail view ──
    if data.startswith("detail:"):
        identifier = data[7:]
        try:
            async with aiohttp.ClientSession() as session:
                meta = await archive_metadata(session, identifier)
        except Exception as e:
            log.error(f"Metadata error: {e}")
            return await query.edit_message_text("⚠️ Could not load movie details.")

        if not meta:
            return await query.edit_message_text("⚠️ Movie not found.")

        d = meta.get("metadata", {})
        title = html.escape(d.get("title", "Untitled"))
        year = d.get("year", d.get("date", ""))
        creator = html.escape(d.get("creator", "") or "")
        desc = html.escape(truncate(d.get("description", ""), 400))
        runtime = d.get("runtime", "")

        lines = [f"🎬 <b>{title}</b>"]
        meta_parts = []
        if year:
            meta_parts.append(f"📅 {html.escape(str(year)[:4])}")
        if runtime:
            meta_parts.append(f"⏱️ {html.escape(str(runtime))}")
        if creator:
            meta_parts.append(f"🎭 {creator[:60]}")
        if meta_parts:
            lines.append(" ".join(meta_parts))
        if desc:
            lines.append(f"\n📝 {desc}")

        lines.append(f"\n🔗 <a href=\"https://archive.org/details/{identifier}\">Full page on Archive.org</a>")

        mp4s = find_mp4_files(meta)
        buttons = []
        if mp4s:
            # Show the top 2 largest files (usually the HD versions)
            for mp4 in mp4s[:2]:  
                url = f"https://archive.org/download/{identifier}/{mp4['name']}"
                label = f"⬇️ High Quality ({mp4['size']})"
                buttons.append([InlineKeyboardButton(label, url=url)])

        buttons.append([InlineKeyboardButton(
            "▶️ Stream on Archive.org",
            url=f"https://archive.org/details/{identifier}",
        )])
        
        nav_row = [InlineKeyboardButton("⬅️ Back", callback_data="noop")]
        if runtime:
            # Clean runtime to just digits for the similar search
            clean_runtime = "".join(filter(str.isdigit, runtime))
            if clean_runtime:
                nav_row.append(InlineKeyboardButton("🧩 Find Similar", callback_data=f"similar:{clean_runtime}"))
        buttons.append(nav_row)

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

# ─── Inline query handler ─────────────────────────────────────────────────────
async def on_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    inline_query = update.inline_query
    query_text = inline_query.query.strip()

    if len(query_text) < 2:
        results = []
        suggestions = ["charlie chaplin", "nosferatu", "buster keaton", "night of the living dead"]
        for s in suggestions:
            results.append({
                "type": "article",
                "id": f"sugg-{s}",
                "title": f"🔍 Search: {s}",
                "input_message_content": InputTextMessageContent(f"Search for: {s}\nUse /find {s} in a chat with the bot."),
                "description": "Tap to share this search",
            })
        await inline_query.answer(results, cache_time=10)
        return

    try:
        async with aiohttp.ClientSession() as session:
            data = await archive_search(session, query_text, page=0, rows=RESULTS_PER_PAGE)
            docs = data.get("response", {}).get("docs", [])
    except Exception as e:
        log.error(f"Inline search error: {e}")
        return await inline_query.answer([], cache_time=5)

    results = []
    for d in docs:
        ident = d["identifier"]
        title = d.get("title", "Untitled")
        year = d.get("year", "")
        desc = truncate(d.get("description", ""), 100)
        thumb_url = f"{ARCHIVE_THUMB}{ident}"

        results.append({
            "type": "article",
            "id": ident,
            "title": f"🎬 {title}" + (f" ({year})" if year else ""),
            "description": desc or "Public-domain film on Archive.org",
            "thumb_url": thumb_url,
            "input_message_content": InputTextMessageContent(
                format_movie_card(d),
                parse_mode="HTML",
                disable_web_page_preview=False,
            ),
            "reply_markup": InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Watch / Download", url=f"https://archive.org/details/{ident}"),
            ]]),
        })

    await inline_query.answer(results, cache_time=30)

# ─── Application lifecycle ────────────────────────────────────────────────────
async def on_error(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"Exception: {ctx.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. Please try again.")
        except Exception:
            pass

def main():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("random", cmd_random))
    app.add_handler(CommandHandler("popular", cmd_popular))
    app.add_handler(CommandHandler("genre", cmd_genre))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(InlineQueryHandler(on_inline))
    app.add_error_handler(on_error)

    log.info("🎬 Movie bot starting (polling)…")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    # Crash-recovery loop
    while True:
        try:
            main()
        except Exception as e:
            log.error(f"Bot crashed: {e} — restarting in 10s")
            time.sleep(10)
