"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import PROG, __version__
from .analysis import collect_folders, join_path, match_folder_path, render_scan, render_tree, scan, scan_space
from .dia import READ_SCRIPT, DiaSource, DumpDiaSource, OsascriptDiaSource
from .errors import Arc2DiaError
from .fsutil import (
    ARC_SIDEBAR,
    DEFAULT_BACKUP_ROOT,
    LEGACY_BACKUP_ROOT,
    DEFAULT_OUT_DIR,
    DEFAULT_PLAN_FILE,
    expand,
    sha256_bytes,
    write_json,
)
from .model import Sidebar
from .plan import (
    DEFAULT_OPEN_TABS_FOLDER,
    FOLDER_STRATEGIES,
    NON_WEB_MODES,
    OPEN_TABS_MODES,
    SPLIT_VIEW_MODES,
    STRATEGY_HELP,
    TAB_GROUP_MODES,
    ZEN_STRATEGY_HELP,
    TARGET_NAMES,
    TARGETS,
    ZEN_MAX_SUBFOLDERS,
    FolderOverride,
    Plan,
    SpacePlan,
    load_plan,
)
from .prepare import CLEANED_NAME, load_manifest, prepare
from .swap import PgrepChecker, ProcessChecker, restore, status, swap
from .transform import what_if
from .verify import render_verify, verify


@dataclass
class Deps:
    checker: ProcessChecker
    dia_source: Optional[DiaSource]


def _out(text: str = "") -> None:
    print(text)


def _load_sidebar(path) -> tuple:
    path = expand(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise Arc2DiaError(
            f"{path} not found. Pass --sidebar PATH (Arc keeps it in ~/Library/Application Support/Arc/)."
        ) from None
    except OSError as exc:
        raise Arc2DiaError(f"cannot read {path}: {exc}") from None
    return Sidebar.from_bytes(data), sha256_bytes(data), path


def _pairs(values: Optional[List[str]], what: str) -> List[tuple]:
    out = []
    for v in values or []:
        if "=" not in v:
            raise Arc2DiaError(f"{what} expects NAME=VALUE, got {v!r}")
        name, value = v.rsplit("=", 1)
        out.append((name.strip(), value.strip()))
    return out


# --------------------------------------------------------------------------- plan building

def build_plan(sb: Sidebar, args, source_path: str, sha: str) -> Plan:
    target = getattr(args, "target", None) or "dia"
    zen = target == "zen"
    depth_arg = getattr(args, "max_folder_depth", None)
    if depth_arg is not None and not zen:
        raise Arc2DiaError("--max-folder-depth only applies to --target zen")
    if depth_arg is not None and depth_arg < 0:
        raise Arc2DiaError("--max-folder-depth must be 0 (no limit) or more")
    max_depth = (ZEN_MAX_SUBFOLDERS if depth_arg is None else (depth_arg or None)) if zen else None
    space_strats = _pairs(getattr(args, "space", None), "--space")
    overrides = _pairs(getattr(args, "override", None), "--override")
    excluded = set(getattr(args, "exclude_space", None) or [])
    for _, strat in space_strats + overrides:
        if strat not in FOLDER_STRATEGIES:
            raise Arc2DiaError(f"unknown folder strategy {strat!r}; choose from {', '.join(FOLDER_STRATEGIES)}")
    titles = {s.title for s in sb.spaces} | {s.id for s in sb.spaces}
    for name, _ in space_strats:
        if name not in titles:
            raise Arc2DiaError(f'--space: no space called "{name}"')
    for name in excluded:
        if name not in titles:
            raise Arc2DiaError(f'--exclude-space: no space called "{name}"')

    spaces = []
    matched_overrides = set()
    for space in sb.spaces:
        infos = collect_folders(sb, space.pinned_id)
        nested = any(i.parent_folder is not None for i in infos.values())
        strategy = None
        for name, strat in space_strats:
            if name in (space.title, space.id):
                strategy = strat
        if strategy is None:
            # Dia flattens nested folders, so path-names keeps their names; Zen keeps nesting.
            strategy = args.strategy or ("path-names" if nested and not zen else "keep")
        sp = SpacePlan(
            space_id=space.id,
            title=space.title,
            import_space=not (space.title in excluded or space.id in excluded),
            folder_strategy=strategy,
            dedupe_trees=not args.no_dedupe_trees,
            dedupe_urls=args.dedupe_urls,
            open_tabs=args.open_tabs,
            open_tabs_folder_name=args.open_tabs_folder,
            open_tabs_split_views=args.split_views,
            open_tabs_tab_groups=args.tab_groups or ("subfolders" if zen else "flatten"),
            pinned_wrappers="keep" if args.keep_pinned_wrappers else ("folders" if zen else "unwrap"),
            drop_empty_folders=not args.keep_empty_folders,
            non_web=args.non_web,
            merge_bookmark_folders=not args.no_merge_bookmark_folders,
        )
        for path, strat in overrides:
            if match_folder_path(infos, path):
                sp.folder_overrides.append(FolderOverride(strategy=strat, path=path))
                matched_overrides.add(path)
        spaces.append(sp)
    for path, _ in overrides:
        if path not in matched_overrides:
            raise Arc2DiaError(f'--override: no pinned folder matches "{path}" in any space')
    return Plan(spaces=spaces, source_path=source_path, source_sha256=sha, target=target, max_folder_depth=max_depth)


def annotated_plan_json(sb: Sidebar, plan: Plan) -> dict:
    """The plan plus informational ``_`` keys that help when editing it by hand."""
    data = plan.to_json()
    data["_help"] = _plan_help(plan.target)
    return _annotate_spaces(sb, plan, data)


def _plan_help(target: str) -> dict:
    if target == "zen":
        return {
            "target": "zen: the cleaned file is meant for Zen's Arc importer (Zen 1.24 or later)",
            "max_folder_depth": f"pinned folders deeper than this are merged into their parent, as Zen does "
                                f"(its pref zen.folders.max-subfolders, {ZEN_MAX_SUBFOLDERS} by default); raise both "
                                "together, or null for no limit here",
            "folder_strategy": ZEN_STRATEGY_HELP,
            "folder_overrides": 'list of {"path": "A › B", "strategy": ...} or {"folder_id": ..., "strategy": ...}; '
                                "top-level folders take any strategy, nested folders only to-bookmarks",
            "import": "false leaves the space out of the import (Zen creates no space for it)",
            "open_tabs": {"pinned-folder": "move open tabs into one new pinned folder (default)",
                          "bookmarks": "export open tabs to bookmarks.html",
                          "leave": "do nothing; Zen's importer will skip them"},
            "open_tabs_split_views": "flatten: split views become their separate tabs; keep: leave them (Zen "
                                     "replaces a split view with its tabs anyway)",
            "open_tabs_tab_groups": "subfolders: one subfolder per tab group (default for Zen); flatten: tabs from "
                                    "Arc tab groups go straight into the open-tabs folder, in order",
            "pinned_wrappers": "folders: tab groups in the pinned area become folders with the same names, because "
                               "Zen's importer drops tab groups (default for Zen); unwrap: split views and tab "
                               "groups are replaced by their tabs; keep: leave them as they are",
            "merge_bookmark_folders": "in bookmarks.html, merge sibling folders with the same name and keep one copy "
                                      "of a URL per folder",
            "non_web": "keep: leave non-web links (Zen keeps file: and ftp: and skips the rest, such as "
                       "javascript:); to-bookmarks: export every non-http(s) link to bookmarks.html",
            "dedupe_trees": "remove folder trees whose URLs all exist in another folder with the same name",
            "dedupe_urls": "remove repeated URLs in the pinned area, keeping the first copy",
        }
    return {
        "folder_strategy": STRATEGY_HELP,
        "folder_overrides": 'list of {"path": "A › B", "strategy": ...} or {"folder_id": ..., "strategy": ...}; '
                            "top-level folders take any strategy, nested folders only to-bookmarks",
        "import": "false leaves the space out of the import (no Dia profile is created for it)",
        "open_tabs": {"pinned-folder": "move open tabs into one new pinned folder (default)",
                      "bookmarks": "export open tabs to bookmarks.html",
                      "leave": "do nothing; Dia's importer will skip them"},
        "open_tabs_split_views": "flatten: split views become their separate tabs; keep: leave split views as they are",
        "open_tabs_tab_groups": "flatten: tabs from Arc tab groups go straight into the open-tabs folder, in order; "
                                "subfolders: one subfolder per tab group (Dia turns each into its own flat group)",
        "pinned_wrappers": "unwrap: split views and tab groups in the pinned area are replaced by their tabs, in "
                           "order, because Dia's importer drops the tabs inside them (seen in a real migration "
                           "test); keep: leave them as they are",
        "merge_bookmark_folders": "in bookmarks.html, merge sibling folders with the same name and keep one copy "
                                  "of a URL per folder",
        "non_web": "keep: leave file:, javascript: and other non-web links (Dia drops them); "
                   "to-bookmarks: export them to bookmarks.html",
        "dedupe_trees": "remove folder trees whose URLs all exist in another folder with the same name",
        "dedupe_urls": "remove repeated URLs in the pinned area, keeping the first copy",
    }


def _annotate_spaces(sb: Sidebar, plan: Plan, data: dict) -> dict:
    for sp_json in data["spaces"]:
        space = sb.space(sp_json["space_id"])
        if space is None:
            continue
        s = scan_space(sb, space)
        sp_json["_about"] = {
            "pinned_tabs": s.pinned_tabs,
            "folders": s.folders,
            "nested_folders": s.nested_folders,
            "max_depth": s.max_depth,
            "open_tabs": s.open_tabs,
            "open_tab_groups": s.open_tab_groups,
            "pinned_split_views": s.pinned_split_views,
            "pinned_tab_groups": s.pinned_tab_groups,
            "pinned_wrapped_tabs": s.pinned_wrapped_tabs,
            "tabs_without_url": s.tabs_without_url,
            "non_web_urls": len(s.non_web),
            "duplicate_trees": [
                {"remove": join_path(d.removed_path), "remove_folders": d.folders, "keep": join_path(d.kept_path),
                 "keep_folders": d.kept_folders, "tabs": d.tabs, "match": d.match}
                for d in s.duplicate_trees
            ],
            "what_if_folder_strategy": what_if(sb, plan, space.id),
        }
        sp_json["_top_level_folders"] = [
            {"folder_id": t.id, "path": t.title, "tabs": t.tabs, "folders": t.folders, "max_depth": t.max_depth}
            for t in s.top_folders
        ]
    return data


def _n(count: int, word: str) -> str:
    return f"{count} {word if count == 1 else word + 's'}"


def render_plan_summary(data: dict) -> str:
    lines: List[str] = []
    add = lines.append
    target = data.get("target", "dia")
    browser = TARGET_NAMES.get(target, "Dia")
    add(f"Target: {browser}"
        + (f" (pinned folders deeper than {data['max_folder_depth']} levels are merged into their parent)"
           if target == "zen" and data.get("max_folder_depth") else ""))
    add("Folder strategies:")
    for name, desc in (ZEN_STRATEGY_HELP if target == "zen" else STRATEGY_HELP).items():
        add(f"  {name:<13} {desc}")
    for sp in data["spaces"]:
        about = sp.get("_about", {})
        add("")
        add(f'Space "{sp["title"]}": {about.get("folders", 0)} folders ({about.get("nested_folders", 0)} nested, '
            f'max depth {about.get("max_depth", 0)}), {about.get("pinned_tabs", 0)} pinned tabs, '
            f'{about.get("open_tabs", 0)} open tabs')
        if not sp["import"]:
            add("  import: false (" + ("Zen creates no space for it)" if target == "zen"
                                       else "no Dia profile will be created for this space)"))
            continue
        add(f"  chosen folder_strategy: {sp['folder_strategy']}")
        for ov in sp["folder_overrides"]:
            add(f"  override: {ov.get('path') or ov.get('folder_id')} -> {ov['strategy']}")
        wi = about.get("what_if_folder_strategy") or {}
        if wi:
            add("  what Dia would get (with this space's other options):")
            for strat, r in wi.items():
                mark = "*" if strat == sp["folder_strategy"] else " "
                add(f"   {mark} {strat:<13} {_n(r['pinned_groups'], 'group'):>10} ({r['nested_groups']} nested in Arc), "
                    f"{r['tabs_in_groups']} tabs in groups, {r['loose_pinned_tabs']} loose, "
                    f"{r['bookmarks']} to bookmarks, {r['deduplicated']} duplicates removed"
                    + (f", {r['wrapped_tabs_lost']} dropped in split views/tab groups"
                       if r.get("wrapped_tabs_lost") else ""))
        add(f"  open tabs: {sp['open_tabs']}" + (f' ("{sp["open_tabs_folder_name"]}")' if sp["open_tabs"] != "leave" else "")
            + f"; split views: {sp['open_tabs_split_views']}; tab groups: {sp['open_tabs_tab_groups']}"
            + (f" ({about['open_tab_groups']} in this space)" if about.get("open_tab_groups") else ""))
        if about.get("pinned_wrapped_tabs"):
            held = [_n(about.get(k, 0), w) for k, w in (("pinned_split_views", "split view"),
                                                         ("pinned_tab_groups", "tab group")) if about.get(k)]
            if target == "zen":
                add(f"  pinned split views and tab groups: {sp.get('pinned_wrappers', 'folders')} "
                    f"({', '.join(held)} holding {_n(about['pinned_wrapped_tabs'], 'tab')}; Zen's importer "
                    "drops tab groups and replaces split views with their tabs)")
            else:
                add(f"  pinned split views and tab groups: {sp.get('pinned_wrappers', 'unwrap')} "
                    f"({', '.join(held)} holding {_n(about['pinned_wrapped_tabs'], 'tab')}; "
                    "Dia's importer drops the tabs inside them)")
        add(f"  dedupe_trees: {str(sp['dedupe_trees']).lower()}, dedupe_urls: {str(sp['dedupe_urls']).lower()}, "
            f"drop_empty_folders: {str(sp['drop_empty_folders']).lower()}, non_web: {sp['non_web']}")
        for d in about.get("duplicate_trees", []):
            verb = "would be removed" if sp["dedupe_trees"] else "found (dedupe_trees is off)"
            add(f"    duplicate tree {verb}: {d['remove']} ({_n(d['tabs'], 'tab')} in {_n(d['remove_folders'], 'folder')}); "
                f"kept: {d['keep']} ({_n(d['keep_folders'], 'folder')})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- commands

def cmd_scan(args, deps: Deps) -> int:
    sb, sha, path = _load_sidebar(args.sidebar)
    report = scan(sb, str(path), sha)
    if args.tree:
        for space in sb.spaces:
            _out(render_tree(sb, space))
            _out()
        return 0
    if args.json:
        _out(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _out(render_scan(report))
    return 0


def cmd_plan(args, deps: Deps) -> int:
    sb, sha, path = _load_sidebar(args.sidebar)
    plan = build_plan(sb, args, str(path), sha)
    data = annotated_plan_json(sb, plan)
    if args.print:
        _out(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    out = expand(args.output)
    if out.exists() and not args.force:
        raise Arc2DiaError(f"{out} already exists; pass --force to overwrite it or -o to choose another file")
    write_json(out, data)
    _out(render_plan_summary(data))
    _out("")
    _out(f"Plan written to {out}. Review or edit it, then run: {PROG} prepare --plan {out}")
    return 0


def cmd_prepare(args, deps: Deps) -> int:
    sb, sha, path = _load_sidebar(args.sidebar)
    if args.plan:
        plan = load_plan(args.plan)
        if args.target and args.target != plan.target:
            raise Arc2DiaError(f"the plan {args.plan} is for {plan.target_name}, not {TARGET_NAMES[args.target]}; "
                               f"make a new one with `{PROG} plan --target {args.target}`")
        changed = [opt for opt, attr, default in _PLAN_OPTION_DEFAULTS if getattr(args, attr) != default]
        if changed:
            print(f"{PROG}: note: --plan is given, so {', '.join(changed)} "
                  f"{'is' if len(changed) == 1 else 'are'} ignored; edit the plan file instead", file=sys.stderr)
    else:
        plan = build_plan(sb, args, str(path), sha)
    outcome = prepare(path, plan, args.out_dir, force=args.force)
    if args.json:
        _out(json.dumps({"manifest": outcome.manifest, "validation": outcome.validation.to_dict(),
                         **outcome.result.to_dict()}, indent=2, ensure_ascii=False))
    else:
        _out(outcome.text)
    return 0 if outcome.validation.ok else 1


def cmd_swap(args, deps: Deps) -> int:
    for line in swap(args.sidebar, args.prepared, args.backup_dir, args.apply, deps.checker,
                     allow_other_source=args.allow_other_source):
        _out(line)
    return 0


def backup_root(arg: str) -> str:
    """The backup folder to read: the old ~/arc2dia-backups when only that one exists."""
    if (arg == str(DEFAULT_BACKUP_ROOT) and not expand(DEFAULT_BACKUP_ROOT).exists()
            and expand(LEGACY_BACKUP_ROOT).exists()):
        return str(LEGACY_BACKUP_ROOT)
    return arg


def cmd_restore(args, deps: Deps) -> int:
    for line in restore(args.sidebar, backup_root(args.backup_dir), args.apply, deps.checker, args.backup):
        _out(line)
    return 0


def cmd_status(args, deps: Deps) -> int:
    for line in status(args.sidebar, backup_root(args.backup_dir), deps.checker, args.prepared):
        _out(line)
    return 0


def cmd_verify(args, deps: Deps) -> int:
    if args.print_script:
        _out(READ_SCRIPT.strip())
        return 0
    mapping = dict(_pairs(args.map, "--map"))
    original, current_sha, current_path = _load_sidebar(args.sidebar)
    mode = "original"
    expected = original
    if args.prepared:
        manifest = load_manifest(args.prepared)
        if manifest.get("target", "dia") != "dia":
            raise Arc2DiaError("verify compares with Dia; for a Zen import, check each space against the "
                               '"Zen, after" lines in the prepare report (report.txt)')
        cleaned_path = expand(args.prepared) / manifest.get("cleaned_file", CLEANED_NAME)
        expected = Sidebar.load(cleaned_path)
        mode = "prepared"
        if current_sha == manifest.get("cleaned_sha256"):
            _out(f"Warning: {current_path} is still the cleaned copy. Run `{PROG} restore --apply` "
                 "before opening Arc.")
            original = expected
        elif current_sha != manifest.get("source_sha256"):
            # Arc changed the file since prepare, or the cleaned file came from another sidebar
            # (swap --allow-other-source). Explain differences with the prepared source if it is
            # still there, otherwise with the cleaned file itself.
            original = expected
            source = expand(manifest["source_path"]) if manifest.get("source_path") else None
            used = "the cleaned file"
            try:
                if source is not None and sha256_bytes(source.read_bytes()) == manifest.get("source_sha256"):
                    original, used = Sidebar.load(source), str(source)
            except (OSError, Arc2DiaError):
                pass
            _out(f"Note: {current_path} is not the file this migration was prepared from; "
                 f"differences are explained with {used}.")
    if args.dia_dump:
        source: DiaSource = DumpDiaSource(args.dia_dump)
    else:
        source = deps.dia_source or OsascriptDiaSource()
    profiles = source.read()
    report = verify(expected, profiles, original_sb=original, mode=mode, mapping=mapping)
    if args.json:
        _out(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _out(render_verify(report))
    return 0 if report.ok else 1


# --------------------------------------------------------------------------- parser

_PLAN_OPTION_DEFAULTS = [
    ("--strategy", "strategy", None),
    ("--space", "space", None),
    ("--override", "override", None),
    ("--exclude-space", "exclude_space", None),
    ("--no-dedupe-trees", "no_dedupe_trees", False),
    ("--dedupe-urls", "dedupe_urls", False),
    ("--open-tabs", "open_tabs", "pinned-folder"),
    ("--open-tabs-folder", "open_tabs_folder", DEFAULT_OPEN_TABS_FOLDER),
    ("--split-views", "split_views", "flatten"),
    ("--tab-groups", "tab_groups", None),
    ("--max-folder-depth", "max_folder_depth", None),
    ("--keep-pinned-wrappers", "keep_pinned_wrappers", False),
    ("--no-merge-bookmark-folders", "no_merge_bookmark_folders", False),
    ("--keep-empty-folders", "keep_empty_folders", False),
    ("--non-web", "non_web", "keep"),
]


def _add_plan_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("plan options (defaults shown in brackets)")
    g.add_argument("--target", choices=TARGETS,
                   help="browser that will import the cleaned file [dia; with --plan, the plan's own target]")
    g.add_argument("--strategy", choices=FOLDER_STRATEGIES,
                   help="folder strategy for every space [Dia: path-names when a space has nested folders, "
                        "else keep; Zen: keep]")
    g.add_argument("--space", action="append", metavar="SPACE=STRATEGY",
                   help="folder strategy for one space, by title or id (repeatable)")
    g.add_argument("--override", action="append", metavar="PATH=STRATEGY",
                   help='strategy for one folder path in any space, e.g. "Imported From Edge=to-bookmarks"; '
                        "nested folders only accept to-bookmarks (repeatable)")
    g.add_argument("--exclude-space", action="append", metavar="SPACE",
                   help="leave a space out of the import (repeatable)")
    g.add_argument("--no-dedupe-trees", action="store_true",
                   help="keep duplicate folder trees [they are removed]")
    g.add_argument("--dedupe-urls", action="store_true", help="remove repeated pinned URLs, keeping the first [off]")
    g.add_argument("--open-tabs", choices=OPEN_TABS_MODES, default="pinned-folder",
                   help="what to do with open (Today) tabs [pinned-folder]")
    g.add_argument("--open-tabs-folder", default=DEFAULT_OPEN_TABS_FOLDER, metavar="NAME",
                   help=f'name of the folder for open tabs ["{DEFAULT_OPEN_TABS_FOLDER}"]')
    g.add_argument("--split-views", choices=SPLIT_VIEW_MODES, default="flatten",
                   help="split views among open tabs [flatten]")
    g.add_argument("--tab-groups", choices=TAB_GROUP_MODES,
                   help="Arc tab groups among open tabs: flatten into the open-tabs folder, or one subfolder each "
                        "[Dia: flatten; Zen: subfolders]")
    g.add_argument("--keep-pinned-wrappers", action="store_true",
                   help="leave split views and tab groups in the pinned area as they are; Dia's importer drops "
                        "the tabs inside both, Zen's the tabs inside tab groups [Dia: they are unwrapped into "
                        "their tabs; Zen: tab groups become folders]")
    g.add_argument("--max-folder-depth", type=int, metavar="N",
                   help="Zen only: merge pinned folders deeper than N levels into their parent, as Zen does; set "
                        f"Zen's zen.folders.max-subfolders to the same N; 0 for no limit [{ZEN_MAX_SUBFOLDERS}]")
    g.add_argument("--keep-empty-folders", action="store_true", help="keep folders with no tabs [they are dropped]")
    g.add_argument("--no-merge-bookmark-folders", action="store_true",
                   help="in bookmarks.html, keep same-named sibling folders apart [they are merged]")
    g.add_argument("--non-web", choices=NON_WEB_MODES, default="keep",
                   help="file:, javascript: and other non-web links [keep; Dia drops them]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Move an Arc sidebar to Dia or Zen without losing open tabs or nested folders. "
                    "Workflow: scan, plan, prepare, swap, (import in Dia or Zen), restore, verify (Dia only).",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def sidebar_arg(p, default=str(ARC_SIDEBAR)):
        p.add_argument("--sidebar", default=default, metavar="PATH",
                       help=f"Arc's StorableSidebar.json [{ARC_SIDEBAR}]" if default else
                       "Arc's StorableSidebar.json [the path recorded at swap time]")

    p = sub.add_parser("scan", help="read-only diagnosis of the Arc sidebar")
    sidebar_arg(p)
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument("--tree", action="store_true", help="print each space as an outline instead")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan", help="write a plan file with per-space choices")
    sidebar_arg(p)
    p.add_argument("-o", "--output", default=str(DEFAULT_PLAN_FILE), metavar="FILE",
                   help=f"plan file to write [{DEFAULT_PLAN_FILE}]")
    p.add_argument("--force", action="store_true", help="overwrite an existing plan file")
    p.add_argument("--print", action="store_true", help="print the plan JSON instead of writing it")
    _add_plan_options(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("prepare", help="apply the plan to a copy; write the cleaned file and bookmarks.html")
    sidebar_arg(p)
    p.add_argument("--plan", metavar="FILE", help="plan file from `plan` [build one from the options below]")
    p.add_argument("-o", "--out-dir", default=str(DEFAULT_OUT_DIR), metavar="DIR",
                   help=f"output folder [{DEFAULT_OUT_DIR}]")
    p.add_argument("--force", action="store_true", help="overwrite a previous prepare in the same folder")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    _add_plan_options(p)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("swap", help="back up the Arc sidebar and install the cleaned copy (dry run unless --apply)")
    sidebar_arg(p)
    p.add_argument("--prepared", default=str(DEFAULT_OUT_DIR), metavar="DIR", help=f"output of prepare [{DEFAULT_OUT_DIR}]")
    p.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_ROOT), metavar="DIR",
                   help=f"where backups go [{DEFAULT_BACKUP_ROOT}]")
    p.add_argument("--apply", action="store_true", help="really do it")
    p.add_argument("--allow-other-source", action="store_true",
                   help="testing only: install a cleaned file prepared from a different sidebar, such as the "
                        "synthetic example; the current Arc file is still backed up and restored")
    p.set_defaults(func=cmd_swap)

    p = sub.add_parser("restore", help="put the original Arc sidebar back and check its hash (dry run unless --apply)")
    sidebar_arg(p, default=None)
    p.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_ROOT), metavar="DIR",
                   help=f"where backups are [{DEFAULT_BACKUP_ROOT}]")
    p.add_argument("--backup", metavar="NAME", help="restore this backup folder instead of the active swap")
    p.add_argument("--apply", action="store_true", help="really do it")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("status", help="show whether a swap is active and list backups")
    sidebar_arg(p)
    p.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_ROOT), metavar="DIR")
    p.add_argument("--prepared", metavar="DIR", help="also compare with this prepare output")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("verify", help="compare Dia's profiles with Arc (reads Dia through osascript, read-only)")
    sidebar_arg(p)
    p.add_argument("--prepared", metavar="DIR", help="compare with the cleaned file Dia imported [compare with Arc as is]")
    p.add_argument("--map", action="append", metavar="SPACE=PROFILE", help="pair an Arc space with a Dia profile (repeatable)")
    p.add_argument("--dia-dump", metavar="FILE", help="read Dia data saved from the script instead of running osascript")
    p.add_argument("--print-script", action="store_true", help="print the read-only AppleScript and exit")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[List[str]] = None, checker: Optional[ProcessChecker] = None,
         dia_source: Optional[DiaSource] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    deps = Deps(checker or PgrepChecker(), dia_source)
    try:
        return args.func(args, deps)
    except Arc2DiaError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:  # output piped into head, less...
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
