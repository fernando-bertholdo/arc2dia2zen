"""What still works under the old name after the rename to arc2dia2zen (0.2.0)."""
import json
import subprocess
import sys
from pathlib import Path

from arc2dia2zen import cli
from arc2dia2zen.fsutil import DEFAULT_BACKUP_ROOT, LEGACY_BACKUP_ROOT
from arc2dia2zen.plan import plan_from_json

ROOT = Path(__file__).resolve().parent.parent


def test_old_plan_key_still_loads():
    plan = plan_from_json({"arc2dia_plan_version": 1, "spaces": []})
    assert plan.spaces == []
    assert "arc2dia2zen_plan_version" in plan.to_json()


def test_backups_from_before_the_rename_are_found(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    default = str(DEFAULT_BACKUP_ROOT)
    assert cli.backup_root(default) == default
    (tmp_path / "arc2dia-backups").mkdir()
    assert cli.backup_root(default) == str(LEGACY_BACKUP_ROOT)
    (tmp_path / "arc2dia2zen-backups").mkdir()
    assert cli.backup_root(default) == default
    assert cli.backup_root("/somewhere/else") == "/somewhere/else"


def test_old_module_name_still_runs():
    r = subprocess.run([sys.executable, "-m", "arc2dia", "--version"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0
    assert "arc2dia2zen" in r.stdout
    assert "arc2dia is now arc2dia2zen" in r.stderr
