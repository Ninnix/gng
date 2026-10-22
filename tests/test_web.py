import unittest
from unittest.mock import patch

from groq_not_grok.web import Web


class WebTests(unittest.TestCase):
    def test_search_uses_rss_if_google_returns_challenge(self):
        web = Web()
        with patch.object(web, "_run_page_js", return_value="CAPTCHA unusual traffic"), \
             patch.object(web, "_google_news_search", return_value="RSS results") as rss:
            self.assertEqual(web.google_search("topic"), "RSS results")
        rss.assert_called_once_with("topic")

    def test_search_uses_rss_if_browser_is_unavailable(self):
        web = Web()
        with patch.object(web, "_run_page_js", side_effect=OSError("missing browser")), \
             patch.object(web, "_google_news_search", return_value="RSS results"):
            self.assertEqual(web.google_search("topic"), "RSS results")

    def test_chromium_environment_setting_is_used(self):
        with patch.dict("os.environ", {"GROQ_NOT_GROK_CHROME": "/tmp/chromium"}):
            self.assertEqual(Web._chrome_executable(), "/tmp/chromium")


if __name__ == "__main__":
    unittest.main()
