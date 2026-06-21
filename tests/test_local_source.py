from __future__ import annotations

import io
import json
import tarfile
import zipfile
from email import message_from_string
from pathlib import Path

import pytest
from factories import build_npm_tarball_bytes, build_wheel_bytes

from mcp2mcpb.exceptions import RegistryFetchError
from mcp2mcpb.local_source import (
    ArtifactKind,
    _homepage_and_repo,
    _meta_from_npm,
    _meta_from_wheel,
    _npm_author,
    _npm_node_engine,
    _npm_repo_url,
    _read_npm_readme,
    _read_wheel_metadata,
    inspect_local,
    resolve_artifact,
)
from mcp2mcpb.models import PackageSource, Registry, ServerType


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_resolve_explicit_wheel(tmp_path: Path) -> None:
    whl = _write(
        tmp_path / "pkg-1.0.0-py3-none-any.whl", build_wheel_bytes("pkg", "1.0.0")
    )
    artifact, kind = resolve_artifact(whl)
    assert artifact == whl
    assert kind is ArtifactKind.WHEEL


def test_resolve_explicit_npm_tgz(tmp_path: Path) -> None:
    tgz = _write(tmp_path / "pkg-1.0.0.tgz", build_npm_tarball_bytes("pkg", "1.0.0"))
    artifact, kind = resolve_artifact(tgz)
    assert artifact == tgz
    assert kind is ArtifactKind.NPM


def test_directory_prefers_wheel_over_sdist(tmp_path: Path) -> None:
    _write(tmp_path / "pkg-1.0.0-py3-none-any.whl", build_wheel_bytes("pkg", "1.0.0"))
    _write(tmp_path / "pkg-1.0.0.tar.gz", b"sdist-bytes")
    artifact, kind = resolve_artifact(tmp_path)
    assert artifact.name.endswith(".whl")
    assert kind is ArtifactKind.WHEEL


def test_explicit_sdist_is_rejected(tmp_path: Path) -> None:
    sdist = _write(tmp_path / "pkg-1.0.0.tar.gz", b"sdist-bytes")
    with pytest.raises(RegistryFetchError, match="pass the wheel"):
        resolve_artifact(sdist)


def test_empty_directory_errors(tmp_path: Path) -> None:
    with pytest.raises(RegistryFetchError, match="no build artifact"):
        resolve_artifact(tmp_path)


def test_multiple_wheels_error(tmp_path: Path) -> None:
    _write(tmp_path / "pkg-1.0.0-py3-none-any.whl", build_wheel_bytes("pkg", "1.0.0"))
    _write(
        tmp_path / "other-2.0.0-py3-none-any.whl", build_wheel_bytes("other", "2.0.0")
    )
    with pytest.raises(RegistryFetchError, match="multiple"):
        resolve_artifact(tmp_path)


def test_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(RegistryFetchError, match="not found"):
        resolve_artifact(tmp_path / "nope")


async def test_meta_from_npm_reads_package_json(tmp_path: Path) -> None:
    tgz = tmp_path / "srv-9.9.9.tgz"
    tgz.write_bytes(build_npm_tarball_bytes("srv", "9.9.9"))
    source = PackageSource(registry=Registry.NPM, name="srv", version="9.9.9")
    meta = await _meta_from_npm(tgz, source)
    assert meta.name == "srv"
    assert meta.version == "9.9.9"
    assert meta.server_type is ServerType.NODE
    assert meta.entry.command


async def test_meta_from_wheel_reads_metadata(tmp_path: Path) -> None:
    whl = tmp_path / "demo-pkg-2.3.0-py3-none-any.whl"
    whl.write_bytes(
        build_wheel_bytes(
            "demo-pkg",
            "2.3.0",
            console_scripts={"demo-pkg": "demo_pkg.__main__:main"},
        )
    )
    source = PackageSource(registry=Registry.PYPI, name="demo-pkg", version="2.3.0")
    meta = await _meta_from_wheel(whl, source)
    assert meta.name == "demo-pkg"
    assert meta.version == "2.3.0"
    assert meta.server_type is ServerType.PYTHON
    assert meta.entry.command  # entry point detected from the wheel


async def test_inspect_local_wheel_returns_meta_and_path(tmp_path: Path) -> None:
    whl = tmp_path / "demo-pkg-2.3.0-py3-none-any.whl"
    whl.write_bytes(
        build_wheel_bytes(
            "demo-pkg", "2.3.0", console_scripts={"demo-pkg": "demo_pkg.__main__:main"}
        )
    )
    source = PackageSource(registry=Registry.PYPI, name="demo-pkg", version=None)
    meta, archive = await inspect_local(whl, source)
    assert meta.version == "2.3.0"
    assert archive == whl


async def test_inspect_local_rejects_kind_registry_mismatch(tmp_path: Path) -> None:
    whl = tmp_path / "demo-pkg-2.3.0-py3-none-any.whl"
    whl.write_bytes(build_wheel_bytes("demo-pkg", "2.3.0"))
    source = PackageSource(registry=Registry.NPM, name="demo-pkg", version=None)
    with pytest.raises(RegistryFetchError, match="does not match"):
        await inspect_local(whl, source)


async def test_inspect_local_warns_on_pin_mismatch(tmp_path: Path, capsys) -> None:
    whl = tmp_path / "demo-pkg-2.3.0-py3-none-any.whl"
    whl.write_bytes(
        build_wheel_bytes(
            "demo-pkg", "2.3.0", console_scripts={"demo-pkg": "demo_pkg.__main__:main"}
        )
    )
    source = PackageSource(registry=Registry.PYPI, name="demo-pkg", version="1.0.0")
    meta, _ = await inspect_local(whl, source)
    assert meta.version == "2.3.0"  # artifact wins
    assert "1.0.0" in capsys.readouterr().out


def _build_npm_tarball_with_readme(
    name: str,
    version: str,
    readme_text: str,
) -> bytes:
    """Like ``build_npm_tarball_bytes`` but also adds a ``package/README.md`` member."""
    unscoped = name.rsplit("/", 1)[-1]
    package_json = {
        "name": name,
        "version": version,
        "description": f"{name} MCP server.",
        "bin": {unscoped: "./dist/index.js"},
        "main": "dist/index.js",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        pkg_data = json.dumps(package_json).encode("utf-8")
        pkg_info = tarfile.TarInfo("package/package.json")
        pkg_info.size = len(pkg_data)
        tf.addfile(pkg_info, io.BytesIO(pkg_data))

        index = b"console.log('mcp');\n"
        idx_info = tarfile.TarInfo("package/dist/index.js")
        idx_info.size = len(index)
        tf.addfile(idx_info, io.BytesIO(index))

        readme_data = readme_text.encode("utf-8")
        readme_info = tarfile.TarInfo("package/README.md")
        readme_info.size = len(readme_data)
        tf.addfile(readme_info, io.BytesIO(readme_data))
    return buf.getvalue()


async def test_meta_from_npm_reads_readme_env_vars(tmp_path: Path) -> None:
    """_meta_from_npm must read env vars from the tarball's README, not package.json."""
    readme = "Set MY_API_KEY to your API key before running the server."
    tgz = tmp_path / "srv-1.0.0.tgz"
    tgz.write_bytes(_build_npm_tarball_with_readme("srv", "1.0.0", readme))
    source = PackageSource(registry=Registry.NPM, name="srv", version="1.0.0")
    meta = await _meta_from_npm(tgz, source)
    assert "MY_API_KEY" in meta.detected_env_vars


async def test_inspect_local_npm_returns_meta_and_path(tmp_path: Path) -> None:
    """inspect_local dispatches to the npm branch and returns (meta, archive)."""
    tgz = tmp_path / "srv-3.0.0.tgz"
    tgz.write_bytes(build_npm_tarball_bytes("srv", "3.0.0"))
    source = PackageSource(registry=Registry.NPM, name="srv", version="3.0.0")
    meta, archive = await inspect_local(tgz, source)
    assert meta.server_type is ServerType.NODE
    assert meta.version == "3.0.0"
    assert archive == tgz


def test_directory_picks_npm_tgz_when_no_wheel(tmp_path: Path) -> None:
    tgz = _write(tmp_path / "pkg-1.0.0.tgz", build_npm_tarball_bytes("pkg", "1.0.0"))
    artifact, kind = resolve_artifact(tmp_path)
    assert artifact == tgz
    assert kind is ArtifactKind.NPM


def test_unrecognised_extension_errors(tmp_path: Path) -> None:
    p = _write(tmp_path / "pkg-1.0.0.zip", b"zip-bytes")
    with pytest.raises(RegistryFetchError, match="unrecognised artifact type"):
        resolve_artifact(p)


def test_read_wheel_metadata_missing_raises(tmp_path: Path) -> None:
    whl = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/__init__.py", "")
    whl.write_bytes(buf.getvalue())
    with pytest.raises(RegistryFetchError, match="no .dist-info/METADATA"):
        _read_wheel_metadata(whl)


def test_homepage_and_repo_from_project_url() -> None:
    msg = message_from_string(
        "Metadata-Version: 2.1\n"
        "Name: pkg\n"
        "Project-URL: Repository, https://github.com/example/pkg\n"
        "Project-URL: Homepage, https://example.com\n"
    )
    homepage, repo = _homepage_and_repo(msg)
    assert homepage == "https://example.com"
    assert repo == "https://github.com/example/pkg"


def test_npm_author_dict_form() -> None:
    assert _npm_author({"author": {"name": "Alice"}}) == "Alice"
    assert _npm_author({"author": {}}) == "Unknown"


def test_npm_repo_url_dict_form() -> None:
    assert (
        _npm_repo_url({"repository": {"url": "https://github.com/x/y"}})
        == "https://github.com/x/y"
    )
    assert _npm_repo_url({"repository": {"type": "git"}}) is None


def test_npm_node_engine_dict_form() -> None:
    assert _npm_node_engine({"engines": {"node": ">=18"}}) == ">=18"
    assert _npm_node_engine({"engines": {}}) is None


def test_read_npm_readme_returns_empty_for_directory_member(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("package/README.md")
        info.type = tarfile.DIRTYPE
        tf.addfile(info)
    tgz = tmp_path / "test.tgz"
    tgz.write_bytes(buf.getvalue())
    assert _read_npm_readme(tgz) == ""
