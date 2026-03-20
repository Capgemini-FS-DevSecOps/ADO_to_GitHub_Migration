# Contributing

## Development Setup

```bash
git clone <this-repo>
cd ADO2GH
python -m venv venv
source venv/bin/activate
pip install -e .
```

## Project Structure

The tool is a Python package under `ado2gh/`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design.

Key conventions:
- All CLI commands live in `ado2gh/cli.py`
- All dataclasses and enums live in `ado2gh/models.py`
- Each module imports from `ado2gh.logging_config` for logging
- State is persisted in SQLite via `ado2gh/state/db.py`
- API clients are in `ado2gh/clients/` with retry and rate-limit handling

## Adding a New ADO Task Mapping

Edit `ado2gh/pipelines/transformer.py` — add to the `ADO_TASK_MAP` dict:

```python
"YourTask@1": "owner/action@vN",
```

For tasks that map to `run:` steps (shell commands), use the run-based format already used by `CmdLine@2`, `Bash@3`, etc.

## Adding a New Migration Scope

1. Add the scope to `MigrationScope` enum in `ado2gh/models.py`
2. Add a handler method `_migrate_<scope>` in `ado2gh/core/migration_engine.py`
3. Register the handler in the `scope_handlers` dict in `migrate_repo()`

## Adding a New CLI Command

Add the Click command in `ado2gh/cli.py`. Use lazy imports inside the function body to keep startup fast:

```python
@cli.command()
@click.option("--config", "-c", required=True)
def my_command(config):
    """Description."""
    from ado2gh.core.config_loader import ConfigLoader
    # ...
```

## Code Style

- No unnecessary abstractions — three similar lines are better than a premature helper
- Imports at module top except for CLI commands (lazy imports for fast startup)
- Rich for console output, standard logging for debug/info/warning/error
- SQLite for state — no external database dependencies
