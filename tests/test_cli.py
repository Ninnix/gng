import io
import json
import sys
import unittest
from unittest.mock import patch

from gng import GroqError
import gng.main as cli


def response(data):
    return io.BytesIO(json.dumps(data).encode())


class ChatMessagesTests(unittest.TestCase):
    def test_chat_messages_keeps_prior_turns(self):
        result = {"choices": [{"finish_reason": "stop", "message": {"content": "third"}}]}
        messages = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ]
        with patch("gng.client.urlopen", return_value=response(result)) as send:
            self.assertEqual(cli.AIGenerator("secret").client.chat_messages(messages), "third")
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload["messages"], messages)

    def test_generator_uses_history_when_supplied(self):
        ai = cli.AIGenerator("secret")
        with patch.object(ai.client, "chat_messages", return_value="answer") as chat:
            self.assertEqual(ai.generate("three", history=[
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
            ]), "answer")
        self.assertEqual(chat.call_args.args[0], [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ])


class CatalogTests(unittest.TestCase):
    def test_offline_catalog_keeps_default_and_vision_models(self):
        with patch.object(cli, "GroqClient", side_effect=GroqError("no key")):
            choices, source = cli._catalog()
        self.assertEqual(source, "offline")
        ids = {choice["id"] for choice in choices}
        self.assertIn("openai/gpt-oss-120b", ids)
        self.assertIn("qwen/qwen3.8-27b", ids)
        self.assertTrue(all(choice["access"] == "free" for choice in choices))

    def test_models_flag_requests_free_catalog(self):
        with patch.object(cli, "_print_models") as show, \
             patch.object(sys, "argv", ["gng", "--models"]):
            cli.main()
        self.assertEqual(show.call_args.kwargs["access"], "free")

    def test_models_all_flag_requests_full_catalog(self):
        with patch.object(cli, "_print_models") as show, \
             patch.object(sys, "argv", ["gng", "--models-all"]):
            cli.main()
        self.assertIsNone(show.call_args.kwargs["access"])

    def test_picker_accepts_number_and_alias(self):
        choices = [{
            "id": item["id"],
            "label": item["id"],
            "owner": item["owned_by"],
            "ctx": item["context_window"],
            "vision": item["id"] == cli.DEFAULT_VISION_MODEL,
        } for item in cli.FALLBACK_MODELS]
        choices.sort(key=lambda choice: (
            0 if choice["id"] == cli.DEFAULT_MODEL else 1 if choice["id"] == cli.DEFAULT_VISION_MODEL else 2,
            choice["id"],
        ))
        self.assertEqual(cli._parse_choice("1", choices)[0], cli.DEFAULT_MODEL)
        self.assertEqual(cli._parse_choice("qwen", choices)[0], cli.DEFAULT_VISION_MODEL)


if __name__ == "__main__":
    unittest.main()
