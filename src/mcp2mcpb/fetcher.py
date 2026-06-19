"""Async registry clients for PyPI and npm.

Each fetcher downloads the package source archive (wheel for PyPI, tgz for
npm), inspects it for an entry point, and returns normalised metadata plus
the path to the downloaded archive.

All network errors are converted into :class:`RegistryFetchError` with an
actionable message; unexpected errors propagate.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path

import httpx

from mcp2mcpb import inspector
from mcp2mcpb.exceptions import RegistryFetchError
from mcp2mcpb.models import (
    PackageMeta,
    PackageSource,
    Registry,
    ServerType,
)

_PYPI_BASE = "https://pypi.org/pypi"
_NPM_BASE = "https://registry.npmjs.org"
_TIMEOUT = httpx.Timeout(30.0)


async def _download(client: httpx.AsyncClient, url: str, dest_dir: Path) -> Path:
    """Download a URL into ``dest_dir`` and return the local file path."""
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    filename = url.rsplit("/", 1)[-1] or "download"
    target = dest_dir / filename
    target.write_bytes(resp.content)
    return target


def _split_keywords(raw: object) -> list[str]:
    """Normalise a PyPI 'keywords' string or an npm list into a clean list."""
    if isinstance(raw, list):
        items = [str(k) for k in raw]
    elif isinstance(raw, str):
        # PyPI keywords are comma-separated (older packages use spaces).
        items = raw.split(",") if "," in raw else raw.split()
    else:
        return []
    return [k.strip() for k in items if k.strip()]


def _clean_git_url(url: str) -> str:
    """Normalise a VCS URL to a plain https form for the manifest."""
    url = url.strip().removeprefix("git+").removesuffix(".git")
    if url.startswith("git://"):
        url = "https://" + url[len("git://") :]
    return url


_CLASSIFIER_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",  # noqa: E501
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


def _pypi_license(info: Mapping[str, object]) -> str | None:
    """Resolve an SPDX id, preferring PEP 639 over the legacy string + classifiers."""
    expr = info.get("license_expression")
    if expr:
        return str(expr)
    legacy = info.get("license")
    # Some packages dump the full license text into this field; keep only ids.
    if legacy and len(str(legacy)) < 40:
        return str(legacy)
    classifiers = info.get("classifiers")
    if isinstance(classifiers, list):
        for classifier in classifiers:
            spdx = _CLASSIFIER_SPDX.get(str(classifier))
            if spdx:
                return spdx
    return None


def _pypi_repo_url(info: Mapping[str, object]) -> str | None:
    """Pick the most repo-like URL from PyPI ``project_urls`` (case-insensitive)."""
    urls = info.get("project_urls")
    if isinstance(urls, dict):
        lowered = {str(k).lower(): str(v) for k, v in urls.items()}
        for key in ("repository", "source", "source code", "code", "github"):
            if key in lowered:
                return _clean_git_url(lowered[key])
        if "homepage" in lowered:
            return _clean_git_url(lowered["homepage"])
    home = info.get("home_page")
    return str(home) if home else None


def _npm_repo_url(data: Mapping[str, object]) -> str | None:
    """Extract the repository URL from npm metadata (object or string form)."""
    repo = data.get("repository")
    if isinstance(repo, dict) and repo.get("url"):
        return _clean_git_url(str(repo.get("url")))
    if isinstance(repo, str) and repo:
        return _clean_git_url(repo)
    home = data.get("homepage")
    return str(home) if home else None


def _npm_node_engine(data: Mapping[str, object]) -> str | None:
    """Return the ``engines.node`` constraint from npm metadata, if declared."""
    engines = data.get("engines")
    if isinstance(engines, dict) and engines.get("node"):
        return str(engines.get("node"))
    return None


def _select_wheel_url(files: list[dict[str, object]]) -> str:
    """Pick the best wheel for entry-point inspection.

    Prefer a 'py3-none-any' wheel; fall back to the first available wheel.
    """
    wheels = [f for f in files if str(f.get("filename", "")).endswith(".whl")]
    if not wheels:
        raise RegistryFetchError(
            "no wheel (.whl) distribution found; cannot inspect entry point"
        )
    for wheel in wheels:
        if "py3-none-any" in str(wheel.get("filename", "")):
            return str(wheel["url"])
    return str(wheels[0]["url"])


async def fetch_pypi(source: PackageSource) -> tuple[PackageMeta, Path]:
    """Fetch metadata and the source wheel for a PyPI package."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            meta_url = f"{_PYPI_BASE}/{source.name}/json"
            resp = await client.get(meta_url, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()

            info = data["info"]
            version = source.version or str(info["version"])
            files = data.get("releases", {}).get(version)
            if not files:
                raise RegistryFetchError(
                    f"version {version} not found for PyPI package '{source.name}'"
                )

            wheel_url = _select_wheel_url(files)
            dest_dir = Path(tempfile.mkdtemp(prefix="mcpb-pypi-"))
            archive = await _download(client, wheel_url, dest_dir)
    except httpx.HTTPStatusError as exc:
        raise RegistryFetchError(
            f"PyPI returned HTTP {exc.response.status_code} for {exc.request.url}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise RegistryFetchError(f"PyPI request timed out for '{source.name}'") from exc
    except httpx.RequestError as exc:
        raise RegistryFetchError(
            f"PyPI request failed for '{source.name}': {exc}"
        ) from exc

    pinned = source.model_copy(update={"version": version})
    entry = await inspector.detect_entry_point(archive, pinned)
    env_vars = inspector.scan_readme_for_env_vars(str(info.get("description", "")))

    meta = PackageMeta(
        name=str(info["name"]),
        version=version,
        description=str(info.get("summary") or ""),
        author=str(info.get("author") or info.get("author_email") or "Unknown"),
        homepage=info.get("home_page") or None,
        license_id=_pypi_license(info),
        server_type=ServerType.PYTHON,
        entry=entry,
        detected_env_vars=env_vars,
        keywords=_split_keywords(info.get("keywords")),
        repository_url=_pypi_repo_url(info),
        requires_python=info.get("requires_python") or None,
    )
    return meta, archive


async def fetch_npm(source: PackageSource) -> tuple[PackageMeta, Path]:
    """Fetch metadata and the source tarball for an npm package."""
    selector = source.version or "latest"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            meta_url = f"{_NPM_BASE}/{source.name}/{selector}"
            resp = await client.get(meta_url, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()

            version = str(data["version"])
            tarball_url = str(data["dist"]["tarball"])
            dest_dir = Path(tempfile.mkdtemp(prefix="mcpb-npm-"))
            archive = await _download(client, tarball_url, dest_dir)
    except httpx.HTTPStatusError as exc:
        raise RegistryFetchError(
            f"npm returned HTTP {exc.response.status_code} for {exc.request.url}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise RegistryFetchError(f"npm request timed out for '{source.name}'") from exc
    except httpx.RequestError as exc:
        raise RegistryFetchError(
            f"npm request failed for '{source.name}': {exc}"
        ) from exc

    pinned = source.model_copy(update={"version": version})
    entry = await inspector.detect_entry_point(archive, pinned)
    env_vars = inspector.scan_readme_for_env_vars(str(data.get("readme", "")))

    author = data.get("author")
    author_name = (
        author.get("name", "Unknown")
        if isinstance(author, dict)
        else str(author or "Unknown")
    )

    meta = PackageMeta(
        name=str(data["name"]),
        version=version,
        description=str(data.get("description") or ""),
        author=author_name,
        homepage=data.get("homepage") or None,
        license_id=data.get("license") or None,
        server_type=ServerType.NODE,
        entry=entry,
        detected_env_vars=env_vars,
        keywords=_split_keywords(data.get("keywords")),
        repository_url=_npm_repo_url(data),
        node_engine=_npm_node_engine(data),
    )
    return meta, archive


async def fetch(source: PackageSource) -> tuple[PackageMeta, Path]:
    """Dispatch to the correct registry fetcher."""
    match source.registry:
        case Registry.PYPI:
            return await fetch_pypi(source)
        case Registry.NPM:
            return await fetch_npm(source)
