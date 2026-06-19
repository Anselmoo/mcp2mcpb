"""Entry-point detection from downloaded package archives.

No network access — all I/O is from local files. CPU-bound archive parsing
is offloaded from the event loop with ``asyncio.to_thread()``.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

from mcp2mcpb.exceptions import EntryPointError
from mcp2mcpb.launch import parse_mcpb_table
from mcp2mcpb.models import EntryPoint, LaunchOverrides, PackageSource, Registry

# Tokens (uppercase, 4+ chars) containing any of these are treated as env vars.
_ENV_KEYWORDS = (
    "API",
    "KEY",
    "TOKEN",
    "SECRET",
    "PASS",
    "AUTH",
    "CRED",
    "BEARER",
    "WEBHOOK",
)
# Common CI / release / badge secrets that show up in READMEs but are never
# MCP-server runtime configuration — exclude them to avoid false positives.
_ENV_DENYLIST = frozenset(
    {
        "CODECOV_TOKEN",
        "COVERALLS_REPO_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "PYPI_TOKEN",
        "PYPI_API_TOKEN",
        "TWINE_PASSWORD",
        "TWINE_USERNAME",
        "NPM_TOKEN",
        "DOCKER_PASSWORD",
        "DOCKER_USERNAME",
        "CARGO_REGISTRY_TOKEN",
        "SONAR_TOKEN",
        "SNYK_TOKEN",
    }
)
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")


def _module_root(package_name: str) -> str:
    """Normalise a distribution name to an importable module root."""
    return package_name.replace("-", "_")


# ── PyPI wheel inspection ─────────────────────────────────────────────────────


def _detect_wheel_entry(archive: Path, package_name: str) -> EntryPoint:
    module_root = _module_root(package_name)
    callable_module: str | None = None

    with zipfile.ZipFile(archive) as zf:
        entry_points_member = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/entry_points.txt")),
            None,
        )
        if entry_points_member is not None:
            parser = configparser.ConfigParser()
            parser.read_string(zf.read(entry_points_member).decode("utf-8"))
            if parser.has_section("console_scripts"):
                scripts = dict(parser.items("console_scripts"))
                callable_module = _pick_console_script(
                    scripts, module_root, package_name
                )

    if callable_module is not None:
        # callable looks like 'pkg.module:func' → run the importable module root.
        top_level = callable_module.split(":", 1)[0].split(".", 1)[0]
        return EntryPoint(
            command="python",
            args=["-m", top_level],
            entry_file=f"server/{top_level}/__main__.py",
        )

    # Fallback: no console_scripts → assume a runnable package __main__.
    return EntryPoint(
        command="python",
        args=["-m", module_root],
        entry_file=f"server/{module_root}/__main__.py",
    )


def _pick_console_script(
    scripts: dict[str, str], module_root: str, package_name: str
) -> str:
    """Choose the most likely MCP console script callable.

    Prefer scripts whose name or callable references 'mcp' or the package's
    module root; otherwise take the first declared script.
    """
    needles = ("mcp", module_root, package_name)
    for name, callable_path in scripts.items():
        haystack = f"{name} {callable_path}".lower()
        if any(needle.lower() in haystack for needle in needles):
            return callable_path
    return next(iter(scripts.values()))


# ── npm tarball inspection ────────────────────────────────────────────────────


def _read_npm_package_json(archive: Path) -> dict[str, object]:
    """Extract and parse ``package/package.json`` from an npm ``.tgz`` tarball.

    Raises :exc:`EntryPointError` if the member is absent or unreadable.
    """
    with tarfile.open(archive, "r:gz") as tf:
        member = next(
            (m for m in tf.getmembers() if m.name == "package/package.json"),
            None,
        )
        if member is None:
            raise EntryPointError("npm tarball does not contain package/package.json")
        extracted = tf.extractfile(member)
        if extracted is None:
            raise EntryPointError("could not read package/package.json from tarball")
        return json.loads(extracted.read().decode("utf-8"))  # type: ignore[no-any-return]


def _detect_npm_entry(archive: Path) -> EntryPoint:
    package_json = _read_npm_package_json(archive)

    entry_rel = _resolve_npm_entry_file(package_json)
    # Normalise './dist/index.js' → 'dist/index.js' under the bundle's server/.
    # Use removeprefix (not lstrip, which strips a char set and would corrupt
    # paths whose first component starts with '.' such as './.bin/cli.js').
    entry_rel = entry_rel.removeprefix("./")
    entry_file = f"server/{entry_rel}"
    return EntryPoint(
        command="node",
        args=[f"${{__dirname}}/{entry_file}"],
        entry_file=entry_file,
    )


def _resolve_npm_entry_file(package_json: dict[str, object]) -> str:
    bin_field = package_json.get("bin")
    if isinstance(bin_field, str):
        return bin_field
    if isinstance(bin_field, dict) and bin_field:
        # Prefer the bin keyed by the (unscoped) package name; a multi-bin
        # package may expose several executables and the first need not be it.
        name = str(package_json.get("name", ""))
        unscoped = name.rsplit("/", 1)[-1]
        preferred = bin_field.get(unscoped)
        if isinstance(preferred, str):
            return preferred
        first_value = next(iter(bin_field.values()))
        if isinstance(first_value, str):
            return first_value
    main_field = package_json.get("main")
    if isinstance(main_field, str):
        return main_field
    return "index.js"


# ── Candidate scripts, declared extras, in-package config ────────────────────

_SERVER_HINTS = ("server", "mcp", "stdio")


def _is_server_ish(name: str, callable_path: str = "") -> bool:
    hay = f"{name} {callable_path}".lower()
    return any(h in hay for h in _SERVER_HINTS)


def _server_ish_sort_key(name: str, callable_path: str = "") -> tuple[int, int]:
    """Return a (primary, secondary) sort key; lower = higher priority.

    Primary 0: the **callable** (module path) explicitly contains any server hint
    (server/mcp/stdio) — i.e. the script's own module is a server module, not
    merely sharing a package name that has "server" in it.
    Primary 1: name or callable contains any server hint ("server"/"mcp"/"stdio").
    Primary 2: no server-ish hint at all.

    Secondary 0: script name's last hyphen-segment is "server" or "stdio"
    (e.g. ``mcp-server-example-server`` → "server"), making it the most
    explicit entry among equally-ranked primaries.
    Secondary 1: otherwise.
    """
    # Primary: check callable-only (strip module root that matches package name)
    # Use the portion after the first '.' to avoid the top-level package prefix.
    callable_lower = callable_path.lower()
    # Module part before ':' e.g. 'mcp_server_example.server' → 'server' suffix
    module_part = callable_lower.split(":", 1)[0]
    # Strip the top-level package component to get the sub-module part
    sub_module = module_part.split(".", 1)[1] if "." in module_part else module_part
    if any(h in sub_module for h in _SERVER_HINTS):
        primary = 0
    elif _is_server_ish(name, callable_path):
        primary = 1
    else:
        primary = 2

    # Secondary: explicit "-server" or "-stdio" suffix in the script name
    last_segment = name.rsplit("-", 1)[-1].lower() if "-" in name else name.lower()
    secondary = 0 if last_segment in ("server", "stdio") else 1
    return (primary, secondary)


def _wheel_console_scripts(archive: Path) -> dict[str, str]:
    with zipfile.ZipFile(archive) as zf:
        member = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/entry_points.txt")),
            None,
        )
        if member is None:
            return {}
        parser = configparser.ConfigParser()
        parser.read_string(zf.read(member).decode("utf-8"))
        if parser.has_section("console_scripts"):
            return dict(parser.items("console_scripts"))
    return {}


def candidate_scripts(archive: Path, source: PackageSource) -> list[str]:
    """Return console-script names (pypi) / bin names (npm), server-ish first.

    Scripts whose callable sub-module contains ``server`` or ``stdio`` come
    first, then others whose name/callable contains any server hint, then the
    rest.  A secondary tie-break promotes names that end with ``-server`` or
    ``-stdio``.
    """
    if source.registry is Registry.PYPI:
        scripts = _wheel_console_scripts(archive)
        all_names = list(scripts)
        all_names.sort(key=lambda n: _server_ish_sort_key(n, scripts[n]))
        return all_names
    # npm: bin names
    pkg = _read_npm_package_json(archive)
    bin_field = pkg.get("bin")
    if isinstance(bin_field, dict):
        names: list[str] = [str(k) for k in bin_field]
        names.sort(key=_server_ish_sort_key)
        return names
    return [source.name.rsplit("/", 1)[-1]]


def inpackage_overrides(archive: Path, source: PackageSource) -> LaunchOverrides:
    """Return in-package launch overrides, or an empty ``LaunchOverrides`` if absent.

    PyPI wheels: reads a ``mcpb.toml`` member anywhere in the zip.
    npm tarballs: reads the ``"mcpb"`` object from ``package/package.json``.
    """
    if source.registry is Registry.PYPI:
        with zipfile.ZipFile(archive) as zf:
            member = next(
                (
                    n
                    for n in zf.namelist()
                    if n.endswith("/mcpb.toml") or n == "mcpb.toml"
                ),
                None,
            )
            if member is None:
                return LaunchOverrides()
            return parse_mcpb_table(tomllib.loads(zf.read(member).decode("utf-8")))
    pkg = _read_npm_package_json(archive)
    mcpb = pkg.get("mcpb")
    if isinstance(mcpb, dict):
        return parse_mcpb_table({str(k): v for k, v in mcpb.items()})
    return LaunchOverrides()


def declared_extras(archive: Path, source: PackageSource) -> list[str]:
    """Return the list of extras declared in the wheel METADATA.

    Always returns ``[]`` for npm packages.
    """
    if source.registry is not Registry.PYPI:
        return []
    with zipfile.ZipFile(archive) as zf:
        member = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None
        )
        if member is None:
            return []
        text = zf.read(member).decode("utf-8", errors="replace")
    return [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Provides-Extra:")
    ]


# ── Public async dispatch ─────────────────────────────────────────────────────


async def detect_entry_point(archive: Path, source: PackageSource) -> EntryPoint:
    """Detect the server entry point from a downloaded package archive."""
    match source.registry:
        case Registry.PYPI:
            return await asyncio.to_thread(_detect_wheel_entry, archive, source.name)
        case Registry.NPM:
            return await asyncio.to_thread(_detect_npm_entry, archive)


# ── README / description env var scanning ─────────────────────────────────────


def scan_readme_for_env_vars(text: str) -> list[str]:
    """Heuristically extract environment variable names from README text.

    Matches uppercase identifiers of 4+ chars containing any of:
    API, KEY, TOKEN, SECRET, PASS, AUTH, CRED, BEARER, WEBHOOK.

    Returns a sorted, deduplicated list.
    """
    found: set[str] = set()
    for token in _IDENTIFIER.findall(text or ""):
        if token in _ENV_DENYLIST:
            continue
        if any(keyword in token for keyword in _ENV_KEYWORDS):
            found.add(token)
    return sorted(found)
