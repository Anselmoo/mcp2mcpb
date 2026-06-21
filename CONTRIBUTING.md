# Contributing to mcp2mcpb

Thanks for your interest! mcp2mcpb is a Python 3.12+ project managed with `uv`.

## Setup

```bash
uv sync --extra dev
uvx pre-commit install   # optional: run hooks on every commit
```

## Before opening a PR

Run the full gate and make sure it's green:

```bash
uv run poe check         # ruff lint + format-check + ty + pytest (100% coverage)
```

Tests are fully offline (`respx`, `monkeypatch`, `tmp_path`) — never add a test
that hits the network or runs `uvx`/`npx`.

## Conventions

- Conventional-commit subjects (`feat:`, `fix:`, `chore:`, `ci:`, `docs:` …).
- Branch names use those prefixes (e.g. `feat/…`, `fix/…`).
- Update `CHANGELOG.md` under `## [Unreleased]`.
- Versions are bumped by `rrt`; do not hand-edit version strings.

## Reporting bugs / requesting features

Use the issue templates. For security issues, see [SECURITY.md](SECURITY.md).
