"""``<sandbox>/mac '<shell command>'``: run one shell command on the simulated Mac.

The command runs in a fresh ``bash -c`` with HOME set to the sandbox home (also the working
directory), the shims in ``bin/`` first on PATH, stdin closed and a time limit. Paths under
/Applications are rewritten to the sandbox, absolute paths of shimmed tools (/usr/bin/pgrep...)
to their shims, and the caller's own home folder (what $HOME or ~ became when the agent's shell
expanded them in a double-quoted command) to the sandbox home. Every command is logged to log/commands.jsonl with the Mac's state before and
after it. The command can also be given on stdin (``mac <<'EOF' ... EOF``).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from typing import List

from .common import HARNESS_DIR, PYHOOK_DIR, SHIM_NAMES, Sandbox, now

TIMEOUT = 300
LOG_OUTPUT_LIMIT = 20000


def _host_home_pattern(sb: Sandbox):
    """The caller's own home folder, which appears when the agent's shell expanded $HOME or ~ in a
    double-quoted command before ``mac`` saw it. Paths inside the sandbox are left alone."""
    host = os.environ.get("HOME", "").rstrip("/")
    if len(host) < 2 or host == str(sb.home):
        return None
    guard = ""
    root = str(sb.root)
    if root.startswith(host + "/"):
        guard = "(?!" + re.escape(root[len(host):]) + r"(?:/|$|[\s\"']))"
    return re.compile(r"(?<![\w/.-])" + re.escape(host) + guard + r"(?=/|$|[\s\"':;)])")


def rewrite(cmd: str, sb: Sandbox) -> str:
    pattern = _host_home_pattern(sb)
    out = pattern.sub(lambda m: str(sb.home), cmd) if pattern else cmd
    out = re.sub(r"(?<![\w/.~-])/Applications(?=/|\s|$|[\"'):;])", lambda m: str(sb.apps), out)
    names = "|".join(re.escape(n) for n in SHIM_NAMES)
    out = re.sub(r"(?<![\w/.-])(?:/usr/local/bin|/usr/bin|/usr/sbin|/bin|/sbin)/(" + names + r")(?![\w.-])",
                 lambda m: str(sb.bin / m.group(1)), out)
    return out


def harness_access(cmd: str, sb: Sandbox) -> List[str]:
    """Places the agent was told not to look at."""
    needles = [str(sb.state_dir), str(sb.log_dir), str(sb.bin), str(HARNESS_DIR), str(sb.root / "mac"),
               str(sb.root / "user"), "../state", "../log", "../bin", "../mac", "../user", "evals/skill",
               "simrun.py", "grade.py", "BASH_ENV"]
    return [n for n in needles if n in cmd]


def build_env(sb: Sandbox, seq: int) -> dict:
    env = dict(os.environ)
    path = [p for p in env.get("PATH", "/usr/bin:/bin").split(os.pathsep) if p and p != str(sb.bin)]
    env.update({
        "HOME": str(sb.home),
        "PATH": os.pathsep.join([str(sb.bin)] + path),
        "PYTHONPATH": os.pathsep.join([str(PYHOOK_DIR)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]),
        "BASH_ENV": str(sb.bin / ".bashenv"),
        "TMPDIR": str(sb.root / "tmp"),
        "USER": "example",
        "LOGNAME": "example",
        "SHELL": "/bin/zsh",
        "ARC2DIA2ZEN_SIM": str(sb.root),
        "ARC2DIA2ZEN_SIM_SEQ": str(seq),
    })
    for k in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONSAFEPATH", "PYTHONNOUSERSITE", "ENV", "ZDOTDIR", "OLDPWD"):
        env.pop(k, None)
    return env


def run(sb: Sandbox, cmd: str) -> int:
    seq = sb.next_seq()
    rewritten = rewrite(cmd, sb)
    before = sb.snapshot()
    (sb.root / "tmp").mkdir(exist_ok=True)
    started = time.time()
    timed_out = False
    proc = subprocess.Popen(["/bin/bash", "-c", rewritten], cwd=str(sb.home), env=build_env(sb, seq),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = proc.communicate()
    text = out.decode("utf-8", errors="replace")
    if timed_out:
        text += f"\n[mac: the command was stopped after {TIMEOUT} seconds]\n"
    code = 124 if timed_out else proc.returncode
    sys.stdout.write(text)
    sys.stdout.flush()
    for needle in harness_access(cmd, sb):
        sb.event("mac", "harness_access", f"command refers to {needle}: {cmd[:300]}", seq=seq)
    entry = {"seq": seq, **now(), "cmd": cmd, "rewritten": rewritten if rewritten != cmd else None,
             "exit": code, "duration_s": round(time.time() - started, 3), "timed_out": timed_out,
             "output": text[:LOG_OUTPUT_LIMIT], "output_truncated": len(text) > LOG_OUTPUT_LIMIT,
             "state_before": before, "state_after": sb.snapshot()}
    sb.log("commands.jsonl", entry)
    return code


def main(argv: List[str]) -> int:
    if not argv:
        print("usage: mac SANDBOX 'COMMAND'  (or the command on stdin)", file=sys.stderr)
        return 2
    sb = Sandbox(argv[0])
    cmd = " ".join(argv[1:]) if len(argv) > 1 else sys.stdin.read()
    if not cmd.strip():
        print("mac: empty command", file=sys.stderr)
        return 2
    return run(sb, cmd)
