#!/usr/bin/env python3
# MemDBG MCP Server - JSON-RPC protocol types and stdio message I/O.
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# JSON-RPC message types
# ---------------------------------------------------------------------------

@dataclass
class MCPMessage:
    jsonrpc: str = "2.0"
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    result: Any = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.method:
            d["method"] = self.method
        if self.params:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPMessage":
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            method=data.get("method", ""),
            params=data.get("params", {}),
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
        )

    @classmethod
    def request(cls, method: str, params: Dict[str, Any], msg_id: int) -> "MCPMessage":
        return cls(jsonrpc="2.0", method=method, params=params, id=msg_id)

    @classmethod
    def response(cls, msg_id: int, result: Any) -> "MCPMessage":
        return cls(jsonrpc="2.0", id=msg_id, result=result)

    @classmethod
    def error_response(cls, msg_id: int, code: int, message: str) -> "MCPMessage":
        return cls(jsonrpc="2.0", id=msg_id, error={"code": code, "message": message})

    @classmethod
    def notification(cls, method: str, params: Dict[str, Any]) -> "MCPMessage":
        return cls(jsonrpc="2.0", method=method, params=params)

    @property
    def is_request(self) -> bool:
        return bool(self.method) and self.id is not None

    @property
    def is_notification(self) -> bool:
        return bool(self.method) and self.id is None

    @property
    def is_response(self) -> bool:
        return self.id is not None and not self.method


# Legacy aliases for compatibility
MCPRequest = MCPMessage
MCPResponse = MCPMessage
MCPNotification = MCPMessage


# ---------------------------------------------------------------------------
# GUI update / event types
# ---------------------------------------------------------------------------

@dataclass
class GuiUpdate:
    """Python -> C++ : push UI tree update."""
    method: str = "ui_update"
    widgets: List[Dict[str, Any]] = field(default_factory=list)

    def to_message(self) -> MCPMessage:
        return MCPMessage.notification("ui_update", {"widgets": self.widgets})


@dataclass
class GuiEvent:
    """C++ -> Python : user interaction event."""
    method: str = "ui_event"
    widget_id: str = ""
    event: str = ""      # "click", "edited", "toggled", "selected", "deactivated"
    value: Any = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuiEvent":
        return cls(
            method=data.get("method", "ui_event"),
            widget_id=data.get("widget_id", data.get("id", "")),
            event=data.get("event", ""),
            value=data.get("value"),
        )


# ---------------------------------------------------------------------------
# Stdio message I/O (MCP-style framing)
# ---------------------------------------------------------------------------

def read_message(stdin=None) -> Optional[Dict[str, Any]]:
    """Read one JSON-RPC message from stdin (Content-Length framing)."""
    inp = stdin or sys.stdin
    headers: Dict[str, str] = {}
    while True:
        line = inp.buffer.readline()
        if not line:
            return None
        line = line.decode("ascii", "replace").strip()
        if not line:
            break
        key, _, value = line.partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = inp.buffer.read(length).decode("utf-8")
    return json.loads(body)


def write_message(message: Dict[str, Any], stdout=None) -> None:
    """Write one JSON-RPC message to stdout (Content-Length framing)."""
    out = stdout or sys.stdout
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    out.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    out.buffer.write(payload)
    out.buffer.flush()
