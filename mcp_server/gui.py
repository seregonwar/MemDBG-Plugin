#!/usr/bin/env python3
# MemDBG MCP Server - Python-side ImGui widget builder.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# GuiBuilder lets Python plugins describe an ImGui widget tree that the
# C++ frontend renders natively.  Widgets are declared imperatively
# (immediate-mode style) and serialised to a JSON update pushed to the
# frontend via stdin.

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Union

from .protocol import GuiUpdate, write_message


# ---------------------------------------------------------------------------
# Widget constructors (return plain dicts)
# ---------------------------------------------------------------------------

def _w(type_: str, **kwargs: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": type_}
    d.update(kwargs)
    return d


def Text(text: str = "", color: str = "") -> Dict[str, Any]:
    return _w("text", text=str(text), color=color)


def TextColored(text: str = "", color: str = "primary") -> Dict[str, Any]:
    return _w("text_colored", text=str(text), color=color)


def Separator() -> Dict[str, Any]:
    return _w("separator")


def Spacing(count: int = 1) -> Dict[str, Any]:
    return _w("spacing", count=count)


def SameLine(offset: float = 0.0, spacing: float = -1.0) -> Dict[str, Any]:
    return _w("same_line", offset=offset, spacing=spacing)


def Button(widget_id: str, label: str, variant: str = "primary",
           width: float = 0.0, height: float = 0.0,
           disabled: bool = False) -> Dict[str, Any]:
    return _w("button", id=widget_id, label=str(label),
              variant=variant, width=width, height=height,
              disabled=disabled)


def Checkbox(widget_id: str, label: str, checked: bool = False) -> Dict[str, Any]:
    return _w("checkbox", id=widget_id, label=str(label), checked=checked)


def InputInt(widget_id: str, label: str = "", value: int = 0,
             step: int = 1, step_fast: int = 100,
             width: float = 0.0) -> Dict[str, Any]:
    return _w("input_int", id=widget_id, label=str(label),
              value=value, step=step, step_fast=step_fast, width=width)


def InputFloat(widget_id: str, label: str = "", value: float = 0.0,
               step: float = 0.1, step_fast: float = 1.0,
               width: float = 0.0) -> Dict[str, Any]:
    return _w("input_float", id=widget_id, label=str(label),
              value=value, step=step, step_fast=step_fast, width=width)


def InputText(widget_id: str, label: str = "", value: str = "",
              hint: str = "", width: float = 0.0,
              multiline: bool = False) -> Dict[str, Any]:
    return _w("input_text", id=widget_id, label=str(label),
              value=str(value), hint=str(hint), width=width,
              multiline=multiline)


def SliderInt(widget_id: str, label: str = "", value: int = 0,
              min_val: int = 0, max_val: int = 100,
              width: float = 0.0) -> Dict[str, Any]:
    return _w("slider_int", id=widget_id, label=str(label),
              value=value, min=min_val, max=max_val, width=width)


def SliderFloat(widget_id: str, label: str = "", value: float = 0.0,
                min_val: float = 0.0, max_val: float = 1.0,
                width: float = 0.0) -> Dict[str, Any]:
    return _w("slider_float", id=widget_id, label=str(label),
              value=value, min=min_val, max=max_val, width=width)


def Combo(widget_id: str, label: str = "", items: Optional[List[str]] = None,
          selected: int = 0, width: float = 0.0) -> Dict[str, Any]:
    return _w("combo", id=widget_id, label=str(label),
              items=items or [], selected=selected, width=width)


def BeginGroup() -> Dict[str, Any]:
    return _w("begin_group")


def EndGroup() -> Dict[str, Any]:
    return _w("end_group")


def BeginChild(child_id: str, width: float = 0.0, height: float = 0.0,
               border: bool = True) -> Dict[str, Any]:
    return _w("begin_child", id=child_id, width=width, height=height, border=border)


def EndChild() -> Dict[str, Any]:
    return _w("end_child")


def Table(table_id: str, headers: List[str], rows: List[List[str]],
          width: float = 0.0, height: float = 0.0) -> Dict[str, Any]:
    return _w("table", id=table_id, headers=headers, rows=rows,
              width=width, height=height)


def TableRow(row: List[str]) -> Dict[str, Any]:
    return _w("table_row", cells=row)


def TableCell(text: str = "") -> Dict[str, Any]:
    return _w("table_cell", text=str(text))


def BatchReadTable(table_id: str, items: List[Dict[str, Any]],
                   width: float = 0.0, height: float = 0.0) -> Dict[str, Any]:
    """Efficient multi-address memory-read widget.

    Each item dict should have:
      - ``address`` (int): the virtual address
      - ``label`` (str, optional): short description
      - ``value_type`` (str, optional): ``u8``, ``u32``, ``u64``, ``hex``, ``float``
      - ``value`` (str, optional): current display value string

    The C++ frontend renders a compact table showing addresses, labels,
    and formatted values with a refresh button.  When the user presses
    the refresh button, the frontend emits an ``ui_event`` with
    ``"event": "refresh_batch"`` and the items list as ``value`` (a
    JSON array of ``{address, length}`` so the plugin can issue a
    ``batch_read`` call and update the value strings.

    Use ``was_batch_refreshed(widget_id)`` to detect the refresh event.
    """
    return _w("batch_read_table", id=table_id, items=items,
              width=width, height=height)


def DataTable(table_id: str, headers: List[str], rows: List[List[str]],
              width: float = 0.0, height: float = 0.0) -> Dict[str, Any]:
    """Alias for Table – kept for API clarity."""
    return Table(table_id=table_id, headers=headers, rows=rows,
                 width=width, height=height)


# ---------------------------------------------------------------------------
# GuiBuilder — immediate-mode builder with event queue
# ---------------------------------------------------------------------------

_UNSET = object()


class GuiBuilder:
    """Imperative ImGui widget builder for Python plugins.

    Interactive widgets automatically persist their current value inside
    the builder across frames.  The plugin no longer needs to manually
    track values in a ``PluginState`` class — just call the widget method
    with a unique ``widget_id`` each frame and the builder remembers the
    last user-edited or programmatically-set value.

    To seed an initial value, call ``gui.set_value(id, value)`` once
    before the main loop, or pass ``value=`` on the first call to the
    widget method.  Subsequent calls can omit ``value=``; the builder
    will use the persisted state.

    Read the current value with ``gui.get_value(id, default)``.
    """

    def __init__(self, stdout=None) -> None:
        self._widgets: List[Dict[str, Any]] = []
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._widget_state: Dict[str, Any] = {}
        self._out = stdout or sys.stdout

    # ------------------------------------------------------------------
    # Per-frame lifecycle
    # ------------------------------------------------------------------

    def begin_frame(self) -> None:
        self._widgets.clear()
        self._process_stdin()

    def end_frame(self) -> None:
        self.flush()

    def flush(self) -> None:
        if not self._widgets:
            return
        update = GuiUpdate(widgets=list(self._widgets))
        write_message(update.to_message().to_dict(), self._out)
        self._widgets.clear()

    # ------------------------------------------------------------------
    # Programmatic state access
    # ------------------------------------------------------------------

    def get_value(self, widget_id: str, default: Any = None) -> Any:
        """Return the persisted value for *widget_id*, or *default*."""
        return self._widget_state.get(widget_id, default)

    def set_value(self, widget_id: str, value: Any) -> None:
        """Overwrite the persisted value for *widget_id*.

        The new value will be used when the widget is next rendered.
        """
        self._widget_state[widget_id] = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _actual(widget_id: str, state: Dict[str, Any],
                sentinel: Any, value: Any, default: Any) -> Any:
        """Return the effective value for a widget, updating state if needed."""
        if value is not sentinel:
            state[widget_id] = value
        return state.get(widget_id, default)

    # Display
    def text(self, text: str = "", color: str = "") -> GuiBuilder:
        self._widgets.append(Text(text=text, color=color))
        return self

    def text_colored(self, text: str = "", color: str = "primary") -> GuiBuilder:
        self._widgets.append(TextColored(text=text, color=color))
        return self

    def separator(self) -> GuiBuilder:
        self._widgets.append(Separator())
        return self

    def spacing(self, count: int = 1) -> GuiBuilder:
        self._widgets.append(Spacing(count=count))
        return self

    def same_line(self, offset: float = 0.0, spacing: float = -1.0) -> GuiBuilder:
        self._widgets.append(SameLine(offset=offset, spacing=spacing))
        return self

    # Interactive  (value= omitted -> persisted state used)
    def button(self, widget_id: str, label: str, variant: str = "primary",
               width: float = 0.0, height: float = 0.0,
               disabled: bool = False) -> GuiBuilder:
        self._widgets.append(Button(
            widget_id=widget_id, label=label, variant=variant,
            width=width, height=height, disabled=disabled,
        ))
        return self

    def checkbox(self, widget_id: str, label: str,
                 checked: Any = _UNSET) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, checked, False)
        self._widgets.append(Checkbox(
            widget_id=widget_id, label=label, checked=actual,
        ))
        return self

    def input_int(self, widget_id: str, label: str = "",
                  value: Any = _UNSET,
                  step: int = 1, step_fast: int = 100,
                  width: float = 0.0) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, value, 0)
        self._widgets.append(InputInt(
            widget_id=widget_id, label=label, value=actual,
            step=step, step_fast=step_fast, width=width,
        ))
        return self

    def input_float(self, widget_id: str, label: str = "",
                    value: Any = _UNSET,
                    step: float = 0.1, step_fast: float = 1.0,
                    width: float = 0.0) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, value, 0.0)
        self._widgets.append(InputFloat(
            widget_id=widget_id, label=label, value=actual,
            step=step, step_fast=step_fast, width=width,
        ))
        return self

    def input_text(self, widget_id: str, label: str = "",
                   value: Any = _UNSET,
                   hint: str = "", width: float = 0.0,
                   multiline: bool = False) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, value, "")
        self._widgets.append(InputText(
            widget_id=widget_id, label=label, value=actual,
            hint=hint, width=width, multiline=multiline,
        ))
        return self

    def slider_int(self, widget_id: str, label: str = "",
                   value: Any = _UNSET,
                   min_val: int = 0, max_val: int = 100,
                   width: float = 0.0) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, value, 0)
        self._widgets.append(SliderInt(
            widget_id=widget_id, label=label, value=actual,
            min_val=min_val, max_val=max_val, width=width,
        ))
        return self

    def slider_float(self, widget_id: str, label: str = "",
                     value: Any = _UNSET,
                     min_val: float = 0.0, max_val: float = 1.0,
                     width: float = 0.0) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, value, 0.0)
        self._widgets.append(SliderFloat(
            widget_id=widget_id, label=label, value=actual,
            min_val=min_val, max_val=max_val, width=width,
        ))
        return self

    def combo(self, widget_id: str, label: str = "",
              items: Optional[List[str]] = None,
              selected: Any = _UNSET, width: float = 0.0) -> GuiBuilder:
        actual = self._actual(widget_id, self._widget_state,
                              _UNSET, selected, 0)
        self._widgets.append(Combo(
            widget_id=widget_id, label=label,
            items=items or [], selected=actual, width=width,
        ))
        return self

    # Layout
    def begin_group(self) -> GuiBuilder:
        self._widgets.append(BeginGroup())
        return self

    def end_group(self) -> GuiBuilder:
        self._widgets.append(EndGroup())
        return self

    def begin_child(self, child_id: str, width: float = 0.0,
                    height: float = 0.0, border: bool = True) -> GuiBuilder:
        self._widgets.append(BeginChild(
            child_id=child_id, width=width, height=height, border=border,
        ))
        return self

    def end_child(self) -> GuiBuilder:
        self._widgets.append(EndChild())
        return self

    # Tables
    def table(self, table_id: str, headers: List[str], rows: List[List[str]],
              width: float = 0.0, height: float = 0.0) -> GuiBuilder:
        self._widgets.append(Table(
            table_id=table_id, headers=headers, rows=rows,
            width=width, height=height,
        ))
        return self

    def data_table(self, table_id: str, headers: List[str],
                   rows: List[List[str]],
                   width: float = 0.0, height: float = 0.0) -> GuiBuilder:
        """Alias for table()."""
        return self.table(table_id=table_id, headers=headers, rows=rows,
                          width=width, height=height)

    def batch_read_table(self, table_id: str, items: List[Dict[str, Any]],
                         width: float = 0.0, height: float = 0.0) -> GuiBuilder:
        """Add a batch-read memory table widget.

        Args:
            table_id: Unique widget identifier.
            items: List of {address, label?, value_type?, value?} dicts.
                   value_type can be \"u8\", \"u16\", \"u32\", \"u64\",
                   \"hex\", \"float\", \"double\".
                   value is the current display string for the data.
        """
        self._widgets.append(BatchReadTable(
            table_id=table_id, items=items, width=width, height=height,
        ))
        return self

    # Event helpers
    def was_clicked(self, widget_id: str) -> bool:
        for evt in self._events.get(widget_id, []):
            if evt.get("event") == "click":
                return True
        return False

    def was_edited(self, widget_id: str) -> bool:
        for evt in self._events.get(widget_id, []):
            if evt.get("event") in ("edited", "deactivated", "selected"):
                return True
        return False

    def was_toggled(self, widget_id: str) -> bool:
        for evt in self._events.get(widget_id, []):
            if evt.get("event") == "toggled":
                return True
        return False

    def was_batch_refreshed(self, widget_id: str) -> bool:
        """True if a batch_read_table refresh button was clicked."""
        for evt in self._events.get(widget_id, []):
            if evt.get("event") == "refresh_batch":
                return True
        return False

    def event_value(self, widget_id: str, default: Any = None) -> Any:
        if widget_id in self._events:
            for evt in self._events[widget_id]:
                if "value" in evt:
                    return evt["value"]
        return default

    def _process_stdin(self) -> None:
        self._events.clear()
        from .protocol import read_message as _read
        try:
            while True:
                raw = _read()
                if raw is None:
                    break
                self._ingest_message(raw)
        except (EOFError, IOError, BlockingIOError):
            pass

    def _ingest_message(self, raw: Dict[str, Any]) -> None:
        method = raw.get("method", "")
        params = raw.get("params", {})

        if method == "ui_event":
            event = params.get("event", "")
            widget_id = params.get("id", params.get("widget_id", ""))
            value = params.get("value")
            if widget_id:
                # Auto-persist edited/toggled/selected values
                if event in ("edited", "toggled", "selected"):
                    self._widget_state[widget_id] = value
                self._events.setdefault(widget_id, []).append({
                    "event": event,
                    "id": widget_id,
                    "value": value,
                })
        elif method == "exit":
            raise SystemExit(0)
