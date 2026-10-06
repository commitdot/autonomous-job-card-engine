"""
AJEMcpClient
============
A lightweight subprocess-based MCP client that the AJE adapter layer uses to
call the AJE MCP server tools during gap analysis.

It speaks the MCP JSON-RPC protocol over stdio, launching the server as a
child process — no network port, no daemon setup required.

Usage
-----
    from src.mcp_client import AJEMcpClient

    mcp = AJEMcpClient(
        server_script="mcp-server/src/aje_mcp_server/server.py",
        env={
            "GITHUB_TOKEN":  "...",
            "GITHUB_REPO":   "commitdot/autonomous-job-card-engine",
            "AJE_WORKSPACE": "/absolute/path/to/workspace",
        }
    )

    # Collect all signals in one call
    context = mcp.collect_gap_signals()

    # Or call individual tools
    issues = mcp.call_tool("get_open_issues", {"labels": "bug", "max_results": 10})
"""

import json
import os
import subprocess
import sys
import time
from typing import Any


class AJEMcpClient:
    """
    Spawns the AJE MCP server as a subprocess and communicates with it over
    JSON-RPC 2.0 via stdin/stdout (MCP stdio transport).

    The client is lazy — it only starts the server process on the first tool
    call and keeps it alive for the lifetime of the AJEMcpClient instance.
    Call `close()` (or use as a context manager) to terminate the server.
    """

    def __init__(
        self,
        server_script: str = None,
        env: dict[str, str] = None,
    ):
        """
        Args:
            server_script: Path to the MCP server Python script.
                           Defaults to mcp-server/src/aje_mcp_server/server.py
                           relative to this file's package root.
            env:           Extra environment variables to pass to the server process.
                           GITHUB_TOKEN, GITHUB_REPO, and AJE_WORKSPACE are the
                           most important ones. Falls back to os.environ if not set.
        """
        if server_script is None:
            # Default: resolve relative to this file's location (src/)
            pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            server_script = os.path.join(
                pkg_root, "mcp-server", "src", "aje_mcp_server", "server.py"
            )

        self.server_script = server_script
        self._env = {**os.environ, **(env or {})}
        self._proc: subprocess.Popen | None = None
        self._request_id = 0

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call_tool(self, tool_name: str, arguments: dict[str, Any] = None) -> Any:
        """
        Call a single MCP tool by name and return its parsed result.

        Returns the text content of the first response item, or a dict with
        an "error" key if the call failed.
        """
        self._ensure_server()

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        try:
            self._proc.stdin.write(json.dumps(request) + "\n")
            self._proc.stdin.flush()

            # Read response lines until we find the matching id
            deadline = time.time() + 30
            while time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                response = json.loads(line.strip())
                if response.get("id") == self._request_id:
                    break
            else:
                return {"error": f"Timeout waiting for response to tool '{tool_name}'"}

            if "error" in response:
                return {"error": response["error"].get("message", "Unknown MCP error")}

            result = response.get("result", {})
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                try:
                    return json.loads(content[0]["text"])
                except json.JSONDecodeError:
                    return content[0]["text"]
            return result

        except Exception as e:
            return {"error": str(e)}

    def collect_gap_signals(self) -> dict[str, Any]:
        """
        Calls all available intelligence tools and returns a single consolidated
        dict. Safe to call even when individual tools fail — failures are returned
        inline with an "error" key so the adapter can still build a partial prompt.
        """
        print("  [MCP] Collecting live gap signals from repository...")

        signals: dict[str, Any] = {}

        tool_calls = [
            ("open_issues",     "get_open_issues",       {}),
            ("failing_tests",   "get_failing_tests",     {}),
            ("coverage",        "get_coverage_report",   {}),
            ("outdated_deps",   "get_outdated_deps",     {}),
            ("security_alerts", "get_security_alerts",   {}),
            ("todo_comments",   "scan_todo_comments",    {}),
            ("pr_feedback",     "get_pr_review_feedback",{}),
        ]

        for signal_key, tool_name, args in tool_calls:
            print(f"  [MCP] → {tool_name}...")
            signals[signal_key] = self.call_tool(tool_name, args)

        return signals

    def close(self):
        """Terminate the MCP server subprocess if it is running."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
        self._proc = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_server(self):
        """Start the MCP server subprocess on first use."""
        if self._proc and self._proc.poll() is None:
            return  # already running

        if not os.path.exists(self.server_script):
            raise FileNotFoundError(
                f"AJE MCP server script not found: {self.server_script}\n"
                "Run: pip install -e mcp-server/"
            )

        self._proc = subprocess.Popen(
            [sys.executable, self.server_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # server logs go to stderr — don't mix with JSON-RPC
            text=True,
            env=self._env,
            bufsize=1,
        )

        # Send MCP initialize handshake
        self._request_id += 1
        init_request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "aje-engine", "version": "0.1.0"},
            },
        }
        self._proc.stdin.write(json.dumps(init_request) + "\n")
        self._proc.stdin.flush()

        # Wait for initialized response
        deadline = time.time() + 10
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if line:
                resp = json.loads(line.strip())
                if resp.get("id") == self._request_id:
                    break
            time.sleep(0.05)

        # Send initialized notification
        self._proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        self._proc.stdin.flush()
        print(f"  [MCP] Server started — {self.server_script}")
