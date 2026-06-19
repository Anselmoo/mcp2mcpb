"""Sandbox simulation of Claude Desktop launching the MCP server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import typer

from mcp2mcpb import __version__, ui


def resolve_placeholders(
    val: str,
    dirname: str,
    user_config_vals: dict[str, str],
) -> str:
    """Resolve ${__dirname} and ${user_config.key} in strings."""
    # Replace ${__dirname}
    val = val.replace("${__dirname}", dirname)

    # Replace ${user_config.key}
    def replace_config(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        return user_config_vals.get(key, "")

    val = re.sub(r"\$\{user_config\.([a-zA-Z0-9_-]+)\}", replace_config, val)
    return val


async def read_messages(
    reader: asyncio.StreamReader,
    msg_queue: asyncio.Queue[dict[str, Any]],
    verbose: bool,
) -> None:
    """Read lines from stdout and queue them as JSON-RPC messages."""
    try:
        while not reader.at_eof():
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if verbose:
                print(f"\033[90m[JSON-RPC Recv]\033[0m {line}")
            try:
                msg = json.loads(line)
                await msg_queue.put(msg)
            except json.JSONDecodeError:
                ui.warning(
                    f"Server wrote non-JSON output to stdout: {line!r} "
                    "(This violates the MCP protocol!)"
                )
    except Exception as e:
        if verbose:
            ui.warning(f"Error reading stdout: {e}")


async def read_stderr(reader: asyncio.StreamReader) -> None:
    """Read lines from stderr and print them to stdout with a label."""
    with contextlib.suppress(Exception):
        while not reader.at_eof():
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line:
                print(f"\033[91m[Server Stderr]\033[0m {line}")


async def wait_for_response(
    msg_queue: asyncio.Queue[dict[str, Any]],
    req_id: int,
    timeout: float,  # noqa: ASYNC109
) -> dict[str, Any]:
    """Wait for a response matching the given JSON-RPC request ID."""
    start_time = asyncio.get_event_loop().time()
    while True:
        elapsed = asyncio.get_event_loop().time() - start_time
        remaining = timeout - elapsed
        if remaining <= 0:
            raise TimeoutError()
        try:
            msg = await asyncio.wait_for(msg_queue.get(), timeout=remaining)
            if msg.get("id") == req_id:
                return msg
            # Handle server-to-client notifications/requests
            if "method" in msg:
                method = msg["method"]
                if method in ("notifications/message", "notifications/log"):
                    params = msg.get("params", {})
                    level = params.get("level", "info")
                    text = params.get("message", "")
                    print(f"\033[94m[{level.upper()} from Server]\033[0m {text}")
        except TimeoutError as err:
            raise TimeoutError() from err


async def run_sandbox(
    manifest: dict[str, Any],
    dirname: Path,
    env_var: list[str],
    timeout: float,  # noqa: ASYNC109
    verbose: bool,
) -> int:
    """Run the sandbox test using the manifest and dirname."""
    server_cfg = manifest.get("server", {})
    mcp_cfg = server_cfg.get("mcp_config", {})
    command = mcp_cfg.get("command")
    args = mcp_cfg.get("args", [])
    env = mcp_cfg.get("env", {})
    user_config = manifest.get("user_config", {})

    if not command:
        ui.error("No launch command specified in manifest.json")
        return 1

    # Resolve user configurations
    user_config_vals: dict[str, str] = {}
    for item in env_var:
        if "=" in item:
            k, v = item.split("=", 1)
            user_config_vals[k.strip().lower()] = v
            os.environ[k.strip()] = v
        else:
            ui.warning(f"Invalid env format {item!r}. Expected KEY=VALUE.")

    for key, field in user_config.items():
        key_lower = key.lower()
        if key_lower in user_config_vals:
            continue

        # Check existing environment
        env_val = os.environ.get(key) or os.environ.get(key.upper())
        if env_val is not None:
            user_config_vals[key_lower] = env_val
            continue

        # Prompt if TTY
        if sys.stdin.isatty():
            desc = field.get("description", "")
            title = field.get("title", key)
            req = field.get("required", False)
            sensitive = field.get("sensitive", False)

            prompt_msg = f"Value for {title}"
            if desc:
                prompt_msg += f" ({desc})"
            if req:
                prompt_msg += " [REQUIRED]"

            val = typer.prompt(prompt_msg, default="", hide_input=sensitive)
            if req and not val:
                ui.error(f"Config field '{key}' is required but not provided.")
                return 1
            user_config_vals[key_lower] = val
        else:
            req = field.get("required", False)
            if req:
                ui.warning(
                    f"Config field '{key}' is required but stdin is not a TTY. "
                    "Using empty value."
                )
            user_config_vals[key_lower] = ""

    # Resolve placeholders in command, args, and env
    resolved_command = resolve_placeholders(command, str(dirname), user_config_vals)
    resolved_args = [
        resolve_placeholders(arg, str(dirname), user_config_vals) for arg in args
    ]
    resolved_env = {
        resolve_placeholders(k, str(dirname), user_config_vals): resolve_placeholders(
            v, str(dirname), user_config_vals
        )
        for k, v in env.items()
    }

    # Merge environment variables
    proc_env = dict(os.environ)
    for k, v in resolved_env.items():
        proc_env[k] = v

    ui.section("Sandbox Run Config")
    ui.info(f"Executable: {resolved_command}")
    ui.info(f"Arguments:  {' '.join(resolved_args)}")

    # Hide sensitive env variables
    masked_env = {}
    for k, v in resolved_env.items():
        # Check if the variable is marked as sensitive
        is_sensitive = False
        for cfg_k, cfg_field in user_config.items():
            if cfg_field.get("sensitive") and cfg_k.upper() == k.upper():
                is_sensitive = True
                break
        if is_sensitive:
            masked_env[k] = "********"
        else:
            masked_env[k] = v
    if masked_env:
        ui.info(f"Environment overrides: {masked_env}")

    ui.section("Launching Process")
    try:
        proc = await asyncio.create_subprocess_exec(
            resolved_command,
            *resolved_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
    except Exception as e:
        ui.error(f"Failed to start subprocess: {e}")
        return 1

    msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    if proc.stdout is None or proc.stderr is None or proc.stdin is None:
        ui.error("Subprocess pipes were not correctly initialized.")
        return 1

    stdout_task = asyncio.create_task(read_messages(proc.stdout, msg_queue, verbose))
    stderr_task = asyncio.create_task(read_stderr(proc.stderr))

    exit_code = 0
    try:
        # Step 1: Initialize handshake
        ui.info("Sending JSON-RPC 'initialize' request...")
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mcp2mcpb-sandbox",
                    "version": __version__,
                },
            },
        }
        if verbose:
            print(f"\033[90m[JSON-RPC Send]\033[0m {json.dumps(init_req)}")
        proc.stdin.write(json.dumps(init_req).encode("utf-8") + b"\n")
        await proc.stdin.drain()

        # Wait for initialize response
        try:
            init_res = await wait_for_response(msg_queue, 1, timeout)
            if "error" in init_res:
                ui.error(f"Server returned initialization error: {init_res['error']}")
                exit_code = 1
            else:
                res_val = init_res.get("result", {})
                server_info = res_val.get("serverInfo", {})
                ui.success("Server successfully initialized!")
                ui.info(
                    f"Server Name: {server_info.get('name', 'Unknown')}, "
                    f"Version: {server_info.get('version', 'Unknown')}"
                )
                proto_ver = res_val.get("protocolVersion", "Unknown")
                ui.info(f"Protocol Version: {proto_ver}")
        except TimeoutError:
            ui.error(f"Server initialization timed out after {timeout} seconds.")
            exit_code = 1
        except Exception as e:
            ui.error(f"Error during initialization: {e}")
            exit_code = 1

        if exit_code == 0:
            # Step 2: Send initialized notification
            init_notif = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            if verbose:
                print(f"\033[90m[JSON-RPC Send]\033[0m {json.dumps(init_notif)}")
            proc.stdin.write(json.dumps(init_notif).encode("utf-8") + b"\n")
            await proc.stdin.drain()

            # Step 3: List Tools
            ui.info("Requesting tool list...")
            tools_req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            }
            if verbose:
                print(f"\033[90m[JSON-RPC Send]\033[0m {json.dumps(tools_req)}")
            proc.stdin.write(json.dumps(tools_req).encode("utf-8") + b"\n")
            await proc.stdin.drain()

            try:
                tools_res = await wait_for_response(msg_queue, 2, timeout)
                if "error" in tools_res:
                    err_msg = tools_res["error"]
                    ui.error(f"Server returned tool listing error: {err_msg}")
                else:
                    tools = tools_res.get("result", {}).get("tools", [])
                    ui.success(f"Discovered {len(tools)} tools:")
                    for tool in tools:
                        desc = tool.get("description", "No description")
                        # Keep description short
                        if len(desc) > 80:
                            desc = desc[:77] + "..."
                        print(f"  - \033[1m{tool.get('name')}\033[0m: {desc}")
            except TimeoutError:
                ui.error("Server tool listing timed out.")
            except Exception as e:
                ui.error(f"Error during tool listing: {e}")

        # Step 4: Clean Shutdown
        ui.info("Closing stdin to stop server...")
        with contextlib.suppress(Exception):
            proc.stdin.close()
            await proc.stdin.wait_closed()

    finally:
        # Stop stdout/stderr reading
        stdout_task.cancel()
        stderr_task.cancel()

        # Check process state
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                ui.warning("Server process did not exit on EOF. Terminating...")
                with contextlib.suppress(Exception):
                    proc.terminate()
                    await proc.wait()

        code = proc.returncode
        if code in (0, 143, -15) and exit_code == 0:
            ui.success("Sandbox run completed successfully.")
        else:
            ui.error(
                f"Sandbox run finished with exit status (process code: {code}, "
                f"run code: {exit_code})"
            )
            if exit_code == 0:
                exit_code = code if code is not None else 1

    return exit_code
