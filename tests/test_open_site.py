import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy import open_site


class DesktopLauncherTests(unittest.TestCase):
    def test_local_only_starts_database_and_local_api_without_preview(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(open_site, 'load_config'), \
             patch.object(open_site, 'ensure_database') as database, \
             patch.object(open_site, 'healthy', return_value=True), \
             patch.object(open_site, 'launch') as launch, patch.object(open_site, 'running_url') as preview:
            self.assertEqual(open_site.ensure_local_site(), 'http://127.0.0.1:8001')
            database.assert_called_once()
            launch.assert_not_called()
            preview.assert_not_called()

    def test_existing_site_is_reused_without_starting_duplicate_processes(self):
        url = 'https://existing-preview.trycloudflare.com'
        with patch.object(Path, 'exists', return_value=True), patch.object(open_site, 'load_config'), \
             patch.object(open_site, 'ensure_database') as database, \
             patch.object(open_site, 'healthy', return_value=True), \
             patch.object(open_site, 'running_url', return_value=url), \
             patch.object(open_site, 'launch') as launch:
            self.assertEqual(open_site.ensure_site(), url)
            database.assert_called_once()
            launch.assert_not_called()

    def test_database_failure_prevents_opening_even_a_healthy_http_server(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(open_site, 'load_config'), \
             patch.object(open_site, 'ensure_database', side_effect=RuntimeError('database unavailable')), \
             patch.object(open_site, 'launch') as launch, patch.object(open_site, 'running_url') as url:
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                open_site.ensure_site()
            launch.assert_not_called()
            url.assert_not_called()

    def test_old_status_cannot_open_dead_or_unexpected_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            status = Path(folder) / 'status.json'
            with patch.object(open_site, 'STATUS', status):
                status.write_text(json.dumps({'state': 'running', 'port': 8002,
                                             'url': 'https://old-preview.trycloudflare.com'}))
                with patch.object(open_site, 'healthy', return_value=False):
                    self.assertIsNone(open_site.running_url())
                status.write_text(json.dumps({'state': 'running', 'port': 8002,
                                             'url': 'https://unrelated.example'}))
                with patch.object(open_site, 'healthy', return_value=True):
                    self.assertIsNone(open_site.running_url())

    def test_second_launcher_is_locked_but_lock_is_released_after_exit(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(open_site, 'RUNTIME', Path(folder)):
            with open_site.launcher_lock():
                with self.assertRaisesRegex(RuntimeError, 'уже выполняется'):
                    with open_site.launcher_lock():
                        self.fail('A concurrent launch must not proceed')
            with open_site.launcher_lock():
                pass

    def test_missing_local_settings_never_creates_new_empty_database(self):
        with patch.object(Path, 'exists', return_value=False), patch.object(open_site, 'launch') as launch:
            with self.assertRaisesRegex(RuntimeError, 'пустую базу'):
                open_site.ensure_site()
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
