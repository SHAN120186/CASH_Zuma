"""The counterparty catalog accepts distinct INNs for the same name."""
import os
import unittest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from app.company_scope import migrate_counterparty_names


class CounterpartyNameMigrationTests(unittest.TestCase):
    def test_sqlite_retains_rows_columns_indexes_triggers_and_foreign_keys(self):
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql('PRAGMA foreign_keys=ON')
                conn.exec_driver_sql('CREATE TABLE companies (id INTEGER PRIMARY KEY)')
                conn.exec_driver_sql('INSERT INTO companies VALUES (1)')
                conn.exec_driver_sql('CREATE TABLE counterparties (id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), name VARCHAR(160) NOT NULL, inn VARCHAR(20) NOT NULL, note TEXT NOT NULL, future_column TEXT, CONSTRAINT old_name UNIQUE(company_id, name))')
                conn.exec_driver_sql("INSERT INTO counterparties VALUES (17, 1, 'Same', '111111111', 'kept', 'newer data')")
                conn.exec_driver_sql('CREATE UNIQUE INDEX ux_counterparties_company_name ON counterparties(company_id,name)')
                conn.exec_driver_sql('CREATE INDEX other_counterparty_index ON counterparties(inn)')
                conn.exec_driver_sql('CREATE TABLE references_cp (counterparty_id INTEGER REFERENCES counterparties(id))')
                conn.exec_driver_sql('INSERT INTO references_cp VALUES (17)')
                conn.exec_driver_sql('CREATE TABLE trigger_log (cp_id INTEGER)')
                conn.exec_driver_sql('CREATE TRIGGER counterparties_insert AFTER INSERT ON counterparties BEGIN INSERT INTO trigger_log VALUES (NEW.id); END')
            migrate_counterparty_names(engine)
            migrate_counterparty_names(engine)
            with engine.begin() as conn:
                self.assertEqual(conn.exec_driver_sql('SELECT * FROM counterparties').fetchone(), (17, 1, 'Same', '111111111', 'kept', 'newer data'))
                self.assertEqual(conn.exec_driver_sql('SELECT * FROM references_cp').fetchone()[0], 17)
                self.assertFalse(conn.exec_driver_sql('PRAGMA foreign_key_check').fetchall())
                indexes = {i['name'] for i in inspect(conn).get_indexes('counterparties')}
                self.assertIn('other_counterparty_index', indexes)
                self.assertNotIn('ux_counterparties_company_name', indexes)
                conn.exec_driver_sql("INSERT INTO counterparties VALUES (18, 1, 'Same', '222222222', '', NULL)")
                self.assertEqual(conn.exec_driver_sql('SELECT cp_id FROM trigger_log').fetchall(), [(18,)])
        finally:
            engine.dispose()

    def test_sqlite_drops_named_index_without_rebuilding_table(self):
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql('CREATE TABLE counterparties (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT, inn TEXT)')
                conn.exec_driver_sql('CREATE UNIQUE INDEX ux_counterparties_company_name ON counterparties(company_id,name)')
                conn.exec_driver_sql("INSERT INTO counterparties VALUES (1, 1, 'Same', '1')")
            migrate_counterparty_names(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql("INSERT INTO counterparties VALUES (2, 1, 'Same', '2')")
                self.assertEqual(conn.exec_driver_sql('SELECT COUNT(*) FROM counterparties').scalar_one(), 2)
        finally:
            engine.dispose()

    @unittest.skipUnless(os.getenv('COUNTERPARTY_MIGRATION_TEST_DATABASE_URL'), 'No disposable local PostgreSQL configured')
    def test_postgres_retains_rows_and_removes_both_name_uniqueness_forms(self):
        url = make_url(os.environ['COUNTERPARTY_MIGRATION_TEST_DATABASE_URL'])
        if (url.get_backend_name() != 'postgresql' or url.host not in ('127.0.0.1', 'localhost')
                or not (url.database or '').startswith('zuma_test_')):
            raise RuntimeError('Only a disposable local PostgreSQL test database is permitted')
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql('CREATE TABLE companies (id INTEGER PRIMARY KEY)')
                conn.exec_driver_sql('INSERT INTO companies VALUES (1)')
                conn.exec_driver_sql('CREATE TABLE counterparties (id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), name VARCHAR(160) NOT NULL, inn VARCHAR(20) NOT NULL, note TEXT NOT NULL, CONSTRAINT old_name UNIQUE(company_id,name))')
                conn.exec_driver_sql('CREATE UNIQUE INDEX ux_counterparties_company_name ON counterparties(company_id,name)')
                conn.exec_driver_sql('CREATE INDEX other_counterparty_index ON counterparties(inn)')
                conn.exec_driver_sql("INSERT INTO counterparties VALUES (17, 1, 'Same', '111111111', 'kept')")
                conn.exec_driver_sql('CREATE TABLE references_cp (counterparty_id INTEGER REFERENCES counterparties(id))')
                conn.exec_driver_sql('INSERT INTO references_cp VALUES (17)')
            migrate_counterparty_names(engine)
            migrate_counterparty_names(engine)
            with engine.begin() as conn:
                self.assertEqual(conn.exec_driver_sql('SELECT * FROM counterparties').fetchone(), (17, 1, 'Same', '111111111', 'kept'))
                self.assertEqual(conn.exec_driver_sql('SELECT * FROM references_cp').fetchone()[0], 17)
                indexes = {i['name'] for i in inspect(conn).get_indexes('counterparties')}
                self.assertIn('other_counterparty_index', indexes)
                self.assertNotIn('ux_counterparties_company_name', indexes)
                self.assertNotIn(['company_id', 'name'], [c['column_names'] for c in inspect(conn).get_unique_constraints('counterparties')])
                conn.exec_driver_sql("INSERT INTO counterparties VALUES (18, 1, 'Same', '222222222', '')")
                self.assertEqual(conn.exec_driver_sql('SELECT COUNT(*) FROM counterparties').scalar_one(), 2)
        finally:
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
