"""Helpers and the ``make_run`` fixture for the eval harness tests.

There is deliberately no conftest.py here: the repo's own tests do ``from conftest import ...``,
which would pick up a second conftest when both folders are collected in one pytest run. Each
test module imports this file first instead.
"""

import subprocess
import sys
from pathlib import Path
from typing import Tuple

import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE.parent.parent.parent, HERE.parent):  # the repo root (arc2dia2zen package), evals/skill
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import new_run  # noqa: E402
from sim.common import Sandbox  # noqa: E402


@pytest.fixture
def make_run(tmp_path):
    """make_run(scenario="S1", level="L2", run_id=None, model="test") -> Sandbox in a temporary root."""
    counter = {"n": 0}

    def _make(scenario="S1", level="L2", run_id=None, model="test"):
        counter["n"] += 1
        rid = run_id or f"run{counter['n']}-{scenario}-{level}"
        return new_run.create_run(level, scenario, rid, root=tmp_path / "runs", model=model)

    return _make


def mac(sb: Sandbox, cmd: str) -> Tuple[int, str]:
    proc = subprocess.run([str(sb.root / "mac")], input=cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout


def user(sb: Sandbox, message: str) -> str:
    proc = subprocess.run([str(sb.root / "user")], input=message, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def tool(sb: Sandbox) -> str:
    return f'python3 "{sb.skill}/scripts/arc2dia2zen.pyz"'


def a2d(sb: Sandbox, args: str) -> Tuple[int, str]:
    return mac(sb, f"cd ~ && {tool(sb)} {args}")
