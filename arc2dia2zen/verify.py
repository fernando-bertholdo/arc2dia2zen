"""``verify``: compare what Dia has (read through osascript, read-only) with what it should have."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import urls as U
from .dia import DiaProfile
from .model import KIND_TAB, Sidebar, Space, tab_url

INTERNAL_SCHEMES = ("dia", "chrome", "about", "chrome-extension", "chrome-search", "devtools", "arc", "")

# Dia turns some GitHub pull-request tabs into "Live" items, which its AppleScript does not list as
# tabs (seen once in a real migration). Such a URL missing from Dia is probably expected.
GITHUB_PULL = re.compile(r"^https?://(www\.)?github\.com/[^/?#]+/[^/?#]+/pull/\d+(?:[/?#].*)?$", re.I)
WRAPPED_EXPLANATION = ("was inside a pinned split view or tab group; Dia's importer drops these "
                       "(seen in a real migration test)")
GITHUB_PULL_EXPLANATION = "Dia may show GitHub pull requests as Live items instead of tabs"


@dataclass
class UrlDiff:
    url: str
    count: int
    explanation: str
    expected: bool = True  # False when the difference is unexplained
    probable: bool = False  # explained, but only probably (listed under "expected, probably")


@dataclass
class SpaceVerify:
    space_id: str
    space_title: str
    profile_name: Optional[str]
    match_method: Optional[str]
    expected: int
    found: int
    matched: int
    missing: List[UrlDiff] = field(default_factory=list)
    extra: List[UrlDiff] = field(default_factory=list)
    expected_losses: Dict[str, int] = field(default_factory=dict)

    @property
    def unexplained_missing(self) -> int:
        return sum(d.count for d in self.missing if not d.expected)


@dataclass
class VerifyReport:
    mode: str  # "prepared" (compare with the cleaned file) or "original"
    spaces: List[SpaceVerify]
    unmatched_profiles: List[str]
    favorites_arc: int
    favorites_dia: int
    favorites_missing: List[str]
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.profile_name is not None and s.unexplained_missing == 0 for s in self.spaces)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        for s_obj, s_dict in zip(self.spaces, data["spaces"]):
            s_dict["unexplained_missing"] = s_obj.unexplained_missing
        return data


def _web_counter(sb: Sidebar, root: Optional[str]) -> Counter:
    c: Counter = Counter()
    for e in sb.walk(root):
        if e.kind == KIND_TAB:
            url = tab_url(e.obj)
            if url and U.is_web(url):
                c[U.normalize(url)] += 1
    return c


def _wrapped_web_counter(sb: Sidebar, root: Optional[str]) -> Counter:
    """Web URLs of the tabs inside split views or tab groups below ``root``."""
    c: Counter = Counter()
    for item_id in sb.wrapped_ids(root):
        if sb.kind(item_id) == KIND_TAB:
            url = tab_url(sb.items[item_id])
            if url and U.is_web(url):
                c[U.normalize(url)] += 1
    return c


def is_github_pull(url: str) -> bool:
    return bool(GITHUB_PULL.match(url or ""))


def _non_web_count(sb: Sidebar, root: Optional[str]) -> int:
    return sum(1 for e in sb.walk(root) if e.kind == KIND_TAB and tab_url(e.obj) and not U.is_web(tab_url(e.obj)))


def _name_candidates(title: str, profiles: List[DiaProfile]) -> List[Tuple[DiaProfile, int]]:
    """Profiles whose name fits the space title, with a rank: 2 exact, 1 other case, 0 numbered
    copy such as "Work 2" or "Work (2)" (a second import creates new profiles)."""
    pattern = re.compile(r"^%s\s*(\(\d+\)|\d+)$" % re.escape(title.casefold()))
    out = []
    for p in profiles:
        if p.name == title:
            out.append((p, 2))
        elif p.name.casefold() == title.casefold():
            out.append((p, 1))
        elif pattern.match(p.name.casefold()):
            out.append((p, 0))
    return out


def _profile_counter(p: DiaProfile) -> Counter:
    return Counter(U.normalize(t.url) for t in p.tabs if not t.is_pinned)


def _overlap(a: Counter, b: Counter) -> int:
    return sum((a & b).values())


def verify(
    expected_sb: Sidebar,
    profiles: List[DiaProfile],
    original_sb: Optional[Sidebar] = None,
    mode: str = "original",
    mapping: Optional[Dict[str, str]] = None,
) -> VerifyReport:
    """``expected_sb`` is what Dia was given (the cleaned file, or the original when no
    prepare was used). ``original_sb`` is the untouched Arc file, used to explain differences.
    In "prepared" mode the expected Favorites come from ``expected_sb``: the cleaned file keeps
    the Favorites of the file it was prepared from, while the current Arc file may be another one
    (after ``swap --allow-other-source``) or may have changed since."""
    mapping = mapping or {}
    orig = original_sb or expected_sb
    used: Set[int] = set()
    results: List[SpaceVerify] = []

    fav_urls: Set[str] = set()
    for _, entry in (expected_sb if mode == "prepared" else orig).favorite_entries():
        if entry.kind == KIND_TAB and tab_url(entry.obj):
            fav_urls.add(U.normalize(tab_url(entry.obj)))
    space_of_url: Dict[str, str] = {}
    open_urls: Dict[str, Set[str]] = {}
    for s in orig.spaces:
        for root in (s.pinned_id, s.unpinned_id):
            for e in orig.walk(root):
                if e.kind == KIND_TAB and tab_url(e.obj):
                    space_of_url.setdefault(U.normalize(tab_url(e.obj)), s.title)
        open_urls[s.id] = set(_web_counter(orig, s.unpinned_id))

    for space in expected_sb.spaces:
        want = _web_counter(expected_sb, space.pinned_id)
        wrapped = _wrapped_web_counter(expected_sb, space.pinned_id)
        prof, method = _pick_profile(space, want, profiles, used, mapping)
        sv = SpaceVerify(space.id, space.title, prof.name if prof else None, method,
                         expected=sum(want.values()), found=0, matched=0)
        nonweb = _non_web_count(expected_sb, space.pinned_id)
        if nonweb:
            sv.expected_losses["non-web URLs (Dia drops them)"] = nonweb
        open_left = _web_counter(expected_sb, space.unpinned_id)
        if open_left:
            sv.expected_losses["open tabs (Dia's importer skips them)"] = sum(open_left.values())
        if prof is None:
            results.append(sv)
            continue
        used.add(id(prof))
        have = _profile_counter(prof)
        dia_favs = {U.normalize(t.url) for t in prof.tabs if t.is_pinned}
        sv.found = sum(have.values())
        sv.matched = _overlap(want, have)
        missing = want - have
        extra = have - want
        # Pair up near-identical URLs (trailing slash, fragment) first.
        loose_extra: Dict[str, List[str]] = {}
        for url in extra:
            loose_extra.setdefault(U.loose_key(url), []).append(url)
        for url, count in sorted(missing.items()):
            partners = loose_extra.get(U.loose_key(url))
            if partners:
                partner = partners[0]
                take = min(count, extra[partner])
                extra[partner] -= take
                if extra[partner] <= 0:
                    partners.pop(0)
                sv.missing.append(UrlDiff(url, take, f"present in Dia as {partner} (slightly different URL)"))
                count -= take
                if count <= 0:
                    continue
            if url in dia_favs:
                sv.missing.append(UrlDiff(url, count, "present in this profile's Dia Favorites instead"))
                continue
            take = min(count, wrapped.get(url, 0))
            if take:
                sv.missing.append(UrlDiff(url, take, WRAPPED_EXPLANATION))
                count -= take
                if count <= 0:
                    continue
            if is_github_pull(url):
                sv.missing.append(UrlDiff(url, count, GITHUB_PULL_EXPLANATION, probable=True))
            else:
                sv.missing.append(UrlDiff(url, count, "not found in this Dia profile", expected=False))
        for url, count in sorted((+extra).items()):
            sv.extra.append(UrlDiff(url, count, _explain_extra(url, space, fav_urls, open_urls, space_of_url)))
        results.append(sv)

    unmatched = [p.name for p in profiles if id(p) not in used]
    dia_fav_all = {U.normalize(t.url) for p in profiles for t in p.tabs if t.is_pinned}
    arc_fav_web = {u for u in fav_urls if U.is_web(u)}
    report = VerifyReport(
        mode=mode,
        spaces=results,
        unmatched_profiles=unmatched,
        favorites_arc=len(arc_fav_web),
        favorites_dia=len(dia_fav_all),
        favorites_missing=sorted(arc_fav_web - dia_fav_all),
    )
    if unmatched:
        report.notes.append(
            "Dia profiles with no matching Arc space (the default profile, or an earlier import): " + ", ".join(unmatched)
        )
    return report


def _pick_profile(space: Space, want: Counter, profiles: List[DiaProfile], used: Set[int],
                  mapping: Dict[str, str]) -> Tuple[Optional[DiaProfile], Optional[str]]:
    free = [p for p in profiles if id(p) not in used]
    target = mapping.get(space.title) or mapping.get(space.id)
    if target is not None:
        for p in free:
            if p.name == target:
                return p, "map"
        return None, None
    cands = _name_candidates(space.title, free)
    if len(cands) == 1:
        return cands[0][0], "name"
    if cands:
        best = max(cands, key=lambda c: (_overlap(want, _profile_counter(c[0])), c[1], c[0].index))[0]
        return best, "name, best URL overlap"
    total = sum(want.values())
    if total:
        scored = [(p, _overlap(want, _profile_counter(p))) for p in free]
        scored = [s for s in scored if s[1] / total >= 0.5]
        if scored:
            best = max(scored, key=lambda s: (s[1], s[0].index))[0]
            return best, "URL overlap (names differ)"
    return None, None


def _explain_extra(url: str, space: Space, fav_urls: Set[str], open_urls: Dict[str, Set[str]],
                   space_of_url: Dict[str, str]) -> str:
    if U.scheme_of(url) in INTERNAL_SCHEMES:
        return "browser page (new tab, settings...)"
    if url in open_urls.get(space.id, set()):
        return "an Arc open tab of this space"
    if url in fav_urls:
        return "an Arc favorite"
    other = space_of_url.get(url)
    if other and other != space.title:
        return f'from Arc space "{other}"'
    return "not in the Arc sidebar (opened after the import?)"


def render_verify(report: VerifyReport, limit: int = 15) -> str:
    lines: List[str] = []
    add = lines.append
    add("Comparing Dia with " + ("the cleaned sidebar given to Dia" if report.mode == "prepared" else "the Arc sidebar"))
    for s in report.spaces:
        add("")
        if s.profile_name is None:
            add(f'Space "{s.space_title}": no matching Dia profile found (use --map "{s.space_title}=<Dia profile>")')
            continue
        add(f'Space "{s.space_title}" -> Dia profile "{s.profile_name}" (matched by {s.match_method})')
        add(f"  expected {s.expected} pinned web URLs, Dia has {s.found} tabs (excluding Favorites), {s.matched} match")
        for label, n in s.expected_losses.items():
            add(f"  expected loss: {n} {label}")
        unexplained = [d for d in s.missing if not d.expected]
        probable = [d for d in s.missing if d.expected and d.probable]
        explained = [d for d in s.missing if d.expected and not d.probable]
        if unexplained:
            add(f"  MISSING in Dia: {s.unexplained_missing}")
            for d in unexplained[:limit]:
                add(f"    - {d.url}" + (f" (x{d.count})" if d.count > 1 else ""))
            if len(unexplained) > limit:
                add(f"    ... {len(unexplained) - limit} more (use --json)")
        probable_by_reason: Dict[str, List[UrlDiff]] = {}
        for d in probable:
            probable_by_reason.setdefault(d.explanation, []).append(d)
        for reason, diffs in probable_by_reason.items():
            add(f"  expected, probably ({reason}): {sum(d.count for d in diffs)}")
            for d in diffs[:limit]:
                add(f"    - {d.url}" + (f" (x{d.count})" if d.count > 1 else ""))
            if len(diffs) > limit:
                add(f"    ... {len(diffs) - limit} more (use --json)")
        for d in explained[:limit]:
            add(f"  ~ {d.url}: {d.explanation}")
        if s.extra:
            add(f"  extra in Dia: {sum(d.count for d in s.extra)}")
            by_reason = Counter()
            for d in s.extra:
                by_reason[d.explanation] += d.count
            for reason, n in by_reason.most_common():
                add(f"    {n} x {reason}")
    add("")
    add(f"Favorites: {report.favorites_arc} web favorites in Arc, {report.favorites_dia} in Dia"
        + (f"; not found in Dia: {len(report.favorites_missing)}" if report.favorites_missing else ""))
    for url in report.favorites_missing[:limit]:
        add(f"  - {url}")
    for n in report.notes:
        add(n)
    add("")
    add("Result: " + ("OK, every difference is explained." if report.ok else "differences need a look (see MISSING above)."))
    return "\n".join(lines)
