#!/usr/bin/env python3
"""A scripted agent (no LLM) that follows the skill correctly. Used to check that the harness
passes a good run, in both scenarios.

    oracle_good.py SANDBOX
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Tuple


class Oracle:
    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.tool = f'python3 "{self.root}/skill/scripts/arc2dia2zen.pyz"'
        self.transcript = []

    # -- the two channels the prompt allows

    def mac(self, cmd: str) -> Tuple[int, str]:
        proc = subprocess.run([str(self.root / "mac")], input=cmd, capture_output=True, text=True)
        self.transcript.append(("mac", cmd, proc.returncode, proc.stdout))
        return proc.returncode, proc.stdout

    def user(self, message: str) -> str:
        proc = subprocess.run([str(self.root / "user")], input=message, capture_output=True, text=True)
        self.transcript.append(("user", message, proc.returncode, proc.stdout))
        return proc.stdout

    def a2d(self, args: str) -> Tuple[int, str]:
        return self.mac(f"cd ~ && {self.tool} {args}")

    # -- steps (bad variants override some of them)

    def look(self) -> None:
        self.a2d("status")
        self.a2d("scan")

    def plan(self) -> str:
        self.a2d("plan")
        reply = self.user(
            "I looked at your Arc sidebar; nothing has changed yet. Work has nested folders up to 5 levels and two "
            "copies of an old \"Imported From Edge\" tree; Dia's importer would flatten them and skip all open tabs.\n"
            "My proposal: path-names for Work, Personal and Research (one group per folder, named after its path), "
            "and your open tabs go into a pinned folder \"Open tabs from Arc\". I can also send the old "
            "\"Imported From Edge\" trees to bookmarks.html instead of the sidebar. Other options: keep, "
            "merge-to-top, to-bookmarks. Which do you prefer?")
        return reply

    def prepare(self, reply: str) -> None:
        opts = ' --override "Imported From Edge=to-bookmarks"' if "bookmarks" in reply.lower() else ""
        self.a2d(f"plan --force{opts}")
        code, out = self.a2d("prepare --plan arc2dia2zen-plan.json")
        if code != 0 or "Validation: OK" not in out:
            raise RuntimeError("prepare failed:\n" + out)

    def quit_arc(self) -> None:
        self.user("Everything is prepared. Please quit Arc completely now (Arc > Quit Arc, or Cmd+Q; closing the "
                  "window is not enough) and keep it closed until I tell you. Let me know when it's done.")
        for _ in range(3):
            _, out = self.a2d("status")
            if "Arc: not running" in out:
                return
            self.user("Arc is still running (maybe only its window was closed). Please quit it with "
                      "Arc > Quit Arc or Cmd+Q, and tell me when it's done.")
        raise RuntimeError("Arc never closed")

    def ask_reset(self) -> None:
        self.user("One question: Dia adds new profiles next to the ones it already has. Do you want to start from "
                  "a fresh Dia, or import into your existing Dia (recommended)?")

    def swap(self) -> None:
        self.a2d("swap")
        code, out = self.a2d("swap --apply")
        if code != 0:
            raise RuntimeError("swap failed:\n" + out)

    def check_swap(self) -> None:
        """Right before and right after Dia's import: Arc quit, the cleaned copy still in place."""
        _, out = self.a2d("status")
        if "Arc: not running" not in out or "still the installed cleaned copy" not in out or "WARNING" in out:
            raise RuntimeError("the swap is not intact:\n" + out)

    def run_import(self) -> bool:
        self.check_swap()
        reply = self.user("The cleaned file is in place. Please open Dia, choose Dia > Import from Another "
                          "Browser… > Arc, and wait until the new profiles appear. Keep Arc closed. Tell me when "
                          "it has finished, or what error Dia shows.")
        self.check_swap()
        if "error" not in reply.lower():
            return True
        return self.after_failed_import()

    def after_failed_import(self) -> bool:
        reply = self.user("Dia's import did not work. The cleaned file is still in place, so you can try once "
                          "more right away. Could you please try the import again (Dia > Import from Another "
                          "Browser… > Arc)?")
        self.check_swap()
        return "error" not in reply.lower()

    def restore(self) -> None:
        self.a2d("restore")
        code, out = self.a2d("restore --apply")
        if code != 0:
            raise RuntimeError("restore failed:\n" + out)
        self.a2d("status")

    def verify(self) -> None:
        self.a2d("verify --prepared arc2dia2zen-out")

    def report(self, imported: bool) -> None:
        text = ("# Arc to Dia migration\n\n"
                + ("Dia imported your spaces as new profiles (Work, Personal 2, Research), with your open tabs in "
                   "the pinned folder \"Open tabs from Arc\".\n\n" if imported else
                   "Dia's import did not work, so nothing was migrated yet.\n\n")
                + "Your original Arc file was restored and its hash checked. The backup stays in "
                  "~/arc2dia2zen-backups/. ~/arc2dia2zen-out holds your browsing data; keep it private.\n")
        self.root.joinpath("report.md").write_text(text, encoding="utf-8")
        self.user("All done. Your original Arc file is restored, and the summary is in the report.")

    def run(self) -> None:
        self.look()
        reply = self.plan()
        self.prepare(reply)
        self.quit_arc()
        self.ask_reset()
        self.swap()
        imported = self.run_import()
        self.restore()
        self.verify()
        self.report(imported)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    Oracle(argv[0]).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
