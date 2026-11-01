"""Error types. Every error the CLI expects to show the user derives from Arc2DiaError."""


class Arc2DiaError(Exception):
    """An expected, user-facing error. The CLI prints the message and exits with code 2."""


class FormatError(Arc2DiaError):
    """StorableSidebar.json does not look like the format this tool understands."""


class PlanError(Arc2DiaError):
    """The plan file is invalid or does not match the sidebar."""


class SafetyError(Arc2DiaError):
    """A safety precondition failed (Arc running, file changed, swap already active...)."""
