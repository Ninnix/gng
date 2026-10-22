import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from groq_not_grok import AIGenerator, DEFAULT_MODEL, DEFAULT_VISION_MODEL, GroqClient, GroqError


def response(data):
    return io.BytesIO(json.dumps(data).encode())


class ClientTests(unittest.TestCase):
    def test_chat_uses_defaults_and_authorization(self):
        result = {"choices": [{"finish_reason": "stop", "message": {"content": "hello"}}]}
        with patch("groq_not_grok.client.urlopen", return_value=response(result)) as send:
            client = GroqClient("secret")
            self.assertEqual(client.chat("Hi"), "hello")
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], DEFAULT_MODEL)
        self.assertEqual(payload["messages"][0]["content"], "Hi")

    def test_vision_uses_configured_vision_model_and_json(self):
        result = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        image = "data:image/png;base64,aGVsbG8="
        with patch("groq_not_grok.client.urlopen", return_value=response(result)) as send:
            self.assertEqual(GroqClient("secret").vision("Review", image), "{}")
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload["model"], DEFAULT_VISION_MODEL)
        self.assertEqual(payload["messages"][0]["content"][1]["image_url"]["url"], image)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_retryable_http_error_retries_once(self):
        limited = HTTPError("url", 429, "limited", {"Retry-After": "0"}, None)
        ok = response({"choices": [{"message": {"content": "ready"}}]})
        with patch("groq_not_grok.client.urlopen", side_effect=[limited, ok]) as send, \
             patch("groq_not_grok.client.time.sleep"):
            self.assertEqual(GroqClient("secret", retries=1).chat("Hi"), "ready")
        self.assertEqual(send.call_count, 2)

    def test_truncated_response_and_invalid_image_are_rejected(self):
        partial = response({"choices": [{"finish_reason": "length", "message": {"content": "x"}}]})
        with patch("groq_not_grok.client.urlopen", return_value=partial):
            with self.assertRaisesRegex(GroqError, "truncated"):
                GroqClient("secret").chat("Hi")
        with self.assertRaises(GroqError):
            GroqClient("secret").vision("Review", "data:image/svg+xml;base64,PHN2Zz4=")

    def test_generator_adds_web_context_and_can_close(self):
        web = type("Search", (), {"google_search": lambda self, query: "result", "close": lambda self: None})()
        ai = AIGenerator("secret", web=web)
        with patch.object(ai.client, "chat", return_value="answer") as chat:
            self.assertEqual(ai.ask("query", web_search=True), "answer")
        self.assertIn("<web>\nresult\n</web>", chat.call_args.args[0])
        ai.close()
        self.assertIsNone(ai.web)


if __name__ == "__main__":
    unittest.main()
