"""Branch coverage for sandbox.py helpers and run_sandbox edge cases.

Fully offline: no real subprocess is launched — asyncio.create_subprocess_exec
is monkeypatched to a mock process throughout.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

from mcp2mcpb import sandbox
from mcp2mcpb.sandbox import (
    read_messages,
    read_stderr,
    run_sandbox,
    wait_for_response,
)

# ── stream helpers ────────────────────────────────────────────────────────────


class _LineReader:
    """A StreamReader-like object that never reports EOF (forces the break path)."""

    def __init__(self, lines: list[str], *, raise_at: int | None = None) -> None:
        self._lines = [line.encode("utf-8") + b"\n" for line in lines]
        self.i = 0
        self.raise_at = raise_at

    async def readline(self) -> bytes:
        if self.raise_at is not None and self.i == self.raise_at:
            self.i += 1
            raise RuntimeError("read failure")
        if self.i < len(self._lines):
            chunk = self._lines[self.i]
            self.i += 1
            return chunk
        return b""

    def at_eof(self) -> bool:
        return False


async def test_read_messages_queues_json_and_warns_on_garbage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = _LineReader(["", '{"id": 1}', "not-json{"])
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await read_messages(cast("asyncio.StreamReader", reader), queue, verbose=True)
    assert queue.qsize() == 1
    assert queue.get_nowait() == {"id": 1}
    assert "non-JSON" in capsys.readouterr().out


async def test_read_messages_swallows_reader_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = _LineReader([], raise_at=0)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await read_messages(cast("asyncio.StreamReader", reader), queue, verbose=True)
    assert "Error reading stdout" in capsys.readouterr().out


async def test_read_stderr_prints_lines(capsys: pytest.CaptureFixture[str]) -> None:
    reader = _LineReader(["boom", "", "kaboom"])
    await read_stderr(cast("asyncio.StreamReader", reader))
    out = capsys.readouterr().out
    assert "boom" in out
    assert "kaboom" in out


async def test_wait_for_response_handles_notifications_then_returns() -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await queue.put(
        {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"level": "warning", "message": "heads up"},
        }
    )
    await queue.put({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    msg = await wait_for_response(queue, 7, timeout=1.0)
    assert msg["result"] == {"ok": True}


async def test_wait_for_response_times_out_on_empty_queue() -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    with pytest.raises(TimeoutError):
        await wait_for_response(queue, 1, timeout=0.05)


async def test_wait_for_response_zero_timeout_raises() -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    with pytest.raises(TimeoutError):
        await wait_for_response(queue, 1, timeout=0.0)


# ── run_sandbox: mock process ─────────────────────────────────────────────────


class _Reader:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [line.encode("utf-8") + b"\n" for line in lines]
        self.i = 0

    async def readline(self) -> bytes:
        if self.i < len(self._lines):
            chunk = self._lines[self.i]
            self.i += 1
            return chunk
        return b""

    def at_eof(self) -> bool:
        return self.i >= len(self._lines)


class _Writer:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _Proc:
    def __init__(
        self,
        responses: list[str],
        *,
        returncode: int | None = 0,
        no_stdout: bool = False,
        wait_raises: bool = False,
    ) -> None:
        self.stdout = None if no_stdout else _Reader(responses)
        self.stderr = _Reader([])
        self.stdin = _Writer()
        self.returncode = returncode
        self._wait_raises = wait_raises

    def terminate(self) -> None:
        pass

    async def wait(self) -> int:
        if self._wait_raises:
            raise TimeoutError
        return self.returncode or 0


def _init_ok(tools: list[dict[str, Any]] | None = None) -> list[str]:
    return [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "srv", "version": "1.0"},
                },
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": tools if tools is not None else []},
            }
        ),
    ]


def _manifest(
    *,
    command: str | None = "python",
    env: dict[str, str] | None = None,
    user_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mcp_config: dict[str, Any] = {"args": [], "env": env or {}}
    if command is not None:
        mcp_config["command"] = command
    manifest: dict[str, Any] = {"server": {"mcp_config": mcp_config}}
    if user_config is not None:
        manifest["user_config"] = user_config
    return manifest


def _patch_proc(monkeypatch: pytest.MonkeyPatch, proc: _Proc) -> None:
    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


async def _run(manifest: dict[str, Any], **kwargs: Any) -> int:
    return await run_sandbox(
        manifest=manifest,
        dirname=Path(tempfile.gettempdir()),
        env_var=kwargs.pop("env_var", []),
        timeout=kwargs.pop("timeout", 1.0),
        verbose=kwargs.pop("verbose", False),
    )


async def test_run_sandbox_no_command_returns_1() -> None:
    assert await _run(_manifest(command=None)) == 1


async def test_run_sandbox_invalid_env_format_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    code = await _run(_manifest(), env_var=["NOEQUALS"], verbose=True)
    assert code == 0
    assert "Invalid env format" in capsys.readouterr().out


async def test_run_sandbox_uses_existing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox.os, "environ", {"API_KEY": "from-env"})
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    manifest = _manifest(
        env={"API_KEY": "${user_config.api_key}"},
        user_config={"api_key": {"type": "string", "title": "API_KEY"}},
    )
    assert await _run(manifest) == 0


async def test_run_sandbox_prompts_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Stdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sandbox.sys, "stdin", _Stdin())
    monkeypatch.setattr(sandbox.typer, "prompt", lambda *a, **k: "typed-value")
    monkeypatch.setattr(sandbox.os, "environ", {})
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    manifest = _manifest(
        user_config={
            "api_key": {
                "type": "string",
                "title": "API_KEY",
                "description": "the key",
                "required": True,
            }
        },
    )
    assert await _run(manifest) == 0


async def test_run_sandbox_required_prompt_empty_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Stdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sandbox.sys, "stdin", _Stdin())
    monkeypatch.setattr(sandbox.typer, "prompt", lambda *a, **k: "")
    monkeypatch.setattr(sandbox.os, "environ", {})
    manifest = _manifest(
        user_config={"api_key": {"title": "API_KEY", "required": True}},
    )
    assert await _run(manifest) == 1


async def test_run_sandbox_required_non_tty_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Stdin:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sandbox.sys, "stdin", _Stdin())
    monkeypatch.setattr(sandbox.os, "environ", {})
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    manifest = _manifest(
        user_config={"api_key": {"title": "API_KEY", "required": True}},
    )
    code = await _run(manifest)
    assert code == 0
    assert "not a TTY" in capsys.readouterr().out


async def test_run_sandbox_masks_sensitive_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sandbox.os, "environ", {})
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    manifest = _manifest(
        env={"API_KEY": "${user_config.api_key}"},
        user_config={"api_key": {"title": "API_KEY", "sensitive": True}},
    )
    code = await _run(manifest, env_var=["api_key=supersecret"])
    out = capsys.readouterr().out
    assert code == 0
    assert "********" in out
    assert "supersecret" not in out


async def test_run_sandbox_subprocess_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> _Proc:
        raise OSError("exec failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    assert await _run(_manifest()) == 1


async def test_run_sandbox_pipes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proc(monkeypatch, _Proc(_init_ok(), no_stdout=True))
    assert await _run(_manifest()) == 1


async def test_run_sandbox_init_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "no"}})
    ]
    _patch_proc(monkeypatch, _Proc(responses))
    assert await _run(_manifest()) == 1


async def test_run_sandbox_init_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sandbox, "wait_for_response", boom)
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    assert await _run(_manifest()) == 1


async def test_run_sandbox_tools_error_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"message": "nope"}}),
    ]
    _patch_proc(monkeypatch, _Proc(responses))
    code = await _run(_manifest())
    assert code == 0
    assert "tool listing error" in capsys.readouterr().err


async def test_run_sandbox_tools_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the init response is provided; tools/list never gets a reply.
    responses = [json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}})]
    _patch_proc(monkeypatch, _Proc(responses))
    assert await _run(_manifest(), timeout=0.2) == 0


async def test_run_sandbox_tools_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": 1, "result": {"serverInfo": {}}}
        raise RuntimeError("tools boom")

    monkeypatch.setattr(sandbox, "wait_for_response", flaky)
    _patch_proc(monkeypatch, _Proc(_init_ok()))
    assert await _run(_manifest()) == 0


async def test_run_sandbox_truncates_long_tool_description(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    long_desc = "x" * 200
    _patch_proc(
        monkeypatch,
        _Proc(_init_ok(tools=[{"name": "big", "description": long_desc}])),
    )
    assert await _run(_manifest()) == 0
    assert "..." in capsys.readouterr().out


async def test_run_sandbox_terminates_unresponsive_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _Proc(_init_ok(), returncode=None, wait_raises=True)
    _patch_proc(monkeypatch, proc)
    assert await _run(_manifest()) == 1
