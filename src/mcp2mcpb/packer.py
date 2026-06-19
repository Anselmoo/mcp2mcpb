"""Pack a prepared bundle directory into a ``.mcpb`` ZIP archive."""

from __future__ import annotations

import platform
import re
import zipfile
from pathlib import Path

from mcp2mcpb.exceptions import PackError
from mcp2mcpb.models import BundleMode, Manifest

_OS_MAP = {"darwin": "macos", "windows": "windows", "linux": "linux"}
_ARCH_MAP = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def _platform_tag() -> str:
    """Return a normalised '{os}-{arch}' tag for the current platform."""
    os_name = _OS_MAP.get(platform.system().lower(), platform.system().lower())
    arch = _ARCH_MAP.get(platform.machine().lower(), platform.machine().lower())
    return f"{os_name}-{arch}"


def _safe_name(name: str) -> str:
    """Sanitise a package name into a filesystem-safe filename component."""
    cleaned = name.lstrip("@").replace("/", "-")
    return re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)


def mcpb_filename(name: str, version: str, mode: BundleMode) -> str:
    """Return the ``.mcpb`` filename for a bundle.

    Reference bundles are platform-agnostic ('universal'); complete bundles are
    tagged with the current platform because their vendored wheels may be
    platform-specific.
    """
    suffix = "universal" if mode == BundleMode.REFERENCE else _platform_tag()
    return f"{_safe_name(name)}-{version}-{suffix}.mcpb"


def pack_mcpb(
    bundle_dir: Path,
    manifest: Manifest,
    output_dir: Path,
    mode: BundleMode,
) -> Path:
    """Write manifest.json into ``bundle_dir`` and ZIP it into a ``.mcpb`` file.

    Returns the absolute path to the created archive. Raises :class:`PackError`
    if any filesystem or ZIP operation fails.
    """
    filename = mcpb_filename(manifest.name, manifest.version, mode)

    try:
        manifest_path = bundle_dir / "manifest.json"
        manifest.write_to(manifest_path)

        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = (output_dir / filename).resolve()

        with zipfile.ZipFile(
            out_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zf:
            # manifest.json is always the first entry for reproducibility.
            zf.write(manifest_path, "manifest.json")
            for path in sorted(bundle_dir.rglob("*")):
                if path == manifest_path or not path.is_file():
                    continue
                zf.write(path, path.relative_to(bundle_dir).as_posix())
    except OSError as exc:
        raise PackError(f"failed to write .mcpb archive: {exc}") from exc

    return out_path
