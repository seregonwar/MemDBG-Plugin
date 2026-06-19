#!/usr/bin/env python3
# MemDBG MCP Server Manager — GUI plugin for managing the MCP server.
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from memdbg import MemDBG, MemDBGError, load_context

try:
    from mcp_server.plugin_config import PluginConfig
    from mcp_server.gui import GuiBuilder
    from mcp_server.protocol import read_message, GuiEvent
    from mcp_server.tools import MEMDBG_TOOLS
except ImportError:
    from plugin_config import PluginConfig
    from gui import GuiBuilder
    from protocol import read_message, GuiEvent
    from tools import MEMDBG_TOOLS


# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------

class PluginState:
    def __init__(self) -> None:
        self.api: MemDBG | None = None
        self.server_process: subprocess.Popen | None = None
        self.server_port: int = 9000
        self.server_running: bool = False
        self.status: str = "Server stopped"

        # Activity log (lines captured from server stderr)
        self.log_lines: List[str] = []
        self.log_queue: queue.Queue = queue.Queue()
        self.log_thread: threading.Thread | None = None
        self._log_stop = False

        # Tool explorer
        self.selected_tool: str = ""
        self.tool_test_params: str = "{}"
        self.tool_test_result: str = ""

        # Configuration
        self.disabled_tools: List[str] = []


def main() -> int:
    ctx = load_context()
    state = PluginState()

    try:
        state.api = MemDBG.from_context()
    except MemDBGError as exc:
        _log(state, f"Cannot connect to payload: {exc}")
        state.api = None

    plugin_cfg = PluginConfig("mcp-manager")
    plugin_cfg.load()
    state.server_port = int(plugin_cfg.get("port", 9000))
    state.disabled_tools = plugin_cfg.get("disabled_tools", [])

    gui = GuiBuilder()
    gui.set_value("input_port", state.server_port)
    gui.set_value("input_test_params", "{}")

    while True:
        gui.begin_frame()

        # Drain the activity log queue
        _drain_log(state)

        # --- Handle events ---
        if gui.was_clicked("btn_start"):
            _start_server(state)
        if gui.was_clicked("btn_stop"):
            _stop_server(state)
        if gui.was_clicked("btn_test_tool"):
            _test_tool(state, gui)
        if gui.was_clicked("btn_clear_log"):
            state.log_lines.clear()

        # Configuration persistence
        if gui.was_edited("input_port"):
            port = int(gui.get_value("input_port", 9000))
            state.server_port = port
            plugin_cfg.set("port", port)
            plugin_cfg.save()

        # Track disabled tools from UI checkboxes
        _sync_disabled_tools(gui, state, plugin_cfg)

        build_ui(gui, state)

        gui.end_frame()
        time.sleep(0.016)


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_ui(gui: GuiBuilder, state: PluginState) -> None:
    """Construct the widget tree for the MCP Manager."""

    gui.text_colored("MCP Server Manager", "primary2")
    gui.separator()
    gui.spacing()

    # --- Status ---
    status_color = "success" if state.server_running else "muted"
    gui.text(f"Status: {state.status}", color=status_color)
    gui.spacing()

    # --- Section 1: Start/Stop Controls ---
    gui.text("Server Control", color="primary")
    if state.server_running:
        gui.text(f"Listening on 127.0.0.1:{state.server_port}", color="success")
        gui.text(f"PID: {state.server_process.pid if state.server_process else '?'}",
                 color="dim")
    else:
        gui.text("Server is not running", color="dim")

    gui.same_line()
    gui.button("btn_start", "Start Server", variant="primary",
               disabled=state.server_running)
    gui.same_line()
    gui.button("btn_stop", "Stop Server", variant="danger",
               disabled=not state.server_running)
    gui.spacing()

    # --- Section 2: Activity Log ---
    gui.text("Activity Log", color="primary")
    _build_log_view(gui, state)
    gui.same_line()
    gui.button("btn_clear_log", "Clear", variant="soft")
    gui.spacing()

    # --- Section 3: Tool Explorer ---
    gui.text("Tool Explorer", color="primary")
    _build_tool_explorer(gui, state)
    gui.spacing()

    # --- Section 4: Configuration ---
    gui.text("Configuration", color="primary")
    gui.input_int("input_port", "TCP Port", step=1, step_fast=100)
    gui.spacing()

    gui.text("Enabled Tools", color="primary")
    for tool in MEMDBG_TOOLS:
        tool_name = tool["name"]
        widget_id = f"tool_{tool_name}"
        enabled = tool_name not in state.disabled_tools
        gui.set_value(widget_id, enabled)
        gui.checkbox(widget_id, tool_name)
    gui.spacing()


# ---------------------------------------------------------------------------
# Log view (scrollable child)
# ---------------------------------------------------------------------------

def _build_log_view(gui: GuiBuilder, state: PluginState) -> None:
    """Render the activity log in a scrollable child window."""
    recent = state.log_lines[-200:]  # Keep last 200 lines
    text = "\n".join(recent) if recent else "(no activity yet)"
    gui.begin_child("log_panel", width=0, height=120, border=True)
    gui.text(text, color="dim")
    gui.end_child()


# ---------------------------------------------------------------------------
# Tool explorer
# ---------------------------------------------------------------------------

def _build_tool_explorer(gui: GuiBuilder, state: PluginState) -> None:
    """Render the tool list selector and test UI."""
    tool_names = [t["name"] for t in MEMDBG_TOOLS]
    if not tool_names:
        gui.text("No tools available", color="dim")
        return

    if state.selected_tool:
        sel_idx = tool_names.index(state.selected_tool) if state.selected_tool in tool_names else 0
    else:
        sel_idx = 0
    gui.set_value("combo_tools", sel_idx)
    gui.combo("combo_tools", "Select tool", items=tool_names)

    # Read selection back
    idx = int(gui.get_value("combo_tools", 0))
    if 0 <= idx < len(tool_names):
        state.selected_tool = tool_names[idx]

    # Show tool description
    for tool in MEMDBG_TOOLS:
        if tool["name"] == state.selected_tool:
            gui.text(tool.get("description", ""), color="dim")
            break

    gui.spacing()
    gui.text("Test parameters (JSON):", color="dim")
    gui.input_text("input_test_params", "Params",
                   hint='{"key": "value"}',
                   width=0)
    gui.same_line()
    gui.button("btn_test_tool", "Run Tool", variant="primary")

    # Show test result
    if state.tool_test_result:
        gui.spacing()
        gui.text("Result:", color="success")
        gui.text(state.tool_test_result[:2000], color="dim")


def _test_tool(state: PluginState, gui: GuiBuilder) -> None:
    """Execute the selected tool with test parameters."""
    if not state.selected_tool or state.api is None:
        state.tool_test_result = "No tool selected or no payload connection"
        return

    params_str = str(gui.get_value("input_test_params", "{}"))
    try:
        params = json.loads(params_str)
    except json.JSONDecodeError as exc:
        state.tool_test_result = f"Invalid JSON params: {exc}"
        return

    try:
        from mcp_server.tools import execute_tool
    except ImportError:
        from tools import execute_tool

    try:
        result = execute_tool(state.api, state.selected_tool, params)
        state.tool_test_result = json.dumps(result, indent=2, default=str)
        _log(state, f"[Tool Test] {state.selected_tool} -> OK")
    except Exception as exc:
        state.tool_test_result = f"Error: {exc}"
        _log(state, f"[Tool Test] {state.selected_tool} -> ERROR: {exc}")


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _start_server(state: PluginState) -> None:
    """Spawn the MCP server as a subprocess."""
    if state.server_running:
        return

    # Determine the server entry point
    server_script = os.path.join(_plugin_dir, "mcp_server", "server.py")
    if not os.path.exists(server_script):
        # Fallback: look in the plugin data dir
        server_script = os.path.join(_plugin_dir, "..", "mcp_server", "server.py")
    if not os.path.exists(server_script):
        # Try running as module
        server_script = "-m"
        server_args = ["mcp_server.server", "--tcp", str(state.server_port)]
    else:
        server_args = [server_script, "--tcp", str(state.server_port)]

    # Build command
    python_exe = sys.executable
    if server_script == "-m":
        cmd = [python_exe] + server_args
    else:
        cmd = [python_exe, server_script, "--tcp", str(state.server_port)]

    # Disabled tools flag
    if state.disabled_tools:
        cmd.append("--disable-tools")
        cmd.append(",".join(state.disabled_tools))

    _log(state, f"[Manager] Starting MCP server: {' '.join(cmd)}")

    try:
        state.server_process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        state.server_running = True
        state.status = f"Running on port {state.server_port}"
        _log(state, f"[Manager] Server started, PID {state.server_process.pid}")

        # Start log reader thread
        state._log_stop = False
        state.log_thread = threading.Thread(
            target=_log_reader,
            args=(state,),
            daemon=True,
        )
        state.log_thread.start()

    except Exception as exc:
        state.server_running = False
        state.status = f"Failed to start: {exc}"
        _log(state, f"[Manager] ERROR: {exc}")


def _stop_server(state: PluginState) -> None:
    """Stop the MCP server subprocess."""
    if not state.server_running or state.server_process is None:
        return

    _log(state, "[Manager] Stopping MCP server...")
    state._log_stop = True

    try:
        state.server_process.terminate()
        try:
            state.server_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            state.server_process.kill()
            state.server_process.wait()
    except Exception as exc:
        _log(state, f"[Manager] Error stopping: {exc}")

    state.server_running = False
    state.server_process = None
    state.status = "Server stopped"
    _log(state, "[Manager] Server stopped")


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def _log(state: PluginState, line: str) -> None:
    """Add a line to the activity log."""
    state.log_queue.put(line)


def _log_reader(state: PluginState) -> None:
    """Background thread: read from server stderr pipe."""
    try:
        while not state._log_stop and state.server_process is not None:
            if state.server_process.stderr is None:
                break
            line = state.server_process.stderr.readline()
            if not line:
                break
            line = line.rstrip("\n\r")
            if line:
                state.log_queue.put(line)
    except (ValueError, IOError):
        pass


def _drain_log(state: PluginState) -> None:
    """Drain queued log lines into state.log_lines (called from main thread)."""
    while True:
        try:
            line = state.log_queue.get_nowait()
            state.log_lines.append(line)
        except queue.Empty:
            break
    # Cap at 1000 lines
    if len(state.log_lines) > 1000:
        state.log_lines = state.log_lines[-1000:]


# ---------------------------------------------------------------------------
# Configuration sync
# ---------------------------------------------------------------------------

def _sync_disabled_tools(gui: GuiBuilder, state: PluginState,
                         plugin_cfg: PluginConfig) -> None:
    """Detect tool toggle changes and persist to config."""
    changed = False
    for tool in MEMDBG_TOOLS:
        widget_id = f"tool_{tool['name']}"
        if gui.was_toggled(widget_id):
            enabled = bool(gui.get_value(widget_id, True))
            if enabled and tool["name"] in state.disabled_tools:
                state.disabled_tools.remove(tool["name"])
                changed = True
            elif not enabled and tool["name"] not in state.disabled_tools:
                state.disabled_tools.append(tool["name"])
                changed = True
    if changed:
        plugin_cfg.set("disabled_tools", state.disabled_tools)
        plugin_cfg.save()
        # Server restart recommended
        _log(state, "[Manager] Tool configuration changed — restart server to apply")


if __name__ == "__main__":
    raise SystemExit(main())
