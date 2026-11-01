import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from arc2dia2zen.model import KIND_FOLDER, Sidebar, folder_title, tab_url  # noqa: E402
from arc2dia2zen.plan import Plan, SpacePlan  # noqa: E402
from arc2dia2zen.swap import ProcessChecker  # noqa: E402
import fixturegen  # noqa: E402


class FakeChecker(ProcessChecker):
    def __init__(self, running=False):
        self.running = running
        self.calls = []

    def is_running(self, name):
        self.calls.append(name)
        return self.running


@pytest.fixture
def rich_raw():
    return fixturegen.rich_sidebar()


@pytest.fixture
def rich_sb(rich_raw):
    return Sidebar(copy.deepcopy(rich_raw))


@pytest.fixture
def sidebar_file(tmp_path, rich_raw):
    """A synthetic Arc folder with StorableSidebar.json in it."""
    arc_dir = tmp_path / "Library" / "Application Support" / "Arc"
    arc_dir.mkdir(parents=True)
    path = arc_dir / "StorableSidebar.json"
    path.write_text(json.dumps(rich_raw), encoding="utf-8")
    return path


def make_plan(sb, per_space=None, **common):
    """A plan with the same options for every space, optionally overridden per space title."""
    per_space = per_space or {}
    spaces = []
    for s in sb.spaces:
        kw = dict(common)
        kw.update(per_space.get(s.title, {}))
        spaces.append(SpacePlan(space_id=s.id, title=s.title, **kw))
    return Plan(spaces=spaces)


def space_by_title(sb, title):
    for s in sb.spaces:
        if s.title == title:
            return s
    raise KeyError(title)


def pinned_children(sb, title):
    """[(kind, label)] for the direct children of a space's pinned container."""
    space = space_by_title(sb, title)
    out = []
    for cid in sb.children(space.pinned_id):
        obj = sb.items[cid]
        kind = sb.kind(cid)
        if kind == KIND_FOLDER:
            out.append(("folder", obj.get("title")))
        elif kind == "tab":
            out.append(("tab", tab_url(obj)))
        else:
            out.append((kind, obj.get("title")))
    return out


def folder_children_urls(sb, folder_id):
    return [tab_url(sb.items[c]) for c in sb.children(folder_id) if sb.kind(c) == "tab"]


def find_folder(sb, title, space_title=None):
    roots = [space_by_title(sb, space_title)] if space_title else sb.spaces
    hits = []
    for s in roots:
        for root in (s.pinned_id, s.unpinned_id):
            for e in sb.walk(root):
                if e.kind == KIND_FOLDER and folder_title(e.obj) == title:
                    hits.append(e.id)
    return hits
