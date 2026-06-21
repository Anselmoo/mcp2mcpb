from __future__ import annotations

import os
import subprocess
import sys

import pytest

from mcp2mcpb._ci_args import build_cli_args


def _base(**over: str) -> dict[str, str]:
    env = {
        "PACKAGE": "demo-pkg",
        "REGISTRY": "pypi",
        "MODE": "reference",
        "OUTPUT_DIR": "dist",
        "FROM_DIST": "",
        "VERSION": "",
        "GITHUB_REF_NAME": "",
        "RUNNER": "",
        "ENTRY_SCRIPT": "",
        "EXTRAS": "",
        "SUBCOMMAND": "",
        "TRANSPORT": "",
        "NO_PROBE": "false",
    }
    env.update(over)
    return env


def test_base_args_always_present() -> None:
    args = build_cli_args(_base(VERSION="1.0.0"))
    assert args[0] == "demo-pkg"
    assert "--registry" in args and args[args.index("--registry") + 1] == "pypi"
    assert "--mode" in args and args[args.index("--mode") + 1] == "reference"
    assert "--output" in args and args[args.index("--output") + 1] == "dist"


def test_registry_mode_explicit_version() -> None:
    args = build_cli_args(_base(VERSION="1.2.0"))
    assert "--pin" in args and args[args.index("--pin") + 1] == "1.2.0"
    assert "--from-dist" not in args


def test_registry_mode_version_from_tag() -> None:
    args = build_cli_args(_base(GITHUB_REF_NAME="v1.2.0"))
    assert args[args.index("--pin") + 1] == "1.2.0"


def test_registry_mode_branch_ref_raises() -> None:
    with pytest.raises(ValueError, match="could not resolve a release version"):
        build_cli_args(_base(GITHUB_REF_NAME="main"))


def test_registry_mode_no_version_raises() -> None:
    with pytest.raises(ValueError, match="could not resolve a release version"):
        build_cli_args(_base())


def test_local_mode_omits_pin() -> None:
    args = build_cli_args(_base(FROM_DIST="dist", GITHUB_REF_NAME="main"))
    assert "--from-dist" in args and args[args.index("--from-dist") + 1] == "dist"
    assert "--pin" not in args


def test_local_mode_ignores_stray_version() -> None:
    args = build_cli_args(_base(FROM_DIST="dist", VERSION="9.9.9"))
    assert "--pin" not in args
    assert args[args.index("--from-dist") + 1] == "dist"


def test_optional_flags_appended_once() -> None:
    args = build_cli_args(
        _base(
            VERSION="1.0.0",
            RUNNER="uvx",
            ENTRY_SCRIPT="srv",
            EXTRAS="mcp extra2",
            SUBCOMMAND="start-mcp-server",
            TRANSPORT="stdio",
            NO_PROBE="true",
        )
    )
    assert args.count("--runner") == 1 and args[args.index("--runner") + 1] == "uvx"
    assert args[args.index("--entry-script") + 1] == "srv"
    assert args.count("--extra") == 2
    assert args[args.index("--subcommand") + 1] == "start-mcp-server"
    assert args[args.index("--transport") + 1] == "stdio"
    assert "--no-probe" in args


def test_empty_optionals_append_nothing() -> None:
    args = build_cli_args(_base(VERSION="1.0.0"))
    for flag in (
        "--runner",
        "--entry-script",
        "--extra",
        "--subcommand",
        "--transport",
        "--no-probe",
    ):
        assert flag not in args


def test_module_entrypoint_prints_one_arg_per_line() -> None:
    env = _base(VERSION="1.0.0", RUNNER="uvx")
    proc = subprocess.run(
        [sys.executable, "-m", "mcp2mcpb._ci_args"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "demo-pkg"
    assert "--runner" in lines and "uvx" in lines


def test_module_entrypoint_exits_nonzero_on_unresolvable() -> None:
    env = _base(GITHUB_REF_NAME="main")
    proc = subprocess.run(
        [sys.executable, "-m", "mcp2mcpb._ci_args"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "could not resolve a release version" in proc.stderr
