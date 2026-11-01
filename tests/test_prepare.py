import copy
import itertools
import json

import pytest

import fixturegen
from arc2dia2zen import bookmarks as B
from arc2dia2zen.errors import Arc2DiaError
from arc2dia2zen.model import Sidebar, tab_url
from arc2dia2zen.plan import FOLDER_STRATEGIES
from arc2dia2zen.prepare import BOOKMARKS_NAME, CLEANED_NAME, MANIFEST_NAME, prepare
from arc2dia2zen.transform import transform
from arc2dia2zen.validate import shape_problems, structure_problems, validate
from conftest import make_plan


def _assert_alternating(raw):
    cont = raw["sidebar"]["containers"][1]
    for key in ("items", "spaces"):
        lst = cont[key]
        assert len(lst) % 2 == 0
        for i in range(0, len(lst), 2):
            assert isinstance(lst[i], str) and isinstance(lst[i + 1], dict)
            assert lst[i + 1]["id"] == lst[i]


COMBOS = list(itertools.product(
    FOLDER_STRATEGIES,
    ("pinned-folder", "bookmarks", "leave"),
    (True, False),   # dedupe_trees
    (True, False),   # dedupe_urls
    ("keep", "to-bookmarks"),
))


@pytest.mark.parametrize("strategy, open_tabs, dedupe_trees, dedupe_urls, non_web", COMBOS)
def test_every_combination_validates(tmp_path, sidebar_file, strategy, open_tabs, dedupe_trees, dedupe_urls, non_web):
    sb = Sidebar.load(sidebar_file)
    plan = make_plan(sb, folder_strategy=strategy, open_tabs=open_tabs, dedupe_trees=dedupe_trees,
                     dedupe_urls=dedupe_urls, non_web=non_web)
    out = prepare(sidebar_file, plan, tmp_path / "out")
    assert out.validation.ok, out.validation.errors
    cleaned = Sidebar.load(tmp_path / "out" / CLEANED_NAME)
    assert structure_problems(cleaned) == set()
    _assert_alternating(cleaned.raw)
    assert shape_problems(cleaned.raw) == []
    # Every original tab is accounted for exactly once.
    original_tabs = {i for i in sb.items if sb.kind(i) == "tab"}
    assert {e.item_id for e in out.result.ledger} == original_tabs
    assert out.validation.stats["original_tabs"] == len(original_tabs) == 64


def test_url_accounting_by_location(tmp_path, sidebar_file):
    sb = Sidebar.load(sidebar_file)
    plan = make_plan(sb, folder_strategy="to-bookmarks", open_tabs="pinned-folder", dedupe_trees=True)
    out = prepare(sidebar_file, plan, tmp_path / "out")
    cleaned = Sidebar.load(tmp_path / "out" / CLEANED_NAME)
    html_urls = [n.url for _, n in B.iter_links(B.parse((tmp_path / "out" / BOOKMARKS_NAME).read_text()))]
    for e in out.result.ledger:
        if e.fate == "sidebar":
            assert (tab_url(cleaned.items[e.item_id]) or "") == e.url
        elif e.fate == "bookmarks":
            assert e.url in html_urls
        elif e.fate == "deduplicated":
            assert e.item_id not in cleaned.items
    counts = out.result.fate_counts()
    assert counts == {"sidebar": 28, "bookmarks": 25, "deduplicated": 11, "dropped": 0}
    assert sum(counts.values()) == 64


def test_outputs_and_manifest(tmp_path, sidebar_file):
    sb = Sidebar.load(sidebar_file)
    out = prepare(sidebar_file, make_plan(sb, folder_strategy="to-bookmarks"), tmp_path / "out")
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == sorted([CLEANED_NAME, BOOKMARKS_NAME, MANIFEST_NAME, "plan.json", "report.json", "report.txt"])
    manifest = json.loads((tmp_path / "out" / MANIFEST_NAME).read_text())
    assert manifest["validation_ok"] is True
    assert manifest["sync_keys"] == ["sidebarSyncState", "firebaseSyncState"]
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert len(report["ledger"]) == 64
    assert "URL accounting" in (tmp_path / "out" / "report.txt").read_text()
    # The source file is untouched.
    assert json.loads(sidebar_file.read_text()) == fixturegen.rich_sidebar()


def test_refuses_to_overwrite_without_force(tmp_path, sidebar_file):
    sb = Sidebar.load(sidebar_file)
    plan = make_plan(sb)
    prepare(sidebar_file, plan, tmp_path / "out")
    with pytest.raises(Arc2DiaError, match="--force"):
        prepare(sidebar_file, plan, tmp_path / "out")
    prepare(sidebar_file, plan, tmp_path / "out", force=True)


def test_refuses_arc_folder_as_output(sidebar_file):
    sb = Sidebar.load(sidebar_file)
    with pytest.raises(Arc2DiaError, match="Arc's own folder"):
        prepare(sidebar_file, make_plan(sb), sidebar_file.parent)


def test_pre_existing_oddities_are_warnings(tmp_path):
    raw = fixturegen.rich_sidebar(orphan=True)
    path = tmp_path / "StorableSidebar.json"
    path.write_text(json.dumps(raw))
    sb = Sidebar.load(path)
    out = prepare(path, make_plan(sb, folder_strategy="path-names"), tmp_path / "out")
    assert out.validation.ok
    assert any("already present" in w for w in out.validation.warnings)
    orphan = [e for e in out.result.ledger if e.origin == "orphan"]
    assert len(orphan) == 1 and orphan[0].fate == "sidebar"


# --------------------------------------------------------------------------- validation catches bugs

@pytest.fixture
def prepared(rich_sb):
    plan = make_plan(rich_sb, folder_strategy="to-bookmarks")
    res = transform(rich_sb, plan)
    html = B.render(res.bookmarks)
    return rich_sb, res, html


def test_validation_passes_on_good_output(prepared):
    sb, res, html = prepared
    assert validate(sb, res.sidebar.dumps(), html, res.ledger, set()).ok


def test_validation_catches_a_lost_tab(prepared):
    sb, res, html = prepared
    out = res.sidebar.clone()
    victim = next(e.item_id for e in res.ledger if e.fate == "sidebar" and e.origin == "pinned")
    out.delete_subtree(victim)
    v = validate(sb, out.dumps(), html, res.ledger, set())
    assert not v.ok and any("missing" in e for e in v.errors)


def test_validation_catches_a_dangling_id(prepared):
    sb, res, html = prepared
    out = res.sidebar.clone()
    pinned = out.spaces[0].pinned_id
    out.items[pinned]["childrenIds"].append("DANGLING-ID")
    v = validate(sb, out.dumps(), html, res.ledger, set())
    assert any("dangling child id DANGLING-ID" in e for e in v.errors)


def test_validation_catches_missing_bookmark(prepared):
    sb, res, html = prepared
    lines = html.splitlines()
    first_link = next(i for i, line in enumerate(lines) if "<A HREF" in line)
    del lines[first_link]
    v = validate(sb, res.sidebar.dumps(), "\n".join(lines), res.ledger, set())
    assert any("missing from bookmarks.html" in e for e in v.errors)


def test_validation_catches_broken_shape(prepared):
    sb, res, html = prepared
    raw = copy.deepcopy(res.sidebar.raw)
    raw["sidebar"]["containers"][1]["items"].append("odd-one-out")
    v = validate(sb, json.dumps(raw).encode(), html, res.ledger, set())
    assert not v.ok


def test_validation_catches_changed_sync_state(prepared):
    sb, res, html = prepared
    raw = copy.deepcopy(res.sidebar.raw)
    raw["sidebarSyncState"] = {"tampered": True}
    v = validate(sb, json.dumps(raw).encode(), html, res.ledger, set())
    assert any("other than items and spaces" in e for e in v.errors)


def test_validation_catches_invented_tab(prepared):
    sb, res, html = prepared
    out = res.sidebar.clone()
    pinned = out.spaces[0].pinned_id
    out.items["INVENTED"] = {"id": "INVENTED", "title": "x", "parentID": pinned, "childrenIds": [],
                             "data": {"tab": {"savedURL": "https://invented.example.com/"}}}
    out.items[pinned]["childrenIds"].append("INVENTED")
    v = validate(sb, out.dumps(), html, res.ledger, set())
    assert any("should never create tabs" in e for e in v.errors)
