import os
import yaml
import json
import datetime
from openai import OpenAI
import telebot
import schedule
import threading
import time
import logging
from telebot import types
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dateutil.parser import parse


# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load configuration
with open('config.yaml', 'r', encoding='utf-8') as file:
    config = yaml.safe_load(file)

TELEGRAM_TOKEN = config['telegram_token']
OPENAI_API_KEY = config['openai_api_key']
HABITS_CONFIG = config['habits']
REMINDER_TIME = config['reminder_time']
DATA_DIR = config['data_directory']

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Conversation states
AWAITING_INPUT = 'AWAITING_INPUT'
CONFIRMING = 'CONFIRMING'
EDITING = 'EDITING'
MANUAL_INPUT = 'MANUAL_INPUT'

user_states = {}
user_data = {}
active_users = set()

# Create a global keyboard with command buttons
command_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
command_markup.add('/habits', '/manual', '/help')


# Function to upload the DataFrame to Google Sheets
def upload_to_google_sheets(df):
    logging.info("Uploading report to Google Sheets.")
    # Define the scope
    scope = [
        'https://www.googleapis.com/auth/spreadsheets',
        # 'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]

    # Provide the path to your service account key file
    creds = ServiceAccountCredentials.from_json_keyfile_name('striking-domain-430417-u3-bcf2406d6bcb.json', scope)

    # Authorize the client
    client_google = gspread.authorize(creds)

    # Open the Google Sheet by name or URL
    try:
        sheet = client_google.open("Diary").sheet1  # Assuming you want to update the first sheet
    except Exception as e:
        logging.error(f"Error opening Google Sheet: {e}")
        return

    # Clear the existing content in the sheet
    try:
        sheet.clear()
    except Exception as e:
        logging.error(f"Error clearing Google Sheet: {e}")
        return

    # Convert DataFrame to a list of lists
    data = [df.columns.values.tolist()] + df.values.tolist()

    # Update the sheet with the data
    try:
        sheet.update(range_name='A1', values=data)
        logging.info("Google Sheet has been updated successfully.")
    except Exception as e:
        logging.error(f"Error updating Google Sheet: {e}")


def parse_habit_properties(habits_config):
    habit_properties = {}
    required_habits = []
    for habit_name, habit_info in habits_config.items():
        # Ensure 'type' is correctly formatted
        habit_type = habit_info['type']
        if isinstance(habit_type, list):
            # Convert all types to strings (e.g., "null" stays as a string)
            habit_type = [str(t) for t in habit_type]
        else:
            habit_type = str(habit_type)
        habit_property = {
            "type": habit_type,
            "description": habit_info['description']
        }
        # Add optional fields if they exist
        if 'minimum' in habit_info:
            habit_property['minimum'] = habit_info['minimum']
        if 'maximum' in habit_info:
            habit_property['maximum'] = habit_info['maximum']
        habit_properties[habit_name] = habit_property
        required_habits.append(habit_name)
    return habit_properties, required_habits


# Parse habit properties and required habits
habit_properties, required_habits = parse_habit_properties(HABITS_CONFIG)


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} started the bot.")
    active_users.add(user_id)

    # Send welcome message with command buttons
    bot.send_message(
        message.chat.id,
        "Welcome to the Habit Tracker Bot! Choose a command:",
        reply_markup=command_markup
    )


@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = (
        "Habit Tracker Bot Help:\n"
        "- /start: Start the bot and get a welcome message.\n"
        "- /habits: Begin tracking your habits by describing your day in text or voice.\n"
        "- /manual: Manually input your habits in JSON format.\n"
        "- /help: Show this help message.\n\n"
        "You can select commands using the buttons provided.\n\n"
        "After initiating habit tracking with /habits:\n"
        "1. Provide a description of your day, including the habits listed.\n"
        "2. The bot will extract your habits and present them for confirmation.\n"
        "3. If the data is correct, reply with 'Yes' to save it.\n"
        "4. If corrections are needed, reply with 'No' and provide corrections in text or voice."
    )
    bot.send_message(message.chat.id, help_text, reply_markup=command_markup)


@bot.message_handler(commands=['habits'])
def habits_command(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated /habits command.")
    user_states[user_id] = AWAITING_INPUT
    user_data[user_id] = {}
    active_users.add(user_id)

    # Build the habits reminder message with bold habit names
    habits_list = ""
    for habit_name, habit_info in HABITS_CONFIG.items():
        habits_list += f"- *{habit_name}*: {habit_info['description']}\n"
    reminder_message = (
        "Please describe your day, either by text or voice message.\n\n"
        "Please include the following habits:\n" + habits_list
    )
    bot.send_message(message.chat.id, reminder_message, parse_mode='Markdown', reply_markup=command_markup)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == AWAITING_INPUT,
                     content_types=['text', 'voice'])
def handle_input(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing input in state AWAITING_INPUT.")
    if message.voice:
        user_input = transcribe_voice_message(message)
        if user_input is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        user_input = message.text

    user_data[user_id]['user_input'] = user_input

    # Prepare the function parameters for GPT-4o-mini function calling
    function_parameters = {
        "type": "object",
        "properties": habit_properties,
        "required": required_habits
    }

    # Process input with GPT-4o-mini
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a bot that extracts user habits from their daily description. "
                        "Extract the following habits and provide the output in JSON format. "
                        "Ensure that the 'diary' field is grammatically correct."
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

    # Prepare the 'Yes' and 'No' buttons along with command buttons
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
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
    if user_response == 'yes':
        # Save JSON
        os.makedirs(DATA_DIR, exist_ok=True)
        date_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        file_path = os.path.join(DATA_DIR, f"{date_str}_{user_id}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(user_data[user_id]['json_output'])
        bot.send_message(message.chat.id, "Your habits have been saved. Thank you!", reply_markup=command_markup)
        logging.info(f"Data saved for user {user_id} at {file_path}")
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
    if message.voice:
        correction = transcribe_voice_message(message)
        if correction is None:
            bot.reply_to(message, "Sorry, I couldn't process your voice message. Please try again.")
            return
    else:
        correction = message.text

    # Prepare the function parameters for GPT-4o-mini function calling
    function_parameters = {
        "type": "object",
        "properties": habit_properties,
        "required": required_habits
    }

    # Send correction back to GPT-4o-mini along with previous input and output
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a bot that extracts user habits from their daily description. "
                        "Extract the following habits and provide the output in JSON format. "
                        "Ensure that the 'diary' field is grammatically correct."
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

    # Prepare the 'Yes' and 'No' buttons along with command buttons
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Yes', 'No')
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
    user_id = message.from_user.id
    logging.info(f"User {user_id} initiated manual input.")
    user_states[user_id] = MANUAL_INPUT
    active_users.add(user_id)
    bot.send_message(message.chat.id, "Please input your habits in JSON format.", reply_markup=command_markup)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == MANUAL_INPUT)
def manual_input(message):
    user_id = message.from_user.id
    logging.info(f"User {user_id} is providing manual input.")
    json_text = message.text
    try:
        json_data = json.loads(json_text)
        user_data[user_id]['json_output'] = json.dumps(json_data, ensure_ascii=False, indent=4)
        os.makedirs(DATA_DIR, exist_ok=True)
        date_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        file_path = os.path.join(DATA_DIR, f"{date_str}_{user_id}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(user_data[user_id]['json_output'])
        bot.send_message(message.chat.id, "Your manual input has been saved. Thank you!", reply_markup=command_markup)
        logging.info(f"Manual data saved for user {user_id} at {file_path}")
        user_states[user_id] = None
        user_data[user_id] = {}
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error for user {user_id}: {e}")
        bot.reply_to(message, "Invalid JSON format. Please try again.")


def transcribe_voice_message(message):
    user_id = message.from_user.id
    logging.info(f"Transcribing voice message for user {user_id}.")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        # Save the file to disk
        voice_file_path = f'voice_note_{user_id}.ogg'
        with open(voice_file_path, 'wb') as f:
            f.write(downloaded_file)
        # Now send the file to OpenAI's API
        with open(voice_file_path, 'rb') as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        # Delete the voice file after processing
        os.remove(voice_file_path)
        logging.info(f"Voice transcription for user {user_id}: {transcript.text}")
        return transcript.text
    except Exception as e:
        logging.error(f"Error transcribing voice message for user {user_id}: {e}")
        return None


def send_reminders():
    logging.info("Sending reminders to active users.")
    for user_id in active_users:
        try:
            bot.send_message(user_id, "Don't forget to track your habits today! Type /habits to begin.", reply_markup=command_markup)
            logging.info(f"Reminder sent to user {user_id}.")
        except Exception as e:
            logging.error(f"Failed to send reminder to {user_id}: {e}")


def generate_excel_report():
    logging.info("Generating Excel report from habit data.")
    data = []

    # Ensure DATA_DIR exists
    if not os.path.exists(DATA_DIR):
        logging.warning(f"Data directory '{DATA_DIR}' does not exist.")
        return

    # Get list of JSON files in DATA_DIR
    json_files = [f for f in os.listdir(DATA_DIR) if f.endswith('.json')]
    if not json_files:
        logging.warning("No JSON files found in data directory.")
        return

    # Process each JSON file
    for filename in json_files:
        file_path = os.path.join(DATA_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            # Extract date and user ID from filename
            basename = os.path.basename(filename)
            date_str, time_str, user_id_with_ext = basename.split('_')
            date_time_str = date_str + '_' + time_str
            user_id_str = user_id_with_ext.split('.')[0]

            # Attempt to parse the date with time
            try:
                date_time = datetime.datetime.strptime(date_time_str, '%Y-%m-%d_%H-%M-%S')
            except ValueError:
                # If no time is provided, fall back to date-only format
                date_time = datetime.datetime.strptime(date_time_str, '%Y-%m-%d')

            # Convert the `datetime` object to a string in the desired format
            date_time_str = date_time.strftime('%Y-%m-%d %H:%M:%S')
            date_str = date_time.strftime('%Y-%m-%d')

            # Flatten JSON data and add date and user_id
            flat_data = {'datetime': date_time_str, 'date': date_str, 'user_id': user_id_str}
            flat_data.update(json_data)
            data.append(flat_data)
        except Exception as e:
            logging.error(f"Error processing file {filename}: {e}")

    if not data:
        logging.warning("No data to write to Excel.")
        return

    # Create a DataFrame
    df = pd.DataFrame(data)

    # Sort data by date and user_id
    df.sort_values(by=['datetime', 'user_id'], inplace=True)

    # Remove duplicates for each user and date, keeping the latest entry
    df = df.drop_duplicates(subset=['date', 'user_id'], keep='last')

    df.fillna('', inplace=True)

    # Write DataFrame to Excel
    os.makedirs('reports', exist_ok=True)
    excel_file = os.path.join('reports', 'habit_data.xlsx')
    try:
        df.to_excel(excel_file, index=False)
        logging.info(f"Excel report generated at {excel_file}")
    except Exception as e:
        logging.error(f"Error writing to Excel file: {e}")

    # Upload to Google Sheets
    upload_to_google_sheets(df)



def schedule_checker():
    while True:
        schedule.run_pending()
        time.sleep(1)


# Schedule the daily reminders
schedule.every().day.at(REMINDER_TIME).do(send_reminders)

# Schedule the Excel report generation at a specific time (e.g., 23:59)
REPORT_GENERATION_TIME = '17:16'
schedule.every().day.at(REPORT_GENERATION_TIME).do(generate_excel_report)

generate_excel_report()
if __name__ == '__main__':
    # Start the scheduler in a separate thread
    threading.Thread(target=schedule_checker).start()
    # Start the bot
    logging.info("Starting the bot.")
    bot.polling(none_stop=True)
