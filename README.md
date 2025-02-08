# Habit Tracker Bot v2

## Overview
A Telegram bot for tracking habits with Google Sheets integration and OpenAI-assisted habit extraction.

## Setup Instructions
- Create a Google Service Account and set the `SERVICE_ACCOUNT_FILE` variable in your `.env`.
- Define the following environment variables in your `.env`:
  - `TELEGRAM_TOKEN_TEST`
  - `OPENAI_API_KEY`
  - `SERVICE_ACCOUNT_FILE` (if different from the default)
- Link your Google Sheet using the `/set_sheet <your_sheet_id>` command.
- Update your bot’s configuration by sending the updated JSON with `/update_config`.

## Running Locally
1. Install dependencies:  
   `pip install -r requirements.txt`
2. Start the bot:  
   `python bot.py`

## Running with Docker
1. Build the Docker image:  
   `docker build -t habits_bot .`
2. Run the Docker container:  
   `docker run -d --env-file .env habits_bot`

## Commands
- `/start`: Begin interaction with the bot.
- `/habits`: Record your day's habits.
- `/manual`: Manually input habit data in JSON format.
- `/help`: Display help information.
- `/set_sheet`: Link your Google Sheet.
- `/update_config`: Update your bot configuration.

# Habit Tracker Bot

The Habit Tracker Bot is a Telegram bot that helps you track your daily habits by conversing with you via text or voice messages. It leverages OpenAI's GPT models to extract habit data from your descriptions and saves them for later analysis.

## Features

- **Customizable Habits**: Define your own habits in the `config.json` file without changing the code.
- **Text and Voice Input**: Provide your daily descriptions via text or voice messages.
- **Grammar Correction**: Ensures that your diary entries are grammatically correct.
- **Interactive Interface**: Uses buttons and keyboards for easy interaction.
- **Daily Reminders**: Sends you reminders to track your habits.

## Installation

### Prerequisites

- Python 3.9 or higher
- Telegram account
- OpenAI API key
- Environment variables:
  - `TELEGRAM_TOKEN`: Your Telegram bot token.
  - `OPENAI_API_KEY`: Your OpenAI API key.

### Steps

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/habit-tracker-bot.git
   cd habit-tracker-bot
   ```

2. **Create a Virtual Environment (Optional but Recommended)**

   ```bash
   python -m venv venv
   ```

3. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set Up Configuration**

   - Rename `config_example.json` to `config.json`.
   - Fill out the `config.json` file with your Telegram bot token, OpenAI API key, and habits definitions.
   - Instructions for filling out `config.json` are provided below.

5. **Validate Configuration**

   Before running the bot, validate your `config.json` file:

   ```bash
   python validate_config.py
   ```

   If there are any issues, the script will inform you. Fix them before proceeding.

6. **Run the Bot**

   ```bash
   python bot.py
   ```

## Configuration Guide (`config.json`)

The `config.json` file contains all the necessary configurations for the bot. Below is an example and explanation of each field.

```json
{
  "habits": {
    "sleep": {
      "type": "integer",
      "description": "Number of hours you slept."
    },
    "exercise": {
      "type": "boolean",
      "description": "Whether you exercised today."
    },
    "mood": {
      "type": [
        "integer",
        "null"
      ],
      "minimum": 1,
      "maximum": 5,
      "description": "Your mood level from 1 (bad) to 5 (good). Use 'null' if not specified."
    },
    "diary": {
      "type": "string",
      "description": "A brief summary of your day with correct grammar."
    }
  },
  "reminder_time": "17:15",
  "data_directory": "data/habits/"
}
```

### Fields Explanation

- **telegram\_token**: Your Telegram bot token provided by [BotFather](https://core.telegram.org/bots#6-botfather).
- **openai\_api\_key**: Your OpenAI API key for accessing GPT models.
- **habits**: A dictionary of habits you want to track.
  - Each habit has:
    - **type**: The data type of the habit. Can be `string`, `integer`, `boolean`, or a list of types.
    - **description**: A description of the habit.
    - **minimum** (optional): Minimum value for numerical types.
    - **maximum** (optional): Maximum value for numerical types.
- **reminder\_time**: The time when the bot sends you a reminder to track your habits (in `HH:MM` 24-hour format).
- **data\_directory**: The directory where your habit data will be saved.

### Filling Out `config.json`

1. **Defining Habits**

   - **Adding a Habit**

     ```json
     "habits": {
       "your_habit_name": {
         "type": "your_type",
         "description": "Your habit description."
       }
     }
     ```

     - **your\_habit\_name**: A unique identifier for your habit (e.g., `reading`).
     - **your\_type**: The data type (`string`, `integer`, `boolean`, or list of types).
     - **description**: Explain what the habit is about.

   - **Example with Constraints**

     ```json
     "habits": {
       "water_intake": {
         "type": "integer",
         "minimum": 0,
         "maximum": 10,
         "description": "Number of glasses of water you drank today."
       }
     }
     ```

   - **Handling Nullable Types**

     If a habit can be `null`, include `null` in the type list:

     ```json
     "mood": {
       "type": ["integer", "null"],
       "minimum": 1,
       "maximum": 5,
       "description": "Your mood level from 1 to 5. Use 'null' if not specified."
     }
     ```

2. **Reminder Time**

   - Set the time when you want to receive daily reminders:
     ```json
     "reminder_time": "21:00"
     ```

3. **Data Directory**

   - Specify where you want your habit data to be saved:
     ```json
     "data_directory": "data/habits/"
     ```

## Validation Script (`validate_config.py`)

Before running the bot, it's crucial to ensure that your `config.yaml` file is correctly set up. The `validate_config.py` script checks the configuration file for:

- Presence of required fields.
- Correct data types.
- Habits descriptions matching OpenAI function calling format.

### Usage

```bash
python validate_config.py
```

If the script finds any issues, it will display error messages indicating what needs to be fixed.

## Running the Bot

After validating your configuration:

```bash
python bot.py
```

---

## Additional Notes

- **Data Privacy**: Ensure that your API keys are kept secure and not shared publicly.
- **Error Handling**: The bot includes logging for debugging purposes. Logs are printed to the console.
- **Extensibility**: You can add new habits or modify existing ones by updating the `config.yaml` file.

## TODO

- [ ] move to container
- [ ] create more user-friendly instructions 
- [ ] create user friendly config file
 
## Contributing

If you'd like to contribute to this project, please fork the repository and submit a pull request.

## License

This project is licensed under the MIT License.

