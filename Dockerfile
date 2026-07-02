FROM python:3.11-slim

# Install ffmpeg, libsodium and libopus (required for discord.py native voice)
RUN apt-get update \
	&& apt-get install -y --no-install-recommends ffmpeg libsodium-dev libopus-dev \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first so this layer is cached until requirements.txt changes
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt

# Copy project files
COPY . /app

# Run the bot
CMD ["python","main.py"]
