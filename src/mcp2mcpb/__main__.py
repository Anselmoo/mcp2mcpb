"""Entry point: python -m mcp2mcpb or the mcp2mcpb CLI."""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Annotated

import typer

from mcp2mcpb import __version__, ui
from mcp2mcpb.bundler import bundle
from mcp2mcpb.exceptions import McpbConversionError
from mcp2mcpb.fetcher import fetch
from mcp2mcpb.generator import generate_manifest
from mcp2mcpb.inspector import (
    candidate_scripts,
    declared_extras,
    inpackage_overrides,
)
from mcp2mcpb.launch import (
    auto_extra_layer,
    load_sidecar,
    load_sidecar_pin,
    resolve_launch,
)
from mcp2mcpb.models import (
    BundleMode,
    LaunchOverrides,
    PackageSource,
    Registry,
    Runner,
    Transport,
)
from mcp2mcpb.packer import pack_mcpb
from mcp2mcpb.prober import probe_help
from mcp2mcpb.sandbox import run_sandbox

app = typer.Typer(
    name="mcp2mcpb",
    help="Convert PyPI/npm MCP servers into one-click .mcpb bundles.",
    add_completion=True,
    rich_markup_mode=None,  # keep output clean; we handle formatting via ui.py
)

# Real subcommands; anything else as the first token is treated as a package name
# for the implicit `convert` command (see `main`).
_COMMANDS = frozenset({"convert", "unpack", "sandbox"})


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mcp2mcpb {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Print the mcp2mcpb version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Convert PyPI/npm MCP servers into one-click .mcpb bundles."""


def _cli_overrides(
    *,
    runner: Runner | None,
    entry_script: str | None,
    extra: list[str],
    subcommand: str | None,
    transport: Transport | None,
    from_spec: bool | None = None,
) -> LaunchOverrides:
    return LaunchOverrides(
        runner=runner,
        entry_script=entry_script,
        extras=extra or None,
        subcommand=shlex.split(subcommand) if subcommand else None,
        transport=transport,
        from_spec=from_spec,
    )


def _normalize_pin(pin: str | None) -> str | None:
    """Map a CLI ``--pin`` value to a concrete version, or None for latest."""
    if pin is None or pin.strip().lower() == "latest":
        return None
    return pin.strip()


@app.command()
def convert(
    package: Annotated[str, typer.Argument(help="Package name, e.g. mcp-server-fetch")],
    registry: Annotated[
        Registry, typer.Option("--registry", "-r", help="pypi or npm")
    ] = Registry.PYPI,
    pin: Annotated[
        str | None,
        typer.Option("--pin", "-v", help="Pin exact package version; omit for latest"),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output directory for .mcpb files"),
    ] = Path("dist"),
    mode: Annotated[
        BundleMode,
        typer.Option(
            "--mode",
            "-m",
            help="complete (vendor deps) or reference (uvx/npx)",
        ),
    ] = BundleMode.COMPLETE,
    runner: Annotated[
        Runner | None,
        typer.Option(
            "--runner",
            help="Override the runner (uvx, npx, uv-run, python, node)",
        ),
    ] = None,
    entry_script: Annotated[
        str | None,
        typer.Option("--entry-script", help="Override the entry script name"),
    ] = None,
    extra: Annotated[
        list[str],
        typer.Option("--extra", help="Extra dependency to install (repeatable)"),
    ] = [],  # noqa: B006
    subcommand: Annotated[
        str | None,
        typer.Option(
            "--subcommand",
            help="Override subcommand (space-separated string)",
        ),
    ] = None,
    transport: Annotated[
        Transport | None,
        typer.Option("--transport", help="Override transport (stdio, none, auto)"),
    ] = None,
    from_spec: Annotated[
        bool | None,
        typer.Option(
            "--from/--no-from",
            help="Force/suppress uvx `--from` (default: auto-derive from extras / "
            "entry-script)",
        ),
    ] = None,
    no_probe: Annotated[
        bool,
        typer.Option(
            "--no-probe/--probe",
            help="Disable --help probe (default: probe enabled)",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Show the resolved launch recipe and which config file was used",
        ),
    ] = False,
) -> None:
    """Convert a published MCP server into a .mcpb bundle."""
    # Version precedence: CLI --pin > cwd config `version` > latest.
    version = _normalize_pin(pin) or load_sidecar_pin(Path.cwd())
    source = PackageSource(registry=registry, name=package, version=version)
    cli_ov = _cli_overrides(
        runner=runner,
        entry_script=entry_script,
        extra=extra,
        subcommand=subcommand,
        transport=transport,
        from_spec=from_spec,
    )
    try:
        asyncio.run(
            _convert(source, output, mode, cli_ov, probe=not no_probe, verbose=verbose)
        )
    except McpbConversionError as exc:
        ui.error(str(exc))
        raise typer.Exit(code=1) from None


async def _convert(
    source: PackageSource,
    output: Path,
    mode: BundleMode,
    cli_overrides: LaunchOverrides,
    probe: bool,
    verbose: bool = False,
) -> None:
    ui.section(f"Converting {source.registry}/{source.name}")
    ui.info(f"Fetching metadata from {source.registry} …")
    meta, archive = await fetch(source)
    ui.success(f"Found {meta.name} {meta.version}")

    candidates = candidate_scripts(archive, source)
    extras = declared_extras(archive, source)
    probe_overrides = await probe_help(source, candidates, extras, enabled=probe)
    layers = [
        cli_overrides,
        load_sidecar(Path.cwd()),
        inpackage_overrides(archive, source),
        probe_overrides,
    ]
    auto_extra = auto_extra_layer(extras, layers)
    if auto_extra is not None:
        ui.warning(
            "auto-including the 'mcp' extra (declared by the package); "
            "override with --extra or [tool.mcpb]"
        )
        layers.append(auto_extra)
    launch = resolve_launch(source.registry, mode, layers)

    # --no-from on a uvx recipe drops anything that requires `--from`.
    if (
        launch.from_spec is False
        and launch.runner is Runner.UVX
        and (
            launch.extras
            or (launch.entry_script and launch.entry_script != source.name)
        )
    ):
        ui.warning(
            "--no-from / from=false ignores extras and a differing entry-script; "
            f"the command becomes a bare `uv tool run {source.name}`."
        )

    if verbose:
        config = _sidecar_source(Path.cwd())
        ui.info(f"Config file: {config}" if config else "Config file: none")
        ui.info(f"Version: {source.version or 'latest'}")
        ui.info(
            "Resolved launch recipe — "
            f"runner={launch.runner}, entry_script={launch.entry_script}, "
            f"extras={launch.extras}, subcommand={launch.subcommand}, "
            f"transport={launch.transport}, from={launch.from_spec}"
        )

    if meta.detected_env_vars:
        ui.warning(
            f"Detected env vars: {', '.join(meta.detected_env_vars)} — "
            "these become user_config fields in the manifest."
        )
    manifest = generate_manifest(meta, source, mode, launch)

    with tempfile.TemporaryDirectory() as tmp_str:
        # Fixed working-dir name: the package name may contain '/' (scoped npm
        # packages like @scope/pkg) and never appears in the packed archive.
        bundle_dir = Path(tmp_str) / "bundle"
        bundle_dir.mkdir()
        if mode == BundleMode.COMPLETE:
            ui.section("Bundling dependencies")
            await bundle(source, meta, bundle_dir, mode, archive, launch)
            ui.success("Dependencies vendored")
        ui.section("Packing .mcpb")
        out_path = pack_mcpb(bundle_dir, manifest, output, mode)
        ui.success(f"Created {out_path}")


def _sidecar_source(cwd: Path) -> str | None:
    """Return a label for the config file that supplies launch overrides, if any.

    Mirrors :func:`mcp2mcpb.launch.load_sidecar`'s lookup order so ``--verbose``
    can report exactly which file was consulted.
    """
    dotfile = cwd / ".mcpb.toml"
    if dotfile.is_file():
        return ".mcpb.toml"
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        tool = parsed.get("tool", {})
        if isinstance(tool, dict) and isinstance(tool.get("mcpb"), dict):
            return "pyproject.toml [tool.mcpb]"
    return None


@app.command()
def unpack(
    bundle_path: Annotated[Path, typer.Argument(help="Path to the .mcpb file")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Directory to extract into (default: a sibling folder)",
        ),
    ] = None,
) -> None:
    """Extract a .mcpb bundle and print its manifest.json."""
    if not bundle_path.is_file():
        ui.error(f"no such file: {bundle_path}")
        raise typer.Exit(code=1)

    dest = output or bundle_path.parent / bundle_path.name.removesuffix(".mcpb")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path) as zf:
            zf.extractall(dest)  # noqa: S202  (bundles we produce; trusted input)
    except (OSError, zipfile.BadZipFile) as exc:
        ui.error(f"failed to unpack {bundle_path}: {exc}")
        raise typer.Exit(code=1) from None

    ui.success(f"Extracted to {dest}")
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        ui.warning("no manifest.json found in bundle")
        return
    ui.section("manifest.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(data, indent=2))


@app.command()
def sandbox(
    path: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "Path to .mcpb file, manifest.json, or directory containing "
                "manifest.json (defaults to auto-detected bundle in dist/ or "
                "current directory)"
            )
        ),
    ] = None,
    env_var: Annotated[
        list[str],
        typer.Option(
            "--env",
            "-e",
            help="Set env variables / user config values for sandbox in KEY=VALUE",
        ),
    ] = [],  # noqa: B006
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Startup/initialization timeout in seconds"),
    ] = 10.0,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show full JSON-RPC communication details",
        ),
    ] = False,
) -> None:
    """Simulate Claude Desktop running the MCP server under stdio and verify."""
    target_path = path
    if target_path is None:
        dist_dir = Path("dist")
        mcpb_files = list(dist_dir.glob("*.mcpb")) if dist_dir.is_dir() else []
        if len(mcpb_files) == 1:
            target_path = mcpb_files[0]
            ui.info(f"Auto-detected bundle: {target_path}")
        elif len(mcpb_files) > 1:
            ui.info("Multiple bundles found in dist/. Please specify which to run:")
            for f in mcpb_files:
                ui.info(f"  - {f}")
            raise typer.Exit(code=1)
        else:
            if Path("manifest.json").is_file():
                target_path = Path("manifest.json")
                ui.info("Auto-detected manifest.json in current directory.")
            else:
                ui.error(
                    "No .mcpb bundle or manifest.json found. Please specify the path."
                )
                raise typer.Exit(code=1)

    if not target_path.exists():
        ui.error(f"Path does not exist: {target_path}")
        raise typer.Exit(code=1)

    if target_path.is_file() and (
        target_path.suffix == ".mcpb" or zipfile.is_zipfile(target_path)
    ):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            try:
                with zipfile.ZipFile(target_path) as zf:
                    zf.extractall(tmp_dir)
            except Exception as e:
                ui.error(f"Failed to unpack bundle {target_path}: {e}")
                raise typer.Exit(code=1) from e

            manifest_path = tmp_dir / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                ui.error(f"Failed to read/parse manifest.json: {e}")
                raise typer.Exit(code=1) from e

            exit_code = asyncio.run(
                run_sandbox(
                    manifest=manifest,
                    dirname=tmp_dir,
                    env_var=env_var,
                    timeout=timeout,
                    verbose=verbose,
                )
            )
            if exit_code != 0:
                raise typer.Exit(code=exit_code)

    elif target_path.is_file() and target_path.name == "manifest.json":
        try:
            manifest = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as e:
            ui.error(f"Failed to read/parse manifest.json: {e}")
            raise typer.Exit(code=1) from e

        exit_code = asyncio.run(
            run_sandbox(
                manifest=manifest,
                dirname=target_path.parent.resolve(),
                env_var=env_var,
                timeout=timeout,
                verbose=verbose,
            )
        )
        if exit_code != 0:
            raise typer.Exit(code=exit_code)

    elif target_path.is_dir():
        manifest_path = target_path / "manifest.json"
        if not manifest_path.is_file():
            ui.error(f"No manifest.json found in directory: {target_path}")
            raise typer.Exit(code=1)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            ui.error(f"Failed to read/parse manifest.json: {e}")
            raise typer.Exit(code=1) from e

        exit_code = asyncio.run(
            run_sandbox(
                manifest=manifest,
                dirname=target_path.resolve(),
                env_var=env_var,
                timeout=timeout,
                verbose=verbose,
            )
        )
        if exit_code != 0:
            raise typer.Exit(code=exit_code)
    else:
        ui.error(
            f"Invalid target: {target_path}. Must be a .mcpb bundle, manifest.json, "
            "or directory containing manifest.json."
        )
        raise typer.Exit(code=1)


def _route(args: list[str]) -> list[str]:
    """Inject the implicit ``convert`` command for a bare ``mcp2mcpb <package>``.

    Leaves real subcommands (``convert``, ``unpack``) and option-led invocations
    (``--version``, ``--help``, no args) untouched.
    """
    if args and not args[0].startswith("-") and args[0] not in _COMMANDS:
        return ["convert", *args]
    return list(args)


def main(argv: list[str] | None = None) -> None:
    """Console entry point. Keeps the original ``mcp2mcpb <package> [options]``
    UX while routing real subcommands such as ``unpack``."""
    app(_route(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    main()
