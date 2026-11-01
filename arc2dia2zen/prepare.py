"""``prepare``: apply a plan to a copy of the sidebar and write everything into one output folder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import PROG, __version__
from .bookmarks import render
from .errors import Arc2DiaError
from .fsutil import expand, iso_now, sha256_bytes, sha256_file, write_bytes_atomic, write_json
from .model import Sidebar
from .plan import Plan
from .transform import FATE_BOOKMARKS, FATE_DEDUPED, FATE_DROPPED, FATE_SIDEBAR, PrepareResult, transform
from .validate import Validation, validate

CLEANED_NAME = "StorableSidebar.cleaned.json"
BOOKMARKS_NAME = "bookmarks.html"
MANIFEST_NAME = "manifest.json"
REPORT_JSON = "report.json"
REPORT_TXT = "report.txt"
PLAN_NAME = "plan.json"
OUTPUT_FILES = (CLEANED_NAME, BOOKMARKS_NAME, MANIFEST_NAME, REPORT_JSON, REPORT_TXT, PLAN_NAME)


@dataclass
class PrepareOutcome:
    out_dir: Path
    result: PrepareResult
    validation: Validation
    manifest: dict
    text: str


def prepare(sidebar_path, plan: Plan, out_dir, force: bool = False, **transform_kwargs) -> PrepareOutcome:
    sidebar_path = expand(sidebar_path)
    out_dir = expand(out_dir)
    try:
        source_bytes = sidebar_path.read_bytes()
    except OSError as exc:
        raise Arc2DiaError(f"cannot read {sidebar_path}: {exc}") from None
    if out_dir.resolve() == sidebar_path.parent.resolve():
        raise Arc2DiaError("the output folder must not be Arc's own folder; choose another --out-dir")
    if out_dir.exists():
        existing = [n for n in OUTPUT_FILES if (out_dir / n).exists()]
        if existing and not force:
            raise Arc2DiaError(
                f"{out_dir} already holds a prepared migration ({', '.join(existing)}); "
                "pass --force to overwrite it or choose another --out-dir"
            )
    source_sha = sha256_bytes(source_bytes)
    source = Sidebar.from_bytes(source_bytes)

    result = transform(source, plan, **transform_kwargs)
    warnings = list(result.warnings)
    if plan.source_sha256 and plan.source_sha256 != source_sha:
        warnings.append("the sidebar file changed since the plan was made; folder ids in the plan were re-checked")

    cleaned = result.sidebar.dumps()
    html_text = render(result.bookmarks) if result.bookmarks else ""

    out_dir.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(out_dir / CLEANED_NAME, cleaned)
    if html_text:
        write_bytes_atomic(out_dir / BOOKMARKS_NAME, html_text.encode("utf-8"))
    elif (out_dir / BOOKMARKS_NAME).exists():
        # A previous run left a bookmarks file; replace it with an empty, valid one so the folder is consistent.
        write_bytes_atomic(out_dir / BOOKMARKS_NAME, render([]).encode("utf-8"))
    write_json(out_dir / PLAN_NAME, plan.to_json())

    # Validate what is on disk, not what is in memory.
    disk_cleaned = (out_dir / CLEANED_NAME).read_bytes()
    disk_html = (out_dir / BOOKMARKS_NAME).read_text(encoding="utf-8") if html_text else ""
    excluded = {sp.space_id for sp in plan.spaces if not sp.import_space}
    validation = validate(source, disk_cleaned, disk_html, result.ledger, excluded)
    validation.warnings[:0] = warnings

    manifest = {
        "tool": PROG,
        "tool_version": __version__,
        "created_at": iso_now(),
        "source_path": str(sidebar_path),
        "source_sha256": source_sha,
        "source_size": len(source_bytes),
        "cleaned_file": CLEANED_NAME,
        "cleaned_sha256": sha256_file(out_dir / CLEANED_NAME),
        "bookmarks_file": BOOKMARKS_NAME if html_text else None,
        "bookmarks_sha256": sha256_file(out_dir / BOOKMARKS_NAME) if html_text else None,
        "sync_keys": source.sync_keys,
        "validation_ok": validation.ok,
    }
    report = {
        "manifest": manifest,
        "validation": validation.to_dict(),
        **result.to_dict(),
    }
    write_json(out_dir / REPORT_JSON, report)
    text = render_prepare(out_dir, result, validation, manifest)
    write_bytes_atomic(out_dir / REPORT_TXT, (text + "\n").encode("utf-8"))
    # The manifest goes last: swap refuses to run without it.
    write_json(out_dir / MANIFEST_NAME, manifest)
    return PrepareOutcome(out_dir, result, validation, manifest, text)


def load_manifest(out_dir) -> dict:
    path = expand(out_dir) / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Arc2DiaError(f"no {MANIFEST_NAME} in {expand(out_dir)}; run prepare first") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise Arc2DiaError(f"cannot read {path}: {exc}") from None


def _n(count: int, word: str) -> str:
    return f"{count} {word if count == 1 else word + 's'}"


def render_prepare(out_dir: Path, result: PrepareResult, validation: Validation, manifest: dict) -> str:
    lines: List[str] = []
    add = lines.append
    add(f"Prepared in {out_dir}")
    add(f"  {CLEANED_NAME}  sha256 {manifest['cleaned_sha256'][:16]}...")
    if manifest.get("bookmarks_file"):
        add(f"  {BOOKMARKS_NAME}  {validation.stats.get('bookmarks_in_html', 0)} bookmarks")
    add(f"  {PLAN_NAME}, {REPORT_JSON} (full ledger), {REPORT_TXT}, {MANIFEST_NAME}")
    for sr in result.spaces:
        add("")
        add(f'Space "{sr.title}": ' + ("excluded" if not sr.plan["import"] else sr.plan["folder_strategy"]))
        for a in sr.actions:
            add(f"  - {a}")
        if not sr.actions:
            add("  - no changes")
        b, a = sr.before, sr.after
        if b and a:
            groups = (str(b.pinned_groups) if b.pinned_groups_min == b.pinned_groups
                      else f"{b.pinned_groups_min} to {b.pinned_groups}")
            add(f"  Dia, before: {groups} pinned groups ({b.nested_groups} nested in Arc), "
                f"{_n(b.open_tabs_lost, 'open tab')} not imported"
                + (f", {_n(b.wrapped_tabs_lost, 'tab')} in pinned split views and tab groups dropped"
                   if b.wrapped_tabs_lost else ""))
            add(f"  Dia, after:  {_n(a.pinned_groups, 'pinned group')} ({a.nested_groups} nested in Arc), "
                f"{_n(a.loose_pinned_tabs, 'loose pinned tab')}, {_n(a.tabs_in_groups, 'tab')} in groups, "
                f"{_n(a.open_tabs_lost, 'open tab')} not imported"
                + (f", {_n(a.wrapped_tabs_lost, 'tab')} in pinned split views and tab groups dropped"
                   if a.wrapped_tabs_lost else "")
                + (f", {_n(a.non_web_dropped, 'non-web URL')} dropped by Dia" if a.non_web_dropped else ""))
    counts = result.fate_counts()
    total = sum(counts.values())
    add("")
    add(f"URL accounting: {total} tabs: {counts[FATE_SIDEBAR]} in the sidebar, {counts[FATE_BOOKMARKS]} in "
        f"{BOOKMARKS_NAME}, {counts[FATE_DEDUPED]} removed as duplicates (a copy is kept), "
        f"{counts[FATE_DROPPED]} dropped with a reason")
    no_url = [e for e in result.ledger if not e.url and e.fate == FATE_SIDEBAR]
    if no_url:
        add(f"  {_n(len(no_url), 'tab')} without a URL stay in the sidebar (reason: no URL; Dia may skip them)")
    dropped = [e for e in result.ledger if e.fate == FATE_DROPPED]
    for e in dropped[:10]:
        add(f"  dropped: {e.url or e.title} ({e.reason})")
    if len(dropped) > 10:
        add(f"  ... {len(dropped) - 10} more in {REPORT_JSON}")
    add("Validation: " + ("OK (shape preserved, no dangling ids, every URL accounted for)" if validation.ok else "FAILED"))
    for err in validation.errors[:20]:
        add(f"  error: {err}")
    for w in validation.warnings:
        add(f"  warning: {w}")
    if validation.ok:
        add("")
        add(f"Next: quit Arc, then run `{PROG} swap --prepared {out_dir}` to see the swap steps "
            "(add --apply to do it).")
    return "\n".join(lines)

