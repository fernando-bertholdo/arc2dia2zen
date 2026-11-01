# arc2dia2zen

Move your Arc sidebar to Dia, or to Zen (preview), without losing your open tabs or the shape of
your nested folders.

Until 0.2.0 this was arc2dia. The new name is for what comes next: Arc, Dia and Zen are close
enough to share one community. Since 0.3.0, `--target zen` prepares the file for Zen's own Arc
importer ([Zen Browser](#zen-browser-preview) below). `python3 -m arc2dia` still works for a while
as an alias.

arc2dia2zen is a small command-line tool for macOS. It does not replace Dia's own Arc importer: it
prepares a cleaned copy of Arc's sidebar file, lets the official importer read that copy, and then
puts your original file back, byte for byte. It is written in plain Python 3.9+ with no
dependencies and no network access.

Status: v0. The full workflow (scan, plan, prepare, swap, Dia's import, restore, verify) ran end to
end on a real Mac with real Arc and Dia data, and again with the synthetic example through
`swap --allow-other-source`. Both times `restore` put the original file back byte for byte. Arc's
and Dia's formats are undocumented and may change. Read the safety notes before you use it.

## Quick start

```sh
git clone https://github.com/fernando-bertholdo/arc2dia2zen.git && cd arc2dia2zen
python3 -m arc2dia2zen scan                      # see what Dia's importer would do (read-only)
python3 -m arc2dia2zen prepare                   # write a cleaned copy to arc2dia2zen-out/
# quit Arc (Arc > Quit Arc; closing its window is not enough), then:
python3 -m arc2dia2zen swap --apply              # put the cleaned copy in Arc's place
python3 -m arc2dia2zen status                    # Arc not running, cleaned copy in place, no WARNING
# in Dia: Dia > Import from Another Browser... > Arc, and wait for it to finish
python3 -m arc2dia2zen status                    # same check again
python3 -m arc2dia2zen restore --apply           # put your original Arc file back
python3 -m arc2dia2zen verify --prepared arc2dia2zen-out
```

Read "The safe workflow" below before running `swap` for real.

## Zen Browser (preview)

Zen 1.24 and later import Arc on their own. The importer's source (`ZenMigrationUtils.sys.mjs`)
shows what it leaves behind, and `prepare --target zen` changes the copy so those things come along:

| Zen's importer | What `prepare --target zen` does |
| --- | --- |
| Reads only the pinned area, so open tabs stay in Arc | Moves each space's open tabs into a pinned folder, "Open tabs from Arc", with Arc tab groups as subfolders |
| Drops Arc tab groups and the tabs in them | Turns pinned tab groups into folders with the same names |
| Merges folders deeper than `zen.folders.max-subfolders` (5) into their parent | Does the same merge in the copy and lists it in the report (`--max-folder-depth`) |
| Keeps nested folders and turns split views into their tabs | Leaves both alone |
| Skips URLs that aren't http, https, file or ftp | Counts them in the report |

```sh
python3 -m arc2dia2zen plan --target zen      # writes arc2dia2zen-plan.json
python3 -m arc2dia2zen prepare --plan arc2dia2zen-plan.json
# quit Arc, then:
python3 -m arc2dia2zen swap --apply           # ends with the steps for Zen's import
# import in Zen, then:
python3 -m arc2dia2zen restore --apply
```

The "Zen, before" and "Zen, after" lines of the report come from a port of Zen's parser in
`arc2dia2zen/zen.py`. I checked it against Zen 1.25t (Twilight, build 20261031100347): on the
synthetic example and on a real sidebar with 6 spaces, the port and Zen's own `parseArcSidebar` gave
the same result, item for item, for the original file and for the cleaned one.

Not there yet: putting the open tabs back as open tabs after the import, cookies, and a `verify`
for Zen. `verify` still reads Dia only.

## Use it as an Agent Skill

`skill/arc2dia2zen/` is an Agent Skill: a `SKILL.md` plus the whole tool in one file,
`scripts/arc2dia2zen.pyz`. Copy the folder into your agent's skills folder (for Claude Code,
`~/.claude/skills/arc2dia2zen/`) and ask it to migrate your Arc spaces, folders and open tabs to Dia.
The skill walks the agent through the same workflow: it shows you the plan, asks you to quit Arc,
waits while you run Dia's importer, always restores your original file, and never opens tabs.
If your agent's permission check stops `swap --apply`, approve it in your app; the skill tells the
agent never to work around it.

`scripts/build_skill.sh` rebuilds the archive after a code change. `evals/skill/` holds the test
harness, and `evals/skill/RESULTS.md` describes how Haiku, Sonnet and Opus did with three levels of
detail in the instructions. The shipped `SKILL.md` is the most detailed level.

## Why

Arc shuts down on December 22, 2026. Dia, from the same company, has an official importer
(Dia > Import from Another Browser... > Arc, also offered during first-run onboarding). It turns
each Arc Space into a Dia Profile and each Arc folder into a pinned Tab Group, and it keeps
Favorites, pinned tabs, bookmarks, history, passwords, cookies and extensions.

Two things go wrong, though:

**Open tabs are not imported.** Arc's open ("Today", unpinned) tabs did not come over at all in a
real migration: 0 of 210, even though the help article says they do.

**Nested folders are flattened.** Dia cannot nest Tab Groups, so every nested Arc folder becomes its
own top-level pinned group, named only after itself. A space with 116 folders, 112 of them nested
up to 5 levels deep, became over a hundred flat groups in one long list, each with no hint of
where it came from. Most of them were two copies of the same old "Imported From Edge" bookmark
tree.

Re-creating the missing tabs by script is not a good fix either. Tabs created through Dia's
AppleScript load immediately, and opening about 50 at once froze a 16 GB Mac. Tabs that come in
through the importer stay unloaded until you click them. arc2dia2zen therefore never opens tabs in Dia;
it only changes what the importer sees.

## What Dia's importer does, and what arc2dia2zen changes

| In Arc | Dia's importer | With arc2dia2zen |
| --- | --- | --- |
| Space | New Profile (every import creates new ones) | Same; a space can also be left out |
| Pinned tabs | Pinned tabs, unloaded until clicked | Same |
| Pinned split views and Arc tab groups | Their tabs are dropped (seen in a real migration test) | Unwrapped: their tabs take the wrapper's place, in order |
| Top-level folder | Pinned Tab Group | Same |
| Nested folder | Its own flat group, named after itself only | Your choice of four strategies (below) |
| Open (Today) tabs, including Arc tab groups | Not imported | Moved into one pinned folder, "Open tabs from Arc"; split views and tab groups are flattened (tab groups can become subfolders instead) |
| Duplicate folder trees | Imported twice | Optionally removed when every URL survives elsewhere |
| Favorites | Kept | Not touched |
| `file:`, `data:`, `javascript:`, `chrome-extension:` links | Dropped | Reported; optionally exported to `bookmarks.html` |
| Easels, Arc notes/documents | No equivalent | Reported and left in place |
| Tabs with no saved URL | Unknown | Never removed, deduplicated or exported; left in the sidebar and listed with "no URL" |

## How it works

Dia's importer reads Arc's `~/Library/Application Support/Arc/StorableSidebar.json`. With Arc
quit, arc2dia2zen moves that file into a backup folder, puts a cleaned copy in its place, and waits
while you run Dia's importer. Then it copies the original back and checks its SHA-256 hash. Arc
must stay closed the whole time: when Arc Sync is on (the file has `sidebarSyncState` or
`firebaseSyncState`), an open Arc could upload the modified sidebar to your other devices.

Dia's encrypted `tabs.db` cannot be edited, and Dia's AppleScript has no access to tab groups, so
changing the importer's input is the only safe lever.

## Requirements

macOS with Arc and Dia installed, and Python 3.9 or newer. The `python3` that ships with macOS
(3.9, installed with the Command Line Tools) is enough. `scan`, `plan` and `prepare` also run on
other systems if you pass `--sidebar` with a copy of the file.

Run it from a checkout, without installing anything:

```sh
git clone https://github.com/fernando-bertholdo/arc2dia2zen.git && cd arc2dia2zen
python3 -m arc2dia2zen --help
```

Or install the `arc2dia2zen` command with `pipx install .` (or `pip install --user .`).

## The safe workflow

The examples use `arc2dia2zen`; from a checkout, type `python3 -m arc2dia2zen` instead.

**1. Look first.** Nothing is written.

```sh
arc2dia2zen scan            # per-space counts and what Dia's importer would do
arc2dia2zen scan --tree     # the folder outline of each space
```

**2. Make a plan.** This writes `arc2dia2zen-plan.json` and prints, for every space, what Dia would end
up with under each folder strategy, so you can compare before choosing.

```sh
arc2dia2zen plan
arc2dia2zen plan --strategy merge-to-top --override "Imported From Edge=to-bookmarks" --force
```

Edit the file if you want per-space choices (see "Plan file" below).

**3. Prepare.** This applies the plan to a copy and writes everything into `arc2dia2zen-out/`. It then
re-reads the files it wrote and checks them: the file keeps Arc's shape, no id points to a missing
item, and every original URL is accounted for (still in the sidebar, in `bookmarks.html`, removed
as a duplicate with a surviving copy, or dropped with a stated reason).

```sh
arc2dia2zen prepare --plan arc2dia2zen-plan.json
arc2dia2zen scan --tree --sidebar arc2dia2zen-out/StorableSidebar.cleaned.json   # inspect the result
```

**4. Quit Arc** completely: Arc > Quit Arc, or Cmd+Q. Closing Arc's window does not quit Arc; it
keeps running in the background. Leave it quit until the restore in step 8 is done.

Other apps and automation (scripts, agents, launchers) can start Arc in the background, without a
window. In a real test this happened while the cleaned file was in place: Arc ran for about 25
minutes with Sync on and rewrote the sidebar (`restore` noticed and kept that version in the
backup). Pause such tools until the restore. If you can, turn off Arc's sync before you start; it
is the safest choice, because a stray Arc then cannot upload the cleaned sidebar to your other
devices. Arc may rewrite its sidebar file when you change that setting, so do it before step 2.

**5. Swap.** Without `--apply` this only prints what it would do.

```sh
arc2dia2zen swap
arc2dia2zen swap --apply
```

It refuses if Arc is running, if the sidebar file changed since `prepare`, if validation failed or
if another swap is still active. The original goes to `~/arc2dia2zen-backups/<timestamp>/`.

**6. Optional: start from a fresh Dia.** Each import creates new profiles and never merges into
existing ones, so importing into a Dia you already used gives you a second set. To start clean,
quit Dia and set its data aside. `swap` prints these commands with a timestamped folder name; the
tool never runs them for you.

```sh
mkdir -p "$HOME/dia-reset-backup"
defaults export company.thebrowser.dia "$HOME/dia-reset-backup/company.thebrowser.dia.plist"
mv "$HOME/Library/Application Support/Dia" "$HOME/dia-reset-backup/Dia"
defaults delete company.thebrowser.dia
```

To undo it later, quit Dia, move the new `Dia` folder aside, move the saved one back, and run
`defaults import company.thebrowser.dia "$HOME/dia-reset-backup/company.thebrowser.dia.plist"`.

**7. Import in Dia.** Right before you start, run `arc2dia2zen status`. It must show
`Arc: not running` and `the sidebar file is still the installed cleaned copy`, with no `WARNING`
line. Then open Dia and choose Dia > Import from Another Browser... > Arc (a fresh Dia offers it
during onboarding). Wait until the new profiles appear. Imported pinned tabs only load when you
click them; do not open them all at once.

When the import is done, run `arc2dia2zen status` again. If Arc is running, quit it (Arc > Quit Arc).
If status warns that the file no longer matches the cleaned copy, Arc or another program wrote it
and Dia may have read that version: restore (step 8), then start again from step 2.

**8. Restore** the original Arc file. Again, dry run first.

```sh
arc2dia2zen restore
arc2dia2zen restore --apply
```

The original is copied back from the backup and its hash is checked. The backup stays where it is.

**9. Verify.** This reads Dia's profile names and tab URLs (read-only) and compares them with what
Dia was given. Dia must be running with a window open; macOS may ask you to allow your terminal to
control Dia.

```sh
arc2dia2zen verify --prepared arc2dia2zen-out
```

Differences that are expected are explained: non-web links Dia drops, open tabs you chose to leave,
tabs left inside pinned split views or tab groups, Favorites, tabs you opened after the import,
URLs that differ only by a trailing slash. GitHub pull requests that Dia does not list are shown
under "expected, probably", since Dia may show them as Live items instead of tabs. Anything else
is listed as missing. With `--prepared`, Favorites are compared with the cleaned file, so the
check also fits a test run with `swap --allow-other-source`. If a profile name does not match its
space, pair them with `--map "Arc space=Dia profile"`.

**10. Open Arc again.** `arc2dia2zen status` tells you at any time whether a swap is still active,
and warns when the sidebar file changed while it was.

## Folder strategies

Take this Arc folder:

```
Imported From Edge          1 tab
  Dev                       1 tab
    Python                  2 tabs
```

| Strategy | What Dia gets |
| --- | --- |
| `keep` | Dia's default: three flat groups, "Imported From Edge", "Dev" and "Python" |
| `path-names` | Three flat groups named "Imported From Edge", "Imported From Edge › Dev" and "Imported From Edge › Dev › Python". Folders that hold only subfolders disappear, since their names live on in the paths |
| `merge-to-top` | One group, "Imported From Edge", with all four tabs in their original order |
| `to-bookmarks` | Nothing in the sidebar; the whole tree, nested, goes to `bookmarks.html` |

A strategy applies to a space's top-level folders. Each top-level folder can have its own
strategy, and a nested folder can be sent to `to-bookmarks` on its own; its parent folders are
recreated in `bookmarks.html` so it keeps its place. When `plan` is run without `--strategy`, spaces
with nested folders get `path-names` and the others `keep`.

Dia's importer drops the tabs inside split views and Arc tab groups in the pinned area (seen in a
real migration test). By default `prepare` therefore unwraps them before it applies the strategy:
their tabs take the wrapper's place in the same folder, in order, and the wrapper goes away (a tab
group's name is not kept). Duplicate detection and `to-bookmarks` still treat a pinned tab group as
a folder, so an exported tab group keeps its name in `bookmarks.html`. With
`"pinned_wrappers": "keep"` (`--keep-pinned-wrappers`), tab groups count as folders for every
strategy, and `prepare` warns that Dia will drop their tabs.

When two exported trees share a name, such as two "Imported From Edge" imports, they become one
folder in `bookmarks.html`, with repeated URLs kept once per folder (`merge_bookmark_folders`).
`bookmarks.html` uses the Netscape bookmark format that browsers import and export. Chromium-based
browsers can import it; check whether your Dia version offers an HTML bookmarks import, or keep the
file as an archive.

## Plan file

`plan` writes JSON. Keys starting with `_` are information for you (counts, the what-if table,
the list of top-level folders with their ids) and are ignored when the plan is read. Each space has:

| Key | Values | Default |
| --- | --- | --- |
| `import` | `true`, or `false` to leave the space out (no Dia profile is created for it) | `true` |
| `folder_strategy` | `keep`, `path-names`, `merge-to-top`, `to-bookmarks` | `path-names` if nested, else `keep` |
| `folder_overrides` | list of `{"path": "A › B", "strategy": ...}` or `{"folder_id": ..., "strategy": ...}` | `[]` |
| `dedupe_trees` | remove a folder tree when another folder with the same name holds all its URLs | `true` |
| `dedupe_urls` | remove repeated URLs in the pinned area, keeping the first copy | `false` |
| `open_tabs` | `pinned-folder`, `bookmarks` or `leave` | `pinned-folder` |
| `open_tabs_folder_name` | any name | `Open tabs from Arc` |
| `open_tabs_split_views` | `flatten` (split views become separate tabs) or `keep` (Dia then drops their tabs) | `flatten` |
| `open_tabs_tab_groups` | `flatten` (tabs from Arc tab groups go straight into the folder, in order) or `subfolders` (one subfolder per group; Dia then shows each as its own flat group) | `flatten` |
| `pinned_wrappers` | `unwrap` (split views and tab groups in the pinned area are replaced by their tabs, in order) or `keep` (Dia drops their tabs) | `unwrap` |
| `drop_empty_folders` | remove folders left with nothing in them | `true` |
| `non_web` | `keep` (Dia drops them) or `to-bookmarks` | `keep` |
| `merge_bookmark_folders` | in `bookmarks.html`, merge sibling folders with the same name and keep one copy of a URL per folder | `true` |

Override paths can be written with " › " or " > " between folder names. A path matches every folder
with that full path, so `"Imported From Edge"` catches both copies of a duplicated import.
Duplicate trees are only removed automatically when the folders share a name; folders with
different names but overlapping content are reported by `scan` and left alone.

## What `prepare` writes

All files go into the output folder (`arc2dia2zen-out/` by default), never into Arc's folder:
`StorableSidebar.cleaned.json` (the file Dia will read), `bookmarks.html` (only when something was
exported), `plan.json` (the plan as applied), `report.txt`, `report.json` (with a ledger entry for
every original tab: where it was, what happened to it and why) and `manifest.json` (hashes that
`swap` checks).

These files contain your browsing data. Do not commit or share them.

## Safety guarantees

arc2dia2zen never deletes a file. Files are moved into backup folders or copied, and a test checks that
the code has no delete calls. The only files it overwrites are its own: earlier outputs (only with
`--force`) and its bookkeeping files in the backup folder.

`scan`, `plan`, `prepare` and `verify` do not touch Arc's or Dia's data. `swap` and `restore` are dry
runs unless you pass `--apply`. They check that Arc is not running (`pgrep -x Arc`) before starting
and again right before moving the file. `swap` also refuses when the sidebar file changed since
`prepare`, when validation failed, when the cleaned file was modified, or when a swap is already
active. The original is moved, not rewritten, into a timestamped folder and its hash is checked.
`restore` copies it back and checks the hash again; if the file in Arc's folder changed while
swapped, that version is kept in the backup folder too, not overwritten.

`verify` runs an AppleScript that only reads profile names, tab titles and URLs. It never creates,
loads, closes or focuses tabs, and it does not launch Dia. Print it with `arc2dia2zen verify --print-script`.

There is no network access: the package uses only the Python standard library and imports no
networking module (also checked by a test).

## Limits

`swap`, `restore` and `verify` need macOS. Arc and Dia also run on Windows, where Arc keeps the
sidebar file elsewhere; that path is untested.

Arc's `StorableSidebar.json` and Dia's importer are undocumented. This tool is based on what was
observed in October 2026 and may break with any update of either browser. The prediction of how
many groups Dia creates is an estimate, which is why `scan` shows a range when some folders hold
only subfolders.

Leaving a space out (`"import": false`) removes it from the cleaned file's space list. That is the
least tested option.

Easels and Arc documents have no Dia equivalent and are not migrated. Tabs with no saved URL are
kept as they are; whether Dia imports them is unknown. Non-web links are dropped by
Dia unless you export them to `bookmarks.html`.

If Arc is opened while the cleaned file is in place and Sync is on, the cleaned sidebar may reach
your other devices. `restore` puts the local file back, but it cannot undo a sync. Keep Arc quit,
and turn off Arc's sync before you start if you can.

arc2dia2zen is an independent project. It is not affiliated with, endorsed by or supported by The
Browser Company or Atlassian. Arc and Dia are their trademarks.

## Development

```sh
python3 -m pip install pytest   # the only thing the tests need
python3 -m pytest
```

Tests use synthetic sidebars built by `tests/fixturegen.py` (all URLs use reserved `example.*`
domains); `examples/synthetic-StorableSidebar.json` is one of them, ready for
`arc2dia2zen scan --sidebar examples/synthetic-StorableSidebar.json`. Everything that touches macOS
(`pgrep`, `osascript`) sits behind small interfaces, so the suite runs anywhere.

To test the whole flow on a Mac without giving Dia your own data, prepare the synthetic example and
install it with `arc2dia2zen swap --apply --allow-other-source`. Normally `swap` refuses a cleaned file
that was not prepared from the current Arc file; this flag lets it through for testing, and your
real Arc file is still backed up and restored as usual. Do this with Dia's data set aside (step 6),
so the test profiles land in a throwaway Dia.

The code lives in `arc2dia2zen/`: `model.py` (file format), `analysis.py` (scan and prediction),
`plan.py`, `transform.py` (the strategies), `bookmarks.py`, `validate.py`, `prepare.py`,
`swap.py`, `dia.py` (AppleScript), `verify.py`, `zen.py` (the port of Zen's Arc importer) and
`cli.py`.

## License

MIT. See `LICENSE`.
