# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `action.yml` description shortened to meet GitHub Marketplace 125-character limit.
- `mcp2mcpb-src` default install spec bumped to `mcp2mcpb>=0.2`.

## [0.2.0] - 2026-06-19

### Added
- Initial release: convert published PyPI/npm MCP servers into one-click `.mcpb`
  bundles for Claude Desktop and any MCPB-aware client.
- Typer CLI (`mcp2mcpb <package>`, plus `python -m mcp2mcpb`; `--version`
  prints the tool version) with `complete` (vendor deps) and `reference`
  (uvx/npx) bundle modes.
- Async `httpx` fetchers for PyPI and npm; entry-point detection from wheel
  `dist-info/entry_points.txt` and npm `package.json` (`bin`/`main`).
- Pydantic v2 manifest generation per MCPB spec v0.4, including heuristic
  README env-var detection → `user_config` fields (with a CI/badge-token
  denylist and optional-by-default fields).
- Rich manifests: `display_name`, `repository`, `keywords`, and a
  `compatibility` block (platforms + derived runtime constraint), populated
  from registry metadata. SPDX `license` resolved from PEP 639
  `license-expression`, the legacy field, or trove classifiers.
- Auto-includes a package's `mcp` extra when none is specified (e.g.
  `tanabesugano[mcp]`), with an override notice.
- Complete-mode bundles ship the upstream `LICENSE`/`NOTICE` at the bundle root
  (redistribution requirement); reference bundles keep the manifest SPDX field.
- Complete-mode Python bundles inject `PYTHONPATH=${__dirname}/server` so
  vendored dependencies are importable at runtime.
- `mcp2mcpb sandbox <bundle|manifest|dir>` command — simulate Claude Desktop
  launching the server under stdio: runs the MCP `initialize` handshake, sends
  `notifications/initialized`, queries `tools/list`, and verifies clean EOF
  shutdown. Supports `--env KEY=VALUE`, `--timeout`, and `--verbose`; with no
  path it auto-detects a single bundle in `dist/` or a `manifest.json` in the
  current directory.
- MIT `LICENSE` file at the repository root.
- `mcp2mcpb-src` input on the GitHub Action to override the install source
  (defaults to `mcp2mcpb>=0.1`).
- `poe unpack <bundle.mcpb>` dev task to extract a bundle and print its manifest.
- Platform-aware packer producing `{name}-{version}-{os}-{arch}.mcpb`
  (complete) or `{name}-{version}-universal.mcpb` (reference).
- Reusable GitHub Action (`action.yml`) and upstream-polling workflows
  (`check-upstream.yml`, `build-upstream.yml`).
- `ty` type checking, `ruff` lint/format, and a fully offline `pytest` suite
  with `respx` mocks enforcing **100% line coverage** (`fail_under = 100`).

### CI/CD
- Self-contained `cicd.yml`: lint + types → test matrix (3.12–3.14) → build →
  SBOM → build-provenance attestation → TestPyPI → PyPI (trusted publishing) →
  GitHub release.
- Local release tasks via `poe` (`poe build`, `poe check`, `poe dist`).

[Unreleased]: https://github.com/Anselmoo/mcp2mcpb/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Anselmoo/mcp2mcpb/releases/tag/v0.2.0
