"""Presentation helpers for text and JSON output."""

from __future__ import annotations

import json
from typing import Iterable, Mapping, Sequence

from .model import ServiceStatus


def json_dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def render_status(statuses: Sequence[ServiceStatus]) -> str:
    if not statuses:
        return "No services declared."
    rows = [("SERVICE", "MANAGER", "HEALTH", "INTENT", "EXPOSURE", "DETAIL")]
    for item in statuses:
        rows.append(
            (
                item.name,
                item.manager_state.value,
                item.health_state.value,
                item.intent.value,
                item.exposure.value,
                item.detail,
            )
        )
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        lines.append(line)
        if idx == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def render_issues(issues: Iterable[Mapping[str, str]]) -> str:
    lines = []
    for issue in issues:
        lines.append(f"[{issue['level']}] {issue['where']}: {issue['message']}")
    return "\n".join(lines) if lines else "ok"
