import unittest

from music import Music, normalize_query_for_lavalink


class DummyResponse:
    def __init__(self):
        self.done = False
        self.messages = []

    def is_done(self):
        return self.done

    async def send_message(self, **kwargs):
        self.done = True
        self.messages.append(("send_message", kwargs))

    async def defer(self, **kwargs):
        self.done = True
        self.messages.append(("defer", kwargs))


class DummyFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)


class DummyInteraction:
    def __init__(self):
        self.response = DummyResponse()
        self.followup = DummyFollowup()


class NormalizeQueryTests(unittest.TestCase):
    def test_spotify_url_uses_spsearch(self):
        self.assertEqual(
            normalize_query_for_lavalink("https://open.spotify.com/track/123"),
            "spsearch:https://open.spotify.com/track/123",
        )

    def test_apple_music_url_uses_amsearch(self):
        self.assertEqual(
            normalize_query_for_lavalink("https://music.apple.com/us/album/test/123"),
            "amsearch:https://music.apple.com/us/album/test/123",
        )

    def test_deezer_url_uses_dzsearch(self):
        self.assertEqual(
            normalize_query_for_lavalink("https://www.deezer.com/track/456"),
            "dzsearch:https://www.deezer.com/track/456",
        )

    def test_plain_text_defaults_to_youtube(self):
        self.assertEqual(normalize_query_for_lavalink("Never Gonna Give You Up"), "ytsearch:Never Gonna Give You Up")

    def test_send_interaction_uses_followup_when_response_already_done(self):
        cog = Music.__new__(Music)
        interaction = DummyInteraction()

        async def run_test():
            interaction.response.done = True
            await cog.send_interaction(interaction, content="ok", ephemeral=True)
            return interaction.followup.messages

        messages = unittest.IsolatedAsyncioTestCase().runTest = None
        import asyncio

        result = asyncio.run(run_test())
        self.assertEqual(len(result), 1)
        self.assertIn("content", result[0])
        self.assertEqual(result[0]["content"], "ok")


if __name__ == "__main__":
    unittest.main()
