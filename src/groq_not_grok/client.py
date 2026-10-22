import json
import os
import re
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
MAX_IMAGE_URL_LENGTH = 28 * 1024 * 1024
IMAGE_URL = re.compile(r"data:image/(?:jpeg|png|gif|webp);base64,[A-Za-z0-9+/]*={0,2}\Z")


class GroqError(RuntimeError):
    pass


class GroqClient:
    def __init__(self, api_key=None, base_url=None, timeout=90, retries=2,
                 model=None, vision_model=None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY") or os.getenv("GROQ_KEY")
        if not self.api_key:
            raise GroqError("Set GROQ_API_KEY or GROQ_KEY")
        self.base_url = (base_url or os.getenv("GROQ_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.getenv("GROQ_MODEL") or DEFAULT_MODEL
        self.vision_model = (vision_model or os.getenv("GROQ_VISION_MODEL")
                             or DEFAULT_VISION_MODEL)
        self.timeout = max(1, float(timeout))
        self.retries = max(0, int(retries))

    def complete(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self.base_url + "/chat/completions"
        for attempt in range(self.retries + 1):
            request = Request(url, data=body, headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "groq-not-grok/0.2",
            })
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                return self._content(data)
            except HTTPError as error:
                status = error.code
                retry_after = self._retry_after(error)
                error.close()
                if status not in (408, 425, 429, 500, 502, 503, 504) or attempt == self.retries:
                    raise GroqError(f"Groq HTTP {status}") from None
                if retry_after > 30:
                    raise GroqError("Groq rate limited; retry in a later request") from None
                time.sleep(max(2 ** attempt, retry_after))
            except (URLError, TimeoutError, OSError) as error:
                if attempt == self.retries:
                    raise GroqError("Groq connection failed") from error
                time.sleep(2 ** attempt)
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise GroqError("Groq returned an invalid response") from error

    @staticmethod
    def _content(data):
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise GroqError("Groq truncated the response; reduce the output size")
        content = choice["message"].get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict) and part.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            raise GroqError("Groq returned no text")
        return content

    @staticmethod
    def _retry_after(error):
        if not error.headers:
            return 0
        value = error.headers.get("Retry-After", "").strip()
        if value.isdigit():
            return min(3600, int(value))
        try:
            return max(0, min(3600, int(parsedate_to_datetime(value).timestamp() - time.time())))
        except (TypeError, ValueError, OverflowError):
            return 0

    def chat(self, prompt, model=None, role="user", temperature=0.7,
             max_completion_tokens=8192, response_format=None, reasoning_effort=None):
        payload = {
            "model": model or self.model,
            "messages": [{"role": role, "content": prompt}],
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        return self.complete(payload)

    def vision(self, prompt, image_url, model=None):
        if not isinstance(image_url, str):
            raise GroqError("Image must be an HTTPS URL or supported image data URL")
        inline = IMAGE_URL.fullmatch(image_url) is not None
        if (not image_url.startswith("https://") and not inline
                or inline and len(image_url) > MAX_IMAGE_URL_LENGTH):
            raise GroqError("Image must be an HTTPS URL or supported image data URL under 20 MB")
        return self.complete({
            "model": model or self.vision_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            "temperature": 0.2,
            "max_completion_tokens": 512,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
        })

    def list_models(self):
        request = Request(self.base_url + "/models", headers={
            "Authorization": "Bearer " + self.api_key,
            "Accept": "application/json",
            "User-Agent": "groq-not-grok/0.2",
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
            models = data["data"]
            if not isinstance(models, list):
                raise ValueError("models must be a list")
            return models
        except HTTPError as error:
            error.close()
            raise GroqError(f"Groq HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError) as error:
            raise GroqError("Groq connection failed") from error
        except (KeyError, TypeError, ValueError) as error:
            raise GroqError("Groq returned an invalid model list") from error
