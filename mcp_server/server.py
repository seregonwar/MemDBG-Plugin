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
import select
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure the SDK is importable
_sdk_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sdk_dir not in sys.path:
    sys.path.insert(0, _sdk_dir)

from memdbg import MemDBG, MemDBGError, load_context

from .protocol import MCPMessage, read_message, write_message
from .tools import MEMDBG_TOOLS, execute_tool


class _SocketWriter:
    """Minimal file-like wrapper around a socket for write_message()."""
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self.buffer = self  # protocol.py accesses stdout.buffer

    def write(self, data: bytes) -> int:
        self._sock.sendall(data)
        return len(data)

    def flush(self) -> None:
        pass


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
        self._api_lock = threading.Lock()

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

    def run_tcp(self, host: str = "127.0.0.1", port: int = 9000) -> None:
        """Run the MCP server in TCP mode (blocking loop).

        Listens on a TCP socket and accepts multiple client connections.
        Each connection is handled in its own thread via _handle_client().
        Logs connections, requests, and responses to stderr for the GUI.
        """
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((host, port))
        server_sock.listen(5)
        server_sock.settimeout(1.0)

        print(f"[MCP] TCP server listening on {host}:{port}", file=sys.stderr, flush=True)

        try:
            while True:
                try:
                    client_sock, addr = server_sock.accept()
                    print(f"[MCP] Client connected: {addr[0]}:{addr[1]}",
                          file=sys.stderr, flush=True)
                    t = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock, addr),
                        daemon=True,
                    )
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            server_sock.close()
            print("[MCP] TCP server stopped", file=sys.stderr, flush=True)

    _MAX_BUF = 1 * 1024 * 1024   # 1 MiB — drop connection if exceeded
    _MAX_MSG = 16 * 1024 * 1024  # 16 MiB — drop connection if Content-Length exceeds

    def _handle_client(self, sock: socket.socket,
                       addr: tuple) -> None:
        """Handle one TCP client connection (runs in its own thread)."""
        buf = b""
        drop = False
        try:
            while True:
                # Read available data
                ready, _, _ = select.select([sock], [], [], 0.5)
                if not ready:
                    continue
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk

                # Guard against unbounded buffer growth (malformed/malicious client)
                if len(buf) > self._MAX_BUF:
                    print(f"[MCP] !! {addr[0]}: buffer exceeded {self._MAX_BUF} bytes — dropping",
                          file=sys.stderr, flush=True)
                    break

                # Process complete MCP messages (Content-Length framing)
                while True:
                    header_end = buf.find(b"\r\n\r\n")
                    if header_end < 0:
                        break
                    header = buf[:header_end].decode("ascii", "replace")
                    length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                length = int(line.split(":")[1].strip())
                            except ValueError:
                                pass
                    # Guard against oversized Content-Length
                    if length > self._MAX_MSG:
                        print(f"[MCP] !! {addr[0]}: Content-Length {length} exceeds {self._MAX_MSG} — dropping",
                              file=sys.stderr, flush=True)
                        drop = True
                        break
                    if length <= 0:
                        buf = buf[header_end + 4:]
                        continue
                    body_start = header_end + 4
                    if len(buf) < body_start + length:
                        break  # incomplete body
                    body = buf[body_start:body_start + length]
                    buf = buf[body_start + length:]

                    try:
                        raw = json.loads(body.decode("utf-8"))
                        log_msg = raw.get("method", "response")
                        log_id = raw.get("id", "")
                        print(f"[MCP] <- {addr[0]}: {log_msg} (id={log_id})",
                              file=sys.stderr, flush=True)

                        msg = MCPMessage.from_dict(raw)
                        writer = _SocketWriter(sock)
                        self._handle_message(msg, writer)

                        if msg.is_request:
                            print(f"[MCP] -> {addr[0]}: response (id={msg.id})",
                                  file=sys.stderr, flush=True)
                    except Exception as exc:
                        err = MCPMessage.error_response(
                            raw.get("id", 0) if isinstance(raw, dict) else 0,
                            -32603, str(exc),
                        )
                        write_message(err.to_dict(), _SocketWriter(sock))
                        print(f"[MCP] !! {addr[0]}: error - {exc}",
                              file=sys.stderr, flush=True)
                if drop:
                    break  # oversized message — close connection
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            sock.close()
            print(f"[MCP] Client disconnected: {addr[0]}:{addr[1]}",
                  file=sys.stderr, flush=True)

    # ------------------------------------------------------------------
    # Message dispatch (shared with stdio mode)
    # ------------------------------------------------------------------

    def _handle_message(self, msg: MCPMessage,
                        writer: Any = None) -> None:
        if msg.is_notification:
            self._handle_notification(msg)
        elif msg.is_request:
            self._handle_request(msg, writer)
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

    def _handle_request(self, msg: MCPMessage,
                        writer: Any = None) -> None:
        method = msg.method
        params = msg.params
        msg_id = msg.id or 0

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
                write_message(MCPMessage.response(msg_id, result).to_dict(), writer)

            elif method == "tools/list":
                result = self._handle_tools_list()
                write_message(MCPMessage.response(msg_id, result).to_dict(), writer)

            elif method == "tools/call":
                result = self._handle_tools_call(params)
                write_message(MCPMessage.response(msg_id, result).to_dict(), writer)

            elif method == "ping":
                write_message(MCPMessage.response(msg_id, {}).to_dict(), writer)

            else:
                write_message(
                    MCPMessage.error_response(
                        msg_id, -32601, f"Method not found: {method}"
                    ).to_dict(), writer
                )

        except MemDBGError as exc:
            write_message(
                MCPMessage.error_response(msg_id, -32000, str(exc)).to_dict(), writer
            )
        except Exception as exc:
            write_message(
                MCPMessage.error_response(msg_id, -32603, str(exc)).to_dict(), writer
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

        # Ensure API is available (thread-safe)
        if self._api is None:
            with self._api_lock:
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
        print("  python3 -m mcp_server.server [--stdio | --tcp [PORT]]")
        print()
        print("Options:")
        print("  --stdio       Run in stdio mode (default)")
        print("  --tcp [PORT]  Run in TCP mode on 127.0.0.1:PORT (default 9000)")
        print()
        print("Set MEMDBG_CONTEXT to a context.json file produced by the MemDBG")
        print("frontend, or pass it as the first positional argument.")
        return 0

    # Parse --tcp flag
    tcp_port: Optional[int] = None
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--tcp":
            tcp_port = 9000
            if i < len(sys.argv) and not sys.argv[i].startswith("-"):
                try:
                    tcp_port = int(sys.argv[i])
                except ValueError:
                    pass
            break

    try:
        api = MemDBG.from_context()
    except MemDBGError:
        api = None  # Will be lazily initialized on first tool call

    server = MCPServer(api)

    if tcp_port is not None:
        server.run_tcp(port=tcp_port)
    else:
        server.run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
