"""License-file extraction tests."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from mcp2mcpb.licensing import extract_license_files
from mcp2mcpb.models import PackageSource, Registry


def _wheel(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def _tarball(path: Path, members: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _pypi() -> PackageSource:
    return PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0")


def _npm() -> PackageSource:
    return PackageSource(registry=Registry.NPM, name="pkg", version="1.0.0")


def test_wheel_license_in_distinfo_root(tmp_path: Path):
    path = _wheel(
        tmp_path / "pkg-1.0.0-py3-none-any.whl",
        {
            "pkg-1.0.0.dist-info/LICENSE": "MIT text",
            "pkg-1.0.0.dist-info/METADATA": "Name: pkg\n",
            "pkg/__init__.py": "",
        },
    )
    found = extract_license_files(path, _pypi())
    assert found == {"LICENSE": b"MIT text"}


def test_wheel_pep639_licenses_subdir(tmp_path: Path):
    path = _wheel(
        tmp_path / "pkg-1.0.0-py3-none-any.whl",
        {
            "pkg-1.0.0.dist-info/licenses/LICENSE.txt": "Apache",
            "pkg-1.0.0.dist-info/licenses/NOTICE": "notice",
            "pkg-1.0.0.dist-info/RECORD": "",
        },
    )
    found = extract_license_files(path, _pypi())
    assert found == {"LICENSE.txt": b"Apache", "NOTICE": b"notice"}


def test_wheel_no_license_returns_empty(tmp_path: Path):
    path = _wheel(
        tmp_path / "pkg-1.0.0-py3-none-any.whl",
        {"pkg-1.0.0.dist-info/METADATA": "Name: pkg\n", "pkg/__init__.py": ""},
    )
    assert extract_license_files(path, _pypi()) == {}


def test_npm_top_level_license(tmp_path: Path):
    path = _tarball(
        tmp_path / "pkg-1.0.0.tgz",
        {
            "package/LICENSE": "MIT",
            "package/package.json": "{}",
            "package/src/LICENSE": "nested — ignored",
        },
    )
    found = extract_license_files(path, _npm())
    assert found == {"LICENSE": b"MIT"}
