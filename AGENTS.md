# Repository Guidelines

## Project Structure & Module Organization
Claude Engineer v3 is a Python assistant framework with both CLI and Flask web interfaces. The main entrypoints are `qqcode.py` for the terminal assistant, `app.py` for the web UI, and `config.py` for provider, model, and tool settings. Tool implementations live in `tools/`, with `tools/base.py` defining the shared tool contract. UI assets are split between `templates/` and `static/` (`static/css/`, `static/js/`). Tests live in `tests/`. Treat `Claude-Eng-v2/` as legacy reference code, not the active implementation.

## Build, Test, and Development Commands
- `uv venv && source .venv/bin/activate` — create and activate a local virtual environment.
- `uv run app.py` — start the Flask interface at `http://localhost:5000`.
- `uv run qqcode.py` — run the CLI assistant locally.
- `uv run pytest` — run the pytest suite configured in `pyproject.toml`.
- `uv run ruff check .` — lint Python files.
- `uv run black .` — format Python files with Black.
- `uv run mypy .` — run static type checks.

## Coding Style & Naming Conventions
Use Python 3.9+ style with 4-space indentation and Black-compatible formatting. Keep line length at 88 characters. Ruff enforces pycodestyle, Pyflakes, isort, bugbear, comprehensions, pyupgrade, and Ruff-specific rules. Avoid relative imports; they are banned in `pyproject.toml`. Name modules and tool files in lowercase `snake_case`, classes in `PascalCase`, and functions or variables in `snake_case`.

## Testing Guidelines
Use pytest for new tests and place them under `tests/` as `test_*.py`. Focus tests on public behavior such as response parsing, provider compatibility, and tool execution boundaries. If changing a tool, cover both successful execution and error handling. Run `uv run pytest` before submitting changes.

## Commit & Pull Request Guidelines
Recent history uses short, direct subjects such as `Update readme.md`, `web interface`, and `better token management`. Prefer concise imperative or descriptive commit subjects, with extra context in the body when behavior changes. Pull requests should include a summary, testing evidence, linked issues when applicable, and screenshots or recordings for UI changes in `app.py`, `templates/`, or `static/`.

## Security & Configuration Tips
Do not commit secrets. Use `.env` locally and keep `.env.example` updated when configuration changes. Provider credentials, model names, and token limits should flow through `config.py` or environment variables rather than being hardcoded.
