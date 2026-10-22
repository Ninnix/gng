# Groq Not Grok

A dependency-free Python client for Groq chat completions, image input, and browser-backed web search.

## Requirements

- Python 3.11 or newer.
- A Groq API key in `GROQ_API_KEY` or `GROQ_KEY`.
- Chrome, Chromium, or Brave for browser search and page extraction. Google News RSS is used when Chrome is unavailable or Google presents a challenge.

The package uses only Python's standard library at runtime. `hatchling` is needed only to build a wheel.

## Install

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows, activate `.venv\\Scripts\\activate` instead.

## Use

```python
from gng import AIGenerator

ai = AIGenerator()
try:
    print(ai.generate("Explain this result", with_web=True))
    print(ai.ask_image("Describe the image", "https://example.com/image.jpg"))
finally:
    ai.close()
```

The default chat model is `openai/gpt-oss-120b`; image requests use `qwen/qwen3.8-27b` and return JSON. Set `GROQ_MODEL` or `GROQ_VISION_MODEL` to override either default. `GROQ_BASE_URL` changes the API endpoint.

## Browser settings

Set `GROQ_NOT_GROK_CHROME` when the browser executable is outside a standard location. `GROQ_NOT_GROK_CDP_PORT`, `GROQ_NOT_GROK_CDP_HOST`, and `GROQ_NOT_GROK_CHROME_PROFILE` configure the local DevTools connection and browser profile. `Web.google_search()` visits Google in the browser; `Web.visit_page()` extracts visible content from a URL. Search falls back to Google News RSS when the browser is unavailable or search is challenged.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).
