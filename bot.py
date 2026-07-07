import telebot
from telebot import types
import sqlite3
import threading
import os
import time
import logging

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("chatbot")

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

ADMIN_ID = os.environ.get("ADMIN_ID")  # optional: numeric telegram user id for report alerts

bot = telebot.TeleBot(TOKEN)

local = threading.local()

# ---------------- DB Setup + Migration ----------------
def get_db():
    if not hasattr(local, 'conn'):
        local.conn = sqlite3.connect('users.db', check_same_thread=False, timeout=15)
        local.cursor = local.conn.cursor()
        local.cursor.execute('''CREATE TABLE IF NOT EXISTS users
            (user_id INTEGER PRIMARY KEY, username TEXT, country TEXT, state TEXT,
             gender TEXT, age INTEGER, partner INTEGER, match_time INTEGER)''')

        local.cursor.execute('''CREATE TABLE IF NOT EXISTS blocks
            (blocker_id INTEGER, blocked_id INTEGER, PRIMARY KEY (blocker_id, blocked_id))''')

        local.cursor.execute('''CREATE TABLE IF NOT EXISTS reports
            (id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER, reported_id INTEGER,
             reason TEXT, created_at INTEGER)''')

        # Migration: add match_time column if an old DB file exists without it
        local.cursor.execute("PRAGMA table_info(users)")
        existing_cols = {row[1] for row in local.cursor.fetchall()}
        if 'match_time' not in existing_cols:
            local.cursor.execute("ALTER TABLE users ADD COLUMN match_time INTEGER")
            logger.info("Migrated users table: added match_time column")

        local.cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_partner ON users(partner)")
        local.conn.commit()
    return local.conn, local.cursor

waiting_users_lock = threading.Lock()
waiting_users = []

registering_lock = threading.Lock()
registering_users = set()

# ---------------- Keyboards ----------------
COMMON_COUNTRIES = ["United States", "India", "United Kingdom", "Canada", "Australia", "Germany", "France", "Japan", "Brazil", "Russia", "China", "Mexico", "Italy", "Spain", "South Korea", "Nigeria", "Turkey", "Other"]

INDIAN_STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Other State"]

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

def escape_markdown(text):
    """Escape Telegram Markdown special characters to prevent broken/garbled messages."""
    if not text:
        return text
    special_chars = ['_', '*', '`', '[', ']']
    for ch in special_chars:
        text = text.replace(ch, '\\' + ch)
    return text

def cancel_flow(chat_id, user_id=None):
    bot.clear_step_handler_by_chat_id(chat_id)
    if user_id is not None:
        unmark_registering(user_id)

def get_profile(user_id):
    conn, cursor = get_db()
    cursor.execute("SELECT user_id, username, country, state, gender, age, partner FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def set_partner(user_id, partner_id):
    conn, cursor = get_db()
    now = int(time.time())
    cursor.execute("UPDATE users SET partner=?, match_time=? WHERE user_id=?", (partner_id, now, user_id))
    cursor.execute("UPDATE users SET partner=?, match_time=? WHERE user_id=?", (user_id, now, partner_id))
    conn.commit()

def clear_partner_pair(user_id, partner_id):
    conn, cursor = get_db()
    cursor.execute("UPDATE users SET partner=NULL, match_time=NULL WHERE user_id IN (?,?)", (user_id, partner_id))
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

def get_time_left(user_id):
    conn, cursor = get_db()
    cursor.execute("SELECT match_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        elapsed = time.time() - row[0]
        return max(0, 60 - int(elapsed))
    return 0

def is_blocked(a_id, b_id):
    """True if either user has blocked the other."""
    conn, cursor = get_db()
    cursor.execute(
        "SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
        (a_id, b_id, b_id, a_id)
    )
    return cursor.fetchone() is not None

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
        "/end or /exit - End Chat\n"
        "/block - Block current partner (won't be matched again)\n"
        "/report <reason> - Report current partner to admins"
    )

@bot.message_handler(commands=['check'])
def check_status(message):
    profile = get_profile(message.from_user.id)
    if not profile:
        bot.send_message(message.chat.id, "❌ No profile found. Use /start to register.")
        return
    _, username, country, state, gender, age, partner = profile
    lines = [f"👤 Username: {username or 'N/A'}", f"🌍 Country: {country}", f"📍 State: {state or 'N/A'}", f"⚧ Gender: {gender}", f"🎂 Age: {age}"]
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
    if profile[6]:
        bot.send_message(message.chat.id, "⚠️ You're already in a chat. Use /end first.")
        return
    if user_id in waiting_users:
        bot.send_message(message.chat.id, "⏳ Already searching, please wait...")
        return

    with waiting_users_lock:
        partner_id = None
        for candidate_id in waiting_users:
            if candidate_id == user_id:
                continue
            if is_blocked(user_id, candidate_id):
                continue
            # Guard against stale entries: confirm candidate is actually still free
            candidate_profile = get_profile(candidate_id)
            if not candidate_profile or candidate_profile[6]:
                continue
            partner_id = candidate_id
            break

        if partner_id:
            waiting_users.remove(partner_id)
            set_partner(user_id, partner_id)
            ok1 = send_matched_message(user_id, partner_id)
            ok2 = send_matched_message(partner_id, user_id)
            if not (ok1 and ok2):
                # One side couldn't be reached (e.g. blocked the bot) - roll back the match
                clear_partner_pair(user_id, partner_id)
                if ok1:
                    try:
                        bot.send_message(user_id, "⚠️ Your partner is unreachable. Use /find to search again.")
                    except telebot.apihelper.ApiException:
                        pass
                if ok2:
                    try:
                        bot.send_message(partner_id, "⚠️ Your partner is unreachable. Use /find to search again.")
                    except telebot.apihelper.ApiException:
                        pass
        else:
            waiting_users.append(user_id)
            bot.send_message(message.chat.id, "🔎 Searching for a partner...")

def send_matched_message(user_id, partner_id):
    """Returns True if the match notification was delivered successfully."""
    partner_profile = get_profile(partner_id)
    if not partner_profile:
        return False
    _, _, country, state, gender, age, _ = partner_profile
    country = escape_markdown(country)
    state = escape_markdown(state)
    gender = escape_markdown(gender)
    state_text = f" - {state}" if state and state != "N/A" else ""

    text = f"""✅ **Partner Matched!**

**Age:** {age}
**Gender:** {gender}
**Country:** {country}{state_text}

🔗 Links are restricted
📸 Media sharing unlocked after **1 minute**

/exit — Leave the chat
/block — Block this partner
/report <reason> — Report this partner"""

    try:
        bot.send_message(user_id, text, parse_mode='Markdown')
        return True
    except telebot.apihelper.ApiException as e:
        logger.warning(f"Could not deliver match message to {user_id}: {e}")
        return False

@bot.message_handler(commands=['end', 'exit'])
def end_chat(message):
    user_id = message.from_user.id
    remove_from_waiting(user_id)
    profile = get_profile(user_id)
    if profile and profile[6]:
        partner_id = profile[6]
        clear_partner_pair(user_id, partner_id)
        bot.send_message(message.chat.id, "🚪 Chat ended.")
        try:
            bot.send_message(partner_id, "🚪 Your partner left the chat.")
        except telebot.apihelper.ApiException as e:
            logger.warning(f"Could not notify partner {partner_id} of chat end: {e}")
    else:
        bot.send_message(message.chat.id, "ℹ️ You're not in a chat.")

@bot.message_handler(commands=['block'])
def block_partner(message):
    user_id = message.from_user.id
    profile = get_profile(user_id)
    if not profile or not profile[6]:
        bot.send_message(message.chat.id, "ℹ️ You're not in a chat right now.")
        return
    partner_id = profile[6]
    conn, cursor = get_db()
    cursor.execute("INSERT OR IGNORE INTO blocks (blocker_id, blocked_id) VALUES (?,?)", (user_id, partner_id))
    conn.commit()
    clear_partner_pair(user_id, partner_id)
    bot.send_message(message.chat.id, "🚫 Partner blocked. You won't be matched with them again.")
    try:
        bot.send_message(partner_id, "🚪 Your partner left the chat.")
    except telebot.apihelper.ApiException as e:
        logger.warning(f"Could not notify partner {partner_id} of block: {e}")

@bot.message_handler(commands=['report'])
def report_partner(message):
    user_id = message.from_user.id
    profile = get_profile(user_id)
    if not profile or not profile[6]:
        bot.send_message(message.chat.id, "ℹ️ You need to be in a chat to report someone.")
        return
    partner_id = profile[6]
    reason = message.text.replace('/report', '', 1).strip() or "No reason provided"
    conn, cursor = get_db()
    cursor.execute(
        "INSERT INTO reports (reporter_id, reported_id, reason, created_at) VALUES (?,?,?,?)",
        (user_id, partner_id, reason, int(time.time()))
    )
    conn.commit()
    bot.send_message(message.chat.id, "✅ Report submitted. Thank you for keeping the community safe.")
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, f"🚨 Report: user {user_id} reported {partner_id}\nReason: {reason}")
        except telebot.apihelper.ApiException as e:
            logger.warning(f"Could not notify admin of report: {e}")

# ---------------- Registration Flow ----------------
def process_country(message):
    if is_command(message.text): return
    country_input = (message.text or "").strip()
    if not country_input:
        bot.send_message(message.chat.id, "❌ Please select or type a country.", reply_markup=get_country_keyboard())
        bot.register_next_step_handler(message, process_country)
        return
    country = country_input.title()
    if country == "Other":
        bot.send_message(message.chat.id, "🌍 Enter country name:", reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(message, process_custom_country)
        return
    if country == "India":
        bot.send_message(message.chat.id, "🇮🇳 Select your State:", reply_markup=get_indian_states_keyboard())
        bot.register_next_step_handler(message, lambda m: process_state(m, "India"))
    else:
        bot.send_message(message.chat.id, f"🌍 Country: {country}\n\n⚧ Select Gender:", reply_markup=get_gender_keyboard())
        bot.register_next_step_handler(message, lambda m: process_gender(m, country, "N/A"))

def process_custom_country(message):
    if is_command(message.text): return
    country = (message.text or "").strip().title()
    if not country:
        bot.send_message(message.chat.id, "❌ Please enter a valid country name.")
        bot.register_next_step_handler(message, process_custom_country)
        return
    bot.send_message(message.chat.id, f"🌍 Country: {country}\n\n⚧ Select Gender:", reply_markup=get_gender_keyboard())
    bot.register_next_step_handler(message, lambda m: process_gender(m, country, "N/A"))

def process_state(message, country):
    if is_command(message.text): return
    state = (message.text or "").strip().title()
    if not state:
        bot.send_message(message.chat.id, "❌ Please select or type your state.", reply_markup=get_indian_states_keyboard())
        bot.register_next_step_handler(message, lambda m: process_state(m, country))
        return
    bot.send_message(message.chat.id, "⚧ Select Gender:", reply_markup=get_gender_keyboard())
    bot.register_next_step_handler(message, lambda m: process_gender(m, country, state))

def process_gender(message, country, state):
    if is_command(message.text): return
    text = (message.text or "").strip().title()
    if not text:
        bot.send_message(message.chat.id, "❌ Please select gender.", reply_markup=get_gender_keyboard())
        bot.register_next_step_handler(message, lambda m: process_gender(m, country, state))
        return
    bot.send_message(message.chat.id, "🎂 Select Age:", reply_markup=get_age_keyboard())
    bot.register_next_step_handler(message, lambda m: process_age(m, country, state, text))

def process_age(message, country, state, gender):
    if is_command(message.text): return
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
    if is_command(message.text): return
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
            '''INSERT INTO users (user_id, username, country, state, gender, age, partner, match_time)
               VALUES (?,?,?,?,?,?,NULL,NULL)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, country=excluded.country, state=excluded.state,
                 gender=excluded.gender, age=excluded.age''',
            (message.from_user.id, message.from_user.username, country, state, gender, age)
        )
        conn.commit()
        bot.send_message(message.chat.id, "✅ Profile Saved!\nUse /find to look for a chat partner.", reply_markup=types.ReplyKeyboardRemove())
    except sqlite3.Error as e:
        bot.send_message(message.chat.id, "❌ Error saving profile. Try /start again.")
        logger.error(f"DB Error while saving profile for {message.from_user.id}: {e}")
    finally:
        unmark_registering(message.from_user.id)

# ---------------- Media Block for first 60 seconds ----------------
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'video_note'])
def block_media(message):
    user_id = message.from_user.id
    time_left = get_time_left(user_id)
    if time_left > 0:
        bot.reply_to(message, f"📸 Media sharing is allowed after **{time_left} seconds**.")
        return
    relay_message(message)

# ---------------- Relay Messages ----------------
@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    if is_command(message.text):
        return
    if is_registering(message.from_user.id):
        return
    relay_message(message)

def relay_message(message):
    profile = get_profile(message.from_user.id)
    if not profile or not profile[6]:
        bot.send_message(message.chat.id, "ℹ️ You're not in a chat. Use /find to get matched.")
        return
    partner_id = profile[6]
    try:
        if message.content_type == 'text':
            bot.send_message(partner_id, message.text)
        else:
            bot.forward_message(partner_id, message.chat.id, message.message_id)
    except telebot.apihelper.ApiException as e:
        bot.send_message(message.chat.id, "⚠️ Couldn't deliver your message.")
        logger.warning(f"Relay error from {message.from_user.id} to {partner_id}: {e}")

# ---------------- Commands Menu ----------------
bot.set_my_commands([
    types.BotCommand("start", "Register / restart"),
    types.BotCommand("help", "Show available commands"),
    types.BotCommand("check", "Check your profile & status"),
    types.BotCommand("find", "Find a chat partner"),
    types.BotCommand("end", "End current chat"),
    types.BotCommand("exit", "Leave the chat"),
    types.BotCommand("block", "Block current partner"),
    types.BotCommand("report", "Report current partner"),
])

logger.info("🤖 Bot Started - Final Version")
bot.infinity_polling(none_stop=True, interval=0, timeout=20)
