import copy
import json

import pytest

import fixturegen
from arc2dia2zen import bookmarks as B
from arc2dia2zen.errors import PlanError
from arc2dia2zen.model import KIND_FOLDER, Sidebar, tab_url
from arc2dia2zen.plan import FolderOverride
from arc2dia2zen.transform import FATE_BOOKMARKS, FATE_DEDUPED, FATE_DROPPED, FATE_SIDEBAR, transform, what_if
from arc2dia2zen.validate import structure_problems
from conftest import find_folder, folder_children_urls, make_plan, pinned_children, space_by_title

PLAIN = dict(dedupe_trees=False, drop_empty_folders=False, open_tabs="leave", pinned_wrappers="keep")


def run(sb, **kw):
    return transform(sb, make_plan(sb, **kw), id_factory=_ids())


def _ids():
    counter = iter(range(1, 10_000))
    return lambda: "NEW-%04d" % next(counter)


def pinned_folders(sb, title):
    space = space_by_title(sb, title)
    return [e for e in sb.walk(space.pinned_id) if e.kind == KIND_FOLDER]


def test_keep_with_no_options_changes_nothing(rich_sb, rich_raw):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=False, dedupe_urls=False, open_tabs="leave",
              drop_empty_folders=False, non_web="keep", pinned_wrappers="keep")
    assert json.loads(res.sidebar.dumps()) == rich_raw
    assert {e.fate for e in res.ledger} == {FATE_SIDEBAR}


def test_source_is_never_modified(rich_sb, rich_raw):
    run(rich_sb, folder_strategy="path-names")
    run(rich_sb, folder_strategy="to-bookmarks", open_tabs="bookmarks", non_web="to-bookmarks")
    assert json.loads(rich_sb.dumps()) == rich_raw


def test_path_names_flattens_with_full_names(rich_sb):
    res = run(rich_sb, folder_strategy="path-names", **PLAIN)
    sb = res.sidebar
    titles = [label for kind, label in pinned_children(sb, "Work") if kind == "folder"]
    assert titles == [
        "Projects",
        "Projects › Alpha",
        "Projects › Beta",
        "Imported From Edge › Favorites bar",
        "Imported From Edge › Favorites bar › Dev",
        "Imported From Edge › Favorites bar › Dev › Python",
        "Imported From Edge › Favorites bar › Dev › Python › Libraries",
        "Imported From Edge › Other favorites",
        "Imported From Edge",
        "Imported From Edge › Dev",
        "Imported From Edge › Dev › Python",
        "Imported From Edge › Dev › Python › Libraries",
        "Empty folder",
        None,  # the untitled folder keeps its null title
        "Shells › Inner",
    ]
    # No folder is nested any more.
    assert all(not e.path for e in pinned_folders(sb, "Work"))
    alpha = find_folder(sb, "Projects › Alpha", "Work")[0]
    assert folder_children_urls(sb, alpha) == ["https://alpha.example.com/spec", "https://alpha.example.com/notes"]
    # Loose tabs, split view and easel are untouched and keep their order.
    kinds = [k for k, _ in pinned_children(sb, "Work")]
    assert kinds[:2] == ["tab", "splitView"] and kinds[-1] == "easel"
    assert structure_problems(sb) == set()


def test_custom_path_separator(rich_sb):
    plan = make_plan(rich_sb, folder_strategy="path-names", **PLAIN)
    plan.path_separator = " / "
    sb = transform(rich_sb, plan).sidebar
    assert find_folder(sb, "Projects / Alpha", "Work")


def test_merge_to_top(rich_sb):
    res = run(rich_sb, folder_strategy="merge-to-top", **PLAIN)
    sb = res.sidebar
    folders = pinned_folders(sb, "Work")
    assert [f.obj["title"] for f in folders] == [
        "Projects", "Imported From Edge", "Imported From Edge", "Empty folder", None, "Shells",
    ]
    projects = find_folder(sb, "Projects", "Work")[0]
    assert folder_children_urls(sb, projects) == [
        "https://alpha.example.com/board",
        "https://alpha.example.com/spec",
        "https://alpha.example.com/notes",
        "https://beta.example.com/",
        "https://wiki.example.com/",
    ]
    edge_a = find_folder(sb, "Imported From Edge", "Work")[0]
    assert folder_children_urls(sb, edge_a) == [
        "https://pypi.example.org/project/requests",
        "https://pypi.example.org/project/attrs",
        "https://docs.python.example.org/3/",
        "https://git.example.com/",
        "https://news.example.com/",
        "https://recipes.example.net/bread",
        "javascript:alert('bookmarklet')",
    ]
    assert structure_problems(sb) == set()


def test_to_bookmarks_exports_nested_tree(rich_sb):
    res = run(rich_sb, per_space={"Work": {"folder_strategy": "to-bookmarks"}}, folder_strategy="keep", **PLAIN)
    sb = res.sidebar
    assert pinned_folders(sb, "Work") == []
    assert [k for k, _ in pinned_children(sb, "Work")] == ["tab", "splitView", "easel"]
    html = B.render(res.bookmarks)
    nodes = B.parse(html)
    work = nodes[0]
    assert work.title == "Work"
    # The two "Imported From Edge" trees share a name, so they become one folder (merge_bookmark_folders).
    assert [n.title for n in work.children] == [
        "Projects", "Imported From Edge", "Empty folder", "Untitled folder", "Shells",
    ]
    assert [n.title for n in work.children[1].children] == [
        "Favorites bar", "Other favorites", "Dev", "News", "Bread", "A bookmarklet",
    ]
    projects = work.children[0]
    assert [n.title for n in projects.children] == ["Alpha board", "Alpha", "Beta"]
    assert [n.url for n in projects.children[1].children] == [
        "https://alpha.example.com/spec", "https://alpha.example.com/notes",
    ]
    deep = [path for path, _ in B.iter_links(nodes) if len(path) == 6]
    assert deep and deep[0] == ("Work", "Imported From Edge", "Favorites bar", "Dev", "Python", "Libraries")
    exported = [e for e in res.ledger if e.fate == FATE_BOOKMARKS]
    assert len(exported) == 21
    assert all(e.bookmarks_path.startswith("Work › ") for e in exported)


def test_nested_override_to_bookmarks_keeps_context(rich_sb):
    sb0 = rich_sb
    plan = make_plan(sb0, folder_strategy="keep", **PLAIN)
    plan.for_space(space_by_title(sb0, "Work").id).folder_overrides = [
        FolderOverride(strategy="to-bookmarks", path="Projects › Alpha")
    ]
    res = transform(sb0, plan)
    assert not find_folder(res.sidebar, "Alpha", "Work")
    assert find_folder(res.sidebar, "Projects", "Work")
    links = [(path, n.url) for path, n in B.iter_links(res.bookmarks)]
    assert links == [
        (("Work", "Projects", "Alpha"), "https://alpha.example.com/spec"),
        (("Work", "Projects", "Alpha"), "https://alpha.example.com/notes"),
    ]


def test_override_by_path_hits_every_match(rich_sb):
    plan = make_plan(rich_sb, folder_strategy="path-names", **PLAIN)
    plan.for_space(space_by_title(rich_sb, "Work").id).folder_overrides = [
        FolderOverride(strategy="to-bookmarks", path="Imported From Edge")
    ]
    res = transform(rich_sb, plan)
    assert not find_folder(res.sidebar, "Imported From Edge", "Work")
    assert [n.title for n in res.bookmarks[0].children] == ["Imported From Edge"]
    assert find_folder(res.sidebar, "Projects › Alpha", "Work")
    plan.for_space(space_by_title(rich_sb, "Work").id).merge_bookmark_folders = False
    res = transform(rich_sb, plan)
    assert [n.title for n in res.bookmarks[0].children] == ["Imported From Edge", "Imported From Edge"]


def test_override_by_id(rich_sb):
    work = space_by_title(rich_sb, "Work")
    projects = find_folder(rich_sb, "Projects", "Work")[0]
    plan = make_plan(rich_sb, folder_strategy="path-names", **PLAIN)
    plan.for_space(work.id).folder_overrides = [FolderOverride(strategy="keep", folder_id=projects)]
    sb = transform(rich_sb, plan).sidebar
    assert find_folder(sb, "Alpha", "Work") and not find_folder(sb, "Projects › Alpha", "Work")
    assert find_folder(sb, "Imported From Edge › Dev", "Work")


@pytest.mark.parametrize("override, message", [
    (FolderOverride(strategy="merge-to-top", path="Projects › Alpha"), "only to-bookmarks"),
    (FolderOverride(strategy="keep", path="No such folder"), "no pinned folder matches"),
    (FolderOverride(strategy="keep", folder_id="NOT-AN-ID"), "not a pinned folder"),
])
def test_bad_overrides(rich_sb, override, message):
    plan = make_plan(rich_sb, folder_strategy="keep", **PLAIN)
    plan.for_space(space_by_title(rich_sb, "Work").id).folder_overrides = [override]
    with pytest.raises(PlanError, match=message):
        transform(rich_sb, plan)


def test_unknown_space_in_plan(rich_sb):
    plan = make_plan(rich_sb)
    plan.spaces[0].space_id = "NOT-A-SPACE"
    with pytest.raises(PlanError, match="not in this sidebar"):
        transform(rich_sb, plan)


def test_space_missing_from_plan_is_left_unchanged(rich_sb):
    plan = make_plan(rich_sb, folder_strategy="path-names")
    plan.spaces = [sp for sp in plan.spaces if sp.title != "Personal"]
    res = transform(rich_sb, plan)
    assert any("Personal" in w for w in res.warnings)
    assert pinned_children(res.sidebar, "Personal") == pinned_children(rich_sb, "Personal")


# --------------------------------------------------------------------------- open tabs

def test_open_tabs_to_pinned_folder_flattens_split_views(rich_sb):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=False, drop_empty_folders=False)
    sb = res.sidebar
    work = space_by_title(sb, "Work")
    last = sb.children(work.pinned_id)[-1]
    assert sb.items[last]["title"] == "Open tabs from Arc" and last.startswith("NEW-")
    assert folder_children_urls(sb, last) == [
        "https://mail.example.com/inbox",
        "https://calendar.example.com/",
        "https://meet.example.com/room",
        "https://wiki.example.com/",
        "https://search.example.com/?q=arc+to+dia",
        "data:text/plain,hello",
        "about:blank",
    ]
    # The unsupported Arc document stays among the open tabs; the split view item is gone.
    assert [sb.kind(c) for c in sb.children(work.unpinned_id)] == ["arcDocument"]
    assert not any(sb.kind(i) == "splitView" for i in sb.subtree_ids(work.unpinned_id))
    folder = sb.items[last]
    assert folder["data"] == {"list": {}} and folder["parentID"] == work.pinned_id
    assert structure_problems(sb) == set()


def test_open_tabs_folder_name_and_split_keep(rich_sb):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=False, open_tabs_folder_name="Today in Arc",
              open_tabs_split_views="keep")
    sb = res.sidebar
    fid = find_folder(sb, "Today in Arc", "Work")[0]
    kinds = [sb.kind(c) for c in sb.children(fid)]
    assert kinds == ["tab", "splitView", "tab", "tab", "tab", "tab"]
    assert structure_problems(sb) == set()


def test_open_tabs_to_bookmarks(rich_sb):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=False, open_tabs="bookmarks")
    sb = res.sidebar
    work = space_by_title(sb, "Work")
    assert [sb.kind(c) for c in sb.children(work.unpinned_id)] == ["arcDocument"]
    links = [(p, n.url) for p, n in B.iter_links(res.bookmarks) if p[0] == "Work"]
    assert len(links) == 7 and all(p == ("Work", "Open tabs from Arc") for p, _ in links)


def test_open_tabs_leave(rich_sb):
    res = run(rich_sb, folder_strategy="keep", open_tabs="leave")
    work = space_by_title(res.sidebar, "Work")
    assert len(res.sidebar.children(work.unpinned_id)) == 7
    notes = [e.dia_note for e in res.ledger if e.origin == "open" and e.space == "Work"]
    assert all("does not import open tabs" in n for n in notes)


# --------------------------------------------------------------------------- dedupe and cleanup

def test_dedupe_trees(rich_sb):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=True, drop_empty_folders=False, open_tabs="leave")
    sb = res.sidebar
    edges = find_folder(sb, "Imported From Edge", "Work")
    assert len(edges) == 1
    assert not find_folder(sb, "Favorites bar", "Work")  # the copy with the extra layer went
    assert len(find_folder(sb, "Recipes", "Personal")) == 1
    deduped = [e for e in res.ledger if e.fate == FATE_DEDUPED]
    assert len(deduped) == 10  # Work 7, Personal 2, Research 1 (its tab without URL is kept)
    assert all("same URLs are kept in" in e.reason for e in deduped)


def test_dedupe_urls_keeps_first_copy(rich_sb):
    res = run(rich_sb, folder_strategy="path-names", dedupe_trees=False, dedupe_urls=True)
    sb = res.sidebar
    work = space_by_title(sb, "Work")
    urls = [tab_url(e.obj) for e in sb.walk(work.pinned_id) if e.kind == "tab"]
    assert len(urls) == len(set(urls))
    assert urls[0] == "https://wiki.example.com/"
    removed = [e for e in res.ledger if e.fate == FATE_DEDUPED and e.space == "Work"]
    assert len(removed) == 9  # 2 extra wiki copies + 7 from the second Edge tree


def test_drop_empty_folders(rich_sb):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=False, drop_empty_folders=True, open_tabs="leave")
    sb = res.sidebar
    for title in ("Empty folder", "Shells", "Inner"):
        assert not find_folder(sb, title, "Work")
    assert find_folder(sb, "Projects", "Work")


def test_folder_with_only_an_easel_is_not_empty():
    b = fixturegen.Builder(seed="easel")
    _, pinned, _ = b.space("S")
    f = b.folder(pinned, "Boards")
    b.easel(f, "Board")
    sb = Sidebar(b.build())
    res = transform(sb, make_plan(sb, folder_strategy="keep", drop_empty_folders=True))
    assert find_folder(res.sidebar, "Boards")


def test_unsupported_items_survive_removed_folders():
    b = fixturegen.Builder(seed="rescue")
    _, pinned, _ = b.space("S")
    f = b.folder(pinned, "Mixed")
    b.tab(f, "https://rescue.example.com/")
    b.easel(f, "Board")
    sb = Sidebar(b.build())
    res = transform(sb, make_plan(sb, folder_strategy="to-bookmarks"))
    kinds = [k for k, _ in pinned_children(res.sidebar, "S")]
    assert kinds == ["easel"]
    assert any('kept easel "Board"' in a for a in res.spaces[0].actions)


def test_non_web_to_bookmarks(rich_sb):
    res = run(rich_sb, folder_strategy="keep", dedupe_trees=False, non_web="to-bookmarks")
    sb = res.sidebar
    work = space_by_title(sb, "Work")
    left = [tab_url(e.obj) for e in sb.walk(work.pinned_id) if e.kind == "tab"]
    assert all(u.startswith("https://") for u in left)
    links = [n.url for p, n in B.iter_links(res.bookmarks) if p == ("Work", "Non-web links from Arc")]
    # The two identical bookmarklets land in one folder, so only one copy is kept there.
    assert sorted(links) == sorted([
        "javascript:alert('bookmarklet')",
        "file:///Users/example/Documents/report.pdf", "chrome-extension://abcdefghijklmnop/options.html",
        "data:text/plain,hello", "about:blank",
    ])
    js = [e for e in res.ledger if e.url.startswith("javascript:")]
    assert sorted(e.fate for e in js) == ["bookmarks", "deduplicated"]


def test_exclude_space(rich_sb):
    res = run(rich_sb, per_space={"Personal": {"import_space": False}}, folder_strategy="keep")
    sb = res.sidebar
    assert [s.title for s in sb.spaces] == ["Work", "Space 3", "Research"]
    dropped = [e for e in res.ledger if e.fate == FATE_DROPPED]
    assert len(dropped) == 8 and all("excluded" in e.reason for e in dropped)
    personal = space_by_title(rich_sb, "Personal")
    assert personal.pinned_id not in sb.items and personal.unpinned_id not in sb.items
    raw = json.loads(sb.dumps())
    assert len(raw["sidebar"]["containers"][1]["spaces"]) == 6
    assert structure_problems(sb) == set()


def test_favorites_and_sync_state_untouched(rich_sb, rich_raw):
    res = run(rich_sb, folder_strategy="to-bookmarks", open_tabs="bookmarks", dedupe_urls=True, non_web="to-bookmarks")
    raw = json.loads(res.sidebar.dumps())
    for key in ("sidebarSyncState", "firebaseSyncState", "version"):
        assert raw[key] == rich_raw[key]
    cont, orig = raw["sidebar"]["containers"][1], rich_raw["sidebar"]["containers"][1]
    assert cont["topAppsContainerIDs"] == orig["topAppsContainerIDs"]
    for fav in rich_sb.favorites:
        assert res.sidebar.children(fav.container_id) == rich_sb.children(fav.container_id)
    assert raw["sidebar"]["containers"][0] == rich_raw["sidebar"]["containers"][0]


def test_what_if_numbers(rich_sb):
    plan = make_plan(rich_sb)  # defaults: dedupe trees, drop empty, open tabs to a pinned folder
    wi = what_if(rich_sb, plan, space_by_title(rich_sb, "Work").id)
    assert wi["keep"]["pinned_groups"] == 9 and wi["keep"]["nested_groups"] == 5
    assert wi["path-names"]["pinned_groups"] == 9 and wi["path-names"]["nested_groups"] == 0
    assert wi["merge-to-top"]["pinned_groups"] == 4
    assert wi["to-bookmarks"]["pinned_groups"] == 1 and wi["to-bookmarks"]["bookmarks"] == 14
    assert all(r["open_tabs_lost"] == 0 for r in wi.values())


def test_deep_chain():
    sb = Sidebar(fixturegen.deep_sidebar(levels=5))
    res = transform(sb, make_plan(sb, folder_strategy="path-names"))
    titles = [label for _, label in pinned_children(res.sidebar, "Deep")]
    assert titles[-1] == "Level 1 › Level 2 › Level 3 › Level 4 › Level 5"
    assert len(titles) == 5
