# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-06-23
### Added
- `scripts/build_assets.py` pipeline: cairosvg SVG→PNG for logo/hero/social-preview,
  Pillow favicon.ico + PNG size variants; exposed via `poe svg-to-png`, `poe favicon`,
  and `poe build-assets` tasks.
- Version badge (top-right pill) in `assets/hero.svg` kept in sync with `rrt bump`.
- `repo-release-tools` version targets for `hero.svg` and `social-preview.svg` so both
  SVG version badges update alongside `pyproject.toml` and `__init__.py` on every bump.
- `generated_assets` hook re-runs `poe build-assets` after each bump so PNG badges
  always reflect the new version.
- `artifact_targets` fingerprinting for all five generated outputs (logo.png, hero.png,
  social-preview.png, favicon.ico, apple-touch-icon.png) in `.rrt/artifacts.lock.toml`.

### Fixed
- `cairosvg.svg2png()` return value explicitly captured and written with
  `dst.write_bytes()` — the previous `write_to=str(dst)` was unreliable across versions.
- `social-preview.png` wordmark centering bug: `text-anchor="middle"` + `<tspan>`
  breaks in cairosvg/Pango (one font per run, no per-glyph adjustment); replaced with
  two overlaid text elements at measured Menlo glyph positions.
- `social-preview.png` and `hero.png` tagline arrow glyph: Helvetica Neue lacks U+2192;
  Lucida Grande (always present on macOS, verified via fontconfig) added first in the
  `font-family` fallback chain so cairosvg renders `→` instead of `.notdef`.

## [0.5.0] - 2026-06-21
### Added
- `--from-dist PATH` CLI flag: build a `.mcpb` from a locally-built wheel / npm
  `.tgz` (or a directory containing one) instead of fetching from the registry.
  `--registry` keeps selecting only the runtime target (uvx/npx); the flag
  implies `--no-probe` and reads the version from the artifact, so unreleased
  versions and PR builds work.
- `from-dist` input on the composite action and `artifact-name` input on the
  reusable `build-mcpb.yml` workflow, exposing the above to release pipelines.

### Changed
- The action wrappers now build their `python -m mcp2mcpb` argv via the new,
  unit-tested `mcp2mcpb._ci_args` module (one shared code path for both
  wrappers) instead of duplicated YAML bash.
- Registry-mode version resolution is stricter: it requires an explicit
  `version` or a `v*` tag ref and fails clearly on a branch ref, instead of
  silently emitting `--pin <branch-name>`.
- Default install floors bumped to `mcp2mcpb>=0.5` (action `mcp2mcpb-src`) and
  `>=0.5` (workflow `mcp2mcpb-version`).

## [0.4.0] - 2026-06-21
### Added
- `.github/dependabot.yml` (pip + github-actions, weekly) so the SHA-pin
  documentation's Dependabot claim is actually practiced by this repo.
- `.pre-commit-config.yaml` (ruff, ruff-format, ty via `uv run`, plus standard
  file-hygiene hooks); the CI `lint` job now runs `pre-commit run --all-files`.
- Governance scaffolding: CONTRIBUTING, SECURITY (private vuln reporting),
  CODE_OF_CONDUCT (Contributor Covenant 2.1), CODEOWNERS, issue templates
  (bug/feature + config), and a pull-request template.
- Project branding with a refined isometric `.mcpb`-bundle emblem on a corporate
  slate/teal palette (gradients + depth): `assets/logo.svg`, `assets/hero.svg`
  (README banner), and `assets/social-preview.svg` (GitHub social card, 1280×640).

### Changed
- The `github-release` CI job now auto-appends an immutable commit-SHA pin block
  to every GitHub release body (derived from the tagged commit).
- README badges are now dynamic (live PyPI version, supported Python versions,
  CI status) instead of a hardcoded `v0.2.0` badge.
- Maturity classifier bumped `3 - Alpha` → `4 - Beta` to reflect the project's
  stability while staying honestly short of a 1.0/Production claim.

## [0.3.0] - 2026-06-20
### Added
- `--latest` flag: the launch command re-resolves the newest published version on every
  start — npm/uvx use `pkg@latest`; uvx `--from` recipes use `--refresh-package` (since
  `@latest` is not a valid PEP 508 `--from` spec). Conflicts with `--pin`.

### Changed
- Reference-mode bundles now invoke `uv tool run --no-build …` so extensions never
  compile dependencies at launch — a missing/arch-mismatched wheel fails fast with a
  clear "no compatible wheel" error instead of a native build failure.
- Scoped npm package names are flattened in the manifest `name`/`display_name` and the
  bundle filename (`@modelcontextprotocol/server-sequential-thinking` →
  `server-sequential-thinking`) so Claude Desktop shows a clean, untruncated title.

### Documentation
- README: troubleshooting note for Apple-Silicon machines where an Intel Homebrew `uv`
  shadows the arm64 `uv` and triggers cross-compile failures (e.g. `cryptography` /
  `openssl-sys`).

## [0.2.1] - 2026-06-20
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
