#!/usr/bin/env python3
# MemDBG Example GUI Plugin — interactive ImGui dashboard from Python.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This plugin connects to the MemDBG payload, shows process information,
# and lets you read/write memory values through a native ImGui UI.
#
# Run in GUI mode (launched by MemDBG frontend):
#   The frontend spawns this script as a persistent process communicating
#   via stdin/stdout JSON-RPC.

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
from typing import Any, Dict, List

# Ensure the SDK and mcp_server are importable
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from memdbg import MemDBG, MemDBGError, load_context

try:
    from mcp_server.plugin_config import PluginConfig
    from mcp_server.gui import GuiBuilder
    from mcp_server.protocol import read_message, GuiEvent
except ImportError:
    from plugin_config import PluginConfig
    from gui import GuiBuilder
    from protocol import read_message, GuiEvent


# ---------------------------------------------------------------------------
# Plugin state (survives across frames)
# ---------------------------------------------------------------------------

class PluginState:
    def __init__(self) -> None:
        self.api: MemDBG | None = None
        self.processes: List[Dict[str, Any]] = []
        self.selected_pid: int = 0
        self.read_result: bytes = b""
        self.read_error: str = ""
        self.status: str = "Initialising..."
        self.refresh_requested: bool = False

        # Live-refresh timer (frames until next auto-refresh)
        self.live_refresh_timer: int = 0
        self.live_refresh_interval: int = 60  # ~1 s at 60 FPS


def main() -> int:
    """Main loop for the GUI plugin in persistent stdio mode."""
    ctx = load_context()
    console = ctx.get("console", {})
    process = ctx.get("process", {})

    state = PluginState()

    # Connect to the payload
    try:
        state.api = MemDBG.from_context()
        hello = state.api.hello()
        state.status = (
            f"Connected to {hello.get('name', 'payload')} "
            f"v{hello.get('protocol_version', '?')} "
            f"on {console.get('host', '?')}:{console.get('debug_port', '?')}"
        )
    except MemDBGError as exc:
        state.status = f"Cannot connect to payload: {exc}"
        state.api = None

    state.selected_pid = int(process.get("pid", 0))
    state.refresh_requested = True  # Load process list on first frame

    # Load persisted plugin settings
    plugin_cfg = PluginConfig("example-gui")
    plugin_cfg.load()

    # Build the GUI (widget state auto-persisted inside the builder)
    gui = GuiBuilder()

    # Seed initial widget values from persisted config
    gui.set_value("input_address", plugin_cfg.get("address", "0x0"))
    try:
        gui.set_value("input_length", int(plugin_cfg.get("length", 256)))
    except (ValueError, TypeError):
        gui.set_value("input_length", 256)
    gui.set_value("cb_live_refresh", plugin_cfg.get("live_refresh", False))
    try:
        interval = int(plugin_cfg.get("refresh_interval", 60))
        gui.set_value("sl_refresh_interval", interval)
        state.live_refresh_interval = interval
    except (ValueError, TypeError):
        gui.set_value("sl_refresh_interval", 60)
    try:
        gui.set_value("sl_demo_threshold", float(plugin_cfg.get("demo_threshold", 50.0)))
    except (ValueError, TypeError):
        gui.set_value("sl_demo_threshold", 50.0)

    # Seed batch_items into auto-state (complex structured data)
    default_batch = [
        {"address": 0x0, "label": "Example addr", "value_type": "hex",
         "length": 8, "value": "..."},
    ]
    gui.set_value("batch_items", default_batch)

    # Seed per-item edit fields from batch_items (auto-state)
    _seed_batch_edits(gui, default_batch)

    while True:
        # --- Read events from frontend ---
        gui.begin_frame()

        # --- Handle events from previous frame ---
        if gui.was_clicked("btn_refresh"):
            state.refresh_requested = True

        if gui.was_clicked("btn_read_mem"):
            addr_str = str(gui.get_value("input_address", "0x0"))
            try:
                addr = int(addr_str, 16) if addr_str.lower().startswith("0x") else int(addr_str)
            except (ValueError, TypeError):
                addr = 0
            length = int(gui.get_value("input_length", 256))

            if addr > 0 and state.api is not None:
                try:
                    state.read_result = state.api.memory_read(addr, length, pid=state.selected_pid)
                    state.read_error = ""
                    state.status = f"Read {len(state.read_result)} bytes from 0x{addr:X}"
                except MemDBGError as exc:
                    state.read_error = str(exc)
                    state.read_result = b""
            else:
                state.read_error = "Invalid address or no connection"

        # Persist widget values to config on edit
        if gui.was_edited("input_address"):
            plugin_cfg.set("address", gui.get_value("input_address", "0x0"))
            plugin_cfg.save()

        if gui.was_edited("input_length"):
            plugin_cfg.set("length", gui.get_value("input_length", 256))
            plugin_cfg.save()

        if gui.was_toggled("cb_live_refresh"):
            plugin_cfg.set("live_refresh", gui.get_value("cb_live_refresh", False))
            plugin_cfg.save()

        if gui.was_edited("sl_refresh_interval"):
            interval = int(gui.get_value("sl_refresh_interval", 60))
            state.live_refresh_interval = interval
            plugin_cfg.set("refresh_interval", interval)
            plugin_cfg.save()

        if gui.was_edited("sl_demo_threshold"):
            plugin_cfg.set("demo_threshold", gui.get_value("sl_demo_threshold", 50.0))
            plugin_cfg.save()

        # Combo auto-state tracks the selected index — sync to state.selected_pid
        if gui.was_edited("combo_process"):
            idx = int(gui.get_value("combo_process", 0))
            if 0 <= idx < len(state.processes):
                state.selected_pid = int(state.processes[idx].get("pid", 0))
                state.status = f"Selected PID {state.selected_pid}"

        # --- Background refresh ---
        if state.refresh_requested:
            if state.api is not None:
                try:
                    state.processes = state.api.process_list()
                    # Seed the combo auto-state with the index of the current PID
                    selected_idx = 0
                    for i, p in enumerate(state.processes):
                        if p.get("pid", 0) == state.selected_pid:
                            selected_idx = i
                            break
                    gui.set_value("combo_process", selected_idx)
                    state.status = f"Loaded {len(state.processes)} processes"
                except MemDBGError as exc:
                    state.status = f"Process list error: {exc}"
            state.refresh_requested = False

        # --- Live-refresh timer ---
        if gui.get_value("cb_live_refresh", False) and state.api is not None:
            state.live_refresh_timer -= 1
            if state.live_refresh_timer <= 0:
                state.refresh_requested = True
                state.live_refresh_timer = state.live_refresh_interval

        # --- Build widget tree ---
        # (read live slider/batch values for use in build_ui)
        _demo_threshold = float(gui.get_value("sl_demo_threshold", 50.0))
        batch_items: List[Dict[str, Any]] = gui.get_value("batch_items", [])

        # Sync editable batch fields back to batch_items on user edit
        _sync_batch_edits(gui, batch_items)

        build_ui(gui, state, _demo_threshold, batch_items)

        # --- Flush to frontend ---
        gui.end_frame()

        # Frame rate limiting (60 FPS max)
        time.sleep(0.016)


def build_ui(gui: GuiBuilder, state: PluginState,
              demo_threshold: float = 50.0,
              batch_items: List[Dict[str, Any]] | None = None) -> None:
    """Construct the ImGui widget tree for this frame."""

    if batch_items is None:
        batch_items = []

    # --- Header ---
    gui.text_colored("MemDBG Example GUI Plugin", "primary2")
    gui.separator()
    gui.spacing()

    # --- Status ---
    gui.text(state.status, color="muted")
    gui.spacing()

    # --- Connection info ---
    if state.api is not None:
        gui.text("Payload connected", color="success")
    else:
        gui.text("No payload connection", color="danger")
    gui.spacing()

    # --- Process selector ---
    gui.text("Target Process", color="primary")
    process_names = [f"PID {p.get('pid', 0)}: {p.get('name', '?')}" for p in state.processes]
    if process_names:
        gui.combo("combo_process", "Process", items=process_names)
    else:
        gui.text("No processes loaded", color="dim")

    gui.same_line()
    gui.button("btn_refresh", "Refresh", variant="soft")
    gui.same_line()
    gui.checkbox("cb_live_refresh", "Live Refresh")
    gui.spacing()

    # --- Slider demo (auto-state persists values across frames) ---
    gui.text("Settings", color="primary")
    gui.slider_int("sl_refresh_interval", "Refresh interval (frames)",
                   min_val=10, max_val=600)
    gui.slider_float("sl_demo_threshold", "Demo threshold",
                     min_val=0.0, max_val=100.0)
    gui.text(f"  Threshold: {demo_threshold:.1f}  |  Interval: {state.live_refresh_interval} frames",
             color="dim")
    gui.spacing()

    # --- Memory reader ---
    gui.text("Memory Read", color="primary")
    gui.input_text("input_address", "Address", hint="0x...")
    gui.same_line()
    gui.input_int("input_length", "Bytes", step=1, step_fast=256)
    gui.spacing()
    gui.button("btn_read_mem", "Read Memory", variant="primary")

    # Show read result
    if state.read_result:
        gui.spacing()
        hex_str = state.read_result[:256].hex(" ").upper()
        gui.text(f"Result ({len(state.read_result)} bytes):", color="success")
        gui.text(hex_str, color="dim")
    if state.read_error:
        gui.text(state.read_error, color="danger")

    gui.spacing()
    gui.separator()

    # --- Batch Read Table ---
    gui.text("Batch Memory Viewer", color="primary")
    gui.spacing()

    # Handle batch refresh
    if gui.was_batch_refreshed("batch_view"):
        raw_items = gui.event_value("batch_view", "[]")
        try:
            items = json.loads(raw_items) if isinstance(raw_items, str) else raw_items
            if isinstance(items, list) and items and state.api is not None:
                try:
                    batch_results = state.api.batch_read(items, pid=state.selected_pid)
                    for r in batch_results:
                        addr = r.get("address", 0)
                        for item_def in batch_items:
                            if item_def.get("address", 0) == addr:
                                if r.get("status", 0) == 0 and r.get("data"):
                                    raw = base64.b64decode(r["data"])
                                    vt = item_def.get("value_type", "hex")
                                    item_def["value"] = format_value(raw, vt)
                                else:
                                    item_def["value"] = f"err {r.get('status')}"
                                break
                    # Persist updated items back to auto-state
                    gui.set_value("batch_items", batch_items)
                    state.status = f"Batch read: {len(batch_results)} regions"
                except Exception as exc:
                    state.read_error = f"Batch read failed: {exc}"
        except Exception:
            pass

    # --- Editable batch item fields ---
    gui.text("Edit Items", color="primary")
    for i, item in enumerate(batch_items):
        widget_id_addr = f"batch_addr_{i}"
        widget_id_label = f"batch_label_{i}"
        gui.input_text(widget_id_addr, f"Addr #{i}",
                       hint="0x...")
        gui.same_line()
        gui.input_text(widget_id_label, f"Label #{i}",
                       hint="name")
    gui.spacing()

    gui.batch_read_table("batch_view", items=batch_items, height=180)

    # --- Process list table ---
    gui.text("Process Table", color="primary")
    if state.processes:
        headers = ["PID", "Name"]
        rows = [[str(p.get("pid", "?")), p.get("name", "?")]
                for p in state.processes[:20]]
        gui.table("table_procs", headers=headers, rows=rows, height=200)
    else:
        gui.text("Click Refresh to load processes", color="dim")


def _seed_batch_edits(gui: GuiBuilder, items: List[Dict[str, Any]]) -> None:
    """Seed per-item input fields from an initial batch_items list."""
    for i, item in enumerate(items):
        gui.set_value(f"batch_addr_{i}", f"0x{item.get('address', 0):X}")
        gui.set_value(f"batch_label_{i}", str(item.get("label", "")))


def _sync_batch_edits(gui: GuiBuilder, items: List[Dict[str, Any]]) -> None:
    """Detect edits on batch_addr_N / batch_label_N and persist to items."""
    changed = False
    for i, item in enumerate(items):
        addr_id = f"batch_addr_{i}"
        label_id = f"batch_label_{i}"
        if gui.was_edited(addr_id):
            hex_str = str(gui.get_value(addr_id, "0x0"))
            try:
                item["address"] = int(hex_str, 16)
            except (ValueError, TypeError):
                pass
            changed = True
        if gui.was_edited(label_id):
            item["label"] = str(gui.get_value(label_id, ""))
            changed = True
    if changed:
        gui.set_value("batch_items", items)


def format_value(raw: bytes, value_type: str) -> str:
    """Format raw bytes as a display string based on value_type."""
    vt = value_type.lower()
    if vt == "u8" and len(raw) >= 1:
        return str(raw[0])
    elif vt == "u16" and len(raw) >= 2:
        return str(struct.unpack_from("<H", raw, 0)[0])
    elif vt == "u32" and len(raw) >= 4:
        return str(struct.unpack_from("<I", raw, 0)[0])
    elif vt == "u64" and len(raw) >= 8:
        return str(struct.unpack_from("<Q", raw, 0)[0])
    elif vt in ("float", "f32") and len(raw) >= 4:
        return f"{struct.unpack_from('<f', raw, 0)[0]:.4f}"
    elif vt in ("double", "f64") and len(raw) >= 8:
        return f"{struct.unpack_from('<d', raw, 0)[0]:.4f}"
    else:
        return raw[:16].hex(" ").upper()


if __name__ == "__main__":
    raise SystemExit(main())
