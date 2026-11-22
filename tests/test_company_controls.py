"""Company suspend/resume keeps money, sessions, and company isolation intact."""
import json, unittest
from unittest.mock import patch
from sqlalchemy import select, func
import tests.test_site as base
from app.db import unit, Company, CompanyUser, Account, Audit, ReportSchedule, Setting, TelegramGroup

class CompanyControlTests(base.SiteTests):
    def set_availability(self, cid, active, expected=True, reason='Временная техническая остановка'):
        return self.client.put(f'/api/admin/companies/{cid}/availability',
            json={'active':active,'expected_active':expected,'reason':reason}, headers=self.h)

    def test_disable_resume_scoped_sessions_and_no_cross_company_grant(self):
        ids=self.company_ids(); target=ids['UZGERMED']; other=ids['ZUMA']; service=ids['UNASSIGNED']
        cl,h=self.user_in(target,'finance','scoped_finance')
        foreign,fh=self.user_in(other,'finance','foreign_finance')
        self.addCleanup(cl.close); self.addCleanup(foreign.close)
        before=cl.get('/api/accounts',headers=h); self.assertEqual(before.status_code,200,before.text)
        with unit() as s:
            membership=[(x.company_id,x.user_id,x.role) for x in s.scalars(select(CompanyUser).order_by(CompanyUser.company_id,CompanyUser.user_id))]
            account=(s.get(Account,self.acc).opening,s.get(Account,self.acc).archived)
        self.assertEqual(cl.put(f'/api/admin/companies/{target}/availability',headers=h,json={'active':False,'expected_active':True,'reason':'Проверка прав доступа'}).status_code,403)
        self.assertEqual(self.set_availability(service,False).status_code,404)
        disabled=self.set_availability(target,False);self.assertEqual(disabled.status_code,200,disabled.text)
        uid=cl.get('/api/me').json()['user']['id']
        self.assertEqual(self.client.get(f'/api/admin/users/{uid}').json()['state'],'company_suspended')
        self.assertFalse(disabled.json()['company']['active'])
        self.assertEqual(self.set_availability(target,False).status_code,409)
        self.assertEqual(self.client.get('/api/admin/meta').status_code,200)
        meta=self.client.get('/api/admin/meta').json()
        self.assertNotIn(target,[c['id'] for c in meta['companies']]);self.assertFalse(next(c['active'] for c in meta['managed_companies'] if c['id']==target))
        choices=cl.get('/api/companies').json()['companies'];self.assertEqual([(c['id'],c['active']) for c in choices],[(target,False)])
        self.assertEqual(foreign.get('/api/companies').json()['companies'][0]['id'],other)
        self.assertNotIn(target,[c['id'] for c in foreign.get('/api/companies').json()['companies']])
        self.assertEqual(cl.get('/api/me').status_code,200)
        for path in ('/api/bootstrap','/api/accounts','/api/requests','/api/ledger','/api/budgets','/api/dashboard','/api/report'):
            r=cl.get(path,headers=h);self.assertEqual(r.status_code,403,path);self.assertIn('Компания отключена',r.text)
        self.assertEqual(self.client.get('/api/accounts',headers={**self.h,'X-Company-ID':str(target)}).status_code,403)
        self.assertEqual(cl.post('/api/accounts',headers=h,json={'name':'Fake','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).status_code,403)
        self.assertEqual(foreign.get('/api/bootstrap',headers=fh).status_code,200)
        with unit() as s:
            self.assertEqual(account,(s.get(Account,self.acc).opening,s.get(Account,self.acc).archived))
            self.assertEqual(membership,[(x.company_id,x.user_id,x.role) for x in s.scalars(select(CompanyUser).order_by(CompanyUser.company_id,CompanyUser.user_id))])
            self.assertEqual(s.scalar(select(func.count()).select_from(Audit).where(Audit.action=='Компания отключена',Audit.entity_id==str(target))),1)
        enabled=self.set_availability(target,True,False);self.assertEqual(enabled.status_code,200,enabled.text)
        self.assertEqual(self.client.get(f'/api/admin/users/{uid}').json()['state'],'ok')
        self.assertTrue(enabled.json()['company']['active'])
        self.assertEqual(cl.get('/api/accounts',headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/admin/meta').json()['companies'][0]['name'] is None,False)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Audit).where(Audit.action=='Компания включена',Audit.entity_id==str(target))),1)

    def test_conflict_is_not_resolved_by_suspending_one_company(self):
        ids=self.company_ids();cl,h=self.user_in(ids['UZGERMED'],'finance','conflict_user');self.addCleanup(cl.close)
        uid=cl.get('/api/me').json()['user']['id']
        with unit(True) as s:s.add(CompanyUser(user_id=uid,company_id=ids['ZUMA'],role='finance'))
        self.assertEqual(cl.get('/api/companies').json()['companies'],[])
        self.assertEqual(self.set_availability(ids['ZUMA'],False).status_code,200)
        registry=self.client.get(f'/api/admin/users/{uid}').json()
        self.assertEqual(registry['state'],'conflict');self.assertIsNone(registry['company_id']);self.assertIsNone(registry['company_role'])
        self.assertEqual(cl.get('/api/companies').json()['companies'],[])
        self.assertEqual(cl.get('/api/bootstrap',headers=h).status_code,403)
        from app import access
        with unit() as s:
            from app.db import User
            self.assertEqual(access.staff_company_ids(s,s.get(User,uid)),[])

    def test_bot_legacy_snapshot_retains_only_redacted_identity(self):
        item={'request_id':123, 'number':'CF-123', 'status':'pending', 'stage':'finance',
              'assignee_user_id':44, 'assignee_telegram_id':55, 'stage_entered_at':'2026-11-22T01:00:00Z',
              'company_code':'UZGERMED', 'company':'Private name', 'amount':'12345.00', 'currency':'USD',
              'purpose':'Sensitive detail', 'url':'https://private.invalid', 'stage_label':'Private'}
        target=self.company_ids()['UZGERMED']
        env={'BOT_CLIENT_ID':'test-client','BOT_CLIENT_SECRET':'x'*32}
        with patch.dict('os.environ',env), patch('app.bot_api.pending_items',return_value=[item]):
            self.assertEqual(self.set_availability(target,False).status_code,200)
        with unit() as s:
            saved=json.loads(s.get(Setting,f'inactive_reminder_snapshot:{target}').value)
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['request_id'],item['request_id'])
        self.assertEqual(saved[0]['assignee_telegram_id'],item['assignee_telegram_id'])
        self.assertNotIn('Sensitive detail',json.dumps(saved))
        self.assertNotIn('12345.00',json.dumps(saved))
        with patch.dict('os.environ',env), patch('app.bot_api.pending_items',return_value=[]):
            response=self.client.get('/api/bot/v1/pending-requests',auth=('test-client','x'*32))
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['items'],saved)
        self.assertEqual(self.set_availability(target,True,False).status_code,200)
        with unit() as s:self.assertIsNone(s.get(Setting,f'inactive_reminder_snapshot:{target}'))

    def test_bot_group_scope_excludes_suspended_company_without_erasing_group(self):
        chat=-1003333333344; target=self.company_ids()['UZGERMED']
        with unit(True) as s:s.add(TelegramGroup(chat_id=chat,title='Test',companies='UZGERMED',added_by=1))
        env={'BOT_CLIENT_ID':'test-client','BOT_CLIENT_SECRET':'x'*32}
        with patch.dict('os.environ',env):
            get=lambda:self.client.get('/api/bot/v1/access',params={'chat_id':chat},auth=('test-client','x'*32))
            self.assertIn('UZGERMED',get().json()['group']['companies'])
            self.assertEqual(self.set_availability(target,False).status_code,200)
            self.assertNotIn('UZGERMED',get().json()['group']['companies'])
            with unit() as s:self.assertEqual(s.get(TelegramGroup,chat).companies,'UZGERMED')
            self.assertEqual(self.set_availability(target,True,False).status_code,200)
            self.assertIn('UZGERMED',get().json()['group']['companies'])

    def test_bot_reminders_read_consistent_snapshot_during_resume(self):
        target=self.company_ids()['UZGERMED']; key=f'inactive_reminder_snapshot:{target}'
        snapshot=[{'request_id':123,'status':'pending','stage':'finance','assignee_user_id':44,
                   'assignee_telegram_id':55,'stage_entered_at':'2026-11-22T01:00:00+00:00',
                   'company_code':'UZGERMED','company':'UZGERMED','amount':'0.00','currency':'UZS',
                   'purpose':'','stage_label':'','url':''}]
        with unit(True) as s:
            s.get(Company,target).active=False;s.add(Setting(key=key,value=json.dumps(snapshot)))
        def resume_after_first_read(read_session):
            self.assertFalse(read_session.get(Company,target).active)
            with unit(True) as writer:
                writer.get(Company,target).active=True;writer.delete(writer.get(Setting,key))
            return []
        env={'BOT_CLIENT_ID':'test-client','BOT_CLIENT_SECRET':'x'*32}
        with patch.dict('os.environ',env), patch('app.bot_api.pending_items',side_effect=resume_after_first_read):
            response=self.client.get('/api/bot/v1/pending-requests',auth=('test-client','x'*32))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['items'],snapshot)
        with unit() as s:self.assertTrue(s.get(Company,target).active);self.assertIsNone(s.get(Setting,key))

    def test_scheduled_email_is_held_then_resumes(self):
        import worker
        with unit(True) as s:
            schedule=ReportSchedule(company_id=self.company,recipient='test@example.invalid',currency='UZS',hour=0,enabled=True,created_by=1)
            s.add(schedule);s.flush();sid=schedule.id
        self.assertEqual(self.set_availability(self.company,False).status_code,200)
        with patch.object(worker,'send_report') as send, patch.object(worker,'render_report',return_value=b'fake'):
            worker.report_tick();self.assertEqual(send.call_count,0)
            with unit() as s:
                r=s.get(ReportSchedule,sid);self.assertTrue(r.enabled);self.assertIsNone(r.last_attempt)
            self.assertEqual(self.set_availability(self.company,True,False).status_code,200)
            worker.report_tick();self.assertEqual(send.call_count,1)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(CompanyControlTests(name) for name in (
        'test_disable_resume_scoped_sessions_and_no_cross_company_grant',
        'test_conflict_is_not_resolved_by_suspending_one_company',
        'test_bot_legacy_snapshot_retains_only_redacted_identity',
        'test_bot_group_scope_excludes_suspended_company_without_erasing_group',
        'test_bot_reminders_read_consistent_snapshot_during_resume',
        'test_scheduled_email_is_held_then_resumes'))
def tearDownModule():base.tearDownModule()
