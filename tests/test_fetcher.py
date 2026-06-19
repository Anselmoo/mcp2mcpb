"""Registry client tests using respx to mock all HTTP traffic."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from mcp2mcpb.exceptions import RegistryFetchError
from mcp2mcpb.fetcher import (
    _clean_git_url,
    _npm_node_engine,
    _npm_repo_url,
    _pypi_license,
    _pypi_repo_url,
    _split_keywords,
    fetch_npm,
    fetch_pypi,
)
from mcp2mcpb.models import PackageSource, Registry


def _wheel_url(pypi_response_fixture) -> str:
    return pypi_response_fixture["releases"]["1.0.0"][0]["url"]


def test_split_keywords_handles_comma_space_and_list():
    assert _split_keywords("a, b ,c") == ["a", "b", "c"]
    assert _split_keywords("a b c") == ["a", "b", "c"]
    assert _split_keywords(["x", "y"]) == ["x", "y"]
    assert _split_keywords(None) == []
    assert _split_keywords("") == []


def test_clean_git_url_strips_prefix_and_suffix():
    assert _clean_git_url("git+https://github.com/a/b.git") == "https://github.com/a/b"
    assert _clean_git_url("git://github.com/a/b") == "https://github.com/a/b"


def test_pypi_repo_url_prefers_repository_over_homepage():
    info = {
        "project_urls": {
            "Homepage": "https://h.example",
            "Repository": "https://github.com/a/b.git",
        }
    }
    assert _pypi_repo_url(info) == "https://github.com/a/b"


def test_pypi_repo_url_falls_back_to_home_page():
    assert _pypi_repo_url({"home_page": "https://h.example"}) == "https://h.example"
    assert _pypi_repo_url({}) is None


def test_npm_repo_url_object_and_string_forms():
    obj = {"repository": {"type": "git", "url": "git+https://github.com/a/b.git"}}
    assert _npm_repo_url(obj) == "https://github.com/a/b"
    assert _npm_repo_url({"repository": "github.com/a/b"}) == "github.com/a/b"
    assert _npm_repo_url({}) is None


def test_npm_node_engine():
    assert _npm_node_engine({"engines": {"node": ">=18"}}) == ">=18"
    assert _npm_node_engine({"engines": {}}) is None
    assert _npm_node_engine({}) is None


def test_pypi_license_prefers_expression_then_legacy_then_classifier():
    assert _pypi_license({"license_expression": "MIT", "license": "ignored"}) == "MIT"
    assert _pypi_license({"license": "Apache-2.0"}) == "Apache-2.0"
    # Full license text dumped into the legacy field is rejected.
    assert _pypi_license({"license": "x" * 200}) is None
    # Classifier fallback when no usable string fields.
    classifiers = ["License :: OSI Approved :: MIT License"]
    assert _pypi_license({"classifiers": classifiers}) == "MIT"
    assert _pypi_license({}) is None


@respx.mock
async def test_pypi_resolves_latest_version(pypi_response_fixture, fake_wheel_path):
    respx.get("https://pypi.org/pypi/mcp-server-example/json").mock(
        return_value=httpx.Response(200, json=pypi_response_fixture)
    )
    respx.get(_wheel_url(pypi_response_fixture)).mock(
        return_value=httpx.Response(200, content=fake_wheel_path.read_bytes())
    )

    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    meta, archive = await fetch_pypi(source)

    assert meta.version == "1.0.0"
    assert meta.name == "mcp-server-example"
    assert Path(archive).exists()


@respx.mock
async def test_pypi_populates_rich_metadata(pypi_response_fixture, fake_wheel_path):
    info = {
        **pypi_response_fixture["info"],
        "keywords": "mcp, demo",
        "project_urls": {"Repository": "https://github.com/a/b.git"},
        "requires_python": ">=3.12",
        "license_expression": "MIT",
    }
    payload = {**pypi_response_fixture, "info": info}
    respx.get("https://pypi.org/pypi/mcp-server-example/json").mock(
        return_value=httpx.Response(200, json=payload)
    )
    respx.get(_wheel_url(pypi_response_fixture)).mock(
        return_value=httpx.Response(200, content=fake_wheel_path.read_bytes())
    )

    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    meta, _ = await fetch_pypi(source)

    assert meta.keywords == ["mcp", "demo"]
    assert meta.repository_url == "https://github.com/a/b"
    assert meta.requires_python == ">=3.12"
    assert meta.license_id == "MIT"


@respx.mock
async def test_pypi_uses_pinned_version(pypi_response_fixture, fake_wheel_path):
    respx.get("https://pypi.org/pypi/mcp-server-example/json").mock(
        return_value=httpx.Response(200, json=pypi_response_fixture)
    )
    respx.get(_wheel_url(pypi_response_fixture)).mock(
        return_value=httpx.Response(200, content=fake_wheel_path.read_bytes())
    )

    source = PackageSource(
        registry=Registry.PYPI, name="mcp-server-example", version="1.0.0"
    )
    meta, _ = await fetch_pypi(source)
    assert meta.version == "1.0.0"


@respx.mock
async def test_pypi_raises_on_404():
    respx.get("https://pypi.org/pypi/does-not-exist/json").mock(
        return_value=httpx.Response(404)
    )
    source = PackageSource(registry=Registry.PYPI, name="does-not-exist")
    with pytest.raises(RegistryFetchError):
        await fetch_pypi(source)


@respx.mock
async def test_pypi_raises_on_timeout():
    respx.get("https://pypi.org/pypi/slow/json").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    source = PackageSource(registry=Registry.PYPI, name="slow")
    with pytest.raises(RegistryFetchError):
        await fetch_pypi(source)


@respx.mock
async def test_npm_resolves_tarball(npm_response_fixture, fake_npm_tarball_path):
    respx.get("https://registry.npmjs.org/mcp-server-example/latest").mock(
        return_value=httpx.Response(200, json=npm_response_fixture)
    )
    respx.get(npm_response_fixture["dist"]["tarball"]).mock(
        return_value=httpx.Response(200, content=fake_npm_tarball_path.read_bytes())
    )

    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    meta, archive = await fetch_npm(source)

    assert meta.version == "1.0.0"
    assert meta.server_type.value == "node"
    assert Path(archive).exists()


@respx.mock
async def test_npm_populates_rich_metadata(npm_response_fixture, fake_npm_tarball_path):
    payload = {
        **npm_response_fixture,
        "keywords": ["mcp", "demo"],
        "repository": {"type": "git", "url": "git+https://github.com/a/b.git"},
        "engines": {"node": ">=18"},
    }
    respx.get("https://registry.npmjs.org/mcp-server-example/latest").mock(
        return_value=httpx.Response(200, json=payload)
    )
    respx.get(npm_response_fixture["dist"]["tarball"]).mock(
        return_value=httpx.Response(200, content=fake_npm_tarball_path.read_bytes())
    )

    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    meta, _ = await fetch_npm(source)

    assert meta.keywords == ["mcp", "demo"]
    assert meta.repository_url == "https://github.com/a/b"
    assert meta.node_engine == ">=18"
    assert meta.license_id == "MIT"


@respx.mock
async def test_npm_raises_on_404():
    respx.get("https://registry.npmjs.org/missing/latest").mock(
        return_value=httpx.Response(404)
    )
    source = PackageSource(registry=Registry.NPM, name="missing")
    with pytest.raises(RegistryFetchError):
        await fetch_npm(source)
