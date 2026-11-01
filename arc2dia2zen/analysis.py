"""Read-only diagnosis of a sidebar: counts, duplicates and a prediction of Dia's importer."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from . import urls as U
from .model import (
    FOLDER_KINDS,
    KIND_FOLDER,
    KIND_SPLIT,
    KIND_TAB,
    KIND_TAB_GROUP,
    Sidebar,
    Space,
    folder_title,
    is_unsupported_kind,
    tab_url,
)
from .plan import split_folder_path

PATH_SEP = " › "

# Two folders with the same title count as duplicate trees when every URL of one is also
# in the other. Folders with different titles are only reported as "similar" when their
# URL sets overlap at least this much (Jaccard index) and hold at least SIMILAR_MIN_URLS.
SIMILAR_JACCARD = 0.8
SIMILAR_MIN_URLS = 5


def join_path(path) -> str:
    return PATH_SEP.join(path) if path else "(top level)"


# --------------------------------------------------------------------------- folders

@dataclass
class FolderInfo:
    id: str
    title: str
    path: Tuple[str, ...]  # including its own title
    parent_folder: Optional[str]
    top_level: str
    order: int
    direct_tabs: int = 0
    direct_splits: int = 0
    direct_subfolders: int = 0
    direct_other: int = 0
    total_other: int = 0  # easels, Arc documents and unknown items in the subtree
    total_tabs: int = 0
    total_folders: int = 1  # itself plus descendant folders
    max_depth: int = 0

    @property
    def depth(self) -> int:
        return len(self.path)


def count_tabs(sb: Sidebar, item_id: str) -> int:
    """Tabs in a subtree, including tabs inside split views and the item itself."""
    n = 1 if sb.kind(item_id) == KIND_TAB else 0
    return n + sum(1 for e in sb.walk(item_id) if e.kind == KIND_TAB)


def collect_folders(sb: Sidebar, root_id: Optional[str]) -> Dict[str, FolderInfo]:
    """Every folder below ``root_id`` in document order, with sizes."""
    infos: Dict[str, FolderInfo] = {}
    if root_id is None or root_id not in sb.items:
        return infos
    seen = {root_id}

    def visit(fid: str, parent: Optional[str], top: Optional[str], path: Tuple[str, ...]) -> FolderInfo:
        obj = sb.items[fid]
        title = folder_title(obj)
        info = FolderInfo(fid, title, path + (title,), parent, top or fid, len(infos))
        info.max_depth = info.depth
        infos[fid] = info
        for cid in sb.children(fid):
            if cid in seen or cid not in sb.items:
                continue
            seen.add(cid)
            kind = sb.kind(cid)
            if kind in FOLDER_KINDS:
                info.direct_subfolders += 1
                child = visit(cid, fid, info.top_level, info.path)
                info.total_tabs += child.total_tabs
                info.total_other += child.total_other
                info.total_folders += child.total_folders
                info.max_depth = max(info.max_depth, child.max_depth)
            elif kind == KIND_TAB:
                info.direct_tabs += 1
            elif kind == KIND_SPLIT:
                info.direct_splits += 1
                info.direct_tabs += count_tabs(sb, cid)
            else:
                info.direct_other += 1
        info.total_tabs += info.direct_tabs
        info.total_other += info.direct_other
        return info

    for cid in sb.children(root_id):
        if cid not in seen and sb.kind(cid) in FOLDER_KINDS:
            seen.add(cid)
            visit(cid, None, None, ())
    return infos


def match_folder_path(infos: Dict[str, FolderInfo], path: str) -> List[str]:
    """Folder ids whose full title path equals ``path`` (exact first, then ignoring case)."""
    want = split_folder_path(path)
    exact = [i.id for i in infos.values() if list(i.path) == want]
    if exact:
        return exact
    folded = [w.casefold() for w in want]
    return [i.id for i in infos.values() if [p.casefold() for p in i.path] == folded]


def is_inside(infos: Dict[str, FolderInfo], fid: str, ancestor: str) -> bool:
    cur = infos[fid].parent_folder
    while cur is not None:
        if cur == ancestor:
            return True
        cur = infos[cur].parent_folder
    return False


def subtree_url_set(sb: Sidebar, fid: str) -> FrozenSet[str]:
    return frozenset(U.normalize(tab_url(e.obj)) for e in sb.walk(fid) if e.kind == KIND_TAB and tab_url(e.obj))


def subtree_signature(sb: Sidebar, item_id: str, _seen=None) -> str:
    """Structural fingerprint: folder titles, normalized tab URLs and their order."""
    seen = _seen if _seen is not None else set()
    if item_id in seen or item_id not in sb.items:
        return ""
    seen.add(item_id)
    obj = sb.items[item_id]
    kind = sb.kind(item_id)
    if kind == KIND_TAB:
        return "T(%s)" % U.normalize(tab_url(obj))
    inner = ",".join(subtree_signature(sb, c, seen) for c in sb.children(item_id))
    if kind in FOLDER_KINDS:
        return "F(%s:%s)" % (folder_title(obj), inner)
    if kind == KIND_SPLIT:
        return "S(%s)" % inner
    return "X(%s:%s)" % (kind, inner)


# --------------------------------------------------------------------------- duplicates

@dataclass
class DuplicateTree:
    removed_id: str
    removed_path: Tuple[str, ...]
    kept_id: str
    kept_path: Tuple[str, ...]
    match: str  # "identical" or "contained" (same title, every URL also in the kept tree)
    tabs: int
    folders: int
    kept_tabs: int = 0
    kept_folders: int = 0


@dataclass
class SimilarTrees:
    a_id: str
    a_path: Tuple[str, ...]
    b_id: str
    b_path: Tuple[str, ...]
    jaccard: float


def find_duplicate_trees(sb: Sidebar, space: Space) -> List[DuplicateTree]:
    """Folder subtrees in the pinned area that add nothing: identical copies, or a folder
    whose URLs all appear in another, unrelated folder with the same title (e.g. the same
    bookmarks imported twice, once with an extra "Favorites bar" layer).

    Removing every reported tree keeps every URL somewhere in the space: each removal
    needs a target that is still alive at that point, so URLs only ever move to living trees.
    """
    infos = collect_folders(sb, space.pinned_id)
    if not infos:
        return []
    sets = {fid: subtree_url_set(sb, fid) for fid in infos}
    sigs = {fid: subtree_signature(sb, fid) for fid in infos}
    ranked = sorted(infos.values(), key=lambda i: (-i.total_tabs, i.order))
    removed: List[str] = []
    removed_set = set()
    result: List[DuplicateTree] = []

    def dead(fid: str) -> bool:
        if fid in removed_set:
            return True
        cur = infos[fid].parent_folder
        while cur is not None:
            if cur in removed_set:
                return True
            cur = infos[cur].parent_folder
        return False

    for cand in ranked:
        if not sets[cand.id] or dead(cand.id):
            continue
        for target in ranked:
            if target.id == cand.id or not sets[target.id] or dead(target.id):
                continue
            if is_inside(infos, cand.id, target.id) or is_inside(infos, target.id, cand.id):
                continue
            if sigs[cand.id] == sigs[target.id]:
                if target.order < cand.order:
                    match = "identical"
                    break
                continue
            if cand.title.casefold() != target.title.casefold():
                continue
            if not sets[cand.id] <= sets[target.id]:
                continue
            if sets[cand.id] == sets[target.id]:
                # Same URLs, different shape: keep the simpler tree (fewer folders), then the first.
                if (target.total_folders, target.order) > (cand.total_folders, cand.order):
                    continue
            match = "contained"
            break
        else:
            continue
        removed.append(cand.id)
        removed_set.add(cand.id)
        result.append(
            DuplicateTree(cand.id, cand.path, target.id, target.path, match, cand.total_tabs, cand.total_folders,
                          target.total_tabs, target.total_folders)
        )
    return result


def find_similar_trees(sb: Sidebar, space: Space, exclude: FrozenSet[str] = frozenset()) -> List[SimilarTrees]:
    """Unrelated folders with different titles but heavily overlapping URLs. Report only."""
    infos = collect_folders(sb, space.pinned_id)

    def excluded(fid: str) -> bool:
        return fid in exclude or any(is_inside(infos, fid, x) for x in exclude if x in infos)

    cands = [i for i in infos.values() if i.total_tabs >= SIMILAR_MIN_URLS and not excluded(i.id)]
    sets = {i.id: subtree_url_set(sb, i.id) for i in cands}
    out: List[SimilarTrees] = []
    for x in range(len(cands)):
        a = cands[x]
        for y in range(x + 1, len(cands)):
            b = cands[y]
            if a.title.casefold() == b.title.casefold():
                continue
            if is_inside(infos, a.id, b.id) or is_inside(infos, b.id, a.id):
                continue
            sa, sb_ = sets[a.id], sets[b.id]
            union = len(sa | sb_)
            if union < SIMILAR_MIN_URLS:
                continue
            jac = len(sa & sb_) / union
            if jac >= SIMILAR_JACCARD:
                out.append(SimilarTrees(a.id, a.path, b.id, b.path, round(jac, 3)))
    # Keep only the outermost pairs: drop a pair when both sides sit inside an already reported pair.
    kept: List[SimilarTrees] = []
    for pair in out:
        covered = any(
            (is_inside(infos, pair.a_id, k.a_id) or is_inside(infos, pair.a_id, k.b_id))
            and (is_inside(infos, pair.b_id, k.a_id) or is_inside(infos, pair.b_id, k.b_id))
            for k in kept
        )
        if not covered:
            kept.append(pair)
    return kept


@dataclass
class UrlLocation:
    item_id: str
    area: str
    path: Tuple[str, ...]


@dataclass
class DuplicateUrl:
    url: str
    count: int
    locations: List[UrlLocation]


@dataclass
class NonWebUrl:
    item_id: str
    url: str
    scheme: str
    area: str
    path: Tuple[str, ...]


def space_tab_entries(sb: Sidebar, space: Space):
    """(area, WalkEntry) for every tab of a space, pinned first, in sidebar order."""
    for area, root in (("pinned", space.pinned_id), ("open", space.unpinned_id)):
        for entry in sb.walk(root):
            if entry.kind == KIND_TAB:
                yield area, entry


def find_duplicate_urls(sb: Sidebar, space: Space) -> List[DuplicateUrl]:
    groups: Dict[str, List[UrlLocation]] = {}
    for area, entry in space_tab_entries(sb, space):
        url = tab_url(entry.obj)
        if not url:
            continue
        groups.setdefault(U.normalize(url), []).append(UrlLocation(entry.id, area, entry.path))
    return [DuplicateUrl(url, len(locs), locs) for url, locs in groups.items() if len(locs) > 1]


# --------------------------------------------------------------------------- prediction

@dataclass
class DiaPrediction:
    """What Dia's own Arc importer is expected to produce for one space, from observation."""

    profile_name: str
    pinned_groups: int  # one flat group per folder, nested or not
    pinned_groups_min: int  # folders that hold tabs directly (others may come out empty)
    nested_groups: int  # groups that were nested folders in Arc and lose their parent
    loose_pinned_tabs: int
    tabs_in_groups: int
    open_tabs_lost: int
    non_web_dropped: int  # pinned non-http(s) URLs (open ones are already counted as lost)
    unsupported_items: int
    wrapped_tabs_lost: int = 0  # pinned web tabs inside split views or tab groups (Dia drops them)


def predict_import(sb: Sidebar, space: Space) -> DiaPrediction:
    """Observed: one flat group per folder or tab group; open tabs, non-web URLs and the tabs
    inside pinned split views and tab groups are not imported (the last one seen in a real
    migration test). Whether a tab group whose tabs are dropped still shows up as an empty
    group is unknown, so tab groups only count towards the upper estimate."""
    infos = collect_folders(sb, space.pinned_id)
    wrapped = sb.wrapped_ids(space.pinned_id)
    all_pinned_tabs = [e for e in sb.walk(space.pinned_id) if e.kind == KIND_TAB]
    pinned_tabs = [e for e in all_pinned_tabs if e.id not in wrapped]
    open_tabs = [e for e in sb.walk(space.unpinned_id) if e.kind == KIND_TAB]
    unsupported = sum(
        1
        for root in (space.pinned_id, space.unpinned_id)
        for e in sb.walk(root)
        if is_unsupported_kind(e.kind)
    )
    return DiaPrediction(
        profile_name=space.title,
        pinned_groups=len(infos),
        pinned_groups_min=sum(
            1 for i in infos.values()
            if sb.kind(i.id) == KIND_FOLDER and i.id not in wrapped
            and any(sb.kind(c) == KIND_TAB for c in sb.children(i.id))
        ),
        nested_groups=sum(1 for i in infos.values() if i.parent_folder is not None),
        loose_pinned_tabs=sum(1 for e in pinned_tabs if not e.path),
        tabs_in_groups=sum(1 for e in pinned_tabs if e.path),
        open_tabs_lost=len(open_tabs),
        non_web_dropped=sum(1 for e in all_pinned_tabs if tab_url(e.obj) and not U.is_web(tab_url(e.obj))),
        unsupported_items=unsupported,
        wrapped_tabs_lost=sum(1 for e in all_pinned_tabs if e.id in wrapped and U.is_web(tab_url(e.obj))),
    )


# --------------------------------------------------------------------------- scan report

@dataclass
class TopFolder:
    id: str
    title: str
    tabs: int
    folders: int
    max_depth: int


@dataclass
class SpaceScan:
    id: str
    title: str
    profile: str
    pinned_tabs: int
    pinned_loose_tabs: int
    pinned_split_views: int
    folders: int
    top_level_folders: int
    nested_folders: int
    max_depth: int
    empty_folders: List[Tuple[str, ...]]
    folders_with_only_subfolders: int
    open_tabs: int
    open_split_views: int
    open_folders: int
    open_tab_groups: int
    open_tabs_in_groups: int
    pinned_tab_groups: int
    pinned_wrapped_tabs: int  # tabs inside pinned split views or tab groups: Dia drops them
    tabs_without_url: int
    unsupported: Dict[str, int]
    non_web: List[NonWebUrl]
    duplicate_trees: List[DuplicateTree]
    similar_trees: List[SimilarTrees]
    duplicate_urls: List[DuplicateUrl]
    top_folders: List[TopFolder]
    prediction: DiaPrediction

    @property
    def duplicate_url_extra_copies(self) -> int:
        return sum(d.count - 1 for d in self.duplicate_urls)


@dataclass
class FavoritesScan:
    container_id: str
    profile: str
    tabs: int
    urls: List[str]


@dataclass
class ScanReport:
    source: str
    sha256: Optional[str]
    sync_keys: List[str]
    spaces: List[SpaceScan]
    favorites: List[FavoritesScan]
    orphan_items: int
    orphan_tabs: int
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        for s_obj, s_dict in zip(self.spaces, data["spaces"]):
            s_dict["duplicate_url_extra_copies"] = s_obj.duplicate_url_extra_copies
        return data


def scan_space(sb: Sidebar, space: Space) -> SpaceScan:
    infos = collect_folders(sb, space.pinned_id)
    pinned = list(sb.walk(space.pinned_id))
    opened = list(sb.walk(space.unpinned_id))
    unsupported: Counter = Counter()
    for e in pinned + opened:
        if is_unsupported_kind(e.kind):
            unsupported[e.kind] += 1
    non_web = []
    for area, entries in (("pinned", pinned), ("open", opened)):
        for e in entries:
            url = tab_url(e.obj) if e.kind == KIND_TAB else None
            if url and not U.is_web(url):
                non_web.append(NonWebUrl(e.id, url, U.scheme_of(url) or "(none)", area, e.path))
    dups = find_duplicate_trees(sb, space)
    top = [i for i in infos.values() if i.parent_folder is None]
    return SpaceScan(
        id=space.id,
        title=space.title,
        profile=space.profile_label,
        pinned_tabs=sum(1 for e in pinned if e.kind == KIND_TAB),
        pinned_loose_tabs=sum(1 for e in pinned if e.kind == KIND_TAB and not e.path),
        pinned_split_views=sum(1 for e in pinned if e.kind == KIND_SPLIT),
        folders=len(infos),
        top_level_folders=len(top),
        nested_folders=len(infos) - len(top),
        max_depth=max((i.max_depth for i in infos.values()), default=0),
        empty_folders=[i.path for i in infos.values() if i.total_tabs == 0 and i.total_other == 0],
        folders_with_only_subfolders=sum(
            1 for i in infos.values() if i.direct_tabs == 0 and i.direct_other == 0 and i.direct_subfolders > 0
        ),
        open_tabs=sum(1 for e in opened if e.kind == KIND_TAB),
        open_split_views=sum(1 for e in opened if e.kind == KIND_SPLIT),
        open_folders=sum(1 for e in opened if e.kind == KIND_FOLDER),
        open_tab_groups=sum(1 for e in opened if e.kind == KIND_TAB_GROUP),
        open_tabs_in_groups=sum(1 for e in opened if e.kind == KIND_TAB and e.path),
        pinned_tab_groups=sum(1 for e in pinned if e.kind == KIND_TAB_GROUP),
        pinned_wrapped_tabs=len([i for i in sb.wrapped_ids(space.pinned_id) if sb.kind(i) == KIND_TAB]),
        tabs_without_url=sum(1 for e in pinned + opened if e.kind == KIND_TAB and not tab_url(e.obj)),
        unsupported=dict(unsupported),
        non_web=non_web,
        duplicate_trees=dups,
        similar_trees=find_similar_trees(sb, space, frozenset(d.removed_id for d in dups)),
        duplicate_urls=find_duplicate_urls(sb, space),
        top_folders=[TopFolder(i.id, i.title, i.total_tabs, i.total_folders, i.max_depth) for i in top],
        prediction=predict_import(sb, space),
    )


def scan(sb: Sidebar, source: str = "", sha256: Optional[str] = None) -> ScanReport:
    favs = []
    for fav in sb.favorites:
        tabs = [e for e in sb.walk(fav.container_id) if e.kind == KIND_TAB]
        favs.append(FavoritesScan(fav.container_id, fav.profile_label, len(tabs), [tab_url(e.obj) or "" for e in tabs]))
    where = sb.locate_all()
    orphans = [i for i, loc in where.items() if loc.area == "orphan"]
    return ScanReport(
        source=source,
        sha256=sha256,
        sync_keys=sb.sync_keys,
        spaces=[scan_space(sb, s) for s in sb.spaces],
        favorites=favs,
        orphan_items=len(orphans),
        orphan_tabs=sum(1 for i in orphans if sb.kind(i) == KIND_TAB),
        warnings=list(sb.warnings),
    )


# --------------------------------------------------------------------------- text output

def _n(count: int, word: str, plural: Optional[str] = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def render_scan(report: ScanReport) -> str:
    lines: List[str] = []
    add = lines.append
    add(f"Arc sidebar: {report.source}")
    if report.sync_keys:
        add(
            "Arc Sync: on (" + ", ".join(report.sync_keys) + " present). Keep Arc closed from swap until "
            "restore, or Sync may upload the modified sidebar."
        )
    else:
        add("Arc Sync: no sync state found in the file.")
    total_fav = sum(f.tabs for f in report.favorites)
    add(f"Favorites: {_n(total_fav, 'tab')} in {_n(len(report.favorites), 'container')}"
        + (" (" + ", ".join(f"{f.profile}: {f.tabs}" for f in report.favorites) + ")" if report.favorites else ""))
    if report.orphan_items:
        add(f"Unreachable items: {report.orphan_items} ({report.orphan_tabs} tabs) are not under any space; left untouched.")
    for idx, s in enumerate(report.spaces, 1):
        p = s.prediction
        add("")
        add(f'Space {idx}/{len(report.spaces)}: "{s.title}"  (Arc profile: {s.profile})')
        add(f"  Pinned     {_n(s.pinned_tabs, 'tab')}: {s.pinned_loose_tabs} loose, "
            f"{s.pinned_tabs - s.pinned_loose_tabs} in folders; {_n(s.pinned_split_views, 'split view')}")
        add(f"  Folders    {s.folders} total: {s.top_level_folders} top-level, {s.nested_folders} nested; "
            f"max depth {s.max_depth}; {len(s.empty_folders)} empty; "
            f"{s.folders_with_only_subfolders} hold only subfolders")
        add(f"  Open tabs  {_n(s.open_tabs, 'tab')} (Today/unpinned); {_n(s.open_split_views, 'split view')}"
            + (f"; {_n(s.open_tab_groups, 'tab group')} holding {_n(s.open_tabs_in_groups, 'tab')}"
               if s.open_tab_groups else "")
            + (f"; {_n(s.open_folders, 'folder')}" if s.open_folders else ""))
        if s.pinned_tab_groups:
            add(f"  Tab groups {s.pinned_tab_groups} in the pinned area (counted as folders above)")
        if s.pinned_wrapped_tabs:
            add(f"  Wrapped    {_n(s.pinned_wrapped_tabs, 'pinned tab')} inside split views or tab groups: Dia's "
                "importer drops these (seen in a real migration test); prepare unwraps them by default")
        if s.tabs_without_url:
            add(f"  No URL     {_n(s.tabs_without_url, 'tab')} without a saved URL; kept as they are")
        if s.non_web:
            by_scheme = Counter(n.scheme for n in s.non_web)
            add(f"  Non-web    {_n(len(s.non_web), 'URL')} (" + ", ".join(f"{k} {v}" for k, v in sorted(by_scheme.items()))
                + "); Dia drops these on import")
        if s.unsupported:
            add("  Other      " + ", ".join(f"{v} {k}" for k, v in sorted(s.unsupported.items()))
                + " (no Dia equivalent; left in place)")
        add(f"  Duplicates {_n(len(s.duplicate_trees), 'duplicate folder tree')}; "
            f"{_n(len(s.duplicate_urls), 'URL')} appear more than once "
            f"({_n(s.duplicate_url_extra_copies, 'extra copy', 'extra copies')})")
        for d in s.duplicate_trees:
            add(f"    - {join_path(d.removed_path)} [{_n(d.tabs, 'tab')}, {_n(d.folders, 'folder')}] "
                f"{'is identical to' if d.match == 'identical' else 'has all its URLs in'} "
                f"{join_path(d.kept_path)} [{_n(d.kept_tabs, 'tab')}, {_n(d.kept_folders, 'folder')}]")
        for sim in s.similar_trees:
            add(f"    ~ {join_path(sim.a_path)} and {join_path(sim.b_path)} overlap "
                f"{int(sim.jaccard * 100)}% (different names; not removed automatically)")
        add("  Dia's importer, as is:")
        flat = (f"{p.pinned_groups} flat pinned groups" if p.pinned_groups_min == p.pinned_groups
                else f"{p.pinned_groups_min} to {p.pinned_groups} flat pinned groups")
        add(f'    profile "{p.profile_name}" with {flat}'
            + (f" ({p.nested_groups} nested in Arc, named without their parent folders)" if p.nested_groups else "")
            + f", {_n(p.loose_pinned_tabs, 'loose pinned tab')}")
        losses = []
        if p.open_tabs_lost:
            losses.append(_n(p.open_tabs_lost, "open tab"))
        if p.wrapped_tabs_lost:
            losses.append(_n(p.wrapped_tabs_lost, "tab") + " in pinned split views and tab groups")
        if p.non_web_dropped:
            losses.append(_n(p.non_web_dropped, "pinned non-web URL"))
        add("    not imported: " + (", ".join(losses) if losses else "nothing expected"))
    if report.warnings:
        add("")
        add("Warnings:")
        lines.extend(f"  - {w}" for w in report.warnings)
    return "\n".join(lines)


def render_tree(sb: Sidebar, space: Space, max_items: int = 0) -> str:
    """Indented outline of a space: folders with tab counts, tabs with URLs."""
    lines: List[str] = [f'"{space.title}"']
    for area, root in (("Pinned", space.pinned_id), ("Open (Today)", space.unpinned_id)):
        lines.append(f"  {area}")
        shown = 0
        for e in sb.walk(root):
            pad = "    " + "  " * (len(e.path) + (1 if e.in_split else 0))
            if e.kind in FOLDER_KINDS:
                label = " tab group" if e.kind == KIND_TAB_GROUP else ""
                lines.append(f"{pad}[{folder_title(e.obj)}]{label} ({_n(count_tabs(sb, e.id), 'tab')})")
            elif e.kind == KIND_TAB:
                if max_items and shown >= max_items:
                    continue
                shown += 1
                lines.append(f"{pad}- {tab_url(e.obj) or '(no URL)'}")
            elif e.kind == KIND_SPLIT:
                lines.append(f"{pad}<split view>")
            else:
                lines.append(f"{pad}<{e.kind}: {e.obj.get('title') or ''}>")
        if max_items and shown >= max_items:
            lines.append("    ...")
    return "\n".join(lines)
