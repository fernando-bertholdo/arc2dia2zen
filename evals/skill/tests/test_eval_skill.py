"""The skill itself: the three SKILL.md variants, the shipped copy, the zipapp, and the prompt."""

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from evalhelpers import make_run  # noqa: F401  (fixture)
import new_run
from sim.common import REPO_ROOT

VARIANTS = {lv: (new_run.VARIANTS / lv / "SKILL.md").read_text(encoding="utf-8") for lv in new_run.LEVELS}
SHIPPED = REPO_ROOT / "skill" / "arc2dia2zen" / "SKILL.md"
PYZ = REPO_ROOT / "skill" / "arc2dia2zen" / "scripts" / "arc2dia2zen.pyz"


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "missing YAML frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_frontmatter_is_the_same_everywhere():
    fms = [frontmatter(t) for t in VARIANTS.values()] + [frontmatter(SHIPPED.read_text(encoding="utf-8"))]
    assert all(fm == fms[0] for fm in fms)
    fm = fms[0]
    assert set(fm) == {"name", "description"}
    assert fm["name"] == "arc2dia2zen" == SHIPPED.parent.name
    assert 100 < len(fm["description"]) <= 1024 and "Use when" in fm["description"]


def test_shipped_skill_is_l3():
    assert SHIPPED.read_text(encoding="utf-8") == VARIANTS["L3"]


def test_lengths():
    n = {lv: len(t.splitlines()) for lv, t in VARIANTS.items()}
    assert 30 <= n["L1"] <= 50
    assert n["L1"] < n["L2"] < n["L3"]


@pytest.mark.parametrize("level", new_run.LEVELS)
def test_every_level_carries_the_facts_and_rules(level):
    t = VARIANTS[level]
    for needle in ("2026", "StorableSidebar.json", "Import from Another Browser", "new", "open tabs",
                   '<skill dir>/scripts/arc2dia2zen.pyz"', "scan", "plan", "prepare --plan arc2dia2zen-plan.json",
                   "swap --apply", "restore --apply", "verify --prepared arc2dia2zen-out", "status",
                   "open <url>", "Arc Sync", "delete", "kill", "explicit", "language"):
        assert needle in t, (level, needle)


def test_l2_and_l3_have_checkpoints_and_the_failure_path():
    for lv in ("L2", "L3"):
        t = VARIANTS[lv]
        assert t.count("Checkpoint:") >= 7
        assert "Import failed" in t and "restore" in t.lower()


def test_l3_has_templates_decision_table_and_checklist():
    t = VARIANTS["L3"]
    assert t.count("Say (template)") >= 4 and "## Final checklist" in t and t.count("- [ ]") >= 5
    for row in ("Arc still running at swap", "Import failed", "User wants to open Arc mid-flow",
                "verify shows missing items", "swap refused: file changed"):
        assert f"| {row} |" in t
    # command blocks are spelled out in full, never with the A2D shorthand
    for line in t.splitlines():
        if line.startswith("    cd ~"):
            assert '"<skill dir>/scripts/arc2dia2zen.pyz"' in line, line


def test_pyz_runs_and_matches_the_package():
    assert new_run.pyz_matches_package(PYZ)
    proc = subprocess.run([sys.executable, str(PYZ), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "scan" in proc.stdout and "verify" in proc.stdout
    proc = subprocess.run([sys.executable, str(PYZ), "scan", "--sidebar", "/nonexistent"], capture_output=True, text=True)
    assert proc.returncode == 2  # exit codes pass through the zipapp's __main__


def _python39():
    for cand in [shutil.which("python3.9"),
                 *Path.home().glob(".local/share/uv/python/cpython-3.9*/bin/python3.9")]:
        if cand and Path(cand).exists():
            return str(cand)
    return None


@pytest.mark.skipif(_python39() is None, reason="no Python 3.9 interpreter found")
def test_pyz_runs_on_python_3_9():
    py = _python39()
    proc = subprocess.run([py, str(PYZ), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    example = REPO_ROOT / "examples" / "synthetic-StorableSidebar.json"
    proc = subprocess.run([py, str(PYZ), "plan", "--print", "--sidebar", str(example)], capture_output=True, text=True)
    assert proc.returncode == 0 and '"spaces"' in proc.stdout


def test_build_script_rebuilds_the_same_contents(tmp_path):
    out = tmp_path / "arc2dia2zen.pyz"
    env = dict(os.environ, ARC2DIA2ZEN_PYZ_OUT=str(out), PYTHON=sys.executable)
    proc = subprocess.run([str(REPO_ROOT / "scripts" / "build_skill.sh")], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    with zipfile.ZipFile(out) as a, zipfile.ZipFile(PYZ) as b:
        assert sorted(a.namelist()) == sorted(b.namelist())
        assert all(a.read(n) == b.read(n) for n in a.namelist())
    assert out.read_bytes()[:2] == b"#!"


def test_prompt_has_the_skill_the_channels_and_the_rules(make_run):
    sb = make_run("S2", "L3")
    prompt = (sb.root / "PROMPT.md").read_text(encoding="utf-8")
    assert VARIANTS["L3"].rstrip("\n") in prompt
    assert str(sb.skill) in prompt and f"{sb.root}/mac '<shell command>'" in prompt
    assert f"{sb.root}/user '<message>'" in prompt and f"{sb.root}/report.md" in prompt
    assert new_run.OPENING_REQUEST in prompt and "Do not inspect the evaluation harness" in prompt
    assert (sb.skill / "SKILL.md").read_text(encoding="utf-8") == VARIANTS["L3"]
    meta = sb.meta()
    assert meta["level"] == "L3" and meta["scenario"] == "S2" and len(meta["original_sha256"]) == 64


def test_new_run_refuses_an_existing_run_id(make_run):
    sb = make_run(run_id="same")
    with pytest.raises(SystemExit):
        new_run.create_run("L2", "S1", "same", root=sb.root.parent)
