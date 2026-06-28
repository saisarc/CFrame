# Railway Deployment Guide

This repo uses two services on Railway:

1. **Bot service** — Python bot using `main.py`
2. **Lavalink service** — Java Lavalink server with `application.yml`

---

## 1) Lavalink service

Use `Dockerfile.lavalink` in a separate Railway service.

Files needed:
- `Lavalink.jar`
- `application.yml`
- `Dockerfile.lavalink`

Railway service settings:
- Service path: `/` (repo root)
- Dockerfile path: `Dockerfile.lavalink`
- Exposed port: `2333`
- No command override needed; Dockerfile runs Lavalink automatically.

If Railway asks for the service type, choose a container/service type that supports TCP ports rather than a web-only HTTP service.

`application.yml` should include the YouTube plugin config:

```yaml
server:
  port: 2333

lavalink:
  server:
    password: your_lavalink_password
    sources:
      youtube: false
    bufferDurationMs: 400
  plugins:
    - dependency: "dev.lavalink.youtube:youtube-plugin:1.18.1"
      snapshot: false

plugins:
  youtube:
    enabled: true
    allowSearch: true
    allowDirectVideoIds: true
    allowDirectPlaylistIds: true
    clients:
      - MUSIC
      - ANDROID_VR
      - WEB
      - WEBEMBEDDED
```

If Railway gives you a service hostname like `abc123.up.railway.app`, that becomes the host for your bot.

---

## 2) Bot service

Use the existing `Dockerfile` in the repo.

Railway env vars for the bot service:
- `DISCORD_TOKEN`
- `LAVALINK_HOST` — the Lavalink service hostname or public URL
- `LAVALINK_PORT=2333`
- `LAVALINK_PASSWORD=your_lavalink_password`

Your bot code already builds Lavalink URI with:

```python
uri = f"http://{host}:{port}"
```

So `LAVALINK_HOST` should not include `http://`.

---

## 3) Deploy order

1. Deploy the Lavalink service first.
2. Confirm the service is reachable on port `2333`.
3. Deploy the bot service.
4. Set the bot service env vars to match your Lavalink service.

---

## 4) Common issues

- If the bot cannot connect, check that the Lavalink host is reachable from Railway.
- If playback fails, verify the Lavalink password matches exactly.
- If commands remain stuck pending, confirm the bot successfully connected to the node in logs.

---

## 5) Local development

Keep using `.env` locally with:

```env
LAVALINK_HOST=127.0.0.1
LAVALINK_PORT=2333
LAVALINK_PASSWORD=your_lavalink_password
```

Then run Lavalink locally with `java -jar Lavalink.jar` and start the bot with `python main.py`.
