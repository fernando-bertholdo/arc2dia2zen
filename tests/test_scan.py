import json

import fixturegen
from arc2dia2zen.analysis import find_duplicate_trees, find_similar_trees, render_scan, scan
from arc2dia2zen.model import Sidebar
from conftest import space_by_title


def test_work_counts(rich_sb):
    report = scan(rich_sb, "synthetic")
    work = report.spaces[0]
    assert work.title == "Work"
    assert work.pinned_tabs == 24
    assert work.pinned_loose_tabs == 3
    assert work.pinned_split_views == 1
    assert work.folders == 17
    assert work.top_level_folders == 6
    assert work.nested_folders == 11
    assert work.max_depth == 5
    assert sorted(work.empty_folders) == [("Empty folder",), ("Shells",), ("Shells", "Inner")]
    assert work.folders_with_only_subfolders == 2
    assert work.open_tabs == 7
    assert work.open_split_views == 1
    assert work.unsupported == {"easel": 1, "arcDocument": 1}
    schemes = sorted(n.scheme for n in work.non_web)
    assert schemes == ["about", "chrome-extension", "data", "file", "javascript", "javascript"]


def test_work_duplicates(rich_sb):
    work = scan(rich_sb).spaces[0]
    assert len(work.duplicate_trees) == 1
    dup = work.duplicate_trees[0]
    # The copy with the extra "Favorites bar" layer goes; the simpler one stays.
    assert dup.removed_path == ("Imported From Edge",) and dup.folders == 6
    assert dup.kept_folders == 4 and dup.match == "contained"
    assert len(work.duplicate_urls) == 8
    assert work.duplicate_url_extra_copies == 9
    wiki = [d for d in work.duplicate_urls if d.url == "https://wiki.example.com/"][0]
    assert wiki.count == 3 and {loc.area for loc in wiki.locations} == {"pinned", "open"}


def test_identical_nested_duplicate(rich_sb):
    personal = scan(rich_sb).spaces[1]
    assert len(personal.duplicate_trees) == 1
    dup = personal.duplicate_trees[0]
    assert dup.match == "identical"
    assert dup.removed_path == ("Reading", "Recipes") and dup.kept_path == ("Recipes",)


def test_prediction_matches_dia_behaviour(rich_sb):
    p = scan(rich_sb).spaces[0].prediction
    assert p.pinned_groups == 17          # every folder becomes a flat group
    assert p.pinned_groups_min == 13      # folders that hold tabs directly
    assert p.nested_groups == 11
    assert p.open_tabs_lost == 7          # gap 1: open tabs are not imported
    assert p.non_web_dropped == 4
    # The split view's two tabs are dropped (seen in a real migration test).
    assert p.loose_pinned_tabs == 1 and p.tabs_in_groups == 21 and p.wrapped_tabs_lost == 2


def test_favorites_and_sync(rich_sb):
    report = scan(rich_sb)
    assert report.sync_keys == ["sidebarSyncState", "firebaseSyncState"]
    assert [f.tabs for f in report.favorites] == [2, 1]
    nosync = scan(Sidebar(fixturegen.rich_sidebar(sync=False)))
    assert nosync.sync_keys == []


def test_json_is_serializable(rich_sb):
    data = json.loads(json.dumps(scan(rich_sb).to_dict()))
    assert data["spaces"][0]["duplicate_url_extra_copies"] == 9


def test_text_mentions_key_facts(rich_sb):
    text = render_scan(scan(rich_sb, "synthetic"))
    assert "Arc Sync: on" in text
    assert "max depth 5" in text
    assert "not imported: 7 open tabs" in text
    assert "13 to 17 flat pinned groups" in text


def test_similar_trees_are_reported_not_removed():
    b = fixturegen.Builder(seed="sim")
    _, pinned, _ = b.space("S")
    a = b.folder(pinned, "Old bookmarks")
    c = b.folder(pinned, "Bookmarks backup")
    for n in range(6):
        b.tab(a, f"https://sim.example.com/{n}")
        b.tab(c, f"https://sim.example.com/{n}")
    sb = Sidebar(b.build())
    space = space_by_title(sb, "S")
    assert find_duplicate_trees(sb, space) == []
    sims = find_similar_trees(sb, space)
    assert len(sims) == 1 and sims[0].jaccard == 1.0


def test_curated_subset_with_other_name_is_not_a_duplicate():
    b = fixturegen.Builder(seed="cur")
    _, pinned, _ = b.space("S")
    big = b.folder(pinned, "Imported")
    small = b.folder(pinned, "Daily")
    for n in range(5):
        b.tab(big, f"https://cur.example.com/{n}")
    b.tab(small, "https://cur.example.com/1")
    sb = Sidebar(b.build())
    assert find_duplicate_trees(sb, space_by_title(sb, "S")) == []


def test_similar_trees_skip_parts_of_duplicates():
    b = fixturegen.Builder(seed="dupsim")
    _, pinned, _ = b.space("S")
    a = b.folder(pinned, "Imported From Edge")
    bar = b.folder(a, "Favorites bar")
    flat = b.folder(pinned, "Imported From Edge")
    for n in range(6):
        b.tab(bar, f"https://dupsim.example.com/{n}")
        b.tab(flat, f"https://dupsim.example.com/{n}")
    sb = Sidebar(b.build())
    s = scan(sb).spaces[0]
    assert len(s.duplicate_trees) == 1 and s.duplicate_trees[0].folders == 2
    assert s.similar_trees == []  # "Favorites bar" lives inside the removed copy
