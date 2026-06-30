# CFrame Discord Bot

A feature-rich Discord bot for moderation, logging, welcome messages, giveaways, leveling, anti-spam, and reaction roles.

## Railway deployment

This repo can deploy to Railway using two services:

- `bot` service: runs `main.py` using the existing `Dockerfile`
- `lavalink` service: runs Java Lavalink using `Dockerfile.lavalink`, `Lavalink.jar`, and `application.yml`

See `DEPLOY_RAILWAY.md` for detailed setup and environment variables.

---

## MongoDB (optional persistence)

If you want moderation/settings giveaways/reaction roles to persist in MongoDB, set:

- `MONGODB_URI` (**required**) – your MongoDB Atlas connection string
- `MONGODB_DB` (**optional**) – database name (default: `cframe`)

The bot code will only write to Mongo if `MONGODB_URI` is set.

---

## Local development

Create a `.env` file with:

```env
DISCORD_TOKEN=your_discord_token

LAVALINK_HOST=127.0.0.1
LAVALINK_PORT=2333
LAVALINK_PASSWORD=your_lavalink_password

# Optional MongoDB persistence
MONGODB_URI="mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/?appName=cframe"
MONGODB_DB=cframe
```

Then run:

- Lavalink locally: `java -jar Lavalink.jar`
- Bot: `python main.py`

