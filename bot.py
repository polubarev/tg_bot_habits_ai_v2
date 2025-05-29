import os
import datetime
import threading
import time
import logging
import schedule
import telebot
import gspread
from google.oauth2.service_account import Credentials
from telebot import types
from dotenv import load_dotenv
from validate_config import validate_habits, config_schema
import html
import pytz
from openai import OpenAI
from flask import Flask, request, jsonify
from google.cloud import storage
import json
from jsonschema import validate as js_validate

# Load environment variables from .env file
load_dotenv(override=True)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

debug = False

# Use environment variables and default values since local config.json is no longer needed.
if debug:
    WEBHOOK_URL = os.getenv('WEBHOOK_URL_LOCAL')
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN_TEST')

else:
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
REMINDER_TIME = "09:00"  # Default reminder time.

# Google Sheets Service Account configuration
# Google Sheets + GCS scopes
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
STORAGE_SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]
all_scopes = SCOPES + STORAGE_SCOPES

# Load your service‑account JSON once
credentials_path = "../secrets/google-credentials"
try:
    logging.info("Trying use Google Cloud Run secret for Sheets/Storage creds.")
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    logging.info(f"Using credentials from Cloud Run secrets {credentials_path}.")
except FileNotFoundError:
    logging.info("Cloud Run secret not found; falling back to local JSON.")
    svc_file = os.getenv('SERVICE_ACCOUNT_FILE', 'google-credentials.json')
    creds = Credentials.from_service_account_file(svc_file, scopes=SCOPES)
    logging.info(f"Using credentials from local file {svc_file}.")

# Expand those credentials to include the Storage scope
if hasattr(creds, "with_scopes"):
    gcs_creds = creds.with_scopes(all_scopes)
else:
    # Fallback in case your Credentials class doesn't support with_scopes
    gcs_creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=all_scopes
    )

# Authorize gspread with the original Sheets‑only creds
gc = gspread.authorize(creds)
# (… your gspread check here …)

# ——— GCS client for persisting user settings ————————————————
SETTINGS_BUCKET = os.getenv('SETTINGS_BUCKET')
if not SETTINGS_BUCKET:
    raise RuntimeError("SETTINGS_BUCKET environment variable is required")

_storage_client = storage.Client(
    credentials=gcs_creds,  # <-- use gcs_creds, not creds
    project=gcs_creds.project_id
)
_settings_bucket = _storage_client.bucket(SETTINGS_BUCKET)

client = OpenAI(api_key=OPENAI_API_KEY)

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Conversation states
AWAITING_INPUT = 'AWAITING_INPUT'
CONFIRMING = 'CONFIRMING'
EDITING = 'EDITING'
MANUAL_INPUT = 'MANUAL_INPUT'
SELECTING_DATE = 'SELECTING_DATE'
AWAITING_CUSTOM_DATE = 'AWAITING_CUSTOM_DATE'
UPDATING_CONFIG = 'UPDATING_CONFIG'
DREAM_INPUT = 'DREAM_INPUT'  # New state for dream input
DREAM_CONFIRMING = 'DREAM_CONFIRMING'  # New state for dream confirmation
DREAM_EDITING = 'DREAM_EDITING'  # New state for dream editing
THOUGHTS_INPUT = 'THOUGHTS_INPUT'  # New state for thoughts input
THOUGHTS_CONFIRMING = 'THOUGHTS_CONFIRMING'  # New state for thoughts confirmation
THOUGHTS_EDITING = 'THOUGHTS_EDITING'  # New state for thoughts editing

FULL_CONFIGs = {}  # per-user configs
user_timezones = {}  # per-user timezones
user_states = {}
user_data = {}
user_sheets = {}
active_users = set()
USER_HABIT_PROPS: dict[int, tuple[dict, list]] = {}

# Create a global keyboard with command buttons
command_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
command_markup.add('/habits', '/manual', '/dream', '/thoughts')  # Added /dream and /thoughts commands
command_markup.add('/help', '/cancel')
command_markup.add('/update_config', '/set_sheet')  # Added /set_sheet command

# Add global variable for setup completion tracking
user_setup_complete = set()

app = Flask(__name__)  # Initialize Flask app


def save_user_settings(user_id: int):
    """Persist this user's sheet + config + timezone to GCS."""
    data = {
        'sheet_id': user_sheets.get(user_id),
        'config': FULL_CONFIGs.get(user_id, {}),
        'timezone': user_timezones.get(user_id, 'UTC'),
    }
    blob = _settings_bucket.blob(f'settings/{user_id}.json')
    blob.upload_from_string(json.dumps(data), content_type='application/json')
    logging.info(f"Saved settings for user {user_id} to GCS")


def load_all_user_settings():
    """Load every user's JSON from GCS into in‑memory dicts."""
    prefix = 'settings/'
    for blob in _settings_bucket.list_blobs(prefix=prefix):
        # blob.name == 'settings/12345.json'
        user_id = int(blob.name[len(prefix):-5])  # strip prefix + ".json"
        data = json.loads(blob.download_as_text())
        user_sheets[user_id] = data.get('sheet_id')
        FULL_CONFIGs[user_id] = data.get('config', {})
        user_timezones[user_id] = data.get('timezone', 'UTC')
        user_setup_complete.add(user_id)
        logging.info(f"Loaded settings for user {user_id} from GCS")


@app.route("/")
def health_check():
    return "Bot is running", 200


# --- Google Sheets helper functions ---

def ensure_raw_input_column(sheet: gspread.worksheet):
    header = sheet.row_values(1)
    lower = [h.lower() for h in header]

    if "raw_input" not in lower:
        # find the index of "date" (0-based)
        try:
            date_idx = lower.index("date")
        except ValueError:
            date_idx = 1


        sheet.insert_cols(values=[['raw_input']], col=date_idx+2)




def append_to_user_sheet(user_id, date_val, datetime_val, json_data):
    """
    Append a row of habit data to the user's linked Google Sheet by mapping the JSON keys
    to the existing columns in the sheet. For each column header:
      - If the header is 'date', use date_val.
      - If the header is 'datetime', use datetime_val.
      - If the header is 'raw_input', use the raw input text.
      - If the header matches a key in json_data, use its value.
      - Otherwise, leave it blank.
    """
    if user_id not in user_sheets:
        logging.info(f"User {user_id} has not linked a Google Sheet.")
        return False
    
    sheet_id = user_sheets[user_id]
    try:
        sheet = gc.open_by_key(sheet_id).worksheet("Diary Raw")
        header = sheet.row_values(1)  # Retrieve header row from the sheet
        
        # Log the header and data for debugging
        logging.info(f"Sheet header: {header}")
        logging.info(f"JSON data: {json_data}")
        logging.info(f"Raw input: {user_data[user_id].get('user_input', '')}")
        
        row = []
        for col in header:
            if col.lower() == "datetime":
                row.append(datetime_val)
            elif col.lower() == "date":
                row.append(date_val)
            elif col.lower() == "raw_input":
                raw_input = user_data[user_id].get('user_input', '')
                row.append(raw_input)
            elif col in json_data:
                value = json_data[col]
                # Convert any non-string values to string
                if not isinstance(value, str):
                    value = str(value)
                row.append(value)
            else:
                row.append("")
        
        # Log the final row for debugging
        logging.info(f"Appending row: {row}")
        
        # Append the row to the sheet
        sheet.append_row(row, value_input_option='USER_ENTERED')
        logging.info(f"Successfully appended habit data for user {user_id} to sheet {sheet_id}.")
        return True
    except Exception as e:
        logging.error(f"Error updating sheet for user {user_id}: {e}")
        return False


def upload_to_google_sheets(df):
    """
    (Optional) Upload an aggregated report to a common Google Sheet.
    This function uses a sheet titled "Diary_test".
    """
    logging.info("Uploading aggregated report to Google Sheets.")
    try:
        sheet = gc.open("Diary_test").sheet1
    except Exception as e:
        logging.error(f"Error opening Google Sheet 'Diary_test': {e}")
        return

    try:
        sheet.clear()
    except Exception as e:
        logging.error(f"Error clearing Google Sheet: {e}")
        return

    data = [df.columns.values.tolist()] + df.values.tolist()

    try:
        sheet.update(values=data, range_name="A1")
        logging.info("Google Sheet has been updated successfully.")
    except Exception as e:
        logging.error(f"Error updating Google Sheet: {e}")


# --- End Google Sheets helpers ---

def parse_habit_properties(habits_config):
    habit_properties = {}
    required_habits = []
    for habit_name, habit_info in habits_config.items():
        habit_type = habit_info['type']
        if isinstance(habit_type, list):
            habit_type = [str(t) for t in habit_type]
        else:
            habit_type = str(habit_type)
        habit_property = {
            "type": habit_type,
            "description": habit_info['description']
        }
        if 'minimum' in habit_info:
            habit_property['minimum'] = habit_info['minimum']
        if 'maximum' in habit_info:
            habit_property['maximum'] = habit_info['maximum']
        habit_properties[habit_name] = habit_property
        required_habits.append(habit_name)
    return habit_properties, required_habits


# Parse habit properties and required habits
# habit_properties, required_habits = parse_habit_properties(FULL_CONFIGs[user_id].get("habits", {}))


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} started the bot.")
    active_users.add(user_id)
    if user_id not in user_setup_complete:
        welcome_text = (
            "Welcome to the Habit Tracker Bot!\n\n"
            "Before using other commands, please complete the setup:\n"
            "1️⃣ Create a new Google Sheet.\n"
            "2️⃣ Share the sheet with the bot's account email tg-habits-bot@tg-bot-sso.iam.gserviceaccount.com.\n"
            "3️⃣ Get the Sheet ID from the URL (the string between '/d/' and '/edit').\n"
            "4️⃣ Link your Google Sheet using: /set_sheet <your_sheet_id>\n"
            "5️⃣ Update the configuration using: /update_config (follow the example provided).\n\n"
            "After completing these steps, you can use other commands."
        )
        bot.send_message(message.chat.id, welcome_text, reply_markup=command_markup)
    else:
        # ...existing start flow if already set up...
        welcome_text = "Welcome back! You can now use the bot commands."
        bot.send_message(message.chat.id, welcome_text, reply_markup=command_markup)


@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = (
        "Habit Tracker Bot Help:\n"
        "- /start: Start the bot and get a welcome message.\n"
        "- /habits: Begin tracking your habits by describing your day.\n"
        "- /manual: Manually input your habits in JSON format.\n"
        "- /dream: Record your dreams and save them to a separate sheet.\n"  # Added dream command
        "- /thoughts: Record your thoughts and save them to a separate sheet.\n"  # Added thoughts command
        "- /cancel: Cancel the current habit tracking process.\n"
        "- /help: Show this help message.\n"
        "- /update_config: Update the bot configuration.\n"
        "- /set_sheet: Link your personal Google Sheet (see instructions in /start).\n\n"
        "After initiating habit tracking with /habits:\n"
        "1. Choose the date for your entry.\n"
        "2. Provide a description of your day, including the habits listed.\n"
        "3. The bot will extract your habits and present them for confirmation.\n"
        "4. If the data is correct, reply with 'Yes' to save it.\n"
        "5. If corrections are needed, reply with 'No' and provide corrections in text or voice."
    )
    bot.send_message(message.chat.id, help_text, reply_markup=command_markup)


@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated /cancel command.")
    user_states[user_id] = None
    user_data[user_id] = {}
    bot.send_message(message.chat.id, "Your current habit tracking process has been cancelled.",
                     reply_markup=command_markup)


def cancel_process(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} cancelled the process.")
    user_states[user_id] = None
    user_data[user_id] = {}
    bot.send_message(message.chat.id, "Your current habit tracking process has been cancelled.",
                     reply_markup=command_markup)


@bot.message_handler(commands=['habits'])
def habits_command(message):
    if not ensure_setup(message):
        return
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated /habits command.")
    user_states[user_id] = SELECTING_DATE
    user_data[user_id] = {}
    active_users.add(user_id)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Today', 'Yesterday', 'Custom Date')
    markup.add('Cancel')
    markup.add('/habits', '/manual', '/help')
    bot.send_message(message.chat.id, "For which date would you like to record your habits?", reply_markup=markup)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == SELECTING_DATE)
def handle_date_selection(message):
    user_id = message.from_user.id
    text = message.text.lower()
    if text == 'cancel':
        cancel_process(message)
        return
    elif text == 'today':
        user_data[user_id]['date'] = datetime.datetime.now().strftime('%Y-%m-%d')
    elif text == 'yesterday':
        user_data[user_id]['date'] = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    elif text == 'custom date':
        bot.send_message(message.chat.id, "Please enter the date in YYYY-MM-DD format.", reply_markup=command_markup)
        user_states[user_id] = AWAITING_CUSTOM_DATE
        return
    else:
        bot.send_message(message.chat.id, "Invalid option. Please select 'Today', 'Yesterday', or 'Custom Date'.",
                         reply_markup=command_markup)
        return

    user_states[user_id] = AWAITING_INPUT
    prompt_user_for_input(message)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == AWAITING_CUSTOM_DATE)
def handle_custom_date(message):
    user_id = message.from_user.id
    if message.text.lower() == 'cancel':
        cancel_process(message)
        return
    try:
        date = datetime.datetime.strptime(message.text.strip(), '%Y-%m-%d')
        user_data[user_id]['date'] = date.strftime('%Y-%m-%d')
        user_states[user_id] = AWAITING_INPUT
        prompt_user_for_input(message)
    except ValueError:
        bot.send_message(message.chat.id, "Invalid date format. Please enter the date in YYYY-MM-DD format.",
                         reply_markup=command_markup)


def prompt_user_for_input(message):
    user_id = message.from_user.id

    habit_properties, _ = USER_HABIT_PROPS.get(user_id, ({}, []))

    habits_list = ""
    for habit_name, habit_info in FULL_CONFIGs[user_id].get("habits", {}).items():
        habits_list += f"- *{habit_name}*: {habit_info['description']}\n"
    date_str = user_data[user_id]['date']
    reminder_message = (
            f"Please describe your day for {date_str}, either by text or voice message.\n\n"
            "Please include the following habits:\n" + habits_list
    )
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Cancel')
    markup.add('/habits', '/manual', '/help')
    bot.send_message(message.chat.id, reminder_message, parse_mode='Markdown', reply_markup=markup)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == AWAITING_INPUT,
                     content_types=['text', 'voice'])
def handle_input(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing input in state AWAITING_INPUT.")
    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return

    # Store the raw input first
    if message.voice:
        user_input = transcribe_voice_message(message)
        if user_input is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
        # Store the transcribed text as raw input
        user_data[user_id]['user_input'] = user_input
    else:
        # Store the raw text input
        user_input = message.text
        user_data[user_id]['user_input'] = user_input

    habit_properties, required_habits = parse_habit_properties(FULL_CONFIGs[user_id].get("habits", {}))

    function_parameters = {
        "type": "object",
        "properties": habit_properties,
        "required": required_habits
    }

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a bot that extracts user habits from their daily description. "
                        "Extract the following habits and provide the output in JSON format. "
                        "Ensure that the 'diary' field is is matching the input description fully, but fix typos and "
                        "punctuation."
                    )
                },
                {"role": "user", "content": user_input}
            ],
            functions=[
                {
                    "name": "extract_habits",
                    "parameters": function_parameters
                }
            ],
            function_call={"name": "extract_habits"}
        )
    except Exception as e:
        logging.error(f"OpenAI API error: {e}")
        bot.reply_to(message, "Sorry, there was an error processing your input. Please try again later.")
        return

    try:
        json_output = response.choices[0].message.function_call.arguments
        json_data = json.loads(json_output)
        user_data[user_id]['json_output'] = json.dumps(json_data, ensure_ascii=False, indent=4)
        logging.info(f"Extracted data for user {user_id}: {user_data[user_id]['json_output']}")
    except (KeyError, json.JSONDecodeError) as e:
        logging.error(f"Error parsing OpenAI response for user {user_id}: {e}")
        bot.reply_to(message, "Sorry, I couldn't process your input. Please try again.")
        return

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
    markup.add('Cancel')
    markup.add('/habits', '/manual', '/help')
    bot.send_message(
        message.chat.id,
        f"Here is the extracted data:\n```json\n{user_data[user_id]['json_output']}\n```\nIs this correct?",
        parse_mode='Markdown',
        reply_markup=markup
    )
    user_states[user_id] = CONFIRMING


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == CONFIRMING)
def confirm(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is in CONFIRMING state.")
    user_response = message.text.lower()
    if user_response == 'cancel':
        cancel_process(message)
        return
    elif user_response == 'yes':
        # Save JSON locally
        # os.makedirs(DATA_DIR, exist_ok=True)
        current_datetime = datetime.datetime.now()
        date_val = user_data[user_id].get('date', current_datetime.strftime('%Y-%m-%d'))
        datetime_val = current_datetime.strftime('%Y-%m-%d %H:%M:%S')
        timestamp = current_datetime.strftime('%H-%M-%S')
        # file_path = os.path.join(DATA_DIR, f"{date_val}_{timestamp}_{user_id}.json")
        # with open(file_path, 'w', encoding='utf-8') as f:
        #     f.write(user_data[user_id]['json_output'])
        # bot.send_message(message.chat.id, "Your habits have been saved. Thank you!", reply_markup=command_markup)
        # logging.info(f"Data saved for user {user_id} at {file_path}")

        # If the user has linked a Google Sheet, map and append the habit data.
        if user_id in user_sheets:
            try:
                json_data = json.loads(user_data[user_id]['json_output'])
            except Exception as e:
                logging.error(f"Error parsing JSON for user {user_id}: {e}")
                json_data = {}
            appended = append_to_user_sheet(user_id, date_val, datetime_val, json_data)
            if appended:
                bot.send_message(
                    message.chat.id,
                    "Your habit data has also been appended to your Google Sheet.",
                    reply_markup=command_markup
                )
                # Aggregate latest record per day
                aggregate_diary(user_id)
            else:
                bot.send_message(
                    message.chat.id,
                    "⚠️ Failed to append data to your Google Sheet. Please ensure your sheet is shared correctly.",
                    reply_markup=command_markup
                )

        user_states[user_id] = None
        user_data[user_id] = {}
    elif user_response == 'no':
        bot.reply_to(message, "Please describe the corrections you'd like to make, either by text or voice message.")
        user_states[user_id] = EDITING
    else:
        bot.reply_to(message, "Please reply with 'Yes' or 'No'.")
        logging.info(f"User {user_id} provided invalid response: {message.text}")


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == EDITING,
                     content_types=['text', 'voice'])
def edit(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing corrections in EDITING state.")
    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return
    if message.voice:
        correction = transcribe_voice_message(message)
        if correction is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        correction = message.text

    habit_properties, required_habits = parse_habit_properties(FULL_CONFIGs[user_id].get("habits", {}))

    function_parameters = {
        "type": "object",
        "properties": habit_properties,
        "required": required_habits
    }

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a bot that extracts user habits from their daily description. "
                        "Extract the following habits and provide the output in JSON format. "
                        "Ensure that the 'diary' field is is matching the input description fully, but fix typos and "
                        "punctuation."
                    )
                },
                {"role": "user", "content": user_data[user_id]['user_input']},
                {"role": "assistant", "content": user_data[user_id]['json_output']},
                {"role": "user", "content": correction}
            ],
            functions=[
                {
                    "name": "extract_habits",
                    "parameters": function_parameters
                }
            ],
            function_call={"name": "extract_habits"}
        )
    except Exception as e:
        logging.error(f"OpenAI API error during correction for user {user_id}: {e}")
        bot.reply_to(message, "Sorry, there was an error processing your corrections. Please try again later.")
        return

    try:
        json_output = response.choices[0].message.function_call.arguments
        json_data = json.loads(json_output)
        user_data[user_id]['json_output'] = json.dumps(json_data, ensure_ascii=False, indent=4)
        logging.info(f"Updated data for user {user_id}: {user_data[user_id]['json_output']}")
    except (KeyError, json.JSONDecodeError) as e:
        logging.error(f"Error parsing OpenAI response during correction for user {user_id}: {e}")
        bot.reply_to(message, "Sorry, I couldn't process your corrections. Please try again.")
        return

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
    markup.add('Cancel')
    markup.add('/habits', '/manual', '/help')
    bot.send_message(
        message.chat.id,
        f"Updated data:\n```json\n{user_data[user_id]['json_output']}\n```\nIs this correct now?",
        parse_mode='Markdown',
        reply_markup=markup
    )
    user_states[user_id] = CONFIRMING


@bot.message_handler(commands=['manual'])
def manual_input_prompt(message):
    if not ensure_setup(message):
        return
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated manual input.")
    user_states[user_id] = MANUAL_INPUT
    active_users.add(user_id)
    bot.send_message(message.chat.id, "Please input your habits in JSON format.", reply_markup=command_markup)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == MANUAL_INPUT)
def manual_input(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing manual input.")
    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return
    json_text = message.text
    try:
        json_data = json.loads(json_text)
        user_data[user_id]['json_output'] = json.dumps(json_data, ensure_ascii=False, indent=4)
        # os.makedirs(DATA_DIR, exist_ok=True)
        date_str = datetime.datetime.now().strftime('%Y-%m-%d')
        timestamp = datetime.datetime.now().strftime('%H-%M-%S')
        # file_path = os.path.join(DATA_DIR, f"{date_str}_{timestamp}_{user_id}.json")
        # with open(file_path, 'w', encoding='utf-8') as f:
        #     f.write(user_data[user_id]['json_output'])
        # bot.send_message(message.chat.id, "Your manual input has been saved. Thank you!", reply_markup=command_markup)
        # logging.info(f"Manual data saved for user {user_id} at {file_path}")
        user_states[user_id] = None
        user_data[user_id] = {}
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error for user {user_id}: {e}")
        bot.reply_to(message, "Invalid JSON format. Please try again.")


@bot.message_handler(commands=['update_config'])
def update_config_command(message):
    user_id = message.from_user.id

    # 1️⃣ Ensure they've linked a sheet
    if user_id not in user_sheets:
        return bot.send_message(
            message.chat.id,
            "Please link your Google Sheet first using /set_sheet.",
            reply_markup=command_markup
        )

    logging.info(f"User {user_id} initiated /update_config")

    # 2️⃣ Mark state so next message is handled by handle_updated_config
    user_states[user_id] = UPDATING_CONFIG
    active_users.add(user_id)

    # 3️⃣ Fetch their existing config, if any
    user_cfg = FULL_CONFIGs.get(user_id)
    if not user_cfg:
        # No saved config → show example
        try:
            with open("config_example.json", "r", encoding="utf-8") as f:
                example_cfg = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read config_example.json: {e}")
            example_cfg = {}
        display = html.escape(json.dumps(example_cfg, ensure_ascii=False, indent=4))
        bot.send_message(
            message.chat.id,
            "No configuration found. Here's an example to get you started:\n"
            f"<pre>{display}</pre>\n\n"
            "Please send me your updated configuration in JSON format.",
            parse_mode='HTML',
            reply_markup=command_markup
        )
    else:
        # Show their current configuration
        display = html.escape(json.dumps(user_cfg, ensure_ascii=False, indent=4))
        bot.send_message(
            message.chat.id,
            "Here is your current configuration:\n"
            f"<pre>{display}</pre>\n\n"
            "Send me the updated configuration in JSON format.",
            parse_mode='HTML',
            reply_markup=command_markup
        )


# Updated helper function to synchronize sheet header (ensure "datetime" and "date" are first):
def sync_sheet_columns(user_id, updated_config):
    if user_id not in user_sheets:
        return
    sheet_id = user_sheets[user_id]
    try:
        sheet = gc.open_by_key(sheet_id).worksheet("Diary Raw")
        ensure_raw_input_column(sheet)
    except Exception:
        # If "Diary Raw" doesn't exist, use default sheet1 and rename it later if needed.
        sheet = gc.open_by_key(sheet_id).sheet1
    current_header = sheet.row_values(1)
    if not current_header:
        current_header = []
    # Ensure first three columns are "datetime", "date", and "raw_input"
    if len(current_header) < 1 or current_header[0].lower() != "datetime":
        # Remove any duplicates
        current_header = [col for col in current_header if col.lower() != "datetime"]
        current_header.insert(0, "datetime")
    if len(current_header) < 2 or current_header[1].lower() != "date":
        current_header = [col for col in current_header if col.lower() != "date"]
        current_header.insert(1, "date")
    if len(current_header) < 3 or current_header[2].lower() != "raw_input":
        current_header = [col for col in current_header if col.lower() != "raw_input"]
        current_header.insert(2, "raw_input")
    # Add new habit keys if not already present.
    new_habits = list(updated_config["habits"].keys())
    for habit in new_habits:
        if habit not in current_header:
            current_header.append(habit)
    sheet.update(values=[current_header], range_name="1:1")
    logging.info(f"Synchronized sheet header for user {user_id} to: {current_header}")


# New helper function to aggregate the diary:
def aggregate_diary(user_id):
    if user_id not in user_sheets:
        return
    sheet_id = user_sheets[user_id]
    try:
        spreadsheet = gc.open_by_key(sheet_id)
        raw_sheet = spreadsheet.worksheet("Diary Raw")
    except Exception as e:
        logging.error(f"Error opening Diary Raw for user {user_id}: {e}")
        return

    try:
        values = raw_sheet.get_all_values()
        if not values or len(values) < 2:
            logging.info(f"No data to aggregate for user {user_id}.")
            return
        header = values[0]
        # Get indices for 'datetime' and 'date'
        try:
            idx_datetime = header.index("datetime")
            idx_date = header.index("date")
        except ValueError:
            logging.error("Required columns 'datetime' or 'date' are missing.")
            return
        records = values[1:]
        aggregated = {}
        for row in records:
            # Ensure the row is complete
            if len(row) <= max(idx_datetime, idx_date):
                continue
            day = row[idx_date]
            dt_str = row[idx_datetime]
            try:
                dt = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            # Keep record if day not seen or dt is later than stored record.
            if day not in aggregated or dt > aggregated[day]["dt"]:
                aggregated[day] = {"row": row, "dt": dt}
        # Build aggregated data sorted by datetime:
        sorted_entries = sorted(aggregated.values(), key=lambda x: x["dt"])
        agg_rows = [header] + [entry["row"] for entry in sorted_entries]
        # Get or create the "Diary" worksheet.
        try:
            agg_sheet = spreadsheet.worksheet("Diary")
        except Exception:
            agg_sheet = spreadsheet.add_worksheet(title="Diary", rows=100, cols=len(header))
        agg_sheet.clear()
        agg_sheet.update(values=agg_rows, range_name="A1", value_input_option='USER_ENTERED')
        logging.info(f"Aggregated diary for user {user_id} with {len(agg_rows) - 1} records.")
    except Exception as e:
        logging.error(f"Error aggregating diary for user {user_id}: {e}")


@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id) == UPDATING_CONFIG)
def handle_updated_config(message):
    user_id = message.from_user.id
    text = message.text

    # Allow them to cancel
    if text.lower() == 'cancel':
        user_states[user_id] = None
        return bot.send_message(
            message.chat.id,
            "Configuration update cancelled.",
            reply_markup=command_markup
        )

    # Parse JSON
    try:
        new_cfg = json.loads(text)
    except json.JSONDecodeError as e:
        logging.error(f"User {user_id} sent invalid JSON: {e}")
        return bot.reply_to(
            message,
            "Invalid JSON format. Please fix and send it again."
        )

    # Validate schema
    try:
        js_validate(instance=new_cfg, schema=config_schema)
    except Exception as e:
        return bot.reply_to(
            message,
            f"Schema validation failed: {e.message}"
        )

    # Validate habits specifically
    ok, errors = validate_habits(new_cfg.get('habits', {}))
    if not ok:
        return bot.reply_to(
            message,
            "Habits config errors:\n" + "\n".join(errors)
        )

    # ✅ Everything's valid — update in-memory
    FULL_CONFIGs[user_id] = new_cfg

    props, reqs = parse_habit_properties(new_cfg.get("habits", {}))
    USER_HABIT_PROPS[user_id] = (props, reqs)

    user_timezones[user_id] = new_cfg.get('timezone', user_timezones.get(user_id, 'UTC'))

    # Sync your sheet headers now that habits may have changed
    sync_sheet_columns(user_id, new_cfg)

    # Persist to GCS so it survives redeploy
    save_user_settings(user_id)

    # Clear state and confirm
    user_states[user_id] = None
    user_setup_complete.add(user_id)
    logging.info(f"Configuration for user {user_id} updated and saved to GCS.")

    bot.send_message(
        message.chat.id,
        "Configuration has been updated and saved successfully! 🎉",
        reply_markup=command_markup
    )


# New helper function to create diary worksheets upon linking the sheet:
def create_diary_sheets(user_id):
    sheet_id = user_sheets.get(user_id)
    if not sheet_id:
        return

    try:
        spreadsheet = gc.open_by_key(sheet_id)

        # — Diary Raw —
        try:
            raw = spreadsheet.worksheet("Diary Raw")
        except Exception:
            raw = spreadsheet.add_worksheet(title="Diary Raw", rows=100, cols=10)
            # initialize header on brand-new sheet
            raw.update(
                values=[["datetime", "date", "raw_input"]],
                range_name="1:1"
            )
        ensure_raw_input_column(raw)

        # — Diary (aggregated) —
        try:
            diary = spreadsheet.worksheet("Diary")
        except Exception:
            diary = spreadsheet.add_worksheet(title="Diary", rows=100, cols=10)
            diary.update(
                values=[["datetime", "date", "raw_input"]],
                range_name="1:1"
            )
        ensure_raw_input_column(diary)

        # — Dreams —
        try:
            dreams = spreadsheet.worksheet("Dreams")
        except Exception:
            dreams = spreadsheet.add_worksheet(title="Dreams", rows=100, cols=10)
            dreams.update(
                values=[["datetime", "date", "raw_input", "dream"]],
                range_name="1:1"
            )
        else:
            ensure_raw_input_column(dreams)
            hdr = dreams.row_values(1)
            if "dream" not in hdr:
                hdr.append("dream")
                dreams.update(values=[hdr], range_name="1:1")

        # — Thoughts —
        try:
            thoughts = spreadsheet.worksheet("Thoughts")
        except Exception:
            thoughts = spreadsheet.add_worksheet(title="Thoughts", rows=100, cols=10)
            thoughts.update(
                values=[["datetime", "date", "raw_input", "thought"]],
                range_name="1:1"
            )
        else:
            ensure_raw_input_column(thoughts)
            hdr = thoughts.row_values(1)
            if "thought" not in hdr:
                hdr.append("thought")
                thoughts.update(values=[hdr], range_name="1:1")

        logging.info(f"Verified/created all worksheets with raw_input column for user {user_id}.")
    except Exception as e:
        logging.error(f"Error creating diary worksheets for user {user_id}: {e}")




# Updated /set_sheet command:
@bot.message_handler(commands=['set_sheet'])
def set_sheet(message):
    """
    Users send their Google Sheet ID to link it to their account.
    """
    user_id = message.from_user.id
    logging.info(f"ENTERED set_sheet handler for user {user_id}: text={message.text!r}")
    try:
        sheet_id = message.text.split()[1]  # Extract the Sheet ID
        user_sheets[user_id] = sheet_id
        save_user_settings(user_id)
        # Create Diary Raw and Diary worksheets upon linking.
        create_diary_sheets(user_id)
        bot.send_message(message.chat.id,
                         f"✅ Google Sheet linked successfully! Sheet ID: {sheet_id}\nNow, please update your configuration using /update_config.",
                         reply_markup=command_markup)
    except IndexError:
        bot.send_message(message.chat.id, "⚠️ Please provide a Sheet ID. Example:\n`/set_sheet <your_sheet_id>`",
                         parse_mode="Markdown")


def transcribe_voice_message(message):
    user_id = message.from_user.id
    logging.info(f"Transcribing voice message for user {user_id}.")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        voice_file_path = f'voice_note_{user_id}.ogg'
        with open(voice_file_path, 'wb') as f:
            f.write(downloaded_file)
        with open(voice_file_path, 'rb') as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        os.remove(voice_file_path)
        logging.info(f"Voice transcription for user {user_id}: {transcript.text}")
        return transcript.text
    except Exception as e:
        logging.error(f"Error transcribing voice message for user {user_id}: {e}")
        return None


def send_reminders():
    logging.info("Running reminders check…")
    for uid in list(user_setup_complete):
        tz = user_timezones.get(uid, 'UTC')
        try:
            now = datetime.datetime.now(pytz.timezone(tz))
        except:
            now = datetime.datetime.utcnow()
        if now.strftime('%H:%M') == REMINDER_TIME:
            bot.send_message(uid,
                             "⏰ Reminder: don't forget to track today's habits! Use /habits",
                             reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True)
                             .add('/habits', '/help', '/cancel')
                             )


def schedule_checker():
    schedule.every().day.at(REMINDER_TIME).do(send_reminders)
    while True:
        schedule.run_pending()
        time.sleep(30)


def ensure_setup(message):
    user_id = message.from_user.id
    if user_id not in user_setup_complete:
        bot.send_message(message.chat.id, "Please complete initial setup first:\nUse /set_sheet and /update_config.",
                         reply_markup=command_markup)
        return False
    return True


# Add the /dream command handler
@bot.message_handler(commands=['dream'])
def dream_command(message):
    if not ensure_setup(message):
        return
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated /dream command.")
    user_states[user_id] = DREAM_INPUT
    active_users.add(user_id)

    bot.send_message(message.chat.id,
                     "Please describe your dream, either by text or voice message.",
                     reply_markup=command_markup)


# Handler for dream input (text or voice)
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == DREAM_INPUT,
                     content_types=['text', 'voice'])
def handle_dream_input(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing dream input.")

    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return

    if message.voice:
        dream_text = transcribe_voice_message(message)
        if dream_text is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        dream_text = message.text

    # Store the dream text in user_data
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['dream_text'] = dream_text

    # Ask for confirmation
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
    markup.add('Cancel')
    markup.add('/habits', '/dream', '/help')

    bot.send_message(
        message.chat.id,
        f"Here is your dream description:\n\n\"{dream_text}\"\n\nDo you want to save it?",
        reply_markup=markup
    )
    user_states[user_id] = DREAM_CONFIRMING


# Handler for dream confirmation
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == DREAM_CONFIRMING)
def confirm_dream(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is confirming dream input.")
    user_response = message.text.lower()

    if user_response == 'cancel':
        cancel_process(message)
        return
    elif user_response == 'yes':
        # Save dream to Google Sheet
        if user_id in user_sheets:
            try:
                sheet_id = user_sheets[user_id]
                try:
                    sheet = gc.open_by_key(sheet_id).worksheet("Dreams")
                except Exception:
                    # Create Dreams sheet if it doesn't exist
                    spreadsheet = gc.open_by_key(sheet_id)
                    sheet = spreadsheet.add_worksheet(title="Dreams", rows=100, cols=4)
                    sheet.update(values=[["datetime", "date", "raw_input", "dream"]], range_name="A1")

                current_datetime = datetime.datetime.now()
                date_val = current_datetime.strftime('%Y-%m-%d')
                datetime_val = current_datetime.strftime('%Y-%m-%d %H:%M:%S')

                raw = user_data[user_id]['dream_text']
                sheet.append_row(
                    [datetime_val, date_val, raw, raw],
                    value_input_option='USER_ENTERED'
                )
                bot.send_message(message.chat.id, "Your dream has been saved successfully!",
                                 reply_markup=command_markup)
                logging.info(f"Dream saved for user {user_id}.")
            except Exception as e:
                logging.error(f"Error saving dream for user {user_id}: {e}")
                bot.send_message(message.chat.id,
                                 "Failed to save your dream. Please check if your Google Sheet is properly linked.",
                                 reply_markup=command_markup)
        else:
            bot.send_message(message.chat.id,
                             "You need to link a Google Sheet first. Use /set_sheet command.",
                             reply_markup=command_markup)

        # Clear user state and data
        user_states[user_id] = None
        user_data[user_id] = {}
    elif user_response == 'no':
        bot.reply_to(message, "Please provide the corrected dream description, either by text or voice message.")
        user_states[user_id] = DREAM_EDITING
    else:
        bot.reply_to(message, "Please reply with 'Yes' or 'No'.")
        logging.info(f"User {user_id} provided invalid response: {message.text}")


# Handler for dream editing
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == DREAM_EDITING,
                     content_types=['text', 'voice'])
def edit_dream(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is editing dream input.")

    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return

    if message.voice:
        corrected_dream = transcribe_voice_message(message)
        if corrected_dream is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        corrected_dream = message.text

    # Store the corrected dream
    user_data[user_id]['dream_text'] = corrected_dream

    # Ask for confirmation again
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
    markup.add('Cancel')
    markup.add('/habits', '/dream', '/help')

    bot.send_message(
        message.chat.id,
        f"Updated dream description:\n\n\"{corrected_dream}\"\n\nIs this correct now?",
        reply_markup=markup
    )
    user_states[user_id] = DREAM_CONFIRMING


# Add the /thoughts command handler
@bot.message_handler(commands=['thoughts'])
def thoughts_command(message):
    if not ensure_setup(message):
        return
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated /thoughts command.")
    user_states[user_id] = THOUGHTS_INPUT
    active_users.add(user_id)
    bot.send_message(message.chat.id,
                     "Please share your thoughts, either by text or voice message.",
                     reply_markup=command_markup)


# Handler for thoughts input (text or voice)
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == THOUGHTS_INPUT,
                     content_types=['text', 'voice'])
def handle_thoughts_input(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing thoughts input.")
    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return
    if message.voice:
        thought_text = transcribe_voice_message(message)
        if thought_text is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        thought_text = message.text
    user_data.setdefault(user_id, {})['thought_text'] = thought_text
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
    markup.add('Cancel')
    markup.add('/habits', '/thoughts', '/help')
    bot.send_message(
        message.chat.id,
        f"Here are your thoughts:\n\n\"{thought_text}\"\n\nDo you want to save it?",
        reply_markup=markup
    )
    user_states[user_id] = THOUGHTS_CONFIRMING


# Handler for thoughts confirmation
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == THOUGHTS_CONFIRMING)
def confirm_thoughts(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is confirming thoughts input.")
    user_response = message.text.lower()
    if user_response == 'cancel':
        cancel_process(message)
        return
    elif user_response == 'yes':
        if user_id in user_sheets:
            try:
                sheet_id = user_sheets[user_id]
                try:
                    sheet = gc.open_by_key(sheet_id).worksheet("Thoughts")
                except Exception:
                    spreadsheet = gc.open_by_key(sheet_id)
                    sheet = spreadsheet.add_worksheet(title="Thoughts", rows=100, cols=4)
                    sheet.update(values=[["datetime", "date", "raw_input", "thought"]], range_name="A1")
                current_datetime = datetime.datetime.now()
                date_val = current_datetime.strftime('%Y-%m-%d')
                datetime_val = current_datetime.strftime('%Y-%m-%d %H:%M:%S')
                
                raw = user_data[user_id]['thought_text']
                sheet.append_row(
                    [datetime_val, date_val, raw, raw],
                    value_input_option='USER_ENTERED'
                )
                bot.send_message(message.chat.id, "Your thoughts have been saved successfully!",
                                 reply_markup=command_markup)
                logging.info(f"Thoughts saved for user {user_id}.")
            except Exception as e:
                logging.error(f"Error saving thoughts for user {user_id}: {e}")
                bot.send_message(message.chat.id,
                                 "Failed to save your thoughts. Please check if your Google Sheet is properly linked.",
                                 reply_markup=command_markup)
        else:
            bot.send_message(message.chat.id,
                             "You need to link a Google Sheet first. Use /set_sheet command.",
                             reply_markup=command_markup)
        user_states[user_id] = None
        user_data[user_id] = {}
    elif user_response == 'no':
        bot.reply_to(message, "Please provide the corrected thoughts, either by text or voice message.")
        user_states[user_id] = THOUGHTS_EDITING
    else:
        bot.reply_to(message, "Please reply with 'Yes' or 'No'.")
        logging.info(f"User {user_id} provided invalid response: {message.text}")


# Handler for thoughts editing
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == THOUGHTS_EDITING,
                     content_types=['text', 'voice'])
def edit_thoughts(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is editing thoughts input.")
    if message.text and message.text.lower() == 'cancel':
        cancel_process(message)
        return
    if message.voice:
        corrected_thought = transcribe_voice_message(message)
        if corrected_thought is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        corrected_thought = message.text
    user_data[user_id]['thought_text'] = corrected_thought
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
    markup.add('Cancel')
    markup.add('/habits', '/thoughts', '/help')
    bot.send_message(
        message.chat.id,
        f"Updated thoughts:\n\n\"{corrected_thought}\"\n\nIs this correct now?",
        reply_markup=markup
    )
    user_states[user_id] = THOUGHTS_CONFIRMING


# @app.route(f"/{TELEGRAM_TOKEN}", methods=['POST'])
# def webhook():
#     update_json = request.get_data().decode('utf-8')
#     # Try to parse to get update_id early, or log the raw JSON if parsing fails
#     try:
#         temp_update_for_id = telebot.types.Update.de_json(update_json)
#         logging.info(f"Webhook received. Update ID: {temp_update_for_id.update_id}, Raw data: {update_json}")
#     except Exception as e:
#         logging.error(f"Webhook received. Could not parse update_id early. Raw data: {update_json}, Error: {e}")
#
#     update = telebot.types.Update.de_json(update_json) # Your original parsing
#     bot.process_new_updates([update])
#     return '', 200


@app.route(f"/{TELEGRAM_TOKEN}", methods=['POST'])
def webhook():
    # 1) grab the JSON
    update_json = request.get_json(force=True)
    update = telebot.types.Update.de_json(update_json)

    # 2) return 200 right away
    #    so Telegram knows we got it and won't retry
    resp = jsonify({"status": "received"})
    resp.status_code = 200

    # 3) process in a daemon thread
    threading.Thread(
        target=lambda: bot.process_new_updates([update]),
        daemon=True
    ).start()

    return resp


if __name__ == '__main__':
    # Load previously saved user settings from GCS
    load_all_user_settings()

    # 1. set webhook
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")

    # # 2. start scheduler thread
    # threading.Thread(target=schedule_checker, daemon=True).start()

    # 3. run Flask
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

