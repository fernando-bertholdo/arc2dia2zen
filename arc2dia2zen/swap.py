"""``swap``, ``restore`` and ``status``: put the cleaned sidebar in place for Dia's importer and
put the original back afterwards. Nothing is ever deleted: files are moved into a
timestamped backup folder, and the original is copied back from there (the backup stays)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from . import PROG
from .errors import Arc2DiaError, SafetyError
from .fsutil import (
    DIA_DEFAULTS_DOMAIN,
    copy_file_atomic,
    expand,
    iso_now,
    read_json,
    sha256_file,
    timestamp,
    unique_dir,
    unique_file,
    write_json,
)
from .prepare import load_manifest

STATE_FILE = "state.json"
SWAP_RECORD = "swap.json"
ORIGINAL_NAME = "StorableSidebar.original.json"
INSTALLED_NAME = "StorableSidebar.installed-cleaned.json"
ARC_PROCESS = "Arc"


class ProcessChecker:
    """Interface: tells whether a process with an exact name is running."""

    def is_running(self, name: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class PgrepChecker(ProcessChecker):
    def is_running(self, name: str) -> bool:
        try:
            proc = subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise SafetyError("pgrep is not available, so I cannot check that Arc is closed") from None
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        raise SafetyError(f"pgrep failed (exit {proc.returncode}), so I cannot check that Arc is closed")


def require_arc_closed(checker: ProcessChecker) -> None:
    if checker.is_running(ARC_PROCESS):
        raise SafetyError(
            "Arc is running. Quit Arc completely (Arc > Quit Arc, or Cmd+Q; closing its window does not quit "
            "it) and run this again. Arc must stay closed until restore, because Arc Sync can upload the "
            "modified sidebar."
        )


def read_state(backup_root: Path) -> dict:
    path = backup_root / STATE_FILE
    if not path.exists():
        return {"active": None, "history": []}
    try:
        state = read_json(path)
    except (OSError, ValueError) as exc:
        raise SafetyError(f"cannot read {path}: {exc}") from None
    state.setdefault("active", None)
    state.setdefault("history", [])
    return state


def write_state(backup_root: Path, state: dict) -> None:
    write_json(backup_root / STATE_FILE, state)


def _short(sha: Optional[str]) -> str:
    return (sha or "?")[:16] + "..."


# --------------------------------------------------------------------------- swap

def swap(sidebar_path, prepared_dir, backup_root, apply: bool, checker: ProcessChecker,
         allow_other_source: bool = False) -> List[str]:
    sidebar = expand(sidebar_path)
    backup_root = expand(backup_root)
    prepared = expand(prepared_dir)
    manifest = load_manifest(prepared)
    if not manifest.get("validation_ok"):
        raise SafetyError(f"the prepared files in {prepared} failed validation; see report.txt and fix the plan")
    cleaned = prepared / manifest["cleaned_file"]
    if not cleaned.exists():
        raise SafetyError(f"{cleaned} is missing; run prepare again")
    if sha256_file(cleaned) != manifest["cleaned_sha256"]:
        raise SafetyError(f"{cleaned} changed after prepare; run prepare again")
    state = read_state(backup_root)
    if state.get("active"):
        raise SafetyError(
            f"a swap is already active (backup {backup_root / state['active']}). "
            f"Run `{PROG} restore --apply` first."
        )
    if not sidebar.exists():
        raise SafetyError(f"{sidebar} does not exist")
    current = sha256_file(sidebar)
    if current == manifest["cleaned_sha256"]:
        raise SafetyError(
            f"{sidebar} already is the cleaned file, but no active swap is recorded in {backup_root}. "
            f"Check `{PROG} status` before going on."
        )
    other_source = current != manifest["source_sha256"]
    if other_source and not allow_other_source:
        raise SafetyError(
            f"{sidebar} changed since prepare (Arc was probably opened). Run plan/prepare again on the current file."
        )
    require_arc_closed(checker)

    backup_dir = unique_dir(backup_root, timestamp())
    original_backup = backup_dir / ORIGINAL_NAME
    lines = []
    if other_source:
        lines.append(
            "TEST MODE (--allow-other-source): the cleaned file was prepared from a different sidebar, "
            "so Dia will import that other data. Your current Arc file is still backed up and restored."
        )
    lines += [
        f"Swap plan for {sidebar}:",
        f"  1. move the original to {original_backup} (sha256 {_short(current)}) and check its hash",
        f"  2. copy {cleaned} into its place and check its hash ({_short(manifest['cleaned_sha256'])})",
        f"  3. record the swap in {backup_root / STATE_FILE}",
    ]
    if manifest.get("sync_keys"):
        lines.append("Arc Sync looks enabled in this file. Keep Arc quit until restore is done.")
        if not apply:
            lines.append("  Safest, if you can: turn off Arc's sync before the swap. Arc may rewrite this file when "
                         "you do, so run plan and prepare again afterwards.")
        else:
            lines.append("  Turning off Arc's sync before the swap is the safest choice; do not open Arc now to do "
                         "it, only before a later run.")
    if not apply:
        lines.extend(ARC_QUIT_NOTES)
        lines.append("Dry run: nothing was changed. Re-run with --apply to do it.")
        return lines

    backup_dir.mkdir(parents=True)
    shutil.copy2(prepared / "manifest.json", backup_dir / "prepared-manifest.json")
    shutil.copy2(cleaned, backup_dir / "StorableSidebar.cleaned.json")
    record = {
        "status": "in-progress",
        "created_at": iso_now(),
        "sidebar_path": str(sidebar),
        "original_file": ORIGINAL_NAME,
        "original_sha256": current,
        "cleaned_sha256": manifest["cleaned_sha256"],
        "prepared_dir": str(prepared),
        "other_source": other_source,
    }
    write_json(backup_dir / SWAP_RECORD, record)

    require_arc_closed(checker)
    shutil.move(str(sidebar), str(original_backup))
    if sha256_file(original_backup) != current:
        shutil.move(str(original_backup), str(sidebar))
        raise SafetyError("the backup copy does not match the original; nothing was swapped")
    try:
        copy_file_atomic(cleaned, sidebar)
        shutil.copymode(original_backup, sidebar)
        if sha256_file(sidebar) != manifest["cleaned_sha256"]:
            raise SafetyError("the installed file does not match the cleaned file")
    except Exception:
        if sidebar.exists():
            shutil.move(str(sidebar), str(unique_file(backup_dir / "StorableSidebar.failed-install.json")))
        copy_file_atomic(original_backup, sidebar)
        record["status"] = "rolled-back"
        write_json(backup_dir / SWAP_RECORD, record)
        raise
    record["status"] = "swapped"
    record["swapped_at"] = iso_now()
    write_json(backup_dir / SWAP_RECORD, record)
    state["active"] = backup_dir.name
    state["history"].append({"backup": backup_dir.name, "swapped_at": record["swapped_at"]})
    write_state(backup_root, state)

    lines.append("")
    lines.append(f"Done. The original is safe in {original_backup}.")
    lines.append("")
    lines.extend(dia_import_steps(prepared))
    return lines


ARC_QUIT_NOTES = [
    "Arc must stay quit until restore: closing Arc's window does not quit Arc (use Arc > Quit Arc,",
    "or Cmd+Q), and other apps or automation can launch Arc in the background without a window.",
]


def dia_import_steps(prepared: Optional[Path] = None) -> List[str]:
    ts = timestamp()
    reset_dir = f'"$HOME/dia-reset-backup-{ts}"'
    support = '"$HOME/Library/Application Support/Dia"'
    verify = f"{PROG} verify" + (f" --prepared {prepared}" if prepared else "")
    return [
        "Import into Dia now. Keep Arc quit until step 3 is done.",
        *ARC_QUIT_NOTES,
        f"Run `{PROG} status` right before and right after the import: it must show \"Arc: not running\"",
        "and no WARNING line. If Arc is running, quit it with Arc > Quit Arc; if the file changed,",
        "restore first and start again from plan.",
        "",
        "Optional: start from a fresh Dia. Each import creates new profiles and never merges into",
        "existing ones, so a reset avoids a second set of profiles. Quit Dia first, then run:",
        "",
        f"  mkdir -p {reset_dir}",
        f"  defaults export {DIA_DEFAULTS_DOMAIN} {reset_dir}/{DIA_DEFAULTS_DOMAIN}.plist",
        f"  mv {support} {reset_dir}/Dia",
        f"  defaults delete {DIA_DEFAULTS_DOMAIN}",
        "",
        "  To undo the reset later: quit Dia, then",
        f"  mv {support} {support}-after-import   # keep the new one aside",
        f"  mv {reset_dir}/Dia {support}",
        f"  defaults import {DIA_DEFAULTS_DOMAIN} {reset_dir}/{DIA_DEFAULTS_DOMAIN}.plist",
        "",
        "1. Open Dia and choose Dia > Import from Another Browser... > Arc. A fresh Dia offers the",
        "   same Arc import during onboarding.",
        "2. Wait until the import finishes and the new profiles appear. Imported pinned tabs only",
        "   load when clicked; do not open them all at once.",
        f"3. Put the original sidebar back: {PROG} restore --apply",
        f"4. Check the result: {verify}",
        "5. Only now open Arc again.",
    ]


# --------------------------------------------------------------------------- restore

def restore(sidebar_path, backup_root, apply: bool, checker: ProcessChecker, backup_name: Optional[str] = None) -> List[str]:
    backup_root = expand(backup_root)
    state = read_state(backup_root)
    name = backup_name or state.get("active")
    if not name:
        raise SafetyError(
            f"no active swap is recorded in {backup_root}. Use `{PROG} status` to list backups and "
            "--backup NAME to restore a specific one."
        )
    bdir = backup_root / name
    if not (bdir / SWAP_RECORD).exists():
        raise SafetyError(f"{bdir} is not a {PROG} backup (no {SWAP_RECORD})")
    record = read_json(bdir / SWAP_RECORD)
    original = bdir / record.get("original_file", ORIGINAL_NAME)
    if not original.exists():
        raise SafetyError(f"the backup {original} is missing; nothing was changed")
    if sha256_file(original) != record["original_sha256"]:
        raise SafetyError(f"the backup {original} does not match its recorded hash; nothing was changed")
    sidebar = expand(sidebar_path) if sidebar_path else Path(record["sidebar_path"])
    require_arc_closed(checker)

    lines = [f"Restore plan for {sidebar} from {bdir}:"]
    current = sha256_file(sidebar) if sidebar.exists() else None
    move_to: Optional[Path] = None
    if current == record["original_sha256"]:
        lines.append("  the original is already in place; only the swap record will be closed")
    else:
        if current is None:
            lines.append("  the sidebar file is missing; the original will be copied back")
        elif current == record.get("cleaned_sha256"):
            move_to = unique_file(bdir / INSTALLED_NAME)
            lines.append(f"  1. move the installed cleaned file to {move_to}")
        else:
            move_to = unique_file(bdir / f"StorableSidebar.unexpected-{timestamp()}.json")
            lines.append(f"  1. move the current file (unexpected content, sha256 {_short(current)}) to {move_to}")
            lines.append("     Warning: the file changed while swapped. Arc may have been opened, or another tool wrote it.")
        lines.append(f"  2. copy {original} back to {sidebar} and check sha256 {_short(record['original_sha256'])}")
    if not apply:
        lines.append("Dry run: nothing was changed. Re-run with --apply to do it.")
        return lines

    require_arc_closed(checker)
    if current != record["original_sha256"]:
        if move_to is not None:
            shutil.move(str(sidebar), str(move_to))
        copy_file_atomic(original, sidebar)
        if sha256_file(sidebar) != record["original_sha256"]:
            raise SafetyError(
                f"the restored file does not match the original hash. The original is still at {original}."
            )
    record["status"] = "restored"
    record["restored_at"] = iso_now()
    write_json(bdir / SWAP_RECORD, record)
    if state.get("active") == name:
        state["active"] = None
    state["history"].append({"backup": name, "restored_at": record["restored_at"]})
    write_state(backup_root, state)
    lines.append(f"Restored. {sidebar} matches the original byte for byte (sha256 {_short(record['original_sha256'])}).")
    lines.append(f"The backup stays in {bdir}. You can open Arc again.")
    return lines


# --------------------------------------------------------------------------- status

def status(sidebar_path, backup_root, checker: Optional[ProcessChecker] = None, prepared_dir=None) -> List[str]:
    sidebar = expand(sidebar_path)
    backup_root = expand(backup_root)
    lines = [f"Sidebar file: {sidebar}"]
    current = sha256_file(sidebar) if sidebar.exists() else None
    lines.append(f"  sha256 {current}" if current else "  (missing)")
    arc_running: Optional[bool] = None
    if checker is not None:
        try:
            arc_running = checker.is_running(ARC_PROCESS)
            lines.append("Arc: running" if arc_running else "Arc: not running")
        except Arc2DiaError as exc:
            lines.append(f"Arc: unknown ({exc})")
    if prepared_dir is not None:
        try:
            manifest = load_manifest(prepared_dir)
            if current == manifest.get("source_sha256"):
                lines.append("  matches the source of the prepared migration (original Arc data)")
            elif current == manifest.get("cleaned_sha256"):
                lines.append("  is the CLEANED file: run restore before opening Arc")
            else:
                lines.append("  matches neither the prepared source nor the cleaned file")
        except Arc2DiaError:
            pass
    state = read_state(backup_root)
    lines.append(f"Backups: {backup_root}")
    if state.get("active"):
        lines.append(f"  ACTIVE SWAP: {state['active']}. Run `{PROG} restore --apply` before opening Arc.")
        lines.extend(_active_swap_checks(backup_root / state["active"], current, arc_running))
    else:
        lines.append("  no active swap")
    if backup_root.exists():
        for d in sorted(p for p in backup_root.iterdir() if (p / SWAP_RECORD).exists()):
            try:
                rec = read_json(d / SWAP_RECORD)
            except (OSError, ValueError):
                continue
            mark = ""
            if current and current == rec.get("original_sha256"):
                mark = " (current file is this original)"
            elif current and current == rec.get("cleaned_sha256"):
                mark = " (current file is this cleaned copy)"
            lines.append(f"  {d.name}: {rec.get('status')}{mark}")
    return lines


def _active_swap_checks(bdir: Path, current: Optional[str], arc_running: Optional[bool]) -> List[str]:
    """While a swap is active, the sidebar file must still be the installed cleaned copy and Arc
    must not run. Arc launched in the background (by another app or automation) rewrites the file."""
    try:
        record = read_json(bdir / SWAP_RECORD)
    except (OSError, ValueError):
        return [f"WARNING: cannot read {bdir / SWAP_RECORD}; check the backup folder before going on."]
    lines = []
    if current is not None and current == record.get("cleaned_sha256"):
        lines.append("  the sidebar file is still the installed cleaned copy")
    elif current is None:
        lines.append("WARNING: the sidebar file is missing while a swap is active. "
                     f"Quit Arc, then run `{PROG} restore --apply`.")
    elif current == record.get("original_sha256"):
        lines.append("WARNING: the sidebar file is the original again, not the cleaned copy, but the swap is "
                     f"still recorded. Do not run Dia's import now; run `{PROG} restore --apply` to close it.")
    else:
        lines.append("WARNING: the sidebar file no longer matches the installed cleaned copy. Arc or another "
                     f"program wrote the file; quit Arc, then restore (`{PROG} restore --apply`).")
    if arc_running:
        lines.append("WARNING: Arc is running while a swap is active. Quit it with Arc > Quit Arc or Cmd+Q "
                     f"(closing its window does not quit it), then run `{PROG} status` again.")
    return lines
