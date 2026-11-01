"""The plan file: per-space choices that ``prepare`` applies. Stored as JSON.

Keys that start with an underscore are informational (counts, what-if numbers, folder
lists) and are ignored when the plan is loaded, so they can be left in place or deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import PlanError

PLAN_VERSION = 1

FOLDER_STRATEGIES = ("keep", "path-names", "merge-to-top", "to-bookmarks")
OPEN_TABS_MODES = ("pinned-folder", "bookmarks", "leave")
SPLIT_VIEW_MODES = ("flatten", "keep")
TAB_GROUP_MODES = ("flatten", "subfolders")
PINNED_WRAPPER_MODES = ("unwrap", "keep")
NON_WEB_MODES = ("keep", "to-bookmarks")

DEFAULT_OPEN_TABS_FOLDER = "Open tabs from Arc"
DEFAULT_PATH_SEPARATOR = " › "

STRATEGY_HELP = {
    "keep": "leave folders as they are; Dia turns every folder, nested or not, into its own flat pinned group",
    "path-names": 'one flat group per folder that holds tabs, named after its full path ("A › B › C")',
    "merge-to-top": "one group per top-level folder; tabs from its subfolders are merged into it",
    "to-bookmarks": "take the folder out of the sidebar and export it, nested, to bookmarks.html",
}


@dataclass
class FolderOverride:
    strategy: str
    folder_id: Optional[str] = None
    path: Optional[str] = None  # folder titles from the top, joined by " › " or " > "

    def to_json(self) -> dict:
        out: Dict[str, Any] = {}
        if self.folder_id is not None:
            out["folder_id"] = self.folder_id
        if self.path is not None:
            out["path"] = self.path
        out["strategy"] = self.strategy
        return out


@dataclass
class SpacePlan:
    space_id: str
    title: str = ""
    import_space: bool = True
    folder_strategy: str = "keep"
    folder_overrides: List[FolderOverride] = field(default_factory=list)
    dedupe_trees: bool = True
    dedupe_urls: bool = False
    open_tabs: str = "pinned-folder"
    open_tabs_folder_name: str = DEFAULT_OPEN_TABS_FOLDER
    open_tabs_split_views: str = "flatten"
    open_tabs_tab_groups: str = "flatten"
    pinned_wrappers: str = "unwrap"
    drop_empty_folders: bool = True
    non_web: str = "keep"
    merge_bookmark_folders: bool = True

    def to_json(self) -> dict:
        return {
            "space_id": self.space_id,
            "title": self.title,
            "import": self.import_space,
            "folder_strategy": self.folder_strategy,
            "folder_overrides": [o.to_json() for o in self.folder_overrides],
            "dedupe_trees": self.dedupe_trees,
            "dedupe_urls": self.dedupe_urls,
            "open_tabs": self.open_tabs,
            "open_tabs_folder_name": self.open_tabs_folder_name,
            "open_tabs_split_views": self.open_tabs_split_views,
            "open_tabs_tab_groups": self.open_tabs_tab_groups,
            "pinned_wrappers": self.pinned_wrappers,
            "drop_empty_folders": self.drop_empty_folders,
            "non_web": self.non_web,
            "merge_bookmark_folders": self.merge_bookmark_folders,
        }


def unchanged_space_plan(space_id: str, title: str = "") -> SpacePlan:
    """A plan that leaves a space exactly as it is."""
    return SpacePlan(
        space_id=space_id,
        title=title,
        folder_strategy="keep",
        dedupe_trees=False,
        dedupe_urls=False,
        open_tabs="leave",
        pinned_wrappers="keep",
        drop_empty_folders=False,
        non_web="keep",
    )


@dataclass
class Plan:
    spaces: List[SpacePlan]
    source_path: str = ""
    source_sha256: str = ""
    path_separator: str = DEFAULT_PATH_SEPARATOR
    version: int = PLAN_VERSION

    def for_space(self, space_id: str) -> Optional[SpacePlan]:
        for sp in self.spaces:
            if sp.space_id == space_id:
                return sp
        return None

    def to_json(self) -> dict:
        return {
            "arc2dia2zen_plan_version": self.version,
            "source": {"path": self.source_path, "sha256": self.source_sha256},
            "path_separator": self.path_separator,
            "spaces": [sp.to_json() for sp in self.spaces],
        }


# --------------------------------------------------------------------------- loading

_SPACE_KEYS = {
    "space_id", "title", "import", "folder_strategy", "folder_overrides", "dedupe_trees", "dedupe_urls",
    "open_tabs", "open_tabs_folder_name", "open_tabs_split_views", "open_tabs_tab_groups", "pinned_wrappers",
    "drop_empty_folders", "non_web", "merge_bookmark_folders",
}
# "arc2dia_plan_version" is the key plans had before the rename to arc2dia2zen (0.2.0).
_TOP_KEYS = {"arc2dia2zen_plan_version", "arc2dia_plan_version", "source", "path_separator", "spaces"}


def _check_keys(obj: dict, allowed: set, where: str) -> None:
    unknown = sorted(k for k in obj if not k.startswith("_") and k not in allowed)
    if unknown:
        raise PlanError(f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}")


def _enum(value: Any, allowed, where: str) -> str:
    if value not in allowed:
        raise PlanError(f"{where}: {value!r} is not one of {', '.join(allowed)}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise PlanError(f"{where}: expected true or false, got {value!r}")
    return value


def _str(value: Any, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise PlanError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def space_plan_from_json(obj: Any, where: str) -> SpacePlan:
    if not isinstance(obj, dict):
        raise PlanError(f"{where}: expected an object")
    _check_keys(obj, _SPACE_KEYS, where)
    if "space_id" not in obj:
        raise PlanError(f"{where}: missing space_id")
    sp = SpacePlan(space_id=_str(obj["space_id"], f"{where}.space_id"))
    sp.title = obj.get("title") if isinstance(obj.get("title"), str) else ""
    if "import" in obj:
        sp.import_space = _bool(obj["import"], f"{where}.import")
    if "folder_strategy" in obj:
        sp.folder_strategy = _enum(obj["folder_strategy"], FOLDER_STRATEGIES, f"{where}.folder_strategy")
    overrides = obj.get("folder_overrides", [])
    if not isinstance(overrides, list):
        raise PlanError(f"{where}.folder_overrides: expected a list")
    for i, ov in enumerate(overrides):
        w = f"{where}.folder_overrides[{i}]"
        if not isinstance(ov, dict):
            raise PlanError(f"{w}: expected an object")
        _check_keys(ov, {"folder_id", "path", "strategy"}, w)
        if ("folder_id" in ov) == ("path" in ov):
            raise PlanError(f"{w}: give exactly one of folder_id or path")
        sp.folder_overrides.append(
            FolderOverride(
                strategy=_enum(ov.get("strategy"), FOLDER_STRATEGIES, f"{w}.strategy"),
                folder_id=_str(ov["folder_id"], f"{w}.folder_id") if "folder_id" in ov else None,
                path=_str(ov["path"], f"{w}.path") if "path" in ov else None,
            )
        )
    for key in ("dedupe_trees", "dedupe_urls", "drop_empty_folders", "merge_bookmark_folders"):
        if key in obj:
            setattr(sp, key, _bool(obj[key], f"{where}.{key}"))
    if "open_tabs" in obj:
        sp.open_tabs = _enum(obj["open_tabs"], OPEN_TABS_MODES, f"{where}.open_tabs")
    if "open_tabs_folder_name" in obj:
        sp.open_tabs_folder_name = _str(obj["open_tabs_folder_name"], f"{where}.open_tabs_folder_name")
    if "open_tabs_split_views" in obj:
        sp.open_tabs_split_views = _enum(obj["open_tabs_split_views"], SPLIT_VIEW_MODES, f"{where}.open_tabs_split_views")
    if "open_tabs_tab_groups" in obj:
        sp.open_tabs_tab_groups = _enum(obj["open_tabs_tab_groups"], TAB_GROUP_MODES, f"{where}.open_tabs_tab_groups")
    if "pinned_wrappers" in obj:
        sp.pinned_wrappers = _enum(obj["pinned_wrappers"], PINNED_WRAPPER_MODES, f"{where}.pinned_wrappers")
    if "non_web" in obj:
        sp.non_web = _enum(obj["non_web"], NON_WEB_MODES, f"{where}.non_web")
    return sp


def plan_from_json(data: Any) -> Plan:
    if not isinstance(data, dict):
        raise PlanError("plan: expected a JSON object")
    _check_keys(data, _TOP_KEYS, "plan")
    version = data.get("arc2dia2zen_plan_version", data.get("arc2dia_plan_version"))
    if version != PLAN_VERSION:
        raise PlanError(f"plan: arc2dia2zen_plan_version must be {PLAN_VERSION}, got {version!r}")
    spaces_raw = data.get("spaces")
    if not isinstance(spaces_raw, list):
        raise PlanError("plan: spaces must be a list")
    spaces = [space_plan_from_json(s, f"spaces[{i}]") for i, s in enumerate(spaces_raw)]
    seen = set()
    for sp in spaces:
        if sp.space_id in seen:
            raise PlanError(f"plan: space {sp.space_id} is listed twice")
        seen.add(sp.space_id)
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    sep = data.get("path_separator", DEFAULT_PATH_SEPARATOR)
    return Plan(
        spaces=spaces,
        source_path=str(source.get("path") or ""),
        source_sha256=str(source.get("sha256") or ""),
        path_separator=_str(sep, "plan.path_separator", allow_empty=False) if sep != DEFAULT_PATH_SEPARATOR else sep,
    )


def load_plan(path) -> Plan:
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"cannot read plan {path}: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"plan {path} is not valid JSON: {exc}") from None
    return plan_from_json(data)


def split_folder_path(path: str) -> List[str]:
    """Split an override path written with " › " or " > " between folder titles."""
    for sep in ("›", ">"):
        if sep in path:
            return [p.strip() for p in path.split(sep)]
    return [path.strip()]
