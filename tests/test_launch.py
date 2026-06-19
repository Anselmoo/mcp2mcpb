from pathlib import Path

import pytest

from mcp2mcpb.exceptions import ConfigError
from mcp2mcpb.launch import (
    auto_extra_layer,
    default_launch,
    load_sidecar,
    load_sidecar_pin,
    parse_mcpb_table,
    resolve_launch,
)
from mcp2mcpb.models import (
    BundleMode,
    LaunchOverrides,
    Registry,
    Runner,
    Transport,
)


def test_default_runner_from_registry_and_mode():
    assert default_launch(Registry.PYPI, BundleMode.REFERENCE).runner is Runner.UVX
    assert default_launch(Registry.NPM, BundleMode.REFERENCE).runner is Runner.NPX
    assert default_launch(Registry.PYPI, BundleMode.COMPLETE).runner is Runner.PYTHON
    assert default_launch(Registry.NPM, BundleMode.COMPLETE).runner is Runner.NODE


def test_default_transport_is_auto():
    spec = default_launch(Registry.PYPI, BundleMode.REFERENCE)
    assert spec.transport is Transport.AUTO


def test_resolve_empty_layers_returns_default():
    spec = resolve_launch(Registry.PYPI, BundleMode.REFERENCE, [])
    assert spec == default_launch(Registry.PYPI, BundleMode.REFERENCE)


def test_first_non_none_wins_per_field():
    high = LaunchOverrides(entry_script="server-bin")
    low = LaunchOverrides(entry_script="other", extras=["mcp"])
    spec = resolve_launch(Registry.PYPI, BundleMode.REFERENCE, [high, low])
    assert spec.entry_script == "server-bin"  # high wins
    assert spec.extras == ["mcp"]  # only low set it


def test_runner_override_changes_runner():
    spec = resolve_launch(
        Registry.PYPI, BundleMode.REFERENCE, [LaunchOverrides(runner=Runner.UV_RUN)]
    )
    assert spec.runner is Runner.UV_RUN


def test_parse_kebab_keys_to_fields():
    ov = parse_mcpb_table(
        {
            "runner": "uvx",
            "entry-script": "srv",
            "extras": ["mcp"],
            "subcommand": ["start-mcp-server"],
            "transport": "stdio",
        }
    )
    assert ov.runner is Runner.UVX
    assert ov.entry_script == "srv"
    assert ov.extras == ["mcp"]
    assert ov.subcommand == ["start-mcp-server"]
    assert ov.transport is Transport.STDIO


def test_parse_unknown_key_ignored():
    ov = parse_mcpb_table({"bogus": 1, "runner": "npx"})
    assert ov.runner is Runner.NPX


def test_parse_bad_value_raises_config_error():
    with pytest.raises(ConfigError):
        parse_mcpb_table({"runner": "not-a-runner"})


def test_load_sidecar_dotfile(tmp_path: Path):
    (tmp_path / ".mcpb.toml").write_text(
        'runner = "uvx"\nentry-script = "srv"\n', encoding="utf-8"
    )
    ov = load_sidecar(tmp_path)
    assert ov.entry_script == "srv"


def test_load_sidecar_pyproject_tool_table(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcpb]\nentry-script = "srv2"\n', encoding="utf-8"
    )
    ov = load_sidecar(tmp_path)
    assert ov.entry_script == "srv2"


def test_load_sidecar_missing_returns_empty(tmp_path: Path):
    assert load_sidecar(tmp_path) == LaunchOverrides()


def test_auto_extra_applies_when_mcp_declared_and_no_layer_extras():
    layers = [LaunchOverrides(entry_script="srv"), LaunchOverrides()]
    ov = auto_extra_layer(["mcp", "plotly"], layers)
    assert ov is not None
    assert ov.extras == ["mcp"]


def test_auto_extra_skips_when_a_layer_already_set_extras():
    layers = [LaunchOverrides(extras=["plotly"])]
    assert auto_extra_layer(["mcp"], layers) is None


def test_auto_extra_skips_when_mcp_not_declared():
    assert auto_extra_layer(["plotly"], [LaunchOverrides()]) is None


# ── from_spec (--from / `from`) ───────────────────────────────────────────────


def test_parse_from_key_to_from_spec():
    assert parse_mcpb_table({"from": True}).from_spec is True
    assert parse_mcpb_table({"from": False}).from_spec is False
    assert parse_mcpb_table({"runner": "uvx"}).from_spec is None


def test_resolve_propagates_from_spec():
    spec = resolve_launch(
        Registry.PYPI, BundleMode.REFERENCE, [LaunchOverrides(from_spec=True)]
    )
    assert spec.from_spec is True


# ── version pin (handled outside the launch recipe) ───────────────────────────


def test_parse_version_key_is_ignored_not_unknown():
    # `version` is valid in [tool.mcpb] but resolved by load_sidecar_pin, so it
    # must not raise, warn-as-unknown, or leak into the recipe.
    ov = parse_mcpb_table({"version": "1.9.0", "runner": "uvx"})
    assert ov.runner is Runner.UVX
    assert not hasattr(ov, "version")


def test_load_sidecar_pin_dotfile(tmp_path: Path):
    (tmp_path / ".mcpb.toml").write_text('version = "1.9.0"\n', encoding="utf-8")
    assert load_sidecar_pin(tmp_path) == "1.9.0"


def test_load_sidecar_pin_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcpb]\nversion = "2.0.0"\n', encoding="utf-8"
    )
    assert load_sidecar_pin(tmp_path) == "2.0.0"


def test_load_sidecar_pin_latest_and_missing_are_none(tmp_path: Path):
    assert load_sidecar_pin(tmp_path) is None  # no config
    (tmp_path / ".mcpb.toml").write_text('version = "LATEST"\n', encoding="utf-8")
    assert load_sidecar_pin(tmp_path) is None  # explicit latest (case-insensitive)
