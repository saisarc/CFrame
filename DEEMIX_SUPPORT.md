# DeeMix Support Notes

This repo currently plays audio via **Lavalink** using **Wavelink** (`wavelink.Pool.fetch_tracks`).

Because your Lavalink does **not** appear to be configured for DeeMix/Deezer (response: **3 — No / not configured**), the bot cannot reliably resolve DeeMix sources yet.

## What’s needed on the Lavalink side
Enable a Lavalink plugin / source that can resolve Deezer/DeeMix tracks, depending on your Lavalink setup:

- Deezer URL support (e.g. `https://www.deezer.com/track/...`)
- or a dedicated resolver/scheme (e.g. `deezer:` or `deemix:`)

After enabling it, the bot-side change will be straightforward:
- Add a `/deemix <link|query>` command that passes the input to `wavelink.Pool.fetch_tracks()`
- Queue-compatible + updates the voice channel name the same way as `/play`

## Next step
Provide which Lavalink plugin/source you enable (or share your Lavalink `application.yml` snippet for sources). Then I will implement `/deemix` in `music.py` to match your Lavalink capabilities.
