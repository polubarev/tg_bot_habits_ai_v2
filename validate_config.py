import yaml
import sys
import jsonschema
from jsonschema import validate


# Load configuration
def load_config():
    try:
        with open('config.yaml', 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
        return config
    except FileNotFoundError:
        print("Error: config.yaml file not found.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing config.yaml: {e}")
        sys.exit(1)


# Define the expected schema for the config file
config_schema = {
    "type": "object",
    "properties": {
        "telegram_token": {"type": "string"},
        "openai_api_key": {"type": "string"},
        "habits": {"type": "object"},
        "reminder_time": {"type": "string", "pattern": "^(?:[01]\d|2[0-3]):[0-5]\d$"},
        "data_directory": {"type": "string"}
    },
    "required": ["telegram_token", "openai_api_key", "habits", "reminder_time", "data_directory"]
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
    for habit_name, habit_info in habits.items():
        # Validate 'type'
        habit_type = habit_info.get('type')
        if not habit_type:
            print(f"Error in habit '{habit_name}': 'type' is required.")
            return False
        if isinstance(habit_type, list):
            for t in habit_type:
                if t not in VALID_TYPES:
                    print(f"Error in habit '{habit_name}': Invalid type '{t}'. Allowed types are {VALID_TYPES}.")
                    return False
        elif habit_type not in VALID_TYPES:
            print(f"Error in habit '{habit_name}': Invalid type '{habit_type}'. Allowed types are {VALID_TYPES}.")
            return False

        # Validate 'description'
        if 'description' not in habit_info or not isinstance(habit_info['description'], str):
            print(f"Error in habit '{habit_name}': 'description' is required and must be a string.")
            return False

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
                    print(f"Error in habit '{habit_name}': Field '{key}' is not allowed for type(s) {types_list}.")
                    return False

                # Validate field types
                if key == 'minimum' or key == 'maximum':
                    if not isinstance(habit_info[key], (int, float)):
                        print(f"Error in habit '{habit_name}': '{key}' must be a number.")
                        return False
                elif key == 'enum':
                    if not isinstance(habit_info[key], list):
                        print(f"Error in habit '{habit_name}': 'enum' must be a list.")
                        return False
                elif key == 'pattern':
                    if not isinstance(habit_info[key], str):
                        print(f"Error in habit '{habit_name}': 'pattern' must be a string.")
                        return False
                elif key == 'properties':
                    if not isinstance(habit_info[key], dict):
                        print(f"Error in habit '{habit_name}': 'properties' must be an object.")
                        return False
                elif key == 'required':
                    if not isinstance(habit_info[key], list) or not all(isinstance(s, str) for s in habit_info[key]):
                        print(f"Error in habit '{habit_name}': 'required' must be a list of strings.")
                        return False
                elif key == 'items':
                    if not isinstance(habit_info[key], dict):
                        print(f"Error in habit '{habit_name}': 'items' must be an object.")
                        return False
                elif key in ['minItems', 'maxItems', 'minLength', 'maxLength']:
                    if not isinstance(habit_info[key], int):
                        print(f"Error in habit '{habit_name}': '{key}' must be an integer.")
                        return False
                # Add other field validations as necessary

    return True


def main():
    config = load_config()

    # Validate the overall config structure
    try:
        validate(instance=config, schema=config_schema)
    except jsonschema.exceptions.ValidationError as err:
        print(f"Configuration Error: {err.message}")
        sys.exit(1)

    # Validate habits separately
    if not validate_habits(config['habits']):
        sys.exit(1)

    print("Configuration is valid.")


if __name__ == "__main__":
    main()
