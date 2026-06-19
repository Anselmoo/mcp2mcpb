# tests/test_cli.py
import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from mcp2mcpb.__main__ import _cli_overrides, _normalize_pin, _route, app
from mcp2mcpb.models import Runner, Transport


def test_normalize_pin():
    assert _normalize_pin(None) is None
    assert _normalize_pin("latest") is None
    assert _normalize_pin("LATEST") is None
    assert _normalize_pin(" 1.9.0 ") == "1.9.0"


def test_cli_overrides_builds_partial():
    ov = _cli_overrides(
        runner=Runner.UVX,
        entry_script="srv",
        extra=["mcp"],
        subcommand="start-mcp-server",
        transport=Transport.STDIO,
    )
    assert ov.entry_script == "srv"
    assert ov.extras == ["mcp"]
    assert ov.subcommand == ["start-mcp-server"]
    assert ov.runner == Runner.UVX
    assert ov.transport == Transport.STDIO


def test_cli_overrides_empty_when_unset():
    ov = _cli_overrides(
        runner=None, entry_script=None, extra=[], subcommand=None, transport=None
    )
    assert ov.runner is None
    assert ov.extras is None
    assert ov.subcommand is None


def test_version_flag():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "mcp2mcpb" in result.stdout


def test_route_injects_convert_for_bare_package():
    assert _route(["mcp-server-fetch", "--registry", "pypi"]) == [
        "convert",
        "mcp-server-fetch",
        "--registry",
        "pypi",
    ]


def test_route_leaves_subcommands_and_options_untouched():
    assert _route(["unpack", "x.mcpb"]) == ["unpack", "x.mcpb"]
    assert _route(["convert", "pkg"]) == ["convert", "pkg"]
    assert _route(["--version"]) == ["--version"]
    assert _route([]) == []


def test_unpack_extracts_and_prints_manifest(tmp_path: Path):
    bundle = tmp_path / "demo-1.0.0-universal.mcpb"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"name": "demo", "version": "1.0.0"}))

    result = CliRunner().invoke(app, ["unpack", str(bundle)])

    assert result.exit_code == 0
    extracted = tmp_path / "demo-1.0.0-universal"
    assert (extracted / "manifest.json").is_file()
    assert '"name": "demo"' in result.stdout


def test_unpack_missing_file_errors():
    result = CliRunner().invoke(app, ["unpack", "/no/such/bundle.mcpb"])
    assert result.exit_code == 1
