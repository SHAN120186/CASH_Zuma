"""Доступ по компаниям и центр администрирования (2.12.5).

Каждый тест воспроизводит найденное нарушение: на версии 2.12.4 он падает, после
исправления проходит. Файл запускается отдельно: python tests/test_access.py
"""
import os, sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Временная БД, клиент и помощники SiteTests; сами тесты SiteTests здесь не повторяются.
import tests.test_site as base
from tests.test_site import PASSWORD, TestClient, app
from sqlalchemy import select, delete, func
from app.db import unit, User, CompanyUser, Company, LoginSession, Audit, TelegramLink, TelegramGroup, PaymentRequest, Delegation, initialize

PDF = b'%PDF-1.4\nsynthetic request document'


class AccessTests(base.SiteTests):
    def login(self, name, password=PASSWORD):
        cl = TestClient(app)
        r = cl.post('/api/login', json={'username': name, 'password': password})
        return cl, r

    def staff(self, company_code, role, name):
        """Сотрудник одной компании; временный пароль уже сменён."""
        ids = self.company_ids()
        cl, h = self.user_in(ids[company_code], role, name)
        return cl, h

    def user_id(self, name):
        with unit() as s:
            return s.scalar(select(User.id).where(User.username == name).execution_options(company_unscoped=True))

    def links(self, name):
        with unit() as s:
            companies = {c.id: c.code for c in s.scalars(select(Company))}
            uid = s.scalar(select(User.id).where(User.username == name).execution_options(company_unscoped=True))
            return sorted((companies[m.company_id], m.role) for m in s.scalars(select(CompanyUser).where(CompanyUser.user_id == uid)))

    def draft(self, client, headers, amount='100'):
        """Черновик (компания, канал, валюта и приоритет из формы) с внутренней заявкой."""
        body = {**self.request_body(amount, 'Закупка материалов для производства'), 'status': 'draft'}
        r = client.post('/api/requests', json=body, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()['id']
        up = client.post(f'/api/requests/{rid}/documents', params={'kind': 'internal'}, content=PDF,
                         headers={**headers, 'Content-Type': 'application/pdf', 'X-Filename': 'indent.pdf'})
        self.assertEqual(up.status_code, 200, up.text)
        return rid, up.json()['id']

    # ---- 1. Закупки и чужие заявки
    def test_procurement_sees_only_own_requests_files_and_no_full_budget(self):
        self.budget('100000', 'soft')
        a = self.staff('UZGERMED', 'procurement', 'buyer_a')
        b = self.staff('UZGERMED', 'procurement', 'buyer_b')
        try:
            ra, da = self.draft(*a)
            rb, db = self.draft(*b, amount='200')
            listed = a[0].get('/api/requests', headers=a[1]).json()
            self.assertEqual([x['id'] for x in listed], [ra], 'procurement lists only own requests')
            self.assertTrue(listed[0]['budget'].get('hidden'), 'the full budget of the category stays hidden')
            self.assertNotIn('limit', {k for k, v in listed[0]['budget'].items() if v is not None})
            paged = a[0].get('/api/requests?paginated=true', headers=a[1]).json()
            self.assertEqual(paged['total'], 1, 'totals do not count foreign requests')
            self.assertIn(a[0].get(f'/api/requests/{rb}/documents', headers=a[1]).status_code, (403, 404))
            self.assertIn(a[0].get(f'/api/request-documents/{db}', headers=a[1]).status_code, (403, 404))
            up = a[0].post(f'/api/requests/{rb}/documents', params={'kind': 'other'}, content=PDF,
                           headers={**a[1], 'Content-Type': 'application/pdf', 'X-Filename': 'x.pdf'})
            self.assertIn(up.status_code, (403, 404))
            self.assertIn(a[0].delete(f'/api/request-documents/{db}', headers=a[1]).status_code, (403, 404))
            for path in ('/api/accounts', '/api/dashboard', '/api/ledger', '/api/budgets', '/api/export/ledger.csv'):
                self.assertEqual(a[0].get(path, headers=a[1]).status_code, 403, path)
            self.assertEqual(a[0].get('/api/bootstrap', headers=a[1]).json()['counterparties'], [])
            # Форма заявки показывает только карточку выбранной статьи на период, без остатков счетов.
            check = a[0].get(f'/api/budget-check?account_id={self.acc}&category_id={self.cat}&amount=10&date={self.dues["urgent"]}', headers=a[1])
            self.assertEqual(check.status_code, 200)
            self.assertEqual(set(check.json()), {'period', 'currency', 'budget_set', 'limit', 'used', 'reserved', 'available', 'after', 'status', 'mode'})
            self.assertFalse({'balance', 'actual', 'opening', 'spent', 'remaining'} & set(check.json()))
            self.assertEqual(listed[0]['budget_card']['period'], check.json()['period'])
        finally:
            a[0].close(); b[0].close()

    # ---- 2. Исторические связи со всеми компаниями
    def test_legacy_links_never_open_a_second_company_and_restart_does_not_restore_them(self):
        ids = self.company_ids()
        cl, h = self.staff('UZGERMED', 'employee', 'legacy_staff')
        try:
            # Старая миграция создавала связи со всеми компаниями без роли.
            with unit(True) as s:
                uid = s.scalar(select(User.id).where(User.username == 'legacy_staff'))
                for c in s.scalars(select(Company)):
                    if not s.get(CompanyUser, (c.id, uid)):
                        s.add(CompanyUser(company_id=c.id, user_id=uid, role=None))
            initialize(); initialize()
            self.assertEqual(self.links('legacy_staff'), [('UNASSIGNED', None), ('UZGERMED', 'employee'), ('ZUMA', None)])
            self.assertEqual([c['code'] for c in cl.get('/api/companies').json()['companies']], ['UZGERMED'])
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['ZUMA'])}).status_code, 403)
            # Действующие роли в двух компаниях — конфликт: доступ приостановлен до решения администратора.
            with unit(True) as s:
                s.get(CompanyUser, (ids['ZUMA'], uid)).role = 'employee'
            self.assertEqual(cl.get('/api/companies').json()['companies'], [])
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['UZGERMED'])}).status_code, 403)
            row = next(u for u in self.client.get('/api/admin/users', headers=self.h).json() if u['username'] == 'legacy_staff')
            self.assertEqual(row['state'], 'conflict')
            # Явное решение администратора оставляет одну компанию.
            r = self.client.put(f'/api/admin/users/{uid}/assignment', headers=self.h,
                                json={'company_id': ids['ZUMA'], 'role': 'employee', 'reason': 'Сотрудник работает в Zuma'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.links('legacy_staff'), [('ZUMA', 'employee')])
            initialize()
            self.assertEqual(self.links('legacy_staff'), [('ZUMA', 'employee')])
        finally:
            cl.close()

    # ---- 3. Одно правило для всех путей изменения роли
    def test_every_assignment_path_moves_the_employee_to_one_company(self):
        ids = self.company_ids()
        self.staff('UZGERMED', 'employee', 'mover')[0].close()
        uid = self.user_id('mover')
        zh = {**self.h, 'X-Company-ID': str(ids['ZUMA'])}
        # Прежняя форма «Управлять» в другой компании.
        r = self.client.post(f'/api/users/{uid}', json={'role': 'finance', 'active': True}, headers=zh)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.links('mover'), [('ZUMA', 'finance')])
        # «Добавить по логину».
        self.assertEqual(self.client.post('/api/company-users', json={'username': 'mover', 'role': 'cashier'}, headers=self.h).status_code, 200)
        self.assertEqual(self.links('mover'), [('UZGERMED', 'cashier')])
        # Центр администрирования.
        r = self.client.put(f'/api/admin/users/{uid}/assignment', headers=self.h,
                            json={'company_id': ids['ZUMA'], 'role': 'procurement', 'reason': 'Перевод в отдел закупок Zuma'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.links('mover'), [('ZUMA', 'procurement')])

    def test_a_role_change_ends_the_old_sessions(self):
        cl, h = self.staff('UZGERMED', 'employee', 'session_user')
        try:
            uid = self.user_id('session_user')
            self.assertEqual(cl.get('/api/bootstrap', headers=h).status_code, 200)
            self.client.put(f'/api/admin/users/{uid}/assignment', headers=self.h,
                            json={'company_id': self.company, 'role': 'procurement', 'reason': 'Перевод в отдел закупок'})
            self.assertEqual(cl.get('/api/bootstrap', headers=h).status_code, 401)
        finally:
            cl.close()

    # ---- 4. Реестр и действия с пользователем другой компании и архивным
    def test_registry_actions_work_for_other_companies_and_archived_users(self):
        ids = self.company_ids()
        self.staff('ZUMA', 'cashier', 'zuma_cash')[0].close()
        uid = self.user_id('zuma_cash')
        # Выбрана UZGERMED, пользователь из Zuma: ложного «не найден» нет.
        r = self.client.post(f'/api/users/{uid}/password', json={'password': 'AnotherTemporary_2026!'}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        row = next(u for u in self.client.get('/api/admin/users', headers=self.h).json() if u['username'] == 'zuma_cash')
        self.assertEqual((row['company_role'], row['state']), ('cashier', 'ok'))
        # Сохранение имени роль не меняет.
        r = self.client.put(f'/api/admin/users/{uid}', json={'name': 'Кассир Zuma', 'reason': 'Уточнено ФИО сотрудника'}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.links('zuma_cash'), [('ZUMA', 'cashier')])
        # Архив и восстановление только с явным назначением и новым временным паролем.
        self.assertEqual(self.client.post(f'/api/admin/users/{uid}/archive', json={'reason': 'Сотрудник уволен приказом'}, headers=self.h).status_code, 200)
        self.assertEqual(self.links('zuma_cash'), [])
        self.assertEqual(self.client.post(f'/api/admin/users/{uid}/password', json={'reason': 'Попытка сброса архивного'}, headers=self.h).status_code, 409)
        self.assertEqual(self.client.post(f'/api/admin/users/{uid}/restore', json={'reason': 'Вернулся на работу'}, headers=self.h).status_code, 422)
        r = self.client.post(f'/api/admin/users/{uid}/restore', headers=self.h,
                             json={'company_id': ids['UZGERMED'], 'role': 'employee', 'reason': 'Вернулся на работу в UZGERMED'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(len(r.json()['temporary_password']), 16)
        self.assertEqual(self.links('zuma_cash'), [('UZGERMED', 'employee')])

    def test_holding_status_never_comes_back_by_itself(self):
        self.make_user('admin', 'admin_two')[0].close()
        uid = self.user_id('admin_two')
        self.assertEqual(self.client.post(f'/api/admin/users/{uid}/archive', json={'reason': 'Администратор уволен'}, headers=self.h).status_code, 200)
        r = self.client.post(f'/api/admin/users/{uid}/restore', headers=self.h,
                             json={'company_id': self.company, 'role': 'employee', 'reason': 'Возвращён рядовым сотрудником'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['user']['holding_role'], None)
        with unit() as s:
            self.assertEqual(s.get(User, uid).role, 'employee')

    def test_only_the_holding_admin_reaches_the_admin_api(self):
        founder = self.make_user('founder', 'owner_view')
        director = self.staff('UZGERMED', 'director', 'boss_view')
        try:
            uid = self.user_id('boss_view')
            for cl, h in (founder, director):
                self.assertEqual(cl.get('/api/admin/users', headers=h).status_code, 403)
                self.assertEqual(cl.post(f'/api/admin/users/{uid}/password', json={'reason': 'Попытка сброса пароля'}, headers=h).status_code, 403)
                self.assertEqual(cl.post('/api/admin/users', headers=h, json={'username': 'x.self', 'name': 'Самоназначение',
                    'holding_role': 'admin', 'reason': 'Попытка создать администратора'}).status_code, 403)
            # Себе финансовые права администратор не выдаёт; последнего администратора не архивируют.
            me = self.client.get('/api/me').json()['user']['id']
            self.assertEqual(self.client.put(f'/api/admin/users/{me}/assignment', headers=self.h,
                             json={'company_id': self.company, 'role': 'director', 'reason': 'Сам себе директор'}).status_code, 409)
            self.assertEqual(self.client.post(f'/api/admin/users/{me}/archive', json={'reason': 'Архивировать себя'}, headers=self.h).status_code, 409)
        finally:
            founder[0].close(); director[0].close()

    def test_access_changes_are_journaled_with_actor_reason_before_and_after(self):
        self.staff('UZGERMED', 'employee', 'journal_user')[0].close()
        uid = self.user_id('journal_user')
        self.client.put(f'/api/admin/users/{uid}/assignment', headers=self.h,
                        json={'company_id': self.company, 'role': 'procurement', 'reason': 'Перевод в отдел закупок'})
        card = self.client.get(f'/api/admin/users/{uid}', headers=self.h).json()
        row = next(h for h in card['history'] if h['action'] == 'Назначение в компании')
        detail = json.loads(row['detail'])
        self.assertEqual(row['actor'], 'Test Admin')
        self.assertEqual(detail['reason'], 'Перевод в отдел закупок')
        self.assertEqual(detail['before']['assignments'], [['UZGERMED', 'employee']])
        self.assertEqual(detail['after']['assignments'], [['UZGERMED', 'procurement']])

    # ---- Временный пароль и архив
    def test_temporary_password_must_be_changed_and_old_sessions_end(self):
        r = self.client.post('/api/admin/users', headers=self.h, json={'username': 'uzgermed.new', 'name': 'Новый сотрудник',
                             'company_id': self.company, 'role': 'employee', 'reason': 'Приём на работу по приказу'})
        self.assertEqual(r.status_code, 200, r.text)
        temp = r.json()['temporary_password']
        self.assertGreaterEqual(len(temp), 16)
        cl, login = self.login('uzgermed.new', temp)
        try:
            self.assertEqual(login.status_code, 200)
            self.assertTrue(login.json()['user']['must_change_password'])
            h = {'X-CSRF-Token': login.json()['csrf']}
            self.assertEqual(cl.get('/api/companies').status_code, 403)
            self.assertEqual(cl.post('/api/password', json={'old_password': temp, 'new_password': temp}, headers=h).status_code, 422)
            self.assertEqual(cl.post('/api/password', json={'old_password': temp, 'new_password': 'MyOwnPassword_2026!'}, headers=h).status_code, 200)
        finally:
            cl.close()
        cl, login = self.login('uzgermed.new', 'MyOwnPassword_2026!')
        try:
            self.assertFalse(login.json()['user']['must_change_password'])
            self.assertEqual(cl.get('/api/companies').status_code, 200)
            uid = self.user_id('uzgermed.new')
            reset = self.client.post(f'/api/admin/users/{uid}/password', json={'reason': 'Сотрудник забыл пароль'}, headers=self.h)
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(cl.get('/api/companies').status_code, 401, 'the old session ends after a reset')
            self.assertEqual(self.login('uzgermed.new', 'MyOwnPassword_2026!')[1].status_code, 401, 'the old password stops working')
        finally:
            cl.close()

    def test_archived_user_cannot_sign_in_and_loses_telegram_but_keeps_authorship(self):
        cl, h = self.staff('UZGERMED', 'employee', 'leaver')
        try:
            rid, _ = self.draft(cl, h)
            uid = self.user_id('leaver')
            with unit(True) as s:
                s.add(TelegramLink(user_id=uid, telegram_user_id=777000111))
            self.assertEqual(self.client.post(f'/api/admin/users/{uid}/archive', json={'reason': 'Сотрудник уволен приказом'}, headers=self.h).status_code, 200)
            self.assertEqual(cl.get('/api/bootstrap', headers=h).status_code, 401)
            self.assertEqual(self.login('leaver')[1].status_code, 401)
            with unit() as s:
                self.assertIsNone(s.scalar(select(TelegramLink).where(TelegramLink.user_id == uid)))
                self.assertEqual(s.get(PaymentRequest, rid).creator_id, uid)
        finally:
            cl.close()

    # ---- Перенос учётных записей
    def test_transition_plan_archives_old_accounts_and_creates_personal_logins(self):
        from deploy import access_transition as tr
        import app.db as db
        self.staff('UZGERMED', 'employee', 'old_shared')[0].close()
        out = Path(tempfile.mkdtemp())
        backup = out / 'backup.dump'
        backup.write_bytes(b'verified backup placeholder')
        # На PostgreSQL применение требует отчёт проверки восстановления этой копии.
        (out / 'backup-verified-test.json').write_text(json.dumps({'backup': backup.name, 'mismatch': {}}), encoding='utf-8')
        plan = {'reason': 'Переход на персональные учётные записи', 'keep': ['admin'], 'archive_others': True,
                'accounts': [{'section': 'ZUMA', 'name': 'Азиз Каримов', 'role': 'procurement', 'login': 'zuma.aziz'},
                             {'section': 'HOLDING', 'name': 'Учредитель', 'role': 'founder', 'login': 'holding.owner'},
                             {'section': 'UZGERMED', 'name': None, 'role': 'cashier', 'login': None}]}
        errors, actions = tr.check_plan(db, plan)
        self.assertEqual(errors, [])
        self.assertEqual(actions['archive'], ['old_shared'])
        self.assertTrue(tr.check_plan(db, {**plan, 'keep': []})[0][-1].startswith('После перехода не останется'))
        tr.apply(db, plan, out, backup)
        self.assertEqual(self.links('zuma.aziz'), [('ZUMA', 'procurement')])
        self.assertEqual(self.links('old_shared'), [])
        creds = next(out.glob('logins-*.csv')).read_text(encoding='utf-8-sig')
        self.assertIn('ожидает сотрудника', creds)
        self.assertIn('архивирован', creds)
        journal = next(out.glob('transition-journal-*.json')).read_text(encoding='utf-8')
        password = next(line.split(';')[4] for line in creds.splitlines() if 'zuma.aziz' in line)
        self.assertGreaterEqual(len(password), 16)
        self.assertNotIn(password, journal, 'the journal never holds passwords')
        cl, login = self.login('zuma.aziz', password)
        self.assertTrue(login.json()['user']['must_change_password'])
        cl.close()

    # ---- Документы и журнал операций по праву в компании и каналу оплаты
    def cash(self):
        r = self.post('/api/accounts', {'name': 'Касса', 'kind': 'cash', 'currency': 'UZS', 'opening': '500000',
                                        'opening_date': str(base.today() - base.timedelta(days=10))})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def test_request_documents_follow_the_company_role_and_the_payment_channel(self):
        author = self.staff('UZGERMED', 'employee', 'doc_author')
        rid, doc = self.draft(*author)
        # Прежнее поле роли «финансовый директор» при роли «закупки» в компании прав не даёт.
        buyer = self.staff('UZGERMED', 'procurement', 'stale_role')
        with unit(True) as s:
            s.scalar(select(User).where(User.username == 'stale_role')).role = 'finance'
        try:
            self.assertEqual(buyer[0].get(f'/api/requests/{rid}/documents', headers=buyer[1]).status_code, 403)
            self.assertIn(buyer[0].get(f'/api/request-documents/{doc}', headers=buyer[1]).status_code, (403, 404))
            up = buyer[0].post(f'/api/requests/{rid}/documents', params={'kind': 'contract'}, content=PDF,
                               headers={**buyer[1], 'Content-Type': 'application/pdf', 'X-Filename': 'forged.pdf'})
            self.assertIn(up.status_code, (403, 404))
            # Плательщик канала читает документы утверждённой заявки, кассир банковскую — нет.
            up = author[0].post(f'/api/requests/{rid}/documents', params={'kind': 'contract'}, content=PDF,
                                headers={**author[1], 'Content-Type': 'application/pdf', 'X-Filename': 'contract.pdf'})
            self.assertEqual(up.status_code, 200, up.text)
            submit = author[0].post(f'/api/requests/{rid}/decision', json={'action': 'submit', 'version': self.version(rid)}, headers=author[1])
            self.assertEqual(submit.status_code, 200, submit.text)
            self.assertEqual(self.approve(rid).status_code, 200)
            payer = self.make_user('accountant', 'bank_payer')
            cashier = self.make_user('cashier', 'cash_payer')
            self.assertEqual(payer[0].get(f'/api/requests/{rid}/documents', headers=payer[1]).status_code, 200)
            self.assertEqual(payer[0].get(f'/api/request-documents/{doc}', headers=payer[1]).status_code, 200)
            self.assertEqual(cashier[0].get(f'/api/requests/{rid}/documents', headers=cashier[1]).status_code, 403)
            # Прежние версии остаются доступны тем, кто вправе читать документы.
            history = self.client.get(f'/api/requests/{rid}/documents?history=true', headers=self.h).json()
            self.assertEqual(sorted((d['kind'], d['version'], d['active']) for d in history),
                             [('contract', 1, True), ('internal', 1, True)])
            payer[0].close(); cashier[0].close()
        finally:
            author[0].close(); buyer[0].close()

    def version(self, rid):
        return next(x['version'] for x in self.client.get('/api/requests').json() if x['id'] == rid)

    def test_payers_read_only_the_operations_of_their_channel(self):
        cash = self.cash()
        bank_entry = self.ledger('100', 'in', 'BANK-IN').json()['id']
        cash_entry = self.ledger('50', 'in', 'CASH-IN', account_id=cash).json()['id']
        doc = self.client.post(f'/api/ledger/{bank_entry}/document', content=PDF,
                               headers={**self.h, 'Content-Type': 'application/pdf', 'X-Filename': 'bank.pdf'}).json()['id']
        accountant = self.make_user('accountant', 'bank_only')
        cashier = self.make_user('cashier', 'cash_only')
        try:
            self.assertEqual([x['id'] for x in accountant[0].get('/api/ledger', headers=accountant[1]).json()], [bank_entry])
            self.assertEqual([x['id'] for x in cashier[0].get('/api/ledger', headers=cashier[1]).json()], [cash_entry])
            self.assertEqual(cashier[0].get(f'/api/documents?ledger_id={bank_entry}', headers=cashier[1]).status_code, 404)
            self.assertEqual(cashier[0].get(f'/api/documents/{doc}', headers=cashier[1]).status_code, 404)
            self.assertEqual(accountant[0].get(f'/api/documents/{doc}', headers=accountant[1]).status_code, 200)
        finally:
            accountant[0].close(); cashier[0].close()

    def test_material_accountant_reads_operations_only(self):
        self.ledger('100', 'in', 'MAT-IN')
        cl, h = self.staff('UZGERMED', 'material_accountant', 'material')
        try:
            self.assertEqual(cl.get('/api/ledger', headers=h).status_code, 200)
            for path in ('/api/accounts', '/api/dashboard', '/api/requests', '/api/budgets', '/api/export/ledger.csv', '/api/audit'):
                self.assertEqual(cl.get(path, headers=h).status_code, 403, path)
            self.assertEqual(cl.post('/api/ledger', headers=h, json={'account_id': self.acc, 'category_id': self.cat, 'kind': 'in', 'amount': '1',
                             'date': self.date, 'reference': 'MAT-W', 'note': 'Попытка записи'}).status_code, 403)
        finally:
            cl.close()

    # ---- Вход и сеансы
    def test_render_login_limit_uses_the_real_client_address(self):
        os.environ['UZGERMED_HOSTING'] = 'render'
        try:
            attacker = TestClient(app)
            for i in range(8):
                r = attacker.post('/api/login', json={'username': 'admin', 'password': 'wrong-password'}, headers={'X-Forwarded-For': '203.0.113.9'})
                self.assertEqual(r.status_code, 401)
            self.assertEqual(attacker.post('/api/login', json={'username': 'admin', 'password': PASSWORD}, headers={'X-Forwarded-For': '203.0.113.9'}).status_code, 429)
            # Подделанный левый адрес не помогает: учитывается последний, добавленный прокси Render.
            self.assertEqual(attacker.post('/api/login', json={'username': 'admin', 'password': PASSWORD}, headers={'X-Forwarded-For': '1.1.1.1, 203.0.113.9'}).status_code, 429)
            # Сотрудник с другого адреса входит: чужие ошибки его не блокируют.
            self.assertEqual(TestClient(app).post('/api/login', json={'username': 'admin', 'password': PASSWORD}, headers={'X-Forwarded-For': '198.51.100.7'}).status_code, 200)
            attacker.close()
        finally:
            os.environ.pop('UZGERMED_HOSTING', None)

    def test_anonymous_calls_do_not_fill_the_journal_and_bad_csrf_is_refused(self):
        with unit() as s:
            before = s.scalar(select(func.count()).select_from(Audit))
        anonymous = TestClient(app)
        for path in ('/api/me', '/api/unknown', '/api/accounts'):
            anonymous.get(path)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Audit)), before)
        self.assertEqual(self.client.post('/api/logout', headers={'X-CSRF-Token': 'ключ'.encode('utf-8')}).status_code, 403)
        anonymous.close()

    def test_password_change_guessing_is_limited_and_weak_passwords_are_refused(self):
        cl, h = self.staff('UZGERMED', 'employee', 'guesser')
        try:
            for i in range(8):
                self.assertEqual(cl.post('/api/password', json={'old_password': f'wrong-{i}', 'new_password': 'NewPassword_2026!'}, headers=h).status_code, 403)
            self.assertEqual(cl.post('/api/password', json={'old_password': PASSWORD, 'new_password': 'NewPassword_2026!'}, headers=h).status_code, 429)
        finally:
            cl.close()
        r = self.client.post('/api/users', headers=self.h, json={'username': 'weak.one', 'name': 'Слабый пароль', 'password': '123456789012345', 'role': 'employee'})
        self.assertEqual(r.status_code, 422)

    def test_admin_resets_other_passwords_but_changes_his_own_in_the_profile(self):
        me = self.client.get('/api/me').json()['user']['id']
        self.assertEqual(self.client.post(f'/api/admin/users/{me}/password', json={'reason': 'Сброс собственного пароля'}, headers=self.h).status_code, 409)

    def test_rights_change_unlinks_telegram_and_the_journal_keeps_names(self):
        self.staff('UZGERMED', 'employee', 'tg_user')[0].close()
        uid = self.user_id('tg_user')
        with unit(True) as s:
            s.add(TelegramLink(user_id=uid, telegram_user_id=777000222))
        self.client.put(f'/api/admin/users/{uid}/assignment', headers=self.h,
                        json={'company_id': self.company_ids()['ZUMA'], 'role': 'employee', 'reason': 'Перевод в Zuma'})
        with unit() as s:
            self.assertIsNone(s.scalar(select(TelegramLink).where(TelegramLink.user_id == uid)))
        # Переведённый сотрудник остаётся в журнале UZGERMED под своим именем.
        self.staff('UZGERMED', 'employee', 'mover2')
        cl, h = self.relogin('mover2')
        self.draft(cl, h)
        cl.close()
        self.client.put(f'/api/admin/users/{self.user_id("mover2")}/assignment', headers=self.h,
                        json={'company_id': self.company_ids()['ZUMA'], 'role': 'employee', 'reason': 'Перевод в Zuma'})
        names = {row['user'] for row in self.client.get('/api/audit', headers=self.h).json()}
        self.assertIn('mover2', names)
        self.assertNotIn('Система', {row['user'] for row in self.client.get('/api/audit', headers=self.h).json() if row['action'] == 'Создана заявка'})
        journal = self.client.get('/api/admin/audit', headers=self.h).json()
        self.assertTrue(any(row['action'] == 'Вход в систему' and row['company'] == 'Холдинг' for row in journal['items']))

    def test_bot_reminds_only_by_an_explicit_role_in_one_company(self):
        from app.bot_api import company_role_of
        from types import SimpleNamespace
        zuma, uzg = SimpleNamespace(id=1, code='ZUMA'), SimpleNamespace(id=2, code='UZGERMED')
        staff = SimpleNamespace(id=5, role='finance')
        self.assertIsNone(company_role_of(staff, zuma, {1: {5: None}}), 'a link without a role is not a role')
        self.assertEqual(company_role_of(staff, zuma, {1: {5: 'finance'}, '_valid': {1: {5}}}), 'finance')
        conflict = {1: {5: 'finance'}, 2: {5: 'finance'}, '_valid': {1: {5}, 2: {5}}}
        self.assertIsNone(company_role_of(staff, uzg, conflict), 'a conflict suspends reminders too')

    # ---- ВрИО и изменения доступа (2.14)
    def substitute(self, acting, replaced, role='finance', headers=None):
        return self.client.post('/api/delegations', headers=headers or self.h, json={
            'user_id': self.user_id(acting), 'replaced_user_id': self.user_id(replaced), 'role': role,
            'starts_on': self.date, 'ends_on': str(base.today() + base.timedelta(days=5)), 'reason': 'Отпуск сотрудника по графику'})

    def acting_roles(self, name, company=None):
        cl, h = self.relogin(name, company)
        try:
            r = cl.get('/api/bootstrap', headers=h)
            return [a['role'] for a in r.json()['user']['acting']] if r.status_code == 200 else r.status_code
        finally:
            cl.close()

    def ended(self, delegation_id, reason):
        """Замещение отменено: время отмены, исполнитель и запись в журнале компании замещения."""
        with unit() as s:
            d = s.get(Delegation, delegation_id)
            rows = list(s.scalars(select(Audit).where(Audit.action == 'Отменено ВрИО', Audit.entity == 'delegation',
                                                      Audit.entity_id == str(delegation_id))))
            self.assertIsNotNone(d.revoked_at, delegation_id)
            self.assertEqual(d.revoked_by, self.client.get('/api/me').json()['user']['id'])
            self.assertEqual([(a.company_id, reason in a.detail) for a in rows], [(self.company, True)])

    def test_substitutes_come_only_from_the_company_and_lose_the_role_with_their_access(self):
        self.staff('UZGERMED', 'finance', 'vrio_fin')[0].close()
        self.staff('UZGERMED', 'director', 'vrio_boss')[0].close()
        self.staff('ZUMA', 'employee', 'vrio_zuma')[0].close()
        self.make_user('founder', 'vrio_owner')[0].close()
        for name in ('vrio_a', 'vrio_b', 'vrio_c', 'vrio_d'):
            self.staff('UZGERMED', 'employee', name)[0].close()
        # Замещающий назначается только из сотрудников этой компании; учредитель не замещает.
        refused = self.substitute('vrio_zuma', 'vrio_fin')
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(self.substitute('vrio_owner', 'vrio_fin').status_code, 409)
        self.assertEqual(self.acting_roles('vrio_zuma', self.company_ids()['ZUMA']), [])
        ids = {}
        for name in ('vrio_a', 'vrio_b', 'vrio_c'):
            r = self.substitute(name, 'vrio_fin');self.assertEqual(r.status_code, 200, r.text);ids[name] = r.json()['id']
        r = self.substitute('vrio_d', 'vrio_boss', 'director');self.assertEqual(r.status_code, 200, r.text);ids['vrio_d'] = r.json()['id']
        for name in ids:
            self.assertEqual(len(self.acting_roles(name)), 1, name)
        zuma = self.company_ids()['ZUMA']
        # Перевод в другую компанию.
        moved = self.client.put(f'/api/admin/users/{self.user_id("vrio_a")}/assignment', headers=self.h,
                                json={'company_id': zuma, 'role': 'employee', 'reason': 'Перевод сотрудника в Zuma'})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.ended(ids['vrio_a'], 'Перевод сотрудника в Zuma')
        self.assertEqual(self.acting_roles('vrio_a', zuma), [])
        self.assertEqual(self.acting_roles('vrio_a', self.company), 403)
        # Архив.
        self.assertEqual(self.client.post(f'/api/admin/users/{self.user_id("vrio_b")}/archive', headers=self.h,
                                          json={'reason': 'Сотрудник уволен приказом'}).status_code, 200)
        self.ended(ids['vrio_b'], 'Сотрудник уволен приказом')
        self.assertEqual(self.login('vrio_b')[1].status_code, 401)
        # Снятие назначения в компании.
        self.assertEqual(self.client.post(f'/api/admin/users/{self.user_id("vrio_c")}/unassign', headers=self.h,
                                          json={'company_id': self.company, 'reason': 'Доступ к компании снят'}).status_code, 200)
        self.ended(ids['vrio_c'], 'Доступ к компании снят')
        cl, h = self.relogin('vrio_c')
        try:
            self.assertEqual(cl.get('/api/companies').json()['companies'], [])
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(self.company)}).status_code, 403)
        finally:
            cl.close()
        # Заменяемый сотрудник потерял роль: замещение этой роли тоже заканчивается, ВрИО остаётся сотрудником.
        self.assertEqual(self.client.put(f'/api/admin/users/{self.user_id("vrio_boss")}/assignment', headers=self.h,
                                         json={'company_id': self.company, 'role': 'finance', 'reason': 'Новая должность сотрудника'}).status_code, 200)
        self.ended(ids['vrio_d'], 'Новая должность сотрудника')
        self.assertEqual(self.acting_roles('vrio_d'), [])
        states = {x['id']: x['state'] for x in self.client.get('/api/delegations', headers=self.h).json()}
        self.assertEqual({states[i] for i in ids.values()}, {'revoked'})

    def test_archiving_a_holding_admin_removes_only_his_telegram_summary_groups(self):
        self.make_user('admin', 'tg_admin')[0].close()
        uid, me = self.user_id('tg_admin'), self.client.get('/api/me').json()['user']['id']
        gone, kept, demoted = -1001111000001, -1001111000002, -1001111000003
        self.make_user('admin', 'tg_admin2')[0].close()
        uid2 = self.user_id('tg_admin2')
        with unit(True) as s:
            s.add(TelegramGroup(chat_id=gone, title='Группа уволенного', companies='UZGERMED,ZUMA', added_by=uid))
            s.add(TelegramGroup(chat_id=kept, title='Своя группа', companies='UZGERMED', added_by=me))
            s.add(TelegramGroup(chat_id=demoted, title='Группа бывшего администратора', companies='ZUMA', added_by=uid2))
        r = self.client.post(f'/api/admin/users/{uid}/archive', headers=self.h, json={'reason': 'Администратор уволен приказом'})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put(f'/api/admin/users/{uid2}/holding', headers=self.h,
                            json={'company_id': self.company, 'role': 'employee', 'reason': 'Больше не администратор холдинга'})
        self.assertEqual(r.status_code, 200, r.text)
        with unit() as s:
            self.assertEqual(sorted(g.chat_id for g in s.scalars(select(TelegramGroup))), [kept])
            rows = list(s.scalars(select(Audit).where(Audit.action == 'Telegram-группа отключена').order_by(Audit.id)))
        ids = self.company_ids()
        self.assertEqual(sorted((a.entity_id, a.company_id, a.user_id) for a in rows),
                         sorted([(str(gone), ids['UZGERMED'], me), (str(gone), ids['ZUMA'], me), (str(demoted), ids['ZUMA'], me)]))
        self.assertTrue(all(a.entity == 'telegram_group' for a in rows))
        self.assertIn('отключён', next(a.detail for a in rows if a.entity_id == str(gone)))
        self.assertIn('больше не администратор холдинга', next(a.detail for a in rows if a.entity_id == str(demoted)))

    def test_attachment_names_are_cleaned(self):
        from app.erp import safe_filename
        self.assertEqual(safe_filename('счёт\r\n"№1".pdf', '.pdf'), 'счёт№1.pdf')
        self.assertTrue(safe_filename('a' * 300 + '.pdf', '.pdf').endswith('.pdf'))
        self.assertEqual(len(safe_filename('a' * 300 + '.pdf', '.pdf')), 220)

    # ---- 2.14.0: заявки, отправленные до выпуска, и повтор загрузки
    def legacy(self, rid, finance_id=None):
        """Состояние заявки, отправленной в 2.12.x: снимка политики и проверки бухгалтера нет."""
        with unit(True) as s:
            r = s.get(PaymentRequest, rid)
            r.route_policy = r.route_threshold = r.route_at = None
            r.checked_by = r.checked_at = None
            r.finance_approved_by = finance_id

    def me_id(self, client, headers):
        return client.get('/api/me', headers=headers).json()['user']['id']

    def test_requests_sent_before_2_14_keep_the_old_route_without_the_accountant_check(self):
        waiting_finance = self.request('300').json()['id']
        waiting_director = self.request('400').json()['id']
        finance = self.stage_user('finance')
        self.legacy(waiting_finance)
        self.legacy(waiting_director, self.me_id(*finance))
        row = self.row(waiting_finance)
        self.assertEqual((row['approval_stage'], row['route']['policy'], row['route']['director_required']), ('finance', None, True))
        self.assertNotIn('check', row['actions'])
        self.assertEqual(self.row(waiting_director)['approval_stage'], 'director')
        # Бухгалтер такую заявку не проверяет: этапа проверки у неё нет.
        refused = self.post(f'/api/requests/{waiting_director}/decision', {'action': 'check'}, *self.stage_user('check'))
        self.assertEqual(refused.status_code, 409, refused.text)
        # Директор утверждает сразу, как в 2.12.x; прежний маршрут по-прежнему требует директора.
        ok = self.post(f'/api/requests/{waiting_director}/decision', {'action': 'approve', 'note': 'Утверждаю по прежнему маршруту'}, *self.stage_user('director'))
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual((ok.json()['status'], ok.json()['checked_by']), ('approved', None))
        first = self.post(f'/api/requests/{waiting_finance}/decision', {'action': 'approve', 'note': 'Бюджет и дата проверены'}, *finance)
        self.assertEqual((first.status_code, first.json()['status'], first.json()['approval_stage']), (200, 'pending', 'director'), first.text)
        # Проверявший бухгалтер отсутствует, поэтому бухгалтер канала может оплатить утверждённую прежнюю заявку.
        paid = self.ledger('400', 'out', 'LEGACY-1', client=self.stage_user('check'), request_id=waiting_director)
        self.assertEqual(paid.status_code, 200, paid.text)

    def test_repeating_the_same_upload_does_not_create_a_second_version(self):
        rid = self.request('500').json()['id']
        self.approve(rid)
        before = self.row(rid)
        self.assertEqual(before['status'], 'approved')
        # The reply to the first upload was lost: the same bytes are sent again (the helper alone would make a new file).
        contract = next(d for d in before['documents_list'] if d['kind'] == 'contract' and d.get('current', True))
        same_bytes = self.client.get(contract['url']).content
        again = self.upload(rid, 'contract', content=same_bytes)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()['duplicate'])
        self.assertFalse(again.json()['approval_reset'])
        after = self.row(rid)
        self.assertEqual((after['status'], after['version']), ('approved', before['version']))
        self.assertEqual([d['version'] for d in after['documents_list'] if d['kind'] == 'contract'], [1])
        other = self.upload(rid, 'other', name='act.pdf', content=b'%PDF-1.4\nanother act')
        self.assertTrue(other.json()['approval_reset'])
        self.assertEqual(self.upload(rid, 'other', name='act.pdf', content=b'%PDF-1.4\nanother act').json()['duplicate'], True)
        self.assertEqual(sum(d['kind'] == 'other' for d in self.row(rid)['documents_list']), 1)

    def test_file_events_logged_before_2_14_stay_in_the_request_history(self):
        rid = self.request('600', submit=False).json()['id']
        editor = self.stage_user('finance')
        editor_id = self.me_id(*editor)
        company = self.company_ids()['UZGERMED']  # не внутри unit(True): HTTP-запрос ждал бы блокировку записи
        with unit(True) as s:
            s.add(Audit(company_id=company, user_id=editor_id, action='Добавлен документ к заявке',
                        entity='request_document', entity_id='999', detail=f'request={rid}; kind=internal; version=1'))
            s.add(Audit(company_id=company, user_id=editor_id, action='Добавлен документ к заявке',
                        entity='request_document', entity_id='998', detail=f'request={rid}0; kind=internal; version=1'))
        history = [h['action'] for h in self.row(rid)['history']]
        self.assertEqual(history.count('Добавлен документ к заявке'), 1, history)
        from app.services import all_participants
        with unit() as s:
            self.assertIn(editor_id, all_participants(s, s.get(PaymentRequest, rid)))

    def test_private_output_folder_is_refused_only_inside_the_repository_or_onedrive(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
        import importlib
        transition = importlib.import_module('access_transition')
        self.assertTrue(transition.synced(Path('C:/Users/x/OneDrive/Documents/private')))
        self.assertTrue(transition.synced(Path('C:/Users/x/OneDrive - Company/private')))
        self.assertFalse(transition.synced(Path('C:/Users/x/AppData/Local/Temp/C--Users-x-OneDrive-Desktop-site/private')))
        self.assertFalse(transition.synced(Path('D:/zuma-private')))

    # ---- Проверка выпуска 2.14.0: редакторы круга и версия заявки
    def test_whoever_changed_the_request_or_its_files_in_this_round_never_approves_it(self):
        rid = self.request('700').json()['id']
        self.assertEqual(self.finance_approve(rid).status_code, 200)
        director = self.stage_user('director')
        # Директор заменяет договор: согласования снимаются, он стал последним редактором.
        replaced = self.upload(rid, 'contract', client=director[0], headers=director[1], name='contract-v2.pdf')
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertTrue(replaced.json()['approval_reset'])
        # Автор добавляет ещё один файл: последним редактором снова становится автор.
        self.assertEqual(self.upload(rid, 'other', name='act.pdf').status_code, 200)
        self.assertEqual(self.finance_approve(rid).status_code, 200)
        card = director[0].get(f'/api/requests/{rid}', headers=director[1]).json()
        self.assertEqual(card['approval_stage'], 'director')
        self.assertNotIn('approve', card['actions'], 'the director who replaced the invoice is not offered the approval')
        denied = self.post(f'/api/requests/{rid}/decision', {'action': 'approve', 'note': 'Утверждаю свой же договор'}, *director)
        self.assertEqual(denied.status_code, 403, denied.text)
        other_director = self.make_user('director', 'chief2')
        ok = self.post(f'/api/requests/{rid}/decision', {'action': 'approve', 'note': 'Утверждаю после проверки'}, *other_director)
        self.assertEqual((ok.status_code, ok.json()['status']), (200, 'approved'), ok.text)
        # После возврата на доработку круг начинается заново: прежняя правка директора больше не мешает.
        back = self.post(f'/api/requests/{rid}/decision', {'action': 'return', 'note': 'Нужен исправленный счёт'}, *self.stage_user('finance'))
        self.assertEqual(back.status_code, 200, back.text)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision', {'action': 'submit'}).status_code, 200)
        self.assertEqual(self.finance_approve(rid).status_code, 200)
        again = self.post(f'/api/requests/{rid}/decision', {'action': 'approve', 'note': 'Новый круг согласования'}, *director)
        self.assertEqual((again.status_code, again.json()['status']), (200, 'approved'), again.text)

    def test_every_document_change_moves_the_request_version(self):
        author = self.make_user('employee', 'ver_author')
        created = self.request('300', client=author[0], headers=author[1], submit=False).json()
        rid, v0 = created['id'], created['version']
        first = self.upload(rid, 'internal', client=author[0], headers=author[1])
        self.assertEqual(first.status_code, 200, first.text)
        self.assertGreater(first.json()['request_version'], v0)
        second = self.upload(rid, 'internal', client=author[0], headers=author[1], name='indent-v2.pdf')
        self.assertGreater(second.json()['request_version'], first.json()['request_version'])
        self.assertEqual(self.upload(rid, 'contract', client=author[0], headers=author[1]).status_code, 200)
        # Финансовый руководитель открыл заявку, автор заменил договор: отправка с прежней версией отклоняется.
        fin = self.stage_user('finance')
        seen = fin[0].get(f'/api/requests/{rid}', headers=fin[1]).json()['version']
        self.assertEqual(self.upload(rid, 'contract', client=author[0], headers=author[1], name='contract-v2.pdf').status_code, 200)
        stale = self.post(f'/api/requests/{rid}/decision', {'action': 'submit', 'version': seen}, *fin)
        self.assertEqual(stale.status_code, 409, stale.text)
        stale_save = fin[0].put(f'/api/requests/{rid}', json={**self.request_body('300'), 'status': 'pending', 'version': seen,
                                                                'reason': 'Отправляю от имени автора'}, headers=fin[1])
        self.assertEqual(stale_save.status_code, 409, stale_save.text)
        fresh = self.post(f'/api/requests/{rid}/decision', {'action': 'submit'}, *fin)
        self.assertEqual((fresh.status_code, fresh.json()['status']), (200, 'pending'), fresh.text)

for _name in dir(base.SiteTests):
    if _name.startswith('test'):
        setattr(AccessTests, _name, None)


def tearDownModule():
    base.tearDownModule()


if __name__ == '__main__':
    unittest.main(verbosity=2)
