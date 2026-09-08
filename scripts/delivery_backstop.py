"""Decide whether a window needs firing again, from the watchdog's verdict.

GitHub's scheduler is best-effort. On 2026-09-08 it produced no run at all for
either evening workflow -- close_update's three ticks and daily_summary's three
ticks all silently did not happen -- while the morning's fired 1.5 to 3.6 hours
late. More cron ticks cannot fix a scheduler that fires none of them, so a
second trigger runs from a machine that is always on and calls this to decide
whether to act.

The decision is here, in Python, rather than inline in the shell that runs it,
because getting it wrong is expensive in both directions: repairing a window
that was merely early re-runs a pipeline for no reason, and declining to repair
a genuinely missing one is the silence the operator asked never to have again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# What each window must have produced. Deliberately narrow: these are outcomes
# the pipeline is supposed to create, so a false one means a run is missing.
WATCHED: dict[str, tuple[str, ...]] = {
    "morning": ("prediction", "email"),
    "evening": ("actuals", "summary"),
}

# ``automation`` is excluded on purpose. AUTOMATION_ENABLED being off is a
# decision someone made, and dispatching the workflows anyway would override an
# operator who deliberately stopped the pipeline. It is also false whenever this
# runs outside Actions, where the variable simply is not in the environment --
# repairing on it would fire the pipeline every single day.
NEVER_REPAIRED_ON = frozenset({"automation", "db_capacity", "dashboard"})


def parse_verdict(raw: str) -> dict[str, Any] | None:
    """The watchdog's JSON line, or ``None`` when there is not one.

    No verdict means the check itself failed, and a backstop that treats "I
    could not tell" as "it is broken" would re-run the pipeline on every
    network blip.
    """

    for line in reversed(raw.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "checks" in parsed:
            return parsed
    return None


def needs_repair(verdict: Mapping[str, Any] | None, window: str) -> bool:
    """Is an outcome this window owes actually missing?

    ``NOT_YET_DUE`` and a JPX holiday both come back as something other than
    ``CHECKED``; neither is a failure, and firing on them would run the evening
    pipeline at lunchtime and on days the market never opened.
    """

    if verdict is None:
        return False
    if verdict.get("verdict") != "CHECKED":
        return False
    watched = WATCHED.get(window)
    if not watched:
        raise ValueError(f"unknown window: {window}")
    checks = verdict.get("checks") or {}
    if not isinstance(checks, Mapping):
        return False
    return any(
        not (checks.get(name) or {}).get("ok", True)
        for name in watched
        if name not in NEVER_REPAIRED_ON
    )


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when the window needs firing again, 1 when it does not.

    Shell convention rather than Python's: the caller is a shell script whose
    ``if`` reads an exit status.
    """

    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("window", choices=sorted(WATCHED))
    parser.add_argument("--verdict", default="-", help="JSON, or - for stdin")
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.verdict == "-" else args.verdict
    return 0 if needs_repair(parse_verdict(raw), args.window) else 1


if __name__ == "__main__":
    raise SystemExit(main())
