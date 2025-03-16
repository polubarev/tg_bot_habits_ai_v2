# Use the official lightweight Python image.
FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Copy dependency file and install dependencies
# Assuming a requirements.txt exists; if not, list dependencies or create one.
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code into the container
COPY . /app/

# Command to start the bot
CMD ["python", "bot.py"]