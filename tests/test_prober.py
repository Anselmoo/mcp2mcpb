import time

import pytest

from mcp2mcpb import prober
from mcp2mcpb.models import LaunchOverrides, PackageSource, Registry, Transport
from mcp2mcpb.prober import parse_help_text, probe_help


def test_parse_transport_stdio_choice():
    text = "options:\n  --transport {stdio,http}  Transport to use\n"
    transport, sub = parse_help_text(text)
    assert transport is Transport.STDIO
    assert sub is None


def test_parse_no_transport_flag_means_none():
    transport, _ = parse_help_text("usage: srv [-h]\n  -h, --help  show help\n")
    assert transport is Transport.NONE


def test_parse_server_subcommand():
    text = "Commands:\n  start-mcp-server  Run the server\n  index  Build index\n"
    _, sub = parse_help_text(text)
    assert sub == ["start-mcp-server"]


async def test_probe_uses_first_clean_candidate():
    async def fake_runner(cmd):
        # first candidate exits clean and shows a transport flag
        return (0, "options:\n  --transport {stdio}\n")

    ov = await probe_help(
        PackageSource(registry=Registry.PYPI, name="p", version="1.0.0"),
        candidates=["p-server", "p"],
        extras=[],
        runner=fake_runner,
    )
    assert ov.entry_script == "p-server"
    assert ov.transport is Transport.STDIO


async def test_probe_disabled_returns_empty():
    ov = await probe_help(
        PackageSource(registry=Registry.PYPI, name="p"),
        candidates=["p"],
        extras=[],
        enabled=False,
    )
    assert ov == LaunchOverrides()


async def test_probe_runner_failure_returns_empty():
    async def boom(cmd):
        raise OSError("uv not found")

    ov = await probe_help(
        PackageSource(registry=Registry.PYPI, name="p"),
        candidates=["p"],
        extras=[],
        runner=boom,
    )
    assert ov == LaunchOverrides()


async def test_run_help_kills_and_raises_on_timeout(monkeypatch):
    # A real subprocess that never exits; with a tiny timeout the call must
    # raise promptly (process killed) rather than blocking for the full sleep.
    monkeypatch.setattr(prober, "_TIMEOUT", 0.2)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await prober._run_help(["sleep", "5"])
    assert time.monotonic() - start < 3.0


async def test_probe_continues_past_failing_candidate():
    calls: list[str] = []

    async def flaky_runner(cmd):
        # cmd is the help command list; the candidate script is the 5th element
        # for pypi (uv tool run --from <spec> <script> --help). Use call order.
        calls.append("call")
        if len(calls) == 1:
            raise OSError("first candidate blew up")
        return (0, "options:\n  --transport {stdio}\n")

    ov = await probe_help(
        PackageSource(registry=Registry.PYPI, name="p", version="1.0.0"),
        candidates=["p-first", "p-second"],
        extras=[],
        runner=flaky_runner,
    )
    assert ov.entry_script == "p-second"
