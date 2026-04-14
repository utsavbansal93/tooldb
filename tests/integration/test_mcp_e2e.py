"""End-to-end smoke test for the MCP server.

Starts the MCP server as a subprocess, sends the MCP initialize handshake
over stdio, verifies the tool list, and kills it cleanly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# All expected MCP tools (including the new assess_production_readiness)
EXPECTED_TOOLS = {
    "find_tool",
    "record_experience",
    "list_my_tools",
    "run_benchmark",
    "invoke_tool",
    "extract_tool_metadata",
    "delete_tool",
    "find_recipes",
    "suggest_recipe",
    "save_recipe",
    "get_stats",
    "assess_production_readiness",
}


@pytest.mark.integration
def test_mcp_server_starts_and_lists_tools() -> None:
    """Start MCP server subprocess, handshake, verify tools, kill."""
    uv_path = shutil.which("uv")
    if uv_path is None:
        pytest.skip("uv not found on PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"

        # Build stdin: initialize + initialized notification + tools/list
        messages = [
            json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.1.0"},
                },
            }),
            json.dumps({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }),
            json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }),
        ]
        stdin_data = "\n".join(messages) + "\n"

        import os

        env = {**os.environ, "TOOLDB_PATH": str(db_path)}

        proc = subprocess.run(
            [uv_path, "run", "tooldb-mcp"],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # Parse all JSON-RPC responses from stdout
        responses: list[dict] = []
        for line in proc.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        # Find the initialize response (id=1)
        init_resp = next((r for r in responses if r.get("id") == 1), None)
        assert init_resp is not None, (
            f"No initialize response found. Got {len(responses)} messages. "
            f"stderr: {proc.stderr[:500]}"
        )
        assert "result" in init_resp, f"Initialize failed: {init_resp}"

        # Find the tools/list response (id=2)
        tools_resp = next((r for r in responses if r.get("id") == 2), None)
        assert tools_resp is not None, (
            f"No tools/list response found. Got {len(responses)} messages. "
            f"stderr: {proc.stderr[:500]}"
        )
        assert "result" in tools_resp, f"tools/list failed: {tools_resp}"

        tools = tools_resp["result"].get("tools", [])
        tool_names = {t["name"] for t in tools}

        missing = EXPECTED_TOOLS - tool_names
        assert not missing, f"Missing MCP tools: {missing}. Got: {sorted(tool_names)}"
