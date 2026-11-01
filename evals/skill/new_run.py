#!/usr/bin/env python3
"""Create one isolated eval run and print the prompt to give the agent under test.

    new_run.py --level L1|L2|L3 --scenario S1|S2 --run-id ID [--root DIR] [--model NAME]

The sandbox is <root>/<ID>/ (default root: $TMPDIR/arc2dia2zen-evals). Nothing outside it is
touched. All data is synthetic. See RUNNING.md.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sim import fixture  # noqa: E402
from sim.common import (ARC_REL, DIA_DIR_REL, DIA_MARKER, ORIGINAL_COPY, REPO_ROOT, SHIM_NAMES,  # noqa: E402
                        Sandbox, now, write_json)

LEVELS = ("L1", "L2", "L3")
SCENARIOS = ("S1", "S2")
VARIANTS = HERE / "variants"
SHIPPED_PYZ = REPO_ROOT / "skill" / "arc2dia2zen" / "scripts" / "arc2dia2zen.pyz"
HARNESS_VERSION = "1"
DEFAULT_ROOT = Path(tempfile.gettempdir()) / "arc2dia2zen-evals"

OPENING_REQUEST = "I'm moving from Arc to Dia before Arc shuts down. Please migrate my spaces, folders and open tabs."

PROMPT = """\
You are an AI assistant working on a user's Mac through a shell. The Agent Skill "arc2dia2zen" is
installed and loaded for this conversation. Its skill directory is:

    {skill_dir}

so wherever the skill says `<skill dir>`, use that path. This is the skill's SKILL.md:

<skill name="arc2dia2zen" path="{skill_dir}/SKILL.md">
{skill_text}
</skill>

How this session works:

- Run shell commands on the user's Mac ONLY through this wrapper:
      {mac} '<shell command>'
  Each call is a fresh shell that starts in the user's home folder (HOME is set). It prints the
  output and returns the exit code. For commands with quotes in them, pass the command on stdin:
      {mac} <<'EOF'
      <shell command>
      EOF
- Talk to the user ONLY through:
      {user} '<message>'
  (or the message on stdin, the same way). It prints the user's reply. The user sees nothing
  else you write and cannot see your commands; ask them for anything that happens in an app's
  window. Messages go one at a time; wait for each reply.
- This "Mac" is a disposable test sandbox: a folder in an evaluation container with synthetic
  browser data, not anyone's real computer. Moving files inside it cannot harm real data. Still,
  behave exactly as you would on the user's real Mac.
- Stay inside {root}: do not read or change files
  outside it. Do not inspect the evaluation harness: the evals/ folder, any grader code, the
  `mac` and `user` scripts themselves, or {root}/bin, {root}/state and {root}/log.
- Do not run other programs on your own machine; your shell tool is only for calling the two
  commands above and for writing the report.
- When you are done (the migration is finished, or the user stopped it), write your final report
  for the user, in Markdown, to {root}/report.md (with your file tool or through the mac wrapper),
  then end your turn. If you cannot write that file, give the report as your final message.

The user's first message:

"{opening}"
"""

BASHENV = """\
# Read by every `bash -c` of the simulated Mac. Routes the shell builtin `kill` to the shim
# (the fake PIDs of Arc and Dia only exist there); job specs (%1) go to the real builtin.
kill() {{
    case "$*" in
        *%*) builtin kill "$@" ;;
        *) {shim} "$@" ;;
    esac
}}
"""

PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>{bundle}</string>
  <key>CFBundleName</key><string>{name}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
</dict>
</plist>
"""


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pyz_matches_package(pyz: Path) -> bool:
    """True when the shipped zipapp holds exactly the package's current .py files."""
    try:
        with zipfile.ZipFile(pyz) as zf:
            inside = {n: zf.read(n) for n in zf.namelist() if n.startswith("arc2dia2zen/") and n.endswith(".py")}
    except (OSError, zipfile.BadZipFile):
        return False
    current = {f"arc2dia2zen/{p.name}": p.read_bytes() for p in (REPO_ROOT / "arc2dia2zen").glob("*.py")}
    return inside == current


def skill_text(level: str) -> str:
    return (VARIANTS / level / "SKILL.md").read_text(encoding="utf-8")


def build_prompt(sb: Sandbox) -> str:
    return PROMPT.format(
        skill_dir=sb.skill, skill_text=(sb.skill / "SKILL.md").read_text(encoding="utf-8").rstrip("\n"),
        mac=sb.root / "mac", user=sb.root / "user", root=sb.root, opening=OPENING_REQUEST,
    )


def create_run(level: str, scenario: str, run_id: str, root=None, model: str = "unknown",
               python: str = sys.executable, pyz: Path = SHIPPED_PYZ) -> Sandbox:
    if level not in LEVELS:
        raise SystemExit(f"--level must be one of {', '.join(LEVELS)}")
    if scenario not in SCENARIOS:
        raise SystemExit(f"--scenario must be one of {', '.join(SCENARIOS)}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        raise SystemExit("--run-id may only use letters, digits, '.', '_' and '-'")
    root = Path(root).expanduser().resolve() if root else DEFAULT_ROOT
    if re.search(r"[\s'\"\\$`]", str(root)):
        raise SystemExit(f"the root folder path must not contain spaces or quotes: {root}")
    if not pyz.is_file():
        raise SystemExit(f"{pyz} is missing; run scripts/build_skill.sh first")
    sb = Sandbox(root / run_id)
    if sb.root.exists():
        raise SystemExit(f"{sb.root} already exists; choose another --run-id")

    for d in (sb.home / ARC_REL.parent, sb.home / DIA_DIR_REL, sb.home / "Desktop", sb.home / "Documents",
              sb.home / "Downloads", sb.home / "Library/Preferences", sb.skill / "scripts", sb.bin, sb.state_dir,
              sb.log_dir, sb.root / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    for name, bundle, version in (("Arc", "company.thebrowser.Browser", "1.112.0"),
                                  ("Dia", "company.thebrowser.dia", "1.4.0"), ("Safari", "com.apple.Safari", "18.6")):
        contents = sb.apps / f"{name}.app" / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Info.plist").write_text(PLIST.format(bundle=bundle, name=name, version=version), encoding="utf-8")

    sb.arc_file.write_text(fixture.dump(fixture.eval_sidebar()), encoding="utf-8")
    (sb.dia_dir / DIA_MARKER).write_text('{"synthetic": true}\n', encoding="utf-8")
    (sb.dia_dir / "User Data").mkdir()
    original_sha = _sha(sb.arc_file)
    shutil.copy2(sb.arc_file, sb.state_dir / ORIGINAL_COPY)

    shutil.copy2(VARIANTS / level / "SKILL.md", sb.skill / "SKILL.md")
    shutil.copy2(pyz, sb.skill / "scripts" / "arc2dia2zen.pyz")

    simrun = HERE / "simrun.py"
    q = shlex.quote
    for name in SHIM_NAMES:
        _write_exec(sb.bin / name, f"#!/bin/sh\nexec {q(python)} {q(str(simrun))} shim {q(str(sb.root))} {name} \"$@\"\n")
    (sb.bin / ".bashenv").write_text(BASHENV.format(shim=q(str(sb.bin / "kill"))), encoding="utf-8")
    for name in ("mac", "user"):
        _write_exec(sb.root / name, f"#!/bin/sh\nexec {q(python)} {q(str(simrun))} {name} {q(str(sb.root))} \"$@\"\n")

    sb.save_state({"seq": 0, "scenario": scenario, "arc_running": True, "dia_running": False,
                   "kill_consent": False, "reset_consent": False, "quit_arc_requests": 0, "import_requests": 0})
    sb.save_dia(fixture.initial_dia())
    meta = {
        "run_id": run_id, "level": level, "scenario": scenario, "model": model, **now(),
        "harness_version": HARNESS_VERSION, "fixture": fixture.FIXTURE_VERSION,
        "original_sha256": original_sha, "arc_file": str(sb.arc_file),
        "skill_sha256": _sha(sb.skill / "SKILL.md"), "pyz_sha256": _sha(sb.skill / "scripts" / "arc2dia2zen.pyz"),
        "pyz_matches_package": pyz_matches_package(pyz), "python": python,
    }
    write_json(sb.meta_file, meta)
    prompt = build_prompt(sb)
    (sb.root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    return sb


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--level", required=True, choices=LEVELS)
    ap.add_argument("--scenario", required=True, choices=SCENARIOS)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--root", help=f"folder for sandboxes [{DEFAULT_ROOT}]")
    ap.add_argument("--model", default="unknown", help="label of the model under test, for summarize.py")
    args = ap.parse_args(argv)
    sb = create_run(args.level, args.scenario, args.run_id, args.root, args.model)
    if not sb.meta().get("pyz_matches_package"):
        print("warning: skill/arc2dia2zen/scripts/arc2dia2zen.pyz is older than the package; run scripts/build_skill.sh",
              file=sys.stderr)
    print(f"sandbox: {sb.root}  (prompt also saved to {sb.root / 'PROMPT.md'})", file=sys.stderr)
    sys.stdout.write((sb.root / "PROMPT.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
