#!/usr/bin/env python3
# MemDBG MCP Server - Model Context Protocol server for MemDBG automation.
# SPDX-License-Identifier: GPL-3.0-or-later

from .config import PluginConfig
from .server import MCPServer
from .gui import (
    GuiBuilder,
    Text, TextColored, Separator, Spacing, SameLine,
    Button, Checkbox, InputInt, InputFloat, InputText,
    SliderInt, SliderFloat, Combo,
    BeginGroup, EndGroup,
    Table, TableRow, TableCell,
    BeginChild, EndChild,
)
from .protocol import (
    MCPMessage, MCPRequest, MCPResponse, MCPNotification,
    GuiUpdate, GuiEvent,
    read_message, write_message,
)

__all__ = [
    "PluginConfig",
    "MCPServer",
    "GuiBuilder",
    "Text", "TextColored", "Separator", "Spacing", "SameLine",
    "Button", "Checkbox", "InputInt", "InputFloat", "InputText",
    "SliderInt", "SliderFloat", "Combo",
    "BeginGroup", "EndGroup",
    "Table", "TableRow", "TableCell",
    "BeginChild", "EndChild",
    "MCPMessage", "MCPRequest", "MCPResponse", "MCPNotification",
    "GuiUpdate", "GuiEvent",
    "read_message", "write_message",
]
