import json
import tempfile
import unittest
from copy import deepcopy
import stat
from pathlib import Path

from homed.calendar_store import CalendarError, CalendarStore


class CalendarStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "calendar.sqlite3"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_event_persists_across_reopen_and_is_isolated_by_owner(self):
        store = CalendarStore(self.db_path)
        created = store.create_event(
            "charlie",
            {
                "title": "Dentist",
                "start": "2026-09-09T10:00:00",
                "end": "2026-09-09T10:45:00",
                "timezone": "Europe/London",
                "all_day": False,
                "category": "personal",
                "location": "High Street",
                "notes": "Bring paperwork",
                "recurrence": None,
            },
        )

        reopened = CalendarStore(self.db_path)
        fetched = reopened.get_event("charlie", created["id"])
        self.assertEqual(fetched["title"], "Dentist")
        self.assertEqual(fetched["start"], "2026-09-09T10:00:00")
        self.assertEqual(fetched["source"], "personal")
        self.assertEqual(fetched["revision"], 1)
        self.assertNotIn("owner", fetched)
        with self.assertRaises(CalendarError) as hidden:
            reopened.get_event("someone-else", created["id"])
        self.assertEqual(hidden.exception.status, 404)

    def test_stale_revision_cannot_update_or_delete_an_event(self):
        store = CalendarStore(self.db_path)
        created = store.create_event("charlie", timed_event())
        updated = store.update_event("charlie", created["id"], {"title": "New title"}, 1)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["title"], "New title")

        with self.assertRaises(CalendarError) as stale_update:
            store.update_event("charlie", created["id"], {"notes": "stale"}, 1)
        self.assertEqual(stale_update.exception.status, 409)
        with self.assertRaises(CalendarError) as stale_delete:
            store.delete_event("charlie", created["id"], 1)
        self.assertEqual(stale_delete.exception.status, 409)
        store.delete_event("charlie", created["id"], 2)
        with self.assertRaises(CalendarError) as missing:
            store.get_event("charlie", created["id"])
        self.assertEqual(missing.exception.status, 404)

    def test_nonrecurring_listing_is_aware_exclusive_and_database_is_private(self):
        store = CalendarStore(self.db_path)
        created = store.create_event("charlie", timed_event())
        store.create_event("another-user", timed_event(title="Private"))

        result = store.list_events("charlie", "2026-09-09T09:00:00Z", "2026-09-09T10:00:00Z")
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["id"], created["id"])
        self.assertEqual(result["events"][0]["start"], "2026-09-09T10:00:00+01:00")
        self.assertEqual(result["range"]["end"], "2026-09-09T10:00:00Z")
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o600)

        excluded = store.list_events("charlie", "2026-09-09T11:00:00+01:00", "2026-09-09T12:00:00+01:00")
        self.assertEqual(excluded["events"], [])

    def test_date_only_ranges_use_exclusive_london_midnights_across_dst(self):
        store = CalendarStore(self.db_path)
        store.create_event(
            "charlie",
            {
                "title": "September day", "start": "2026-09-10", "end": "2026-09-11",
                "timezone": "Europe/London", "all_day": True, "category": "occasion",
            },
        )
        store.create_event(
            "charlie",
            timed_event(
                title="After spring boundary", start="2026-03-30T00:30:00", end="2026-03-30T01:00:00"
            ),
        )
        store.create_event(
            "charlie",
            timed_event(
                title="After autumn lower boundary", start="2026-10-25T00:30:00", end="2026-10-25T00:45:00"
            ),
        )

        before_september = store.list_events("charlie", "2026-09-09", "2026-09-10")
        self.assertEqual(before_september["events"], [])
        september = store.list_events("charlie", "2026-09-10", "2026-09-11")
        self.assertEqual([event["title"] for event in september["events"]], ["September day"])
        before_spring = store.list_events("charlie", "2026-03-29", "2026-03-30")
        self.assertEqual(before_spring["events"], [])
        autumn = store.list_events("charlie", "2026-10-25", "2026-10-26")
        self.assertEqual([event["title"] for event in autumn["events"]], ["After autumn lower boundary"])

    def test_export_restore_is_idempotent_and_rejects_cross_owner_collision(self):
        source = CalendarStore(self.db_path)
        created = source.create_event("charlie", timed_event(title="Passport renewal"))
        backup = source.export_events("charlie")
        self.assertNotIn("owner", backup["events"][0])

        restored_path = Path(self.tempdir.name) / "restored.sqlite3"
        restored = CalendarStore(restored_path)
        self.assertEqual(restored.restore_events("charlie", backup), {"restored": 1, "reviewed": 1})
        self.assertEqual(restored.restore_events("charlie", backup), {"restored": 0, "reviewed": 1})
        self.assertEqual(restored.get_event("charlie", created["id"])["title"], "Passport renewal")
        with self.assertRaises(CalendarError) as collision:
            restored.restore_events("another-user", backup)
        self.assertEqual(collision.exception.status, 409)

    def test_restore_rejects_stale_or_divergent_records_without_partial_merge(self):
        source = CalendarStore(self.db_path)
        first = source.create_event("charlie", timed_event(title="One"))
        second = source.create_event(
            "charlie", timed_event(title="Two", start="2026-09-10T10:00:00", end="2026-09-10T11:00:00")
        )
        backup = source.export_events("charlie")
        target = CalendarStore(Path(self.tempdir.name) / "conflicts.sqlite3")
        target.restore_events("charlie", backup)

        divergent = deepcopy(backup)
        divergent["events"][0]["title"] = "Different at the same revision"
        with self.assertRaises(CalendarError) as equal_revision:
            target.restore_events("charlie", divergent)
        self.assertEqual(equal_revision.exception.status, 409)
        self.assertEqual(target.get_event("charlie", first["id"])["title"], "One")

        target.delete_event("charlie", first["id"], 1)
        target.update_event("charlie", second["id"], {"title": "Locally newer"}, 1)
        with self.assertRaises(CalendarError) as stale:
            target.restore_events("charlie", backup)
        self.assertEqual(stale.exception.status, 409)
        with self.assertRaises(CalendarError):
            target.get_event("charlie", first["id"])
        newer = target.get_event("charlie", second["id"])
        self.assertEqual((newer["title"], newer["revision"]), ("Locally newer", 2))

    def test_malformed_restore_is_rejected_atomically_before_any_write(self):
        source = CalendarStore(self.db_path)
        source.create_event("charlie", timed_event(title="One"))
        source.create_event("charlie", timed_event(title="Two", start="2026-09-10T10:00:00", end="2026-09-10T11:00:00"))
        backup = source.export_events("charlie")
        malformed = deepcopy(backup)
        malformed["events"][1]["category"] = "work"

        target = CalendarStore(Path(self.tempdir.name) / "target.sqlite3")
        target.create_event("charlie", timed_event(title="Existing"))
        with self.assertRaises(CalendarError):
            target.restore_events("charlie", malformed)
        result = target.list_events("charlie", "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z")
        self.assertEqual([record["title"] for record in result["records"]], ["Existing"])

    def test_restore_requires_real_aware_timestamps(self):
        store = CalendarStore(self.db_path)
        store.create_event("charlie", timed_event())
        backup = store.export_events("charlie")

        for field_path in (("exported_at",), ("events", 0, "created_at"), ("events", 0, "updated_at")):
            malformed = deepcopy(backup)
            target = malformed
            for part in field_path[:-1]:
                target = target[part]
            target[field_path[-1]] = "2026-09-08"
            with self.subTest(field_path=field_path):
                with self.assertRaises(CalendarError):
                    CalendarStore(Path(self.tempdir.name) / "timestamps.sqlite3").restore_events("charlie", malformed)

    def test_weekly_recurrence_keeps_london_wall_time_across_dst(self):
        store = CalendarStore(self.db_path)
        created = store.create_event(
            "charlie",
            timed_event(
                title="Sunday walk",
                start="2026-03-22T10:00:00",
                end="2026-03-22T11:00:00",
                recurrence={"frequency": "weekly", "interval": 1, "until": None, "count": 3},
            ),
        )
        result = store.list_events("charlie", "2026-03-20T00:00:00Z", "2026-04-10T00:00:00Z")

        self.assertEqual(
            [event["start"] for event in result["events"]],
            ["2026-03-22T10:00:00+00:00", "2026-03-29T10:00:00+01:00", "2026-04-05T10:00:00+01:00"],
        )
        self.assertEqual([event["end"][-6:] for event in result["events"]], ["+00:00", "+01:00", "+01:00"])
        self.assertEqual(result["events"][1]["extendedProps"]["event_id"], created["id"])
        self.assertEqual(result["records"][0]["start"], "2026-03-22T10:00:00")

    def test_yearly_all_day_occasion_uses_exclusive_end_dates(self):
        store = CalendarStore(self.db_path)
        store.create_event(
            "charlie",
            {
                "title": "Anniversary",
                "start": "2026-09-08",
                "end": "2026-09-09",
                "timezone": "Europe/London",
                "all_day": True,
                "category": "occasion",
                "recurrence": {"frequency": "yearly", "interval": 1, "until": None, "count": 2},
            },
        )
        result = store.list_events("charlie", "2026-09-08T00:00:00Z", "2027-09-09T00:00:00Z")
        self.assertEqual(
            [(event["start"], event["end"], event["allDay"]) for event in result["events"]],
            [("2026-09-08", "2026-09-09", True), ("2027-09-08", "2027-09-09", True)],
        )

    def test_month_end_and_leap_day_counts_include_only_real_dates(self):
        store = CalendarStore(self.db_path)
        store.create_event(
            "charlie",
            timed_event(
                title="Month end", start="2026-01-31T10:00:00", end="2026-01-31T11:00:00",
                recurrence={"frequency": "monthly", "interval": 1, "until": None, "count": 3},
            ),
        )
        store.create_event(
            "charlie",
            {
                "title": "Leap day", "start": "2024-02-29", "end": "2024-03-01",
                "timezone": "Europe/London", "all_day": True, "category": "occasion",
                "recurrence": {"frequency": "yearly", "interval": 1, "until": None, "count": 2},
            },
        )
        monthly = store.list_events("charlie", "2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z")
        self.assertEqual(
            [event["start"][:10] for event in monthly["events"] if event["title"] == "Month end"],
            ["2026-01-31", "2026-03-31", "2026-05-31"],
        )
        leap = store.list_events("charlie", "2028-02-01T00:00:00Z", "2028-03-02T00:00:00Z")
        self.assertEqual([event["start"] for event in leap["events"]], ["2028-02-29"])

    def test_generated_dst_gap_is_skipped_overlap_uses_first_fold_and_long_event_overlaps(self):
        store = CalendarStore(self.db_path)
        store.create_event(
            "charlie",
            timed_event(
                title="DST gap", start="2026-03-27T01:30:00", end="2026-03-27T02:30:00",
                recurrence={"frequency": "daily", "interval": 1, "until": None, "count": 4},
            ),
        )
        spring = store.list_events("charlie", "2026-03-27T00:00:00Z", "2026-04-01T00:00:00Z")
        self.assertEqual(
            [event["start"][:10] for event in spring["events"]],
            ["2026-03-27", "2026-03-28", "2026-03-30", "2026-03-31"],
        )

        store.create_event(
            "charlie",
            timed_event(
                title="DST overlap", start="2026-10-18T01:30:00", end="2026-10-18T02:30:00",
                recurrence={"frequency": "weekly", "interval": 1, "until": None, "count": 2},
            ),
        )
        autumn = store.list_events("charlie", "2026-10-25T00:00:00Z", "2026-10-26T00:00:00Z")
        self.assertEqual(len(autumn["events"]), 1)
        self.assertEqual(autumn["events"][0]["start"], "2026-10-25T01:30:00+01:00")

        separate = CalendarStore(Path(self.tempdir.name) / "overlap.sqlite3")
        separate.create_event(
            "charlie",
            timed_event(
                title="Gap ending", start="2026-03-28T00:30:00", end="2026-03-28T01:30:00",
                recurrence={"frequency": "daily", "interval": 1, "until": None, "count": 2},
            ),
        )
        gap_end = separate.list_events("charlie", "2026-03-29T00:00:00Z", "2026-03-30T00:00:00Z")
        self.assertEqual(gap_end["events"][0]["end"], "2026-03-29T02:30:00+01:00")

        separate = CalendarStore(Path(self.tempdir.name) / "long-overlap.sqlite3")
        separate.create_event(
            "charlie",
            timed_event(title="Long retreat", start="2026-04-01T10:00:00", end="2026-04-10T10:00:00"),
        )
        overlap = separate.list_events("charlie", "2026-04-05T00:00:00Z", "2026-04-06T00:00:00Z")
        self.assertEqual([event["title"] for event in overlap["events"]], ["Long retreat"])

    def test_validation_rejects_reserved_fields_bad_ranges_and_dst_edges(self):
        store = CalendarStore(self.db_path)
        cases = [
            timed_event(owner="attacker"),
            timed_event(source="work"),
            timed_event(title=""),
            timed_event(start="2026-09-09T11:00:00", end="2026-09-09T10:00:00"),
            timed_event(start="2026-03-29T01:30:00", end="2026-03-29T02:30:00"),
            timed_event(start="2026-10-25T01:30:00", end="2026-10-25T02:30:00"),
            timed_event(recurrence={"frequency": "weekly", "interval": 0, "until": None, "count": None}),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(CalendarError):
                    store.create_event("charlie", payload)
        with self.assertRaises(CalendarError):
            store.list_events("charlie", "2026-01-01T00:00:00Z", "2027-01-03T00:00:00Z")

    def test_capacity_rejects_create_and_update_without_changing_saved_events(self):
        store = CalendarStore(self.db_path, max_events=1)
        created = store.create_event("charlie", timed_event(title="Kept"))
        with self.assertRaises(CalendarError) as count_error:
            store.create_event(
                "charlie", timed_event(title="Rejected", start="2026-09-10T10:00:00", end="2026-09-10T11:00:00")
            )
        self.assertEqual(count_error.exception.status, 413)
        self.assertIn("capacity", str(count_error.exception).lower())
        self.assertEqual([event["id"] for event in store.export_events("charlie")["events"]], [created["id"]])

        baseline = len(json.dumps(store.export_events("charlie"), ensure_ascii=True).encode("utf-8"))
        size_limited = CalendarStore(self.db_path, max_events=1, max_export_bytes=baseline + 20)
        with self.assertRaises(CalendarError) as size_error:
            size_limited.update_event("charlie", created["id"], {"notes": "x" * 100}, created["revision"])
        self.assertEqual(size_error.exception.status, 413)
        unchanged = size_limited.get_event("charlie", created["id"])
        self.assertEqual((unchanged["notes"], unchanged["revision"]), ("", 1))

    def test_restore_capacity_failure_rolls_back_the_whole_merge(self):
        source = CalendarStore(self.db_path)
        source.create_event("charlie", timed_event(title="Imported one"))
        source.create_event(
            "charlie", timed_event(title="Imported two", start="2026-09-10T10:00:00", end="2026-09-10T11:00:00")
        )
        backup = source.export_events("charlie")

        target = CalendarStore(Path(self.tempdir.name) / "capacity.sqlite3", max_events=2)
        existing = target.create_event("charlie", timed_event(title="Existing"))
        with self.assertRaises(CalendarError) as error:
            target.restore_events("charlie", backup)
        self.assertEqual(error.exception.status, 413)
        self.assertEqual([event["id"] for event in target.export_events("charlie")["events"]], [existing["id"]])

    def test_export_larger_than_one_megabyte_restores_within_supported_envelope(self):
        source = CalendarStore(self.db_path)
        notes = "x" * 10000
        for index in range(110):
            source.create_event(
                "charlie",
                timed_event(
                    title=f"Large backup event {index}", notes=notes,
                    start=f"2026-10-{index % 28 + 1:02d}T10:00:00",
                    end=f"2026-10-{index % 28 + 1:02d}T11:00:00",
                ),
            )
        backup = source.export_events("charlie")
        self.assertGreater(len(json.dumps(backup, ensure_ascii=True).encode("utf-8")), 1024 * 1024)

        restored = CalendarStore(Path(self.tempdir.name) / "large-restore.sqlite3")
        self.assertEqual(restored.restore_events("charlie", backup), {"restored": 110, "reviewed": 110})
        self.assertEqual(len(restored.export_events("charlie")["events"]), 110)


def timed_event(**changes):
    payload = {
        "title": "Appointment",
        "start": "2026-09-09T10:00:00",
        "end": "2026-09-09T11:00:00",
        "timezone": "Europe/London",
        "all_day": False,
        "category": "personal",
        "location": "",
        "notes": "",
        "recurrence": None,
    }
    payload.update(changes)
    return payload


if __name__ == "__main__":
    unittest.main()
