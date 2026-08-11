#!/usr/bin/env python3
"""Mirror the session's todo list into the progress report's task file.

Run as a PostToolUse hook on TodoWrite, reading the hook payload on stdin.
The periodic mail's "いま進めているタスク" section used to be a file someone
had to remember to update, so it described last night's work by morning. This
keeps it in step with the todo list automatically: whatever the session is
actually tracking is what the next report shows.

Writes nothing and exits 0 on any malformed input -- a reporting convenience
must never interrupt the work it reports on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_TARGET = Path(".progress-tasks.json")

STATES = {
    "completed": ("完了", "done"),
    "in_progress": ("実行中", "now"),
}
PENDING = ("未着手", "wait")


def convert(todos: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn todo entries into the rows the report renders."""

    total = len(todos)
    tasks = []
    for index, todo in enumerate(todos, 1):
        label, tone = STATES.get(str(todo.get("status", "")), PENDING)
        tasks.append(
            {
                "step": f"{index}/{total}",
                "title": str(todo.get("content", "")),
                "estimate": "—",
                "state": label,
                "tone": tone,
            }
        )
    return {"tasks": tasks}


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    try:
        payload = json.load(sys.stdin)
        todos = payload["tool_input"]["todos"]
        if not isinstance(todos, list):
            raise TypeError("todos must be a list")
    except (ValueError, KeyError, TypeError):
        return 0
    target.write_text(
        json.dumps(convert(todos), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
