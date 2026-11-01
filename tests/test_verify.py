import re

import pytest

from arc2dia2zen import dia as D
from arc2dia2zen.errors import Arc2DiaError
from arc2dia2zen.model import Sidebar, tab_url
from arc2dia2zen.transform import transform
from arc2dia2zen.verify import is_github_pull, render_verify, verify
from conftest import make_plan, space_by_title
import fixturegen


def simulate_dia_import(sb, rename=None, drop=(), favorites=True):
    """What Dia's importer is observed to do: one profile per space with the pinned tabs,
    http(s) only, no open tabs, none of the tabs inside pinned split views or tab groups;
    Favorites show up as isPinned tabs."""
    rename = rename or {}
    fav_tabs = []
    if favorites:
        for _, e in sb.favorite_entries():
            if e.kind == "tab":
                fav_tabs.append(D.DiaTab(tab_url(e.obj), e.obj.get("title") or "", True))
    profiles = [D.DiaProfile(1, "Default", [D.DiaTab("dia://newtab", "New Tab", False)])]
    for n, space in enumerate(sb.spaces, 2):
        tabs = list(fav_tabs)
        wrapped = sb.wrapped_ids(space.pinned_id)
        for e in sb.walk(space.pinned_id):
            url = tab_url(e.obj) if e.kind == "tab" else None
            if url and url.startswith("http") and url not in drop and e.id not in wrapped:
                tabs.append(D.DiaTab(url, e.obj.get("title") or "", False))
        profiles.append(D.DiaProfile(n, rename.get(space.title, space.title), tabs))
    return profiles


def test_prepared_import_verifies_clean(rich_sb):
    cleaned = transform(rich_sb, make_plan(rich_sb, folder_strategy="path-names")).sidebar
    report = verify(cleaned, simulate_dia_import(cleaned), original_sb=rich_sb, mode="prepared")
    assert report.ok
    work = report.spaces[0]
    assert work.profile_name == "Work" and work.match_method == "name"
    assert work.matched == work.expected
    assert work.expected_losses == {"non-web URLs (Dia drops them)": 5}
    assert report.unmatched_profiles == ["Default"]
    assert report.favorites_missing == []
    assert "OK" in render_verify(report)


def test_original_mode_explains_lost_open_tabs(rich_sb):
    report = verify(rich_sb, simulate_dia_import(rich_sb), mode="original")
    assert report.ok
    work = report.spaces[0]
    assert work.expected_losses["open tabs (Dia's importer skips them)"] == 5  # web ones; data:/about: aside
    assert work.expected_losses["non-web URLs (Dia drops them)"] == 4


def test_tabs_dropped_from_pinned_wrappers_are_explained(rich_sb):
    """Without prepare, the split view's tabs (Work) and the pinned tab group's (Research) are lost."""
    report = verify(rich_sb, simulate_dia_import(rich_sb), mode="original")
    assert report.ok
    by_space = {s.space_title: s for s in report.spaces}
    wrapped = {s: sorted(d.url for d in v.missing if "split view or tab group" in d.explanation)
               for s, v in by_space.items()}
    assert wrapped["Work"] == ["https://docs.example.com/a", "https://docs.example.com/b"]
    assert wrapped["Research"] == ["https://papers.example.org/1", "https://papers.example.org/2"]
    text = render_verify(report)
    assert "~ https://docs.example.com/a: was inside a pinned split view or tab group" in text
    assert "MISSING" not in text


def test_favorites_come_from_the_prepared_file(rich_sb):
    """After restore, or with swap --allow-other-source, the current Arc file may hold other
    Favorites than the file Dia imported; verify --prepared must use the cleaned file's."""
    cleaned = transform(rich_sb, make_plan(rich_sb, folder_strategy="path-names")).sidebar
    other = fixturegen.Builder(seed="someone-else")
    other.space("Mine")
    fav = other.favorites()
    for n in range(6):
        other.tab(fav, f"https://own-favorite-{n}.example.com/")
    current = Sidebar(other.build())
    profiles = simulate_dia_import(cleaned)
    report = verify(cleaned, profiles, original_sb=current, mode="prepared")
    assert report.favorites_missing == [] and report.favorites_arc == 3
    assert report.ok
    # Comparing with the original (no --prepared) still uses the Arc file given.
    report = verify(current, profiles, original_sb=current, mode="original")
    assert report.favorites_arc == 6 and len(report.favorites_missing) == 6


def github_pull_sidebar():
    b = fixturegen.Builder(seed="github")
    _, pinned, _ = b.space("Code")
    b.tab(pinned, "https://github.com/example-org/example-repo/pull/42", "Fix the parser")
    b.tab(pinned, "https://github.com/example-org/example-repo/pull/7/files", "Review")
    b.tab(pinned, "https://github.com/example-org/example-repo/issues/3", "An issue")
    b.tab(pinned, "https://code.example.com/", "Code host")
    return Sidebar(b.build())


def test_github_pull_requests_are_expected_probably():
    sb = github_pull_sidebar()
    pulls = ("https://github.com/example-org/example-repo/pull/42",
             "https://github.com/example-org/example-repo/pull/7/files")
    report = verify(sb, simulate_dia_import(sb, drop=pulls))
    assert report.ok
    code = report.spaces[0]
    assert code.unexplained_missing == 0
    assert sorted(d.url for d in code.missing if d.probable) == sorted(pulls)
    text = render_verify(report)
    assert ("expected, probably (Dia may show GitHub pull requests as Live items instead of tabs): 2" in text)
    assert "MISSING" not in text and "OK, every difference is explained" in text


def test_github_issue_and_other_urls_are_still_missing():
    sb = github_pull_sidebar()
    report = verify(sb, simulate_dia_import(sb, drop=("https://github.com/example-org/example-repo/pull/42",
                                                      "https://github.com/example-org/example-repo/issues/3",
                                                      "https://code.example.com/")))
    assert not report.ok
    code = report.spaces[0]
    assert code.unexplained_missing == 2
    assert sorted(d.url for d in code.missing if not d.expected) == [
        "https://code.example.com/", "https://github.com/example-org/example-repo/issues/3",
    ]
    text = render_verify(report)
    assert "MISSING in Dia: 2" in text and "expected, probably" in text


@pytest.mark.parametrize("url, hit", [
    ("https://github.com/o/r/pull/1", True),
    ("http://www.github.com/o/r/pull/12?diff=split", True),
    ("https://GitHub.com/o/r/pull/3#discussion", True),
    ("https://github.com/o/r/pulls", False),
    ("https://github.com/o/r/pull/new/branch", False),
    ("https://gist.github.com/o/r/pull/1", False),
    ("https://example.com/github.com/o/r/pull/1", False),
])
def test_is_github_pull(url, hit):
    assert is_github_pull(url) is hit


def test_missing_url_is_flagged(rich_sb):
    profiles = simulate_dia_import(rich_sb, drop=("https://beta.example.com/",))
    report = verify(rich_sb, profiles)
    assert not report.ok
    work = report.spaces[0]
    assert work.unexplained_missing == 1
    assert "MISSING in Dia: 1" in render_verify(report)


def test_trailing_slash_difference_is_explained(rich_sb):
    profiles = simulate_dia_import(rich_sb)
    work = [p for p in profiles if p.name == "Work"][0]
    for t in work.tabs:
        if t.url == "https://alpha.example.com/board":
            t.url = "https://alpha.example.com/board/"
    report = verify(rich_sb, profiles)
    assert report.ok
    diff = [d for d in report.spaces[0].missing if "slightly different" in d.explanation]
    assert len(diff) == 1


def test_extra_tabs_are_classified(rich_sb):
    profiles = simulate_dia_import(rich_sb)
    work = [p for p in profiles if p.name == "Work"][0]
    work.tabs.append(D.DiaTab("https://mail.example.com/inbox", "Inbox", False))
    work.tabs.append(D.DiaTab("https://bank.example.com/", "Bank", False))
    work.tabs.append(D.DiaTab("https://elsewhere.example.com/", "?", False))
    work.tabs.append(D.DiaTab("chrome://settings", "Settings", False))
    report = verify(rich_sb, profiles)
    reasons = {d.url: d.explanation for d in report.spaces[0].extra}
    assert reasons["https://mail.example.com/inbox"] == "an Arc open tab of this space"
    assert reasons["https://bank.example.com/"] == "an Arc favorite"
    assert reasons["https://elsewhere.example.com/"].startswith("not in the Arc sidebar")
    assert reasons["chrome://settings"].startswith("browser page")
    assert report.ok  # extras never fail the check


def test_name_suffix_and_best_overlap(rich_sb):
    profiles = simulate_dia_import(rich_sb, rename={"Work": "Work 2"})
    profiles.append(D.DiaProfile(9, "Work", [D.DiaTab("https://old.example.com/", "old", False)]))
    report = verify(rich_sb, profiles)
    work = report.spaces[0]
    # Two profiles fit the name; the one holding the space's URLs wins.
    assert work.profile_name == "Work 2" and work.match_method == "name, best URL overlap"
    assert report.ok and "Work" in report.unmatched_profiles
    report = verify(rich_sb, profiles, mapping={"Work": "Work"})
    assert report.spaces[0].profile_name == "Work" and not report.ok
    report = verify(rich_sb, profiles, mapping={"Work": "Work 2"})
    assert report.spaces[0].profile_name == "Work 2" and report.spaces[0].match_method == "map"
    assert report.ok


def test_suffix_match_when_exact_name_is_absent(rich_sb):
    profiles = simulate_dia_import(rich_sb, rename={"Personal": "Personal (2)"})
    report = verify(rich_sb, profiles)
    assert report.spaces[1].profile_name == "Personal (2)"


def test_content_match_when_names_differ(rich_sb):
    profiles = simulate_dia_import(rich_sb, rename={"Work": "Arbeit"})
    report = verify(rich_sb, profiles)
    assert report.spaces[0].profile_name == "Arbeit"
    assert report.spaces[0].match_method == "URL overlap (names differ)"


def test_unmatched_space(rich_sb):
    profiles = [p for p in simulate_dia_import(rich_sb) if p.name != "Personal"]
    report = verify(rich_sb, profiles)
    assert report.spaces[1].profile_name is None and not report.ok
    assert '--map "Personal=<Dia profile>"' in render_verify(report)


# --------------------------------------------------------------------------- osascript interface

def dump_text(profiles):
    rows = []
    for k, p in enumerate(profiles, 1):
        rows.append(D.US.join(["P", str(k), p.name]))
        for t in p.tabs:
            rows.append(D.US.join(["T", str(k), "true" if t.is_pinned else "false", t.url, t.title]))
    return D.RS.join(rows) + "\n"


def test_parse_dump_round_trip(rich_sb):
    profiles = simulate_dia_import(rich_sb)
    parsed = D.parse_dump(dump_text(profiles))
    assert [p.name for p in parsed] == [p.name for p in profiles]
    assert [len(p.tabs) for p in parsed] == [len(p.tabs) for p in profiles]
    assert parsed[1].tabs[0].is_pinned is True


def test_parse_dump_errors():
    with pytest.raises(Arc2DiaError, match="not running"):
        D.parse_dump("ERR" + D.US + "not-running")
    with pytest.raises(Arc2DiaError, match="no open window"):
        D.parse_dump("ERR" + D.US + "no-window")
    assert D.parse_dump("") == []


class FakeRunner(D.Runner):
    def __init__(self, output):
        self.output = output
        self.scripts = []

    def run(self, script):
        self.scripts.append(script)
        return self.output


def test_osascript_source_uses_runner(rich_sb):
    runner = FakeRunner(dump_text(simulate_dia_import(rich_sb)))
    profiles = D.OsascriptDiaSource(runner).read()
    assert len(profiles) == 5
    assert runner.scripts == [D.READ_SCRIPT]


def test_read_script_is_read_only():
    script = D.READ_SCRIPT
    for verb in ("make", "close", "focus", "move", "execute", "delete", "open location", "reload", "activate"):
        assert not re.search(r"\b%s\b" % verb, script), verb
    assert 'tell application "Dia"' in script
    assert "character id 31" in script and "character id 30" in script
    assert "is not running" in script  # does not launch Dia


def test_runner_needs_macos(monkeypatch):
    monkeypatch.setattr(D.sys, "platform", "linux")
    with pytest.raises(Arc2DiaError, match="macOS"):
        D.OsascriptRunner().run("return 1")
