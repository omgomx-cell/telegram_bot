import os
import time
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ---- Health-check server so Render keeps the port alive ----
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - bot is running")
    def log_message(self, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

# ---- Bot handlers ----
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Welcome to the Public-Domain Movie Bot!\n\n"
        "Send /find <title> to search thousands of free, legal films "
        "from the Internet Archive.\n\n"
        "Example: /find charlie chaplin"
    )

async def find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /find <title>\nExample: /find night of the living dead")
    query = " ".join(ctx.args)
    try:
        r = await asyncio.to_thread(
            requests.get, "https://archive.org/advancedsearch.php",
            params={
                "q": f'title:({query}) AND mediatype:(movies)',
                "fl[]": "identifier,title", "rows": 5, "output": "json"
            }, timeout=15
        )
        r.raise_for_status()
        docs = r.json()["response"]["docs"]
    except Exception as e:
        logging.error(f"Search error: {e}")
        return await update.message.reply_text("⚠️ Search failed, please try again in a moment.")

    if not docs:
        return await update.message.reply_text("😕 No public-domain matches found. Try a different title.")

    buttons = [[InlineKeyboardButton(d.get("title", "Untitled")[:40],
               url=f"https://archive.org/details/{d['identifier']}")] for d in docs]
    await update.message.reply_text(
        f"🎬 Found {len(docs)} free & legal result(s) for “{query}”:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ---- Main with auto-restart ----
def main():
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("find", find))
    logging.info("Bot started and polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    while True:
        try:
            main()
        except Exception as e:
            logging.error(f"Bot crashed: {e} — restarting in 10s")
            time.sleep(10)