"""Coverage for the remaining __main__ CLI branches.

Convert error handling, verbose/auto-extra/no-from notices, complete-mode
bundling, the sandbox command's path resolution, and unpack edge cases. All
offline: registries are mocked with respx and the heavy stages (bundling,
sandbox subprocess) are monkeypatched.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import factories
import httpx
import pytest
import respx
from typer.testing import CliRunner

from mcp2mcpb import __main__ as cli
from mcp2mcpb.__main__ import _convert, _sidecar_source, app, main
from mcp2mcpb.models import BundleMode, LaunchOverrides, PackageSource, Registry

runner = CliRunner()


def _mock_pypi(name: str, version: str, **payload_kwargs: Any) -> None:
    respx.get(f"https://pypi.org/pypi/{name}/json").mock(
        return_value=httpx.Response(
            200, json=factories.pypi_payload(name, version, **payload_kwargs)
        )
    )
    respx.get(factories.wheel_url(name, version)).mock(
        return_value=httpx.Response(
            200,
            content=factories.build_wheel_bytes(
                name,
                version,
                console_scripts={f"{name}-server": f"{name.replace('-', '_')}.s:main"},
                extras=payload_kwargs.pop("extras", None),
            ),
        )
    )


# ── convert: error + notice branches ──────────────────────────────────────────


@respx.mock
def test_convert_reports_conversion_error() -> None:
    respx.get("https://pypi.org/pypi/missing/json").mock(
        return_value=httpx.Response(404)
    )
    result = runner.invoke(app, ["convert", "missing", "--no-probe"])
    assert result.exit_code == 1


def test_convert_latest_and_pin_conflict() -> None:
    result = runner.invoke(
        app, ["convert", "pkg", "--latest", "--pin", "1.0.0", "--no-probe"]
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


@respx.mock
async def test_convert_auto_includes_mcp_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    name, version = "pkg", "1.0.0"
    respx.get(f"https://pypi.org/pypi/{name}/json").mock(
        return_value=httpx.Response(200, json=factories.pypi_payload(name, version))
    )
    respx.get(factories.wheel_url(name, version)).mock(
        return_value=httpx.Response(
            200,
            content=factories.build_wheel_bytes(
                name, version, console_scripts={"pkg": "pkg.s:main"}, extras=["mcp"]
            ),
        )
    )
    await _convert(
        PackageSource(registry=Registry.PYPI, name=name, version=version),
        tmp_path,
        BundleMode.REFERENCE,
        LaunchOverrides(),
        probe=False,
    )
    assert "auto-including" in capsys.readouterr().out


@respx.mock
async def test_convert_no_from_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _mock_pypi("pkg", "1.0.0")
    await _convert(
        PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0"),
        tmp_path,
        BundleMode.REFERENCE,
        LaunchOverrides(entry_script="other-script", from_spec=False),
        probe=False,
    )
    assert "ignores extras" in capsys.readouterr().out


@respx.mock
async def test_convert_verbose_reports_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcpb.toml").write_text(
        'entry-script = "pkg-server"\n', encoding="utf-8"
    )
    _mock_pypi("pkg", "1.0.0")
    await _convert(
        PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0"),
        tmp_path,
        BundleMode.REFERENCE,
        LaunchOverrides(),
        probe=False,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert ".mcpb.toml" in out
    assert "Resolved launch recipe" in out


@respx.mock
async def test_convert_complete_mode_vendors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _mock_pypi("pkg", "1.0.0")

    async def fake_bundle(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(cli, "bundle", fake_bundle)
    await _convert(
        PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0"),
        tmp_path,
        BundleMode.COMPLETE,
        LaunchOverrides(),
        probe=False,
    )
    assert "Bundling dependencies" in capsys.readouterr().out


# ── _sidecar_source ───────────────────────────────────────────────────────────


def test_sidecar_source_dotfile(tmp_path: Path) -> None:
    (tmp_path / ".mcpb.toml").write_text("", encoding="utf-8")
    assert _sidecar_source(tmp_path) == ".mcpb.toml"


def test_sidecar_source_pyproject_tool_mcpb(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcpb]\nentry-script = "x"\n', encoding="utf-8"
    )
    assert _sidecar_source(tmp_path) == "pyproject.toml [tool.mcpb]"


def test_sidecar_source_none(tmp_path: Path) -> None:
    assert _sidecar_source(tmp_path) is None


def test_sidecar_source_pyproject_without_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert _sidecar_source(tmp_path) is None


# ── unpack edge cases ─────────────────────────────────────────────────────────


def test_unpack_bad_zip_errors(tmp_path: Path) -> None:
    bad = tmp_path / "broken.mcpb"
    bad.write_text("not a zip", encoding="utf-8")
    result = runner.invoke(app, ["unpack", str(bad)])
    assert result.exit_code == 1


def test_unpack_without_manifest_warns(tmp_path: Path) -> None:
    bundle = tmp_path / "demo.mcpb"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("readme.txt", "hi")
    result = runner.invoke(app, ["unpack", str(bundle)])
    assert result.exit_code == 0
    assert "no manifest.json" in result.output


# ── sandbox command path resolution ───────────────────────────────────────────


def _bundle(path: Path, *, with_manifest: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        if with_manifest:
            zf.writestr("manifest.json", json.dumps({"server": {"mcp_config": {}}}))
        else:
            zf.writestr("readme.txt", "x")
    return path


@pytest.fixture
def _stub_run_sandbox(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace run_sandbox with a stub; returns a list so tests can set the code."""
    code_box = [0]

    async def fake_run_sandbox(**kwargs: Any) -> int:
        return code_box[0]

    monkeypatch.setattr(cli, "run_sandbox", fake_run_sandbox)
    return code_box


def test_sandbox_explicit_bundle(tmp_path: Path, _stub_run_sandbox: list[int]) -> None:
    bundle = _bundle(tmp_path / "demo.mcpb")
    result = runner.invoke(app, ["sandbox", str(bundle)])
    assert result.exit_code == 0


def test_sandbox_bundle_nonzero_exit(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    _stub_run_sandbox[0] = 3
    bundle = _bundle(tmp_path / "demo.mcpb")
    result = runner.invoke(app, ["sandbox", str(bundle)])
    assert result.exit_code == 3


def test_sandbox_bundle_without_manifest_errors(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    bundle = _bundle(tmp_path / "demo.mcpb", with_manifest=False)
    result = runner.invoke(app, ["sandbox", str(bundle)])
    assert result.exit_code == 1


def test_sandbox_manifest_file(tmp_path: Path, _stub_run_sandbox: list[int]) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"server": {"mcp_config": {}}}), encoding="utf-8")
    result = runner.invoke(app, ["sandbox", str(manifest)])
    assert result.exit_code == 0


def test_sandbox_directory(tmp_path: Path, _stub_run_sandbox: list[int]) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"server": {"mcp_config": {}}}), encoding="utf-8"
    )
    result = runner.invoke(app, ["sandbox", str(tmp_path)])
    assert result.exit_code == 0


def test_sandbox_directory_without_manifest_errors(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    result = runner.invoke(app, ["sandbox", str(tmp_path)])
    assert result.exit_code == 1


def test_sandbox_invalid_target(tmp_path: Path, _stub_run_sandbox: list[int]) -> None:
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    result = runner.invoke(app, ["sandbox", str(plain)])
    assert result.exit_code == 1


def test_sandbox_autodetect_single_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_run_sandbox: list[int]
) -> None:
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    _bundle(dist / "only.mcpb")
    result = runner.invoke(app, ["sandbox"])
    assert result.exit_code == 0
    assert "Auto-detected bundle" in result.output


def test_sandbox_autodetect_multiple_bundles_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_run_sandbox: list[int]
) -> None:
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    _bundle(dist / "a.mcpb")
    _bundle(dist / "b.mcpb")
    result = runner.invoke(app, ["sandbox"])
    assert result.exit_code == 1
    assert "Multiple bundles" in result.output


def test_sandbox_autodetect_manifest_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_run_sandbox: list[int]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"server": {"mcp_config": {}}}), encoding="utf-8"
    )
    result = runner.invoke(app, ["sandbox"])
    assert result.exit_code == 0
    assert "Auto-detected manifest.json" in result.output


def test_sandbox_autodetect_nothing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_run_sandbox: list[int]
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["sandbox"])
    assert result.exit_code == 1
    assert "No .mcpb bundle" in result.output


def test_sandbox_bundle_unzip_failure(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    fake = tmp_path / "broken.mcpb"  # .mcpb suffix but not a real zip
    fake.write_text("not a zip", encoding="utf-8")
    result = runner.invoke(app, ["sandbox", str(fake)])
    assert result.exit_code == 1


def test_sandbox_bundle_bad_manifest_json(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    bundle = tmp_path / "demo.mcpb"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("manifest.json", "{not json")
    result = runner.invoke(app, ["sandbox", str(bundle)])
    assert result.exit_code == 1


def test_sandbox_manifest_file_bad_json(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["sandbox", str(manifest)])
    assert result.exit_code == 1


def test_sandbox_manifest_file_nonzero_exit(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    _stub_run_sandbox[0] = 4
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"server": {"mcp_config": {}}}), encoding="utf-8")
    result = runner.invoke(app, ["sandbox", str(manifest)])
    assert result.exit_code == 4


def test_sandbox_directory_bad_manifest_json(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["sandbox", str(tmp_path)])
    assert result.exit_code == 1


def test_sandbox_directory_nonzero_exit(
    tmp_path: Path, _stub_run_sandbox: list[int]
) -> None:
    _stub_run_sandbox[0] = 5
    (tmp_path / "manifest.json").write_text(
        json.dumps({"server": {"mcp_config": {}}}), encoding="utf-8"
    )
    result = runner.invoke(app, ["sandbox", str(tmp_path)])
    assert result.exit_code == 5


# ── main() console entry point ────────────────────────────────────────────────


def test_main_entry_point_version() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
