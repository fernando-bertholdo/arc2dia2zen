"""What Zen Browser's own Arc importer makes of a sidebar, ported from its source.

Zen (1.24 and later) imports Arc in ``src/zen/migration/ZenMigrationUtils.sys.mjs`` of
zen-browser/desktop. This module follows ``parseArcSidebar`` and ``#applyItems`` as they are on
the dev branch at 147e6ebb2 (2026-10-31), so ``prepare --target zen`` can say what Zen will do:

* Only the pinned area of each space is read. Open (Today) tabs are not imported.
* A tab is kept when its URL is http, https, file or ftp; other tabs are skipped.
* Folders (``list``) are kept and split views are replaced by their tabs. Tab groups and every
  other kind are dropped together with what they hold. Folders that end up empty are skipped.
* Folders deeper than the pref ``zen.folders.max-subfolders`` (5) are merged into their parent.
* Arc's top apps become Essentials; this module does not cover them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from . import urls as U
from .model import KIND_TAB, KIND_TAB_GROUP, Sidebar, Space, is_unsupported_kind
from .plan import ZEN_MAX_SUBFOLDERS

ZEN_SCHEMES = ("http", "https", "file", "ftp")
MAX_TREE_DEPTH = 32  # kMaxTreeDepth: the parser stops below this


def zen_url(url: Any) -> Optional[str]:
    """The URL when Zen's importer accepts it (``normalizeUrl``), else None."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    scheme = U.scheme_of(url)
    if scheme not in ZEN_SCHEMES:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if scheme != "file" and not parts.netloc:
        return None
    return url


def _truthy(value: Any) -> bool:
    """JavaScript truthiness, for the ``if (data.tab)`` tests of the parser."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return False
    return value != ""


@dataclass
class ZenItem:
    """One entry of Zen's import plan: a pinned tab or a folder."""

    type: str  # "tab" or "folder"
    title: str
    url: str = ""
    item_id: str = ""
    items: List["ZenItem"] = field(default_factory=list)

    def to_json(self) -> dict:
        if self.type == "tab":
            return {"type": "tab", "url": self.url, "title": self.title}
        return {"type": "folder", "title": self.title, "items": [i.to_json() for i in self.items]}


@dataclass
class _Lost:
    tab_group_tabs: int = 0
    other_tabs: int = 0
    unimportable_urls: int = 0


def _children_of(sb: Sidebar, parent_id: str) -> List[str]:
    parent = sb.items.get(parent_id)
    ids = parent.get("childrenIds") if isinstance(parent, dict) else None
    kids = [i for i in ids or [] if isinstance(i, str) and i in sb.items] if isinstance(ids, list) else []
    if not kids:
        kids = [i for i, obj in sb.items.items() if isinstance(obj, dict) and obj.get("parentID") == parent_id]
    return kids


def _tabs_below(sb: Sidebar, item_id: str) -> int:
    return sum(1 for e in sb.walk(item_id) if e.kind == KIND_TAB)


def _convert(sb: Sidebar, parent_id: str, depth: int, lost: _Lost) -> List[ZenItem]:
    result: List[ZenItem] = []
    if depth > MAX_TREE_DEPTH:
        return result
    for cid in _children_of(sb, parent_id):
        obj = sb.items[cid]
        data = obj.get("data")
        data = data if isinstance(data, dict) else {}
        if _truthy(data.get("tab")):
            tab = data["tab"] if isinstance(data["tab"], dict) else {}
            url = zen_url(tab.get("savedURL"))
            if url:
                title = tab.get("savedTitle") or obj.get("title") or ""
                result.append(ZenItem("tab", title if isinstance(title, str) else "", url, cid))
            else:
                lost.unimportable_urls += 1
        elif _truthy(data.get("list")):
            inner = _convert(sb, cid, depth + 1, lost)
            if inner:
                title = obj.get("title") or ""
                result.append(ZenItem("folder", title if isinstance(title, str) else "", item_id=cid, items=inner))
        elif _truthy(data.get("splitView")):
            result.extend(_convert(sb, cid, depth + 1, lost))
        elif _truthy(data.get(KIND_TAB_GROUP)):
            lost.tab_group_tabs += _tabs_below(sb, cid)
        else:
            lost.other_tabs += _tabs_below(sb, cid)
    return result


@dataclass
class _Applied:
    folders: int = 0
    tabs: int = 0
    loose_tabs: int = 0
    flattened_folders: int = 0


def _apply(items: List[ZenItem], depth: int, in_folder: bool, max_sub: Optional[int], out: _Applied) -> List[ZenItem]:
    built: List[ZenItem] = []
    for it in items:
        if it.type == "tab":
            out.tabs += 1
            if not in_folder:
                out.loose_tabs += 1
            built.append(it)
        elif max_sub is not None and depth >= max_sub:
            out.flattened_folders += 1
            built.extend(_apply(it.items, depth, in_folder, max_sub, out))
        else:
            out.folders += 1
            built.append(ZenItem("folder", it.title, item_id=it.item_id,
                                 items=_apply(it.items, depth + 1, True, max_sub, out)))
    return built


def parse_space(sb: Sidebar, space: Space) -> List[ZenItem]:
    """The items ``parseArcSidebar`` reads for one space, before the depth limit."""
    if not isinstance(space.pinned_id, str):
        return []
    return _convert(sb, space.pinned_id, 0, _Lost())


def import_tree(sb: Sidebar, space: Space, max_subfolders: Optional[int] = ZEN_MAX_SUBFOLDERS) -> List[ZenItem]:
    """The pinned tabs and folders Zen creates for one space, after the depth limit."""
    return _apply(parse_space(sb, space), 0, False, max_subfolders, _Applied())


@dataclass
class ZenPrediction:
    """What Zen's Arc importer is expected to produce for one space (from its source code)."""

    space_title: str
    folders: int  # pinned folders Zen creates
    pinned_tabs: int  # pinned tabs Zen creates, loose or in folders
    loose_pinned_tabs: int
    tabs_in_folders: int
    flattened_folders: int  # folders deeper than the limit, merged into their parent
    max_subfolders: Optional[int]
    open_tabs_lost: int
    tab_group_tabs_lost: int  # pinned tabs inside Arc tab groups
    other_tabs_lost: int  # pinned tabs inside other kinds Zen does not read
    unimportable_urls: int  # pinned tabs without a URL Zen accepts
    unsupported_items: int  # easels, Arc documents and unknown kinds, pinned or open


def predict_zen_import(sb: Sidebar, space: Space,
                       max_subfolders: Optional[int] = ZEN_MAX_SUBFOLDERS) -> ZenPrediction:
    lost = _Lost()
    parsed = _convert(sb, space.pinned_id, 0, lost) if isinstance(space.pinned_id, str) else []
    applied = _Applied()
    _apply(parsed, 0, False, max_subfolders, applied)
    unsupported = sum(
        1
        for root in (space.pinned_id, space.unpinned_id)
        for e in sb.walk(root)
        if is_unsupported_kind(e.kind)
    )
    return ZenPrediction(
        space_title=space.title,
        folders=applied.folders,
        pinned_tabs=applied.tabs,
        loose_pinned_tabs=applied.loose_tabs,
        tabs_in_folders=applied.tabs - applied.loose_tabs,
        flattened_folders=applied.flattened_folders,
        max_subfolders=max_subfolders,
        open_tabs_lost=sum(1 for e in sb.walk(space.unpinned_id) if e.kind == KIND_TAB),
        tab_group_tabs_lost=lost.tab_group_tabs,
        other_tabs_lost=lost.other_tabs,
        unimportable_urls=lost.unimportable_urls,
        unsupported_items=unsupported,
    )


def parse_sidebar_json(sb: Sidebar) -> Dict[str, Any]:
    """Each space's items as ``parseArcSidebar`` returns them (titles and items only), for
    comparing with the real importer."""
    return {"spaces": [{"title": s.title, "items": [i.to_json() for i in parse_space(sb, s)]} for s in sb.spaces]}


def ids_in_tab_groups(sb: Sidebar, root_id: Optional[str]) -> set:
    """Ids of the items below ``root_id`` that sit inside an Arc tab group, at any depth."""
    out: set = set()
    for entry in sb.walk(root_id):
        if entry.kind == KIND_TAB_GROUP and entry.id not in out:
            out.update(e.id for e in sb.walk(entry.id))
    return out
