Create a virtual environment named `protogq`:

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

Set the Groq API key:

```bash
export GROQ_API_KEY="your-api-key"
```

On Windows PowerShell:

```powershell
$env:GROQ_API_KEY = "your-api-key"
```
