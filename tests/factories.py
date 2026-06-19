"""Builders for synthetic package archives + registry payloads.

Used by the offline end-to-end conversion tests. Nothing here touches the
network: each helper returns in-memory bytes / dicts that respx serves and the
real pipeline consumes, so a conversion can be driven exactly as in production
without ever hitting PyPI or npm.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile


def build_wheel_bytes(
    name: str,
    version: str,
    *,
    console_scripts: dict[str, str] | None = None,
    extras: list[str] | None = None,
) -> bytes:
    """Return the bytes of a minimal but valid wheel for ``name==version``."""
    module = name.replace("-", "_")
    dist_info = f"{module}-{version}.dist-info"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if console_scripts:
            body = "[console_scripts]\n" + "".join(
                f"{k} = {v}\n" for k, v in console_scripts.items()
            )
            zf.writestr(f"{dist_info}/entry_points.txt", body)
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        for extra in extras or []:
            metadata += f"Provides-Extra: {extra}\n"
        zf.writestr(f"{dist_info}/METADATA", metadata)
        zf.writestr(f"{module}/__init__.py", "")
        zf.writestr(f"{module}/__main__.py", "def main():\n    pass\n")
    return buf.getvalue()


def wheel_url(name: str, version: str) -> str:
    """Canonical-looking hosted wheel URL for ``name==version``."""
    module = name.replace("-", "_")
    return (
        f"https://files.pythonhosted.org/packages/py3/{module[0]}/{module}/"
        f"{module}-{version}-py3-none-any.whl"
    )


def pypi_payload(
    name: str,
    version: str,
    *,
    summary: str = "An MCP server.",
    license_id: str = "MIT",
    readme: str = "",
    requires_python: str = ">=3.12",
    repository: str | None = None,
    keywords: str = "",
) -> dict[str, object]:
    """A PyPI JSON API payload referencing :func:`wheel_url`."""
    info: dict[str, object] = {
        "name": name,
        "version": version,
        "summary": summary,
        "author": "Example Author",
        "license": license_id,
        "description": readme,
        "requires_python": requires_python,
        "keywords": keywords,
    }
    if repository:
        info["project_urls"] = {"Repository": repository}
    url = wheel_url(name, version)
    return {
        "info": info,
        "releases": {
            version: [
                {
                    "filename": url.rsplit("/", 1)[-1],
                    "url": url,
                    "packagetype": "bdist_wheel",
                    "python_version": "py3",
                }
            ]
        },
    }


def build_npm_tarball_bytes(
    name: str,
    version: str,
    *,
    bin_field: dict[str, str] | str | None = None,
) -> bytes:
    """Return the bytes of a minimal npm ``.tgz`` for ``name@version``."""
    unscoped = name.rsplit("/", 1)[-1]
    package_json = {
        "name": name,
        "version": version,
        "description": f"{name} MCP server.",
        "bin": bin_field if bin_field is not None else {unscoped: "./dist/index.js"},
        "main": "dist/index.js",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = json.dumps(package_json).encode("utf-8")
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

        index = b"console.log('mcp');\n"
        idx_info = tarfile.TarInfo("package/dist/index.js")
        idx_info.size = len(index)
        tf.addfile(idx_info, io.BytesIO(index))
    return buf.getvalue()


def npm_tarball_url(name: str, version: str) -> str:
    """Canonical-looking npm tarball URL for ``name@version``."""
    unscoped = name.rsplit("/", 1)[-1]
    return f"https://registry.npmjs.org/{name}/-/{unscoped}-{version}.tgz"


def npm_payload(
    name: str,
    version: str,
    *,
    description: str = "An MCP server.",
    license_id: str = "MIT",
    readme: str = "",
    repository: str | None = None,
    node_engine: str | None = None,
) -> dict[str, object]:
    """An npm registry payload referencing :func:`npm_tarball_url`."""
    data: dict[str, object] = {
        "name": name,
        "version": version,
        "description": description,
        "license": license_id,
        "readme": readme,
        "author": {"name": "Example Author"},
        "dist": {"tarball": npm_tarball_url(name, version)},
    }
    if repository:
        data["repository"] = {"type": "git", "url": repository}
    if node_engine:
        data["engines"] = {"node": node_engine}
    return data
