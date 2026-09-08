import tempfile
import unittest
from pathlib import Path
from homed.observations import Observations


class ObservationTests(unittest.TestCase):
    def test_failed_collector_keeps_original_observation_time_across_restart(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / 'services.yaml'
            config.write_text('services:\n  optional:\n    driver: manual\n    intent: manual\n')
            clock = [1000.0]
            store = Observations(config, Path(td) / 'state.sqlite3', now=lambda: clock[0], storage_paths=[])
            store.collect_once()
            first = store.snapshot()
            self.assertEqual(first['services'][0]['name'], 'optional')
            self.assertEqual(first['observed_at'], 1000.0)
            config.unlink()
            clock[0] = 1200.0
            store.collect_once()
            aged = store.snapshot()
            self.assertEqual(aged['observed_at'], 1000.0)
            self.assertEqual(aged['last_success_at'], 1000.0)
            self.assertTrue(aged['error'])
            self.assertTrue(aged['stale'])
            reopened = Observations(config, Path(td) / 'state.sqlite3', now=lambda: clock[0], storage_paths=[])
            self.assertEqual(reopened.snapshot()['services'], first['services'])
            self.assertTrue(reopened.snapshot()['stale'])


if __name__ == '__main__':
    unittest.main()


class ObservationSafetyTests(unittest.TestCase):
    def test_optional_service_and_failed_health_are_distinct_observations(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'services.yaml'
            import sys, json
            path.write_text('services:\n  stopped:\n    driver: manual\n    intent: manual\n  wrong-app:\n    driver: manual\n    intent: always\n    health:\n      kind: command\n      command: ' + json.dumps([sys.executable,'-c','raise SystemExit(1)']) + '\n')
            store=Observations(path,Path(td)/'state.sqlite3',storage_paths=[])
            store.collect_once()
            rows={s['name']:s for s in store.snapshot()['services']}
            self.assertEqual(rows['stopped']['intent'],'manual')
            self.assertEqual(rows['wrong-app']['health_state'],'unhealthy')
            self.assertFalse(rows['wrong-app']['ok'])
            self.assertIsNone(store.snapshot()['error'])

    def test_registered_logs_are_bounded_and_credential_patterns_are_redacted(self):
        with tempfile.TemporaryDirectory() as td:
            import json
            log=Path(td)/'service.log'
            log.write_text('ordinary output\n'*2000+'password=private-value\nAuthorization: Bearer private-token\nurl=https://host/path?token=private-query\n')
            config=Path(td)/'services.yaml'
            config.write_text('services:\n  sample:\n    driver: manual\n    logs:\n      path: ' + json.dumps(str(log)) + '\n')
            store=Observations(config,Path(td)/'state.sqlite3',storage_paths=[])
            result=store.logs('sample')
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len(result['text']),16000)
            self.assertNotIn('private-',result['text'])
            with self.assertRaises(ValueError): store.logs('../service.log')


class PartialCollectionTests(unittest.TestCase):
    def test_failed_manager_boundary_cannot_publish_cached_success_as_current(self):
        import json,sys
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            config=Path(td)/'services.yaml'
            config.write_text('services:\n  sample:\n    driver: manual\n    intent: always\n    health:\n      kind: command\n      command: '+json.dumps([sys.executable,'-c','pass'])+'\n')
            clock=[1000.0]
            store=Observations(config,Path(td)/'state.sqlite3',now=lambda:clock[0],storage_paths=[])
            store.collect_once()
            self.assertTrue(store.snapshot()['services'][0]['ok'])
            clock[0]=1010.0
            with patch('subprocess.run',side_effect=PermissionError('fixture denied')):
                store.collect_once()
            snapshot=store.snapshot()
            self.assertFalse(snapshot['services'][0]['ok'])
            self.assertEqual(snapshot['services'][0]['observed_at'],1000.0)
            self.assertEqual(snapshot['last_success_at'],1000.0)
            self.assertEqual(snapshot['observed_at'],1000.0)
            self.assertTrue(snapshot['error'])
            self.assertTrue(snapshot['stale'])

    def test_log_tail_does_not_expose_part_of_a_long_secret_assignment(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            log=Path(td)/'service.log'
            log.write_text('password='+('sensitive-secret-'*2000)+'\nordinary final line\n')
            config=Path(td)/'services.yaml'
            config.write_text('services:\n  sample:\n    driver: manual\n    logs:\n      path: '+json.dumps(str(log))+'\n')
            store=Observations(config,Path(td)/'state.sqlite3',storage_paths=[])
            output=store.logs('sample')['text']
            self.assertNotIn('sensitive-secret',output)
            self.assertIn('ordinary final line',output)
