FROM python:3.11-slim

# Install ffmpeg and libsodium (required by PyNaCl for discord.py voice)
RUN apt-get update && apt-get install -y ffmpeg libsodium-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the bot
CMD ["python","main.py"]
