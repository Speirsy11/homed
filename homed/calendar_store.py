"""Persistent personal calendar storage and bounded recurrence expansion.

Recurring event mutations intentionally operate on the whole series in this
first release.  Occurrence identifiers are presentation identifiers only; the
stable ``event_id`` in ``extendedProps`` identifies the editable record.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule
from .storage import private_database


class CalendarError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


_FIELDS = {
    "title", "start", "end", "timezone", "all_day", "category",
    "location", "notes", "recurrence",
}
_CATEGORIES = {"personal", "occasion"}
_FREQUENCIES = {"daily", "weekly", "monthly", "yearly"}
_RRULE_FREQUENCIES = {"daily": DAILY, "weekly": WEEKLY, "monthly": MONTHLY, "yearly": YEARLY}
MAX_EVENTS = 5000
MAX_EXPORT_BYTES = 8 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise CalendarError(f"{field} must be an aware ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CalendarError(f"{field} must be an aware ISO datetime")
    if parsed.tzinfo is None:
        raise CalendarError(f"{field} must be an aware ISO datetime")
    return value


def _require_string(value: Any, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise CalendarError(f"{field} must be a string between {minimum} and {maximum} characters")
    return value


def _optional_string(value: Any, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum:
        raise CalendarError(f"{field} must be a string no longer than {maximum} characters")
    return value


def _zone(name: Any) -> ZoneInfo:
    if not isinstance(name, str) or not name:
        raise CalendarError("timezone must be an IANA timezone name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CalendarError("timezone must be a valid IANA timezone name")


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise CalendarError(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise CalendarError(f"{field} must be an ISO date")
    if "T" in value or len(value) != 10:
        raise CalendarError(f"{field} must be an ISO date")
    return parsed


def _parse_wall(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise CalendarError(f"{field} must be an ISO local datetime without an offset")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CalendarError(f"{field} must be an ISO local datetime without an offset")
    if parsed.tzinfo is not None or "T" not in value:
        raise CalendarError(f"{field} must be an ISO local datetime without an offset")
    return parsed.replace(microsecond=0)


def _aware_wall(wall: datetime, zone: ZoneInfo, field: str = "time") -> datetime:
    valid: List[datetime] = []
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip == wall:
            valid.append(candidate)
    offsets = {candidate.utcoffset() for candidate in valid}
    if not valid:
        raise CalendarError(f"{field} is a nonexistent local time in the selected timezone")
    if len(offsets) > 1:
        raise CalendarError(f"{field} is an ambiguous local time in the selected timezone")
    return valid[0]


def _validate_recurrence(value: Any, start_date: date) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"frequency", "interval", "until", "count"}:
        raise CalendarError("recurrence must contain only frequency, interval, until, and count")
    frequency = value.get("frequency")
    interval = value.get("interval")
    until = value.get("until")
    count = value.get("count")
    if frequency not in _FREQUENCIES:
        raise CalendarError("recurrence frequency must be daily, weekly, monthly, or yearly")
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 52:
        raise CalendarError("recurrence interval must be an integer between 1 and 52")
    if until is not None:
        until_date = _parse_date(until, "recurrence until")
        if until_date < start_date:
            raise CalendarError("recurrence until cannot be before the event start")
        until = until_date.isoformat()
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000):
        raise CalendarError("recurrence count must be an integer between 1 and 1000")
    if until is not None and count is not None:
        raise CalendarError("recurrence cannot specify both until and count")
    return {"frequency": frequency, "interval": interval, "until": until, "count": count}


def _validate_payload(payload: Any, *, partial: bool = False, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise CalendarError("event must be an object")
    unknown = set(payload) - _FIELDS
    if unknown:
        raise CalendarError(f"event contains unsupported field: {sorted(unknown)[0]}")
    if partial and not payload:
        raise CalendarError("event update must include at least one field")
    merged = {field: base.get(field) for field in _FIELDS} if base else {}
    merged.update(payload)
    required = {"title", "start", "end", "all_day", "category"}
    missing = [field for field in required if field not in merged]
    if missing:
        raise CalendarError(f"event is missing required field: {sorted(missing)[0]}")
    title = _require_string(merged.get("title"), "title", 1, 200)
    all_day = merged.get("all_day")
    if not isinstance(all_day, bool):
        raise CalendarError("all_day must be a boolean")
    category = merged.get("category")
    if category not in _CATEGORIES:
        raise CalendarError("category must be personal or occasion")
    timezone_name = merged.get("timezone", "Europe/London")
    zone = _zone(timezone_name)
    if all_day:
        start = _parse_date(merged.get("start"), "start")
        end = _parse_date(merged.get("end"), "end")
    else:
        start = _parse_wall(merged.get("start"), "start")
        end = _parse_wall(merged.get("end"), "end")
        _aware_wall(start, zone, "start")
        _aware_wall(end, zone, "end")
    if end <= start:
        raise CalendarError("end must be after start")
    recurrence = _validate_recurrence(merged.get("recurrence"), start.date() if isinstance(start, datetime) else start)
    return {
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": timezone_name,
        "all_day": all_day,
        "category": category,
        "location": _optional_string(merged.get("location"), "location", 500),
        "notes": _optional_string(merged.get("notes"), "notes", 10000),
        "recurrence": recurrence,
    }


class CalendarStore:
    def __init__(
        self,
        db_path: Any,
        *,
        max_events: int = MAX_EVENTS,
        max_export_bytes: int = MAX_EXPORT_BYTES,
    ):
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= MAX_EVENTS
        ):
            raise ValueError(f"max_events must be between 1 and {MAX_EVENTS}")
        if (
            isinstance(max_export_bytes, bool)
            or not isinstance(max_export_bytes, int)
            or not 1 <= max_export_bytes <= MAX_EXPORT_BYTES
        ):
            raise ValueError(f"max_export_bytes must be between 1 and {MAX_EXPORT_BYTES}")
        self.db_path = private_database(db_path)
        self.max_events = max_events
        self.max_export_bytes = max_export_bytes
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError("Unsupported calendar database version")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS calendar_events (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start TEXT NOT NULL,
                    end TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    all_day INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    location TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    recurrence TEXT,
                    source TEXT NOT NULL CHECK (source = 'personal'),
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS calendar_events_owner ON calendar_events(owner)")
            connection.execute("PRAGMA user_version=1")

    @staticmethod
    def _owner(owner: Any) -> str:
        if not isinstance(owner, str) or not owner:
            raise CalendarError("owner is required")
        return owner

    @staticmethod
    def _record(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "start": row["start"], "end": row["end"],
            "timezone": row["timezone"], "all_day": bool(row["all_day"]), "category": row["category"],
            "location": row["location"], "notes": row["notes"],
            "recurrence": json.loads(row["recurrence"]) if row["recurrence"] else None,
            "source": "personal", "revision": row["revision"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _export_payload(cls, rows: Iterable[sqlite3.Row]) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "exported_at": _utc_now(),
            "events": [cls._record(row) for row in rows],
        }

    def _check_capacity(self, connection: sqlite3.Connection, owner: str) -> None:
        count = connection.execute(
            "SELECT count(*) FROM calendar_events WHERE owner=?", (owner,)
        ).fetchone()[0]
        if count > self.max_events:
            raise CalendarError(
                "Calendar capacity exceeded. Export or back up existing events before removing some.",
                413,
            )
        rows = connection.execute(
            "SELECT * FROM calendar_events WHERE owner=? ORDER BY created_at,id", (owner,)
        ).fetchall()
        encoded = json.dumps(self._export_payload(rows), ensure_ascii=True).encode("utf-8")
        if len(encoded) > self.max_export_bytes:
            raise CalendarError(
                "Calendar capacity exceeded. Export or back up existing events before removing some.",
                413,
            )

    def create_event(self, owner: Any, payload: Any) -> Dict[str, Any]:
        owner = self._owner(owner)
        event = _validate_payload(payload)
        event_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO calendar_events
                   (id,owner,title,start,end,timezone,all_day,category,location,notes,recurrence,source,revision,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'personal',1,?,?)""",
                (event_id, owner, event["title"], event["start"], event["end"], event["timezone"],
                 int(event["all_day"]), event["category"], event["location"], event["notes"],
                 json.dumps(event["recurrence"]) if event["recurrence"] else None, now, now),
            )
            self._check_capacity(connection, owner)
        return self.get_event(owner, event_id)

    def get_event(self, owner: Any, event_id: Any) -> Dict[str, Any]:
        owner = self._owner(owner)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM calendar_events WHERE id=? AND owner=?", (str(event_id), owner)).fetchone()
        if row is None:
            raise CalendarError("event not found", 404)
        return self._record(row)

    def update_event(self, owner: Any, event_id: Any, payload: Any, revision: Any) -> Dict[str, Any]:
        owner = self._owner(owner)
        current = self.get_event(owner, event_id)
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise CalendarError("revision must be an integer")
        event = _validate_payload(payload, partial=True, base=current)
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE calendar_events SET title=?,start=?,end=?,timezone=?,all_day=?,category=?,location=?,notes=?,
                   recurrence=?,revision=revision+1,updated_at=? WHERE id=? AND owner=? AND revision=?""",
                (event["title"], event["start"], event["end"], event["timezone"], int(event["all_day"]),
                 event["category"], event["location"], event["notes"],
                 json.dumps(event["recurrence"]) if event["recurrence"] else None, now, str(event_id), owner, revision),
            )
            if cursor.rowcount != 1:
                raise CalendarError("event revision conflict", 409)
            self._check_capacity(connection, owner)
        return self.get_event(owner, event_id)

    def delete_event(self, owner: Any, event_id: Any, revision: Any) -> None:
        owner = self._owner(owner)
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise CalendarError("revision must be an integer")
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM calendar_events WHERE id=? AND owner=?", (str(event_id), owner)).fetchone()
            if exists is None:
                raise CalendarError("event not found", 404)
            cursor = connection.execute(
                "DELETE FROM calendar_events WHERE id=? AND owner=? AND revision=?", (str(event_id), owner, revision)
            )
            if cursor.rowcount != 1:
                raise CalendarError("event revision conflict", 409)

    def export_events(self, owner: Any) -> Dict[str, Any]:
        owner = self._owner(owner)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM calendar_events WHERE owner=? ORDER BY created_at,id", (owner,)).fetchall()
        return self._export_payload(rows)

    def restore_events(self, owner: Any, payload: Any) -> Dict[str, Any]:
        """Merge a backup atomically and return changed and reviewed counts.

        Repeated identical imports are no-ops. A stale or same-revision but
        divergent record rejects the whole import, preserving newer local work.
        """
        owner = self._owner(owner)
        records = self._validate_restore(payload)
        changed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_by_id: Dict[str, sqlite3.Row] = {}
            for record in records:
                existing = connection.execute("SELECT * FROM calendar_events WHERE id=?", (record["id"],)).fetchone()
                if existing is None:
                    continue
                if existing["owner"] != owner:
                    raise CalendarError("event id belongs to another owner", 409)
                current = self._record(existing)
                if record["revision"] < current["revision"]:
                    raise CalendarError("backup event is older than the local event", 409)
                if record["revision"] == current["revision"] and record != current:
                    raise CalendarError("backup event conflicts with the local event", 409)
                existing_by_id[record["id"]] = existing
            for record in records:
                existing = existing_by_id.get(record["id"])
                if existing is not None and self._record(existing) == record:
                    continue
                connection.execute(
                    """INSERT INTO calendar_events
                       (id,owner,title,start,end,timezone,all_day,category,location,notes,recurrence,source,revision,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,'personal',?,?,?)
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title,start=excluded.start,end=excluded.end,
                       timezone=excluded.timezone,all_day=excluded.all_day,category=excluded.category,
                       location=excluded.location,notes=excluded.notes,recurrence=excluded.recurrence,
                       revision=excluded.revision,created_at=excluded.created_at,updated_at=excluded.updated_at
                       WHERE calendar_events.owner=excluded.owner""",
                    (record["id"], owner, record["title"], record["start"], record["end"], record["timezone"],
                     int(record["all_day"]), record["category"], record["location"], record["notes"],
                     json.dumps(record["recurrence"]) if record["recurrence"] else None, record["revision"],
                     record["created_at"], record["updated_at"]),
                )
                changed += 1
            self._check_capacity(connection, owner)
        return {"restored": changed, "reviewed": len(records)}

    @staticmethod
    def _validate_restore(payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "exported_at", "events"}:
            raise CalendarError("backup must contain schema_version, exported_at, and events")
        if payload["schema_version"] != 1 or not isinstance(payload["events"], list):
            raise CalendarError("unsupported or malformed calendar backup")
        _audit_timestamp(payload["exported_at"], "backup exported_at")
        expected = _FIELDS | {"id", "source", "revision", "created_at", "updated_at"}
        validated = []
        seen = set()
        for raw in payload["events"]:
            if not isinstance(raw, dict) or set(raw) != expected:
                raise CalendarError("backup contains a malformed event")
            try:
                event_id = str(uuid.UUID(raw["id"]))
            except (ValueError, TypeError, AttributeError):
                raise CalendarError("backup event id must be a UUID")
            if event_id in seen:
                raise CalendarError("backup contains duplicate event ids")
            seen.add(event_id)
            if raw["source"] != "personal":
                raise CalendarError("backup event source must be personal")
            revision = raw["revision"]
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise CalendarError("backup event revision must be a positive integer")
            for field in ("created_at", "updated_at"):
                _audit_timestamp(raw[field], f"backup event {field}")
            clean = _validate_payload({field: raw[field] for field in _FIELDS})
            clean.update({"id": event_id, "source": "personal", "revision": revision,
                          "created_at": raw["created_at"], "updated_at": raw["updated_at"]})
            validated.append(clean)
        return validated

    def list_events(self, owner: Any, start: Any, end: Any) -> Dict[str, Any]:
        owner = self._owner(owner)
        range_start = self._parse_range(start, "start")
        range_end = self._parse_range(end, "end")
        if range_end <= range_start:
            raise CalendarError("range end must be after start")
        if range_end - range_start > timedelta(days=366):
            raise CalendarError("calendar range cannot exceed 366 days")
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM calendar_events WHERE owner=? ORDER BY start,id", (owner,)).fetchall()
        records = []
        occurrences = []
        for row in rows:
            record = self._record(row)
            expanded = list(self._occurrences(record, range_start, range_end))
            if expanded:
                records.append(record)
                occurrences.extend(expanded)
                if len(occurrences) > 10000:
                    raise CalendarError("calendar range contains more than 10000 occurrences", 413)
        occurrences.sort(key=lambda item: (item["start"], item["id"]))
        return {"events": occurrences, "records": records, "range": {"start": start, "end": end}}

    @staticmethod
    def _parse_range(value: Any, field: str) -> datetime:
        if not isinstance(value, str):
            raise CalendarError(f"range {field} must be an ISO datetime")
        if len(value) == 10 and "T" not in value:
            try:
                local_date = date.fromisoformat(value)
            except ValueError:
                raise CalendarError(f"range {field} must be an ISO date or datetime")
            london_midnight = datetime.combine(
                local_date, time.min, tzinfo=ZoneInfo("Europe/London")
            )
            return london_midnight.astimezone(timezone.utc)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CalendarError(f"range {field} must be an ISO datetime")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _occurrences(self, record: Dict[str, Any], range_start: datetime, range_end: datetime) -> Iterable[Dict[str, Any]]:
        all_day = record["all_day"]
        zone = _zone(record["timezone"])
        if all_day:
            original_start = datetime.combine(date.fromisoformat(record["start"]), time.min)
            original_end = datetime.combine(date.fromisoformat(record["end"]), time.min)
        else:
            original_start = datetime.fromisoformat(record["start"])
            original_end = datetime.fromisoformat(record["end"])
        duration = original_end - original_start
        recurrence = record["recurrence"]
        if recurrence is None:
            candidates = [original_start]
        else:
            until = (
                datetime.combine(date.fromisoformat(recurrence["until"]), time.max)
                if recurrence["until"] else None
            )
            # Search in wall-clock space, then use aware instants below for the
            # exact overlap decision. The margin covers even large civil offset
            # changes while duration subtraction finds events begun off-screen.
            local_range_start = range_start.astimezone(zone).replace(tzinfo=None)
            local_range_end = range_end.astimezone(zone).replace(tzinfo=None)
            search_start = local_range_start - duration - timedelta(days=2)
            search_end = local_range_end + timedelta(days=2)
            if recurrence["count"] is None:
                anchor = self._aligned_anchor(
                    original_start, recurrence["frequency"], recurrence["interval"], search_start
                )
                rule = rrule(
                    _RRULE_FREQUENCIES[recurrence["frequency"]], dtstart=anchor,
                    interval=recurrence["interval"], until=until,
                )
                candidates = []
                for scanned, candidate in enumerate(rule, 1):
                    if candidate > search_end:
                        break
                    if scanned > 10001:
                        raise CalendarError("recurrence expansion exceeded the safe scan limit", 413)
                    if candidate >= search_start:
                        candidates.append(candidate)
            else:
                # COUNT applies to valid local starts. dateutil correctly skips
                # impossible month/leap dates; this loop additionally excludes
                # timezone gaps from the count and is bounded by count <= 1000.
                candidates = []
                valid_count = 0
                rule = rrule(
                    _RRULE_FREQUENCIES[recurrence["frequency"]], dtstart=original_start,
                    interval=recurrence["interval"], until=until,
                )
                for scanned, candidate in enumerate(rule, 1):
                    if candidate > search_end:
                        break
                    if scanned > 10001:
                        raise CalendarError("recurrence expansion exceeded the safe scan limit", 413)
                    if all_day or self._generated_aware(candidate, zone) is not None:
                        valid_count += 1
                        if search_start <= candidate <= search_end:
                            candidates.append(candidate)
                        if valid_count == recurrence["count"]:
                            break
        for candidate in candidates:
            candidate_end = candidate + duration
            if all_day:
                aware_start = candidate.replace(tzinfo=zone)
                aware_end = candidate_end.replace(tzinfo=zone)
            else:
                aware_start = self._generated_aware(candidate, zone)
                if aware_start is None:
                    continue
                aware_end = self._generated_aware(candidate_end, zone)
                if aware_end is None:
                    original_start_aware = _aware_wall(original_start, zone, "start")
                    original_end_aware = _aware_wall(original_end, zone, "end")
                    elapsed = (
                        original_end_aware.astimezone(timezone.utc)
                        - original_start_aware.astimezone(timezone.utc)
                    )
                    aware_end = (aware_start.astimezone(timezone.utc) + elapsed).astimezone(zone)
            start_utc = aware_start.astimezone(timezone.utc)
            end_utc = aware_end.astimezone(timezone.utc)
            if start_utc >= range_end:
                continue
            if end_utc <= range_start:
                continue
            if all_day:
                rendered_start = candidate.date().isoformat()
                rendered_end = candidate_end.date().isoformat()
            else:
                rendered_start = aware_start.isoformat()
                rendered_end = aware_end.isoformat()
            recurring = recurrence is not None
            yield {
                "id": f"{record['id']}:{candidate.date().isoformat()}" if recurring else record["id"],
                "title": record["title"], "start": rendered_start, "end": rendered_end,
                "allDay": all_day,
                "extendedProps": {
                    "event_id": record["id"], "revision": record["revision"],
                    "category": record["category"], "recurrence": recurrence,
                    "original_start": record["start"], "source": "personal",
                    "location": record["location"], "notes": record["notes"],
                },
            }

    @staticmethod
    def _aligned_anchor(original: datetime, frequency: str, interval: int, target: datetime) -> datetime:
        """Move an unbounded rule near the view without changing its phase."""
        if target <= original:
            return original
        if frequency in {"daily", "weekly"}:
            unit_days = interval * (7 if frequency == "weekly" else 1)
            steps = max(0, (target - original).days // unit_days)
            return original + timedelta(days=steps * unit_days)
        if frequency == "monthly":
            distance = (target.year - original.year) * 12 + target.month - original.month
            steps = max(0, distance // interval)
            while steps:
                month_index = original.year * 12 + original.month - 1 + steps * interval
                year, month_zero = divmod(month_index, 12)
                try:
                    return original.replace(year=year, month=month_zero + 1)
                except ValueError:
                    steps -= 1
            return original
        steps = max(0, (target.year - original.year) // interval)
        while steps:
            try:
                return original.replace(year=original.year + steps * interval)
            except ValueError:
                steps -= 1
        return original

    @staticmethod
    def _generated_aware(wall: datetime, zone: ZoneInfo) -> Optional[datetime]:
        """Resolve generated wall time: skip gaps and choose fold 0 overlaps."""
        candidate = wall.replace(tzinfo=zone, fold=0)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        return candidate if roundtrip == wall else None
