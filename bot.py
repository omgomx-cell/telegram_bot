import telebot
from telebot import types
import sqlite3
import threading

TOKEN = "8876721186:AAGOFp-tniETzLWBLSNT_mmmOneIp2KrKtU"
bot = telebot.TeleBot(TOKEN)

local = threading.local()

def get_db():
    if not hasattr(local, 'conn'):
        local.conn = sqlite3.connect('users.db', check_same_thread=False, timeout=15)
        local.cursor = local.conn.cursor()
        local.cursor.execute('''CREATE TABLE IF NOT EXISTS users
            (user_id INTEGER PRIMARY KEY, username TEXT, country TEXT, 
             state TEXT, gender TEXT, age INTEGER, partner INTEGER)''')
        local.conn.commit()
    return local.conn, local.cursor

waiting_users = []

# ==================== KEYBOARDS ====================
COMMON_COUNTRIES = ["United States", "India", "United Kingdom", "Canada", "Australia", "Germany", "France", "Japan", "Brazil", "Russia", "China", "Mexico", "Italy", "Spain", "South Korea", "Nigeria", "Turkey", "Other"]

INDIAN_STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Other State"]

AGE_RANGES = ["13-17", "18-20", "21-24", "25-30", "31-40", "41-50", "51+"]

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

# ==================== START ====================

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Welcome to Anonymous Chat Matcher!\nSelect your country:",
        reply_markup=get_country_keyboard()
    )

@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(message.chat.id, "**Commands:**\n/start - Register\n/check - Status\n/find - Find Partner\n/end - End Chat", parse_mode='HTML')

# Country Handler
@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    if message.text.startswith('/'):
        return

    # This prevents duplicate handlers by checking user state
    # Simple version - we'll use step handlers carefully

    if message.text in COMMON_COUNTRIES or message.text == "Other":
        process_country(message)
    else:
        # Default relay or other handling
        relay_message(message)

def process_country(message):
    country = message.text.strip()
    if country == "India":
        bot.send_message(message.chat.id, "🇮🇳 Select your State:", reply_markup=get_indian_states_keyboard())
        bot.register_next_step_handler(message, process_state, country)
    elif country == "Other":
        bot.send_message(message.chat.id, "🌍 Enter country name:")
        bot.register_next_step_handler(message, process_custom_country)
    else:
        ask_state(message, country)

def process_custom_country(message):
    country = message.text.strip().title()
    ask_state(message, country)

def ask_state(message, country):
    bot.send_message(message.chat.id, f"📍 Enter State/Region:\nCountry: <b>{country}</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, process_state, country)

def process_state(message, country):
    state = message.text.strip().title()
    bot.send_message(message.chat.id, "⚧ Select Gender:", reply_markup=get_gender_keyboard())
    bot.register_next_step_handler(message, process_gender, country, state)

def process_gender(message, country, state):
    gender = message.text.capitalize()
    bot.send_message(message.chat.id, "🎂 Select Age:", reply_markup=get_age_keyboard())
    bot.register_next_step_handler(message, process_age, country, state, gender)

def process_age(message, country, state, gender):
    age_text = message.text.strip()
    if age_text == "Custom Age":
        bot.send_message(message.chat.id, "Enter exact age (13-100):")
        bot.register_next_step_handler(message, process_custom_age, country, state, gender)
        return
    try:
        if "-" in age_text:
            age = int(age_text.split('-')[0])
        elif "+" in age_text:
            age = int(age_text.replace('+', ''))
        else:
            age = int(age_text)
        if 13 <= age <= 100:
            save_user_profile(message, country, state, gender, age)
        else:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "❌ Invalid age. Try again.")
        bot.register_next_step_handler(message, process_age, country, state, gender)

def process_custom_age(message, country, state, gender):
    try:
        age = int(message.text.strip())
        if 13 <= age <= 100:
            save_user_profile(message, country, state, gender, age)
        else:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "❌ Invalid number.")
        bot.register_next_step_handler(message, process_custom_age, country, state, gender)

def save_user_profile(message, country, state, gender, age):
    conn, cursor = get_db()
    try:
        cursor.execute('''INSERT OR REPLACE INTO users 
                         (user_id, username, country, state, gender, age, partner)
                         VALUES (?,?,?,?,?,?,NULL)''',
                      (message.from_user.id, message.from_user.username, country, state, gender, age))
        conn.commit()
        bot.send_message(message.chat.id, "✅ Profile Saved Successfully!\nUse /help", reply_markup=types.ReplyKeyboardRemove())
    except:
        bot.send_message(message.chat.id, "❌ Error saving profile.")

# Add your other commands (/check, /find, /end, relay) here as in previous versions

print("🤖 Bot Started - Stable Version")
bot.infinity_polling()
