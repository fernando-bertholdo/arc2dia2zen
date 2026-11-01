import json
import subprocess

import pytest

from arc2dia2zen import swap as S
from arc2dia2zen.errors import SafetyError
from arc2dia2zen.fsutil import sha256_file
from arc2dia2zen.model import Sidebar
from arc2dia2zen.prepare import CLEANED_NAME, MANIFEST_NAME, prepare
from conftest import FakeChecker, make_plan


@pytest.fixture
def env(tmp_path, sidebar_file):
    sb = Sidebar.load(sidebar_file)
    prepare(sidebar_file, make_plan(sb, folder_strategy="path-names"), tmp_path / "out")
    return {
        "sidebar": sidebar_file,
        "prepared": tmp_path / "out",
        "backups": tmp_path / "backups",
        "original_sha": sha256_file(sidebar_file),
        "cleaned_sha": sha256_file(tmp_path / "out" / CLEANED_NAME),
    }


def all_files(*dirs):
    out = set()
    for d in dirs:
        if d.exists():
            out |= {p for p in d.rglob("*") if p.is_file()}
    return out


def do_swap(env, apply=True, running=False):
    return S.swap(env["sidebar"], env["prepared"], env["backups"], apply, FakeChecker(running))


def test_refuses_when_arc_is_running(env):
    with pytest.raises(SafetyError, match="Arc is running"):
        do_swap(env, running=True)
    assert sha256_file(env["sidebar"]) == env["original_sha"]
    assert not env["backups"].exists()


def test_dry_run_changes_nothing(env):
    lines = do_swap(env, apply=False)
    assert any("Dry run" in line for line in lines)
    assert sha256_file(env["sidebar"]) == env["original_sha"]
    assert not env["backups"].exists()


def test_swap_moves_original_to_backup_and_installs_cleaned(env):
    lines = do_swap(env)
    assert sha256_file(env["sidebar"]) == env["cleaned_sha"]
    state = json.loads((env["backups"] / S.STATE_FILE).read_text())
    bdir = env["backups"] / state["active"]
    assert sha256_file(bdir / S.ORIGINAL_NAME) == env["original_sha"]
    record = json.loads((bdir / S.SWAP_RECORD).read_text())
    assert record["status"] == "swapped" and record["original_sha256"] == env["original_sha"]
    text = "\n".join(lines)
    assert "Import from Another Browser" in text
    assert "defaults export company.thebrowser.dia" in text
    assert "restore --apply" in text


def test_refuses_double_swap(env):
    do_swap(env)
    with pytest.raises(SafetyError, match="already active"):
        do_swap(env)


def test_refuses_when_source_changed_since_prepare(env):
    env["sidebar"].write_text(env["sidebar"].read_text().replace("Team Wiki", "Team Wiki 2"))
    with pytest.raises(SafetyError, match="changed since prepare"):
        do_swap(env)


def test_allow_other_source_installs_and_restores_the_real_file(env):
    real = env["sidebar"].read_text().replace("Team Wiki", "Team Wiki 2")
    env["sidebar"].write_text(real)
    real_sha = sha256_file(env["sidebar"])
    lines = S.swap(env["sidebar"], env["prepared"], env["backups"], True, FakeChecker(False),
                   allow_other_source=True)
    assert any("TEST MODE" in line for line in lines)
    assert sha256_file(env["sidebar"]) == env["cleaned_sha"]
    S.restore(env["sidebar"], env["backups"], True, FakeChecker(False))
    assert sha256_file(env["sidebar"]) == real_sha


def test_refuses_failed_validation(env):
    manifest_path = env["prepared"] / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["validation_ok"] = False
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SafetyError, match="failed validation"):
        do_swap(env)


def test_refuses_tampered_cleaned_file(env):
    cleaned = env["prepared"] / CLEANED_NAME
    cleaned.write_bytes(cleaned.read_bytes() + b" ")
    with pytest.raises(SafetyError, match="changed after prepare"):
        do_swap(env)


def test_restore_is_byte_for_byte(env):
    original_bytes = env["sidebar"].read_bytes()
    do_swap(env)
    before = all_files(env["backups"])
    lines = S.restore(None, env["backups"], True, FakeChecker(False))
    assert env["sidebar"].read_bytes() == original_bytes
    assert any("byte for byte" in line for line in lines)
    after = all_files(env["backups"])
    assert before <= after  # nothing was removed from the backups
    state = json.loads((env["backups"] / S.STATE_FILE).read_text())
    assert state["active"] is None
    names = {p.name for p in after}
    assert S.INSTALLED_NAME in names and S.ORIGINAL_NAME in names


def test_restore_dry_run_and_arc_running(env):
    do_swap(env)
    S.restore(None, env["backups"], False, FakeChecker(False))
    assert sha256_file(env["sidebar"]) == env["cleaned_sha"]
    with pytest.raises(SafetyError, match="Arc is running"):
        S.restore(None, env["backups"], True, FakeChecker(True))
    assert sha256_file(env["sidebar"]) == env["cleaned_sha"]


def test_restore_keeps_unexpected_content(env):
    do_swap(env)
    env["sidebar"].write_text('{"changed": "by someone else"}')
    lines = S.restore(None, env["backups"], True, FakeChecker(False))
    assert sha256_file(env["sidebar"]) == env["original_sha"]
    assert any("unexpected content" in line for line in lines)
    kept = [p for p in all_files(env["backups"]) if p.name.startswith("StorableSidebar.unexpected-")]
    assert len(kept) == 1 and "by someone else" in kept[0].read_text()


def test_restore_without_active_swap(env):
    with pytest.raises(SafetyError, match="no active swap"):
        S.restore(None, env["backups"], True, FakeChecker(False))


def test_restore_refuses_corrupted_backup(env):
    do_swap(env)
    state = json.loads((env["backups"] / S.STATE_FILE).read_text())
    original = env["backups"] / state["active"] / S.ORIGINAL_NAME
    original.write_bytes(original.read_bytes() + b"x")
    with pytest.raises(SafetyError, match="does not match its recorded hash"):
        S.restore(None, env["backups"], True, FakeChecker(False))
    assert sha256_file(env["sidebar"]) == env["cleaned_sha"]


def test_restore_specific_backup_twice(env):
    do_swap(env)
    name = json.loads((env["backups"] / S.STATE_FILE).read_text())["active"]
    S.restore(None, env["backups"], True, FakeChecker(False))
    # Restoring again when the original is already in place only closes the record.
    lines = S.restore(None, env["backups"], True, FakeChecker(False), backup_name=name)
    assert any("already in place" in line for line in lines)
    assert sha256_file(env["sidebar"]) == env["original_sha"]


def test_failed_install_rolls_back(env, monkeypatch):
    real = S.copy_file_atomic
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(S, "copy_file_atomic", flaky)
    with pytest.raises(OSError):
        do_swap(env)
    assert sha256_file(env["sidebar"]) == env["original_sha"]
    state_file = env["backups"] / S.STATE_FILE
    assert not state_file.exists() or json.loads(state_file.read_text())["active"] is None


def test_status_reports_active_swap(env):
    do_swap(env)
    lines = S.status(env["sidebar"], env["backups"], FakeChecker(False), env["prepared"])
    text = "\n".join(lines)
    assert "ACTIVE SWAP" in text and "CLEANED" in text and "Arc: not running" in text
    assert "the sidebar file is still the installed cleaned copy" in text
    assert "WARNING" not in text


def test_status_warns_when_the_file_changed_during_the_swap(env):
    """Arc launched in the background (by another app) rewrote the cleaned file."""
    do_swap(env)
    env["sidebar"].write_bytes(env["sidebar"].read_bytes().replace(b"Team Wiki", b"Team Wiki (synced)"))
    lines = S.status(env["sidebar"], env["backups"], FakeChecker(False))
    warnings = [line for line in lines if line.startswith("WARNING:")]
    assert warnings == [
        "WARNING: the sidebar file no longer matches the installed cleaned copy. Arc or another program wrote "
        "the file; quit Arc, then restore (`arc2dia2zen restore --apply`)."
    ]
    assert not any("still the installed cleaned copy" in line for line in lines)


def test_status_warns_when_arc_runs_during_the_swap(env):
    do_swap(env)
    lines = S.status(env["sidebar"], env["backups"], FakeChecker(True))
    warnings = [line for line in lines if line.startswith("WARNING:")]
    assert len(warnings) == 1 and "Arc is running while a swap is active" in warnings[0]
    assert "closing its window does not quit it" in warnings[0]


def test_status_warns_when_the_original_is_back_but_the_swap_is_recorded(env):
    do_swap(env)
    state = json.loads((env["backups"] / S.STATE_FILE).read_text())
    original = env["backups"] / state["active"] / S.ORIGINAL_NAME
    env["sidebar"].write_bytes(original.read_bytes())
    text = "\n".join(S.status(env["sidebar"], env["backups"], FakeChecker(False)))
    assert "WARNING: the sidebar file is the original again" in text and "Do not run Dia's import now" in text
    env["sidebar"].rename(env["sidebar"].with_name("moved.json"))
    text = "\n".join(S.status(env["sidebar"], env["backups"], FakeChecker(False)))
    assert "WARNING: the sidebar file is missing while a swap is active" in text


def test_status_without_a_swap_has_no_warning(env):
    text = "\n".join(S.status(env["sidebar"], env["backups"], FakeChecker(True)))
    assert "no active swap" in text and "Arc: running" in text and "WARNING" not in text


def test_swap_output_says_how_to_keep_arc_quit(env):
    dry = "\n".join(do_swap(env, apply=False))
    assert "closing Arc's window does not quit Arc (use Arc > Quit Arc,\nor Cmd+Q)" in dry
    assert "other apps or automation can launch Arc in the background" in dry
    assert "Safest, if you can: turn off Arc's sync before the swap" in dry
    applied = "\n".join(do_swap(env))
    assert "closing Arc's window does not quit Arc" in applied
    assert "other apps or automation can launch Arc in the background" in applied
    assert "Turning off Arc's sync before the swap is the safest choice; do not open Arc now" in applied
    assert "Run `arc2dia2zen status` right before and right after the import" in applied


def test_pgrep_checker(monkeypatch):
    def fake_run(code):
        return lambda *a, **k: subprocess.CompletedProcess(a, code)

    monkeypatch.setattr(S.subprocess, "run", fake_run(0))
    assert S.PgrepChecker().is_running("Arc") is True
    monkeypatch.setattr(S.subprocess, "run", fake_run(1))
    assert S.PgrepChecker().is_running("Arc") is False
    monkeypatch.setattr(S.subprocess, "run", fake_run(3))
    with pytest.raises(SafetyError):
        S.PgrepChecker().is_running("Arc")

    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(S.subprocess, "run", missing)
    with pytest.raises(SafetyError, match="pgrep"):
        S.PgrepChecker().is_running("Arc")


def test_pgrep_called_with_exact_name(monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(S.subprocess, "run", fake)
    S.PgrepChecker().is_running("Arc")
    assert seen["cmd"] == ["pgrep", "-x", "Arc"]
