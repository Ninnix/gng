import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from gng.web import Web, WebError


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

    def test_google_image_search_runs_page_javascript(self):
        web = Web()
        result = {"text": "Google Images", "results": [{
            "url": "https://images.example.org/claude.jpg",
            "source": "https://example.org/claude",
            "description": "Claude interface", "width": 1200, "height": 800,
            "engine": "Google Images",
        }]}
        with patch.object(web, "_run_page_js", return_value=json.dumps(result)) as run:
            images = web.google_image_search("Claude Duet", limit=10)
        self.assertEqual(images, result["results"])
        url, script = run.call_args.args
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["tbm"], ["isch"])
        self.assertEqual(params["q"], ["Claude Duet"])
        self.assertTrue(run.call_args.kwargs["dynamic_scroll"])
        self.assertIn("imgurl", script)
        self.assertIn("imgrefurl", script)

    def test_google_image_search_rejects_browser_challenge(self):
        web = Web()
        page = {"text": "Our systems have detected unusual traffic", "results": []}
        with patch.object(web, "_run_page_js", return_value=json.dumps(page)):
            with self.assertRaisesRegex(WebError, "browser challenge"):
                web.google_image_search("Claude Duet")

    def test_http_request_finishes_at_body_boundary(self):
        class Socket:
            def __init__(self, response):
                self.response = response

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def settimeout(self, timeout):
                pass

            def sendall(self, data):
                pass

            def recv(self, size):
                if self.response:
                    data, self.response = self.response, b""
                    return data
                raise TimeoutError("connection remains open")

        responses = (
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nOK\r\n0\r\n\r\n",
        )
        for response in responses:
            with self.subTest(response=response[:35]), \
                 patch("gng.web.socket.create_connection", return_value=Socket(response)):
                self.assertEqual(Web(port=1)._http_request("GET", "/json/version"), "OK")


if __name__ == "__main__":
    unittest.main()
