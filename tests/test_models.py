"""Unit tests for the Pydantic v2 domain models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp2mcpb.models import (
    EntryPoint,
    LaunchOverrides,
    LaunchSpec,
    Manifest,
    ManifestAuthor,
    McpConfig,
    PackageSource,
    Registry,
    Runner,
    ServerConfig,
    ServerType,
    Transport,
    UserConfigField,
)


def test_blank_package_name_raises():
    with pytest.raises(ValidationError):
        PackageSource(registry=Registry.PYPI, name="   ")


def test_pinned_pypi_with_and_without_version():
    with_version = PackageSource(registry=Registry.PYPI, name="pkg", version="1.2.0")
    without = PackageSource(registry=Registry.PYPI, name="pkg")
    assert with_version.pinned == "pkg==1.2.0"
    assert without.pinned == "pkg"


def test_pinned_npm_with_and_without_version():
    with_version = PackageSource(
        registry=Registry.NPM, name="@scope/pkg", version="2.0.0"
    )
    without = PackageSource(registry=Registry.NPM, name="@scope/pkg")
    assert with_version.pinned == "@scope/pkg@2.0.0"
    assert without.pinned == "@scope/pkg"


def _make_manifest() -> Manifest:
    return Manifest(
        name="mcp-server-example",
        version="1.0.0",
        description="An example.",
        author=ManifestAuthor(name="Example Author"),
        server=ServerConfig(
            type=ServerType.PYTHON,
            entry_point="server/mcp_server_example/__main__.py",
            mcp_config=McpConfig(command="python", args=["-m", "mcp_server_example"]),
        ),
        user_config={
            "my_api_key": UserConfigField(
                title="MY_API_KEY",
                description="Value for MY_API_KEY.",
                sensitive=True,
            )
        },
    )


def test_manifest_write_to_produces_valid_json(tmp_path: Path):
    target = tmp_path / "manifest.json"
    _make_manifest().write_to(target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["manifest_version"] == "0.4"
    assert data["name"] == "mcp-server-example"


def test_user_config_field_sensitive_serialises():
    field = UserConfigField(title="TOKEN", description="A token.", sensitive=True)
    dumped = json.loads(field.model_dump_json())
    assert dumped["sensitive"] is True
    assert dumped["type"] == "string"


def test_strenum_accepts_string_and_enum():
    from_enum = PackageSource(registry=Registry.PYPI, name="pkg")
    # Raw string input (as parsed from CLI/JSON) coerces to the StrEnum.
    from_string = PackageSource.model_validate({"registry": "pypi", "name": "pkg"})
    assert from_enum.registry == from_string.registry == Registry.PYPI


def test_entry_point_round_trips():
    entry = EntryPoint(command="node", args=["x.js"], entry_file="server/x.js")
    assert entry.command == "node"
    assert entry.entry_file == "server/x.js"


def test_runner_and_transport_values():
    assert Runner.UVX == "uvx"
    assert Runner.UV_RUN == "uv-run"
    assert {r.value for r in Runner} == {"uvx", "npx", "uv-run", "python", "node"}
    assert {t.value for t in Transport} == {"stdio", "none", "auto"}


def test_servertype_has_uv():
    assert ServerType.UV == "uv"


def test_launchspec_defaults_and_frozen():
    spec = LaunchSpec(runner=Runner.UVX)
    assert spec.entry_script is None
    assert spec.extras == []
    assert spec.subcommand == []
    assert spec.transport is Transport.AUTO
    with pytest.raises(ValidationError):
        spec.runner = Runner.NPX  # frozen


def test_launchoverrides_all_optional():
    empty = LaunchOverrides()
    assert empty.runner is None
    assert empty.extras is None  # None means "not set", distinct from []


def test_manifest_version_default_is_0_4():
    # _make_manifest() is the existing helper in this file
    assert _make_manifest().manifest_version == "0.4"
