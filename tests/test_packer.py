"""ZIP output tests for the packer."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mcp2mcpb.exceptions import PackError
from mcp2mcpb.models import (
    BundleMode,
    Manifest,
    ManifestAuthor,
    McpConfig,
    ServerConfig,
    ServerType,
)
from mcp2mcpb.packer import pack_mcpb


def _manifest() -> Manifest:
    return Manifest(
        name="mcp-server-example",
        version="1.0.0",
        description="An example.",
        author=ManifestAuthor(name="Example Author"),
        server=ServerConfig(
            type=ServerType.PYTHON,
            entry_point="server/mcp_server_example/__main__.py",
            mcp_config=McpConfig(command="python", args=["-m", "mcp_server_example"]),
        ),
    )


def _bundle_dir(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    server = bundle / "server"
    server.mkdir(parents=True)
    (server / "module.py").write_text("print('hi')\n", encoding="utf-8")
    return bundle


def test_pack_creates_mcpb_file(tmp_path: Path):
    out = pack_mcpb(
        _bundle_dir(tmp_path), _manifest(), tmp_path / "dist", BundleMode.COMPLETE
    )
    assert out.suffix == ".mcpb"
    assert out.exists()


def test_pack_output_is_valid_zip_with_manifest(tmp_path: Path):
    out = pack_mcpb(
        _bundle_dir(tmp_path), _manifest(), tmp_path / "dist", BundleMode.COMPLETE
    )
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert names[0] == "manifest.json"  # written first
        data = json.loads(zf.read("manifest.json"))
        assert data["manifest_version"] == "0.4"


def test_complete_filename_has_platform_tag(tmp_path: Path):
    out = pack_mcpb(
        _bundle_dir(tmp_path), _manifest(), tmp_path / "dist", BundleMode.COMPLETE
    )
    # one of the normalised os tags must appear in the filename
    assert any(tag in out.name for tag in ("linux", "macos", "windows"))


def test_reference_filename_is_universal(tmp_path: Path):
    out = pack_mcpb(
        _bundle_dir(tmp_path), _manifest(), tmp_path / "dist", BundleMode.REFERENCE
    )
    assert "universal" in out.name


def test_scoped_npm_name_is_filename_safe(tmp_path: Path):
    manifest = _manifest().model_copy(update={"name": "@scope/server-github"})
    out = pack_mcpb(
        _bundle_dir(tmp_path), manifest, tmp_path / "dist", BundleMode.REFERENCE
    )
    assert "@" not in out.name
    assert "/" not in out.name
    assert out.name.startswith("scope-server-github-")


def test_pack_raises_on_unwritable_output(tmp_path: Path):
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        with pytest.raises(PackError):
            pack_mcpb(
                _bundle_dir(tmp_path),
                _manifest(),
                readonly / "dist",
                BundleMode.COMPLETE,
            )
    finally:
        readonly.chmod(0o700)
