"""Best-effort launch detection by running a candidate's `--help`."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable

from mcp2mcpb import ui
from mcp2mcpb.models import (
    LaunchOverrides,
    PackageSource,
    Registry,
    Transport,
)

_TIMEOUT = 15.0
_TRANSPORT_RE = re.compile(r"--transport[=\s]+\{?([a-z0-9,\-]+)", re.IGNORECASE)
_COMMANDS_RE = re.compile(r"^\s*([a-z][a-z0-9\-]*)\s{2,}\S", re.MULTILINE)
_SERVER_SUBCMD = ("server", "mcp", "start", "stdio")

# Injectable subprocess runner (named HelpRunner to avoid colliding with the
# Runner enum in models.py).
HelpRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]


def parse_help_text(text: str) -> tuple[Transport | None, list[str] | None]:
    transport: Transport | None
    m = _TRANSPORT_RE.search(text)
    if m is None:
        transport = Transport.NONE
    else:
        choices = m.group(1).split(",")
        transport = Transport.STDIO if "stdio" in choices else Transport.NONE

    subcommand: list[str] | None = None
    if re.search(r"^\s*Commands?:", text, re.MULTILINE | re.IGNORECASE):
        for name in _COMMANDS_RE.findall(text):
            if any(h in name.lower() for h in _SERVER_SUBCMD):
                subcommand = [name]
                break
    return transport, subcommand


async def _run_help(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        # Closed stdin: a stdio MCP server that ignores `--help` gets EOF and
        # exits instead of blocking on (and stealing) the parent's terminal.
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except TimeoutError:
        # Kill and reap so a hung probe never leaves an orphan running.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _help_command(source: PackageSource, script: str, extras: list[str]) -> list[str]:
    if source.registry is Registry.PYPI:
        extra = f"[{','.join(extras)}]" if extras else ""
        pin = f"=={source.version}" if source.version else ""
        pkg_spec = f"{source.name}{extra}{pin}"
        return ["uv", "tool", "run", "--from", pkg_spec, script, "--help"]
    return ["npx", "-y", source.pinned, "--help"]


async def probe_help(
    source: PackageSource,
    candidates: list[str],
    extras: list[str],
    *,
    enabled: bool = True,
    runner: HelpRunner = _run_help,
) -> LaunchOverrides:
    """Run candidate --help commands; return detected overrides. Never raises."""
    if not enabled or not candidates:
        return LaunchOverrides()
    for script in candidates:
        try:
            code, text = await runner(_help_command(source, script, extras))
            if code == 0 or "usage" in text.lower():
                transport, subcommand = parse_help_text(text)
                if len(candidates) > 1:
                    ui.warning(
                        f"auto-selected entry script {script!r}; "
                        "verify the generated command"
                    )
                return LaunchOverrides(
                    entry_script=script,
                    transport=transport,
                    subcommand=subcommand,
                )
        except (OSError, TimeoutError, ValueError):
            continue
    return LaunchOverrides()
