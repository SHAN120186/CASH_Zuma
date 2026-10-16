"""Role-restricted files and sales receipt planning, using synthetic temporary data only."""
import hashlib
import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_site as base
from sqlalchemy import func, select
from app.db import (Account, Audit, BusinessProject, BusinessSourceFile, Category,
                    Delegation, Ledger, Receipt, ReportArchive, ReportArchiveFile,
                    TelegramLink, User, unit)
from app.security import NO_DOWNLOAD_ROLES

BOT = ('role-test-bot', 'temporary-role-test-secret-' + 'x' * 32)
PDF = b'%PDF-1.4\nsynthetic role-restricted document\n%%EOF\n'


class RoleDownloadTests(base.SiteTests):
    def fixtures(self):
        entry = self.ledger('100', reference='ROLE-READ').json()['id']
        document = self.client.post(f'/api/ledger/{entry}/document', content=PDF,
            headers={**self.h, 'X-Filename': 'synthetic.pdf'}).json()['id']
        payment = self.request('10', submit=False).json()['id']
        request_document = self.upload(payment).json()['id']
        owner = self.client.get('/api/me').json()['user']['id']
        with unit(True) as s:
            archive = ReportArchive(company_id=self.company, created_by=owner,
                title='Synthetic prepared report', period_start=base.today(),
                period_end=base.today(), request_key=str(uuid4()), version=1, status='ready')
            s.add(archive)
            s.flush()
            for format in ('pdf', 'xlsx'):
                s.add(ReportArchiveFile(company_id=self.company, archive_id=archive.id,
                    format=format, filename='synthetic.' + format, mime='application/octet-stream',
                    sha256=hashlib.sha256(PDF).hexdigest(), size=len(PDF), content=PDF))
            project = BusinessProject(company_id=self.company, created_by=owner,
                title='Synthetic private project', request_key=str(uuid4()))
            s.add(project)
            s.flush()
            source = BusinessSourceFile(company_id=self.company, project_id=project.id,
                relative_path='synthetic.pdf', filename='synthetic.pdf',
                sha256=hashlib.sha256(PDF).hexdigest(), size=len(PDF), content=PDF)
            s.add(source)
            s.flush()
            return {'archive': archive.id, 'project': project.id, 'source': source.id,
                    'document': document, 'request_document': request_document, 'payment': payment}

    def file_paths(self, records):
        company = self.company
        return [
            '/api/export/report.xlsx', '/api/export/report.pdf', '/api/export/ledger.csv',
            '/api/import/template.csv', f"/api/documents/{records['document']}",
            f"/api/request-documents/{records['request_document']}",
            f'/api/business-projects/template.xlsx?company_id={company}',
            f"/api/business-projects/{records['project']}/sources/{records['source']}/download?company_id={company}",
            f"/api/business-projects/{records['project']}/native.xlsx?company_id={company}&revision=0",
            *[f"/api/report-archives/{records['archive']}/files/{format}?company_id={company}"
              for format in ('pdf', 'xlsx')],
        ]

    def test_all_named_roles_are_denied_each_file_route_and_bot_archive(self):
        records = self.fixtures()
        with patch.dict(os.environ, {'BOT_CLIENT_ID': BOT[0], 'BOT_CLIENT_SECRET': BOT[1]}):
            for index, role in enumerate(sorted(NO_DOWNLOAD_ROLES)):
                with self.subTest(role=role):
                    client, headers = self.make_user(role, 'restricted_' + role)
                    self.addCleanup(client.close)
                    user = client.get('/api/bootstrap', headers=headers).json()['user']
                    self.assertNotIn('download', user['permissions'])
                    # Attachments follow request visibility, independently of report downloads.
                    for path in self.file_paths(records):
                        if path.startswith('/api/request-documents/'):
                            continue
                        response = client.get(path, headers=headers)
                        self.assertEqual(response.status_code, 403, (role, path, response.text))
                        self.assertNotIn('Content-Disposition', response.headers)
                        self.assertNotEqual(response.content, PDF)
                    telegram_id = 80000 + index
                    with unit(True) as s:
                        s.add(TelegramLink(user_id=user['id'], telegram_user_id=telegram_id))
                    for format in ('pdf', 'xlsx'):
                        response = self.client.get(
                            f"/api/bot/v1/report-archives/{records['archive']}/files/{format}",
                            params={'company_id': self.company, 'telegram_user_id': telegram_id}, auth=BOT)
                        self.assertEqual(response.status_code, 403, (role, format, response.text))
                    choices = self.client.get('/api/bot/v1/report-archive-companies',
                        params={'telegram_user_id': telegram_id}, auth=BOT)
                    self.assertEqual(choices.json()['items'], [])

    def test_request_attachments_follow_visibility_for_roles_and_previous_versions(self):
        author = self.make_user('employee', 'attachment_author')
        self.addCleanup(author[0].close)
        rid = self.request('10', client=author[0], headers=author[1]).json()['id']
        original = next(d for d in self.row(rid)['documents_list'] if d['kind'] == 'internal')
        replacement = PDF + b'% replacement version\n'
        replaced = self.upload(rid, client=author[0], headers=author[1], content=replacement)
        self.assertEqual(replaced.status_code, 200, replaced.text)
        current = next(d for d in self.row(rid)['documents_list'] if d['kind'] == 'internal' and d['current'])
        self.assertNotEqual(original['id'], current['id'])
        for role in ('finance', 'accountant', 'auditor'):
            actor = self.make_user(role, 'attachment_' + role)
            self.addCleanup(actor[0].close)
            permissions = actor[0].get('/api/bootstrap', headers=actor[1]).json()['user']['permissions']
            self.assertNotIn('download', permissions)
            self.assertEqual(actor[0].get(f'/api/requests/{rid}', headers=actor[1]).status_code, 200)
            for doc in (original, current):
                response = actor[0].get(doc['url'], headers=actor[1])
                self.assertEqual(response.status_code, 200, (role, response.text))
                self.assertIn('attachment;', response.headers['Content-Disposition'])
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                if doc == current:
                    self.assertEqual(response.content, replacement)
                else:
                    self.assertTrue(response.content.startswith(base.PDF))
        for role in ('employee', 'procurement'):
            owner = author if role == 'employee' else self.make_user(role, 'attachment_buyer')
            if role != 'employee':
                self.addCleanup(owner[0].close)
                own_rid = self.request('10', client=owner[0], headers=owner[1]).json()['id']
                document = self.row(own_rid)['documents_list'][0]
            else:
                document = current
            self.assertEqual(owner[0].get(document['url'], headers=owner[1]).status_code, 200)

    def test_request_attachments_reject_foreign_channel_company_and_invalid_sessions(self):
        rid = self.request('10').json()['id']
        doc = self.row(rid)['documents_list'][0]
        stranger = self.make_user('employee', 'attachment_stranger')
        cashier = self.make_user('cashier', 'attachment_cashier')
        foreign = self.user_in(self.company_ids()['ZUMA'], 'director', 'attachment_foreign')
        for actor in (stranger, cashier, foreign):
            self.addCleanup(actor[0].close)
        for actor, status in ((stranger, 403), (cashier, 403), (foreign, 404)):
            response = actor[0].get(doc['url'], headers=actor[1])
            self.assertEqual(response.status_code, status, response.text)
            self.assertNotIn('Content-Disposition', response.headers)
        self.assertEqual(foreign[0].get(doc['url'], headers={**foreign[1], 'X-Company-ID': str(self.company)}).status_code, 403)
        self.assertEqual(self.client.get(doc['url'], params={'company_id': self.company_ids()['ZUMA']}).status_code, 404)
        anonymous = base.TestClient(base.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get(doc['url']).status_code, 401)
        self.assertEqual(self.approve(rid).status_code, 200)
        accountant = self.make_user('accountant', 'attachment_bank_payer')
        self.addCleanup(accountant[0].close)
        self.assertEqual(accountant[0].get(doc['url'], headers=accountant[1]).status_code, 200)
        self.assertEqual(cashier[0].get(doc['url'], headers=cashier[1]).status_code, 403)
        cash_id = self.post('/api/accounts', {'name': 'Attachment cash', 'kind': 'cash', 'currency': 'UZS',
            'opening': '1000', 'opening_date': self.date}).json()['id']
        cash_rid = self.request('10', account=cash_id).json()['id']
        self.assertEqual(self.approve(cash_rid).status_code, 200)
        cash_doc = self.row(cash_rid)['documents_list'][0]
        self.assertEqual(cashier[0].get(cash_doc['url'], headers=cashier[1]).status_code, 200)
        self.assertEqual(accountant[0].get(cash_doc['url'], headers=accountant[1]).status_code, 403)
        user_id = accountant[0].get('/api/me').json()['user']['id']
        with unit(True) as s:
            s.get(User, user_id).active = False
        self.assertEqual(accountant[0].get(doc['url'], headers=accountant[1]).status_code, 401)

    def test_authorized_roles_keep_report_and_document_downloads(self):
        records = self.fixtures()
        with patch('app.erp.render_report', return_value=PDF):
            for role in ('admin', 'founder', 'director', 'operator', 'investor'):
                with self.subTest(role=role):
                    client, headers = self.make_user(role, 'allowed_' + role)
                    self.addCleanup(client.close)
                    self.assertIn('download', client.get('/api/bootstrap', headers=headers).json()['user']['permissions'])
                    for path in (self.file_paths(records)[:3] + self.file_paths(records)[4:6]
                                 + self.file_paths(records)[-2:]):
                        response = client.get(path, headers=headers)
                        self.assertEqual(response.status_code, 200, (role, path, response.text))
                        self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_finance_and_auditor_keep_report_view_but_archive_cannot_download(self):
        records = self.fixtures()
        for role in ('finance', 'auditor'):
            client, headers = self.make_user(role, 'viewer_' + role)
            self.addCleanup(client.close)
            self.assertEqual(client.get('/api/report', headers=headers).status_code, 200)
            listing = client.get('/api/report-archives', params={'company_id': self.company}, headers=headers)
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertEqual([record['id'] for record in listing.json()['items']], [records['archive']])
            self.assertFalse(listing.json()['items'][0]['can_download'])
            if role == 'finance':
                owner_id = client.get('/api/me').json()['user']['id']
                with unit(True) as s:
                    s.get(BusinessProject, records['project']).created_by = owner_id
                detail = client.get(f"/api/business-projects/{records['project']}",
                    params={'company_id': self.company}, headers=headers).json()
                self.assertTrue(detail['can_edit'])
                self.assertTrue(detail['files'])
                self.assertTrue(all('download_url' not in source for source in detail['files']))

    def sales_actor(self, name='sales_team'):
        client, headers = self.make_user('sales', name)
        self.addCleanup(client.close)
        bootstrap = client.get('/api/bootstrap', headers=headers).json()
        self.assertEqual(set(bootstrap['user']['permissions']), {'overview', 'receipts', 'receipt_edit', 'receipt_delete'})
        self.assertTrue(all(c['type'] == 'income' for c in bootstrap['categories']))
        self.assertFalse(bootstrap['counterparties'])
        self.assertTrue(all('balance' not in a and 'opening' not in a for a in bootstrap['accounts']))
        category = bootstrap['categories'][0]['id']
        body = {'account_id': self.acc, 'category_id': category, 'amount': '125.50',
                'date': self.date, 'counterparty': 'Synthetic customer'}
        return client, headers, body

    def test_sales_can_plan_expected_receipt_without_posting_actual_or_changing_budget(self):
        client, headers, body = self.sales_actor()
        before = self.client.get('/api/dashboard').json()['balance']
        with unit() as s:
            ledger_count = s.scalar(select(func.count()).select_from(Ledger))
        created = client.post('/api/receipts', json=body, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)
        receipt_id = created.json()['id']
        planned = client.get('/api/receipts', headers=headers)
        self.assertEqual(planned.status_code, 200)
        self.assertEqual([(r['id'], r['status'], r['amount']) for r in planned.json()],
                         [(receipt_id, 'expected', '125.50')])
        dashboard = client.get('/api/dashboard', headers=headers)
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertEqual(dashboard.json()['balance'], before)
        self.assertEqual(dashboard.json()['incoming7'], '125.50')
        for path in ('/api/accounts', '/api/budgets', '/api/ledger', '/api/requests',
                     '/api/report', '/api/model', '/api/audit', '/api/users', '/api/admin/meta',
                     '/api/business-projects?company_id=' + str(self.company),
                     '/api/report-archives?company_id=' + str(self.company),
                     '/api/group-report?company_id=' + str(self.company) + '&month=' + self.month,
                     '/api/report-schedules'):
            self.assertEqual(client.get(path, headers=headers).status_code, 403, path)
        actual = {**body, 'kind': 'in', 'reference': 'UNAUTHORIZED-RECEIPT', 'receipt_id': receipt_id}
        self.assertEqual(client.post('/api/ledger', json=actual, headers=headers).status_code, 403)
        self.assertEqual(client.post('/api/budgets', json={'category_id': self.cat,
            'month': self.month, 'currency': 'UZS', 'amount': '999', 'mode': 'hard',
            'reason': 'Synthetic unauthorized limit change'},
            headers=headers).status_code, 403)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), ledger_count)
            self.assertEqual(s.get(Receipt, receipt_id).status, 'expected')
            recorded = s.scalar(select(Audit).where(Audit.entity == 'receipt', Audit.entity_id == str(receipt_id)))
            self.assertEqual(recorded.role, 'sales')

    def test_sales_receipt_validates_income_scope_amount_and_account_dates(self):
        client, headers, body = self.sales_actor()
        with unit() as s:
            foreign_category = s.scalar(select(Category.id).where(Category.company_id != self.company))
        foreign = self.client.post('/api/accounts', headers={**self.h, 'X-Company-ID': str(self.company_ids()['ZUMA'])},
            json={'name': 'Foreign sales bank', 'kind': 'bank', 'currency': 'UZS',
                  'opening': '0', 'opening_date': self.date}).json()['id']
        cases = [({'category_id': self.cat}, 422), ({'category_id': foreign_category}, 404),
                 ({'account_id': foreign}, 404), ({'date': str(base.today() - timedelta(days=11))}, 422)]
        cases += [({'amount': value}, 422) for value in ('0', '-1', '1.001', 'NaN', 'Infinity')]
        for updates, code in cases:
            response = client.post('/api/receipts', json={**body, **updates}, headers=headers)
            self.assertEqual(response.status_code, code, (updates, response.text))
        with unit(True) as s:
            s.get(Account, self.acc).archived = True
        self.assertEqual(client.post('/api/receipts', json=body, headers=headers).status_code, 409)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Receipt)), 0)

    def test_sales_and_download_denials_survive_acting_director(self):
        records = self.fixtures()
        director, _ = self.make_user('director', 'replaced_director')
        self.addCleanup(director.close)
        replaced_id = director.get('/api/me').json()['user']['id']
        for role in sorted(NO_DOWNLOAD_ROLES):
            client, headers = self.make_user(role, 'acting_' + role)
            self.addCleanup(client.close)
            user_id = client.get('/api/me').json()['user']['id']
            with unit(True) as s:
                s.add(Delegation(company_id=self.company, user_id=user_id, replaced_user_id=replaced_id,
                    role='director', starts_on=base.today(), ends_on=base.today() + timedelta(days=1),
                    reason='Synthetic acting director assignment', created_by=1))
            bootstrap = client.get('/api/bootstrap', headers=headers).json()
            self.assertNotIn('download', bootstrap['user']['permissions'], role)
            self.assertEqual(client.get(self.file_paths(records)[-1], headers=headers).status_code, 403, role)
            if role == 'sales':
                self.assertEqual(set(bootstrap['user']['permissions']), {'overview', 'receipts', 'receipt_edit', 'receipt_delete'})
                self.assertEqual(bootstrap['user']['acting'], [])
                for path in ('/api/accounts', '/api/budgets', '/api/ledger', '/api/requests', '/api/report'):
                    self.assertEqual(client.get(path, headers=headers).status_code, 403, path)
                from app.bot_api import roles_in
                user = SimpleNamespace(id=user_id, role='sales')
                company = SimpleNamespace(id=self.company, code='UZGERMED')
                members = {self.company: {user_id: 'sales'}, '_valid': {self.company: {user_id}}}
                self.assertEqual(roles_in(user, company, members, {(self.company, user_id): {'director'}}), {'sales'})

    def test_native_file_denial_happens_before_expensive_calculation(self):
        records = self.fixtures()
        client, headers = self.make_user('finance', 'no_native_file')
        self.addCleanup(client.close)
        with patch('app.native_projects.snapshot') as snapshot, patch('app.native_projects.calculate_candidate') as calculate:
            response = client.get(self.file_paths(records)[8], headers=headers)
            self.assertEqual(response.status_code, 403)
            snapshot.assert_not_called()
            calculate.assert_not_called()


for _name in dir(base.SiteTests):
    if _name.startswith('test'):
        setattr(RoleDownloadTests, _name, None)


def tearDownModule():
    # Release the Windows SQLite handle; keep the shared temporary folder alive
    # when this module is included in a combined discovery run.
    base.engine.dispose()


if __name__ == '__main__':
    unittest.main(verbosity=2)
