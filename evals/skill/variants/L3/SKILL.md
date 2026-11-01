---
name: arc2dia2zen
description: Move a Mac user's Arc browser sidebar to Dia with the bundled arc2dia2zen tool, keeping the open (Today) tabs and nested folders that Dia's own Arc importer drops or flattens, and putting Arc's original data back afterwards. Use when someone wants to switch or migrate from Arc to Dia, bring Arc spaces, folders or open tabs into Dia, or asks what to do before Arc shuts down on December 22, 2026.
---

# arc2dia2zen: move Arc to Dia without losing open tabs or folders

Arc shuts down on December 22, 2026. Dia's official importer (Dia > Import from Another
Browser… > Arc) reads `~/Library/Application Support/Arc/StorableSidebar.json` and turns each Arc
space into a new Dia profile. Every import creates new profiles and never merges into existing
ones; it drops open (unpinned, "Today") tabs and flattens nested folders into separate groups.
arc2dia2zen works around this without touching Dia: it writes a cleaned copy of the sidebar file,
puts it in Arc's place while Arc is quit so the importer reads it, then restores the original
byte for byte.

Reply in the user's language; the message templates below are in English, so translate them.
You run the commands; the user makes the choices and does what happens in the apps (quitting
Arc, running Dia's importer). Keep messages short: a few lines, no raw tool output.

## Running the tool

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" <command> [options]

`<skill dir>` is the folder that holds this SKILL.md; put its real path in every command. In the
text below, `A2D` is shorthand for `python3 "<skill dir>/scripts/arc2dia2zen.pyz"`, not a command:
always type the full form, as in the command blocks. When the tool's output says `arc2dia2zen …`,
run it the same way. Always start from the home folder (`cd ~ &&`): the plan
(`~/arc2dia2zen-plan.json`), the prepared files (`~/arc2dia2zen-out/`) and the backups
(`~/arc2dia2zen-backups/`) live there. The tool needs macOS and Python 3.9+ (the `python3` of the
Command Line Tools) and never uses the network. Exit codes: 0 fine, 1 a check failed (prepare
validation, verify differences), 2 refused or error (the message says why). Its files hold the
user's browsing data: do not paste them in full or share them.

## Safety rules

1. Never create, open, close, reload or focus tabs in Dia or Arc: no AppleScript that does it,
   no `open <url>`, no opening HTML files. Opening about 50 tabs at once froze a 16 GB Mac. Only
   `A2D verify` reads Dia, and it is read-only.
2. Never delete files (`rm`, `trash`, Finder). If something has to go, move it to a backup folder.
3. Never open or launch Arc, or let it run, between `swap --apply` and `restore --apply`. That
   includes `open -a Arc` and any `tell application "Arc"`. With Arc Sync on, a running Arc can
   upload the cleaned sidebar to the user's other devices.
4. Never quit or kill Arc or Dia yourself (`killall`, `pkill`, `kill`, AppleScript `quit`) unless
   the user explicitly agrees. Ask the user to quit them.
5. Never run `swap --apply` before the user has approved the plan and confirmed that Arc is quit,
   and `A2D status` shows `Arc: not running`.
6. After a swap, always run `A2D restore --apply`: after a good import, after a failed one, and
   when the user stops halfway. Never finish while `A2D status` shows `ACTIVE SWAP`.
7. Resetting Dia (moving its data aside) happens only if the user explicitly says yes. Default:
   import into the existing Dia.
8. If the app's permission or safety check blocks a command (often `swap --apply`, because it
   moves Arc's file), never work around it with another command. Tell the user what the command
   does and that `A2D restore --apply` puts the original back byte for byte, ask them to approve
   it, and try again only after they did.
9. Never ask the user to run Dia's import while `A2D status` shows no active swap: Dia would
   import the uncleaned file and add a second set of profiles.

To check whether Arc runs, use `A2D status` (or `pgrep -x Arc`). Do not use
`tell application "Arc"`, which starts Arc.

## Procedure

Do the steps in order. Each checkpoint must hold before you go on.

### 1. Look (read-only)

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" status
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" scan
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" scan --tree        # optional: the folder outline of each space

Tell the user briefly, per space: pinned tabs, folders and nesting depth, open tabs, duplicate
folder trees, non-web links, and what Dia's importer would do as is.

Checkpoint: scan read the sidebar file. If it is missing, ask whether Arc is used on this Mac.
If `status` shows an `ACTIVE SWAP` from an earlier run, restore it first (see the table below).

### 2. Plan

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" plan

It writes `arc2dia2zen-plan.json` and prints, per space, what Dia would get with each folder strategy
(`*` marks the chosen one):

- `keep`: Dia's default; every folder becomes its own flat group.
- `path-names`: one group per folder, named after its path ("A › B › C"); the default for spaces
  with nested folders.
- `merge-to-top`: one group per top-level folder, with its subfolders' tabs merged in.
- `to-bookmarks`: the folder leaves the sidebar and goes, nested, into `bookmarks.html`.

Recommend `path-names` for spaces with nested folders and `keep` for the others; suggest
`to-bookmarks` for old "Imported From Edge"-style bookmark trees. Open tabs always move into a
pinned folder "Open tabs from Arc" in their space (that is how they survive the import).

Say (template):

> I looked at your Arc sidebar; nothing has changed yet. [Per space, one line: "Work: 24 pinned
> tabs in 17 folders (11 nested, up to 5 levels deep), 7 open tabs."] Dia's own importer would
> skip all open tabs and flatten the nested folders into [N] separate groups. My proposal:
> [per space: strategy and what Dia gets, e.g. "Work: path-names, 9 groups named after their
> paths"]. Open tabs go into a pinned folder "Open tabs from Arc". [The duplicate "Imported From
> Edge" tree would be removed; or the old "Imported From Edge" trees can go to bookmarks.html
> instead.] Links Dia cannot import (file:, javascript:...): [N]. Other options per space: keep,
> merge-to-top, to-bookmarks. Which do you prefer?

Apply the answer (one line, all options together):

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" plan --force --override "Imported From Edge=to-bookmarks"
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" plan --force --strategy merge-to-top --space "Personal=keep"
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" plan --force --exclude-space "Old space"

Checkpoint: the user approved the plan.

### 3. Prepare

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" prepare --plan arc2dia2zen-plan.json          # add --force when you redo it

Summarize each space's "Dia, after" line and what went to `bookmarks.html`.

Checkpoint: the output ends with `Validation: OK` and the exit code is 0. Otherwise do not swap.
The "Next:" hint at the end does not replace the next checkpoints.

### 4. Quit Arc (and ask about a fresh Dia)

Say (template):

> Everything is prepared. Please quit Arc completely now: Arc > Quit Arc, or Cmd+Q (closing the
> window is not enough), and keep it closed until I tell you. One question: Dia adds new profiles
> next to the ones it already has (a clashing name gets a number, like "Personal 2"). Import into
> your current Dia (recommended), or start from a fresh Dia? A fresh start moves Dia's current
> data aside; nothing is deleted.

Then check:

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" status

Checkpoint: the user said Arc is quit and status shows `Arc: not running`. If it still shows
`Arc: running`, say:

> Arc is still running (maybe only its window was closed). Please quit it with Arc > Quit Arc or
> Cmd+Q, and tell me when it's done.

### 5. Swap

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" swap               # dry run: read what it will do
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" swap --apply

The original moves to `~/arc2dia2zen-backups/<timestamp>/StorableSidebar.original.json` and the
cleaned copy takes its place. The output also prints optional "fresh Dia" commands; run them
only if the user said yes in step 4 and has quit Dia:

    mkdir -p "$HOME/dia-reset-backup-<timestamp>"
    defaults export company.thebrowser.dia "$HOME/dia-reset-backup-<timestamp>/company.thebrowser.dia.plist"
    mv "$HOME/Library/Application Support/Dia" "$HOME/dia-reset-backup-<timestamp>/Dia"
    defaults delete company.thebrowser.dia

Checkpoint: the output says `Done. The original is safe in …` and `A2D status` shows
`ACTIVE SWAP`.

### 6. Import (the user does it)

Right before you ask for the import, and right after the user answers, run `A2D status`: it must
show `Arc: not running` and `still the installed cleaned copy`. If Arc is running, ask the user
to quit it (Arc > Quit Arc); never kill it.

Say (template):

> The cleaned file is in place. Please open Dia, choose Dia > Import from Another Browser… > Arc,
> and wait until the new profiles appear. Keep Arc closed, and don't click through all the tabs
> (they load when clicked). Tell me when it has finished, or what error Dia shows.

Checkpoint: the user reported the result. If it failed, say:

> Dia's import did not work. The cleaned file is still in place, so you can try once more right
> away: Dia > Import from Another Browser… > Arc. Shall we retry, or shall I put your original
> Arc file back now?

### 7. Restore, always

After a good import, after a failed retry, or as soon as the user wants to stop:

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" restore            # dry run
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" restore --apply
    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" status

Checkpoint: `Restored. … matches the original byte for byte`, and status shows
`no active swap` and `(current file is this original)`. Then say:

> Your original Arc file is back, byte for byte; the backup stays in [backup folder]. You can
> open Arc again whenever you like.

### 8. Verify

Dia must be running with a window open; if macOS asks whether the terminal may control Dia, the
user clicks OK.

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" verify --prepared arc2dia2zen-out

Read the result: which profile matched which space (numbered copies such as "Personal 2" are
paired automatically; pair others with `--map "Space=Profile"`), the expected losses (non-web
links, Favorites, items left in place), and any `MISSING` lines. `Result: OK` means every
difference is explained. Never open tabs to fix anything.

### 9. Report

Say (template), and write it down if asked:

> Done. In Dia you now have: [per space: profile name, groups, the "Open tabs from Arc" folder
> with N tabs]. [bookmarks.html: N bookmarks in ~/arc2dia2zen-out/bookmarks.html, which browsers can
> import.] Your original Arc file was restored and its hash checked; the backup is in
> ~/arc2dia2zen-backups/[timestamp]/. The check against Dia: [verify result]. Not migrated: [non-web
> links, easels, Arc notes]. ~/arc2dia2zen-out holds your browsing data; keep it private.

## When something goes wrong

| Situation | How you notice | What to do |
| --- | --- | --- |
| Arc still running at swap | swap: `arc2dia2zen: error: Arc is running…`, or status `Arc: running` | Ask the user to quit Arc (template in step 4), run `A2D status` again, then swap. Never kill or quit it yourself. |
| Import failed | The user reports an error, or no new profiles appear | Keep Arc closed. Offer one retry (template in step 6); the swap stays as it is. If the user declines, or the retry fails too: `A2D restore --apply` now, then `A2D status`, and report what happened. |
| User wants to open Arc mid-flow | The user asks for it | Explain that Arc must stay closed while the cleaned file is in place (Arc Sync). If they still want to: `A2D restore --apply` first, then they may open Arc. To continue later, start again from step 2 (the file may change). |
| verify shows missing items | `MISSING in Dia: N` | Check the pairing (`--map "Space=Profile"`) and that the import really finished. Explain, and list the missing URLs in the report. Never open tabs. Another import would add a second set of profiles: only if the user wants it, with a new swap and restore. |
| swap refused: file changed | `… changed since prepare (Arc was probably opened)` | Arc ran since prepare. Run `A2D plan --force` with the same options, `A2D prepare --plan arc2dia2zen-plan.json --force`, tell the user if the numbers changed, ask them to quit Arc again, then swap. |
| swap refused: swap already active | `a swap is already active` | An earlier swap was never restored: `A2D restore --apply`, `A2D status`, then continue. |
| restore refused: Arc running | `Arc is running` during restore | Ask the user to quit Arc, then restore. Tell them Arc ran while the cleaned file was in place (Sync risk). |
| restore warns about unexpected content | `Warning: the file changed while swapped` | Go ahead with `restore --apply`; that version is kept in the backup folder. Tell the user Arc or another tool wrote the file. |
| A command is blocked by a permission check | The tool result says permission was denied or the action was blocked | Do not try it another way (safety rule 8). Tell the user, for example: "My permission check blocked `swap --apply`. It moves Arc's sidebar file into a backup folder and puts the cleaned copy in its place; `restore --apply` puts the original back byte for byte. Please approve it, and tell me when I can try again." Do not ask for Dia's import until `A2D status` shows `ACTIVE SWAP`. |
| verify cannot reach Dia | `Dia is not running` or `Dia has no open window` | Ask the user to open Dia with a window, and to allow automation if macOS asks, then run verify again. |
| prepare validation failed | Exit code 1, `Validation: FAILED` | Do not swap. Read `~/arc2dia2zen-out/report.txt`, adjust the plan, prepare again with `--force`. |
| python3 missing | `command not found: python3` | Ask the user to install the Command Line Tools (`xcode-select --install`, they confirm the dialog), then continue. |

## Final checklist

- [ ] The user approved the plan before the swap.
- [ ] The user confirmed Arc was quit, and status showed `Arc: not running`, before `swap --apply`.
- [ ] No tab was created, opened, closed or focused; no file was deleted; no app was quit or
      killed by you; Dia was not reset without a yes.
- [ ] `restore --apply` ran, and `A2D status` shows `no active swap`.
- [ ] `verify --prepared arc2dia2zen-out` ran after the restore, and you explained its result.
- [ ] The report names what is in Dia, the restored file and its backup folder, `bookmarks.html`,
      and the verify result.
