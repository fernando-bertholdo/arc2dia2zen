---
name: arc2dia2zen
description: Move a Mac user's Arc browser sidebar to Dia with the bundled arc2dia2zen tool, keeping the open (Today) tabs and nested folders that Dia's own Arc importer drops or flattens, and putting Arc's original data back afterwards. Use when someone wants to switch or migrate from Arc to Dia, bring Arc spaces, folders or open tabs into Dia, or asks what to do before Arc shuts down on December 22, 2026.
---

# arc2dia2zen

Arc shuts down on 2026-12-22. Dia's importer (Dia > Import from Another Browser… > Arc) reads
`~/Library/Application Support/Arc/StorableSidebar.json`. Every import creates new Dia profiles
(it never merges), drops open tabs and flattens nested folders. arc2dia2zen writes a cleaned copy of
that file, swaps it in for the import and puts the original back afterwards. Reply in the user's
language.

Run the tool from the home folder, where it keeps its files:

    cd ~ && python3 "<skill dir>/scripts/arc2dia2zen.pyz" <command>

`<skill dir>` is the folder of this file. The tool's output calls itself `arc2dia2zen`.

1. `status`, then `scan`: tell the user what each space holds.
2. `plan`: show the folder strategies per space (keep, path-names, merge-to-top, to-bookmarks)
   and your recommendation, and let the user choose. Apply it with `plan --force` plus options,
   e.g. `--override "Imported From Edge=to-bookmarks"`.
3. `prepare --plan arc2dia2zen-plan.json`: it must end with `Validation: OK`.
4. Ask the user to quit Arc completely; `status` must show `Arc: not running`.
5. `swap`, then `swap --apply`.
6. Ask the user to run Dia > Import from Another Browser… > Arc and tell you how it went.
   Right before asking, and right after, run `status`: Arc must not be running and the cleaned
   copy must still be in place; if Arc runs, ask the user to quit it (Arc > Quit Arc), never kill it.
7. `restore`, then `restore --apply`, always: also when the import failed or the user stops.
8. `verify --prepared arc2dia2zen-out`, with Dia open.
9. Report: what is in Dia, that Arc's file was restored, where the backup is.

Hard rules:

- Never create, open, close or focus tabs in Dia or Arc (no AppleScript for it, no `open <url>`).
- Never delete files; move them to a backup folder instead.
- Never open Arc or let it run between swap and restore (Arc Sync may upload the cleaned file).
- Never quit or kill Arc or Dia yourself unless the user explicitly agrees.
- Never run `swap --apply` before the user approved the plan and confirmed Arc is closed.
- Never finish with a swap still active (check `status`).
- Reset Dia (move its data aside) only with the user's explicit consent.
- If a permission or safety check blocks a command (often `swap --apply`), never work around it:
  say what the command does and that `restore` undoes it, and ask the user to approve it.
- Never ask the user to run Dia's import unless `status` shows an active swap.
