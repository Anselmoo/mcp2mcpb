"""Unit tests for the sandbox module."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mcp2mcpb.__main__ import app
from mcp2mcpb.sandbox import resolve_placeholders, run_sandbox


def test_resolve_placeholders() -> None:
    val = "run --from ${user_config.pkg_name} ${__dirname}/server.py"
    dirname = "/path/to/dir"
    user_config_vals = {"pkg_name": "my-pkg"}
    res = resolve_placeholders(val, dirname, user_config_vals)
    assert res == "run --from my-pkg /path/to/dir/server.py"


class MockStreamReader:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.idx = 0

    async def readline(self) -> bytes:
        if self.idx < len(self.lines):
            line = self.lines[self.idx]
            self.idx += 1
            return line.encode("utf-8") + b"\n"
        return b""

    def at_eof(self) -> bool:
        return self.idx >= len(self.lines)


class MockStreamWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class MockProcess:
    def __init__(
        self,
        stdout_responses: list[str],
        stderr_lines: list[str] | None = None,
    ) -> None:
        self.stdout = MockStreamReader(stdout_responses)
        self.stderr = MockStreamReader(stderr_lines or [])
        self.stdin = MockStreamWriter()
        self.returncode = 0

    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_run_sandbox_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_data = {
        "manifest_version": "0.4",
        "name": "test-server",
        "version": "1.0.0",
        "description": "Test",
        "author": {"name": "Test Author"},
        "server": {
            "type": "python",
            "entry_point": "server.py",
            "mcp_config": {
                "command": "python",
                "args": ["${__dirname}/server.py"],
                "env": {"API_KEY": "${user_config.api_key}"},
            },
        },
        "user_config": {
            "api_key": {
                "type": "string",
                "title": "API_KEY",
                "description": "API Key",
                "required": True,
            }
        },
    }

    responses = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "mock-server", "version": "1.0"},
                },
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "test_tool",
                            "description": "A test tool",
                            "inputSchema": {},
                        }
                    ]
                },
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {}}),
    ]

    mock_proc = MockProcess(responses)

    async def mock_create_subprocess_exec(
        *args: Any,
        **kwargs: Any,
    ) -> MockProcess:
        assert args[0] == "python"
        assert args[1] == f"{tmp_path}/server.py"
        assert kwargs["env"]["API_KEY"] == "secret-key"
        return mock_proc

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        mock_create_subprocess_exec,
    )

    exit_code = await run_sandbox(
        manifest=manifest_data,
        dirname=tmp_path,
        env_var=["api_key=secret-key"],
        timeout=2.0,
        verbose=True,
    )

    assert exit_code == 0
    # verify requests were sent
    sent_msgs = [json.loads(d.decode("utf-8").strip()) for d in mock_proc.stdin.written]
    assert len(sent_msgs) == 3
    assert sent_msgs[0]["method"] == "initialize"
    assert sent_msgs[1]["method"] == "notifications/initialized"
    assert sent_msgs[2]["method"] == "tools/list"


@pytest.mark.asyncio
async def test_run_sandbox_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_data = {
        "manifest_version": "0.4",
        "name": "test-server",
        "version": "1.0.0",
        "description": "Test",
        "author": {"name": "Test Author"},
        "server": {
            "type": "python",
            "entry_point": "server.py",
            "mcp_config": {
                "command": "python",
                "args": [],
            },
        },
    }

    # Empty stdout response leads to timeout
    mock_proc = MockProcess([])

    async def mock_create_subprocess_exec(
        *args: Any,
        **kwargs: Any,
    ) -> MockProcess:
        return mock_proc

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        mock_create_subprocess_exec,
    )

    exit_code = await run_sandbox(
        manifest=manifest_data, dirname=tmp_path, env_var=[], timeout=0.1, verbose=False
    )

    assert exit_code == 1


def test_sandbox_cli_target_missing() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["sandbox", "/no/such/file.mcpb"])
    assert result.exit_code == 1
    assert "Path does not exist" in result.output
