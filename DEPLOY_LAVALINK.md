Running Lavalink (quick start)

Option 1 — run locally with the Lavalink JAR:

1. Download the latest Lavalink jar:

```bash
wget https://github.com/freyacodes/Lavalink/releases/latest/download/Lavalink.jar -O Lavalink.jar
```

2. Create `application.yml` next to the jar with contents (example):

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
    - dependency: "com.github.topi314.lavasrc:lavasrc-plugin:4.8.3"
      repository: "https://maven.lavalink.dev/releases"
    - dependency: "com.github.topi314.lavalyrics:lavalyrics-plugin:1.1.0"
      repository: "https://maven.lavalink.dev/releases"

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
  lavasrc:
    providers:
      - "spsearch:%QUERY%"
      - "amsearch:%QUERY%"
      - "dzsearch:%QUERY%"
      - "ytsearch:%QUERY%"
    sources:
      spotify: true
      applemusic: true
      deezer: true
    spotify:
      clientId: "${SPOTIFY_CLIENT_ID:}"
      clientSecret: "${SPOTIFY_CLIENT_SECRET:}"
      countryCode: "US"
    applemusic:
      countryCode: "US"
    deezer:
      countryCode: "US"
  lavalyrics:
    sources:
      - spotify
      - applemusic
      - youtube
```

3. Run Lavalink:

```bash
java -jar Lavalink.jar
```

Option 2 — run via Docker (example):

```bash
docker run -d --name lavalink \
  -p 2333:2333 \
  -v $(pwd)/application.yml:/opt/Lavalink/application.yml \
  freyacodes/lavalink:latest
```

Configure your bot environment variables (Railway or local):

- `LAVALINK_HOST` (default: `127.0.0.1`)
- `LAVALINK_PORT` (default: `2333`)
- `LAVALINK_PASSWORD` (must match the Lavalink `application.yml` password)

Notes:
- Lavalink requires Java 17+.
- For Railway, run Lavalink as a separate service (or host externally) and set the above env vars in your bot service.
