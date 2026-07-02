import unittest

from music import normalize_query_for_lavalink


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


if __name__ == "__main__":
    unittest.main()
