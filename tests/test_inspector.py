"""Entry-point detection and env-var scanning tests (real temp files)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from mcp2mcpb.inspector import (
    candidate_scripts,
    declared_extras,
    detect_entry_point,
    inpackage_overrides,
    scan_readme_for_env_vars,
)
from mcp2mcpb.models import LaunchOverrides, PackageSource, Registry


def _make_npm_tarball(path: Path, package_json: dict) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        data = json.dumps(package_json).encode("utf-8")
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return path


async def test_detect_wheel_entry_with_console_script(fake_wheel_path):
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    entry = await detect_entry_point(fake_wheel_path, source)
    assert entry.command == "python"
    assert "mcp_server_example" in entry.args
    assert entry.entry_file.endswith("__main__.py")


async def test_detect_wheel_entry_fallback(fake_wheel_no_entrypoints_path):
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    entry = await detect_entry_point(fake_wheel_no_entrypoints_path, source)
    assert entry.command == "python"
    assert entry.args == ["-m", "mcp_server_example"]


async def test_detect_npm_entry(fake_npm_tarball_path):
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    entry = await detect_entry_point(fake_npm_tarball_path, source)
    assert entry.command == "node"
    assert "dist/index.js" in entry.entry_file
    assert entry.entry_file.startswith("server/")


def test_scan_readme_for_env_vars():
    text = (
        "Set MY_API_KEY and BEARER_TOKEN before starting. The URL and ID are optional."
    )
    found = scan_readme_for_env_vars(text)
    assert "MY_API_KEY" in found
    assert "BEARER_TOKEN" in found
    assert "URL" not in found
    assert "ID" not in found
    assert found == sorted(found)


async def test_npm_bin_dict_prefers_package_name(tmp_path: Path):
    # Multi-bin package: the bin keyed by the (unscoped) name must win,
    # not merely the first dict entry.
    tarball = _make_npm_tarball(
        tmp_path / "pkg.tgz",
        {
            "name": "@scope/server-github",
            "version": "1.0.0",
            "bin": {
                "helper": "./dist/helper.js",
                "server-github": "./dist/main.js",
            },
        },
    )
    source = PackageSource(registry=Registry.NPM, name="@scope/server-github")
    entry = await detect_entry_point(tarball, source)
    assert entry.entry_file == "server/dist/main.js"


async def test_npm_dotfile_entry_not_corrupted(tmp_path: Path):
    # Regression: lstrip('./') would have turned './.bin/cli.js' into
    # 'bin/cli.js', dropping the leading '.' of the directory component.
    tarball = _make_npm_tarball(
        tmp_path / "pkg.tgz",
        {"name": "dotpkg", "version": "1.0.0", "bin": "./.bin/cli.js"},
    )
    source = PackageSource(registry=Registry.NPM, name="dotpkg")
    entry = await detect_entry_point(tarball, source)
    assert entry.entry_file == "server/.bin/cli.js"


def test_scan_readme_excludes_ci_badge_tokens():
    # Regression: a real package README mentioned CODECOV_TOKEN (a CI badge
    # secret), which must not surface as MCP-server runtime config.
    text = "Coverage uploads use CODECOV_TOKEN and GITHUB_TOKEN. Set OPENAI_API_KEY."
    found = scan_readme_for_env_vars(text)
    assert "CODECOV_TOKEN" not in found
    assert "GITHUB_TOKEN" not in found
    assert "OPENAI_API_KEY" in found


def test_scan_readme_handles_empty():
    assert scan_readme_for_env_vars("") == []


def test_candidate_scripts_orders_server_first(fake_wheel_multi_script_path):
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    cands = candidate_scripts(fake_wheel_multi_script_path, source)
    assert cands[0] == "mcp-server-example-server"  # server-ish first
    assert "mcp-server-example" in cands


def test_inpackage_overrides_from_wheel_mcpb_toml(fake_wheel_with_mcpb):
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    ov = inpackage_overrides(fake_wheel_with_mcpb, source)
    assert ov.entry_script == "mcp-server-example-server"


def test_inpackage_overrides_npm_mcpb_key(fake_npm_tarball_with_mcpb):
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    ov = inpackage_overrides(fake_npm_tarball_with_mcpb, source)
    assert ov.subcommand == ["serve"]


def test_inpackage_overrides_absent_is_empty(fake_wheel_path):
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    assert inpackage_overrides(fake_wheel_path, source) == LaunchOverrides()


def test_declared_extras_from_wheel(fake_wheel_multi_script_path):
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    extras = declared_extras(fake_wheel_multi_script_path, source)
    assert extras == ["mcp"]


def test_declared_extras_npm_is_empty(fake_npm_tarball_path):
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    assert declared_extras(fake_npm_tarball_path, source) == []


def test_candidate_scripts_npm_bin_dict(fake_npm_tarball_path):
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    cands = candidate_scripts(fake_npm_tarball_path, source)
    assert "mcp-serve" in cands
