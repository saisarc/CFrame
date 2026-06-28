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
3. Railway will install dependencies from `requirements.txt` (if not using Docker).
4. You can deploy via Docker (recommended for ffmpeg support) or using Railway's default Python builder.

Docker deployment (recommended):

```bash
# Railway will build the provided Dockerfile which includes ffmpeg
# No Procfile is required when deploying from Docker
```

Deploying without Docker:

```bash
# Railway will run the Procfile command
python main.py
```

## Required environment variables

Set these in Railway:

- DISCORD_TOKEN
- GROQ_API_KEY
- DEV_ID
- LOG_CHANNEL_ID
 - DEVLOG_CHANNEL_ID
 - MISE_PYTHON_GITHUB_ATTESTATIONS (optional: set to `false` if you hit mise attestation issues)

## Notes

- Keep your token private and never commit it to GitHub.
- Railway will redeploy automatically when you push new commits.

Checklist before redeploying on Railway:

- Ensure `.env` is NOT committed to the repo (this project includes `.gitignore`).
- Ensure `Dockerfile` and `requirements.txt` are present in the repo (they are included).
- Add the environment variables listed above in the Railway project settings.
