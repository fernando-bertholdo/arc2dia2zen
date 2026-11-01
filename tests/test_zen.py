"""Target zen: the plan defaults, prepare's transformation and the port of Zen's Arc importer
(src/zen/migration/ZenMigrationUtils.sys.mjs in zen-browser/desktop, dev at 147e6ebb2)."""

import json

import pytest

import fixturegen
from arc2dia2zen import swap as S
from arc2dia2zen.errors import PlanError
from arc2dia2zen.model import FOLDER_KINDS, KIND_FOLDER, KIND_SPLIT, KIND_TAB_GROUP, Sidebar, folder_title
from arc2dia2zen.plan import plan_from_json
from arc2dia2zen.prepare import CLEANED_NAME, prepare
from arc2dia2zen.transform import FATE_SIDEBAR
from arc2dia2zen.zen import import_tree, parse_space, predict_zen_import, zen_url
from conftest import FakeChecker, space_by_title
from test_cli import run


def zen_plan(path, capsys, *extra):
    code, out, err = run(["plan", "--target", "zen", "--sidebar", str(path), "--print", *extra], capsys)
    assert code == 0, err
    return json.loads(out)


def write_sidebar(tmp_path, raw):
    path = tmp_path / "Arc" / "StorableSidebar.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def folder_depth(sb, root):
    return max((len(e.path) + 1 for e in sb.walk(root) if e.kind in FOLDER_KINDS), default=0)


def tree_json(items):
    return [i.to_json() for i in items]


# --------------------------------------------------------------------------- the plan

def test_old_plans_are_for_dia_and_zen_plans_round_trip():
    base = {"arc2dia2zen_plan_version": 1, "spaces": []}
    plan = plan_from_json(base)
    assert plan.target == "dia" and plan.max_folder_depth is None
    zen = plan_from_json({**base, "target": "zen"})
    assert zen.target == "zen" and zen.max_folder_depth == 5
    assert plan_from_json(zen.to_json()).max_folder_depth == 5
    assert plan_from_json({**base, "target": "zen", "max_folder_depth": None}).max_folder_depth is None
    for bad in (0, -1, 2.5, True, "5"):
        with pytest.raises(PlanError, match="max_folder_depth"):
            plan_from_json({**base, "target": "zen", "max_folder_depth": bad})
    with pytest.raises(PlanError, match="only applies to target zen"):
        plan_from_json({**base, "max_folder_depth": 5})
    with pytest.raises(PlanError, match="plan.target"):
        plan_from_json({**base, "target": "chrome"})
    assert "max_folder_depth" not in plan.to_json() and plan.to_json()["target"] == "dia"


def test_zen_plan_defaults(sidebar_file, capsys):
    data = zen_plan(sidebar_file, capsys)
    assert data["target"] == "zen" and data["max_folder_depth"] == 5
    for sp in data["spaces"]:
        assert sp["folder_strategy"] == "keep"  # Zen keeps nested folders; Dia's default would be path-names
        assert sp["open_tabs"] == "pinned-folder"
        assert sp["open_tabs_tab_groups"] == "subfolders"
        assert sp["pinned_wrappers"] == "folders"
        assert sp["_about"]["what_if_folder_strategy"] == {}  # the what-if numbers are Dia's
    assert zen_plan(sidebar_file, capsys, "--max-folder-depth", "0")["max_folder_depth"] is None
    assert zen_plan(sidebar_file, capsys, "--max-folder-depth", "7", "--tab-groups", "flatten",
                    "--keep-pinned-wrappers")["spaces"][0]["pinned_wrappers"] == "keep"


def test_dia_plan_defaults_are_unchanged(sidebar_file, capsys):
    code, out, _ = run(["plan", "--sidebar", str(sidebar_file), "--print"], capsys)
    data = json.loads(out)
    assert data["target"] == "dia" and "max_folder_depth" not in data
    work = data["spaces"][0]
    assert (work["folder_strategy"], work["open_tabs_tab_groups"], work["pinned_wrappers"]) == (
        "path-names", "flatten", "unwrap")
    code, _, err = run(["plan", "--sidebar", str(sidebar_file), "--print", "--max-folder-depth", "3"], capsys)
    assert code == 2 and "only applies to --target zen" in err


def test_prepare_refuses_a_plan_made_for_another_target(sidebar_file, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(zen_plan(sidebar_file, capsys)), encoding="utf-8")
    code, _, err = run(["prepare", "--sidebar", str(sidebar_file), "--plan", str(plan), "--target", "dia",
                        "-o", str(tmp_path / "out")], capsys)
    assert code == 2 and "is for Zen, not Dia" in err


# --------------------------------------------------------------------------- the importer port

def test_zen_url_follows_the_importable_schemes():
    for ok in ("https://a.example/", "http://a.example/x", " https://a.example/ ", "file:///Users/x/a.pdf",
               "ftp://files.example/"):
        assert zen_url(ok)
    for bad in (None, "", "javascript:void(0)", "chrome://settings", "about:blank", "arc://boost",
                "http://", "mailto:a@example.com", 42):
        assert zen_url(bad) is None


def test_parser_port_keeps_folders_flattens_split_views_and_drops_the_rest():
    b = fixturegen.Builder(seed="zen-port")
    _, pinned, unpinned = b.space("S")
    b.tab(pinned, "https://one.example/", "One")
    b.tab(pinned, "javascript:alert(1)", "Script")
    folder = b.folder(pinned, "Folder")
    b.tab(folder, "https://two.example/", "Two")
    b.folder(folder, "Empty")
    b.split(pinned, ["https://three.example/", "https://four.example/"])
    group = b.tab_group(pinned, "Group")
    b.tab(group, "https://five.example/", "Five")
    b.easel(pinned, "Board")
    b.urlless_tab(pinned, "No URL")
    b.tab(unpinned, "https://open.example/", "Open")
    sb = Sidebar(b.build())
    space = sb.spaces[0]
    assert tree_json(parse_space(sb, space)) == [
        {"type": "tab", "url": "https://one.example/", "title": "One"},
        {"type": "folder", "title": "Folder", "items": [{"type": "tab", "url": "https://two.example/", "title": "Two"}]},
        {"type": "tab", "url": "https://three.example/", "title": "three.example"},
        {"type": "tab", "url": "https://four.example/", "title": "four.example"},
    ]
    p = predict_zen_import(sb, space)
    assert (p.pinned_tabs, p.loose_pinned_tabs, p.folders) == (4, 3, 1)
    assert (p.open_tabs_lost, p.tab_group_tabs_lost, p.unimportable_urls, p.unsupported_items) == (1, 1, 2, 1)


def test_parser_port_falls_back_to_parent_ids_when_children_ids_are_empty():
    b = fixturegen.Builder(seed="zen-parent")
    _, pinned, _ = b.space("S")
    b.tab(pinned, "https://a.example/", "A")
    b.tab(pinned, "https://b.example/", "B")
    raw = b.build()
    sb = Sidebar(raw)
    sb.items[pinned]["childrenIds"] = []
    assert [i.title for i in parse_space(sb, sb.spaces[0])] == ["A", "B"]


def test_depth_limit_merges_deeper_folders_into_their_parent_in_order():
    sb = Sidebar(fixturegen.deep_sidebar(levels=7, tabs_per_level=1))
    space = sb.spaces[0]
    limited = import_tree(sb, space, 5)
    node, levels = limited[0], 1
    while any(i.type == "folder" for i in node.items):
        node = next(i for i in node.items if i.type == "folder")
        levels += 1
    assert levels == 5 and node.title == "Level 5"
    assert [i.title for i in node.items] == ["L5 T0", "L6 T0", "L7 T0"]
    assert predict_zen_import(sb, space, 5).flattened_folders == 2
    assert predict_zen_import(sb, space, None).flattened_folders == 0


# --------------------------------------------------------------------------- prepare --target zen

@pytest.fixture
def zen_prepared(sidebar_file, tmp_path, capsys):
    plan = plan_from_json(zen_plan(sidebar_file, capsys))
    outcome = prepare(sidebar_file, plan, tmp_path / "out")
    return outcome, Sidebar.load(tmp_path / "out" / CLEANED_NAME), Sidebar.load(sidebar_file)


def test_prepare_zen_validates_and_records_the_target(zen_prepared):
    outcome, _, _ = zen_prepared
    assert outcome.validation.ok, outcome.validation.errors
    assert outcome.manifest["target"] == "zen" and outcome.manifest["max_folder_depth"] == 5
    assert "Zen, after:" in outcome.text and "Dia, after:" not in outcome.text
    assert all(not e.dia_note for e in outcome.result.ledger)
    assert any("Zen's importer skips it" in e.zen_note for e in outcome.result.ledger if not e.url)


def test_pinned_tab_groups_become_folders_in_place_and_split_views_stay(zen_prepared):
    _, out, src = zen_prepared
    research_src, research = space_by_title(src, "Research"), space_by_title(out, "Research")
    group = next(e for e in src.walk(research_src.pinned_id) if e.kind == KIND_TAB_GROUP)
    position = src.children(research_src.pinned_id).index(group.id)
    assert out.children(research.pinned_id)[position] == group.id
    assert out.kind(group.id) == KIND_FOLDER and out.items[group.id]["title"] == folder_title(group.obj)
    assert out.children(group.id) == src.children(group.id)
    assert not any(e.kind == KIND_TAB_GROUP for s in out.spaces for e in out.walk(s.pinned_id))
    work = space_by_title(out, "Work")
    assert any(e.kind == KIND_SPLIT for e in out.walk(work.pinned_id))  # Zen flattens it on its own


def test_open_tabs_land_in_a_pinned_folder_with_tab_groups_as_subfolders(zen_prepared):
    _, out, src = zen_prepared
    research = space_by_title(out, "Research")
    folder = next(c for c in out.children(research.pinned_id)
                  if out.kind(c) == KIND_FOLDER and out.items[c]["title"] == "Open tabs from Arc")
    src_groups = [folder_title(e.obj) for e in src.walk(space_by_title(src, "Research").unpinned_id)
                  if e.kind == KIND_TAB_GROUP]
    sub = [out.items[c]["title"] for c in out.children(folder) if out.kind(c) == KIND_FOLDER]
    assert sub == src_groups and len(sub) == 2


def test_zen_imports_every_tab_that_stays_in_the_sidebar(zen_prepared):
    """Through the importer port: nothing is left for Zen to drop or flatten, and every pinned
    tab of the cleaned file with a URL Zen accepts comes out of the import."""
    outcome, out, _ = zen_prepared
    for space in out.spaces:
        assert tree_json(import_tree(out, space, 5)) == tree_json(import_tree(out, space, None))
        p = predict_zen_import(out, space, 5)
        assert (p.tab_group_tabs_lost, p.flattened_folders) == (0, 0)
        expected = sorted(
            e.url for e in outcome.result.ledger
            if e.space_id == space.id and e.fate == FATE_SIDEBAR and zen_url(e.url)
            and (e.new_path or "").startswith("pinned")
        )
        got = []

        def walk(items):
            for i in items:
                if i.type == "tab":
                    got.append(i.url)
                else:
                    walk(i.items)

        walk(import_tree(out, space, 5))
        assert sorted(got) == expected
        assert p.open_tabs_lost == sum(1 for e in out.walk(space.unpinned_id) if out.kind(e.id) == "tab")


def test_prepare_merges_folders_past_the_depth_limit_like_zen(tmp_path, capsys):
    raw = fixturegen.deep_sidebar(levels=8, tabs_per_level=2)
    path = write_sidebar(tmp_path, raw)
    plan = plan_from_json(zen_plan(path, capsys))
    outcome = prepare(path, plan, tmp_path / "out")
    assert outcome.validation.ok, outcome.validation.errors
    out, src = Sidebar.load(tmp_path / "out" / CLEANED_NAME), Sidebar(raw)
    space = out.spaces[0]
    assert folder_depth(out, space.pinned_id) == 5
    # The cleaned file, imported without any limit, gives what Zen makes of the original with its limit.
    assert tree_json(import_tree(out, space, None)) == tree_json(import_tree(src, src.spaces[0], 5))
    assert "merged 3 folders deeper than 5 levels" in outcome.text

    unlimited = plan_from_json(zen_plan(path, capsys, "--max-folder-depth", "0"))
    prepare(path, unlimited, tmp_path / "out2")
    assert folder_depth(Sidebar.load(tmp_path / "out2" / CLEANED_NAME), space.pinned_id) == 8


def test_keep_leaves_pinned_tab_groups_and_warns(sidebar_file, tmp_path, capsys):
    plan = plan_from_json(zen_plan(sidebar_file, capsys, "--keep-pinned-wrappers"))
    outcome = prepare(sidebar_file, plan, tmp_path / "out")
    assert any("inside tab groups in the pinned area; Zen's importer drops these" in w
               for w in outcome.validation.warnings)
    research = next(s for s in outcome.result.spaces if s.title == "Research")
    assert research.after.tab_group_tabs_lost == 2


# --------------------------------------------------------------------------- swap, status, verify

def test_swap_prints_the_zen_steps_and_status_names_zen(zen_prepared, sidebar_file, tmp_path):
    outcome, _, _ = zen_prepared
    backups = tmp_path / "backups"
    lines = S.swap(sidebar_file, outcome.out_dir, backups, True, FakeChecker(False))
    text = "\n".join(lines)
    assert "Import into Zen now" in text and "zen.welcome-screen.seen = true" in text
    assert "Import into Dia" not in text and "zen.folders.max-subfolders" not in text  # 5 is Zen's default
    state = json.loads((backups / S.STATE_FILE).read_text())
    original = backups / state["active"] / S.ORIGINAL_NAME
    sidebar_file.write_bytes(original.read_bytes())
    status = "\n".join(S.status(sidebar_file, backups, FakeChecker(False)))
    assert "Do not run Zen's import now" in status
    S.restore(None, backups, True, FakeChecker(False))


def test_zen_steps_ask_for_the_same_depth_in_zen():
    text = "\n".join(S.zen_import_steps(None, 8))
    assert "zen.folders.max-subfolders = 8" in text
    assert "as deep as your folders go" in "\n".join(S.zen_import_steps(None, None))


def test_verify_says_it_reads_dia_only(zen_prepared, sidebar_file, capsys):
    outcome, _, _ = zen_prepared
    code, _, err = run(["verify", "--sidebar", str(sidebar_file), "--prepared", str(outcome.out_dir),
                        "--dia-dump", "/nonexistent"], capsys)
    assert code == 2 and "verify compares with Dia" in err


def test_untouched_split_views_inside_open_tabs_get_no_dia_warning(sidebar_file, tmp_path, capsys):
    plan = plan_from_json(zen_plan(sidebar_file, capsys, "--split-views", "keep"))
    outcome = prepare(sidebar_file, plan, tmp_path / "out")
    assert not any("Dia's importer" in w for w in outcome.validation.warnings)
    work = space_by_title(Sidebar.load(tmp_path / "out" / CLEANED_NAME), "Work")
    assert any(e.kind == KIND_SPLIT for e in Sidebar.load(tmp_path / "out" / CLEANED_NAME).walk(work.pinned_id))
