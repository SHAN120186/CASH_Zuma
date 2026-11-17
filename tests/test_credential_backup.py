"""Synthetic verification of encrypted rows, without a key or real backups."""
from datetime import datetime, timedelta
import os
import unittest
import uuid

from deploy.credential_backup import (PRESENCE_SQL, ROWS_SQL, credential_snapshot,
                                      credential_snapshots_match)


STAMP = datetime(2026, 11, 17, 8, 30, 12, 123456)
ROWS = [(2, b'synthetic-encrypted-two', 'fingerprint-two', 'key-two', 7, STAMP),
        (1, b'synthetic-encrypted-one', 'fingerprint-one', 'key-one', None, STAMP)]


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self, rows=(), *, present=True):
        self.rows = rows
        self.present = present
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        if query == PRESENCE_SQL:
            return FakeCursor([(self.present,)])
        if query == ROWS_SQL:
            return FakeCursor(sorted(self.rows, key=lambda row: row[0]))
        raise AssertionError('Unexpected database operation')


class CredentialBackupTests(unittest.TestCase):
    def test_missing_table_does_not_read_rows(self):
        connection = FakeConnection(ROWS, present=False)
        self.assertEqual(credential_snapshot(connection), {'present': False, 'count': 0, 'sha256': None})
        self.assertEqual(connection.queries, [PRESENCE_SQL])

    def test_digest_is_ordered_normalizes_binary_and_returns_no_rows(self):
        source = credential_snapshot(FakeConnection(ROWS))
        alternate = [(row[0], memoryview(row[1]), *row[2:]) for row in reversed(ROWS)]
        self.assertEqual(source, credential_snapshot(FakeConnection(alternate)))
        self.assertEqual(set(source), {'present', 'count', 'sha256'})
        self.assertEqual(source['count'], 2)
        self.assertEqual(len(source['sha256']), 64)
        self.assertNotIn('synthetic-encrypted', str(source))
        self.assertNotIn('fingerprint-one', str(source))

    def test_every_column_and_the_row_count_are_verified(self):
        source = credential_snapshot(FakeConnection([ROWS[0]]))
        replacements = [3, b'changed-synthetic-encrypted', 'changed-fingerprint', 'changed-key', None,
                        STAMP + timedelta(microseconds=1)]
        for index, value in enumerate(replacements):
            with self.subTest(column=index):
                altered = list(ROWS[0])
                altered[index] = value
                self.assertFalse(credential_snapshots_match(source, credential_snapshot(FakeConnection([altered]))))
        self.assertFalse(credential_snapshots_match(source, credential_snapshot(FakeConnection(ROWS))))

    def test_digest_fields_have_unambiguous_boundaries(self):
        left = (1, b'synthetic', 'ab', 'c', None, STAMP)
        right = (1, b'synthetic', 'a', 'bc', None, STAMP)
        self.assertNotEqual(credential_snapshot(FakeConnection([left])), credential_snapshot(FakeConnection([right])))

    def test_absent_and_empty_are_distinct_for_backup_and_migration(self):
        absent = credential_snapshot(FakeConnection(present=False))
        empty = credential_snapshot(FakeConnection())
        populated = credential_snapshot(FakeConnection(ROWS))
        self.assertTrue(credential_snapshots_match(absent, absent))
        self.assertTrue(credential_snapshots_match(empty, empty))
        self.assertFalse(credential_snapshots_match(absent, empty))
        self.assertTrue(credential_snapshots_match(absent, empty, allow_new_empty=True))
        self.assertFalse(credential_snapshots_match(absent, populated, allow_new_empty=True))
        # A missing restored vault fails even if the source vault was empty.
        self.assertFalse(credential_snapshots_match(empty, absent))
        self.assertFalse(credential_snapshots_match(empty, absent, allow_new_empty=True))
        self.assertFalse(credential_snapshots_match(populated, absent, allow_new_empty=True))
        self.assertFalse(credential_snapshots_match(populated, empty, allow_new_empty=True))

    def test_read_errors_propagate_instead_of_reporting_an_empty_vault(self):
        class FailedRead(FakeConnection):
            def execute(self, query):
                if query == ROWS_SQL:
                    raise RuntimeError('Synthetic read failure')
                return super().execute(query)
        with self.assertRaisesRegex(RuntimeError, 'Synthetic read failure'):
            credential_snapshot(FailedRead())


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Local test PostgreSQL is not configured')
class PostgresCredentialBackupTests(unittest.TestCase):
    def test_real_bytea_timestamps_empty_schema_and_corruption(self):
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url
        url = make_url(os.environ['TEST_DATABASE_URL'])
        if not (url.database or '').startswith('zuma_test_') or url.host not in ('localhost', '127.0.0.1', '::1'):
            raise RuntimeError('Credential backup tests require an isolated local zuma_test_ database')
        schema = 'credential_backup_' + uuid.uuid4().hex
        with psycopg.connect(host=url.host, port=url.port or 5432, dbname=url.database,
                             user=url.username, password=url.password, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            try:
                connection.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
                absent = credential_snapshot(connection)
                self.assertFalse(absent['present'])
                connection.execute('CREATE TABLE user_credentials (user_id BIGINT PRIMARY KEY, ciphertext BYTEA NOT NULL, '
                                   'hash_fingerprint VARCHAR(64) NOT NULL, key_id VARCHAR(16) NOT NULL, '
                                   'updated_by BIGINT, updated_at TIMESTAMP NOT NULL)')
                empty = credential_snapshot(connection)
                self.assertTrue(credential_snapshots_match(absent, empty, allow_new_empty=True))
                self.assertFalse(credential_snapshots_match(empty, absent))
                for row in ROWS:
                    connection.execute('INSERT INTO user_credentials VALUES (%s,%s,%s,%s,%s,%s)', row)
                before = credential_snapshot(connection)
                self.assertEqual(before, credential_snapshot(FakeConnection(ROWS)))
                connection.execute('UPDATE user_credentials SET ciphertext=%s WHERE user_id=1', (b'synthetic-corruption',))
                self.assertFalse(credential_snapshots_match(before, credential_snapshot(connection)))
            finally:
                connection.execute('SET search_path TO pg_catalog')
                connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    unittest.main()
