#!/usr/bin/env python3
"""Verify every SPA TabId has a render case in App.tsx."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
HEADER = ROOT / "frontend" / "src" / "components" / "Header.tsx"
APP = ROOT / "frontend" / "src" / "App.tsx"
TS_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"


def tab_ids(source: str) -> set[str]:
    union = re.search(r"export type TabId\s*=\s*(.*?)(?:\n\n|\nexport )", source, re.S)
    if not union:
        raise ValueError("could not find the TabId union in Header.tsx")
    members = set(re.findall(rf"\|\s*'({TS_IDENTIFIER})'", union.group(1)))
    if not members:
        raise ValueError("the TabId union has no parsed members")
    return members


def render_switch(source: str) -> str:
    switch = re.search(r"switch\s*\(\s*tab\s*\)\s*\{", source)
    if not switch:
        raise ValueError("could not find switch (tab) in App.tsx")

    start = switch.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise ValueError("switch (tab) has no closing brace in App.tsx")


def main() -> int:
    try:
        declared = tab_ids(HEADER.read_text())
        rendered = set(re.findall(
            rf"case\s+'({TS_IDENTIFIER})'\s*:", render_switch(APP.read_text())
        ))
    except (OSError, ValueError) as error:
        print(f"Tab render check failed: {error}", file=sys.stderr)
        return 1

    missing = sorted(declared - rendered)
    if missing:
        print("TabIds missing a render case in App.tsx:", file=sys.stderr)
        for tab_id in missing:
            print(f"  - {tab_id}", file=sys.stderr)
        return 1

    print(f"{len(declared)} TabIds have render cases in App.tsx.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
