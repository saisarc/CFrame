# CFrame Discord Bot

A feature-rich Discord bot for moderation, logging, welcome messages, giveaways, leveling, anti-spam, and reaction roles.

## Railway deployment

This repo can deploy to Railway using two services:

- `bot` service: runs `main.py` using the existing `Dockerfile`
- `lavalink` service: runs Java Lavalink using `Dockerfile.lavalink`, `Lavalink.jar`, and `application.yml`

See `DEPLOY_RAILWAY.md` for detailed setup and environment variables.

