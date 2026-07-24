import os, time, asyncio, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatAction
import requests

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s")

TMDB_KEY = os.environ["TMDB_API_KEY"]        # free from themoviedb.org
TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"

# ---- health server for Render ----
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass
def health():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()

# ---- TMDB helpers ----
def search_movie(q):
    r = requests.get(f"{TMDB}/search/movie",
        params={"api_key": TMDB_KEY, "query": q}, timeout=15)
    r.raise_for_status()
    return r.json().get("results", [])[:1]

def watch_providers(movie_id, region="US"):
    r = requests.get(f"{TMDB}/movie/{movie_id}/watch/providers",
        params={"api_key": TMDB_KEY}, timeout=15)
    r.raise_for_status()
    return r.json().get("results", {}).get(region, {})

# ---- handlers ----
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "🎬 *Where-To-Watch Bot*\n\n"
        "Send /find <movie> and I'll find where to legally watch it "
        "in high quality — free & paid options.\n\n"
        "Try: `/find interstellar`", parse_mode="Markdown")

async def find(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args:
        return await u.message.reply_text("Usage: /find <movie name>")
    query = " ".join(c.args)
    await c.bot.send_chat_action(u.message.chat_id, ChatAction.TYPING)
    try:
        results = await asyncio.to_thread(search_movie, query)
        if not results:
            return await u.message.reply_text("😕 Movie not found. Check the spelling?")
        m = results[0]
        prov = await asyncio.to_thread(watch_providers, m["id"])
    except Exception as e:
        logging.error(e)
        return await u.message.reply_text("⚠️ Something went wrong, try again.")

    title = m.get("title", "Unknown")
    year = (m.get("release_date") or "----")[:4]
    rating = m.get("vote_average", 0)
    overview = (m.get("overview") or "No synopsis available.")[:400]

    # collect legal watch options
    options = []
    for kind, label in [("flatrate", "▶️ Stream"), ("free", "🆓 Free"),
                        ("rent", "💵 Rent"), ("buy", "🛒 Buy")]:
        for p in prov.get(kind, []):
            options.append(f"{label}: {p['provider_name']}")
    link = prov.get("link")  # JustWatch page with all HD sources

    caption = (f"🎬 *{title}* ({year})\n"
               f"⭐ {rating}/10\n\n"
               f"{overview}\n\n")
    if options:
        caption += "*Where to watch (HD):*\n" + "\n".join(f"• {o}" for o in options[:8])
    else:
        caption += "_No streaming providers listed for your region._"

    kb = None
    if link:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 All HD watch links", url=link)]])

    poster = m.get("poster_path")
    if poster:
        await u.message.reply_photo(IMG + poster, caption=caption,
                                    parse_mode="Markdown", reply_markup=kb)
    else:
        await u.message.reply_text(caption, parse_mode="Markdown", reply_markup=kb)

def main():
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("find", find))
    logging.info("Bot polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=health, daemon=True).start()
    while True:
        try: main()
        except Exception as e:
            logging.error(f"crash: {e}"); time.sleep(10)
