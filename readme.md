# Claude Engineer v3 Custom

A substantially modified AI assistant framework based on the MIT-licensed `claude-engineer` project. It provides both a terminal assistant and a Flask web interface, supports Anthropic and OpenAI-compatible providers, and loads tools dynamically from the local `tools/` directory.

## Features

- CLI assistant entrypoint in `qqcode.py`
- Flask web UI entrypoint in `app.py`
- Provider configuration through `config.py` and environment variables
- Dynamic tool loading from `tools/`
- Tool creation and extension workflow
- Web interface assets in `templates/` and `static/`
- Optional E2B-powered Python code execution tool
- Support for Anthropic and OpenAI-compatible APIs

## Project Structure

```text
claude-engineer/
├── app.py                  # Flask web interface
├── qqcode.py               # CLI assistant and core runtime
├── config.py               # Provider, model, token, and path settings
├── prompts/                # System prompt definitions
│   └── system_prompts.py
├── tools/                  # Built-in and generated tools
│   ├── base.py             # Shared tool interface
│   └── *.py                # Tool implementations
├── templates/              # Flask HTML templates
├── static/                 # Web CSS and JavaScript
├── .env.example            # Example local configuration
├── pyproject.toml          # Package and dev-tool configuration
├── requirements.txt        # Legacy/alternate dependency list
└── uv.lock                 # uv lockfile
```

Private runtime data such as `.env`, `saved_contexts/`, `uploads/`, local notes, and generated caches are intentionally ignored by Git.

## Installation

Install [`uv`](https://docs.astral.sh/uv/) first, then clone your repository:

```bash
git clone https://github.com/UESTC-ly/qqcode-agent.git
cd qqcode-agent
uv venv
source .venv/bin/activate
uv sync
```

On Windows PowerShell:

```powershell
git clone https://github.com/UESTC-ly/qqcode-agent.git
cd qqcode-agent
uv venv
.venv\Scripts\activate
uv sync
```

## Configuration

Copy the example environment file and fill in only the keys you need:

```bash
cp .env.example .env
```

Supported provider modes:

```bash
# Anthropic
PROVIDER=anthropic
MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=your_anthropic_key

# OpenAI-compatible provider
PROVIDER=openai_compat
MODEL=deepseek-chat
OPENAI_API_KEY=your_provider_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
```

Optional:

```bash
E2B_API_KEY=your_e2b_key
```

Never commit `.env` or real API keys.

## Running Locally

Start the web interface:

```bash
uv run app.py
```

Then open:

```text
http://localhost:5000
```

Run the CLI assistant:

```bash
uv run qqcode.py
```

## Development Commands

```bash
uv run pytest          # Run tests, when local tests are present
uv run ruff check .    # Lint Python files
uv run black .         # Format Python files
uv run mypy .          # Run static type checks
```

## Built-in Tools

The assistant loads tool classes from `tools/`. Current tools include:

- `toolcreator` — create new tools from natural language requirements
- `duckduckgotool` — perform DuckDuckGo searches
- `webscrapertool` — extract readable page content
- `browsertool` — open URLs in the system browser
- `filecontentreadertool`, `filecreatortool`, `fileedittool`, `diffeditortool` — file operations
- `createfolderstool` — create directory structures
- `lintingtool` — run Ruff checks
- `uvpackagemanager` — manage Python packages through uv
- `e2bcodetool` — execute Python in E2B when configured
- `screenshottool` — capture screenshots for vision-capable workflows
- `cwdenvironmenttool` — inspect current working-directory context
- `testrunloganalysistool` — summarize test-run logs

## Attribution

This repository is a substantially modified version of the MIT-licensed `claude-engineer` project.

Original repository: <https://github.com/Doriandarko/claude-engineer>

The upstream project declares MIT licensing in its README and package metadata. This repository preserves that notice and adds a root `LICENSE` file for clarity. Substantial modifications, restructuring, and additional features were made in this repository.

## License

MIT. See [`LICENSE`](LICENSE).
