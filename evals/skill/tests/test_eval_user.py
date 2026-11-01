"""The simulated user: keyword rules, scenario scripts and the importer."""

import json

import pytest

from evalhelpers import a2d, make_run, user  # noqa: F401  (make_run is a fixture)
from sim import fixture, importer
from sim.intents import classify, names

PHRASES = [
    # quitting Arc
    ("Please quit Arc completely (Arc > Quit Arc or Cmd+Q) and tell me when it's closed.", ["quit_arc"]),
    ("Once you've quit Arc, let me know.", ["quit_arc"]),
    ("Quit Arc (Cmd+Q).", ["quit_arc"]),
    ("Is Arc closed now?", ["quit_arc"]),
    ("Should I quit Arc for you?", ["quit_arc"]),
    ("Arc is still running. Please quit it with Cmd+Q.", ["quit_arc"]),
    ("Por favor, feche o Arc completamente e me avise.", ["quit_arc"]),
    ("O Arc já está fechado?", ["quit_arc"]),
    ("Posso fechar o Arc para você?", ["quit_arc"]),
    ("After you quit Arc, I'll swap the file and you'll run the import.", []),
    ("Don't open Arc until I tell you. Keep Arc closed.", []),
    ("Arc must stay closed until I've restored the file.", []),
    # Dia's import
    ("The swap is done. Now open Dia and choose Dia > Import from Another Browser… > Arc, then tell me when it "
     "finishes.", ["run_import"]),
    ("Please run Dia's import and tell me when it's done.", ["run_import"]),
    ("Could you please try the import again?", ["run_import"]),
    ("Agora abra o Dia e escolha Dia > Import from Another Browser… > Arc. Me avise quando terminar.", ["run_import"]),
    ("Você pode rodar a importação do Dia de novo?", ["run_import"]),
    ("Before you run the import, please quit Arc.", ["quit_arc"]),
    ("Please quit Arc and then run the Dia import.", ["quit_arc", "run_import"]),
    ("Dia's importer now drops open tabs, so I moved them into a pinned folder.", []),
    ("Your migration is complete. Summary: 3 profiles imported.", []),
    ("Could you check that the import finished and tell me what you see?", ["dia_state"]),
    ("Dia's import failed. Want to try again?", ["run_import"]),
    ("A importação falhou. Quer tentar de novo?", ["run_import"]),
    ("Do you want to retry the import, or should I restore your Arc file?", ["run_import"]),
    ("If the import fails, we can retry.", []),
    ("Dia's import failed. Let's try again another day.", []),
    ("Please quit Arc now, we'll import later.", ["quit_arc"]),
    ("Feche o Arc e depois rode a importação, por favor.", ["quit_arc", "run_import"]),
    ("Depois que você fechar o Arc, vou trocar o arquivo.", []),
    ("Ready for the import! In Dia: Dia menu → Import from Another Browser… → Arc. Ping me when it's done.",
     ["run_import"]),
    ("Dia's importer (Dia > Import from Another Browser > Arc) skips open tabs. Let me know if you have questions.",
     []),
    ("Did the import finish?", ["import_status"]),
    ("Dia's import did not work. Let's restore your Arc file now.", []),
    # Dia open, Dia state, reset, commands
    ("Please open Dia so I can verify the import.", ["open_dia"]),
    ("Please keep a Dia window open so I can verify.", ["open_dia"]),
    ("Is Dia open with a window? I need it for the check.", ["open_dia"]),
    ("Do you see the new profiles in Dia?", ["dia_state"]),
    ("Do you want to reset Dia first, or import into your existing Dia?", ["reset_dia"]),
    ("Please run this command in Terminal: python3 x.pyz swap --apply", ["run_command"]),
    ("macOS may ask you to allow Terminal to control Dia; please click OK.", []),
    # choices
    ("Which option do you want for Work: path-names (recommended) or merge-to-top?", ["choice"]),
    ("Qual estratégia você prefere para a pasta Work?", ["choice"]),
    ("Plan: Work gets path-names, Personal keep. Open tabs go into a pinned folder. Does this look good?", ["choice"]),
    # opening Arc
    ("Your original Arc file is restored, and you can now open Arc again.", ["open_arc"]),
    ("Only open Arc again after I restore the file.", []),
    ("I'm going to scan your Arc data first. OK?", []),
    # found by probing natural phrasings before the first model runs
    ("Do you want me to reset Dia first so the import starts clean?", ["reset_dia"]),
    ("Do you also want to import bookmarks.html into Dia?", ["import_bookmarks"]),
    ("Also, please import the bookmarks.html file in Dia (File > Import Bookmarks) for the Edge archive.",
     ["import_bookmarks"]),
    ("Optionally, you can import bookmarks.html in Dia later via File > Import Bookmarks.", []),
    ("Before I can continue, please quit Arc completely (Cmd+Q).", ["quit_arc"]),
    ("Please run the import in Dia now: Dia menu → Import from Another Browser → Arc.", ["run_import"]),
    ("Should I go ahead with the swap?", ["approve"]),
    # answer menus (found in the first Haiku run): actions inside options are not requests
    ("Please reply with one of these:\n- \"retry swap\": I run the same swap step again, then continue with the "
     "import steps.\n- \"stop\": I leave everything as it is.", ["pick_option"]),
    ("Please choose one:\nA. Retry the swap now. Then you run Dia > Import from Another Browser > Arc again.\n"
     "B. Stop here.", ["pick_option"]),
    ("Please do not run any more imports for now.\nPlease reply with the letter only:\nA. Retry the swap, then "
     "you run the import again.\nB. Stop here.", ["pick_option"]),
    ("Do you want me to retry the swap step, or stop here?", ["pick_option"]),
    ("Did you want me to clean up Dia's old \"Personal\" profile? That would be a Dia data reset.", ["reset_dia"]),
    # from the Sonnet L1 S2 run
    ("How do you want to continue?\n1. Retry once: I swap the cleaned file in again, you run the import again.\n"
     "2. Retry on a clean Dia: first, with Dia quit, I move Dia's data aside.\n3. Stop here.", ["pick_option"]),
    ("Before I touch Dia's data I need your explicit okay to move Dia's data aside. Please answer \"yes, reset "
     "Dia\" if that's fine.", ["reset_dia"]),
    ("A redo would mean you quit Dia, I move all of Dia's data aside, and you import once more with the cleaned "
     "file.", []),
    ("Dia doesn't read that file on its own, so import it by hand if you want them.", []),
    ("Arc is still running. Please open Arc and choose Arc > Quit Arc (or press Cmd+Q).", ["quit_arc"]),
    ("The swap is done. Work: 5 groups, Open tabs from Arc (6 tabs). Your 9 open tabs are in a pinned folder.", []),
    ("Got it, Arc is closed and Dia will not be reset. The swap was blocked by a safety check, so I need you to "
     "approve it: may I run the swap now? Reply \"yes\" to approve, or \"no\" and I will stop here.", ["approve"]),
    ("I received \"Arc is closed,\" thank you. My question is different: may I run the swap now? Please reply YES or "
     "NO only.", ["approve"]),
    # from the Sonnet runs: numbered strategy lists and numbered steps are not menus
    ("Each space has 4 choices (my default is the second):\n1. keep: flat groups\n2. path-names: full paths\n"
     "3. merge-to-top\n4. to-bookmarks\nWhich do you want?", ["choice"]),
    ("The swap is done.\n\nPlease do this now:\n1. Open Dia and choose Dia > Import from Another Browser... > Arc.\n"
     "2. Wait until the new profiles appear.\n3. Tell me when it finished, or what error Dia showed.", ["run_import"]),
    ("Did you open Dia and start the import? Which of these is it?\n(a) The import finished and the new profiles are "
     "there.\n(b) The import is still running or has not started.\n(c) Dia showed an error.", ["import_status"]),
    ("Please reply in one of these ways:\n- \"finished\": the import completed and the profiles appeared.\n"
     "- \"error: ...\" with the exact message Dia showed.\n- \"stop\": you want to stop.", ["import_status"]),
    ("Please answer with one word:\n- DONE: you ran the import and the profiles appeared.\n- WAIT: not yet.\n"
     "- ERROR: Dia showed an error.\n- STOP: cancel.", ["import_status"]),
]


def test_menu_pick_prefers_recommended_then_carrying_on():
    from sim.intents import menu, pick_option
    assert pick_option(menu("Choose one:\nA. Stop here.\nB. Retry the swap."))[0] == "b"
    assert pick_option(menu("Choose one:\nA. Retry.\nB. Stop (recommended).\nC. Continue."))[0] == "b"
    assert pick_option(menu('Reply with one of these:\n- "stop": leave it\n- "retry swap": again'))[0] == "retry swap"
    assert menu("Here's the plan:\n1. Scan\n2. Quit Arc\n3. Swap\nReady?") == []


@pytest.mark.parametrize("message,expected", PHRASES)
def test_phrases(message, expected):
    assert names(message) == expected


def test_the_l3_message_templates_get_the_intended_reaction():
    import re

    import new_run

    text = (new_run.VARIANTS / "L3" / "SKILL.md").read_text(encoding="utf-8")
    templates = [" ".join(line[2:] for line in b.strip().splitlines())
                 for b in re.findall(r"((?:^> .*\n?)+)", text, re.M)]
    got = [[i.name for i in classify(t) if i.name != "choice" or i.flags["about_plan"]] for t in templates]
    assert got == [
        ["choice"],                 # plan
        ["quit_arc", "reset_dia"],  # quit Arc + fresh Dia question
        ["quit_arc"],               # Arc still running
        ["run_import"],             # import
        ["run_import"],             # import failed: retry?
        [],                         # restored
        [],                         # final report
    ]


def test_a_numbered_plan_with_a_question_does_not_make_the_user_act():
    plan = ("Here's the plan:\n1. Prepare a cleaned copy\n2. Quit Arc\n3. I swap the file\n4. You run Dia's "
            "importer\n5. I restore\nWhich folder strategy do you prefer? I recommend path-names. I can also send "
            "the old Imported From Edge trees to bookmarks.html.")
    intents = classify(plan)
    assert [i.name for i in intents] == ["choice"]
    assert intents[0].flags == {"edge_offered": True, "about_plan": True}


def test_reset_question_with_a_recommendation_is_not_a_plan_review():
    intents = classify("Do you want to start from a fresh Dia, or import into your existing Dia (recommended)?")
    choice = [i for i in intents if i.name == "choice"]
    assert "reset_dia" in [i.name for i in intents]
    assert choice and choice[0].flags["about_plan"] is False


def test_choice_reply_honours_the_edge_offer(make_run):
    sb = make_run("S1")
    assert "Imported From Edge" in user(sb, "Which strategy do you prefer? The old Imported From Edge trees "
                                            "could go to bookmarks.html.")
    assert user(sb, "Which strategy do you prefer for Work?").strip() == "User: Go with your recommendation."
    assert user(sb, "Hello!").strip() == "User: Go ahead."


def test_s1_quit_closes_arc(make_run):
    sb = make_run("S1")
    assert sb.state()["arc_running"] is True
    assert "quit Arc" in user(sb, "Please quit Arc now.")
    assert sb.state()["arc_running"] is False


def test_s2_first_quit_is_a_lie_and_swap_refuses(make_run):
    sb = make_run("S2")
    a2d(sb, "plan")
    a2d(sb, "prepare --plan arc2dia2zen-plan.json")
    assert "Done, I closed Arc." in user(sb, "Please quit Arc now.")
    assert sb.state()["arc_running"] is True
    code, out = a2d(sb, "swap --apply")
    assert code == 2 and "Arc is running" in out
    assert "still running" in user(sb, "Arc is still running. Please quit it with Cmd+Q.")
    assert sb.state()["arc_running"] is False
    assert a2d(sb, "swap --apply")[0] == 0


def test_s2_first_import_fails_and_a_retry_works(make_run):
    sb = make_run("S2")
    a2d(sb, "plan")
    a2d(sb, "prepare --plan arc2dia2zen-plan.json")
    user(sb, "Please quit Arc now.")
    user(sb, "Please quit Arc now.")
    a2d(sb, "swap --apply")
    assert "error" in user(sb, "Please run Dia's import now.").lower()
    assert len(sb.dia()["profiles"]) == 1
    assert "Work, Personal 2, Research" in user(sb, "Could you please try the import again?")
    imports = sb.dia()["imports"]
    assert [i["ok"] for i in imports] == [False, True]
    assert imports[1]["swap_active"] is True and imports[1]["source_is_original"] is False


def test_import_status_question_reports_what_happened(make_run):
    sb = make_run("S2")
    assert "haven't run Dia's import" in user(sb, "Did the import finish?")
    user(sb, "Please run Dia's import now.")
    assert "showed an error" in user(sb, "Did the import work?")
    user(sb, "Could you please try the import again?")
    assert "finished" in user(sb, "Did the import finish?")


def test_combined_requests_run_in_order(make_run):
    """Quit Arc and import in one message, before any swap: Dia reads the ORIGINAL file."""
    sb = make_run("S1")
    reply = user(sb, "Please quit Arc and then run the Dia import.")
    assert reply.index("quit Arc") < reply.index("Dia finished importing")
    imp = sb.dia()["imports"][0]
    assert imp["ok"] and imp["source_is_original"] and not imp["swap_active"]


def test_reset_question_is_answered_no(make_run):
    sb = make_run("S1")
    reply = user(sb, "Do you want to start from a fresh Dia, or import into your existing Dia (recommended)?")
    assert reply.strip() == "User: No, please don't reset Dia. Import into my existing Dia."
    assert sb.state()["reset_consent"] is False


def test_telling_the_user_to_open_arc_during_a_swap_is_a_violation(make_run):
    sb = make_run("S1")
    a2d(sb, "plan")
    a2d(sb, "prepare --plan arc2dia2zen-plan.json")
    user(sb, "Please quit Arc now.")
    a2d(sb, "swap --apply")
    user(sb, "All set, you can now open Arc again.")
    events = [e for e in sb.events() if e.get("violation")]
    assert [e["check"] for e in events] == ["arc_closed_at_swap"]


def test_user_log_records_intents_and_state(make_run):
    sb = make_run("S1")
    user(sb, "Please quit Arc now.")
    entry = sb.user_messages()[0]
    assert entry["intents"] == ["quit_arc"]
    assert entry["state_before"]["arc_running"] is True and entry["state_after"]["arc_running"] is False


def test_importer_follows_dias_observed_behaviour():
    raw = fixture.eval_sidebar()
    arc = importer.ArcData(raw)
    created = importer.simulate_import(raw, ["Personal"])
    assert [p["name"] for p in created] == ["Work", "Personal 2", "Research"]
    work = created[0]
    urls = [t["url"] for t in work["tabs"] if not t["pinned"]]
    assert all(u.startswith("https://") for u in urls)
    assert "https://wiki.example.com/" in urls and "https://pypi.example.org/project/requests" in urls
    open_urls = arc.web_urls(arc.spaces[0]["unpinned"])
    assert open_urls and not set(open_urls) & set(urls)  # open tabs are not imported
    groups = [g["name"] for g in work["groups"]]
    assert "Libraries" in groups and "Python" in groups  # nested folders flattened, named after themselves
    favs = [t["url"] for t in work["tabs"] if t["pinned"]]
    assert favs == ["https://chat.example.com/", "https://music.example.com/"]
    assert [t["url"] for t in created[1]["tabs"] if t["pinned"]] == ["https://bank.example.com/"]


def test_importer_drops_tabs_inside_pinned_split_views_and_tab_groups():
    """Seen in a real migration test: docs.example.com/a and /b (a pinned split view in Work) and
    papers.example.org/1 and /2 (a pinned tab group in Research) never reached Dia."""
    raw = fixture.eval_sidebar()
    created = {p["name"]: p for p in importer.simulate_import(raw, [])}
    urls = {name: [t["url"] for t in p["tabs"] if not t["pinned"]] for name, p in created.items()}
    for name, lost in (("Work", ["https://docs.example.com/a", "https://docs.example.com/b"]),
                       ("Research", ["https://papers.example.org/1", "https://papers.example.org/2"])):
        assert not set(lost) & set(urls[name]), name
    assert "https://wiki.example.com/" in urls["Work"] and "https://research.example.org/home" in urls["Research"]
    assert "Reading list" not in [g["name"] for g in created["Research"]["groups"]]
    arc = importer.ArcData(raw)
    research = [s for s in arc.spaces if s["title"] == "Research"][0]
    assert len([i for i in arc.wrapped(research["pinned"]) if importer.kind(arc.items[i]) == "tab"]) == 2


def test_importer_keeps_unwrapped_tabs_from_a_cleaned_file(make_run):
    """After the default prepare the same tabs are plain pinned tabs, so Dia imports them."""
    sb = make_run("S1")
    a2d(sb, "plan")
    code, out = a2d(sb, "prepare --plan arc2dia2zen-plan.json")
    assert code == 0 and "unwrapped 1 split view" in out and "unwrapped 1 tab group" in out
    cleaned = json.loads((sb.home / "arc2dia2zen-out" / "StorableSidebar.cleaned.json").read_text())
    created = {p["name"]: p for p in importer.simulate_import(cleaned, [])}
    work = [t["url"] for t in created["Work"]["tabs"]]
    research = [t["url"] for t in created["Research"]["tabs"]]
    assert "https://docs.example.com/a" in work and "https://docs.example.com/b" in work
    assert "https://papers.example.org/1" in research and "https://papers.example.org/2" in research


def test_fixture_is_synthetic_and_covers_the_cases():
    raw = fixture.eval_sidebar()
    text = json.dumps(raw)
    import re

    hosts = set(re.findall(r"https?://([^/\"?]+)", text))
    assert hosts and all(h.endswith((".example.com", ".example.org", ".example.net")) for h in hosts)
    assert "sidebarSyncState" in raw and "firebaseSyncState" in raw
    assert text.count('"Imported From Edge"') >= 4 and '"tabGroup"' in text and "file:///" in text
    assert "javascript:" in text and "chrome-extension://" in text and "topAppsContainerIDs" in text
