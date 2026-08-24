"""The shell scripts have to parse.

``scripts/start_dashboard.sh`` was committed truncated -- it ended mid-word, in
the middle of an `export`, with no `streamlit run` line at all. Nothing caught
it, because nothing here had ever looked at a shell script: the Python suite
was green, CI was green, and the one command the operator uses to open the
dashboard could not run.

``bash -n`` parses without executing, so this is safe to run against every
script including the ones that start servers or talk to the database.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = sorted(Path("scripts").glob("*.sh"))


def test_there_are_shell_scripts_to_check() -> None:
    """A glob that silently matches nothing would make every test below vacuous."""

    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_script_parses(script: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available")

    result = subprocess.run(
        [bash, "-n", str(script)], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, f"{script}: {result.stderr.strip()}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_script_is_not_truncated(script: Path) -> None:
    """A file cut off mid-line is the shape the dashboard launcher shipped in."""

    body = script.read_text(encoding="utf-8")

    assert body.endswith("\n"), f"{script} has no trailing newline"
    assert body.strip(), f"{script} is empty"


def test_the_dashboard_launcher_actually_launches_the_dashboard() -> None:
    """Parsing is not enough: the truncated version parsed as far as it went."""

    body = Path("scripts/start_dashboard.sh").read_text(encoding="utf-8")

    assert "streamlit run app.py" in body
    assert "--server.port" in body
