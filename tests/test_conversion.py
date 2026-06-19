"""Offline end-to-end conversion tests for real MCP servers.

Each case drives the *real* pipeline (`_convert`) against synthetic archives +
registry payloads served by respx — no network, no `uvx`/`npx` subprocess (the
``--help`` probe is disabled and reference mode vendors nothing). We assert the
generated ``manifest.json`` launch recipe and the packed bundle structure.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import factories
import httpx
import pytest
import respx
from typer.testing import CliRunner

from mcp2mcpb.__main__ import _convert, app
from mcp2mcpb.models import (
    BundleMode,
    LaunchOverrides,
    PackageSource,
    Registry,
    Transport,
)
from mcp2mcpb.packer import mcpb_filename


@dataclass(frozen=True)
class Case:
    id: str
    name: str
    version: str
    source: PackageSource
    overrides: LaunchOverrides
    payload: dict[str, object]
    archive: bytes
    expected_command: str
    expected_args: list[str]
    expected_env: dict[str, str] = field(default_factory=dict)


def _pypi_case(
    name: str,
    version: str,
    *,
    overrides: LaunchOverrides,
    expected_args: list[str],
    console_scripts: dict[str, str],
    extras: list[str] | None = None,
    readme: str = "",
    expected_env: dict[str, str] | None = None,
) -> Case:
    return Case(
        id=name,
        name=name,
        version=version,
        source=PackageSource(registry=Registry.PYPI, name=name, version=version),
        overrides=overrides,
        payload=factories.pypi_payload(name, version, readme=readme),
        archive=factories.build_wheel_bytes(
            name, version, console_scripts=console_scripts, extras=extras
        ),
        expected_command="uv",
        expected_args=expected_args,
        expected_env=expected_env or {},
    )


def _npm_case(
    name: str,
    version: str,
    *,
    readme: str = "",
    expected_env: dict[str, str] | None = None,
) -> Case:
    return Case(
        id=name,
        name=name,
        version=version,
        source=PackageSource(registry=Registry.NPM, name=name, version=version),
        overrides=LaunchOverrides(),
        payload=factories.npm_payload(name, version, readme=readme),
        archive=factories.build_npm_tarball_bytes(name, version),
        expected_command="npx",
        expected_args=["-y", f"{name}@{version}"],
        expected_env=expected_env or {},
    )


CASES: list[Case] = [
    # ── Own projects (PyPI) ──────────────────────────────────────────────────
    _pypi_case(
        "mcp-zen-of-languages",
        "1.2.0",
        overrides=LaunchOverrides(entry_script="zen-mcp"),
        console_scripts={"zen-mcp": "mcp_zen_of_languages.server:main"},
        expected_args=[
            "tool",
            "run",
            "--from",
            "mcp-zen-of-languages==1.2.0",
            "zen-mcp",
        ],
    ),
    _pypi_case(
        "repo-release-tools",
        "1.9.0",
        overrides=LaunchOverrides(
            extras=["mcp"], entry_script="rrt-mcp", transport=Transport.STDIO
        ),
        console_scripts={"rrt-mcp": "repo_release_tools.mcp:main"},
        extras=["mcp"],
        expected_args=[
            "tool",
            "run",
            "--from",
            "repo-release-tools[mcp]==1.9.0",
            "rrt-mcp",
            "--transport",
            "stdio",
        ],
    ),
    # ── Famous OSS (PyPI) — test-only, not committed ─────────────────────────
    _pypi_case(
        "serena-agent",
        "1.5.3",
        overrides=LaunchOverrides(
            entry_script="serena", subcommand=["start-mcp-server"]
        ),
        console_scripts={"serena": "serena.cli:main"},
        expected_args=[
            "tool",
            "run",
            "--from",
            "serena-agent==1.5.3",
            "serena",
            "start-mcp-server",
        ],
    ),
    # ── Famous OSS (npm) — test-only, not committed ──────────────────────────
    _npm_case(
        "@upstash/context7-mcp",
        "1.0.6",
        readme="Set CONTEXT7_API_KEY to raise your rate limit.",
        expected_env={"CONTEXT7_API_KEY": "${user_config.context7_api_key}"},
    ),
    _npm_case("@modelcontextprotocol/server-memory", "2025.4.24"),
]


def _mock_registry(case: Case) -> None:
    """Register respx routes for the case's registry metadata + archive."""
    name, version = case.name, case.version
    if case.source.registry is Registry.PYPI:
        respx.get(f"https://pypi.org/pypi/{name}/json").mock(
            return_value=httpx.Response(200, json=case.payload)
        )
        respx.get(factories.wheel_url(name, version)).mock(
            return_value=httpx.Response(200, content=case.archive)
        )
    else:
        respx.get(f"https://registry.npmjs.org/{name}/{version}").mock(
            return_value=httpx.Response(200, json=case.payload)
        )
        respx.get(factories.npm_tarball_url(name, version)).mock(
            return_value=httpx.Response(200, content=case.archive)
        )


@respx.mock
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_conversion_roundtrip(case: Case, tmp_path: Path, monkeypatch) -> None:
    # cwd-relative sidecar lookup must be deterministic (no [tool.mcpb] here).
    monkeypatch.chdir(tmp_path)
    _mock_registry(case)

    await _convert(
        case.source,
        tmp_path,
        BundleMode.REFERENCE,
        case.overrides,
        probe=False,
    )

    filename = mcpb_filename(case.name, case.version, BundleMode.REFERENCE)
    bundle = tmp_path / filename
    assert bundle.exists(), f"expected {filename} in {list(tmp_path.iterdir())}"

    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        assert names[0] == "manifest.json"
        manifest = json.loads(zf.read("manifest.json"))

    mcp = manifest["server"]["mcp_config"]
    assert mcp["command"] == case.expected_command
    assert mcp["args"] == case.expected_args
    assert mcp["env"] == case.expected_env


@respx.mock
def test_config_file_drives_version_and_recipe(tmp_path: Path, monkeypatch) -> None:
    """End-to-end through `convert()`: a `.mcpb.toml` supplies the version pin and
    the launch recipe, so only the package name is passed on the CLI."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcpb.toml").write_text(
        'version = "1.5.3"\n'
        'entry-script = "serena"\n'
        'subcommand = ["start-mcp-server"]\n',
        encoding="utf-8",
    )
    name, version = "serena-agent", "1.5.3"
    respx.get(f"https://pypi.org/pypi/{name}/json").mock(
        return_value=httpx.Response(200, json=factories.pypi_payload(name, version))
    )
    respx.get(factories.wheel_url(name, version)).mock(
        return_value=httpx.Response(
            200,
            content=factories.build_wheel_bytes(
                name, version, console_scripts={"serena": "serena.cli:main"}
            ),
        )
    )

    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "convert",
            name,
            "-r",
            "pypi",
            "-m",
            "reference",
            "--no-probe",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output

    bundle = out / mcpb_filename(name, version, BundleMode.REFERENCE)
    with zipfile.ZipFile(bundle) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    # version came from the config; entry-script + subcommand too.
    assert manifest["server"]["mcp_config"]["args"] == [
        "tool",
        "run",
        "--from",
        "serena-agent==1.5.3",
        "serena",
        "start-mcp-server",
    ]
