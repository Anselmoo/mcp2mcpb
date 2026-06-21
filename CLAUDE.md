# mcp2mcpb

CLI + GitHub Action that converts published PyPI/npm MCP servers into `.mcpb`
bundles for Claude Desktop. Python 3.12+, managed with `uv`/hatchling.

## Commands

```bash
uv sync --extra dev          # install deps
uv run poe check             # lint + format-check + type-check + tests (full gate)
uv run poe test              # pytest only        (poe test-cov for coverage)
uv run poe format            # ruff format (writes); poe lint / poe type-check
uv run poe build             # uv build → dist/ (gitignored)
uv run poe unpack <f.mcpb>   # extract a bundle + print its manifest
```

Run `uv run poe check` and confirm it's green before claiming work is done.

## CLI

```bash
mcp2mcpb <package> [-r pypi|npm] [-v VERSION] [-m complete|reference] [--from-dist PATH] [--no-probe] [--verbose]
mcp2mcpb unpack <bundle.mcpb> [-o DIR]
```

`mcp2mcpb <package>` works without typing `convert`: `__main__.main()` injects the
implicit `convert` command (the Typer app is a multi-command group). Preserve that
wrapper when touching the CLI.

- `--from-dist PATH` builds from a locally-built wheel / npm `.tgz` (or a dir
  containing one) and skips the registry fetch — use it in release CI to bundle
  the version being shipped before it is published. `--registry` still selects
  the runtime target (uvx/npx). Implies `--no-probe`.

## Architecture (`src/mcp2mcpb/`)

Pipeline, one module per stage:
`fetcher` (async PyPI/npm download) → `inspector` (entry point / extras / in-package
config) → `prober` (`--help` probe) → `launch` (resolve recipe) → `generator`
(synthesize `Manifest`) → `bundler` (vendor deps, complete mode) → `packer` (zip).

- `models.py` — Pydantic v2 **frozen** models; validate at the boundary, never
  inside pipeline functions.
- `launch.py` — recipe precedence: **CLI > `.mcpb.toml`/`[tool.mcpb]` > in-package
  > `--help` probe > default**. Sidecar keys: `runner, entry-script, extras,
  subcommand, transport`.
- `generator.py` — pure (no I/O); turns a resolved `LaunchSpec` into `mcp_config`.
- `ui.py` — all terminal output; keep printing out of pipeline modules.

## Conventions

- `uv` for everything; `ruff` (line length 88; E/F/UP/B/I/N/ANN/S/ASYNC) and `ty`
  for types — both enforced in CI.
- `repo-release-tools` (`[tool.rrt]`) bumps versions; keep `pyproject.toml` and
  `src/mcp2mcpb/__init__.py` in lock-step.

## Gotchas

- **Tests are fully offline** (`tests/conftest.py`): HTTP via `respx`,
  subprocess/bundling via `monkeypatch`, files via `tmp_path`. Never add a test
  that hits the network or runs `uvx`/`npx`. `tests/factories.py` builds synthetic
  wheels/tarballs.
- `asyncio_mode = "auto"` — write `async def test_*`, no marker needed.
- **`typer` 0.26 vendors click** — there is no importable top-level `click`.
- `dist/` is gitignored and `*.mcpb` is gitignored globally — bundles are never
  committed.
- The `--help` probe launches the real server; it uses `stdin=DEVNULL` +
  kill-on-timeout (`prober.py`). Use `--no-probe` (tests: `probe=False`) when the
  recipe is already known.
