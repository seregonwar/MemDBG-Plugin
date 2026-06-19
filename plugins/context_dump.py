#!/usr/bin/env python3
# MemDBG plugin example - Python context reader.
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import pathlib
import sys

from memdbg import MemDBG, MemDBGError


def main() -> int:
    if len(sys.argv) < 2:
        print("MemDBG did not provide a context file.")
        return 2

    context_path = pathlib.Path(sys.argv[1])
    with context_path.open("r", encoding="utf-8") as handle:
        context = json.load(handle)

    console = context.get("console", {})
    process = context.get("process", {})
    state = context.get("state", {})
    paths = context.get("paths", {})

    print("MemDBG Python plugin context")
    print(f"Console: {console.get('host')}:{console.get('debug_port')} connected={console.get('connected')}")
    print(f"Process: pid={process.get('pid')} name={process.get('name')}")
    print(f"Maps: {state.get('map_count')}  scan hits: {state.get('scan_hit_count')}  trainer entries: {state.get('trainer_entry_count')}")
    print(f"Dump path: {paths.get('dump')}")
    print(f"Trainer file: {paths.get('trainer')}")

    if console.get("connected"):
        try:
            api = MemDBG.from_context(str(context_path))
            hello = api.hello()
            print(f"Payload: {hello.get('name')} protocol={hello.get('protocol_version')} caps=0x{hello.get('capabilities'):X}")
            processes = api.process_list()
            print(f"API process_list(): {len(processes)} processes")
        except MemDBGError as exc:
            print(f"API unavailable: {exc}")
    else:
        print("API unavailable: console offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
