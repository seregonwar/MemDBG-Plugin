#!/usr/bin/env python3
# MemDBG MCP Server - MCP stdio server exposing MemDBG payload APIs.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage:
#   # Direct (loads context from environment or argv)
#   python3 -m mcp_server.server
#
#   # With explicit context file
#   MEMDBG_CONTEXT=/path/to/context.json python3 -m mcp_server.server
#
#   # From an MCP client (e.g. Claude Desktop, Continue, etc.)
#   python3 -m mcp_server.server --stdio

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure the SDK is importable
_sdk_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sdk_dir not in sys.path:
    sys.path.insert(0, _sdk_dir)

from memdbg import MemDBG, MemDBGError, load_context

from .protocol import MCPMessage, read_message, write_message
from .tools import MEMDBG_TOOLS, execute_tool


class MCPServer:
    """MCP stdio server that exposes MemDBG payload tools.

    Implements the Model Context Protocol (2024-11-05) over stdin/stdout
    with Content-Length framed JSON-RPC 2.0 messages.
    """

    SERVER_NAME = "memdbg-mcp-server"
    SERVER_VERSION = "1.0.0"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, api: Optional[MemDBG] = None) -> None:
        self._api: Optional[MemDBG] = api
        self._initialized = False
        self._next_id = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_stdio(self) -> None:
        """Run the MCP server in stdio mode (blocking loop)."""
        while True:
            raw = read_message()
            if raw is None:
                break
            try:
                msg = MCPMessage.from_dict(raw)
                self._handle_message(msg)
            except Exception as exc:
                error_msg = MCPMessage.error_response(
                    raw.get("id", 0), -32603, f"Internal error: {exc}"
                )
                write_message(error_msg.to_dict())

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    def _handle_message(self, msg: MCPMessage) -> None:
        if msg.is_notification:
            self._handle_notification(msg)
        elif msg.is_request:
            self._handle_request(msg)
        # Responses from C++ (e.g. ui_event notifications) are handled here

    def _handle_notification(self, msg: MCPMessage) -> None:
        method = msg.method
        params = msg.params

        if method == "initialized":
            return  # client acknowledgment, no response needed

        elif method == "exit":
            # Graceful shutdown requested by C++ frontend
            raise SystemExit(0)

        # Forward unknown notifications could be UI events from frontend
        # They are handled by the plugin logic, not the server core.

    def _handle_request(self, msg: MCPMessage) -> None:
        method = msg.method
        params = msg.params
        msg_id = msg.id or 0

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
                write_message(MCPMessage.response(msg_id, result).to_dict())

            elif method == "tools/list":
                result = self._handle_tools_list()
                write_message(MCPMessage.response(msg_id, result).to_dict())

            elif method == "tools/call":
                result = self._handle_tools_call(params)
                write_message(MCPMessage.response(msg_id, result).to_dict())

            elif method == "ping":
                write_message(MCPMessage.response(msg_id, {}).to_dict())

            else:
                write_message(
                    MCPMessage.error_response(
                        msg_id, -32601, f"Method not found: {method}"
                    ).to_dict()
                )

        except MemDBGError as exc:
            write_message(
                MCPMessage.error_response(msg_id, -32000, str(exc)).to_dict()
            )
        except Exception as exc:
            write_message(
                MCPMessage.error_response(msg_id, -32603, str(exc)).to_dict()
            )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._initialized = True
        client_info = params.get("clientInfo", {})
        return {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": self.SERVER_NAME,
                "version": self.SERVER_VERSION,
            },
        }

    def _handle_tools_list(self) -> Dict[str, Any]:
        return {"tools": MEMDBG_TOOLS}

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not tool_name:
            raise ValueError("Missing tool name")

        # Ensure API is available
        if self._api is None:
            try:
                self._api = MemDBG.from_context()
            except MemDBGError as exc:
                raise MemDBGError(
                    f"Cannot connect to MemDBG payload: {exc}. "
                    "Ensure MEMDBG_CONTEXT is set and the payload is running."
                )

        result = execute_tool(self._api, tool_name, arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2, default=str),
                }
            ]
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the MemDBG MCP server from the command line."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print("MemDBG MCP Server")
        print(f"  Version: {MCPServer.SERVER_VERSION}")
        print(f"  Protocol: {MCPServer.PROTOCOL_VERSION}")
        print()
        print("Usage:")
        print("  python3 -m mcp_server.server [--stdio]")
        print()
        print("The server reads/writes JSON-RPC 2.0 messages over stdin/stdout")
        print("using Content-Length framing.")
        print()
        print("Set MEMDBG_CONTEXT to a context.json file produced by the MemDBG")
        print("frontend, or pass it as the first positional argument.")
        return 0

    try:
        api = MemDBG.from_context()
    except MemDBGError:
        api = None  # Will be lazily initialized on first tool call

    server = MCPServer(api)
    server.run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
