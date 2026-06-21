"""Vendor a package and its dependencies into a bundle's ``server/`` dir.

In ``reference`` mode this is a no-op — the manifest calls uvx/npx at
runtime, so nothing is vendored. In ``complete`` mode all dependencies are
installed locally so the end user needs no Python/Node toolchain.
"""

from __future__ import annotations

import asyncio
import shutil
import tarfile
from pathlib import Path

from mcp2mcpb import ui
from mcp2mcpb.exceptions import BundleError
from mcp2mcpb.licensing import extract_license_files
from mcp2mcpb.models import (
    BundleMode,
    LaunchSpec,
    PackageMeta,
    PackageSource,
    ServerType,
)

# Probed once and cached: prefer 'uv pip' (fast) when uv is on PATH.
_UV_AVAILABLE: bool | None = None


async def _uv_available() -> bool:
    """Return True if the ``uv`` binary is callable, caching the result."""
    global _UV_AVAILABLE
    if _UV_AVAILABLE is None:
        if shutil.which("uv") is None:
            _UV_AVAILABLE = False
        else:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "uv",
                    "--version",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()
                _UV_AVAILABLE = proc.returncode == 0
            except OSError:
                _UV_AVAILABLE = False
    return _UV_AVAILABLE


async def _run(*cmd: str) -> None:
    """Run a subprocess, raising BundleError with stderr on failure."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise BundleError(
            f"command failed ({' '.join(cmd)}): {detail or 'unknown error'}"
        )


def _python_install_spec(
    source: PackageSource, launch: LaunchSpec, local_wheel: Path | None = None
) -> str:
    """Return the pip install specifier, including extras when set.

    With ``local_wheel`` set (``--from-dist``), install the local wheel file
    directly so an unreleased version never has to be fetched from PyPI.
    """
    extra = f"[{','.join(launch.extras)}]" if launch.extras else ""
    if local_wheel is not None:
        return f"{local_wheel}{extra}"
    pin = f"=={source.version}" if source.version else ""
    return f"{source.name}{extra}{pin}"


async def _bundle_python(pinned: str, server_dir: Path) -> None:
    """Install the package + all deps into a flat target directory."""
    await asyncio.to_thread(server_dir.mkdir, parents=True, exist_ok=True)
    target = str(server_dir)
    cmd: tuple[str, ...]
    if await _uv_available():
        cmd = (
            "uv",
            "pip",
            "install",
            "--target",
            target,
            "--no-compile",
            "--quiet",
            pinned,
        )
    else:
        cmd = (
            "python",
            "-m",
            "pip",
            "install",
            "--target",
            target,
            "--no-compile",
            "--quiet",
            pinned,
        )
    await _run(*cmd)


def _extract_npm_tarball(archive: Path, server_dir: Path) -> None:
    """Extract an npm tarball, stripping the leading 'package/' prefix."""
    server_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.name.startswith("package/"):
                continue
            relative = member.name[len("package/") :]
            if not relative:
                continue
            extracted = tf.extractfile(member)
            dest = server_dir / relative
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if extracted is not None:
                dest.write_bytes(extracted.read())


async def _bundle_node(archive: Path, server_dir: Path) -> None:
    """Extract the npm tarball, then install production dependencies."""
    await asyncio.to_thread(_extract_npm_tarball, archive, server_dir)
    if (server_dir / "package-lock.json").exists():
        await _run("npm", "ci", "--omit=dev", "--prefix", str(server_dir))
    else:
        await _run("npm", "install", "--omit=dev", "--prefix", str(server_dir))


def _write_license_files(dest: Path, files: dict[str, bytes]) -> None:
    """Write extracted license/notice files to the bundle root."""
    for name, content in files.items():
        (dest / name).write_bytes(content)


async def _ship_licenses(source: PackageSource, dest: Path, archive: Path) -> None:
    """Copy the upstream LICENSE/NOTICE into the bundle root (complete mode).

    Required because a complete bundle redistributes the package's code.
    """
    licenses = await asyncio.to_thread(extract_license_files, archive, source)
    if licenses:
        await asyncio.to_thread(_write_license_files, dest, licenses)
    else:
        ui.warning(
            f"no LICENSE file found in {source.name}; the complete bundle "
            "redistributes code without an upstream license — verify the "
            "package's licensing terms"
        )


async def bundle(
    source: PackageSource,
    meta: PackageMeta,
    dest: Path,
    mode: BundleMode,
    archive: Path,
    launch: LaunchSpec,
    *,
    local_wheel: Path | None = None,
) -> None:
    """Vendor dependencies into ``dest/server/`` for ``complete`` bundles."""
    if mode == BundleMode.REFERENCE:
        return

    server_dir = dest / "server"
    match meta.server_type:
        case ServerType.PYTHON:
            await _bundle_python(
                _python_install_spec(source, launch, local_wheel), server_dir
            )
        case ServerType.NODE:
            await _bundle_node(archive, server_dir)
        case ServerType.BINARY:
            raise BundleError(
                "binary server bundling is not supported in complete mode"
            )
    await _ship_licenses(source, dest, archive)
