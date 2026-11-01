# Changelog

## 0.2.0 (2026-11-01)

Renamed to arc2dia2zen. Arc, Dia and Zen are close enough to share one community, and Zen
support comes next.

- The package, the command (`python3 -m arc2dia2zen`), the skill (`skill/arc2dia2zen/`,
  `arc2dia2zen.pyz`) and the default files (`arc2dia2zen-out/`, `arc2dia2zen-plan.json`,
  `~/arc2dia2zen-backups/`) carry the new name.
- Kept under the old name for now: `python3 -m arc2dia` (prints a note, then runs the new
  command), plans with the `arc2dia_plan_version` key, and backups in `~/arc2dia-backups/`,
  which `restore` and `status` still find when the new folder does not exist.
- Entries below keep the old name, as released.

## 0.1.3 (2026-10-25)

Fixes from the first end-to-end runs on a real Mac with real Dia, with real data and with the
synthetic example.

- Dia's importer drops the tabs inside split views (`data.splitView`) and Arc tab groups
  (`data.tabGroup`) in the pinned area: in the test, docs.example.com/a and /b (Work) and
  papers.example.org/1 and /2 (Research) never reached Dia. `prepare` now unwraps them by default,
  at any depth: their items take the wrapper's place in the same parent, in order, and the wrapper
  is removed. This runs after duplicate removal and `to-bookmarks` exports (which still treat a
  pinned tab group as a folder) and before the other folder strategies. New plan option
  `pinned_wrappers: "unwrap" | "keep"` (`--keep-pinned-wrappers`). The prepare report lists what was
  unwrapped (also in `report.json`, per space), and validation warns about tabs still left inside
  pinned wrappers, for example split views kept with `open_tabs_split_views: keep`.
- `scan` and the predictions in `plan` and `prepare` count those tabs as not imported, and tab
  groups only count towards the upper estimate of groups.
- `verify --prepared` takes the expected Favorites from the cleaned file instead of the current Arc
  file, which is the wrong source after `swap --allow-other-source` (it reported the user's own
  favorites as missing). When the current Arc file is not the one the migration was prepared from,
  differences are explained with the prepared source file if it is still there, or with the
  cleaned file, and a note says so.
- `verify` lists GitHub pull-request URLs (`github.com/<owner>/<repo>/pull/<n>`) that Dia does not
  show under "expected, probably", since Dia may show them as Live items instead of tabs, and
  explains tabs lost from pinned split views and tab groups. Other missing URLs are still MISSING.
- In the real test, another program launched Arc in the background while the cleaned file was in
  place; Arc ran for about 25 minutes with Sync on and rewrote the sidebar (`restore` noticed and
  kept that version). `status` now prints a WARNING when a swap is active and the sidebar file no
  longer matches the installed cleaned copy (or is missing, or is the original again), and when Arc
  runs during a swap; otherwise it says the file is still the installed cleaned copy. `swap` and
  the README say that closing Arc's window does not quit Arc, that other apps or automation can
  launch it in the background, that turning off Arc's sync first is the safest choice, and to run
  `status` right before and right after Dia's import.
- Skill (all three eval levels and the shipped `SKILL.md`): run `status` right before and right
  after Dia's import. The eval's simulated importer drops tabs inside pinned split views and tab
  groups too, and the scripted good agent checks `status` around the import.
- README: status line updated after the end-to-end runs.

## 0.1.2 (2026-10-24)

- `swap --allow-other-source` (testing only): install a cleaned file prepared from a different
  sidebar, such as the synthetic example, so the whole flow can be tested on a Mac without giving
  Dia your own data. The current Arc file is still backed up and restored.
- README: quick start, honest status line, real clone URL.

## 0.1.1 (2026-10-24)

Fixes from the first run on a real sidebar.

- Arc tab groups (`data.tabGroup`) among open tabs were treated as unsupported, so the tabs
  inside them were left behind (82 of 87 in one space). Their tabs now move with the other open
  tabs. By default the group wrappers are flattened in order; `open_tabs_tab_groups: "subfolders"`
  (`--tab-groups subfolders`) makes one subfolder per group instead. A tab group in the pinned area
  is handled like a folder by every strategy.
- Tabs without a saved URL broke validation ("duplicate  was removed but no other copy survives")
  and were reported as dropped. They are no longer deduplicated, exported or dropped: they stay in
  the sidebar, with "no URL" as the reason in the report. A folder that holds no URLs is never
  treated as a duplicate tree.
- `bookmarks.html` now merges sibling folders with the same name (for example two "Imported From
  Edge" trees) and keeps one copy of a URL per folder. Turn it off with
  `merge_bookmark_folders: false` (`--no-merge-bookmark-folders`).
- `scan` reports tab groups and tabs without a URL; the synthetic fixture gained a "Research" space
  that covers these cases.

## 0.1.0 (2026-10-24)

First version.

- `scan`: read-only diagnosis per space (pinned tabs, folders and nesting depth, open tabs, split views,
  favorites, non-web URLs, duplicate folder trees, repeated URLs) with a prediction of what Dia's
  importer will create and what it will drop. Text, `--json` or `--tree` output.
- `plan`: writes a JSON plan with per-space choices and a what-if table for each folder strategy
  (`keep`, `path-names`, `merge-to-top`, `to-bookmarks`), per-folder overrides, duplicate removal,
  open-tab handling, empty-folder cleanup, non-web link export and space exclusion.
- `prepare`: applies the plan to a copy, writes the cleaned `StorableSidebar.json` and a nested
  Netscape `bookmarks.html`, then re-reads both and checks shape, id integrity and that every original
  URL is accounted for.
- `swap` / `restore` / `status`: dry run by default; refuse while Arc runs; timestamped backups with
  SHA-256 checks; byte-for-byte restore; nothing is ever deleted.
- `verify`: reads Dia's profiles and tab URLs through a read-only AppleScript and compares them with
  Arc per profile and per URL, explaining expected differences.
