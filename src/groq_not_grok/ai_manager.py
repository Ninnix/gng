import os

from .client import GroqClient
from .utils import remove_think_tags


class AIGenerator:
    def __init__(self, api_key=None, base_url=None, model=None, vision_model=None,
                 timeout=90, retries=2, web=None):
        self.client = GroqClient(api_key, base_url, timeout, retries, model, vision_model)
        self.model = model or os.getenv("GROQ_MODEL") or self.client.model
        self.web = web

    def generate(self, prompt, model=None, role="user", temperature=0.7,
                 with_web=False, show_thinking=True, max_completion_tokens=8192,
                 response_format=None, reasoning_effort=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if with_web:
            if self.web is None:
                from .web import Web
                self.web = Web()
            context = self.web.google_search(prompt)
            if context:
                prompt = f"{prompt}\n\n<web>\n{context}\n</web>"
        text = self.client.chat(prompt, model, role, temperature,
                                max_completion_tokens, response_format, reasoning_effort)
        return text if show_thinking else remove_think_tags(text)

    def ask(self, prompt, *, web_search=False):
        return self.generate(prompt, temperature=0.4, with_web=web_search)

    def ask_image(self, prompt, image_url, model=None):
        return self.client.vision(prompt, image_url, model)

    def generate_with_image(self, prompt, image_url, model=None):
        return self.ask_image(prompt, image_url, model)

    def list_models(self):
        return self.client.list_models()

    def close(self):
        if self.web is not None:
            self.web.close()
            self.web = None
