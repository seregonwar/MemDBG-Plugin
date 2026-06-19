#!/usr/bin/env python3
# MemDBG Python plugin SDK.
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MAGIC = 0x4742444D
VERSION = 1
MAX_PACKET = 1024 * 1024

CMD_HELLO = 0x0001
CMD_PROCESS_LIST = 0x0100
CMD_PROCESS_MAPS = 0x0101
CMD_PROCESS_INFO = 0x0102
CMD_MEMORY_READ = 0x0200
CMD_MEMORY_WRITE = 0x0201
CMD_BATCH_READ = 0x0202
CMD_BATCH_WRITE = 0x0203

BATCH_READ_MAX_ITEMS = 64
BATCH_WRITE_MAX_ITEMS = 64


class MemDBGError(RuntimeError):
    pass


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise MemDBGError("connection closed")
        chunks.extend(part)
    return bytes(chunks)


def load_context(path: Optional[str] = None) -> Dict[str, Any]:
    context_path = path or os.environ.get("MEMDBG_CONTEXT")
    if context_path is None and len(sys.argv) > 1:
        context_path = sys.argv[1]
    if not context_path:
        raise MemDBGError("missing MemDBG context path")
    with Path(context_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class MemDBG:
    def __init__(self, host: str = "127.0.0.1", port: int = 9020,
                 selected_pid: int = 0, timeout: float = 10.0) -> None:
        self.host = host
        self.port = int(port)
        self.selected_pid = int(selected_pid)
        self.timeout = timeout
        self._request_id = 1

    @classmethod
    def from_context(cls, context_path: Optional[str] = None) -> "MemDBG":
        ctx = load_context(context_path)
        console = ctx.get("console", {})
        process = ctx.get("process", {})
        return cls(
            host=console.get("host") or "127.0.0.1",
            port=int(console.get("debug_port") or 9020),
            selected_pid=int(process.get("pid") or 0),
        )

    def request(self, command: int, body: bytes = b"") -> bytes:
        if len(body) > MAX_PACKET:
            raise MemDBGError("request body too large")
        request_id = self._request_id
        self._request_id = (self._request_id + 1) & 0xFFFFFFFF
        if self._request_id == 0:
            self._request_id = 1

        packet = struct.pack("<IHHII", MAGIC, VERSION, command, request_id, len(body)) + body
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(packet)
                header = _read_exact(sock, 20)
                magic, version, resp_command, resp_id, status, length = struct.unpack("<IHHIiI", header)
                if magic != MAGIC or version != VERSION:
                    raise MemDBGError("invalid response header")
                if resp_command != command or resp_id != request_id:
                    raise MemDBGError("response does not match request")
                if length > MAX_PACKET:
                    raise MemDBGError("response too large")
                payload = _read_exact(sock, length)
                if status != 0:
                    raise MemDBGError(f"payload command 0x{command:04x} failed with status {status}")
                return payload
        except OSError as exc:
            raise MemDBGError(
                f"cannot connect to MemDBG payload at {self.host}:{self.port}: {exc}"
            ) from exc

    def hello(self) -> Dict[str, Any]:
        raw = self.request(CMD_HELLO)
        protocol, platform_id, capabilities, debug_port, udp_port, version, name = struct.unpack(
            "<HHIHH16s16s", raw[:44]
        )
        return {
            "protocol_version": protocol,
            "platform_id": platform_id,
            "capabilities": capabilities,
            "debug_port": debug_port,
            "udp_log_port": udp_port,
            "version": _cstr(version),
            "name": _cstr(name),
        }

    def process_list(self) -> List[Dict[str, Any]]:
        raw = self.request(CMD_PROCESS_LIST)
        count = struct.unpack_from("<I", raw, 0)[0]
        out = []
        off = 4
        for _ in range(count):
            pid, name = struct.unpack_from("<i48s", raw, off)
            off += 52
            if pid > 0:
                out.append({"pid": pid, "name": _cstr(name)})
        return out

    def process_maps(self, pid: Optional[int] = None) -> List[Dict[str, Any]]:
        target = int(pid if pid is not None else self.selected_pid)
        raw = self.request(CMD_PROCESS_MAPS, struct.pack("<i", target))
        count = struct.unpack_from("<I", raw, 0)[0]
        out = []
        off = 4
        for _ in range(count):
            start, end, protection, flags, name = struct.unpack_from("<QQII64s", raw, off)
            off += 88
            out.append({
                "start": start,
                "end": end,
                "size": end - start,
                "protection": protection,
                "flags": flags,
                "name": _cstr(name),
            })
        return out

    def process_info(self, pid: Optional[int] = None) -> Dict[str, Any]:
        target = int(pid if pid is not None else self.selected_pid)
        raw = self.request(CMD_PROCESS_INFO, struct.pack("<i", target))
        pid_value, name, title_id, content_id, path = struct.unpack("<i48s16s64s128s", raw[:260])
        return {
            "pid": pid_value,
            "name": _cstr(name),
            "title_id": _cstr(title_id),
            "content_id": _cstr(content_id),
            "path": _cstr(path),
        }

    def memory_read(self, address: int, length: int,
                    pid: Optional[int] = None) -> bytes:
        target = int(pid if pid is not None else self.selected_pid)
        raw = self.request(CMD_MEMORY_READ, struct.pack("<iQI", target, int(address), int(length)))
        if raw.startswith(b"\x00"):
            return raw[1:]
        if raw.startswith(b"\x01"):
            raise MemDBGError("LZ4-compressed response requires frontend-side decompression")
        return raw

    def memory_write(self, address: int, data: bytes,
                     pid: Optional[int] = None) -> int:
        target = int(pid if pid is not None else self.selected_pid)
        payload = struct.pack("<iQI", target, int(address), len(data)) + bytes(data)
        raw = self.request(CMD_MEMORY_WRITE, payload)
        return struct.unpack_from("<I", raw, 0)[0]

    def batch_read(self, items: List[Dict[str, int]],
                   pid: Optional[int] = None) -> List[Dict[str, Any]]:
        """Read up to 64 memory regions in a single efficient request.

        Args:
            items: List of {"address": int, "length": int} dicts. Max 64 items.
            pid: Optional process ID.

        Returns:
            List of {"address": int, "length": int, "status": int,
                     "data": str (base64)} dicts, one per requested item.
        """
        if not items or len(items) == 0:
            raise MemDBGError("batch_read requires at least one item")
        if len(items) > BATCH_READ_MAX_ITEMS:
            raise MemDBGError(
                f"batch_read: max {BATCH_READ_MAX_ITEMS} items per request"
            )

        target = int(pid if pid is not None else self.selected_pid)
        count = len(items)

        # Build request: header + items[] + inline data (no inline data for read)
        header = struct.pack("<iII", target, count, 0)
        body = header
        for item in items:
            if "address" not in item:
                raise MemDBGError("batch_read: each item must have an 'address' key")
            addr = int(item["address"])
            length = int(item.get("length", 4))
            body += struct.pack("<QII", addr, length, 0)

        raw = self.request(CMD_BATCH_READ, body)

        # NOTE: LZ4 decompression is not handled here — the payload
        # returns uncompressed data for batch_read responses.

        # Parse response: count * result_entry + concatenated data bytes
        out: List[Dict[str, Any]] = []
        off = 0
        for _ in range(count):
            if off + 16 > len(raw):
                raise MemDBGError("batch_read: short response")
            addr, length, status = struct.unpack_from("<QII", raw, off)
            off += 16
            out.append({
                "address": addr,
                "length": length,
                "status": status,
                "data": "",
            })

        # Remaining bytes are the concatenated data for successful reads
        data_off = off
        for entry in out:
            if entry["status"] == 0 and entry["length"] > 0:
                end = data_off + entry["length"]
                if end > len(raw):
                    raise MemDBGError("batch_read: data overrun")
                entry["data"] = base64.b64encode(
                    raw[data_off:end]
                ).decode("ascii")
                data_off = end
            else:
                entry["data"] = ""

        return out


def _mcp_read_message(stdin) -> Optional[Dict[str, Any]]:
    headers: Dict[str, str] = {}
    while True:
        line = stdin.buffer.readline()
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
    return json.loads(stdin.buffer.read(length).decode("utf-8"))


def _mcp_write_message(stdout, message: Dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    stdout.buffer.write(payload)
    stdout.buffer.flush()


def run_mcp_stdio(api: Optional[MemDBG] = None) -> None:
    client = api or MemDBG.from_context()

    tools = [
        {
            "name": "memdbg_process_list",
            "description": "List processes from the active MemDBG payload.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "memdbg_process_maps",
            "description": "List memory maps for a PID.",
            "inputSchema": {
                "type": "object",
                "properties": {"pid": {"type": "integer"}},
            },
        },
        {
            "name": "memdbg_memory_read",
            "description": "Read process memory and return base64 bytes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "address": {"type": "integer"},
                    "length": {"type": "integer"},
                },
                "required": ["address", "length"],
            },
        },
    ]

    while True:
        request = _mcp_read_message(sys.stdin)
        if request is None:
            break
        method = request.get("method")
        request_id = request.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "memdbg-python-bridge", "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                args = params.get("arguments") or {}
                if name == "memdbg_process_list":
                    value = client.process_list()
                elif name == "memdbg_process_maps":
                    value = client.process_maps(args.get("pid"))
                elif name == "memdbg_memory_read":
                    data = client.memory_read(args["address"], args["length"], args.get("pid"))
                    value = {"base64": base64.b64encode(data).decode("ascii"), "length": len(data)}
                else:
                    raise MemDBGError(f"unknown tool: {name}")
                result = {"content": [{"type": "text", "text": json.dumps(value, indent=2)}]}
            elif method and method.startswith("notifications/"):
                continue
            else:
                result = {}
            if request_id is not None:
                _mcp_write_message(sys.stdout, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            if request_id is not None:
                _mcp_write_message(sys.stdout, {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                })
