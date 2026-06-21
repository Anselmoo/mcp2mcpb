"""Translate GitHub Action wrapper inputs into the `python -m mcp2mcpb` argv.

Single source of truth for both the composite action (`action.yml`) and the
reusable workflow (`build-mcpb.yml`). Pure and offline so every branch —
local-vs-registry mode, version resolution, optional flags — is unit-tested
instead of living as untested YAML bash.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


def _get(env: Mapping[str, str], key: str) -> str:
    return (env.get(key) or "").strip()


def _resolve_registry_version(env: Mapping[str, str]) -> str:
    """Version for `--pin` in registry mode, or raise if unresolvable.

    Accepts an explicit `VERSION` input, or a `v<digit>` tag ref (e.g.
    `v1.2.0`). A branch ref like `main` is NOT a release version and raises —
    this replaces the old bash that silently emitted `--pin main`.
    """
    version = _get(env, "VERSION")
    if version:
        return version
    ref = _get(env, "GITHUB_REF_NAME")
    if ref[:1] == "v" and ref[1:2].isdigit():
        return ref[1:]
    raise ValueError(
        f"could not resolve a release version (ref {ref!r}); set 'version', or "
        "build from a local artifact with from-dist/artifact-name"
    )


def build_cli_args(env: Mapping[str, str]) -> list[str]:
    """Return the argv (after `python -m mcp2mcpb`) for one conversion."""
    args: list[str] = [
        _get(env, "PACKAGE"),
        "--registry",
        _get(env, "REGISTRY"),
        "--mode",
        _get(env, "MODE"),
        "--output",
        _get(env, "OUTPUT_DIR"),
    ]

    from_dist = _get(env, "FROM_DIST")
    if from_dist:
        args += ["--from-dist", from_dist]
    else:
        args += ["--pin", _resolve_registry_version(env)]

    runner = _get(env, "RUNNER")
    if runner:
        args += ["--runner", runner]
    entry_script = _get(env, "ENTRY_SCRIPT")
    if entry_script:
        args += ["--entry-script", entry_script]
    for extra in _get(env, "EXTRAS").split():
        args += ["--extra", extra]
    subcommand = _get(env, "SUBCOMMAND")
    if subcommand:
        args += ["--subcommand", subcommand]
    transport = _get(env, "TRANSPORT")
    if transport:
        args += ["--transport", transport]
    if _get(env, "NO_PROBE") == "true":
        args.append("--no-probe")

    return args


def main() -> None:
    """CLI entrypoint: print one arg per line, exit 1 on unresolvable version."""
    try:
        args = build_cli_args(os.environ)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("\n".join(args))


if __name__ == "__main__":
    main()
