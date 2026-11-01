import copy
import json

import pytest

import fixturegen
from arc2dia2zen.errors import FormatError
from arc2dia2zen.model import (
    APPLE_EPOCH_OFFSET,
    Sidebar,
    apple_to_unix,
    item_kind,
    profile_label,
    read_pairs,
    tab_title,
    write_pairs,
)


def test_parses_spaces_and_containers(rich_sb):
    assert [s.title for s in rich_sb.spaces] == ["Work", "Personal", "Space 3", "Research"]
    work = rich_sb.spaces[0]
    assert work.pinned_id in rich_sb.items and work.unpinned_id in rich_sb.items
    assert rich_sb.kind(work.pinned_id) == "itemContainer"
    assert work.profile_label == "default"
    assert rich_sb.spaces[1].profile_label == "Profile 1"
    assert rich_sb.spaces[2].raw_title is None


def test_kinds_present(rich_sb):
    kinds = {item_kind(o) for o in rich_sb.items.values()}
    assert {"tab", "list", "tabGroup", "itemContainer", "splitView", "easel", "arcDocument"} <= kinds


def test_favorites_are_attached_to_profiles(rich_sb):
    labels = [(f.profile_label, len(rich_sb.children(f.container_id))) for f in rich_sb.favorites]
    assert labels == [("default", 2), ("Profile 1", 1)]


def test_walk_depth_and_paths(rich_sb):
    work = rich_sb.spaces[0]
    depths = {e.depth for e in rich_sb.walk(work.pinned_id) if e.kind == "list"}
    assert max(depths) == 5
    libs = [e for e in rich_sb.walk(work.pinned_id) if e.kind == "list" and e.obj["title"] == "Libraries"]
    assert libs[0].path == ("Imported From Edge", "Favorites bar", "Dev", "Python")


def test_split_children_are_marked(rich_sb):
    work = rich_sb.spaces[0]
    in_split = [e for e in rich_sb.walk(work.unpinned_id) if e.in_split]
    assert len(in_split) == 2


def test_null_titles_fall_back(rich_sb):
    notes = [o for o in rich_sb.items.values() if (o.get("data", {}).get("tab") or {}).get("savedTitle") == "Alpha notes"]
    assert notes[0]["title"] is None
    assert tab_title(notes[0]) == "Alpha notes"
    search = [o for o in rich_sb.items.values()
              if (o.get("data", {}).get("tab") or {}).get("savedURL", "").startswith("https://search.")]
    assert tab_title(search[0]) == "https://search.example.com/?q=arc+to+dia"


def test_serialize_round_trip_is_identical(rich_raw):
    sb = Sidebar(copy.deepcopy(rich_raw))
    assert json.loads(sb.dumps()) == rich_raw


def test_alternating_pairs_round_trip():
    lst = ["a", {"id": "a"}, "b", {"id": "b"}]
    assert write_pairs(read_pairs(lst, "x")) == lst


@pytest.mark.parametrize("bad", [["a"], ["a", "b"], [{"id": 1}, {"id": 2}], {"a": 1}])
def test_bad_pairs_raise(bad):
    with pytest.raises(FormatError):
        read_pairs(bad, "items")


def test_missing_container_raises():
    with pytest.raises(FormatError):
        Sidebar({"sidebar": {"containers": [{"global": {}}]}})
    with pytest.raises(FormatError):
        Sidebar({"nothing": True})
    with pytest.raises(FormatError):
        Sidebar.from_bytes(b"{not json")


def test_duplicate_item_id_raises(rich_raw):
    items = rich_raw["sidebar"]["containers"][1]["items"]
    items.extend([items[0], copy.deepcopy(items[1])])
    with pytest.raises(FormatError):
        Sidebar(rich_raw)


def test_locate_all_finds_orphans():
    sb = Sidebar(fixturegen.rich_sidebar(orphan=True))
    where = sb.locate_all()
    orphans = [i for i, loc in where.items() if loc.area == "orphan"]
    assert len(orphans) == 1


def test_apple_epoch():
    assert apple_to_unix(0) == APPLE_EPOCH_OFFSET
    assert apple_to_unix(700000000.0) == 1678307200
    assert apple_to_unix(None) is None
    assert apple_to_unix(True) is None


def test_profile_labels():
    assert profile_label({"default": True}) == "default"
    assert profile_label({"custom": {"_0": {"directoryBasename": "Profile 2"}}}) == "Profile 2"
    assert profile_label(None) == "unknown"
