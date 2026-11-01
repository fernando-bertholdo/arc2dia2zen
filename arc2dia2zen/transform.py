"""Apply a plan to a copy of the sidebar. The source Sidebar is never modified.

Order of operations for each space:
  1. excluded spaces are removed whole (import: false)
  2. duplicate folder trees are removed (dedupe_trees)
  3. folders set to to-bookmarks are exported and removed
  4. split views and tab groups left in the pinned area are unwrapped: their items take the
     wrapper's place, in order (pinned_wrappers: unwrap). Dia's importer drops the tabs inside
     them (seen in a real migration test).
  5. the folder strategy is applied to each remaining top-level folder
  6. open (Today) tabs, including those inside Arc tab groups, are moved to a pinned
     folder or exported (open_tabs)
  7. non-web links are exported (non_web: to-bookmarks)
  8. repeated URLs are removed (dedupe_urls)
  9. folders left without content are removed (drop_empty_folders)

Every tab of the original file gets one ledger entry that says where it ended up.
Tab groups count as folders for steps 2 and 3. Tabs without a saved URL are never removed or
exported: they cannot be bookmarks or duplicates, so they stay in the sidebar wherever they go.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from . import urls as U
from .analysis import (
    DiaPrediction,
    collect_folders,
    find_duplicate_trees,
    join_path,
    match_folder_path,
    predict_import,
)
from .bookmarks import BNode, count_links
from .errors import PlanError
from .model import (
    FOLDER_KINDS,
    KIND_SPLIT,
    KIND_TAB,
    KIND_TAB_GROUP,
    WRAPPER_KINDS,
    Sidebar,
    Space,
    apple_to_unix,
    folder_title,
    is_unsupported_kind,
    item_label,
    new_item_id,
    set_folder_title,
    tab_title,
    tab_url,
    unix_to_apple,
)
from .plan import FOLDER_STRATEGIES, Plan, SpacePlan, unchanged_space_plan

NON_WEB_FOLDER = "Non-web links from Arc"

FATE_SIDEBAR = "sidebar"
FATE_BOOKMARKS = "bookmarks"
FATE_DEDUPED = "deduplicated"
FATE_DROPPED = "dropped"
FATES = (FATE_SIDEBAR, FATE_BOOKMARKS, FATE_DEDUPED, FATE_DROPPED)


@dataclass
class LedgerEntry:
    item_id: str
    url: str
    title: str
    space: Optional[str]
    space_id: Optional[str]
    origin: str  # pinned, open, favorites or orphan
    original_path: str
    fate: str = FATE_SIDEBAR
    reason: str = ""
    new_path: Optional[str] = None
    bookmarks_path: Optional[str] = None
    dia_note: str = ""


@dataclass
class SpaceResult:
    space_id: str
    title: str
    plan: dict
    actions: List[str] = field(default_factory=list)
    before: Optional[DiaPrediction] = None
    after: Optional[DiaPrediction] = None
    bookmarks: int = 0
    unwrapped: List[dict] = field(default_factory=list)  # pinned split views and tab groups unwrapped


@dataclass
class PrepareResult:
    sidebar: Sidebar
    bookmarks: List[BNode]
    ledger: List[LedgerEntry]
    spaces: List[SpaceResult]
    warnings: List[str]

    def fate_counts(self) -> Dict[str, int]:
        counts = {f: 0 for f in FATES}
        for e in self.ledger:
            counts[e.fate] = counts.get(e.fate, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "fates": self.fate_counts(),
            "spaces": [asdict(s) for s in self.spaces],
            "warnings": self.warnings,
            "ledger": [asdict(e) for e in self.ledger],
        }


def _n(count: int, word: str) -> str:
    return f"{count} {word if count == 1 else word + 's'}"


class Transformer:
    def __init__(
        self,
        source: Sidebar,
        plan: Plan,
        id_factory: Callable[[], str] = new_item_id,
        now: Optional[float] = None,
    ):
        self.src = source
        self.sb = source.clone()
        self.plan = plan
        self.sep = plan.path_separator
        self.new_id = id_factory
        self.now_apple = unix_to_apple(time.time() if now is None else now)
        self.warnings: List[str] = []
        self.ledger: Dict[str, LedgerEntry] = {}
        self.bm_roots: List[BNode] = []
        self._bm_space_nodes: Dict[str, BNode] = {}
        self._bm_context: Dict[Tuple[str, Tuple[str, ...]], BNode] = {}
        self._bm_urls: Dict[int, set] = {}
        self._merge_bm = True
        self._unwrapped_tabs: set = set()
        self._build_ledger()

    # ------------------------------------------------------------------ ledger

    def _build_ledger(self) -> None:
        where = self.src.locate_all()
        for item_id, obj in self.src.items.items():
            if self.src.kind(item_id) != KIND_TAB:
                continue
            loc = where[item_id]
            space = self.src.space(loc.space_id) if loc.space_id else None
            self.ledger[item_id] = LedgerEntry(
                item_id=item_id,
                url=tab_url(obj) or "",
                title=tab_title(obj),
                space=space.title if space else None,
                space_id=loc.space_id,
                origin=loc.area,
                original_path=join_path(loc.path) if loc.area in ("pinned", "open", "favorites") else "",
            )

    def _set_fate(self, item_id: str, fate: str, reason: str, bookmarks_path: Optional[str] = None) -> None:
        entry = self.ledger.get(item_id)
        if entry is None:
            return
        entry.fate = fate
        entry.reason = reason
        entry.bookmarks_path = bookmarks_path

    def _finalize_ledger(self) -> None:
        where = self.sb.locate_all()
        wrapped = set()
        for space in self.sb.spaces:
            wrapped |= self.sb.wrapped_ids(space.pinned_id)
        for entry in self.ledger.values():
            if entry.fate != FATE_SIDEBAR:
                continue
            loc = where.get(entry.item_id)
            if loc is None:
                # Should never happen; validation reports it as an error.
                entry.new_path = None
                continue
            entry.new_path = f"{loc.area}: {join_path(loc.path)}" if loc.area != "orphan" else "orphan"
            notes = []
            if entry.url and not U.is_web(entry.url):
                notes.append("non-http(s) URL: Dia drops it on import (expected)")
            if loc.area == "open":
                notes.append("still an open tab: Dia's importer does not import open tabs")
            if loc.area == "pinned" and entry.item_id in wrapped:
                notes.append("inside a pinned split view or tab group: Dia's importer drops it "
                             "(seen in a real migration test)")
            elif entry.item_id in self._unwrapped_tabs:
                notes.append("moved out of a pinned split view or tab group, whose tabs Dia's importer drops")
            if loc.area == "orphan":
                notes.append("not under any space; Dia is not expected to import it")
            if not entry.url:
                entry.reason = "no URL"
                notes.append("tab has no saved URL; Dia may skip it")
            entry.dia_note = "; ".join(notes)

    # ------------------------------------------------------------------ bookmarks tree

    def _bm_space(self, space: Space) -> BNode:
        node = self._bm_space_nodes.get(space.id)
        if node is None:
            node = BNode(space.title)
            self._bm_space_nodes[space.id] = node
            self.bm_roots.append(node)
        return node

    def _bm_child(self, parent: BNode, title: str, add_date: Optional[int] = None) -> BNode:
        """A folder under ``parent``. With merge_bookmark_folders, a sibling folder with the
        same name is reused, so two trees with one name end up as one folder."""
        if self._merge_bm:
            for child in parent.children:
                if child.is_folder and child.title == title:
                    return child
        return parent.folder(title, add_date)

    def _bm_folder(self, space: Space, path: Tuple[str, ...]) -> BNode:
        """Folder node for ``path`` under the space node, created on demand (context folders)."""
        node = self._bm_space(space)
        for depth in range(1, len(path) + 1):
            if self._merge_bm:
                node = self._bm_child(node, path[depth - 1])
                continue
            key = (space.id, path[:depth])
            child = self._bm_context.get(key)
            if child is None:
                child = node.folder(path[depth - 1])
                self._bm_context[key] = child
            node = child
        return node

    def _bm_link(self, space: Space, parent: BNode, item_id: str, obj: dict, path: Tuple[str, ...], reason: str) -> None:
        url = tab_url(obj) or ""
        key = U.normalize(url)
        seen = self._bm_urls.setdefault(id(parent), {U.normalize(c.url) for c in parent.children if not c.is_folder})
        if self._merge_bm and key in seen:
            self._set_fate(item_id, FATE_DEDUPED,
                           f"same URL already in bookmarks.html folder {self._bm_path(space, path)}")
            return
        seen.add(key)
        parent.children.append(BNode(tab_title(obj), url, apple_to_unix(obj.get("createdAt"))))
        self._set_fate(item_id, FATE_BOOKMARKS, reason, self._bm_path(space, path))

    def _bm_path(self, space: Space, path: Tuple[str, ...]) -> str:
        return self.sep.join((space.title,) + path)

    def _export_items(self, space: Space, item_ids: List[str], parent: BNode, path: Tuple[str, ...], reason: str) -> None:
        """Append bookmarks for ``item_ids`` (folders recursively, split views flattened) under ``parent``."""
        for item_id in item_ids:
            obj = self.sb.items.get(item_id)
            if obj is None:
                continue
            kind = self.sb.kind(item_id)
            if kind == KIND_TAB:
                if tab_url(obj):
                    self._bm_link(space, parent, item_id, obj, path, reason)
                # A tab without a URL cannot be a bookmark; _rescue_keepers leaves it in the sidebar.
            elif kind in FOLDER_KINDS:
                title = folder_title(obj)
                node = self._bm_child(parent, title, apple_to_unix(obj.get("createdAt")))
                self._export_items(space, self.sb.children(item_id), node, path + (title,), reason)
            elif kind == KIND_SPLIT:
                self._export_items(space, self.sb.children(item_id), parent, path, reason)

    # ------------------------------------------------------------------ structural helpers

    def _rescue_keepers(self, root_id: str, root_path: Tuple[str, ...]) -> List[str]:
        """Before a subtree is removed, move the items that cannot be bookmarks or duplicates
        (easels, Arc documents, unknown kinds, tabs without a URL) next to the subtree root,
        so they stay in the sidebar. Returns one note per rescued item."""
        notes: List[str] = []
        if root_id not in self.sb.items:
            return notes
        parent_id = self.sb.parent_of(root_id)
        if parent_id is None:
            return notes
        rescued = [
            e for e in self.sb.walk(root_id)
            if (is_unsupported_kind(e.kind) and not e.in_split) or (e.kind == KIND_TAB and not tab_url(e.obj))
        ]
        for e in rescued:
            self.sb.detach(e.id)
            siblings = self.sb.children(parent_id)
            index = siblings.index(root_id) if root_id in siblings else None
            self.sb.attach(e.id, parent_id, index)
            what = "tab without URL" if e.kind == KIND_TAB else e.kind
            notes.append(f'kept {what} "{item_label(e.obj)}" in the sidebar (moved out of {join_path(root_path + e.path)})')
        return notes

    def _remove_subtree(self, root_id: str) -> None:
        self.sb.delete_subtree(root_id)

    # ------------------------------------------------------------------ main entry

    def run(self) -> PrepareResult:
        known = {s.id for s in self.src.spaces}
        for sp in self.plan.spaces:
            if sp.space_id not in known:
                raise PlanError(
                    f'plan lists space {sp.space_id} ("{sp.title}") which is not in this sidebar; '
                    "re-run plan against the current file"
                )
        results = []
        for space in list(self.sb.spaces):
            sp = self.plan.for_space(space.id)
            if sp is None:
                sp = unchanged_space_plan(space.id, space.title)
                self.warnings.append(f'space "{space.title}" is not in the plan; left unchanged')
            results.append(self._apply_space(space, sp))
        self._finalize_ledger()
        self.sb.serialize()
        return PrepareResult(self.sb, self.bm_roots, list(self.ledger.values()), results, self.warnings)

    def _apply_space(self, space: Space, sp: SpacePlan) -> SpaceResult:
        res = SpaceResult(space.id, space.title, sp.to_json(), before=predict_import(self.sb, space))
        if not sp.import_space:
            self._exclude_space(space, res.actions)
            res.after = None
            return res
        self._merge_bm = sp.merge_bookmark_folders
        overrides = self._resolve_overrides(space, sp)
        if sp.dedupe_trees:
            self._dedupe_trees(space, res.actions)
        self._export_bookmark_folders(space, sp, overrides, res.actions)
        if sp.pinned_wrappers == "unwrap":
            res.unwrapped = self._unwrap_pinned(space, res.actions)
        self._apply_folder_strategies(space, sp, overrides, res.actions)
        self._handle_open_tabs(space, sp, res.actions)
        if sp.non_web == "to-bookmarks":
            self._export_non_web(space, res.actions)
        if sp.dedupe_urls:
            self._dedupe_urls(space, res.actions)
        if sp.drop_empty_folders:
            self._drop_empty(space, res.actions)
        res.after = predict_import(self.sb, space)
        node = self._bm_space_nodes.get(space.id)
        res.bookmarks = count_links([node]) if node else 0
        return res

    # ------------------------------------------------------------------ steps

    def _exclude_space(self, space: Space, actions: List[str]) -> None:
        tabs = 0
        for root in (space.pinned_id, space.unpinned_id):
            if root is None or root not in self.sb.items:
                continue
            for item_id in self.sb.subtree_ids(root):
                if self.sb.kind(item_id) == KIND_TAB:
                    tabs += 1
                    self._set_fate(item_id, FATE_DROPPED, "space excluded by the plan (import: false); still in Arc")
            self._remove_subtree(root)
        self.sb.spaces = [s for s in self.sb.spaces if s.id != space.id]
        actions.append(f"excluded the space from the import ({_n(tabs, 'tab')} stay in Arc only)")

    def _resolve_overrides(self, space: Space, sp: SpacePlan) -> Dict[str, str]:
        infos = collect_folders(self.sb, space.pinned_id)
        resolved: Dict[str, str] = {}
        for ov in sp.folder_overrides:
            if ov.folder_id is not None:
                if ov.folder_id not in infos:
                    raise PlanError(
                        f'space "{space.title}": override folder_id {ov.folder_id} is not a pinned folder of this space'
                    )
                matches = [ov.folder_id]
            else:
                matches = match_folder_path(infos, ov.path or "")
                if not matches:
                    tops = ", ".join(f'"{i.title}"' for i in infos.values() if i.parent_folder is None) or "none"
                    raise PlanError(
                        f'space "{space.title}": no pinned folder matches override path "{ov.path}" '
                        f"(top-level folders: {tops})"
                    )
            for fid in matches:
                if infos[fid].parent_folder is not None and ov.strategy != "to-bookmarks":
                    raise PlanError(
                        f'space "{space.title}": folder "{join_path(infos[fid].path)}" is nested; '
                        "only to-bookmarks can be set on a nested folder"
                    )
                resolved[fid] = ov.strategy
        return resolved

    def _dedupe_trees(self, space: Space, actions: List[str]) -> None:
        for dup in find_duplicate_trees(self.sb, space):
            if dup.removed_id not in self.sb.items:
                continue
            reason = f"duplicate folder tree; the same URLs are kept in {join_path(dup.kept_path)}"
            kept = self._rescue_keepers(dup.removed_id, dup.removed_path)
            for item_id in self.sb.subtree_ids(dup.removed_id):
                if self.sb.kind(item_id) == KIND_TAB:
                    self._set_fate(item_id, FATE_DEDUPED, reason)
            self._remove_subtree(dup.removed_id)
            how = "identical to" if dup.match == "identical" else "all URLs also in"
            actions.append(
                f"removed duplicate tree {join_path(dup.removed_path)} ({_n(dup.tabs, 'tab')}, "
                f"{_n(dup.folders, 'folder')}; {how} {join_path(dup.kept_path)}, {_n(dup.kept_folders, 'folder')})"
            )
            actions.extend(kept)

    def _export_bookmark_folders(self, space: Space, sp: SpacePlan, overrides: Dict[str, str], actions: List[str]) -> None:
        infos = collect_folders(self.sb, space.pinned_id)
        targets = []
        for fid, info in infos.items():  # document order
            strategy = overrides.get(fid)
            if info.parent_folder is None and strategy is None:
                strategy = sp.folder_strategy
            if strategy == "to-bookmarks":
                targets.append(info)
        for info in targets:
            if info.id not in self.sb.items:
                continue  # already exported with an ancestor
            parent = self._bm_folder(space, info.path[:-1])
            merged = self._merge_bm and any(c.is_folder and c.title == info.title for c in parent.children)
            self._export_items(space, [info.id], parent, info.path[:-1], "folder exported to bookmarks.html (to-bookmarks)")
            kept = self._rescue_keepers(info.id, info.path)
            self._remove_subtree(info.id)
            actions.append(
                f"exported {join_path(info.path)} to bookmarks.html ({_n(info.total_tabs, 'tab')}, "
                f"{_n(info.total_folders, 'folder')}) and removed it from the sidebar"
                + (f'; merged into the "{info.title}" folder already in bookmarks.html' if merged else "")
            )
            actions.extend(kept)

    def _unwrap_pinned(self, space: Space, actions: List[str]) -> List[dict]:
        """Dia's importer drops the tabs inside split views and Arc tab groups in the pinned area
        (seen in a real migration test). Replace every such wrapper, at any depth, with its own
        items, in order, in the same parent; the wrapper item is removed. Returns what was unwrapped."""
        pinned = space.pinned_id
        if pinned is None or pinned not in self.sb.items:
            return []
        done: List[dict] = []
        moved: set = set()
        seen = {pinned}

        def flatten(pid: str, path: Tuple[str, ...]) -> None:
            out: list = []
            for c in list(self.sb.items[pid].get("childrenIds") or []):
                if not isinstance(c, str) or c not in self.sb.items or c in seen:
                    out.append(c)  # dangling ids and repeats are left as they were
                    continue
                seen.add(c)
                kind = self.sb.kind(c)
                obj = self.sb.items[c]
                if kind in WRAPPER_KINDS:
                    direct = sum(1 for x in self.sb.children(c) if self.sb.kind(x) == KIND_TAB)
                    done.append({
                        "kind": kind,
                        "title": folder_title(obj) if kind == KIND_TAB_GROUP else None,
                        "path": join_path(path) if path else "",
                        "tabs": direct,  # its own tabs; a nested wrapper is listed on its own
                    })
                    flatten(c, path + (folder_title(obj),) if kind == KIND_TAB_GROUP else path)
                    # Ids that point to nothing go with the wrapper.
                    inner = [x for x in obj.get("childrenIds") or [] if isinstance(x, str) and x in self.sb.items]
                    for x in inner:
                        self.sb.items[x]["parentID"] = pid
                        if self.sb.kind(x) == KIND_TAB:
                            moved.add(x)
                    out.extend(inner)
                    self.sb.items.pop(c, None)
                else:
                    if kind in FOLDER_KINDS:
                        flatten(c, path + (folder_title(obj),))
                    elif self.sb.children(c):
                        flatten(c, path)
                    out.append(c)
            self.sb.items[pid]["childrenIds"] = out

        flatten(pinned, ())
        if not done:
            return done
        self._unwrapped_tabs |= moved
        splits = [d for d in done if d["kind"] == KIND_SPLIT]
        groups = [d for d in done if d["kind"] == KIND_TAB_GROUP]
        what = " and ".join(x for x in (_n(len(splits), "split view") if splits else "",
                                        _n(len(groups), "tab group") if groups else "") if x)

        def where(d: dict) -> str:
            return f"in {d['path']}" if d["path"] else "at the top level"

        details = [f'tab group "{d["title"]}" {where(d)}, {_n(d["tabs"], "tab")}' for d in groups]
        details += [f"split view {where(d)}, {_n(d['tabs'], 'tab')}" for d in splits]
        one = len(done) == 1
        actions.append(
            f"unwrapped {what} in the pinned area, because Dia's importer drops the tabs inside "
            + ("it" if one else "them")
            + f"; {'its' if one else 'their'} {_n(len(moved), 'tab')} now sit where "
            + ("it was" if one else "the wrappers were")
            + ", in order (" + "; ".join(details[:5]) + ("; ..." if len(details) > 5 else "") + ")"
        )
        return done

    def _apply_folder_strategies(self, space: Space, sp: SpacePlan, overrides: Dict[str, str], actions: List[str]) -> None:
        pinned = space.pinned_id
        if pinned is None or pinned not in self.sb.items:
            return
        tops = [c for c in self.sb.children(pinned) if self.sb.kind(c) in FOLDER_KINDS]
        counts = {s: 0 for s in FOLDER_STRATEGIES}
        made = 0
        for fid in tops:
            strategy = overrides.get(fid, sp.folder_strategy)
            if strategy == "path-names":
                made += self._path_names(pinned, fid)
            elif strategy == "merge-to-top":
                self._merge_to_top(fid)
            counts[strategy] += 1
        if counts["path-names"]:
            actions.append(
                f"path-names: {_n(counts['path-names'], 'top-level folder')} became {_n(made, 'flat folder')} "
                "named after their paths"
            )
        if counts["merge-to-top"]:
            actions.append(f"merge-to-top: merged subfolders into {_n(counts['merge-to-top'], 'top-level folder')}")

    def _path_names(self, pinned: str, top_id: str) -> int:
        emitted: List[Tuple[str, Tuple[str, ...], List[str]]] = []
        dropped: List[str] = []
        seen = set()

        def visit(fid: str, path: Tuple[str, ...]) -> None:
            seen.add(fid)
            obj = self.sb.items[fid]
            p = path + (folder_title(obj),)
            kids = [c for c in self.sb.children(fid) if c in self.sb.items and c not in seen]
            direct = [c for c in kids if self.sb.kind(c) not in FOLDER_KINDS]
            subs = [c for c in kids if self.sb.kind(c) in FOLDER_KINDS]
            if direct or not subs:
                emitted.append((fid, p, direct))
            else:
                dropped.append(fid)
            for s in subs:
                visit(s, p)

        visit(top_id, ())
        pinned_obj = self.sb.items[pinned]
        kids = list(pinned_obj.get("childrenIds") or [])
        idx = kids.index(top_id)
        pinned_obj["childrenIds"] = kids[:idx] + [e[0] for e in emitted] + kids[idx + 1:]
        for fid, path, direct in emitted:
            obj = self.sb.items[fid]
            if len(path) > 1:
                set_folder_title(obj, self.sep.join(path))
            obj["childrenIds"] = list(direct)
            obj["parentID"] = pinned
        for fid in dropped:
            self.sb.items.pop(fid, None)
        return len(emitted)

    def _merge_to_top(self, top_id: str) -> None:
        content: List[str] = []
        folders: List[str] = []
        seen = {top_id}

        def visit(fid: str) -> None:
            for c in self.sb.children(fid):
                if c in seen or c not in self.sb.items:
                    continue
                seen.add(c)
                if self.sb.kind(c) in FOLDER_KINDS:
                    folders.append(c)
                    visit(c)
                else:
                    content.append(c)

        visit(top_id)
        self.sb.items[top_id]["childrenIds"] = content
        for c in content:
            self.sb.items[c]["parentID"] = top_id
        for f in folders:
            self.sb.items.pop(f, None)

    def _new_folder(self, title: str, parent_id: str) -> str:
        """A new, detached folder item shaped like Arc's own folders."""
        fid = self.new_id()
        folder = {
            "id": fid,
            "title": title,
            "parentID": parent_id,
            "childrenIds": [],
            "data": {"list": {}},
            "createdAt": self.now_apple,
            "isUnread": False,
        }
        device = self.sb.items[parent_id].get("originatingDevice") if parent_id in self.sb.items else None
        if device is not None:
            folder["originatingDevice"] = device
        self.sb.items[fid] = folder
        return fid

    def _handle_open_tabs(self, space: Space, sp: SpacePlan, actions: List[str]) -> None:
        """Move the open tabs into one pinned folder (or bookmarks.html). Tabs inside Arc tab
        groups and folders come along; by default their wrappers are flattened, in order."""
        unpinned = space.unpinned_id
        if sp.open_tabs == "leave" or unpinned is None or unpinned not in self.sb.items:
            return
        to_bookmarks = sp.open_tabs == "bookmarks"
        flatten_splits = sp.open_tabs_split_views == "flatten" or to_bookmarks
        group_folders = sp.open_tabs_tab_groups == "subfolders"
        leftovers: List[str] = []   # stays among the open tabs
        removed: List[str] = []     # wrappers to delete (folders, tab groups, flattened split views)
        stats = {"groups": 0, "splits": 0, "tabs": 0}
        seen = {unpinned}

        def visit(pid: str, sink: list) -> None:
            """Fill ``sink`` with item ids to move and (title, entries) pairs for tab groups kept as folders."""
            for c in self.sb.children(pid):
                if c in seen or c not in self.sb.items:
                    continue
                seen.add(c)
                kind = self.sb.kind(c)
                obj = self.sb.items[c]
                if kind == KIND_TAB_GROUP:
                    stats["groups"] += 1
                    removed.append(c)
                    if group_folders:
                        entries: list = []
                        visit(c, entries)
                        if entries:
                            sink.append((folder_title(obj), entries))
                    else:
                        visit(c, sink)
                elif kind in FOLDER_KINDS:
                    removed.append(c)
                    visit(c, sink)
                elif kind == KIND_SPLIT and flatten_splits:
                    stats["splits"] += 1
                    removed.append(c)
                    visit(c, sink)
                elif kind == KIND_TAB and to_bookmarks and not tab_url(obj):
                    leftovers.append(c)  # cannot be a bookmark: stays an open tab
                elif kind == KIND_TAB:
                    stats["tabs"] += 1
                    sink.append(c)
                elif kind == KIND_SPLIT:
                    stats["tabs"] += sum(1 for x in self.sb.children(c) if self.sb.kind(x) == KIND_TAB)
                    sink.append(c)
                else:
                    leftovers.append(c)

        entries: list = []
        visit(unpinned, entries)
        if not entries:
            return
        name = sp.open_tabs_folder_name
        groups_note = ""
        if stats["groups"]:
            groups_note = (f"{_n(stats['groups'], 'tab group')} kept as subfolders" if group_folders
                           else f"{_n(stats['groups'], 'tab group')} flattened")
        notes = [n for n in (groups_note, f"{_n(stats['splits'], 'split view')} flattened" if stats["splits"] else "") if n]

        if to_bookmarks:
            reason = "open tab exported to bookmarks.html (open_tabs: bookmarks)"
            root = self._bm_child(self._bm_space(space), name)

            def export(ents: list, node: BNode, path: Tuple[str, ...]) -> None:
                for ent in ents:
                    if isinstance(ent, tuple):
                        title, sub = ent
                        export(sub, self._bm_child(node, title), path + (title,))
                    else:
                        self._export_items(space, [ent], node, path, reason)
                        for i in self.sb.subtree_ids(ent):
                            self.sb.items.pop(i, None)

            export(entries, root, (name,))
            actions.append(f'exported {_n(stats["tabs"], "open tab")} to bookmarks.html folder "{name}"'
                           + (f" ({'; '.join(notes)})" if notes else ""))
        else:
            pinned = space.pinned_id
            if pinned is None or pinned not in self.sb.items:
                self.warnings.append(f'space "{space.title}" has no pinned container; open tabs left in place')
                return

            def build(ents: list, parent_id: str) -> List[str]:
                ids = []
                for ent in ents:
                    if isinstance(ent, tuple):
                        title, sub = ent
                        gid = self._new_folder(title, parent_id)
                        self.sb.items[gid]["childrenIds"] = build(sub, gid)
                        ids.append(gid)
                    else:
                        self.sb.items[ent]["parentID"] = parent_id
                        ids.append(ent)
                return ids

            fid = self._new_folder(name, pinned)
            self.sb.items[fid]["childrenIds"] = build(entries, fid)
            self.sb.attach(fid, pinned)
            kept_splits = sum(1 for i in self.sb.subtree_ids(fid) if self.sb.kind(i) == KIND_SPLIT)
            if kept_splits:
                self.warnings.append(
                    f'space "{space.title}": {_n(kept_splits, "split view")} kept in "{name}" '
                    "(open_tabs_split_views: keep); Dia's importer drops the tabs inside pinned split views "
                    "(seen in a real migration test), so use flatten to keep them"
                )
            actions.append(f'moved {_n(stats["tabs"], "open tab")} into a new pinned folder "{name}"'
                           + (f" ({'; '.join(notes)})" if notes else ""))
        for r in removed:
            self.sb.items.pop(r, None)
        self.sb.items[unpinned]["childrenIds"] = leftovers
        for lo in leftovers:
            self.sb.items[lo]["parentID"] = unpinned
        if leftovers:
            actions.append(f"left {_n(len(leftovers), 'item')} among the open tabs (unsupported, or tabs without a URL)")

    def _export_non_web(self, space: Space, actions: List[str]) -> None:
        found = [
            e for e in self.sb.walk(space.pinned_id)
            if e.kind == KIND_TAB and tab_url(e.obj) and not U.is_web(tab_url(e.obj))
        ]
        if not found:
            return
        parent = self._bm_child(self._bm_space(space), NON_WEB_FOLDER)
        exported = 0
        for e in found:
            if e.in_split:
                self.warnings.append(f'space "{space.title}": non-web link in a split view kept in the sidebar: {tab_url(e.obj)}')
                continue
            self._export_items(space, [e.id], parent, (NON_WEB_FOLDER,), "non-web link exported to bookmarks.html (non_web: to-bookmarks)")
            self.sb.detach(e.id)
            self.sb.items.pop(e.id, None)
            exported += 1
        actions.append(f'exported {_n(exported, "non-web link")} to bookmarks.html folder "{NON_WEB_FOLDER}"')

    def _dedupe_urls(self, space: Space, actions: List[str]) -> None:
        first: Dict[str, Tuple[str, ...]] = {}
        drop = []
        for e in self.sb.walk(space.pinned_id):
            if e.kind != KIND_TAB:
                continue
            url = tab_url(e.obj)
            if not url:
                continue
            key = U.normalize(url)
            if key not in first:
                first[key] = e.path
            elif not e.in_split:
                drop.append((e, first[key]))
        for e, kept_path in drop:
            self._set_fate(e.id, FATE_DEDUPED, f"repeated URL; the first copy is kept in {join_path(kept_path)}")
            self.sb.detach(e.id)
            self.sb.items.pop(e.id, None)
        if drop:
            actions.append(f"removed {_n(len(drop), 'repeated URL')} (first copy kept)")

    def _drop_empty(self, space: Space, actions: List[str]) -> None:
        pinned = space.pinned_id
        if pinned is None or pinned not in self.sb.items:
            return
        dropped: List[str] = []

        def prune(fid: str, path: Tuple[str, ...]) -> None:
            p = path + (folder_title(self.sb.items[fid]),)
            for c in list(self.sb.children(fid)):
                if self.sb.kind(c) in FOLDER_KINDS:
                    prune(c, p)
            if not [c for c in self.sb.children(fid) if c in self.sb.items]:
                self.sb.detach(fid)
                self.sb.items.pop(fid, None)
                dropped.append(join_path(p))

        for c in list(self.sb.children(pinned)):
            if self.sb.kind(c) in FOLDER_KINDS:
                prune(c, ())
        if dropped:
            actions.append(f"dropped {_n(len(dropped), 'empty folder')}: " + "; ".join(dropped[:5])
                           + (" ..." if len(dropped) > 5 else ""))


def transform(source: Sidebar, plan: Plan, **kwargs) -> PrepareResult:
    return Transformer(source, plan, **kwargs).run()


def what_if(source: Sidebar, plan: Plan, space_id: str) -> Dict[str, dict]:
    """Run the plan for one space with each folder strategy and report the outcome."""
    base = plan.for_space(space_id)
    if base is None:
        return {}
    out: Dict[str, dict] = {}
    for strategy in FOLDER_STRATEGIES:
        variant = replace(base, folder_strategy=strategy, folder_overrides=[])
        only = Plan(spaces=[variant], path_separator=plan.path_separator)
        counter = itertools.count(1)
        res = Transformer(source, only, id_factory=lambda: "WHATIF-%d" % next(counter)).run()
        sr = res.spaces[[s.space_id for s in res.spaces].index(space_id)]
        after = sr.after
        dedup = sum(1 for e in res.ledger if e.space_id == space_id and e.fate == FATE_DEDUPED)
        out[strategy] = {
            "pinned_groups": after.pinned_groups if after else 0,
            "nested_groups": after.nested_groups if after else 0,
            "loose_pinned_tabs": after.loose_pinned_tabs if after else 0,
            "tabs_in_groups": after.tabs_in_groups if after else 0,
            "bookmarks": sr.bookmarks,
            "deduplicated": dedup,
            "open_tabs_lost": after.open_tabs_lost if after else 0,
            "wrapped_tabs_lost": after.wrapped_tabs_lost if after else 0,
        }
    return out
