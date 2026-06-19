#!/usr/bin/env python3
# MemDBG MCP Server - Tool definitions for MCP clients.
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Import the SDK (from the plugin-repository root)
import os
import sys

_sdk_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sdk_dir not in sys.path:
    sys.path.insert(0, _sdk_dir)

from memdbg import MemDBG, BATCH_READ_MAX_ITEMS


MEMDBG_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "memdbg_hello",
        "description": "Send a HELLO handshake to the MemDBG payload and return protocol version, capabilities, and platform info.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memdbg_process_list",
        "description": "List all processes from the active MemDBG payload session.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memdbg_process_maps",
        "description": "List memory maps for a given process ID (PID). Returns start/end addresses, size, protection flags, and name for each mapped region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
            },
        },
    },
    {
        "name": "memdbg_process_info",
        "description": "Get detailed process information (name, title ID, content ID, executable path) for a PID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
            },
        },
    },
    {
        "name": "memdbg_memory_read",
        "description": "Read raw bytes from process memory at a given address. Returns base64-encoded bytes and the byte count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "integer",
                    "description": "Virtual memory address to read from (hex or decimal). Required.",
                },
                "length": {
                    "type": "integer",
                    "description": "Number of bytes to read. Required.",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
            },
            "required": ["address", "length"],
        },
    },
    {
        "name": "memdbg_memory_write",
        "description": "Write raw bytes to process memory at a given address. Returns the number of bytes written.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "integer",
                    "description": "Virtual memory address to write to. Required.",
                },
                "data": {
                    "type": "string",
                    "description": "Base64-encoded bytes to write. Required.",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
            },
            "required": ["address", "data"],
        },
    },
    {
        "name": "memdbg_scan_value",
        "description": "Scan process memory for an exact value match. Returns matching addresses. Supports u8, u16, u32, u64, float, double, and byte-pattern searches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value_type": {
                    "type": "string",
                    "enum": ["u8", "u16", "u32", "u64", "float", "double", "bytes"],
                    "description": "The value type to scan for. Default: u32.",
                    "default": "u32",
                },
                "value": {
                    "type": "string",
                    "description": "The value to search for (as a string; parsed according to value_type). Required.",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
                "start": {
                    "type": "integer",
                    "description": "Start address for the scan range. Default: 0x0 (entire process).",
                },
                "length": {
                    "type": "integer",
                    "description": "Length of the scan range. Default: scans the entire address space.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results. Default: 4096.",
                },
            },
            "required": ["value"],
        },
    },
    {
        "name": "memdbg_cheat_list",
        "description": "List all cheat/trainer entries currently configured in the frontend.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memdbg_cheat_add",
        "description": "Add a cheat/trainer entry to lock a memory value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Human-readable name for the cheat entry.",
                },
                "address": {
                    "type": "integer",
                    "description": "Memory address to lock. Required.",
                },
                "value_type": {
                    "type": "string",
                    "enum": ["u8", "u16", "u32", "u64", "float", "double"],
                    "description": "Value type. Default: u32.",
                },
                "value": {
                    "type": "string",
                    "description": "The value to lock at the address.",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
            },
            "required": ["description", "address"],
        },
    },    {
      "name": "memdbg_batch_read",
      "description": "Read up to 64 memory regions in a single efficient request. Provide a list of {address, length} objects. Returns each region's base64-encoded data and status.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "items": {
            "type": "array",
            "description": "List of {address: integer, length: integer} objects. Max 64 items.",
            "items": {
              "type": "object",
              "properties": {
                "address": {"type": "integer"},
                "length": {"type": "integer"},
              },
              "required": ["address", "length"],
            },
          },
          "pid": {
            "type": "integer",
            "description": "Process ID. If omitted, uses the currently selected process.",
          },
        },
        "required": ["items"],
      },
    },
    {
      "name": "memdbg_aob_scan",
      "description": "Scan process memory for an Array-of-Bytes (AOB) pattern. Use ?? for wildcard bytes (e.g. 48 8B ?? ?? C3).",
      "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "AOB pattern with hex bytes and ?? wildcards, separated by spaces. Required.",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID. If omitted, uses the currently selected process.",
                },
                "start": {
                    "type": "integer",
                    "description": "Start address for the scan range.",
                },
                "length": {
                    "type": "integer",
                    "description": "Length of the scan range.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results. Default: 256.",
                },
            },
            "required": ["pattern"],
        },
    },
]


def execute_tool(api: MemDBG, name: str, args: Dict[str, Any]) -> Any:
    """Execute a named MemDBG tool with the given arguments.

    Returns the result value (which will be JSON-serialized in the response).
    """
    import base64

    if name == "memdbg_hello":
        return api.hello()

    elif name == "memdbg_process_list":
        return api.process_list()

    elif name == "memdbg_process_maps":
        return api.process_maps(args.get("pid"))

    elif name == "memdbg_process_info":
        return api.process_info(args.get("pid"))

    elif name == "memdbg_memory_read":
        data = api.memory_read(
            address=int(args["address"]),
            length=int(args["length"]),
            pid=args.get("pid"),
        )
        return {
            "base64": base64.b64encode(data).decode("ascii"),
            "length": len(data),
        }

    elif name == "memdbg_memory_write":
        import base64
        raw = base64.b64decode(args["data"])
        written = api.memory_write(
            address=int(args["address"]),
            data=raw,
            pid=args.get("pid"),
        )
        return {"written": written}

    elif name == "memdbg_scan_value":
        # Scan is typically frontend-only, but we can expose a simplified version.
        # For now, return a message directing to use the frontend.
        return {
            "message": (
                "Direct scan is not available via MCP stdio. "
                "Use the MemDBG frontend Scanner screen for in-depth scanning, "
                "or use memdbg_memory_read to read regions and scan client-side."
            ),
            "note": "The full scan engine runs inside the MemDBG frontend process.",
        }

    elif name == "memdbg_cheat_list":
        return {
            "message": (
                "Cheat list is managed by the MemDBG frontend. "
                "Use the Trainer screen to add/modify cheats visually."
            ),
        }

    elif name == "memdbg_cheat_add":
        return {
            "message": (
                "Use the MemDBG frontend Trainer screen to add cheats. "
                "Or use memdbg_memory_write to write values directly."
            ),
        }

    elif name == "memdbg_batch_read":
        items = args.get("items", [])
        if not isinstance(items, list) or len(items) == 0:
            raise ValueError("batch_read requires a non-empty 'items' array")
        if len(items) > BATCH_READ_MAX_ITEMS:
            raise ValueError(f"batch_read max {BATCH_READ_MAX_ITEMS} items per request")
        return api.batch_read(items, args.get("pid"))

    elif name == "memdbg_aob_scan":
        return {
            "message": (
                "AOB scan is available in the MemDBG frontend AOB Scanner screen. "
                "For headless AOB scanning, use memdbg_memory_read to fetch regions "
                "and scan client-side."
            ),
        }

    else:
        raise ValueError(f"Unknown tool: {name}")
