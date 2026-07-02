import discord


HELP_PAGES = [
    {
        "title": "🧭 General",
        "commands": [
            {
                "name": "/help",
                "usage": "/help",
                "desc": "Open this interactive help menu.",
            },
            {
                "name": "/status",
                "usage": "/status",
                "desc": "Bot + system diagnostics (uptime, CPU/RAM, latency).",
            },
            {
                "name": "/serverinfo",
                "usage": "/serverinfo",
                "desc": "Show info about the current server.",
            },
            {
                "name": "/userinfo",
                "usage": "/userinfo [user]",
                "desc": "Show info about a user (defaults to yourself).",
            },
        ],
    },
    {
        "title": "🎮 Roblox",
        "commands": [
            {
                "name": "/players",
                "usage": "/players",
                "desc": "Live Roblox player / visits stats for the configured place.",
            },
            {
                "name": "/chat",
                "usage": "/chat <message>",
                "desc": "Chat with the built-in CFrame AI.",
            },
            {
                "name": "/lua",
                "usage": "/lua <query>",
                "desc": "Lookup Lua / Roblox API terms with syntax + examples.",
            },
            {
                "name": "/roadmap",
                "usage": "/roadmap view | add | update | remove | clear",
                "desc": "View/manage the game roadmap (staff actions).",
            },
            {
                "name": "/clearchat",
                "usage": "/clearchat",
                "desc": "Clear your AI chat history.",
            },
        ],
    },
    {
        "title": "🎵 Music",
        "commands": [
            {
                "name": "/play",
                "usage": "/play <query|url>",
                "desc": "Play music from YouTube, Spotify, Apple Music, or direct links via Lavalink.",
            },
            {
                "name": "/deemix",
                "usage": "/deemix <query|url>",
                "desc": "Play Deezer/DeeMix content through Lavalink.",
            },
            {
                "name": "/lyrics",
                "usage": "/lyrics [query]",
                "desc": "Show lyrics for the current track or a searched song.",
            },
            {
                "name": "/filter",
                "usage": "/filter <preset>",
                "desc": "Apply a preset like nightcore, bassboost, vaporwave, robot, or telephone.",
            },
            {
                "name": "/join",
                "usage": "/join",
                "desc": "Join the voice channel you are currently in.",
            },
            {
                "name": "/leave",
                "usage": "/leave",
                "desc": "Leave the voice channel and clear the queue.",
            },
            {
                "name": "/skip",
                "usage": "/skip",
                "desc": "Skip the current track.",
            },
            {
                "name": "/pause",
                "usage": "/pause",
                "desc": "Pause playback.",
            },
            {
                "name": "/resume",
                "usage": "/resume",
                "desc": "Resume playback.",
            },
        ],
    },
    {
        "title": "🛠️ Server Tools",
        "commands": [
            {
                "name": "/moderation overview",
                "usage": "/moderation overview",
                "desc": "Advanced hub for moderation & server settings.",
            },
            {
                "name": "/setlogchannel",
                "usage": "/setlogchannel <channel>",
                "desc": "Set the channel where moderation logs will be sent.",
            },
            {
                "name": "/clearlogchannel",
                "usage": "/clearlogchannel",
                "desc": "Disable moderation logs for this server.",
            },
            {
                "name": "/setwelcomechannel",
                "usage": "/setwelcomechannel <channel>",
                "desc": "Set the welcome message channel.",
            },
            {
                "name": "/setwelcomemessage",
                "usage": "/setwelcomemessage <message template>",
                "desc": "Update the welcome template.",
            },
            {
                "name": "/disablewelcome",
                "usage": "/disablewelcome <true|false>",
                "desc": "Enable/disable welcome messages.",
            },
            {
                "name": "/welcometest",
                "usage": "/welcometest",
                "desc": "Send a test welcome message to the configured channel.",
            },
        ],
    },
    {
        "title": "⚔️ Moderation",
        "commands": [
            {
                "name": "/warn",
                "usage": "/warn <member> <reason>",
                "desc": "Warn a member.",
            },
            {
                "name": "/warnings",
                "usage": "/warnings <member>",
                "desc": "View a member's warnings.",
            },
            {
                "name": "/clearwarnings",
                "usage": "/clearwarnings <member>",
                "desc": "Clear a member's warnings.",
            },
            {
                "name": "/mute",
                "usage": "/mute <member> <duration> <reason>",
                "desc": "Mute for duration like 10m, 1h, 2d.",
            },
            {
                "name": "/unmute",
                "usage": "/unmute <member>",
                "desc": "Remove a timeout from a member.",
            },
            {
                "name": "/kick",
                "usage": "/kick <member> <reason>",
                "desc": "Kick a member.",
            },
            {
                "name": "/ban",
                "usage": "/ban <member> <reason>",
                "desc": "Ban a member.",
            },
            {
                "name": "/unban",
                "usage": "/unban <user_id>",
                "desc": "Unban a user by ID.",
            },
            {
                "name": "/clear",
                "usage": "/clear <amount 1-100>",
                "desc": "Delete messages in the current channel.",
            },
            {
                "name": "/slowmode",
                "usage": "/slowmode <seconds 0-21600>",
                "desc": "Set channel slowmode.",
            },
        ],
    },
    {
        "title": "🎉 Community",
        "commands": [
            {
                "name": "/gstart",
                "usage": "/gstart <prize> <duration> <winner_count=1>",
                "desc": "Start a giveaway (duration like 30m / 2d).",
            },
            {
                "name": "/gend",
                "usage": "/gend <message_id>",
                "desc": "End a giveaway early.",
            },
            {
                "name": "/greroll",
                "usage": "/greroll <message_id>",
                "desc": "Reroll winners for a finished giveaway.",
            },
            {
                "name": "/level",
                "usage": "/level",
                "desc": "Show your current leveling progress.",
            },
            {
                "name": "/leaderboard",
                "usage": "/leaderboard",
                "desc": "Show the server leveling leaderboard.",
            },
            {
                "name": "/toggleleveling",
                "usage": "/toggleleveling <true|false>",
                "desc": "Enable/disable leveling.",
            },
            {
                "name": "/toggleantispam",
                "usage": "/toggleantispam <true|false>",
                "desc": "Enable/disable anti-spam.",
            },
            {
                "name": "/setantispamthreshold",
                "usage": "/setantispamthreshold <threshold>",
                "desc": "Set the anti-spam trigger message threshold.",
            },
            {
                "name": "/autorole",
                "usage": "/autorole <set|clear|list> [role]",
                "desc": "Set/clear/list auto-role on join.",
            },
            {
                "name": "/reactionrole",
                "usage": "/reactionrole <add|remove|list> ...",
                "desc": "Attach reaction roles to a message.",
            },
        ],
    },
    {
        "title": "🛠️ Staff",
        "commands": [
            {
                "name": "/dev",
                "usage": "/dev enable|disable|list ...",
                "desc": "Developer controls (owner only).",
            },
            {
                "name": "/devlog",
                "usage": "/devlog",
                "desc": "Post a development log (staff only).",
            },
            {
                "name": "/updates",
                "usage": "/updates",
                "desc": "Post patch/update notes (staff only).",
            },
            {
                "name": "/hype",
                "usage": "/hype",
                "desc": "Post a hype announcement (staff only).",
            },
            {
                "name": "/countdown",
                "usage": "/countdown",
                "desc": "Start a live countdown (staff only).",
            },
            {
                "name": "/patchpreview",
                "usage": "/patchpreview post|reveal ...",
                "desc": "Tease then reveal patch notes (staff only).",
            },
            {
                "name": "/testing",
                "usage": "/testing open|close|join|list ...",
                "desc": "Manage beta testing signups.",
            },
        ],
    },
]


class HelpPaginator(discord.ui.View):
    def __init__(self, *, author_id: int, pages: list[dict], timeout: float = 120):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.pages = pages
        self.page = 0
        self._rebuild()

    def _build_embed(self) -> discord.Embed:
        data = self.pages[self.page]
        embed = discord.Embed(
            title=f"📚 Help — {data['title']}",
            description="Use the buttons to navigate. Click Close when you're done.",
            color=0x5865F2,
        )
        for cmd in data["commands"]:
            embed.add_field(
                name=f"{cmd['name']}",
                value=f"**Usage:** `{cmd['usage']}`\n{cmd['desc']}",
                inline=False,
            )
        embed.set_footer(text=f"CFrame Bot · Help · Page {self.page+1}/{len(self.pages)}")
        return embed

    def _rebuild(self):
        self.clear_items()

        prev_btn = discord.ui.Button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
        next_btn = discord.ui.Button(label="Next ➡️", style=discord.ButtonStyle.secondary)
        close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger)

        prev_btn.disabled = self.page <= 0
        next_btn.disabled = self.page >= len(self.pages) - 1

        async def on_prev(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Not your menu.", ephemeral=True)
                return
            self.page -= 1
            prev_btn.disabled = self.page <= 0
            next_btn.disabled = self.page >= len(self.pages) - 1
            await interaction.response.edit_message(embed=self._build_embed(), view=self)

        async def on_next(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Not your menu.", ephemeral=True)
                return
            self.page += 1
            prev_btn.disabled = self.page <= 0
            next_btn.disabled = self.page >= len(self.pages) - 1
            await interaction.response.edit_message(embed=self._build_embed(), view=self)

        async def on_close(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Not your menu.", ephemeral=True)
                return
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)

        prev_btn.callback = on_prev
        next_btn.callback = on_next
        close_btn.callback = on_close

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(close_btn)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

