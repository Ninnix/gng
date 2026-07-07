import logging
import os

from openai import OpenAI

from .utils import remove_think_tags
from .web import Web

logger = logging.getLogger(__name__)


class AIGenerator:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        api_key = api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")
        self.client = OpenAI(
            base_url=base_url or os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            api_key=api_key,
        )

    def generate(self, prompt: str, model: str | None = None, role: str = "user",
                 temperature: float = 0.7, with_web: bool = False,
                 show_thinking: bool = True) -> str:
        model = model or os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b")
        try:
            if with_web:
                web_results = Web().google_search(prompt)
                prompt = f"{prompt}\n\n<web>\n{web_results}\n</web>"
            completion = self.client.chat.completions.create(
                messages=[{"role": role, "content": prompt}],
                model=model,
                temperature=temperature,
            )
            content = completion.choices[0].message.content or ""
            return content if show_thinking else remove_think_tags(content)
        except Exception as e:
            logger.error("Error generating text with Groq: %s", e)
            raise
