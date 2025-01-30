import json
import sys
import jsonschema
from jsonschema import validate
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load configuration
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as file:
            config = json.load(file)
        return config
    except FileNotFoundError:
        logging.error("Error: config.json file not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing config.json: {e}")
        sys.exit(1)


# Define the expected schema for the config file
config_schema = {
    "type": "object",
    "properties": {
        "habits": {"type": "object"},
        "reminder_time": {"type": "string", "pattern": "^(?:[01]\d|2[0-3]):[0-5]\d$"},
        "data_directory": {"type": "string"}
    },
    "required": ["habits", "reminder_time", "data_directory"]
}

# Valid types according to OpenAI function calling
VALID_TYPES = ["object", "array", "string", "number", "integer", "boolean", "null"]

# Allowed fields for each type
ALLOWED_FIELDS = {
    "object": ["properties", "required", "description"],
    "array": ["items", "minItems", "maxItems", "description"],
    "string": ["minLength", "maxLength", "pattern", "enum", "description"],
    "number": ["minimum", "maximum", "enum", "description"],
    "integer": ["minimum", "maximum", "enum", "description"],
    "boolean": ["enum", "description"],
    "null": ["description"]
}


def validate_habits(habits):
    errors = []
    for habit_name, habit_info in habits.items():
        # Validate 'type'
        habit_type = habit_info.get('type')
        if not habit_type:
            errors.append(f"Error in habit '{habit_name}': 'type' is required.")
            continue
        if isinstance(habit_type, list):
            for t in habit_type:
                if t not in VALID_TYPES:
                    errors.append(f"Error in habit '{habit_name}': Invalid type '{t}'. Allowed types are {VALID_TYPES}.")
        elif habit_type not in VALID_TYPES:
            errors.append(f"Error in habit '{habit_name}': Invalid type '{habit_type}'. Allowed types are {VALID_TYPES}.")

        # Validate 'description'
        if 'description' not in habit_info or not isinstance(habit_info['description'], str):
            errors.append(f"Error in habit '{habit_name}': 'description' is required and must be a string.")

        # Get the allowed fields based on type
        if isinstance(habit_type, list):
            types_list = habit_type
        else:
            types_list = [habit_type]

        allowed_fields = set()
        for t in types_list:
            allowed_fields.update(ALLOWED_FIELDS.get(t, []))

        # Validate fields
        for key in habit_info:
            if key not in ['type', 'description']:
                if key not in allowed_fields:
                    errors.append(f"Error in habit '{habit_name}': Field '{key}' is not allowed for type(s) {types_list}.")
                    continue

                # Validate field types
                if key == 'minimum' or key == 'maximum':
                    if not isinstance(habit_info[key], (int, float)):
                        errors.append(f"Error in habit '{habit_name}': '{key}' must be a number.")
                elif key == 'enum':
                    if not isinstance(habit_info[key], list):
                        errors.append(f"Error in habit '{habit_name}': 'enum' must be a list.")
                elif key == 'pattern':
                    if not isinstance(habit_info[key], str):
                        errors.append(f"Error in habit '{habit_name}': 'pattern' must be a string.")
                elif key == 'properties':
                    if not isinstance(habit_info[key], dict):
                        errors.append(f"Error in habit '{habit_name}': 'properties' must be an object.")
                elif key == 'required':
                    if not isinstance(habit_info[key], list) or not all(isinstance(s, str) for s in habit_info[key]):
                        errors.append(f"Error in habit '{habit_name}': 'required' must be a list of strings.")
                elif key == 'items':
                    if not isinstance(habit_info[key], dict):
                        errors.append(f"Error in habit '{habit_name}': 'items' must be an object.")
                elif key in ['minItems', 'maxItems', 'minLength', 'maxLength']:
                    if not isinstance(habit_info[key], int):
                        errors.append(f"Error in habit '{habit_name}': '{key}' must be an integer.")
                # Add other field validations as necessary

    if errors:
        for error in errors:
            logging.error(error)
        return False, errors
    return True, []


def main():
    config = load_config()

    # Validate the overall config structure
    try:
        validate(instance=config, schema=config_schema)
    except jsonschema.exceptions.ValidationError as err:
        logging.error(f"Configuration Error: {err.message}")
        sys.exit(1)

    # Validate habits separately
    is_valid, errors = validate_habits(config['habits'])
    if not is_valid:
        sys.exit(1)

    logging.info("Configuration is valid.")


if __name__ == "__main__":
    main()
