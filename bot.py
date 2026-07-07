import telebot
from telebot import types
import sqlite3
import threading
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("8876721186:AAFLAng8Eyp3BUaAKzb5R1fYlL4mtVNVn0U")
if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it in Render: Dashboard -> your service -> Environment -> Add Environment Variable "
        "(key: BOT_TOKEN, value: your token from @BotFather)."
    )
bot = telebot.TeleBot(TOKEN)

local = threading.local()

def get_db():
    if not hasattr(local, 'conn'):
        local.conn = sqlite3.connect('users.db', check_same_thread=False, timeout=15)
        local.cursor = local.conn.cursor()
        local.cursor.execute('''CREATE TABLE IF NOT EXISTS users
            (user_id INTEGER PRIMARY KEY, username TEXT, country TEXT, state TEXT,
             gender TEXT, age INTEGER, partner INTEGER)''')
        local.conn.commit()
    return local.conn, local.cursor

waiting_users_lock = threading.Lock()
waiting_users = []

registering_lock = threading.Lock()
registering_users = set()  # user_ids currently mid-registration flow

# ---------------- Keyboards ----------------
COMMON_COUNTRIES = ["United States", "India", "United Kingdom", "Canada", "Australia",
                     "Germany", "France", "Japan", "Brazil", "Russia", "China", "Mexico",
                     "Italy", "Spain", "South Korea", "Nigeria", "Turkey", "Other"]

INDIAN_STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
                  "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
                  "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
                  "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
                  "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
                  "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Other State"]

# 18+ only.
AGE_RANGES = ["18-20", "21-24", "25-30", "31-40", "41-50", "51+"]

def get_country_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for c in COMMON_COUNTRIES:
        markup.add(types.KeyboardButton(c))
    return markup

def get_indian_states_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for s in INDIAN_STATES:
        markup.add(types.KeyboardButton(s))
    return markup

def get_gender_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("Male"), types.KeyboardButton("Female"))
    markup.add(types.KeyboardButton("Other"))
    return markup

def get_age_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for a in AGE_RANGES:
        markup.add(types.KeyboardButton(a))
    markup.add(types.KeyboardButton("Custom Age"))
    return markup

# ---------------- Helpers ----------------

def is_command(text):
    return bool(text) and text.startswith('/')

def cancel_flow(chat_id, user_id=None):
    """Clear any pending next-step handler so commands can't get swallowed by the flow."""
    bot.clear_step_handler_by_chat_id(chat_id)
    if user_id is not None:
        unmark_registering(user_id)

def get_profile(user_id):
    conn, cursor = get_db()
    cursor.execute("SELECT user_id, username, country, state, gender, age, partner FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def set_partner(user_id, partner_id):
    conn, cursor = get_db()
    cursor.execute("UPDATE users SET partner=? WHERE user_id=?", (partner_id, user_id))
    conn.commit()

def remove_from_waiting(user_id):
    with waiting_users_lock:
        if user_id in waiting_users:
            waiting_users.remove(user_id)

def mark_registering(user_id):
    with registering_lock:
        registering_users.add(user_id)

def unmark_registering(user_id):
    with registering_lock:
        registering_users.discard(user_id)

def is_registering(user_id):
    with registering_lock:
        return user_id in registering_users

# ---------------- Commands ----------------

@bot.message_handler(commands=['start'])
def start(message):
    cancel_flow(message.chat.id, message.from_user.id)
    mark_registering(message.from_user.id)
    msg = bot.send_message(message.chat.id, "👋 Welcome! Select your country (or just type it):", reply_markup=get_country_keyboard())
    bot.register_next_step_handler(msg, process_country)

@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(
        message.chat.id,
        "Commands:\n"
        "/start - Register\n"
        "/check - Status\n"
        "/find - Find Partner\n"
        "/end - End Chat"
    )

@bot.message_handler(commands=['check'])
def check_status(message):
    profile = get_profile(message.from_user.id)
    if not profile:
        bot.send_message(message.chat.id, "❌ No profile found. Use /start to register.")
        return
    _, username, country, state, gender, age, partner = profile
    lines = [
        f"👤 Username: {username or 'N/A'}",
        f"🌍 Country: {country}",
        f"📍 State: {state}",
        f"⚧ Gender: {gender}",
        f"🎂 Age: {age}",
    ]
    if partner:
        lines.append("💬 Status: In a chat")
    elif message.from_user.id in waiting_users:
        lines.append("⏳ Status: Waiting for a partner")
    else:
        lines.append("🔎 Status: Not searching. Use /find")
    bot.send_message(message.chat.id, "\n".join(lines))

@bot.message_handler(commands=['find'])
def find_partner(message):
    user_id = message.from_user.id
    profile = get_profile(user_id)
    if not profile:
        bot.send_message(message.chat.id, "❌ Please /start and register first.")
        return
    if profile[6]:  # already has a partner
        bot.send_message(message.chat.id, "⚠️ You're already in a chat. Use /end first.")
        return
    if user_id in waiting_users:
        bot.send_message(message.chat.id, "⏳ Already searching, please wait...")
        return

    with waiting_users_lock:
        partner_id = None
        for candidate_id in waiting_users:
            if candidate_id != user_id:
                partner_id = candidate_id
                break
        if partner_id:
            waiting_users.remove(partner_id)
            set_partner(user_id, partner_id)
            set_partner(partner_id, user_id)
            bot.send_message(message.chat.id, "✅ Partner found! Say hi 👋\nUse /end to stop chatting.")
            bot.send_message(partner_id, "✅ Partner found! Say hi 👋\nUse /end to stop chatting.")
        else:
            waiting_users.append(user_id)
            bot.send_message(message.chat.id, "🔎 Searching for a partner...")

@bot.message_handler(commands=['end'])
def end_chat(message):
    user_id = message.from_user.id
    remove_from_waiting(user_id)
    profile = get_profile(user_id)
    if profile and profile[6]:
        partner_id = profile[6]
        set_partner(user_id, None)
        set_partner(partner_id, None)
        bot.send_message(message.chat.id, "🚪 Chat ended.")
        try:
            bot.send_message(partner_id, "🚪 Your partner left the chat.")
        except Exception:
            pass
    else:
        bot.send_message(message.chat.id, "ℹ️ You're not in a chat.")

# ---------------- Registration flow ----------------
# Every step below is only ever invoked via register_next_step_handler,
# never via the catch-all handler, so there is no double-firing.

def process_country(message):
    if is_command(message.text):
        return  # let the command handler deal with it instead of consuming it here
    country = (message.text or "").strip()
    if not country:
        bot.send_message(message.chat.id, "❌ Please select or type a country.", reply_markup=get_country_keyboard())
        bot.register_next_step_handler(message, process_country)
        return
    if country == "Other":
        # Explicit "type it yourself" prompt for people who don't see their country in the list
        bot.send_message(message.chat.id, "🌍 Enter country name:", reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(message, process_custom_country)
        return
    # Works whether they tapped a button or just typed the name themselves
    if country.title() == "India":
        bot.send_message(message.chat.id, "🇮🇳 Select your State (or type it):", reply_markup=get_indian_states_keyboard())
        bot.register_next_step_handler(message, lambda m: process_state(m, "India"))
    else:
        ask_state(message, country.title())

def process_custom_country(message):
    if is_command(message.text):
        return
    country = message.text.strip().title()
    ask_state(message, country)

def ask_state(message, country):
    bot.send_message(message.chat.id, f"📍 Enter State:\nCountry: <b>{country}</b>",
                      parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, lambda m: process_state(m, country))

def process_state(message, country):
    if is_command(message.text):
        return
    state = message.text.strip().title()
    bot.send_message(message.chat.id, "⚧ Select Gender:", reply_markup=get_gender_keyboard())
    bot.register_next_step_handler(message, lambda m: process_gender(m, country, state))

def process_gender(message, country, state):
    if is_command(message.text):
        return
    text = (message.text or "").strip().title()
    if not text:
        bot.send_message(message.chat.id, "❌ Please select or type your gender.", reply_markup=get_gender_keyboard())
        bot.register_next_step_handler(message, lambda m: process_gender(m, country, state))
        return
    # Accepts the keyboard buttons OR any freely typed value (e.g. "Non-binary")
    bot.send_message(message.chat.id, "🎂 Select Age (or type it):", reply_markup=get_age_keyboard())
    bot.register_next_step_handler(message, lambda m: process_age(m, country, state, text))

def process_age(message, country, state, gender):
    if is_command(message.text):
        return
    age_text = (message.text or "").strip()
    if age_text == "Custom Age":
        bot.send_message(message.chat.id, "Enter age (18-100):", reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(message, lambda m: process_custom_age(m, country, state, gender))
        return
    age = parse_age_range(age_text)
    if age is not None and 18 <= age <= 100:
        save_user_profile(message, country, state, gender, age)
    else:
        bot.send_message(message.chat.id, "❌ Invalid age. Please pick an option from the keyboard.")
        bot.register_next_step_handler(message, lambda m: process_age(m, country, state, gender))

def process_custom_age(message, country, state, gender):
    if is_command(message.text):
        return
    try:
        age = int(message.text.strip())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Please enter a whole number.")
        bot.register_next_step_handler(message, lambda m: process_custom_age(m, country, state, gender))
        return
    if 18 <= age <= 100:
        save_user_profile(message, country, state, gender, age)
    else:
        bot.send_message(message.chat.id, "❌ This bot is for ages 18-100 only.")
        bot.register_next_step_handler(message, lambda m: process_custom_age(m, country, state, gender))

def parse_age_range(age_text):
    try:
        if "-" in age_text:
            return int(age_text.split('-')[0])
        if "+" in age_text:
            return int(age_text.replace('+', ''))
        return int(age_text)
    except ValueError:
        return None

def save_user_profile(message, country, state, gender, age):
    conn, cursor = get_db()
    try:
        cursor.execute(
            '''INSERT INTO users (user_id, username, country, state, gender, age, partner)
               VALUES (?,?,?,?,?,?,NULL)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, country=excluded.country, state=excluded.state,
                 gender=excluded.gender, age=excluded.age''',
            (message.from_user.id, message.from_user.username, country, state, gender, age)
        )
        conn.commit()
        bot.send_message(message.chat.id, "✅ Profile Saved!\nUse /find to look for a chat partner.",
                          reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Error saving profile. Try /start again.")
        print(f"DB Error: {e}")
    finally:
        unmark_registering(message.from_user.id)

# ---------------- Relay (catch-all) ----------------
# This ONLY relays chat messages between paired partners. It no longer
# does any country/state matching, so it can't race with the flow above.

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_all(message):
    if is_command(message.text):
        return
    if is_registering(message.from_user.id):
        return  # a registration step is in progress; that next-step handler owns this message
    relay_message(message)

def relay_message(message):
    profile = get_profile(message.from_user.id)
    if not profile or not profile[6]:
        bot.send_message(message.chat.id, "ℹ️ You're not in a chat. Use /find to get matched.")
        return
    partner_id = profile[6]
    try:
        bot.send_message(partner_id, message.text)
    except Exception as e:
        bot.send_message(message.chat.id, "⚠️ Couldn't deliver your message. Your partner may have blocked the bot.")
        print(f"Relay error: {e}")

# ---------------- Dummy web server for Render free tier ----------------
# Render's free "Web Service" plan requires an open port to health-check;
# a polling bot doesn't normally open one, so we spin up a trivial HTTP
# server just to satisfy that check. It does nothing else.

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass  # silence default request logging

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# Registers the "/" command menu Telegram shows in the chat UI, so users
# can tap "/" and see + pick every available command, or just type it manually.
bot.set_my_commands([
    types.BotCommand("start", "Register / restart"),
    types.BotCommand("help", "Show available commands"),
    types.BotCommand("check", "Check your profile & status"),
    types.BotCommand("find", "Find a chat partner"),
    types.BotCommand("end", "End current chat"),
])

print("🤖 Bot Started - Clean Version")
bot.infinity_polling()
