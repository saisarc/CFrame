# CFrame Discord Bot

A feature-rich Discord bot for moderation, logging, welcome messages, giveaways, leveling, anti-spam, and reaction roles.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Railway deployment

1. Push this project to GitHub.
2. Create a new Railway project and connect the GitHub repo.
3. Railway will install dependencies from requirements.txt.
4. Use the start command from Procfile:

```bash
python main.py
```

## Required environment variables

Set these in Railway:

- DISCORD_TOKEN
- GROQ_API_KEY
- DEV_ID
- LOG_CHANNEL_ID

## Notes

- Keep your token private and never commit it to GitHub.
- Railway will redeploy automatically when you push new commits.
