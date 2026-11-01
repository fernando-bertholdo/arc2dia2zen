"""Checks run by ``prepare`` on the files it wrote, after re-reading them from disk."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Set

from . import bookmarks as B
from . import urls as U
from .errors import FormatError
from .model import KIND_CONTAINER, KIND_TAB, Sidebar, read_pairs, tab_url
from .transform import FATE_BOOKMARKS, FATE_DEDUPED, FATE_DROPPED, FATE_SIDEBAR, LedgerEntry


@dataclass
class Validation:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "stats": self.stats}


def shape_problems(raw: dict) -> List[str]:
    """The alternating [id, object, ...] shape of items and spaces, and id agreement."""
    problems = []
    try:
        containers = raw["sidebar"]["containers"]
    except (KeyError, TypeError):
        return ["missing sidebar.containers"]
    for idx, cont in enumerate(containers):
        if not isinstance(cont, dict) or "items" not in cont:
            continue
        for key in ("items", "spaces"):
            try:
                pairs = read_pairs(cont.get(key), f"containers[{idx}].{key}")
            except FormatError as exc:
                problems.append(str(exc))
                continue
            for item_id, obj in pairs:
                if "id" in obj and obj["id"] != item_id:
                    problems.append(f"containers[{idx}].{key}: key {item_id} holds an object with id {obj['id']}")
    return problems


def structure_problems(sb: Sidebar) -> Set[str]:
    """Dangling ids, parent/child disagreements, items listed twice and unreachable items."""
    problems: Set[str] = set()
    listed_by: Dict[str, List[str]] = {}
    for item_id, obj in sb.items.items():
        kids = obj.get("childrenIds") or []
        for child in kids:
            if not isinstance(child, str) or child not in sb.items:
                problems.add(f"dangling child id {child} in {item_id}")
                continue
            listed_by.setdefault(child, []).append(item_id)
            if sb.items[child].get("parentID") != item_id:
                problems.add(f"{child} is listed by {item_id} but its parentID is {sb.items[child].get('parentID')}")
        parent = obj.get("parentID")
        if isinstance(parent, str) and parent not in sb.items:
            problems.add(f"dangling parentID {parent} in {item_id}")
    for child, parents in listed_by.items():
        if len(parents) > 1:
            problems.add(f"{child} is listed as a child of several items: {', '.join(sorted(parents))}")
    for space in sb.spaces:
        for label, cid in (("pinned", space.pinned_id), ("unpinned", space.unpinned_id)):
            if cid is None:
                problems.add(f'space {space.id} has no {label} container id')
            elif cid not in sb.items:
                problems.add(f"space {space.id} {label} container {cid} is missing")
            elif sb.kind(cid) != KIND_CONTAINER:
                problems.add(f"space {space.id} {label} container {cid} is a {sb.kind(cid)}, not an itemContainer")
    for item_id, loc in sb.locate_all().items():
        if loc.area == "orphan":
            problems.add(f"item {item_id} is not reachable from any space or favorites container")
    return problems


def _without_items_and_spaces(sb: Sidebar) -> dict:
    raw = copy.deepcopy(sb.raw)
    cont = raw["sidebar"]["containers"][sb.container_index]
    cont.pop("items", None)
    cont.pop("spaces", None)
    return raw


def validate(source: Sidebar, output_bytes: bytes, html_text: str, ledger: List[LedgerEntry],
             excluded_space_ids: Set[str]) -> Validation:
    v = Validation()

    # 1. The output parses and keeps the alternating shape.
    try:
        out = Sidebar.from_bytes(output_bytes)
    except FormatError as exc:
        v.errors.append(f"cleaned file does not parse: {exc}")
        return v
    v.errors.extend(shape_problems(out.raw))

    # 2. Nothing outside items/spaces changed (sync state, version, other containers, favorites list...).
    if _without_items_and_spaces(out) != _without_items_and_spaces(source):
        v.errors.append("parts of the file other than items and spaces changed")

    # 3. Space list: same spaces, same order, minus the excluded ones.
    want = [s.id for s in source.spaces if s.id not in excluded_space_ids]
    got = [s.id for s in out.spaces]
    if want != got:
        v.errors.append(f"space list changed unexpectedly: expected {want}, got {got}")

    # 4. Id integrity: no new structural problems compared with the original file.
    before = structure_problems(source)
    after = structure_problems(out)
    for p in sorted(after - before):
        v.errors.append(p)
    if before:
        v.warnings.append(f"{len(before)} structural oddities already present in the original file were left as they are")

    # 5. URL accounting.
    src_tabs = {i for i in source.items if source.kind(i) == KIND_TAB}
    ledger_ids = [e.item_id for e in ledger]
    if len(set(ledger_ids)) != len(ledger_ids):
        v.errors.append("ledger lists some tabs twice")
    if set(ledger_ids) != src_tabs:
        missing = src_tabs - set(ledger_ids)
        v.errors.append(f"{len(missing)} original tabs have no ledger entry")

    try:
        html_nodes = B.parse(html_text) if html_text else []
    except Exception as exc:  # html.parser is lenient; this is defensive
        v.errors.append(f"bookmarks.html does not parse: {exc}")
        html_nodes = []
    html_urls = Counter(node.url for _, node in B.iter_links(html_nodes))
    html_norm = {U.normalize(u) for u in html_urls}

    out_where = out.locate_all()
    out_space_urls: Dict[str, Set[str]] = {}
    for item_id, loc in out_where.items():
        if out.kind(item_id) == KIND_TAB and loc.space_id:
            out_space_urls.setdefault(loc.space_id, set()).add(U.normalize(tab_url(out.items[item_id])))

    expected_html: Counter = Counter()
    for e in ledger:
        if e.fate == FATE_SIDEBAR:
            obj = out.items.get(e.item_id)
            if obj is None or out.kind(e.item_id) != KIND_TAB:
                v.errors.append(f"tab {e.item_id} ({e.url}) should be in the sidebar but is missing")
            elif (tab_url(obj) or "") != e.url:
                v.errors.append(f"tab {e.item_id} changed URL from {e.url} to {tab_url(obj)}")
            elif out_where[e.item_id].area == "orphan" and e.origin != "orphan":
                v.errors.append(f"tab {e.item_id} ({e.url}) is no longer reachable from its space")
        elif e.fate == FATE_BOOKMARKS:
            expected_html[e.url] += 1
            if e.item_id in out.items:
                v.errors.append(f"tab {e.item_id} was exported to bookmarks but is still in the sidebar")
        elif e.fate == FATE_DEDUPED:
            norm = U.normalize(e.url)
            if not e.url:
                v.errors.append(f"tab {e.item_id} has no URL but was removed as a duplicate")
                continue
            if e.item_id in out.items:
                v.errors.append(f"tab {e.item_id} was marked as a duplicate but is still in the sidebar")
            if norm not in out_space_urls.get(e.space_id or "", set()) and norm not in html_norm:
                v.errors.append(f"duplicate {e.url} was removed but no other copy survives")
        elif e.fate == FATE_DROPPED:
            if not e.reason:
                v.errors.append(f"tab {e.item_id} was dropped without a reason")
            if e.item_id in out.items:
                v.errors.append(f"tab {e.item_id} was marked as dropped but is still in the sidebar")
        else:
            v.errors.append(f"tab {e.item_id} has unknown fate {e.fate!r}")

    if html_urls != expected_html:
        extra = html_urls - expected_html
        missing = expected_html - html_urls
        if missing:
            v.errors.append(f"{sum(missing.values())} bookmarks are missing from bookmarks.html")
        if extra:
            v.errors.append(f"bookmarks.html has {sum(extra.values())} unexpected links")

    # 6. Nothing invented: every tab in the output existed in the original with the same URL.
    for item_id in out.items:
        if out.kind(item_id) != KIND_TAB:
            continue
        if item_id not in source.items:
            v.errors.append(f"tab {item_id} is new; the tool should never create tabs")
        elif tab_url(source.items[item_id]) != tab_url(out.items[item_id]):
            v.errors.append(f"tab {item_id} changed URL")

    # 7. Tabs Dia's importer is known to drop: those inside pinned split views and tab groups.
    wrapped = 0
    for space in out.spaces:
        wrapped += sum(1 for i in out.wrapped_ids(space.pinned_id) if out.kind(i) == KIND_TAB)
    if wrapped:
        v.warnings.append(
            ("1 tab still sits" if wrapped == 1 else f"{wrapped} tabs still sit")
            + " inside split views or tab groups in the pinned area; Dia's importer drops these "
            "(seen in a real migration test)"
        )

    counts = Counter(e.fate for e in ledger)
    v.stats = {
        "original_tabs": len(src_tabs),
        "in_sidebar": counts.get(FATE_SIDEBAR, 0),
        "in_bookmarks": counts.get(FATE_BOOKMARKS, 0),
        "deduplicated": counts.get(FATE_DEDUPED, 0),
        "dropped": counts.get(FATE_DROPPED, 0),
        "bookmarks_in_html": sum(html_urls.values()),
        "output_items": len(out.items),
        "original_items": len(source.items),
        "pinned_wrapped_tabs": wrapped,
    }
    return v
