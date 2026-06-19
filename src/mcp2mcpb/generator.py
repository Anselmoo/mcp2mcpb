"""Synthesize a ``manifest.json`` model from package metadata.

Pure module — no I/O and no side effects. The only output is a fully
populated :class:`Manifest` ready for serialisation by the packer.
"""

from __future__ import annotations

from mcp2mcpb.models import (
    BundleMode,
    Compatibility,
    LaunchSpec,
    Manifest,
    ManifestAuthor,
    McpConfig,
    PackageMeta,
    PackageSource,
    RepositoryRef,
    Runner,
    ServerConfig,
    ServerType,
    Transport,
    UserConfigField,
)

_SENSITIVE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASS", "AUTH", "CRED", "BEARER")

# Bundles target Claude Desktop, which runs on these three OS families.
_PLATFORMS = ["darwin", "win32", "linux"]


def _config_key(var: str) -> str:
    """Normalise 'MY_API_KEY' → 'my_api_key' for user_config keys."""
    return var.lower()


def _is_sensitive(var: str) -> bool:
    """Return True for vars that are likely secrets."""
    return any(marker in var for marker in _SENSITIVE_MARKERS)


def _user_config_and_env(
    env_vars: list[str],
) -> tuple[dict[str, UserConfigField], dict[str, str]]:
    """Build user_config fields and the env injection map from env vars."""
    user_config: dict[str, UserConfigField] = {}
    env: dict[str, str] = {}
    for var in env_vars:
        key = _config_key(var)
        user_config[key] = UserConfigField(
            type="string",
            title=var,
            description=f"Value for the {var} environment variable.",
            sensitive=_is_sensitive(var),
            # Heuristic detection can't know if a var is truly mandatory;
            # default to optional so it never blocks one-click install.
            required=False,
        )
        env[var] = f"${{user_config.{key}}}"
    return user_config, env


def _transport_flag(transport: Transport) -> list[str]:
    return ["--transport", "stdio"] if transport is Transport.STDIO else []


def _from_spec(source: PackageSource, extras: list[str]) -> str:
    name = source.name
    extra_suffix = f"[{','.join(extras)}]" if extras else ""
    pin = f"=={source.version}" if source.version else ""
    return f"{name}{extra_suffix}{pin}"


def render_mcp_config(
    launch: LaunchSpec,
    source: PackageSource,
    meta: PackageMeta,
    mode: BundleMode,
    env: dict[str, str],
) -> tuple[McpConfig, ServerType, str]:
    """Render a resolved LaunchSpec into (mcp_config, server_type, entry_point)."""
    tflag = _transport_flag(launch.transport)
    sub = list(launch.subcommand)

    if launch.runner is Runner.UVX:
        if launch.from_spec is not None:
            needs_from = launch.from_spec
        else:
            needs_from = bool(launch.extras) or (
                launch.entry_script is not None and launch.entry_script != source.name
            )
        if needs_from:
            args = [
                "tool",
                "run",
                "--from",
                _from_spec(source, launch.extras),
                launch.entry_script or source.name,
                *sub,
                *tflag,
            ]
        else:
            args = ["tool", "run", source.pinned, *sub, *tflag]
        return McpConfig(command="uv", args=args, env=env), ServerType.UV, ""

    if launch.runner is Runner.NPX:
        args = ["-y", source.pinned, *sub, *tflag]
        return McpConfig(command="npx", args=args, env=env), ServerType.NODE, ""

    if launch.runner is Runner.UV_RUN:
        with_args: list[str] = []
        for dep in launch.extras:
            with_args += ["--with", dep]
        script = launch.entry_script or source.name
        args = ["run", *with_args, "python", script, *sub, *tflag]
        return McpConfig(command="uv", args=args, env=env), ServerType.UV, ""

    if launch.runner is Runner.NODE:
        args = [*meta.entry.args, *sub, *tflag]
        return (
            McpConfig(command="node", args=args, env=env),
            ServerType.NODE,
            meta.entry.entry_file,
        )

    # Runner.PYTHON (complete python)
    complete_env = dict(env)
    complete_env["PYTHONPATH"] = "${__dirname}/server"
    args = [*meta.entry.args, *sub, *tflag]
    return (
        McpConfig(command="python", args=args, env=complete_env),
        ServerType.PYTHON,
        meta.entry.entry_file,
    )


def _compatibility(meta: PackageMeta) -> Compatibility:
    """Build the compatibility block from the package's runtime constraints.

    Only constraints we can actually derive are asserted; ``claude_desktop`` is
    deliberately left unset.
    """
    runtimes: dict[str, str] = {}
    if meta.server_type is ServerType.NODE:
        if meta.node_engine:
            runtimes["node"] = meta.node_engine
    else:
        runtimes["python"] = meta.requires_python or ">=3.12"
    return Compatibility(platforms=list(_PLATFORMS), runtimes=runtimes)


def generate_manifest(
    meta: PackageMeta,
    source: PackageSource,
    mode: BundleMode,
    launch: LaunchSpec,
) -> Manifest:
    """Synthesize a Manifest from package metadata + the resolved launch recipe."""
    user_config, env = _user_config_and_env(meta.detected_env_vars)
    mcp_config, server_type, entry_point = render_mcp_config(
        launch, source, meta, mode, env
    )
    server = ServerConfig(
        type=server_type, entry_point=entry_point, mcp_config=mcp_config
    )
    repository = RepositoryRef(url=meta.repository_url) if meta.repository_url else None
    return Manifest(
        name=meta.name,
        display_name=meta.name,
        version=meta.version,
        description=meta.description,
        author=ManifestAuthor(name=meta.author),
        repository=repository,
        homepage=meta.homepage,
        license=meta.license_id,
        keywords=list(meta.keywords),
        server=server,
        user_config=user_config,
        compatibility=_compatibility(meta),
    )
