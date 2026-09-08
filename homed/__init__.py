"""homed -- a declarative control layer over existing process managers.

homed does not run or supervise processes itself. It is a thin, inspectable
orchestration layer that knows how to *ask* the real managers (launchd, Docker
/ OrbStack, screen sessions, plain processes, cron-style jobs) about state and
how to drive them up/down/restart in a uniform way.

The native Python dashboard adds pinned cryptography and calendar recurrence
dependencies. See ``README.md`` for installation and the deployment contract.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
