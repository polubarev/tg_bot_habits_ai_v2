# Habit Tracker Bot

The Habit Tracker Bot is a Telegram bot that helps you track your daily habits by conversing with you via text or voice messages. It leverages OpenAI's GPT models to extract habit data from your descriptions and saves them for later analysis.

## Features

- **Customizable Habits**: Define your own habits in the `config.yaml` file without changing the code.
- **Text and Voice Input**: Provide your daily descriptions via text or voice messages.
- **Grammar Correction**: Ensures that your diary entries are grammatically correct.
- **Interactive Interface**: Uses buttons and keyboards for easy interaction.
- **Daily Reminders**: Sends you reminders to track your habits.

## Installation

### Prerequisites

- Python 3.9 or higher
- Telegram account
- OpenAI API key

### Steps

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/habit-tracker-bot.git
   cd habit-tracker-bot
   ```

2. **Create a Virtual Environment (Optional but Recommended)**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set Up Configuration**

   - Rename `config_example.yaml` to `config.yaml`.
   - Fill out the `config.yaml` file with your Telegram bot token, OpenAI API key, and habits definitions.
   - Instructions for filling out `config.yaml` are provided below.

5. **Validate Configuration**

   Before running the bot, validate your `config.yaml` file:

   ```bash
   python validate_config.py
   ```

   If there are any issues, the script will inform you. Fix them before proceeding.

6. **Run the Bot**

   ```bash
   python bot.py
   ```

## Configuration Guide (`config.yaml`)

The `config.yaml` file contains all the necessary configurations for the bot. Below is an example and explanation of each field.

```yaml
telegram_token: 'YOUR_TELEGRAM_BOT_TOKEN'
openai_api_key: 'YOUR_OPENAI_API_KEY'
habits:
  sleep:
    type: integer
    description: "Number of hours you slept."
  exercise:
    type: boolean
    description: "Whether you exercised today."
  mood:
    type:
      - integer
      - "null"
    minimum: 1
    maximum: 5
    description: "Your mood level from 1 (bad) to 5 (good). Use 'null' if not specified."
  diary:
    type: string
    description: "A brief summary of your day with correct grammar."
reminder_time: '20:00'  # 24-hour format
data_directory: 'data/habits/'
```

### Fields Explanation

- **telegram_token**: Your Telegram bot token provided by [BotFather](https://core.telegram.org/bots#6-botfather).
- **openai_api_key**: Your OpenAI API key for accessing GPT models.
- **habits**: A dictionary of habits you want to track.
  - Each habit has:
    - **type**: The data type of the habit. Can be `string`, `integer`, `boolean`, or a list of types.
    - **description**: A description of the habit.
    - **minimum** (optional): Minimum value for numerical types.
    - **maximum** (optional): Maximum value for numerical types.
- **reminder_time**: The time when the bot sends you a reminder to track your habits (in `HH:MM` 24-hour format).
- **data_directory**: The directory where your habit data will be saved.

### Filling Out `config.yaml`

1. **Telegram Token**

   - Replace `'YOUR_TELEGRAM_BOT_TOKEN'` with your actual token:

     ```yaml
     telegram_token: '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ'
     ```

2. **OpenAI API Key**

   - Replace `'YOUR_OPENAI_API_KEY'` with your actual API key:

     ```yaml
     openai_api_key: 'sk-...'
     ```

3. **Defining Habits**

   - **Adding a Habit**

     ```yaml
     habits:
       your_habit_name:
         type: your_type
         description: "Your habit description."
     ```

     - **your_habit_name**: A unique identifier for your habit (e.g., `reading`).
     - **your_type**: The data type (`string`, `integer`, `boolean`, or list of types).
     - **description**: Explain what the habit is about.

   - **Example with Constraints**

     ```yaml
     habits:
       water_intake:
         type: integer
         minimum: 0
         maximum: 10
         description: "Number of glasses of water you drank today."
     ```

   - **Handling Nullable Types**

     If a habit can be `null`, include `"null"` in the type list and enclose it in quotes:

     ```yaml
     mood:
       type:
         - integer
         - "null"
       minimum: 1
       maximum: 5
       description: "Your mood level from 1 to 5. Use 'null' if not specified."
     ```

4. **Reminder Time**

   - Set the time when you want to receive daily reminders:

     ```yaml
     reminder_time: '21:00'  # 9 PM
     ```

5. **Data Directory**

   - Specify where you want your habit data to be saved:

     ```yaml
     data_directory: 'data/habits/'
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
- [ ] option to cancel habits entering process
- [ ] option to chose date of diary (to have an option write diary for previous dates)
- [ ] option to add and remove habits from the UI of bot and not config file

## Contributing

If you'd like to contribute to this project, please fork the repository and submit a pull request.

## License

This project is licensed under the MIT License.

---

Let me know if you need further assistance or additional modifications!