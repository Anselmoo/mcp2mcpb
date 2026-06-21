"""Build a bundle from a locally-built artifact instead of a registry fetch.

Mirrors :func:`mcp2mcpb.fetcher.fetch` — returns ``(PackageMeta, Path)`` — but
reads everything from a local wheel / npm tarball, so an unreleased version (or
a PR build) can be bundled without contacting PyPI or npm. No network access.
"""

from __future__ import annotations

import enum
from pathlib import Path

from mcp2mcpb.exceptions import RegistryFetchError


class ArtifactKind(enum.Enum):
    """Which ecosystem a local artifact belongs to."""

    WHEEL = "wheel"
    NPM = "npm"


def resolve_artifact(path: Path) -> tuple[Path, ArtifactKind]:
    """Resolve a ``--from-dist`` value to one artifact file and its kind.

    ``path`` may be an artifact file or a directory. In a directory a wheel is
    preferred over a co-located sdist (``uv build`` emits both). Raises
    :class:`RegistryFetchError` for an sdist, a missing path, an empty
    directory, or multiple distinct wheels.
    """
    if not path.exists():
        raise RegistryFetchError(f"--from-dist path not found: {path}")

    if path.is_dir():
        wheels = sorted(path.glob("*.whl"))
        tgzs = sorted(path.glob("*.tgz"))
        if len(wheels) > 1 or len(tgzs) > 1 or (wheels and tgzs):
            raise RegistryFetchError(
                f"multiple artifacts in {path}; pass an explicit file"
            )
        if wheels:
            return wheels[0], ArtifactKind.WHEEL
        if tgzs:
            return tgzs[0], ArtifactKind.NPM
        raise RegistryFetchError(f"no build artifact (*.whl / *.tgz) found in {path}")

    name = path.name
    if name.endswith(".whl"):
        return path, ArtifactKind.WHEEL
    if name.endswith(".tgz"):
        return path, ArtifactKind.NPM
    if name.endswith(".tar.gz"):
        raise RegistryFetchError(
            f"{name} is a PyPI sdist; pass the wheel (*.whl), not the sdist"
        )
    raise RegistryFetchError(f"unrecognised artifact type: {name}")
