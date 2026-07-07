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

INDIAN_STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Other State"]

COMMON_COUNTRIES = ["United States", "India", "United Kingdom", "Canada", "Australia", "Germany", "France", "Japan", "Brazil", "Russia", "China", "Mexico", "Italy", "Spain", "South Korea", "Nigeria", "Turkey", "Other"]

AGE_RANGES = ["13-17", "18-20", "21-24", "25-30", "31-40", "41-50", "51+"]

def get_country_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for c in COMMON_COUNTRIES: markup.add(types.KeyboardButton(c))
    return markup

def get_indian_states_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for s in INDIAN_STATES: markup.add(types.KeyboardButton(s))
    return markup

def get_gender_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("Male"), types.KeyboardButton("Female"))
    markup.add(types.KeyboardButton("Other"))
    return markup

def get_age_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for a in AGE_RANGES: markup.add(types.KeyboardButton(a))
    markup.add(types.KeyboardButton("Custom Age"))
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "👋 Welcome! Select your country:", reply_markup=get_country_keyboard())
    bot.register_next_step_handler(message, process_country)

def process_country(message):
    country = message.text.strip() if message.text else ""
    if country == "India":
        bot.send_message(message.chat.id, "🇮🇳 Select your State:", reply_markup=get_indian_states_keyboard())
        bot.register_next_step_handler(message, process_state, country)
    elif country == "Other":
        bot.send_message(message.chat.id, "🌍 Enter your country name:")
        bot.register_next_step_handler(message, process_custom_country)
    else:
        ask_state(message, country)

def process_custom_country(message):
    country = message.text.strip().title() if message.text else ""
    ask_state(message, country)

def ask_state(message, country):
    bot.send_message(message.chat.id, f"📍 Enter your State/Region:\nCountry: <b>{country}</b>", parse_mode='HTML', reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, process_state, country)

def process_state(message, country):
    state = message.text.strip().title() if message.text else ""
    bot.send_message(message.chat.id, "⚧ Select your Gender:", reply_markup=get_gender_keyboard())
    bot.register_next_step_handler(message, process_gender, country, state)

def process_gender(message, country, state):
    gender = message.text.capitalize() if message.text else "Other"
    bot.send_message(message.chat.id, "🎂 Select your Age:", reply_markup=get_age_keyboard())
    bot.register_next_step_handler(message, process_age, country, state, gender)

# Age and save functions (same as before)
def process_age(message, country, state, gender):
    age_text = message.text.strip() if message.text else ""
    if age_text == "Custom Age":
        bot.send_message(message.chat.id, "Enter your exact age (13-100):")
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
        bot.send_message(message.chat.id, "❌ Invalid age.")
        bot.register_next_step_handler(message, process_age, country, state, gender)

def process_custom_age(message, country, state, gender):
    try:
        age = int(message.text.strip())
        if 13 <= age <= 100:
            save_user_profile(message, country, state, gender, age)
        else:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "❌ Enter valid age (13-100)")
        bot.register_next_step_handler(message, process_custom_age, country, state, gender)

def save_user_profile(message, country, state, gender, age):
    conn, cursor = get_db()
    try:
        cursor.execute('''INSERT OR REPLACE INTO users (user_id, username, country, state, gender, age, partner)
                         VALUES (?,?,?,?,?,?,NULL)''', (message.from_user.id, message.from_user.username, country, state, gender, age))
        conn.commit()
        bot.send_message(message.chat.id, "✅ Profile Saved!\nUse /help", reply_markup=types.ReplyKeyboardRemove())
    except:
        bot.send_message(message.chat.id, "❌ Error.")

# Other commands (help, check, find, end, relay) remain the same as previous version

print("🤖 Bot Started (Fixed duplicate messages)")
bot.infinity_polling()
