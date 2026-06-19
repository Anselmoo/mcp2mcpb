"""Targeted tests closing the remaining coverage gaps in the small modules.

Covers branches in fetcher, inspector, prober, licensing, ui, and generator
that the behavioural suites don't otherwise reach. All offline.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from mcp2mcpb import ui
from mcp2mcpb.exceptions import EntryPointError, RegistryFetchError
from mcp2mcpb.fetcher import (
    _pypi_repo_url,
    _select_wheel_url,
    fetch_npm,
    fetch_pypi,
)
from mcp2mcpb.generator import render_mcp_config
from mcp2mcpb.inspector import (
    _read_npm_package_json,
    _resolve_npm_entry_file,
    candidate_scripts,
    declared_extras,
)
from mcp2mcpb.licensing import extract_license_files
from mcp2mcpb.models import (
    BundleMode,
    EntryPoint,
    LaunchSpec,
    PackageMeta,
    PackageSource,
    Registry,
    Runner,
    ServerType,
)
from mcp2mcpb.prober import _help_command, _run_help

# ── fetcher ───────────────────────────────────────────────────────────────────


def test_pypi_repo_url_homepage_in_project_urls() -> None:
    info = {"project_urls": {"Homepage": "https://h.example/x.git"}}
    assert _pypi_repo_url(info) == "https://h.example/x"


def test_select_wheel_url_raises_without_wheel() -> None:
    files: list[dict[str, object]] = [{"filename": "pkg-1.0.0.tar.gz", "url": "u"}]
    with pytest.raises(RegistryFetchError):
        _select_wheel_url(files)


def test_select_wheel_url_falls_back_to_first_wheel() -> None:
    files: list[dict[str, object]] = [
        {"filename": "pkg-1.0.0-cp312-cp312-linux.whl", "url": "first"}
    ]
    assert _select_wheel_url(files) == "first"


@respx.mock
async def test_pypi_version_not_found_raises() -> None:
    import factories

    name = "pkg"
    respx.get(f"https://pypi.org/pypi/{name}/json").mock(
        return_value=httpx.Response(200, json=factories.pypi_payload(name, "1.0.0"))
    )
    source = PackageSource(registry=Registry.PYPI, name=name, version="9.9.9")
    with pytest.raises(RegistryFetchError, match="not found"):
        await fetch_pypi(source)


@respx.mock
async def test_pypi_request_error_raises() -> None:
    respx.get("https://pypi.org/pypi/boom/json").mock(
        side_effect=httpx.ConnectError("no route")
    )
    source = PackageSource(registry=Registry.PYPI, name="boom")
    with pytest.raises(RegistryFetchError, match="request failed"):
        await fetch_pypi(source)


@respx.mock
async def test_npm_timeout_raises() -> None:
    respx.get("https://registry.npmjs.org/slow/latest").mock(
        side_effect=httpx.TimeoutException("slow")
    )
    source = PackageSource(registry=Registry.NPM, name="slow")
    with pytest.raises(RegistryFetchError, match="timed out"):
        await fetch_npm(source)


@respx.mock
async def test_npm_request_error_raises() -> None:
    respx.get("https://registry.npmjs.org/boom/latest").mock(
        side_effect=httpx.ConnectError("no route")
    )
    source = PackageSource(registry=Registry.NPM, name="boom")
    with pytest.raises(RegistryFetchError, match="request failed"):
        await fetch_npm(source)


# ── inspector ───────────────────────────────────────────────────────────────


def _npm_tarball(path: Path, members: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_read_npm_package_json_missing_raises(tmp_path: Path) -> None:
    tarball = _npm_tarball(tmp_path / "p.tgz", {"package/other.txt": "x"})
    with pytest.raises(EntryPointError, match="does not contain"):
        _read_npm_package_json(tarball)


def test_read_npm_package_json_directory_member_raises(tmp_path: Path) -> None:
    # A non-regular member (directory) named package/package.json makes
    # extractfile() return None, exercising the "could not read" guard.
    path = tmp_path / "p.tgz"
    info = tarfile.TarInfo("package/package.json")
    info.type = tarfile.DIRTYPE
    with tarfile.open(path, "w:gz") as tf:
        tf.addfile(info)
    with pytest.raises(EntryPointError, match="could not read"):
        _read_npm_package_json(path)


def test_resolve_npm_entry_file_main_then_default() -> None:
    assert _resolve_npm_entry_file({"main": "lib/m.js"}) == "lib/m.js"
    assert _resolve_npm_entry_file({}) == "index.js"


def test_wheel_no_entry_points_yields_no_candidates(
    fake_wheel_no_entrypoints_path: Path,
) -> None:
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    assert candidate_scripts(fake_wheel_no_entrypoints_path, source) == []


def test_wheel_entry_points_without_console_scripts(tmp_path: Path) -> None:
    path = tmp_path / "w.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "pkg-1.0.0.dist-info/entry_points.txt", "[gui_scripts]\nx = pkg:main\n"
        )
    source = PackageSource(registry=Registry.PYPI, name="pkg")
    assert candidate_scripts(path, source) == []


def test_candidate_scripts_npm_string_bin_uses_unscoped_name(tmp_path: Path) -> None:
    tarball = _npm_tarball(
        tmp_path / "p.tgz",
        {"package/package.json": json.dumps({"name": "@s/pkg", "bin": "./x.js"})},
    )
    source = PackageSource(registry=Registry.NPM, name="@s/pkg")
    assert candidate_scripts(tarball, source) == ["pkg"]


def test_declared_extras_without_metadata_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "w.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pkg/__init__.py", "")
    source = PackageSource(registry=Registry.PYPI, name="pkg")
    assert declared_extras(path, source) == []


# ── prober ────────────────────────────────────────────────────────────────────


async def test_run_help_returns_code_and_output() -> None:
    code, text = await _run_help([sys.executable, "-c", "print('usage: ok')"])
    assert code == 0
    assert "usage" in text


def test_help_command_npm_form() -> None:
    source = PackageSource(registry=Registry.NPM, name="pkg", version="1.0.0")
    assert _help_command(source, "pkg", []) == ["npx", "-y", "pkg@1.0.0", "--help"]


# ── licensing ─────────────────────────────────────────────────────────────────


def test_npm_license_skips_dirs_and_nested(tmp_path: Path) -> None:
    path = tmp_path / "p.tgz"
    with tarfile.open(path, "w:gz") as tf:
        tf.addfile(tarfile.TarInfo("pax_global_header"))  # not a file/package → skip
        dir_info = tarfile.TarInfo("package/sub")
        dir_info.type = tarfile.DIRTYPE
        tf.addfile(dir_info)  # directory member → skipped
        for name, content in {
            "package/LICENSE": "MIT",
            "package/src/LICENSE": "nested ignored",
        }.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    source = PackageSource(registry=Registry.NPM, name="pkg")
    out = extract_license_files(path, source)
    assert out == {"LICENSE": b"MIT"}


# ── ui ────────────────────────────────────────────────────────────────────────


def test_section_empty_title_prints_rule(capsys: pytest.CaptureFixture[str]) -> None:
    ui.section("")
    captured = capsys.readouterr().out
    assert captured.strip() != ""


# ── generator ─────────────────────────────────────────────────────────────────


def test_render_uv_run_with_extras() -> None:
    source = PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0")
    meta = PackageMeta(
        name="pkg",
        version="1.0.0",
        description="d",
        author="a",
        server_type=ServerType.PYTHON,
        entry=EntryPoint(command="python", args=["-m", "pkg"], entry_file="x"),
    )
    launch = LaunchSpec(
        runner=Runner.UV_RUN, extras=["dep-a", "dep-b"], entry_script="s"
    )
    cfg, server_type, _ = render_mcp_config(
        launch, source, meta, BundleMode.COMPLETE, {}
    )
    assert cfg.command == "uv"
    assert "--with" in cfg.args
    assert cfg.args.count("--with") == 2
    assert server_type is ServerType.UV
