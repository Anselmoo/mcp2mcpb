"""Manifest synthesis tests."""

from __future__ import annotations

from mcp2mcpb.generator import generate_manifest, render_mcp_config
from mcp2mcpb.launch import default_launch
from mcp2mcpb.models import (
    BundleMode,
    EntryPoint,
    LaunchSpec,
    PackageMeta,
    PackageSource,
    Registry,
    Runner,
    ServerType,
    Transport,
)


def _python_meta(env_vars: list[str] | None = None) -> PackageMeta:
    return PackageMeta(
        name="mcp-server-example",
        version="1.0.0",
        description="An example.",
        author="Example Author",
        homepage="https://example.com",
        license_id="MIT",
        server_type=ServerType.PYTHON,
        entry=EntryPoint(
            command="python",
            args=["-m", "mcp_server_example"],
            entry_file="server/mcp_server_example/__main__.py",
        ),
        detected_env_vars=env_vars or [],
    )


def _node_meta() -> PackageMeta:
    return PackageMeta(
        name="mcp-server-example",
        version="1.0.0",
        description="An example.",
        author="Example Author",
        server_type=ServerType.NODE,
        entry=EntryPoint(
            command="node",
            args=["${__dirname}/server/dist/index.js"],
            entry_file="server/dist/index.js",
        ),
    )


def test_manifest_includes_repository_keywords_compatibility():
    meta = _python_meta().model_copy(
        update={
            "keywords": ["mcp", "demo"],
            "repository_url": "https://github.com/x/y",
            "requires_python": ">=3.12",
        }
    )
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert manifest.display_name == "mcp-server-example"
    assert manifest.repository is not None
    assert manifest.repository.url == "https://github.com/x/y"
    assert manifest.repository.type == "git"
    assert manifest.keywords == ["mcp", "demo"]
    assert manifest.compatibility is not None
    assert manifest.compatibility.platforms == ["darwin", "win32", "linux"]
    assert manifest.compatibility.runtimes == {"python": ">=3.12"}


def test_compatibility_defaults_python_when_requires_python_missing():
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        _python_meta(),
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert manifest.compatibility is not None
    assert manifest.compatibility.runtimes == {"python": ">=3.12"}


def test_node_compatibility_uses_node_engine():
    meta = _node_meta().model_copy(update={"node_engine": ">=18"})
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert manifest.compatibility is not None
    assert manifest.compatibility.runtimes == {"node": ">=18"}


def test_repository_omitted_when_unknown():
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    manifest = generate_manifest(
        _node_meta(),
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert manifest.repository is None


def test_complete_mode_uses_entry_command():
    meta = _python_meta()
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert manifest.server.mcp_config.command == "python"
    assert manifest.server.entry_point == "server/mcp_server_example/__main__.py"


def test_reference_mode_pypi_uses_uvx():
    meta = _python_meta()
    source = PackageSource(
        registry=Registry.PYPI, name="mcp-server-example", version="1.0.0"
    )
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert manifest.server.mcp_config.command == "uv"
    assert "tool" in manifest.server.mcp_config.args
    assert source.pinned in manifest.server.mcp_config.args
    assert manifest.server.entry_point == ""


def test_reference_mode_npm_uses_npx():
    meta = _node_meta()
    source = PackageSource(
        registry=Registry.NPM, name="mcp-server-example", version="1.0.0"
    )
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert manifest.server.mcp_config.command == "npx"
    assert manifest.server.mcp_config.args[0] == "-y"


def test_env_vars_become_user_config_and_env():
    meta = _python_meta(env_vars=["MY_API_KEY", "SOME_HOST"])
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert "my_api_key" in manifest.user_config
    assert manifest.server.mcp_config.env["MY_API_KEY"] == "${user_config.my_api_key}"


def test_api_key_is_sensitive():
    meta = _python_meta(env_vars=["API_KEY"])
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert manifest.user_config["api_key"].sensitive is True


def test_detected_env_vars_are_optional():
    # Heuristic detection must not force the user to fill a field at install.
    meta = _python_meta(env_vars=["API_KEY"])
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert manifest.user_config["api_key"].required is False


def test_manifest_version_is_always_0_4():
    meta = _python_meta()
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert manifest.manifest_version == "0.4"


def test_complete_python_injects_pythonpath():
    # Deps are vendored flat into server/, so server/ must be importable.
    meta = _python_meta()
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert manifest.server.mcp_config.env["PYTHONPATH"] == "${__dirname}/server"


def test_complete_node_has_no_pythonpath():
    meta = _node_meta()
    source = PackageSource(registry=Registry.NPM, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert "PYTHONPATH" not in manifest.server.mcp_config.env


def test_reference_mode_has_no_pythonpath():
    meta = _python_meta()
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.REFERENCE,
        default_launch(source.registry, BundleMode.REFERENCE),
    )
    assert "PYTHONPATH" not in manifest.server.mcp_config.env


def test_homepage_and_license_are_mapped():
    meta = _python_meta()
    source = PackageSource(registry=Registry.PYPI, name="mcp-server-example")
    manifest = generate_manifest(
        meta,
        source,
        BundleMode.COMPLETE,
        default_launch(source.registry, BundleMode.COMPLETE),
    )
    assert manifest.homepage == "https://example.com"
    assert manifest.license == "MIT"


# ── Six-pattern render tests ──────────────────────────────────────────────────


def _src(name: str, ver: str = "1.0.0", reg: Registry = Registry.PYPI) -> PackageSource:
    return PackageSource(registry=reg, name=name, version=ver)


def test_render_uvx_bare() -> None:
    spec = LaunchSpec(runner=Runner.UVX)
    cfg, stype, entry = render_mcp_config(
        spec, _src("mcp-server-analyzer"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.command == "uv"
    assert cfg.args == ["tool", "run", "mcp-server-analyzer==1.0.0"]
    assert stype is ServerType.UV
    assert entry == ""


def test_render_uvx_from_named_script() -> None:
    spec = LaunchSpec(runner=Runner.UVX, entry_script="mcp-zen-of-languages-server")
    cfg, stype, _ = render_mcp_config(
        spec, _src("mcp-zen-of-languages"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.args == [
        "tool",
        "run",
        "--from",
        "mcp-zen-of-languages==1.0.0",
        "mcp-zen-of-languages-server",
    ]


def test_render_uvx_from_extras() -> None:
    spec = LaunchSpec(runner=Runner.UVX, entry_script="rrt-mcp", extras=["mcp"])
    cfg, _, _ = render_mcp_config(
        spec, _src("repo-release-tools"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.args == [
        "tool",
        "run",
        "--from",
        "repo-release-tools[mcp]==1.0.0",
        "rrt-mcp",
    ]


def test_render_uvx_subcommand() -> None:
    spec = LaunchSpec(
        runner=Runner.UVX, entry_script="serena", subcommand=["start-mcp-server"]
    )
    cfg, _, _ = render_mcp_config(
        spec, _src("serena-agent"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.args == [
        "tool",
        "run",
        "--from",
        "serena-agent==1.0.0",
        "serena",
        "start-mcp-server",
    ]


def test_render_uvx_force_from_when_name_matches() -> None:
    # entry_script == package name → heuristic would NOT use --from; from_spec
    # forces it.
    spec = LaunchSpec(runner=Runner.UVX, from_spec=True)
    cfg, _, _ = render_mcp_config(
        spec, _src("mcp-server-analyzer"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.args == [
        "tool",
        "run",
        "--from",
        "mcp-server-analyzer==1.0.0",
        "mcp-server-analyzer",
    ]


def test_render_uvx_suppress_from_overrides_heuristic() -> None:
    # entry_script differs (heuristic would add --from); from_spec=False forces bare.
    spec = LaunchSpec(runner=Runner.UVX, entry_script="serena", from_spec=False)
    cfg, _, _ = render_mcp_config(
        spec, _src("serena-agent"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.args == ["tool", "run", "serena-agent==1.0.0"]


def test_render_npx_bare() -> None:
    spec = LaunchSpec(runner=Runner.NPX)
    cfg, stype, _ = render_mcp_config(
        spec,
        _src("mcp-ai-agent-guidelines", reg=Registry.NPM),
        _node_meta(),
        BundleMode.REFERENCE,
        {},
    )
    assert cfg.command == "npx"
    assert cfg.args == ["-y", "mcp-ai-agent-guidelines@1.0.0"]
    assert stype is ServerType.NODE


def test_render_transport_stdio_appends_flag() -> None:
    spec = LaunchSpec(runner=Runner.UVX, transport=Transport.STDIO)
    cfg, _, _ = render_mcp_config(
        spec, _src("p"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.args[-2:] == ["--transport", "stdio"]


def test_render_complete_python_keeps_pythonpath() -> None:
    spec = default_launch(Registry.PYPI, BundleMode.COMPLETE)
    cfg, stype, entry = render_mcp_config(
        spec, _src("mcp-server-example"), _python_meta(), BundleMode.COMPLETE, {}
    )
    assert cfg.command == "python"
    assert cfg.args[:2] == ["-m", "mcp_server_example"]
    assert cfg.env["PYTHONPATH"] == "${__dirname}/server"
    assert stype is ServerType.PYTHON
    assert entry == "server/mcp_server_example/__main__.py"


def test_render_complete_node() -> None:
    spec = LaunchSpec(runner=Runner.NODE)
    cfg, stype, entry = render_mcp_config(
        spec,
        _src("mcp-server-example", reg=Registry.NPM),
        _node_meta(),
        BundleMode.COMPLETE,
        {},
    )
    assert cfg.command == "node"
    assert cfg.args == ["${__dirname}/server/dist/index.js"]
    assert stype is ServerType.NODE
    assert entry == "server/dist/index.js"
    assert "PYTHONPATH" not in cfg.env


def test_render_uv_run_appends_transport_flag() -> None:
    spec = LaunchSpec(
        runner=Runner.UV_RUN, entry_script="srv.py", transport=Transport.STDIO
    )
    cfg, stype, _ = render_mcp_config(
        spec, _src("pkg"), _python_meta(), BundleMode.REFERENCE, {}
    )
    assert cfg.command == "uv"
    assert cfg.args == ["run", "python", "srv.py", "--transport", "stdio"]
    assert stype is ServerType.UV
