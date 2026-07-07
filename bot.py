import telebot
from telebot import types
import sqlite3
import threading
import time

TOKEN = "8876721186:AAGOFp-tniETzLWBLSNT_mmmOneIp2KrKtU"
bot = telebot.TeleBot(TOKEN)

# Thread-safe Database
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

# ==================== INDIAN STATES ====================
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Puducherry", "Other State"
]

# ==================== KEYBOARDS ====================
COMMON_COUNTRIES = [
    "United States", "India", "United Kingdom", "Canada", "Australia",
    "Germany", "France", "Japan", "Brazil", "Russia", "China",
    "Mexico", "Italy", "Spain", "South Korea", "Nigeria", "Turkey", "Other"
]

AGE_RANGES = ["13-17", "18-20", "21-24", "25-30", "31-40", "41-50", "51+"]

def get_country_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for country in COMMON_COUNTRIES:
        markup.add(types.KeyboardButton(country))
    return markup

def get_indian_states_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for state in INDIAN_STATES:
        markup.add(types.KeyboardButton(state))
    return markup

def get_gender_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("Male"), types.KeyboardButton("Female"))
    markup.add(types.KeyboardButton("Other"))
    return markup

def get_age_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    for age in AGE_RANGES:
        markup.add(types.KeyboardButton(age))
    markup.add(types.KeyboardButton("Custom Age"))
    return markup

# ==================== COMMANDS ====================

@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(message.chat.id, """**📋 Available Commands**

/start - Register / Setup Profile
/check - Check your status
/profile - View your profile
/find - Find a partner
/end - End current chat
/help - Show this menu""", parse_mode='HTML')

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Welcome to Anonymous Chat Matcher!\n\nSelect your country:",
        reply_markup=get_country_keyboard()
    )
    bot.register_next_step_handler(message, process_country)

def process_country(message):
    country = message.text.strip() if message.text else ""
    
    if country == "India":
        bot.send_message(
            message.chat.id,
            "🇮🇳 Select your State:",
            reply_markup=get_indian_states_keyboard()
        )
        bot.register_next_step_handler(message, process_state, country)
    elif country == "Other":
        bot.send_message(message.chat.id, "🌍 Please type your country name:")
        bot.register_next_step_handler(message, process_custom_country)
    else:
        ask_state(message, country)

def process_custom_country(message):
    country = message.text.strip().title() if message.text else ""
    ask_state(message, country)

def ask_state(message, country):
    bot.send_message(
        message.chat.id,
        f"📍 Enter your State / Region:\nCountry: <b>{country}</b>",
        parse_mode='HTML',
        reply_markup=types.ReplyKeyboardRemove()
    )
    bot.register_next_step_handler(message, process_state, country)

def process_state(message, country):
    state = message.text.strip().title() if message.text else ""
    bot.send_message(
        message.chat.id,
        "⚧ Select your Gender:",
        reply_markup=get_gender_keyboard()
    )
    bot.register_next_step_handler(message, process_gender, country, state)

def process_gender(message, country, state):
    gender = message.text.capitalize() if message.text else "Other"
    bot.send_message(
        message.chat.id,
        "🎂 Select your Age:",
        reply_markup=get_age_keyboard()
    )
    bot.register_next_step_handler(message, process_age, country, state, gender)

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
        bot.send_message(message.chat.id, "❌ Invalid age. Please try again.")
        bot.register_next_step_handler(message, process_age, country, state, gender)

def process_custom_age(message, country, state, gender):
    try:
        age = int(message.text.strip())
        if 13 <= age <= 100:
            save_user_profile(message, country, state, gender, age)
        else:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "❌ Please enter a valid number (13-100).")
        bot.register_next_step_handler(message, process_custom_age, country, state, gender)

def save_user_profile(message, country, state, gender, age):
    conn, cursor = get_db()
    try:
        cursor.execute('''INSERT OR REPLACE INTO users 
                         (user_id, username, country, state, gender, age, partner)
                         VALUES (?,?,?,?,?,?,NULL)''',
                      (message.from_user.id, message.from_user.username, country, state, gender, age))
        conn.commit()
        bot.send_message(
            message.chat.id,
            "✅ **Profile Saved Successfully!**\n\nUse /help to see all commands.",
            parse_mode='HTML',
            reply_markup=types.ReplyKeyboardRemove()
        )
    except:
        bot.send_message(message.chat.id, "❌ Error saving profile.")

# ==================== STATUS & MATCHING ====================

@bot.message_handler(commands=['check', 'profile'])
def check_status(message):
    conn, cursor = get_db()
    user_id = message.from_user.id
    cursor.execute('SELECT country, state, gender, age, partner FROM users WHERE user_id=?', (user_id,))
    user = cursor.fetchone()
    if not user:
        bot.send_message(message.chat.id, "❌ You are not registered. Use /start")
        return
    country, state, gender, age, partner = user
    status = f"**📊 Your Status**\n\n🌍 {country}\n📍 {state}\n⚧ {gender}\n🎂 {age}\n\n"
    if partner:
        status += "🟢 In active chat"
    elif user_id in waiting_users:
        status += "⏳ Waiting for match..."
    else:
        status += "Use /find to search"
    bot.send_message(message.chat.id, status, parse_mode='HTML')

@bot.message_handler(commands=['find'])
def find_partner(message):
    conn, cursor = get_db()
    user_id = message.from_user.id
    cursor.execute('SELECT * FROM users WHERE user_id=?', (user_id,))
    if not cursor.fetchone():
        bot.send_message(message.chat.id, "Please register first with /start")
        return

    if waiting_users:
        partner_id = waiting_users.pop(0)
        cursor.execute('UPDATE users SET partner=? WHERE user_id=?', (partner_id, user_id))
        cursor.execute('UPDATE users SET partner=? WHERE user_id=?', (user_id, partner_id))
        conn.commit()
        bot.send_message(partner_id, "🎉 Matched! Say hi 👋")
        bot.send_message(user_id, "🎉 Matched! Say hi 👋")
    else:
        if user_id not in waiting_users:
            waiting_users.append(user_id)
        bot.send_message(user_id, "🔍 Looking for a match...")

@bot.message_handler(commands=['end'])
def end_chat(message):
    conn, cursor = get_db()
    user_id = message.from_user.id
    cursor.execute('SELECT partner FROM users WHERE user_id=?', (user_id,))
    result = cursor.fetchone()
    if result and result[0]:
        partner_id = result[0]
        cursor.execute('UPDATE users SET partner=NULL WHERE user_id IN (?,?)', (user_id, partner_id))
        conn.commit()
        bot.send_message(partner_id, "👋 Chat ended.")
    bot.send_message(user_id, "👋 Chat ended.")

@bot.message_handler(func=lambda m: True)
def relay_message(message):
    if message.text and message.text.startswith('/'):
        return
    conn, cursor = get_db()
    user_id = message.from_user.id
    cursor.execute('SELECT partner FROM users WHERE user_id=?', (user_id,))
    result = cursor.fetchone()
    if result and result[0]:
        try:
            bot.send_message(result[0], f"👤 {message.text}")
        except:
            bot.send_message(user_id, "❌ Could not send message.")
    else:
        bot.send_message(user_id, "You are not in a chat.\nUse /find")

print("🤖 Bot Started Successfully with Indian States Support!")
bot.infinity_polling()