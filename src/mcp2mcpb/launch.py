"""Resolve a LaunchSpec from a precedence chain of partial overrides."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mcp2mcpb import ui
from mcp2mcpb.exceptions import ConfigError
from mcp2mcpb.models import (
    BundleMode,
    LaunchOverrides,
    LaunchSpec,
    Registry,
    Runner,
    Transport,
)

_DEFAULT_RUNNER = {
    (Registry.PYPI, BundleMode.REFERENCE): Runner.UVX,
    (Registry.NPM, BundleMode.REFERENCE): Runner.NPX,
    (Registry.PYPI, BundleMode.COMPLETE): Runner.PYTHON,
    (Registry.NPM, BundleMode.COMPLETE): Runner.NODE,
}


def default_launch(registry: Registry, mode: BundleMode) -> LaunchSpec:
    """The lowest-precedence launch recipe, derived from registry + mode."""
    return LaunchSpec(
        runner=_DEFAULT_RUNNER[(registry, mode)],
        transport=Transport.AUTO,
    )


def resolve_launch(
    registry: Registry,
    mode: BundleMode,
    layers: list[LaunchOverrides],
) -> LaunchSpec:
    """Merge override layers (highest precedence first) onto the default."""
    base = default_launch(registry, mode)
    fields = (
        "runner",
        "entry_script",
        "extras",
        "subcommand",
        "transport",
        "from_spec",
    )
    resolved: dict[str, Any] = {}
    for field in fields:
        value = next(
            (v for layer in layers if (v := getattr(layer, field)) is not None),
            getattr(base, field),
        )
        resolved[field] = value
    return LaunchSpec(**resolved)


def auto_extra_layer(
    declared_extras: list[str],
    layers: list[LaunchOverrides],
) -> LaunchOverrides | None:
    """Infer the ``mcp`` extra when a package declares it and nothing else set extras.

    Many MCP servers gate their server entry point behind an ``mcp`` extra
    (e.g. ``tanabesugano[mcp]``, ``repo-release-tools[mcp]``). When the package
    declares such an extra and no higher-precedence layer specified extras,
    return a lowest-precedence override that selects it. Returns ``None`` when
    the inference does not apply, so the caller can decide whether to warn.
    """
    if "mcp" in declared_extras and not any(layer.extras for layer in layers):
        return LaunchOverrides(extras=["mcp"])
    return None


_KEY_MAP = {
    "runner": "runner",
    "entry-script": "entry_script",
    "extras": "extras",
    "subcommand": "subcommand",
    "transport": "transport",
    "from": "from_spec",
}

# Keys that are valid in [tool.mcpb] but resolved outside the launch recipe
# (e.g. ``version`` is package identity, read early by ``load_sidecar_pin``).
_HANDLED_ELSEWHERE = frozenset({"version"})


def parse_mcpb_table(table: dict[str, object]) -> LaunchOverrides:
    """Convert a parsed [tool.mcpb] table into LaunchOverrides."""
    data: dict[str, Any] = {}
    for key, value in table.items():
        if key in _HANDLED_ELSEWHERE:
            continue
        field = _KEY_MAP.get(key)
        if field is None:
            ui.warning(f"ignoring unknown [tool.mcpb] key: {key!r}")
            continue
        data[field] = value
    try:
        return LaunchOverrides(**data)
    except ValidationError as exc:
        msg = exc.errors()[0]["msg"]
        raise ConfigError(f"invalid [tool.mcpb] config: {msg}") from exc


def _read_mcpb_table(cwd: Path) -> dict[str, object] | None:
    """Return the [tool.mcpb] table from .mcpb.toml or pyproject in cwd, or None."""
    dotfile = cwd / ".mcpb.toml"
    if dotfile.is_file():
        return tomllib.loads(dotfile.read_text(encoding="utf-8"))
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        tool = parsed.get("tool", {})
        if isinstance(tool, dict) and isinstance(tool.get("mcpb"), dict):
            return tool["mcpb"]
    return None


def load_sidecar(cwd: Path) -> LaunchOverrides:
    """Read launch overrides from .mcpb.toml or pyproject [tool.mcpb] in cwd."""
    table = _read_mcpb_table(cwd)
    return parse_mcpb_table(table) if table is not None else LaunchOverrides()


def load_sidecar_pin(cwd: Path) -> str | None:
    """Read the ``version`` pin from .mcpb.toml / pyproject [tool.mcpb] in cwd.

    Resolved separately from the launch recipe because the version is package
    identity needed *before* the package is fetched. ``"latest"`` (any case) and
    a missing key both mean "unpinned" → ``None``.
    """
    table = _read_mcpb_table(cwd)
    if table is None:
        return None
    version = table.get("version")
    if not isinstance(version, str) or version.strip().lower() == "latest":
        return None
    return version.strip()
