"""Reading and writing Arc's StorableSidebar.json.

The format is undocumented. What this module relies on (observed in real files):

* Top level: ``{"sidebar": {"containers": [...]}, "sidebarSyncState": ..., "firebaseSyncState": ..., "version": ...}``.
* One entry of ``containers`` is a dict with ``spaces``, ``items`` and ``topAppsContainerIDs``.
* ``items`` and ``spaces`` are alternating lists ``[id, object, id, object, ...]``.
* Items link to each other with ``parentID`` and an ordered ``childrenIds`` list.
* The kind of an item is the single key of its ``data`` dict (``tab``, ``list`` for a
  folder, ``tabGroup``, ``itemContainer``, ``splitView``, ``easel``, ``arcDocument``...).
* A ``tabGroup`` (seen among open tabs) holds tabs and keeps its name in
  ``data.tabGroup.title``; its own ``title`` is usually null. It is handled like a folder.
* Dia's importer drops the tabs inside a ``splitView`` or ``tabGroup`` in the pinned area
  (seen in a real migration test); these two kinds are the "wrappers".

Everything else in the file is carried through untouched.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from .errors import FormatError

APPLE_EPOCH_OFFSET = 978307200  # seconds from 1970-01-01 to 2001-01-01 (Arc's createdAt epoch)

KIND_TAB = "tab"
KIND_FOLDER = "list"
KIND_TAB_GROUP = "tabGroup"
KIND_CONTAINER = "itemContainer"
KIND_SPLIT = "splitView"
FOLDER_KINDS = (KIND_FOLDER, KIND_TAB_GROUP)
WRAPPER_KINDS = (KIND_SPLIT, KIND_TAB_GROUP)  # Dia drops the tabs inside these in the pinned area
KNOWN_UNSUPPORTED = ("easel", "arcDocument")
_KNOWN_ORDER = (KIND_TAB, KIND_FOLDER, KIND_TAB_GROUP, KIND_CONTAINER, KIND_SPLIT) + KNOWN_UNSUPPORTED

UNTITLED_FOLDER = "Untitled folder"
UNTITLED_TAB_GROUP = "Untitled tab group"
SYNC_KEYS = ("sidebarSyncState", "firebaseSyncState")


# --------------------------------------------------------------------------- item helpers

def item_kind(obj: dict) -> str:
    data = obj.get("data")
    if not isinstance(data, dict) or not data:
        return "unknown"
    for key in _KNOWN_ORDER:
        if key in data:
            return key
    return sorted(data)[0]


def is_unsupported_kind(kind: str) -> bool:
    """Kinds Dia has no equivalent for (easels, Arc documents, anything unknown)."""
    return kind not in (KIND_TAB, KIND_FOLDER, KIND_TAB_GROUP, KIND_CONTAINER, KIND_SPLIT)


def is_folder_kind(kind: str) -> bool:
    """Folders and tab groups: items that only group other items."""
    return kind in FOLDER_KINDS


def tab_url(obj: dict) -> Optional[str]:
    data = obj.get("data") or {}
    tab = data.get(KIND_TAB) if isinstance(data, dict) else None
    if isinstance(tab, dict):
        url = tab.get("savedURL")
        if isinstance(url, str):
            return url
    return None


def tab_title(obj: dict) -> str:
    title = obj.get("title")
    if isinstance(title, str) and title.strip():
        return title
    data = obj.get("data") or {}
    tab = data.get(KIND_TAB) if isinstance(data, dict) else None
    if isinstance(tab, dict):
        saved = tab.get("savedTitle")
        if isinstance(saved, str) and saved.strip():
            return saved
    return tab_url(obj) or "Untitled"


def folder_title(obj: dict) -> str:
    title = obj.get("title")
    if isinstance(title, str) and title.strip():
        return title
    data = obj.get("data") or {}
    group = data.get(KIND_TAB_GROUP) if isinstance(data, dict) else None
    if isinstance(group, dict):
        name = group.get("title")
        if isinstance(name, str) and name.strip():
            return name
        return UNTITLED_TAB_GROUP
    return UNTITLED_FOLDER


def set_folder_title(obj: dict, title: str) -> None:
    """Rename a folder or tab group (a tab group keeps its name in data.tabGroup.title)."""
    obj["title"] = title
    data = obj.get("data")
    if isinstance(data, dict) and isinstance(data.get(KIND_TAB_GROUP), dict):
        data[KIND_TAB_GROUP]["title"] = title


def item_label(obj: dict) -> str:
    kind = item_kind(obj)
    if kind == KIND_TAB:
        return tab_title(obj)
    if is_folder_kind(kind):
        return folder_title(obj)
    title = obj.get("title")
    return title if isinstance(title, str) and title else kind


def apple_to_unix(ts: Any) -> Optional[int]:
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None
    return int(ts + APPLE_EPOCH_OFFSET)


def unix_to_apple(ts: float) -> float:
    return float(ts) - APPLE_EPOCH_OFFSET


def new_item_id() -> str:
    """Arc ids are upper-case UUID strings."""
    return str(uuid.uuid4()).upper()


def profile_label(profile: Any) -> str:
    """Human label for an Arc profile key such as {"default": true} or {"custom": {"_0": {...}}}."""
    if profile is None:
        return "unknown"
    if isinstance(profile, dict):
        if "default" in profile:
            return "default"
        custom = profile.get("custom")
        if isinstance(custom, dict):
            inner = custom.get("_0")
            if isinstance(inner, dict):
                name = inner.get("directoryBasename") or inner.get("name")
                if name:
                    return str(name)
            return "custom"
        return str(next(iter(profile), "unknown"))
    return str(profile)


# --------------------------------------------------------------------------- alternating lists

def read_pairs(value: Any, what: str) -> List[Tuple[str, dict]]:
    """Parse an alternating ``[id, object, id, object, ...]`` list."""
    if not isinstance(value, list):
        raise FormatError(f"{what}: expected a list, got {type(value).__name__}")
    if len(value) % 2:
        raise FormatError(
            f"{what}: expected an alternating [id, object, ...] list of even length, got {len(value)} entries"
        )
    pairs = []
    for i in range(0, len(value), 2):
        key, obj = value[i], value[i + 1]
        if not isinstance(key, str) or not isinstance(obj, dict):
            raise FormatError(
                f"{what}: entry {i} should be an id string followed by an object, got "
                f"{type(key).__name__} then {type(obj).__name__}"
            )
        pairs.append((key, obj))
    return pairs


def write_pairs(pairs: List[Tuple[str, dict]]) -> list:
    out: list = []
    for key, obj in pairs:
        out.append(key)
        out.append(obj)
    return out


# --------------------------------------------------------------------------- data classes

@dataclass
class Space:
    id: str
    title: str  # display title; falls back to "Space N" when the space has none
    raw_title: Optional[str]
    profile: Any
    pinned_id: Optional[str]
    unpinned_id: Optional[str]
    obj: dict

    @property
    def profile_label(self) -> str:
        return profile_label(self.profile)


@dataclass
class FavoritesContainer:
    container_id: str
    profile: Any  # the profile key listed just before the container id, if any

    @property
    def profile_label(self) -> str:
        return profile_label(self.profile)


@dataclass
class WalkEntry:
    id: str
    obj: dict
    kind: str
    path: Tuple[str, ...]  # titles of the folders above this item, outermost first
    in_split: bool = False

    @property
    def depth(self) -> int:
        """Folder depth: a top-level folder has depth 1."""
        return len(self.path) + (1 if self.kind in FOLDER_KINDS else 0)


@dataclass
class Location:
    space_id: Optional[str]
    area: str  # "pinned", "open", "favorites", "container" or "orphan"
    path: Tuple[str, ...]
    in_split: bool = False


# --------------------------------------------------------------------------- the sidebar

class Sidebar:
    """A parsed StorableSidebar.json. Item objects are shared with ``raw``; call
    :meth:`serialize` after adding or removing items or spaces."""

    def __init__(self, raw: Any):
        if not isinstance(raw, dict):
            raise FormatError("top level: expected a JSON object")
        sidebar = raw.get("sidebar")
        if not isinstance(sidebar, dict) or not isinstance(sidebar.get("containers"), list):
            raise FormatError('missing "sidebar.containers"; is this an Arc StorableSidebar.json file?')
        main = None
        main_index = -1
        for idx, cont in enumerate(sidebar["containers"]):
            if isinstance(cont, dict) and "items" in cont and "spaces" in cont:
                main, main_index = cont, idx
                break
        if main is None:
            raise FormatError('no entry in "sidebar.containers" has both "spaces" and "items"')
        self.raw = raw
        self.container = main
        self.container_index = main_index
        self.warnings: List[str] = []

        self.items: Dict[str, dict] = {}
        for key, obj in read_pairs(main["items"], "sidebar.containers[%d].items" % main_index):
            if key in self.items:
                raise FormatError(f"items: duplicate id {key}")
            if obj.get("id") not in (None, key):
                self.warnings.append(f"item {key} has a different inner id {obj.get('id')!r}")
            self.items[key] = obj

        self.space_pairs = read_pairs(main["spaces"], "sidebar.containers[%d].spaces" % main_index)
        self.spaces: List[Space] = []
        for position, (key, obj) in enumerate(self.space_pairs, 1):
            raw_title = obj.get("title") if isinstance(obj.get("title"), str) else None
            if raw_title and raw_title.strip():
                title = raw_title
            else:
                title = f"Space {position}"
            pinned, unpinned = _space_container_ids(obj)
            if pinned is None or unpinned is None:
                self.warnings.append(f'space "{title}" does not list both pinned and unpinned containers')
            for cid in (pinned, unpinned):
                if cid is not None and cid not in self.items:
                    self.warnings.append(f'space "{title}" points to missing container {cid}')
            self.spaces.append(Space(key, title, raw_title, obj.get("profile"), pinned, unpinned, obj))

        self.favorites: List[FavoritesContainer] = []
        current_profile = None
        top_apps = main.get("topAppsContainerIDs") or []
        if not isinstance(top_apps, list):
            top_apps = []
        for entry in top_apps:
            if isinstance(entry, str):
                if entry in self.items:
                    self.favorites.append(FavoritesContainer(entry, current_profile))
                else:
                    self.warnings.append(f"favorites container {entry} is missing from items")
                current_profile = None
            else:
                current_profile = entry

    # -- loading / saving

    @classmethod
    def from_bytes(cls, data: bytes) -> "Sidebar":
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FormatError(f"not valid JSON: {exc}") from None
        return cls(raw)

    @classmethod
    def load(cls, path) -> "Sidebar":
        path = Path(path).expanduser()
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise FormatError(f"file not found: {path}") from None
        except OSError as exc:
            raise FormatError(f"cannot read {path}: {exc}") from None
        return cls.from_bytes(data)

    def serialize(self) -> dict:
        """Write items and spaces back into ``raw`` (alternating lists, original order) and return it."""
        self.container["items"] = write_pairs(list(self.items.items()))
        live = {s.id for s in self.spaces}
        self.space_pairs = [(k, o) for k, o in self.space_pairs if k in live]
        self.container["spaces"] = write_pairs(self.space_pairs)
        return self.raw

    def dumps(self) -> bytes:
        return json.dumps(self.serialize(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def clone(self) -> "Sidebar":
        # A JSON round trip is a faster deep copy for plain JSON data.
        return Sidebar(json.loads(json.dumps(self.serialize())))

    # -- queries

    @property
    def sync_keys(self) -> List[str]:
        return [k for k in SYNC_KEYS if k in self.raw]

    def get(self, item_id: str) -> Optional[dict]:
        return self.items.get(item_id)

    def kind(self, item_id: str) -> str:
        obj = self.items.get(item_id)
        return item_kind(obj) if obj is not None else "missing"

    def children(self, item_id: str) -> List[str]:
        obj = self.items.get(item_id)
        if obj is None:
            return []
        kids = obj.get("childrenIds") or []
        return [k for k in kids if isinstance(k, str)]

    def space(self, space_id: str) -> Optional[Space]:
        for s in self.spaces:
            if s.id == space_id:
                return s
        return None

    def walk(self, root_id: Optional[str]) -> Iterator[WalkEntry]:
        """Pre-order walk below ``root_id`` (not included), following ``childrenIds``.
        Missing ids are skipped and cycles are cut."""
        if root_id is None or root_id not in self.items:
            return
        seen = {root_id}
        stack: List[Tuple[str, Tuple[str, ...], bool]] = [
            (c, (), False) for c in reversed(self.children(root_id))
        ]
        while stack:
            item_id, path, in_split = stack.pop()
            if item_id in seen or item_id not in self.items:
                continue
            seen.add(item_id)
            obj = self.items[item_id]
            kind = item_kind(obj)
            yield WalkEntry(item_id, obj, kind, path, in_split)
            kids = self.children(item_id)
            if kind in FOLDER_KINDS:
                child_path = path + (folder_title(obj),)
                stack.extend((c, child_path, in_split) for c in reversed(kids))
            elif kids:
                stack.extend((c, path, in_split or kind == KIND_SPLIT) for c in reversed(kids))

    def wrapped_ids(self, root_id: Optional[str]) -> Set[str]:
        """Ids of the items below ``root_id`` that sit inside a split view or an Arc tab group,
        at any depth (the wrappers themselves are not included unless nested in another)."""
        out: Set[str] = set()
        for entry in self.walk(root_id):
            if entry.kind in WRAPPER_KINDS and entry.id not in out:
                out.update(e.id for e in self.walk(entry.id))
        return out

    def favorite_entries(self) -> Iterator[Tuple[FavoritesContainer, WalkEntry]]:
        for fav in self.favorites:
            for entry in self.walk(fav.container_id):
                yield fav, entry

    def locate_all(self) -> Dict[str, Location]:
        """Where every item lives. Items not reachable from a space or favorites are "orphan"."""
        where: Dict[str, Location] = {}
        for space in self.spaces:
            for area, root in (("pinned", space.pinned_id), ("open", space.unpinned_id)):
                if root is None or root not in self.items:
                    continue
                where.setdefault(root, Location(space.id, "container", ()))
                for entry in self.walk(root):
                    where.setdefault(entry.id, Location(space.id, area, entry.path, entry.in_split))
        for fav in self.favorites:
            where.setdefault(fav.container_id, Location(None, "container", ()))
            for entry in self.walk(fav.container_id):
                where.setdefault(entry.id, Location(None, "favorites", entry.path, entry.in_split))
        for item_id in self.items:
            if item_id not in where:
                where[item_id] = Location(None, "orphan", ())
        return where

    # -- mutation helpers (used by the transformer)

    def parent_of(self, item_id: str) -> Optional[str]:
        """The item whose childrenIds lists ``item_id``. Trusts parentID when it agrees,
        otherwise searches, so inconsistent files do not leave dangling references."""
        obj = self.items.get(item_id)
        parent_id = obj.get("parentID") if obj else None
        if isinstance(parent_id, str):
            parent = self.items.get(parent_id)
            if parent is not None and item_id in (parent.get("childrenIds") or []):
                return parent_id
        for pid, pobj in self.items.items():
            if item_id in (pobj.get("childrenIds") or []):
                return pid
        return None

    def detach(self, item_id: str) -> None:
        """Remove ``item_id`` from its parent's childrenIds (the item stays in ``items``)."""
        parent_id = self.parent_of(item_id)
        if parent_id is not None:
            parent = self.items[parent_id]
            parent["childrenIds"] = [c for c in parent.get("childrenIds") or [] if c != item_id]

    def attach(self, item_id: str, parent_id: str, index: Optional[int] = None) -> None:
        parent = self.items[parent_id]
        kids = list(parent.get("childrenIds") or [])
        if index is None:
            kids.append(item_id)
        else:
            kids.insert(index, item_id)
        parent["childrenIds"] = kids
        self.items[item_id]["parentID"] = parent_id

    def subtree_ids(self, item_id: str) -> List[str]:
        return [item_id] + [e.id for e in self.walk(item_id)]

    def delete_subtree(self, item_id: str) -> List[str]:
        """Detach ``item_id`` and drop it and its descendants from ``items``. Returns the dropped ids."""
        ids = self.subtree_ids(item_id)
        self.detach(item_id)
        for i in ids:
            self.items.pop(i, None)
        return ids


def _space_container_ids(obj: dict) -> Tuple[Optional[str], Optional[str]]:
    cids = obj.get("containerIDs") or []
    mapping: Dict[str, str] = {}
    if isinstance(cids, list):
        for i in range(0, len(cids) - 1, 2):
            key, value = cids[i], cids[i + 1]
            if isinstance(key, str) and isinstance(value, str):
                mapping[key] = value
    return mapping.get("pinned"), mapping.get("unpinned")
