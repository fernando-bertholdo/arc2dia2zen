"""A stand-in for Dia's "Import from Another Browser... > Arc", written independently of the
arc2dia2zen package so the eval does not grade the tool with its own parser.

Observed behaviour it copies: every Arc space becomes a NEW Dia profile (a clashing name gets
" 2", " 3"...); pinned tabs are imported, including those inside folders, which are flattened
into one group per folder; Favorites come along as pinned (isPinned) tabs; only http(s) URLs
survive; open ("Today", unpinned) tabs are NOT imported. Tabs without a URL are skipped. Tabs
inside a split view or an Arc tab group in the pinned area are NOT imported either (seen in a
real migration test with the synthetic example file).
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

ORDER = ("tab", "list", "tabGroup", "itemContainer", "splitView", "easel", "arcDocument")
WRAPPERS = ("splitView", "tabGroup")  # Dia drops the pinned tabs inside these


class ParseError(ValueError):
    pass


def _pairs(value: Any) -> List[Tuple[str, dict]]:
    if not isinstance(value, list) or len(value) % 2:
        raise ParseError("not an alternating [id, object, ...] list")
    out = []
    for i in range(0, len(value), 2):
        if isinstance(value[i], str) and isinstance(value[i + 1], dict):
            out.append((value[i], value[i + 1]))
    return out


def kind(obj: dict) -> str:
    data = obj.get("data")
    if not isinstance(data, dict) or not data:
        return "unknown"
    for k in ORDER:
        if k in data:
            return k
    return sorted(data)[0]


def tab_url(obj: dict) -> Optional[str]:
    tab = (obj.get("data") or {}).get("tab")
    if isinstance(tab, dict) and isinstance(tab.get("savedURL"), str):
        return tab["savedURL"]
    return None


def tab_title(obj: dict) -> str:
    if isinstance(obj.get("title"), str) and obj["title"].strip():
        return obj["title"]
    tab = (obj.get("data") or {}).get("tab") or {}
    if isinstance(tab.get("savedTitle"), str) and tab["savedTitle"].strip():
        return tab["savedTitle"]
    return tab_url(obj) or ""


def folder_name(obj: dict) -> str:
    if isinstance(obj.get("title"), str) and obj["title"].strip():
        return obj["title"]
    group = (obj.get("data") or {}).get("tabGroup")
    if isinstance(group, dict) and isinstance(group.get("title"), str) and group["title"].strip():
        return group["title"]
    return "Untitled"


def is_web(url: Optional[str]) -> bool:
    return bool(url) and url.split(":", 1)[0].strip().lower() in ("http", "https")


def norm(url: str) -> str:
    """Light normalisation for comparisons: lower-case scheme and host, "/" for an empty path."""
    if not is_web(url):
        return url
    scheme, sep, rest = url.strip().partition("://")
    if not sep:
        return url
    host, _, path = rest.partition("/")
    return f"{scheme.lower()}://{host.lower()}/{path}"


def profile_label(profile: Any) -> str:
    if isinstance(profile, dict):
        if "default" in profile:
            return "default"
        inner = (profile.get("custom") or {}).get("_0") if isinstance(profile.get("custom"), dict) else None
        if isinstance(inner, dict):
            return str(inner.get("directoryBasename") or inner.get("name") or "custom")
        return "custom"
    return str(profile)


class ArcData:
    """Just enough of StorableSidebar.json for an importer."""

    def __init__(self, raw: Any):
        try:
            main = next(c for c in raw["sidebar"]["containers"] if isinstance(c, dict) and "items" in c and "spaces" in c)
        except (KeyError, TypeError, StopIteration):
            raise ParseError("no sidebar container with spaces and items") from None
        self.items: Dict[str, dict] = dict(_pairs(main["items"]))
        self.spaces = []
        for pos, (sid, obj) in enumerate(_pairs(main["spaces"]), 1):
            ids = obj.get("containerIDs") or []
            cmap = {ids[i]: ids[i + 1] for i in range(0, len(ids) - 1, 2)
                    if isinstance(ids[i], str) and isinstance(ids[i + 1], str)}
            title = obj.get("title") if isinstance(obj.get("title"), str) and obj["title"].strip() else f"Space {pos}"
            self.spaces.append({"id": sid, "title": title, "profile": profile_label(obj.get("profile")),
                                "pinned": cmap.get("pinned"), "unpinned": cmap.get("unpinned")})
        self.favorites: List[Tuple[str, str]] = []
        current = None
        for entry in main.get("topAppsContainerIDs") or []:
            if isinstance(entry, str):
                self.favorites.append((profile_label(current) if current is not None else "default", entry))
                current = None
            else:
                current = entry

    def children(self, item_id: str) -> List[str]:
        obj = self.items.get(item_id) or {}
        return [c for c in obj.get("childrenIds") or [] if isinstance(c, str) and c in self.items]

    def walk(self, root: Optional[str]) -> Iterator[Tuple[str, dict, Optional[str]]]:
        """(id, item, id of the nearest enclosing folder or tab group) in sidebar order."""
        if not root or root not in self.items:
            return
        seen = {root}
        stack = [(c, None) for c in reversed(self.children(root))]
        while stack:
            iid, folder = stack.pop()
            if iid in seen:
                continue
            seen.add(iid)
            obj = self.items[iid]
            yield iid, obj, folder
            k = kind(obj)
            inner = iid if k in ("list", "tabGroup") else folder
            stack.extend((c, inner) for c in reversed(self.children(iid)))

    def wrapped(self, root: Optional[str]) -> set:
        """Ids below ``root`` that sit inside a split view or a tab group, at any depth."""
        out: set = set()
        if not root or root not in self.items:
            return out
        seen = {root}
        stack = [(c, False) for c in reversed(self.children(root))]
        while stack:
            iid, inside = stack.pop()
            if iid in seen:
                continue
            seen.add(iid)
            if inside:
                out.add(iid)
            inner = inside or kind(self.items[iid]) in WRAPPERS
            stack.extend((c, inner) for c in reversed(self.children(iid)))
        return out

    def web_urls(self, root: Optional[str]) -> List[str]:
        return [tab_url(o) for _, o, _ in self.walk(root) if kind(o) == "tab" and is_web(tab_url(o))]


def unique_name(base: str, taken: set) -> str:
    name, n = base, 2
    while name in taken:
        name = f"{base} {n}"
        n += 1
    return name


def simulate_import(raw: Any, existing_names: List[str]) -> List[dict]:
    """The profiles one import adds to Dia."""
    arc = ArcData(raw)
    taken = set(existing_names)
    favs_by_profile: Dict[str, List[dict]] = {}
    for label, cid in arc.favorites:
        for _, obj, _ in arc.walk(cid):
            if kind(obj) == "tab" and is_web(tab_url(obj)):
                favs_by_profile.setdefault(label, []).append({"url": tab_url(obj), "title": tab_title(obj), "pinned": True})
    created = []
    for space in arc.spaces:
        name = unique_name(space["title"], taken)
        taken.add(name)
        tabs = list(favs_by_profile.get(space["profile"], []))
        groups: Dict[str, dict] = {}
        order: List[str] = []
        dropped = arc.wrapped(space["pinned"])
        for iid, obj, folder in arc.walk(space["pinned"]):
            if kind(obj) != "tab" or not is_web(tab_url(obj)) or iid in dropped:
                continue
            tabs.append({"url": tab_url(obj), "title": tab_title(obj), "pinned": False})
            if folder is not None:
                if folder not in groups:
                    groups[folder] = {"name": folder_name(arc.items[folder]), "urls": []}
                    order.append(folder)
                groups[folder]["urls"].append(tab_url(obj))
        created.append({
            "name": name,
            "origin": "import",
            "space_id": space["id"],
            "space_title": space["title"],
            "tabs": tabs,
            "groups": [groups[g] for g in order],
        })
    return created
