"""Split views and Arc tab groups in the pinned area. In a real migration test Dia's importer
dropped the tabs inside them (docs.example.com/a and /b in Work, papers.example.org/1 and /2
in Research, from examples/synthetic-StorableSidebar.json), so prepare unwraps them by default."""

import json

import pytest

import fixturegen
from arc2dia2zen import bookmarks as B
from arc2dia2zen.analysis import predict_import, render_scan, scan
from arc2dia2zen.errors import PlanError
from arc2dia2zen.model import Sidebar, tab_url
from arc2dia2zen.plan import FolderOverride, plan_from_json
from arc2dia2zen.transform import FATE_SIDEBAR, transform
from arc2dia2zen.validate import structure_problems, validate
from conftest import find_folder, make_plan, space_by_title

WRAPPED_URLS = {
    "Work": ["https://docs.example.com/a", "https://docs.example.com/b"],
    "Research": ["https://papers.example.org/1", "https://papers.example.org/2"],
}


def pinned_kinds(sb, title):
    space = space_by_title(sb, title)
    return [(sb.kind(c), tab_url(sb.items[c])) for c in sb.children(space.pinned_id)]


def wrappers_left(sb):
    return [i for s in sb.spaces for i in sb.wrapped_ids(s.pinned_id)]


def test_the_example_file_has_the_wrappers_seen_in_the_real_test():
    raw = json.loads(fixturegen.EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert raw == fixturegen.rich_sidebar()
    sb = Sidebar(raw)
    for title, urls in WRAPPED_URLS.items():
        space = space_by_title(sb, title)
        got = sorted(tab_url(sb.items[i]) for i in sb.wrapped_ids(space.pinned_id) if sb.kind(i) == "tab")
        assert got == urls


def test_default_prepare_unwraps_in_place_and_in_order(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, folder_strategy="keep", dedupe_trees=False, open_tabs="leave",
                                       drop_empty_folders=False))
    sb = res.sidebar
    assert pinned_kinds(sb, "Work")[:4] == [
        ("tab", "https://wiki.example.com/"),
        ("tab", "https://docs.example.com/a"),
        ("tab", "https://docs.example.com/b"),
        ("list", None),  # "Projects" follows, as before
    ]
    assert pinned_kinds(sb, "Research")[:3] == [
        ("tab", "https://research.example.org/home"),
        ("tab", "https://papers.example.org/1"),
        ("tab", "https://papers.example.org/2"),
    ]
    assert wrappers_left(sb) == []
    assert not any(sb.kind(i) in ("splitView", "tabGroup") for s in sb.spaces for i in sb.subtree_ids(s.pinned_id))
    for title in WRAPPED_URLS:
        pinned = space_by_title(sb, title).pinned_id
        for c in sb.children(pinned):
            assert sb.items[c]["parentID"] == pinned
    assert structure_problems(sb) == set()
    assert validate(rich_sb, sb.dumps(), "", res.ledger, set()).ok

    by_title = {r.title: r for r in res.spaces}
    assert by_title["Work"].unwrapped == [{"kind": "splitView", "title": None, "path": "", "tabs": 2}]
    assert by_title["Research"].unwrapped == [{"kind": "tabGroup", "title": "Reading list", "path": "", "tabs": 2}]
    assert any(a.startswith("unwrapped 1 split view in the pinned area") for a in by_title["Work"].actions)
    entries = [e for e in res.ledger if e.url in WRAPPED_URLS["Work"] + WRAPPED_URLS["Research"]]
    assert len(entries) == 4 and all(e.fate == FATE_SIDEBAR for e in entries)
    assert all("moved out of a pinned split view or tab group" in e.dia_note for e in entries)
    assert by_title["Work"].after.wrapped_tabs_lost == 0 and by_title["Work"].before.wrapped_tabs_lost == 2


def test_keep_leaves_the_wrappers_and_says_dia_drops_them(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, folder_strategy="keep", pinned_wrappers="keep"))
    sb = res.sidebar
    assert [k for k, _ in pinned_kinds(sb, "Work")][:2] == ["tab", "splitView"]
    assert len([i for i in wrappers_left(sb) if sb.kind(i) == "tab"]) == 4
    notes = [e.dia_note for e in res.ledger if e.url in WRAPPED_URLS["Research"]]
    assert notes and all("inside a pinned split view or tab group: Dia's importer drops it" in n for n in notes)
    research = [r for r in res.spaces if r.title == "Research"][0]
    assert research.unwrapped == [] and research.after.wrapped_tabs_lost == 2
    v = validate(rich_sb, sb.dumps(), "", res.ledger, set())
    assert v.ok and v.stats["pinned_wrapped_tabs"] == 4
    assert any("4 tabs still sit inside split views or tab groups" in w for w in v.warnings)


def nested_sidebar():
    b = fixturegen.Builder(seed="nested-wrappers")
    _, pinned, _ = b.space("S")
    outer = b.folder(pinned, "Outer")
    b.tab(outer, "https://n.example.com/first")
    group = b.tab_group(outer, "Group")
    b.tab(group, "https://n.example.com/g1")
    b.split(group, ["https://n.example.com/s1", "https://n.example.com/s2"])
    b.tab(group, "https://n.example.com/g2")
    b.easel(group, "Board")
    b.split(outer, ["https://n.example.com/s3", "https://n.example.com/s4"])
    b.tab(outer, "https://n.example.com/last")
    empty = b.tab_group(pinned, "Empty group")
    b.tab(pinned, "https://n.example.com/loose")
    return Sidebar(b.build()), group, empty


def test_nested_wrappers_are_unwrapped_at_any_depth():
    sb, group, empty = nested_sidebar()
    assert predict_import(sb, sb.spaces[0]).wrapped_tabs_lost == 6
    res = transform(sb, make_plan(sb, folder_strategy="keep"))
    out = res.sidebar
    outer = find_folder(out, "Outer")[0]
    assert [tab_url(out.items[c]) or out.kind(c) for c in out.children(outer)] == [
        "https://n.example.com/first",
        "https://n.example.com/g1", "https://n.example.com/s1", "https://n.example.com/s2",
        "https://n.example.com/g2", "easel",
        "https://n.example.com/s3", "https://n.example.com/s4",
        "https://n.example.com/last",
    ]
    assert group not in out.items and empty not in out.items
    assert pinned_kinds(out, "S")[-1] == ("tab", "https://n.example.com/loose")
    assert structure_problems(out) == set()
    assert validate(sb, out.dumps(), "", res.ledger, set()).ok
    kinds = sorted((d["kind"], d["path"], d["tabs"]) for d in res.spaces[0].unwrapped)
    assert kinds == [("splitView", "Outer", 2), ("splitView", "Outer › Group", 2), ("tabGroup", "", 0),
                     ("tabGroup", "Outer", 2)]
    assert "their 6 tabs now sit where the wrappers were, in order" in res.spaces[0].actions[0]
    assert res.spaces[0].after.wrapped_tabs_lost == 0


@pytest.mark.parametrize("strategy", ["keep", "path-names", "merge-to-top"])
def test_every_strategy_sees_plain_folders_after_unwrapping(strategy):
    sb, _, _ = nested_sidebar()
    res = transform(sb, make_plan(sb, folder_strategy=strategy))
    out = res.sidebar
    assert wrappers_left(out) == []
    urls = sorted(tab_url(out.items[i]) for i in out.subtree_ids(out.spaces[0].pinned_id) if out.kind(i) == "tab")
    assert len(urls) == 9
    assert validate(sb, out.dumps(), "", res.ledger, set()).ok


def test_tab_groups_still_count_as_folders_for_to_bookmarks():
    """Exports run before the unwrap, so a pinned tab group keeps its name in bookmarks.html."""
    sb, _, _ = nested_sidebar()
    plan = make_plan(sb, folder_strategy="keep")
    plan.spaces[0].folder_overrides = [FolderOverride(strategy="to-bookmarks", path="Outer › Group")]
    res = transform(sb, plan)
    links = [(p, n.url) for p, n in B.iter_links(res.bookmarks)]
    assert (("S", "Outer", "Group"), "https://n.example.com/g1") in links
    assert (("S", "Outer", "Group"), "https://n.example.com/s2") in links
    out = res.sidebar
    assert [d["kind"] for d in res.spaces[0].unwrapped] == ["splitView", "tabGroup"]
    assert validate(sb, out.dumps(), B.render(res.bookmarks), res.ledger, set()).ok


def test_kept_open_split_views_are_flagged(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, open_tabs_split_views="keep"))
    assert any('1 split view kept in "Open tabs from Arc"' in w and "use flatten" in w for w in res.warnings)
    work = [r for r in res.spaces if r.title == "Work"][0]
    assert work.after.wrapped_tabs_lost == 2  # calendar and meet, now in the pinned area


def test_scan_says_dia_drops_wrapped_tabs(rich_sb):
    report = scan(rich_sb)
    by_title = {s.title: s for s in report.spaces}
    assert by_title["Work"].pinned_wrapped_tabs == 2 and by_title["Research"].pinned_wrapped_tabs == 2
    assert by_title["Personal"].pinned_wrapped_tabs == 0
    text = render_scan(report)
    assert text.count("Dia's importer drops these (seen in a real migration test)") == 2
    assert "not imported: 7 open tabs, 2 tabs in pinned split views and tab groups, 4 pinned non-web URLs" in text
    # Tab groups only count towards the upper estimate of groups.
    research = by_title["Research"].prediction
    assert research.pinned_groups == 8 and research.pinned_groups_min == 6


def _plan_json(sb, **extra):
    space = {"space_id": sb.spaces[0].id, **extra}
    return {"arc2dia2zen_plan_version": 1, "spaces": [space]}


def test_plan_file_option():
    sb, _, _ = nested_sidebar()
    assert plan_from_json(_plan_json(sb)).spaces[0].pinned_wrappers == "unwrap"  # plans from 0.1.2
    assert plan_from_json(_plan_json(sb, pinned_wrappers="keep")).spaces[0].pinned_wrappers == "keep"
    with pytest.raises(PlanError, match="'flatten' is not one of unwrap, keep"):
        plan_from_json(_plan_json(sb, pinned_wrappers="flatten"))


def test_spaces_missing_from_the_plan_keep_their_wrappers(rich_sb):
    plan = make_plan(rich_sb)
    plan.spaces = [sp for sp in plan.spaces if sp.title != "Work"]
    res = transform(rich_sb, plan)
    assert [k for k, _ in pinned_kinds(res.sidebar, "Work")][:2] == ["tab", "splitView"]
