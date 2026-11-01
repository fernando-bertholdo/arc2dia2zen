import json

import pytest

from arc2dia2zen import dia as D
from arc2dia2zen.cli import main
from arc2dia2zen.fsutil import sha256_file
from arc2dia2zen.model import Sidebar
from conftest import FakeChecker
from test_verify import dump_text, simulate_dia_import


def run(argv, capsys, **kw):
    code = main(argv, **kw)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_scan_text_and_json(sidebar_file, capsys):
    code, out, _ = run(["scan", "--sidebar", str(sidebar_file)], capsys)
    assert code == 0 and 'Space 1/4: "Work"' in out
    code, out, _ = run(["scan", "--sidebar", str(sidebar_file), "--json"], capsys)
    data = json.loads(out)
    assert data["spaces"][0]["folders"] == 17


def test_scan_tree(sidebar_file, capsys):
    code, out, _ = run(["scan", "--sidebar", str(sidebar_file), "--tree"], capsys)
    assert code == 0 and "[Libraries] (2 tabs)" in out and "<split view>" in out


def test_missing_sidebar_is_a_clear_error(tmp_path, capsys):
    code, _, err = run(["scan", "--sidebar", str(tmp_path / "nope.json")], capsys)
    assert code == 2 and "not found" in err


def test_plan_file_and_options(tmp_path, sidebar_file, capsys):
    plan_path = tmp_path / "plan.json"
    code, out, _ = run([
        "plan", "--sidebar", str(sidebar_file), "-o", str(plan_path),
        "--strategy", "merge-to-top", "--space", "Personal=keep",
        "--override", "Imported From Edge=to-bookmarks", "--exclude-space", "Space 3",
        "--dedupe-urls", "--open-tabs-folder", "From Arc", "--non-web", "to-bookmarks",
    ], capsys)
    assert code == 0, out
    assert "what Dia would get" in out
    data = json.loads(plan_path.read_text())
    work, personal, third, research = data["spaces"]
    assert work["folder_strategy"] == "merge-to-top" and personal["folder_strategy"] == "keep"
    assert work["folder_overrides"] == [{"path": "Imported From Edge", "strategy": "to-bookmarks"}]
    assert personal["folder_overrides"] == []
    assert research["folder_overrides"] == [{"path": "Imported From Edge", "strategy": "to-bookmarks"}]
    assert third["import"] is False
    assert work["dedupe_urls"] is True and work["open_tabs_folder_name"] == "From Arc"
    assert "what_if_folder_strategy" in work["_about"]
    # Refuses to overwrite without --force.
    code, _, err = run(["plan", "--sidebar", str(sidebar_file), "-o", str(plan_path)], capsys)
    assert code == 2 and "--force" in err


def test_plan_default_strategy_depends_on_nesting(sidebar_file, capsys):
    code, out, _ = run(["plan", "--sidebar", str(sidebar_file), "--print"], capsys)
    data = json.loads(out)
    assert [s["folder_strategy"] for s in data["spaces"]] == ["path-names", "path-names", "keep", "path-names"]


def test_plan_rejects_unknown_override(sidebar_file, capsys):
    code, _, err = run(["plan", "--sidebar", str(sidebar_file), "--print", "--override", "Nope=keep"], capsys)
    assert code == 2 and "no pinned folder matches" in err


def test_edited_plan_with_a_typo(tmp_path, sidebar_file, capsys):
    plan_path = tmp_path / "plan.json"
    run(["plan", "--sidebar", str(sidebar_file), "-o", str(plan_path)], capsys)
    data = json.loads(plan_path.read_text())
    data["spaces"][0]["folder_strategy"] = "pathnames"
    plan_path.write_text(json.dumps(data))
    code, _, err = run(["prepare", "--sidebar", str(sidebar_file), "--plan", str(plan_path),
                        "-o", str(tmp_path / "out")], capsys)
    assert code == 2 and "'pathnames' is not one of" in err
    data["spaces"][0]["folder_strategy"] = "keep"
    data["spaces"][0]["dedup_trees"] = True
    plan_path.write_text(json.dumps(data))
    code, _, err = run(["prepare", "--sidebar", str(sidebar_file), "--plan", str(plan_path),
                        "-o", str(tmp_path / "out")], capsys)
    assert code == 2 and "unknown key(s) dedup_trees" in err


def test_full_workflow(tmp_path, sidebar_file, capsys):
    plan_path = tmp_path / "plan.json"
    out_dir = tmp_path / "out"
    backups = tmp_path / "backups"
    original_sha = sha256_file(sidebar_file)

    assert run(["plan", "--sidebar", str(sidebar_file), "-o", str(plan_path)], capsys)[0] == 0
    code, out, _ = run(["prepare", "--sidebar", str(sidebar_file), "--plan", str(plan_path), "-o", str(out_dir)], capsys)
    assert code == 0 and "Validation: OK" in out

    swap_args = ["swap", "--sidebar", str(sidebar_file), "--prepared", str(out_dir), "--backup-dir", str(backups)]
    code, _, err = run(swap_args + ["--apply"], capsys, checker=FakeChecker(True))
    assert code == 2 and "Arc is running" in err
    code, out, _ = run(swap_args, capsys, checker=FakeChecker(False))
    assert code == 0 and "Dry run" in out and sha256_file(sidebar_file) == original_sha
    code, out, _ = run(swap_args + ["--apply"], capsys, checker=FakeChecker(False))
    assert code == 0 and "Import from Another Browser" in out
    assert sha256_file(sidebar_file) != original_sha

    code, out, _ = run(["status", "--sidebar", str(sidebar_file), "--backup-dir", str(backups)], capsys,
                       checker=FakeChecker(False))
    assert "ACTIVE SWAP" in out

    # Dia imports the cleaned file (simulated), then the original goes back.
    cleaned = Sidebar.load(sidebar_file)
    dump = tmp_path / "dia-dump.txt"
    dump.write_text(dump_text(simulate_dia_import(cleaned)))

    code, out, _ = run(["restore", "--backup-dir", str(backups), "--apply"], capsys, checker=FakeChecker(False))
    assert code == 0 and sha256_file(sidebar_file) == original_sha

    code, out, _ = run(["verify", "--sidebar", str(sidebar_file), "--prepared", str(out_dir),
                        "--dia-dump", str(dump)], capsys)
    assert code == 0 and "every difference is explained" in out
    code, out, _ = run(["verify", "--sidebar", str(sidebar_file), "--prepared", str(out_dir),
                        "--dia-dump", str(dump), "--json"], capsys)
    assert json.loads(out)["ok"] is True


def test_verify_uses_injected_source(sidebar_file, capsys):
    sb = Sidebar.load(sidebar_file)

    class Source(D.DiaSource):
        def read(self):
            return simulate_dia_import(sb)

    code, out, _ = run(["verify", "--sidebar", str(sidebar_file)], capsys, dia_source=Source())
    assert code == 0 and "expected loss: 5 open tabs" in out


def test_verify_print_script(capsys):
    code, out, _ = run(["verify", "--print-script"], capsys)
    assert code == 0 and 'tell application "Dia"' in out


def test_prepare_without_plan_uses_options(tmp_path, sidebar_file, capsys):
    code, out, _ = run(["prepare", "--sidebar", str(sidebar_file), "-o", str(tmp_path / "out"),
                        "--strategy", "to-bookmarks", "--open-tabs", "bookmarks"], capsys)
    assert code == 0 and (tmp_path / "out" / "bookmarks.html").exists()
    code, out, _ = run(["prepare", "--sidebar", str(sidebar_file), "-o", str(tmp_path / "out"), "--json"], capsys)
    assert code == 2


def test_no_command_shows_help(capsys):
    code, out, _ = run([], capsys)
    assert code == 2 and "scan" in out


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "arc2dia2zen" in capsys.readouterr().out


def test_plan_options_with_plan_file_are_reported(tmp_path, sidebar_file, capsys):
    plan_path = tmp_path / "plan.json"
    run(["plan", "--sidebar", str(sidebar_file), "-o", str(plan_path)], capsys)
    code, _, err = run(["prepare", "--sidebar", str(sidebar_file), "--plan", str(plan_path),
                        "-o", str(tmp_path / "out"), "--dedupe-urls"], capsys)
    assert code == 0 and "--dedupe-urls is ignored" in err


def test_verify_after_allow_other_source_uses_the_prepared_files(tmp_path, sidebar_file, capsys):
    """The synthetic example goes through swap --allow-other-source while the user's own Arc file
    (here another synthetic sidebar with 6 favorites) is backed up and restored. verify --prepared
    must compare Favorites with the cleaned file, not with the restored Arc file."""
    import fixturegen

    example = tmp_path / "example.json"
    example.write_bytes(sidebar_file.read_bytes())
    own = fixturegen.Builder(seed="own-arc")
    _, pinned, _ = own.space("Mine")
    own.tab(pinned, "https://mine.example.com/")
    fav = own.favorites()
    for n in range(6):
        own.tab(fav, f"https://own-favorite-{n}.example.com/")
    sidebar_file.write_text(json.dumps(own.build()))
    own_sha = sha256_file(sidebar_file)
    out_dir, backups = tmp_path / "out", tmp_path / "backups"

    assert run(["prepare", "--sidebar", str(example), "-o", str(out_dir)], capsys)[0] == 0
    swap_args = ["swap", "--sidebar", str(sidebar_file), "--prepared", str(out_dir), "--backup-dir", str(backups),
                 "--apply"]
    code, out, _ = run(swap_args + ["--allow-other-source"], capsys, checker=FakeChecker(False))
    assert code == 0 and "TEST MODE" in out
    dump = tmp_path / "dia-dump.txt"
    dump.write_text(dump_text(simulate_dia_import(Sidebar.load(sidebar_file))))
    assert run(["restore", "--backup-dir", str(backups), "--apply"], capsys, checker=FakeChecker(False))[0] == 0
    assert sha256_file(sidebar_file) == own_sha

    code, out, _ = run(["verify", "--sidebar", str(sidebar_file), "--prepared", str(out_dir),
                        "--dia-dump", str(dump)], capsys)
    assert code == 0, out
    assert "is not the file this migration was prepared from" in out and str(example) in out
    assert "Favorites: 3 web favorites in Arc" in out and "not found in Dia" not in out
    assert "every difference is explained" in out

    # Without the example file, the cleaned file explains the differences.
    example.rename(tmp_path / "moved.json")
    code, out, _ = run(["verify", "--sidebar", str(sidebar_file), "--prepared", str(out_dir),
                        "--dia-dump", str(dump)], capsys)
    assert code == 0 and "explained with the cleaned file" in out


def test_keep_pinned_wrappers_option(tmp_path, sidebar_file, capsys):
    code, out, _ = run(["plan", "--sidebar", str(sidebar_file), "--print", "--keep-pinned-wrappers"], capsys)
    data = json.loads(out)
    assert code == 0 and {s["pinned_wrappers"] for s in data["spaces"]} == {"keep"}
    assert data["spaces"][0]["_about"]["pinned_wrapped_tabs"] == 2
    code, out, _ = run(["plan", "--sidebar", str(sidebar_file), "-o", str(tmp_path / "plan.json")], capsys)
    assert code == 0 and "pinned split views and tab groups: unwrap (1 split view holding 2 tabs" in out
    assert json.loads((tmp_path / "plan.json").read_text())["spaces"][0]["pinned_wrappers"] == "unwrap"
    code, out, _ = run(["prepare", "--sidebar", str(sidebar_file), "-o", str(tmp_path / "out"),
                        "--keep-pinned-wrappers"], capsys)
    assert code == 0 and "unwrapped" not in out
    assert "warning: 4 tabs still sit inside split views or tab groups in the pinned area" in out
    code, out, _ = run(["prepare", "--sidebar", str(sidebar_file), "-o", str(tmp_path / "out2")], capsys)
    assert code == 0 and "unwrapped 1 split view in the pinned area" in out
    assert 'unwrapped 1 tab group in the pinned area' in out and '"Reading list" at the top level, 2 tabs' in out
    assert "still sit inside" not in out
