# protogq

Groq/OpenAI-compatible prototyping helper with optional browser-backed web access.

## Overview

`protogq` keeps one LLM mode: Groq through the OpenAI-compatible API at `https://api.groq.com/openai/v1`.

The web helper uses only Python standard-library networking plus a local Chrome/Chromium DevTools connection. It can run Google search pages or visit a URL and extract visible page text without `requests`, BeautifulSoup, or search packages.

## Features

- Groq chat completions through the official `openai` Python client.
- OpenAI-compatible `base_url`, configurable with `GROQ_BASE_URL`.
- Optional web context through Chrome DevTools Protocol.
- Google News RSS fallback when Chrome/CDP is unavailable.
- Minimal dependencies: only `openai` at runtime.

## Requirements

- Python 3.13 or newer.
- A Groq API key in `GROQ_API_KEY`.
- Chrome or Chromium for full-page web access.

## Installation

```bash
uv python install 3.13
uv venv --python 3.13 protogq
source protogq/bin/activate
pip install -e .
```

On Windows:

```powershell
uv python install 3.13
uv venv --python 3.13 protogq
protogq\Scripts\activate
pip install -e .
```

## Quick Start

```bash
export GROQ_API_KEY="your-api-key"
protogq
```

On Windows PowerShell:

```powershell
$env:GROQ_API_KEY = "your-api-key"
protogq
```

## Usage

```python
from protogq import AIGenerator

ai_client = AIGenerator()
response = ai_client.generate(
    "News of the day in Italy",
    model="qwen/qwen3.6-27b",
    with_web=True,
    show_thinking=False,
)
print(response)
```

## Web Access

```python
from protogq import Web

web = Web()
print(web.google_search("Groq latest models"))
print(web.visit_page("https://console.groq.com/docs/models"))
```

Set `PROTOGQ_CHROME` if Chrome or Chromium is not in a standard location:

```bash
export PROTOGQ_CHROME="/path/to/chrome"
```

If Chrome cannot be reached, `google_search()` falls back to Google News RSS using only Python standard-library networking.

## License

`protogq` is licensed under the MIT License. See [LICENSE](LICENSE).
