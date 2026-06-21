"""Build a bundle from a locally-built artifact instead of a registry fetch.

Mirrors :func:`mcp2mcpb.fetcher.fetch` — returns ``(PackageMeta, Path)`` — but
reads everything from a local wheel / npm tarball, so an unreleased version (or
a PR build) can be bundled without contacting PyPI or npm. No network access.
"""

from __future__ import annotations

import enum
import tarfile
import zipfile
from email import message_from_string
from email.message import Message
from pathlib import Path

from mcp2mcpb import inspector, ui
from mcp2mcpb.exceptions import RegistryFetchError
from mcp2mcpb.models import PackageMeta, PackageSource, Registry, ServerType


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


def _read_wheel_metadata(archive: Path) -> Message:
    """Return the parsed RFC822 METADATA message from a wheel."""
    with zipfile.ZipFile(archive) as zf:
        member_name = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")),
            None,
        )
        if member_name is None:
            raise RegistryFetchError(f"no .dist-info/METADATA in wheel {archive.name}")
        return message_from_string(
            zf.read(member_name).decode("utf-8", errors="replace")
        )


def _homepage_and_repo(msg: Message) -> tuple[str | None, str | None]:
    """Extract homepage + repository URL from Project-URL / Home-page headers."""
    homepage = msg.get("Home-page") or None
    repo: str | None = None
    for raw in msg.get_all("Project-URL") or []:
        label, _, url = raw.partition(",")
        key = label.strip().lower()
        url = url.strip()
        if key in {"homepage", "home"} and not homepage:
            homepage = url
        if key in {"repository", "source", "source code"} and not repo:
            repo = url
    return homepage, repo


def _npm_author(pkg: dict[str, object]) -> str:
    author = pkg.get("author")
    if isinstance(author, dict):
        return str(author.get("name") or "Unknown")
    return str(author or "Unknown")


def _npm_repo_url(pkg: dict[str, object]) -> str | None:
    repo = pkg.get("repository")
    if isinstance(repo, dict):
        url = repo.get("url")
        return str(url) if url else None
    return str(repo) if repo else None


def _npm_node_engine(pkg: dict[str, object]) -> str | None:
    engines = pkg.get("engines")
    if isinstance(engines, dict):
        node = engines.get("node")
        if node:
            return str(node)
    return None


def _read_npm_readme(archive: Path) -> str:
    """Return the text of the tarball's ``package/README*`` file, or ``''`` if none."""
    with tarfile.open(archive, "r:gz") as tf:
        member = next(
            (m for m in tf.getmembers() if m.name.lower().startswith("package/readme")),
            None,
        )
        if member is None:
            return ""
        extracted = tf.extractfile(member)
        if extracted is None:
            return ""
        return extracted.read().decode("utf-8", errors="replace")


async def _meta_from_npm(archive: Path, source: PackageSource) -> PackageMeta:
    """Build PackageMeta from a local npm tarball's package.json — no network."""
    pkg = inspector._read_npm_package_json(archive)
    version = str(pkg.get("version") or source.version or "")
    pinned = source.model_copy(update={"version": version})
    entry = await inspector.detect_entry_point(archive, pinned)
    keywords_raw = pkg.get("keywords")
    keywords = [str(k) for k in keywords_raw] if isinstance(keywords_raw, list) else []
    return PackageMeta(
        name=str(pkg.get("name") or source.name),
        version=version,
        description=str(pkg.get("description") or ""),
        author=_npm_author(pkg),
        homepage=(str(pkg["homepage"]) if pkg.get("homepage") else None),
        license_id=(str(pkg["license"]) if pkg.get("license") else None),
        server_type=ServerType.NODE,
        entry=entry,
        detected_env_vars=inspector.scan_readme_for_env_vars(_read_npm_readme(archive)),
        keywords=keywords,
        repository_url=_npm_repo_url(pkg),
        node_engine=_npm_node_engine(pkg),
    )


async def _meta_from_wheel(archive: Path, source: PackageSource) -> PackageMeta:
    """Build PackageMeta from a wheel's METADATA — no network."""
    msg = _read_wheel_metadata(archive)
    version = str(msg.get("Version") or source.version or "")
    pinned = source.model_copy(update={"version": version})
    entry = await inspector.detect_entry_point(archive, pinned)
    payload = msg.get_payload()
    readme = payload if isinstance(payload, str) else ""
    keywords_raw = str(msg.get("Keywords") or "")
    keywords = [k.strip() for k in keywords_raw.replace(",", " ").split() if k.strip()]
    homepage, repo = _homepage_and_repo(msg)
    return PackageMeta(
        name=str(msg.get("Name") or source.name),
        version=version,
        description=str(msg.get("Summary") or ""),
        author=str(msg.get("Author") or msg.get("Author-email") or "Unknown"),
        homepage=homepage,
        license_id=(msg.get("License-Expression") or msg.get("License") or None),
        server_type=ServerType.PYTHON,
        entry=entry,
        detected_env_vars=inspector.scan_readme_for_env_vars(readme),
        keywords=keywords,
        repository_url=repo,
        requires_python=msg.get("Requires-Python") or None,
    )


_KIND_REGISTRY: dict[ArtifactKind, Registry] = {
    ArtifactKind.WHEEL: Registry.PYPI,
    ArtifactKind.NPM: Registry.NPM,
}


async def inspect_local(path: Path, source: PackageSource) -> tuple[PackageMeta, Path]:
    """Return ``(PackageMeta, archive)`` from a local artifact — no network.

    Drop-in replacement for :func:`mcp2mcpb.fetcher.fetch` when a locally-built
    wheel / npm tarball is supplied via ``--from-dist``.
    """
    archive, kind = resolve_artifact(path)
    expected = _KIND_REGISTRY[kind]
    if expected is not source.registry:
        raise RegistryFetchError(
            f"artifact {archive.name} is a {kind.value} package, which does not "
            f"match --registry {source.registry.value}"
        )
    if kind is ArtifactKind.WHEEL:
        meta = await _meta_from_wheel(archive, source)
    else:
        meta = await _meta_from_npm(archive, source)
    if source.version and source.version != meta.version:
        ui.warning(
            f"--pin {source.version} ignored; using the artifact's version "
            f"{meta.version}"
        )
    return meta, archive
