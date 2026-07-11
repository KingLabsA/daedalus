# Contributing to Daedalus

## Setup

```bash
pip install -e ".[dev]"
pre-commit install
```

## Development Workflow

1. Create a feature branch from `main`
2. Make your changes
3. Run tests: `python -m pytest tests/ -v`
4. Run linting: `ruff check . && ruff format --check .`
5. Submit a pull request

## Code Style

- Python 3.10+ compatible
- Line length: 160 chars (enforced by ruff)
- Type hints encouraged but not required for existing code
- No comments unless the why is non-obvious

## Testing

- Unit tests in `tests/test_*.py`
- E2E tests: `python tests/test_e2e_ws.py` (requires running server)
- Frontend: `cd desktop && npx tsc --noEmit`

## Architecture

- `agent_ultimate.py` — main agent loop (being modularized into `core/`)
- `core/` — extracted modules (providers, epistemic, intel, senses, platform, context, cognition)
- `desktop/` — React + Tauri frontend
- `hermes_cli.py` — CLI entry point

## Adding a Provider

1. Add config to `PROVIDER_CONFIGS` in `core/providers.py`
2. Add cost rates to `COST_PER_1K`
3. Add env var to `.env.example`
4. Add liveness probe in `_probe_provider()`
