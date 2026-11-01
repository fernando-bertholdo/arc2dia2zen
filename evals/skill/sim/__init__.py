"""The simulated Mac used by the arc2dia2zen skill eval.

Nothing in here runs on a real Mac or touches real browser data. A sandbox folder (made by
``new_run.py``) holds a fake home with a synthetic Arc sidebar, shims for the macOS commands the
skill could use (``pgrep``, ``osascript``, ``open``...), a ``mac`` wrapper that runs shell
commands inside it and a ``user`` script that plays a deterministic, keyword-driven user.
"""
