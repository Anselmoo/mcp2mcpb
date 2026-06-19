"""Shared fixtures for the mcp2mcpb test-suite.

No real network calls are made anywhere: HTTP is mocked with respx and all
file I/O uses the ``tmp_path`` fixture.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

_MULTI_ENTRY_POINTS = (
    "[console_scripts]\n"
    "mcp-server-example = mcp_server_example.__main__:main\n"
    "mcp-server-example-server = mcp_server_example.server:main\n"
    "mcp-server-example-cli = mcp_server_example.cli:main\n"
)

WHEEL_URL = (
    "https://files.pythonhosted.org/packages/py3/m/mcp-server-example/"
    "mcp_server_example-1.0.0-py3-none-any.whl"
)
TARBALL_URL = (
    "https://registry.npmjs.org/mcp-server-example/-/mcp-server-example-1.0.0.tgz"
)

_README = (
    "Configure the server by setting MY_API_KEY and BEARER_TOKEN. "
    "The default URL and request ID are derived automatically."
)


@pytest.fixture
def pypi_response_fixture() -> dict[str, object]:
    """A PyPI JSON API payload for a fake mcp-server-example==1.0.0."""
    return {
        "info": {
            "name": "mcp-server-example",
            "version": "1.0.0",
            "summary": "An example MCP server.",
            "author": "Example Author",
            "author_email": "author@example.com",
            "home_page": "https://example.com",
            "license": "MIT",
            "description": _README,
        },
        "releases": {
            "1.0.0": [
                {
                    "filename": "mcp_server_example-1.0.0-py3-none-any.whl",
                    "url": WHEEL_URL,
                    "packagetype": "bdist_wheel",
                    "python_version": "py3",
                }
            ]
        },
    }


@pytest.fixture
def npm_response_fixture() -> dict[str, object]:
    """An npm registry /latest payload for a fake mcp-server-example@1.0.0."""
    return {
        "name": "mcp-server-example",
        "version": "1.0.0",
        "description": "An example MCP server.",
        "homepage": "https://example.com",
        "license": "MIT",
        "author": {"name": "Example Author"},
        "readme": _README,
        "dist": {"tarball": TARBALL_URL},
    }


def _build_wheel(path: Path, *, with_entry_points: bool = True) -> Path:
    """Write a minimal wheel (ZIP) to ``path`` and return it."""
    with zipfile.ZipFile(path, "w") as zf:
        if with_entry_points:
            zf.writestr(
                "mcp_server_example-1.0.0.dist-info/entry_points.txt",
                "[console_scripts]\n"
                "mcp-server-example = mcp_server_example.__main__:main\n",
            )
        zf.writestr(
            "mcp_server_example-1.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: mcp-server-example\nVersion: 1.0.0\n",
        )
        zf.writestr("mcp_server_example/__init__.py", "")
        zf.writestr("mcp_server_example/__main__.py", "def main():\n    pass\n")
    return path


@pytest.fixture
def fake_wheel_path(tmp_path: Path) -> Path:
    """A minimal wheel declaring one console_scripts entry point."""
    return _build_wheel(tmp_path / "mcp_server_example-1.0.0-py3-none-any.whl")


@pytest.fixture
def fake_wheel_no_entrypoints_path(tmp_path: Path) -> Path:
    """A minimal wheel with no entry_points.txt (forces the -m fallback)."""
    return _build_wheel(
        tmp_path / "mcp_server_example-1.0.0-py3-none-any.whl",
        with_entry_points=False,
    )


@pytest.fixture
def fake_wheel_multi_script_path(tmp_path: Path) -> Path:
    path = tmp_path / "mcp_server_example-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "mcp_server_example-1.0.0.dist-info/entry_points.txt", _MULTI_ENTRY_POINTS
        )
        zf.writestr(
            "mcp_server_example-1.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: mcp-server-example\nVersion: 1.0.0\n"
            "Provides-Extra: mcp\n",
        )
    return path


@pytest.fixture
def fake_wheel_with_mcpb(tmp_path: Path) -> Path:
    path = tmp_path / "mcp_server_example-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "mcp_server_example-1.0.0.dist-info/entry_points.txt", _MULTI_ENTRY_POINTS
        )
        zf.writestr(
            "mcp_server_example/mcpb.toml",
            'entry-script = "mcp-server-example-server"\n',
        )
    return path


@pytest.fixture
def fake_npm_tarball_with_mcpb(tmp_path: Path) -> Path:
    package_json = {
        "name": "mcp-server-example",
        "version": "1.0.0",
        "bin": {"mcp-serve": "./dist/index.js"},
        "mcpb": {"subcommand": ["serve"]},
    }
    path = tmp_path / "mcp-server-example-1.0.0.tgz"
    with tarfile.open(path, "w:gz") as tf:
        data = json.dumps(package_json).encode("utf-8")
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def fake_npm_tarball_path(tmp_path: Path) -> Path:
    """A minimal npm tarball with a package/package.json declaring a bin."""
    package_json = {
        "name": "mcp-server-example",
        "version": "1.0.0",
        "description": "An example MCP server.",
        "bin": {"mcp-serve": "./dist/index.js"},
        "main": "index.js",
    }
    path = tmp_path / "mcp-server-example-1.0.0.tgz"
    with tarfile.open(path, "w:gz") as tf:
        data = json.dumps(package_json).encode("utf-8")
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

        index = b"console.log('mcp');\n"
        idx_info = tarfile.TarInfo("package/dist/index.js")
        idx_info.size = len(index)
        tf.addfile(idx_info, io.BytesIO(index))
    return path
