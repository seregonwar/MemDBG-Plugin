-- MemDBG plugin example - Lua context summary.
-- SPDX-License-Identifier: GPL-3.0-or-later

local path = arg and arg[1]
if not path then
  print("MemDBG did not provide a context file.")
  os.exit(2)
end

local file = io.open(path, "rb")
if not file then
  print("Cannot open context file: " .. tostring(path))
  os.exit(2)
end

local text = file:read("*a")
file:close()

local function grab_string(key)
  return text:match('"' .. key .. '"%s*:%s*"([^"]*)"') or ""
end

local function grab_number(key)
  return text:match('"' .. key .. '"%s*:%s*([%d%-]+)') or "0"
end

local host = grab_string("host")
local pid = grab_number("pid")
local name = grab_string("name")
local maps = grab_number("map_count")
local hits = grab_number("scan_hit_count")
local cheats = grab_number("trainer_entry_count")

print("MemDBG Lua session brief")
print("Console: " .. host)
print("Process: pid=" .. pid .. " name=" .. name)
print("Maps: " .. maps .. "  scan hits: " .. hits .. "  trainer entries: " .. cheats)
