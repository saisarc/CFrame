#!/usr/bin/env python3
"""
Auto-changelog Discord webhook hook.

Place this file at .git/hooks/post-commit (or symlink it) and make it executable:

    cp changelog_hook.py .git/hooks/post-commit
    chmod +x .git/hooks/post-commit

Then add CHANGELOG_WEBHOOK_URL to your .env file:

    CHANGELOG_WEBHOOK_URL=https://discord.com/api/webhooks/...

Every git commit will silently post an update embed to that webhook.
No code snippets, no diffs — just your commit message and which areas changed.
"""
import os
import sys
import json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env without third-party deps ────────────────────────────────────────
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, val = raw.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)

WEBHOOK_URL = os.getenv("CHANGELOG_WEBHOOK_URL", "").strip()
if not WEBHOOK_URL:
    sys.exit(0)  # not configured — silent skip

# ── File → user-friendly area map ─────────────────────────────────────────────
# Files are grouped into broad areas so users see what changed without
# seeing filenames, code, or technical details.
AREA_MAP: dict[str, str] = {
    "music.py":             "🎵 Music",
    "commands.py":          "⚙️ Commands",
    "extra_commands.py":    "⚙️ Commands",
    "dev_commands.py":      "🛠️ Dev Tools",
    "server_tools.py":      "🛠️ Server Tools",
    "features.py":          "✨ Features",
    "main.py":              "🔧 Core",
    "help_ui.py":           "📚 Help",
    "persistence_mongo.py": "🗄️ Database",
    "mongo_store.py":       "🗄️ Database",
    "mongo_cache.py":       "🗄️ Database",
    "application.yml":      "⚙️ Config",
    "requirements.txt":     "📦 Dependencies",
    "Dockerfile":           "🐳 Deployment",
    "Dockerfile.lavalink":  "🐳 Deployment",
    "Procfile":             "🐳 Deployment",
    "runtime.txt":          "🐳 Deployment",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


# ── Collect commit info ────────────────────────────────────────────────────────
try:
    subject      = git("log", "-1", "--pretty=format:%s")
    body         = git("log", "-1", "--pretty=format:%b")
    changed_raw  = git("diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD")
except Exception as exc:
    print(f"[changelog hook] git error: {exc}", file=sys.stderr)
    sys.exit(0)

changed_files = [f.strip() for f in changed_raw.splitlines() if f.strip()]

# ── Map changed files → unique area labels ─────────────────────────────────────
areas: list[str] = sorted({
    AREA_MAP[Path(f).name]
    for f in changed_files
    if Path(f).name in AREA_MAP
})

# ── Build the description ──────────────────────────────────────────────────────
description_parts = [subject]
if body:
    description_parts.append("")  # blank line
    description_parts.append(body)
description = "\n".join(description_parts).strip()

# ── Build Discord embed ────────────────────────────────────────────────────────
embed: dict = {
    "title": "📋 Update",
    "description": description or "*No description provided.*",
    "color": 5793266,  # 0x5865F2 — Discord blurple
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "footer": {"text": "Posted by Sai"},
    "fields": [],
}

if areas:
    embed["fields"].append({
        "name": "Areas Updated",
        "value": "\n".join(f"• {a}" for a in areas),
        "inline": False,
    })

# ── POST to Discord ────────────────────────────────────────────────────────────
payload = json.dumps({"embeds": [embed]}).encode("utf-8")
try:
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://discord.com, 1.0)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status in (200, 204):
            print("[changelog hook] ✅ Posted to Discord changelog")
        else:
            print(f"[changelog hook] ⚠️ Unexpected HTTP {resp.status}", file=sys.stderr)
except urllib.error.HTTPError as exc:
    body_text = exc.read().decode(errors="replace")
    print(f"[changelog hook] ⚠️ Webhook error {exc.code}: {body_text[:200]}", file=sys.stderr)
except Exception as exc:
    print(f"[changelog hook] ⚠️ Failed: {exc}", file=sys.stderr)
