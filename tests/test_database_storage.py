import sqlite3
import tempfile
import unittest
from pathlib import Path
from homed.calendar_store import CalendarStore


class DatabaseCompatibilityTests(unittest.TestCase):
    def test_calendar_refuses_newer_database_schema_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'calendar.sqlite3'
            connection=sqlite3.connect(path)
            connection.execute('PRAGMA user_version=99')
            connection.close()
            original=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'Unsupported'):
                CalendarStore(path)
            self.assertEqual(path.read_bytes(),original)
