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

Reply in the user's language. You run the commands; the user makes the choices and does what
happens in the apps (quitting Arc, running Dia's importer).

## Running the tool

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" <command> [options]

`<skill dir>` is the folder that holds this SKILL.md. Below, `A2D` is shorthand for
`python3 "<skill dir>/scripts/arc2dia2zen.pyz"`, not a command: always type the full form. When the
tool's output says `arc2dia2zen …`, run it the same way. Always start from the home folder
(`cd ~ &&`): the plan (`~/arc2dia2zen-plan.json`), the prepared files (`~/arc2dia2zen-out/`) and the
backups (`~/arc2dia2zen-backups/`) live there. The tool needs macOS and Python 3.9+ (the `python3` of
the Command Line Tools) and never uses the network. Its files hold the user's browsing data: do
not paste them in full or share them. To check whether Arc runs, use `A2D status` or
`pgrep -x Arc`, never `tell application "Arc"`, which starts it.

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
7. Resetting Dia (moving its data aside: the "Optional: start from a fresh Dia" commands that
   `swap` prints) happens only if the user explicitly says yes. Default: import into the existing
   Dia.
8. If the app's permission or safety check blocks a command (often `swap --apply`, because it
   moves Arc's file), never work around it with another command. Tell the user what the command
   does and that `A2D restore --apply` puts the original back byte for byte, ask them to approve
   it, and try again only after they did.
9. Never ask the user to run Dia's import while `A2D status` shows no active swap: Dia would
   import the uncleaned file and add a second set of profiles.

## Procedure

Do the steps in order. Each checkpoint must hold before you go on.

1. **Look.** Run `A2D status` and `A2D scan`. Tell the user briefly, per space: pinned tabs,
   folders and nesting depth, open tabs, duplicate folder trees, non-web links, and what Dia's
   importer would do as is (nested folders flattened, open tabs lost).
   Checkpoint: scan read the sidebar file. If it is missing, ask whether Arc is used on this Mac.

2. **Plan.** Run `A2D plan`. It writes `arc2dia2zen-plan.json` and prints, per space, what Dia would
   get with each folder strategy (`*` marks the chosen one):
   - `keep`: Dia's default; every folder becomes its own flat group.
   - `path-names`: one group per folder, named after its path ("A › B › C"); the default for
     spaces with nested folders.
   - `merge-to-top`: one group per top-level folder, with its subfolders' tabs merged in.
   - `to-bookmarks`: the folder leaves the sidebar and goes, nested, into `bookmarks.html`.
   Show your recommendation per space with the alternatives, and say that open tabs move into a
   pinned folder "Open tabs from Arc" in their space, which duplicate trees get removed, that old
   "Imported From Edge"-style bookmark trees can go to `bookmarks.html` instead, and that Dia
   drops non-web links. Ask which options the user wants. Apply the answer with
   `A2D plan --force` plus options: `--strategy S`, `--space "Name=S"`,
   `--override "Folder path=S"` (for example `--override "Imported From Edge=to-bookmarks"`),
   `--exclude-space "Name"`.
   Checkpoint: the user approved the plan.

3. **Prepare.** Run `A2D prepare --plan arc2dia2zen-plan.json` (add `--force` when you redo it).
   Summarize each space's "Dia, after" line and what went to `bookmarks.html`.
   Checkpoint: the output ends with `Validation: OK` and the exit code is 0. Otherwise do not
   swap: read `arc2dia2zen-out/report.txt`, adjust the plan and prepare again. Its "Next:" hint does
   not replace the next checkpoints.

4. **Quit Arc.** Ask the user to quit Arc completely (Arc > Quit Arc or Cmd+Q; closing the window
   is not enough) and to keep it closed until you say so. Mention that Dia adds new profiles next
   to the ones it has (a clashing name gets a number, such as "Personal 2") and ask whether they
   want to start from a fresh Dia instead; the default is no. Then run `A2D status`.
   Checkpoint: the user said Arc is quit and status shows `Arc: not running`. If Arc still runs,
   ask again; never kill it.

5. **Swap.** Run `A2D swap` (dry run; read it), then `A2D swap --apply`. The original moves to
   `~/arc2dia2zen-backups/<timestamp>/` and the cleaned copy takes its place. Run the reset commands it
   prints only if the user said yes to a fresh Dia (and has quit Dia).
   Checkpoint: the output says `Done. The original is safe in …` and status shows `ACTIVE SWAP`.
   If swap refuses, see "When something goes wrong".

6. **Import.** Ask the user to open Dia, choose Dia > Import from Another Browser… > Arc, wait
   until the new profiles appear, and tell you when it finished or what error Dia showed. Remind
   them to keep Arc closed and not to click through all imported tabs (they load when clicked).
   Right before you ask, and right after the user answers, run `A2D status`: it must show
   `Arc: not running` and `still the installed cleaned copy`; if Arc is running, ask the user to
   quit it (Arc > Quit Arc), never kill it.
   Checkpoint: the user reported the result.

7. **Restore, always.** If the import failed, offer one retry first: the cleaned file is still in
   place, so no new swap is needed. Then, whether the import worked, failed or the user gave up,
   run `A2D restore` (dry run), `A2D restore --apply` and `A2D status`.
   Checkpoint: `Restored. … matches the original byte for byte`, and status shows
   `no active swap`. If restore refuses because Arc is running, ask the user to quit Arc first.

8. **Verify.** Dia must be running with a window open; if macOS asks whether the terminal may
   control Dia, the user clicks OK. Run `A2D verify --prepared arc2dia2zen-out` and explain the result:
   which profile matched which space (numbered copies such as "Personal 2" are paired
   automatically; pair others with `--map "Space=Profile"`), the expected losses, and any
   `MISSING` lines. Never open tabs to fix anything.

9. **Report.** Tell the user, per space, what is now in Dia; that the original Arc file was
   restored and its hash checked; where the backup is
   (`~/arc2dia2zen-backups/<timestamp>/StorableSidebar.original.json`); where `bookmarks.html` is, if
   one was written, and that browsers can import it; the verify result; that Arc can be opened
   again; and that `~/arc2dia2zen-out` holds private browsing data.

## When something goes wrong

- **Import failed, or the user stops:** never leave the cleaned file in place. Offer one retry;
  otherwise restore right away (step 7). The migration can be started again later from step 2.
- **swap says Arc is running:** ask the user to quit Arc completely, check `A2D status`, then
  swap again. Never kill Arc.
- **swap says the file changed since prepare:** Arc was probably opened. Run `A2D plan --force`
  (with the same options) and `A2D prepare --plan arc2dia2zen-plan.json --force`, tell the user if the
  numbers changed, and continue from step 4.
- **The user wants to open Arc mid-flow:** restore first (step 7); then Arc may be opened.
- **A command is blocked by a permission check:** follow safety rule 8; do not continue the
  procedure, and never ask for Dia's import, until the swap has really happened.
- **verify cannot reach Dia:** ask the user to open a Dia window (and allow automation if macOS
  asks), then run verify again.
