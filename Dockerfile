FROM python:3.11-slim

# Install ffmpeg, libsodium and libopus (required for discord.py native voice)
RUN apt-get update && apt-get install -y ffmpeg libsodium-dev libopus-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the bot
CMD ["python","main.py"]
