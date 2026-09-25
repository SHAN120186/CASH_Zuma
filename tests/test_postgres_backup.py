import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.postgres_backup import create_dump, pg_environment, pg_tool


class BackupTests(unittest.TestCase):
    def url(self):
        return make_url('postgresql+psycopg://test:private-test-only@db.example/neondb'
                        '?sslmode=verify-full&channel_binding=require&sslrootcert=C%3A%2Fca.pem')

    def test_tls_and_credentials_passed_to_subprocess_without_changing_parent(self):
        base = {'PGSSLMODE': 'disable', 'PGCHANNELBINDING': 'disable'}
        env = pg_environment(self.url(), base)
        self.assertEqual(env['PGSSLMODE'], 'verify-full')
        self.assertEqual(env['PGCHANNELBINDING'], 'require')
        self.assertEqual(env['PGSSLROOTCERT'], 'C:/ca.pem')
        self.assertEqual(env['PGPASSWORD'], 'private-test-only')
        self.assertEqual(base['PGSSLMODE'], 'disable')

    def test_backup_client_override_preserves_local_server_selection(self):
        env = {'PG_BIN': '/postgres17/bin', 'PG_BACKUP_BIN': '/postgres18/bin'}
        self.assertIn('postgres18', pg_tool('pg_dump', env))
        self.assertEqual(env['PG_BIN'], '/postgres17/bin')
        self.assertIn('postgres17', pg_tool('pg_dump', {'PG_BIN': env['PG_BIN']}))
        self.assertEqual(pg_tool('pg_dump', {}), 'pg_dump')

    def test_success_uses_unique_names_and_never_sends_password_in_arguments(self):
        def run(args, **kwargs):
            self.assertNotIn('private-test-only', ' '.join(args))
            self.assertEqual(kwargs['env']['PGSSLMODE'], 'verify-full')
            if '--file' in args:
                Path(args[args.index('--file') + 1]).write_bytes(b'archive')
            return subprocess.CompletedProcess(args, 0)
        with tempfile.TemporaryDirectory() as folder, patch('deploy.postgres_backup.subprocess.run', side_effect=run):
            first = create_dump(self.url(), folder, {})
            second = create_dump(self.url(), folder, {})
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b'archive')
            self.assertEqual(second.read_bytes(), b'archive')
            self.assertEqual(list(Path(folder).glob('*.partial')), [])

    def test_failed_archive_validation_keeps_previous_backup_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = Path(folder) / 'previous.dump'
            previous.write_bytes(b'keep')
            error = subprocess.CalledProcessError(1, ['pg_restore', '--list'])
            with patch('deploy.postgres_backup.subprocess.run', side_effect=[subprocess.CompletedProcess([], 0), error]):
                with self.assertRaises(subprocess.CalledProcessError):
                    create_dump(self.url(), folder, {})
            self.assertEqual(list(Path(folder).iterdir()), [previous])
            self.assertEqual(previous.read_bytes(), b'keep')

    def test_version_mismatch_explains_fix_without_connection_secret(self):
        error = subprocess.CalledProcessError(1, ['pg_dump'], stderr=b'server version mismatch')
        with tempfile.TemporaryDirectory() as folder, patch('deploy.postgres_backup.subprocess.run', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'PG_BACKUP_BIN') as caught:
                create_dump(self.url(), folder, {})
            self.assertNotIn('private-test-only', str(caught.exception))
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
