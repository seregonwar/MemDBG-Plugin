#!/usr/bin/env python3
# MemDBG MCP stdio bridge template.
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

from memdbg import MemDBG, run_mcp_stdio


def main() -> int:
    if "--stdio" in sys.argv or os.environ.get("MEMDBG_MCP_STDIO") == "1":
        run_mcp_stdio(MemDBG.from_context())
        return 0

    print("MemDBG MCP stdio bridge template")
    print("This plugin exposes MemDBG process and memory tools over MCP stdio.")
    print("Launch it from an MCP client with:")
    print("  MEMDBG_CONTEXT=/path/to/context.json MEMDBG_MCP_STDIO=1 python3 mcp_stdio_bridge.py --stdio")
    print("")
    print("Available tools:")
    print("  memdbg_process_list")
    print("  memdbg_process_maps")
    print("  memdbg_memory_read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
