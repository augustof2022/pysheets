# pysheets

Python tools for working with spreadsheets.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
uv sync                    # create .venv and install dev dependencies
uv run pytest              # run tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pysheets --version  # run the CLI
```

## Layout

```
src/pysheets/   package source
tests/          pytest suite
```
