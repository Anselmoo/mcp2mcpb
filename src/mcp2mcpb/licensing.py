"""Extract license/notice files from a downloaded package archive.

Complete-mode bundles redistribute the package (and its dependencies), so the
upstream license must travel with the bundle — mirroring conda-forge's
``license_file``. This module pulls LICENSE/NOTICE/COPYING files out of the
fetched wheel (PyPI) or tarball (npm). Dependency licenses already ship inside
the vendored ``*.dist-info/`` directories, so only the top-level package's own
license needs to be lifted out here.

Reference bundles redistribute no upstream code, so they carry only the SPDX
``license`` field in the manifest — this module is not used for them.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from mcp2mcpb.models import PackageSource, Registry

# Filename stems (case-insensitive) that denote a license/notice document.
_LICENSE_STEMS = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT")


def _is_license_name(name: str) -> bool:
    """True if ``name``'s basename looks like a license/notice file."""
    base = name.rsplit("/", 1)[-1]
    return base.upper().startswith(_LICENSE_STEMS)


def _from_wheel(archive: Path) -> dict[str, bytes]:
    """Lift license files from a wheel's ``*.dist-info/`` (incl. PEP 639 licenses/)."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if name.endswith("/") or ".dist-info/" not in name:
                continue
            if _is_license_name(name):
                out[name.rsplit("/", 1)[-1]] = zf.read(name)
    return out


def _from_npm_tarball(archive: Path) -> dict[str, bytes]:
    """Lift top-level license files from an npm tarball (``package/`` prefix)."""
    out: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile() or not member.name.startswith("package/"):
                continue
            relative = member.name[len("package/") :]
            if "/" in relative or not _is_license_name(relative):
                continue
            extracted = tf.extractfile(member)
            if extracted is not None:
                out[relative] = extracted.read()
    return out


def extract_license_files(archive: Path, source: PackageSource) -> dict[str, bytes]:
    """Return ``{filename: bytes}`` of license/notice files found in the archive.

    Empty when none are present; callers decide whether to warn.
    """
    if source.registry is Registry.PYPI:
        return _from_wheel(archive)
    return _from_npm_tarball(archive)
