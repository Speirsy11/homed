"""homed -- a declarative control layer over existing process managers.

homed does not run or supervise processes itself. It is a thin, inspectable
orchestration layer that knows how to *ask* the real managers (launchd, Docker
/ OrbStack, screen sessions, plain processes, cron-style jobs) about state and
how to drive them up/down/restart in a uniform way.

The package is intentionally dependency-light: it runs on the Python standard
library alone. See ``README.md`` for the design philosophy.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
