from __future__ import annotations

from pathlib import Path

import pytest
from factories import build_npm_tarball_bytes, build_wheel_bytes

from mcp2mcpb.exceptions import RegistryFetchError
from mcp2mcpb.local_source import (
    ArtifactKind,
    _meta_from_npm,
    _meta_from_wheel,
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
