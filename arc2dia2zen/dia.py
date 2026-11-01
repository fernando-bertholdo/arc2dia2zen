"""Read-only access to Dia through AppleScript (osascript).

The script below only reads profile names and tab titles/URLs. It never uses ``make``,
``close``, ``focus``, ``move`` or ``execute``, so it cannot open, load or change tabs.
Everything that touches macOS sits behind :class:`DiaSource`, so tests run anywhere.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .errors import Arc2DiaError

US = "\x1f"  # unit separator between fields
RS = "\x1e"  # record separator between rows

# Inside `tell application "Dia"` the word `tab` is Dia's tab class, so the separators are
# built with `character id`. Bulk property reads (`URL of every tab of p`) are one Apple
# Event per profile; the per-tab loop is the fallback.
READ_SCRIPT = r'''
set US to character id 31
set RS to character id 30
set rows to {}
if application "Dia" is not running then return "ERR" & US & "not-running"
tell application "Dia"
    if (count of windows) is 0 then return "ERR" & US & "no-window"
    set ps to profiles of window 1
    repeat with k from 1 to count of ps
        set p to item k of ps
        set pname to ""
        try
            set pname to name of p
        end try
        if pname is missing value then set pname to ""
        set end of rows to "P" & US & (k as text) & US & pname
        set urls to {}
        set titles to {}
        set pins to {}
        set bulk to true
        try
            set urls to URL of every tab of p
            set titles to title of every tab of p
            set pins to isPinned of every tab of p
            if (count of urls) is not (count of titles) or (count of urls) is not (count of pins) then set bulk to false
        on error
            set bulk to false
        end try
        if not bulk then
            set urls to {}
            set titles to {}
            set pins to {}
            set tlist to {}
            try
                set tlist to tabs of p
            end try
            repeat with t in tlist
                set u to ""
                set ti to ""
                set pin to false
                try
                    set u to URL of t
                end try
                try
                    set ti to title of t
                end try
                try
                    set pin to isPinned of t
                end try
                set end of urls to u
                set end of titles to ti
                set end of pins to pin
            end repeat
        end if
        repeat with i from 1 to count of urls
            set u to item i of urls
            if u is missing value then set u to ""
            set ti to item i of titles
            if ti is missing value then set ti to ""
            set pin to item i of pins
            if pin is missing value then set pin to false
            set end of rows to "T" & US & (k as text) & US & (pin as text) & US & u & US & ti
        end repeat
    end repeat
end tell
set AppleScript's text item delimiters to RS
set outText to rows as text
set AppleScript's text item delimiters to ""
return outText
'''


@dataclass
class DiaTab:
    url: str
    title: str
    is_pinned: bool  # true only for Dia Favorites


@dataclass
class DiaProfile:
    index: int
    name: str
    tabs: List[DiaTab] = field(default_factory=list)


class DiaSource:
    """Interface: returns Dia's profiles and their tabs."""

    def read(self) -> List[DiaProfile]:  # pragma: no cover - interface
        raise NotImplementedError


def parse_dump(text: str) -> List[DiaProfile]:
    text = text.rstrip("\r\n")
    if text.startswith("ERR" + US):
        reason = text.split(US, 1)[1]
        if reason == "not-running":
            raise Arc2DiaError("Dia is not running. Open Dia (with the imported profiles) and run verify again.")
        if reason == "no-window":
            raise Arc2DiaError("Dia has no open window. Open a Dia window and run verify again.")
        raise Arc2DiaError(f"Dia script error: {reason}")
    profiles: List[DiaProfile] = []
    by_index = {}
    for row in text.split(RS) if text else []:
        fields = row.split(US)
        if fields[0] == "P" and len(fields) >= 3:
            idx = int(fields[1]) if fields[1].lstrip("-").isdigit() else len(profiles) + 1
            prof = DiaProfile(idx, fields[2])
            profiles.append(prof)
            by_index[fields[1]] = prof
        elif fields[0] == "T" and len(fields) >= 5:
            prof = by_index.get(fields[1])
            if prof is None:
                continue
            prof.tabs.append(DiaTab(url=fields[3], title=US.join(fields[4:]), is_pinned=fields[2].lower() == "true"))
    return profiles


class Runner:
    """Interface for running an AppleScript and returning its stdout."""

    def run(self, script: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class OsascriptRunner(Runner):
    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout

    def run(self, script: str) -> str:
        if sys.platform != "darwin":
            raise Arc2DiaError("reading Dia needs macOS (osascript). On other systems pass --dia-dump FILE.")
        try:
            proc = subprocess.run(
                ["osascript", "-"], input=script, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError:
            raise Arc2DiaError("osascript was not found") from None
        except subprocess.TimeoutExpired:
            raise Arc2DiaError("Dia did not answer within the time limit") from None
        if proc.returncode != 0:
            msg = proc.stderr.strip()
            if "-1743" in msg or "Not authorized" in msg:
                msg += (" (allow your terminal to control Dia in System Settings > Privacy & Security > Automation)")
            raise Arc2DiaError(f"osascript failed: {msg}")
        return proc.stdout


class OsascriptDiaSource(DiaSource):
    def __init__(self, runner: Runner = None):
        self.runner = runner or OsascriptRunner()

    def read(self) -> List[DiaProfile]:
        return parse_dump(self.runner.run(READ_SCRIPT))


class DumpDiaSource(DiaSource):
    """Reads the raw output of READ_SCRIPT saved to a file (``osascript - < script > dump``)."""

    def __init__(self, path):
        self.path = Path(path).expanduser()

    def read(self) -> List[DiaProfile]:
        try:
            return parse_dump(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise Arc2DiaError(f"cannot read {self.path}: {exc}") from None
