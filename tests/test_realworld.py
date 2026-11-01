"""Regression tests for shapes seen in a real StorableSidebar.json: Arc tab groups among open
tabs, a tab group in the pinned area, tabs without a saved URL, and same-named folder trees
exported to bookmarks.html. The data is synthetic (see fixturegen.research_space)."""

import json

import fixturegen
from arc2dia2zen import bookmarks as B
from arc2dia2zen.analysis import find_duplicate_trees, scan
from arc2dia2zen.cli import main
from arc2dia2zen.model import Sidebar, folder_title, tab_url
from arc2dia2zen.plan import FolderOverride
from arc2dia2zen.transform import FATE_DEDUPED, FATE_DROPPED, FATE_SIDEBAR, transform
from arc2dia2zen.validate import structure_problems, validate
from conftest import find_folder, make_plan, pinned_children, space_by_title

REAL_RUN = ["--override", "Imported From Edge=to-bookmarks", "--open-tabs", "pinned-folder",
            "--open-tabs-folder", "Abas abertas do Arc"]


def research(sb):
    return space_by_title(sb, "Research")


def open_folder(sb, name="Open tabs from Arc"):
    return find_folder(sb, name, "Research")[0]


def test_scan_sees_tab_groups_and_tabs_without_url(rich_sb):
    s = [x for x in scan(rich_sb).spaces if x.title == "Research"][0]
    assert s.open_tabs == 7 and s.open_tab_groups == 2 and s.open_tabs_in_groups == 5
    assert s.pinned_tab_groups == 1
    assert s.tabs_without_url == 4
    assert "tabGroup" not in s.unsupported
    assert s.prediction.open_tabs_lost == 7


def test_tab_groups_are_flattened_into_the_open_tabs_folder(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb))
    sb = res.sidebar
    space = research(sb)
    fid = open_folder(sb)
    assert [tab_url(sb.items[c]) for c in sb.children(fid)] == [
        "https://inbox.example.org/",
        "https://flights.example.com/",
        "https://hotels.example.com/",
        None,  # the URL-less "New Tab" from the group moves too
        "https://shop.example.com/cart",
        "https://compare.example.com/",
        "file:///Users/example/notes.txt",
    ]
    assert sb.children(space.unpinned_id) == []
    assert not any(sb.kind(i) == "tabGroup" and sb.items[i].get("parentID") == space.unpinned_id for i in sb.items)
    after = [r for r in res.spaces if r.title == "Research"][0]
    assert after.after.open_tabs_lost == 0
    assert any("2 tab groups flattened" in a for a in after.actions)
    assert not any("unsupported" in a for a in after.actions)
    assert structure_problems(sb) == set()


def test_tab_groups_as_subfolders(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, open_tabs_tab_groups="subfolders"))
    sb = res.sidebar
    fid = open_folder(sb)
    kids = sb.children(fid)
    assert [sb.kind(c) for c in kids] == ["tab", "list", "list", "tab"]
    trip, shop = kids[1], kids[2]
    assert sb.items[trip]["title"] == "Trip planning" and sb.items[shop]["title"] == "Shopping"
    assert sb.items[trip]["parentID"] == fid
    assert [tab_url(sb.items[c]) for c in sb.children(shop)] == [
        "https://shop.example.com/cart", "https://compare.example.com/",
    ]
    assert len(sb.children(trip)) == 3
    assert structure_problems(sb) == set()


def test_tab_groups_to_bookmarks(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, open_tabs="bookmarks", open_tabs_tab_groups="subfolders"))
    links = [(p, n.url) for p, n in B.iter_links(res.bookmarks) if p[0] == "Research"]
    assert (("Research", "Open tabs from Arc", "Trip planning"), "https://hotels.example.com/") in links
    assert (("Research", "Open tabs from Arc"), "https://inbox.example.org/") in links
    # The URL-less tab cannot be a bookmark, so it stays among the open tabs.
    space = research(res.sidebar)
    left = res.sidebar.children(space.unpinned_id)
    assert len(left) == 1 and tab_url(res.sidebar.items[left[0]]) is None
    v = validate(rich_sb, res.sidebar.dumps(), B.render(res.bookmarks), res.ledger, set())
    assert v.ok, v.errors


def nested_group_sidebar():
    b = fixturegen.Builder(seed="pinned-group")
    _, pinned, _ = b.space("S")
    outer = b.folder(pinned, "Outer")
    group = b.tab_group(outer, "Inner group")
    b.tab(group, "https://group.example.com/1")
    b.tab(group, "https://group.example.com/2")
    return Sidebar(b.build()), group


def test_pinned_tab_group_counts_as_a_folder():
    sb, group = nested_group_sidebar()
    s = scan(sb).spaces[0]
    assert s.folders == 2 and s.nested_folders == 1 and s.prediction.pinned_groups == 2


def test_pinned_tab_group_with_path_names():
    sb, group = nested_group_sidebar()
    out = transform(sb, make_plan(sb, folder_strategy="path-names", pinned_wrappers="keep")).sidebar
    obj = out.items[group]
    assert obj["title"] == "Outer › Inner group"
    assert obj["data"]["tabGroup"]["title"] == "Outer › Inner group"
    assert [label for _, label in pinned_children(out, "S")] == ["Outer › Inner group"]


def test_pinned_tab_group_with_merge_and_bookmarks():
    sb, group = nested_group_sidebar()
    out = transform(sb, make_plan(sb, folder_strategy="merge-to-top")).sidebar
    outer = find_folder(out, "Outer")[0]
    assert group not in out.items and len(out.children(outer)) == 2
    res = transform(sb, make_plan(sb, folder_strategy="to-bookmarks"))
    links = [(p, n.url) for p, n in B.iter_links(res.bookmarks)]
    assert links[0] == (("S", "Outer", "Inner group"), "https://group.example.com/1")


def test_real_run_command_validates(tmp_path, sidebar_file, capsys):
    """The coordinator's command on the owner's file, replayed on the synthetic fixture."""
    out_dir = tmp_path / "out"
    code = main(["prepare", "--sidebar", str(sidebar_file), "-o", str(out_dir)] + REAL_RUN)
    text = capsys.readouterr().out
    assert code == 0, text
    assert "Validation: OK" in text
    assert "4 tabs without a URL stay in the sidebar" in text
    report = json.loads((out_dir / "report.json").read_text())
    ledger = report["ledger"]
    no_url = [e for e in ledger if not e["url"]]
    assert len(no_url) == 4
    assert all(e["fate"] == FATE_SIDEBAR and e["reason"] == "no URL" for e in no_url)
    assert not [e for e in ledger if e["fate"] == FATE_DROPPED]
    assert all(e["url"] for e in ledger if e["fate"] == FATE_DEDUPED)
    cleaned = Sidebar.load(out_dir / "StorableSidebar.cleaned.json")
    abas = find_folder(cleaned, "Abas abertas do Arc", "Research")[0]
    assert len(cleaned.children(abas)) == 7
    assert cleaned.children(research(cleaned).unpinned_id) == []


def test_urlless_tab_in_duplicate_tree_is_kept(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, folder_strategy="keep", drop_empty_folders=False))
    sb = res.sidebar
    archive = find_folder(sb, "Archive", "Research")[0]
    kids = sb.children(archive)
    assert len(kids) == 1 and sb.items[kids[0]]["title"] == "Blank snippet"
    assert any('kept tab without URL "Blank snippet"' in a for r in res.spaces for a in r.actions)
    v = validate(rich_sb, sb.dumps(), "", res.ledger, set())
    assert v.ok, v.errors


def test_urlless_tabs_are_never_deduplicated(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, folder_strategy="path-names", dedupe_trees=False, dedupe_urls=True))
    blanks = [e for e in res.ledger if e.title == "Blank snippet"]
    assert len(blanks) == 2 and all(e.fate == FATE_SIDEBAR for e in blanks)
    snips = sorted(e.fate for e in res.ledger if e.url == "https://snip.example.com/1")
    assert snips == ["deduplicated", "sidebar"]


def test_folders_with_only_urlless_tabs_are_not_duplicates():
    b = fixturegen.Builder(seed="blank")
    _, pinned, _ = b.space("S")
    for _ in range(2):
        f = b.folder(pinned, "Drafts")
        b.urlless_tab(f, "Draft")
    sb = Sidebar(b.build())
    assert find_duplicate_trees(sb, sb.spaces[0]) == []


def test_validation_rejects_deduplicated_tab_without_url(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb))
    entry = [e for e in res.ledger if not e.url][0]
    entry.fate = FATE_DEDUPED
    res.sidebar.delete_subtree(entry.item_id)
    v = validate(rich_sb, res.sidebar.dumps(), "", res.ledger, set())
    assert any("has no URL but was removed as a duplicate" in e for e in v.errors)


def test_same_named_trees_merge_in_bookmarks(rich_sb):
    plan = make_plan(rich_sb, folder_strategy="path-names")
    plan.for_space(research(rich_sb).id).folder_overrides = [
        FolderOverride(strategy="to-bookmarks", path="Imported From Edge")
    ]
    res = transform(rich_sb, plan)
    node = [n for n in res.bookmarks if n.title == "Research"][0]
    edge = [c for c in node.children if c.title == "Imported From Edge"]
    assert len(edge) == 1
    assert [c.title for c in edge[0].children] == ["Tools", "Shared X", "Other Y"]
    assert [c.url for c in edge[0].children[0].children] == [
        "https://tools.example.com/a", "https://tools.example.com/c", "https://tools.example.com/b",
    ]
    merged = [e for e in res.ledger if e.space == "Research" and e.fate == FATE_DEDUPED and "bookmarks.html" in e.reason]
    assert len(merged) == 1 and merged[0].url == "https://tools.example.com/a"
    assert "Research › Imported From Edge › Tools" in merged[0].reason
    # The tab without URL from the exported tree stays in the sidebar.
    assert any(tab_url(rich_sb.items[c]) is None for c in res.sidebar.children(research(res.sidebar).pinned_id)
               if c in rich_sb.items)
    assert validate(rich_sb, res.sidebar.dumps(), B.render(res.bookmarks), res.ledger, set()).ok

    plan.for_space(research(rich_sb).id).merge_bookmark_folders = False
    res = transform(rich_sb, plan)
    node = [n for n in res.bookmarks if n.title == "Research"][0]
    assert [c.title for c in node.children] == ["Imported From Edge", "Imported From Edge"]
    assert B.count_links([node]) == 6


def test_cli_options_reach_the_plan(sidebar_file, capsys):
    code = main(["plan", "--sidebar", str(sidebar_file), "--print", "--tab-groups", "subfolders",
                 "--no-merge-bookmark-folders"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    sp = data["spaces"][3]
    assert sp["open_tabs_tab_groups"] == "subfolders" and sp["merge_bookmark_folders"] is False
    assert sp["_about"]["open_tab_groups"] == 2 and sp["_about"]["tabs_without_url"] == 4


def test_tab_group_title_fallbacks():
    assert folder_title({"title": None, "data": {"tabGroup": {"title": "Trip"}}}) == "Trip"
    assert folder_title({"title": None, "data": {"tabGroup": {}}}) == "Untitled tab group"
    assert folder_title({"title": "Own", "data": {"tabGroup": {"title": "Trip"}}}) == "Own"
