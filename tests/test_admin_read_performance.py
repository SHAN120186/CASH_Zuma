"""Registry reads stay bounded while preserving live assignment diagnostics."""
import unittest
from types import SimpleNamespace
from sqlalchemy import event, select
import tests.test_site as base
from app import access
from app.admin_api import registry, user_row
from app.db import Company, CompanyUser, User, engine, unit


class AdminReadPerformanceTests(base.SiteTests):
    def read_registry(self):
        statements=[]
        def record(conn,cursor,statement,parameters,context,many):
            if statement.lstrip().lower().startswith('select'):
                statements.append(statement.lower())
        event.listen(engine,'before_cursor_execute',record)
        try:
            response=self.client.get('/api/admin/users',headers=self.h)
        finally:
            event.remove(engine,'before_cursor_execute',record)
        self.assertEqual(response.status_code,200,response.text)
        return response.json(),statements

    def test_user_count_does_not_increase_registry_queries_or_read_credentials(self):
        created=0
        counts=[]
        for extra in (0,9,29):
            with unit(True) as s:
                for i in range(created,extra):
                    user=User(username=f'synthetic_registry_{i}',name=f'Synthetic registry {i:03}',
                              role='employee',password_hash=base.HASH)
                    s.add(user);s.flush()
                    s.add(CompanyUser(company_id=self.company,user_id=user.id,role='employee'))
            created=extra
            rows,statements=self.read_registry()
            self.assertEqual(len(rows),extra+1)
            counts.append(len(statements))
            self.assertLessEqual(len(statements),7,statements)
            self.assertEqual(sum('from company_users' in sql for sql in statements),1)
            self.assertEqual(sum('from companies' in sql for sql in statements),1)
            self.assertFalse(any('user_credentials' in sql for sql in statements))
            for row in rows:
                self.assertNotIn('password_hash',row)
                self.assertNotIn('ciphertext',row)
        self.assertEqual(len(set(counts)),1,counts)

    def test_batch_diagnostics_keep_invalid_assignments_holding_and_archived_states(self):
        with unit(True) as s:
            companies={c.code:c for c in s.scalars(select(Company))}
            inactive=Company(code='SYNTHETIC_INACTIVE',name='Synthetic inactive',active=False)
            s.add(inactive);s.flush()
            primary=companies['UZGERMED'].id;other=companies['ZUMA'].id;service=companies['UNASSIGNED'].id
            cases=[
                ('single','cashier',True,[(primary,'employee')],'ok',[primary]),
                ('conflict','employee',True,[(primary,'employee'),(other,'finance')],'conflict',[primary,other]),
                ('invalid','employee',True,[(primary,'legacy_invalid')],'no_role',[]),
                ('none','employee',True,[],'no_company',[]),
                ('service','employee',True,[(service,'employee')],'no_role',[]),
                ('inactive','employee',True,[(inactive.id,'employee')],'company_suspended',[]),
                ('mixed','employee',True,[(primary,'employee'),(inactive.id,'employee')],'conflict',[primary]),
                ('founder','founder',True,[],'ok',[]),
                ('holding','admin',True,[(primary,'finance'),(other,'accountant')],'ok',[primary,other]),
                ('archived','employee',False,[(primary,'employee'),(other,'employee')],'archived',[primary,other]),
            ]
            expected={}
            for name,role,active,assignments,state,valid_ids in cases:
                user=User(username='synthetic_'+name,name='Synthetic '+name,role=role,active=active,password_hash=base.HASH)
                s.add(user);s.flush()
                for cid,assigned_role in assignments:
                    s.add(CompanyUser(company_id=cid,user_id=user.id,role=assigned_role))
                expected[user.id]=(state,set(valid_ids),len(assignments))
        rows,_=self.read_registry()
        indexed={row['id']:row for row in rows}
        with unit() as s:
            data=registry(s)
            for uid,(state,valid_ids,size) in expected.items():
                user=s.get(User,uid)
                live=access.access_state(s,user)
                supplied=access.access_state_from_rows(user,data[4].get(uid,[]),data[0])
                self.assertEqual(live,supplied)
                self.assertEqual(user_row(s,user,*data),indexed[uid])
                row=indexed[uid]
                self.assertEqual(row['state'],state,row)
                self.assertEqual(len(row['assignments']),size)
                self.assertEqual({a['company_id'] for a in row['assignments'] if a['valid']},valid_ids)
            # An orphan legacy link remains invalid even if its role is recognized.
            orphan=SimpleNamespace(company_id=999999,role='employee')
            state=access.access_state_from_rows(SimpleNamespace(active=True,role='employee'),[orphan],data[0])
            self.assertEqual(state,{'state':'no_role','valid':[],'stale':[orphan]})

    def test_next_read_reflects_assignment_changes_and_filters_remain_diagnostic(self):
        with unit(True) as s:
            other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            user=User(username='synthetic_fresh',name='Synthetic fresh',role='employee',password_hash=base.HASH)
            s.add(user);s.flush();uid=user.id
            s.add(CompanyUser(company_id=self.company,user_id=uid,role='employee'))
        before,_=self.read_registry()
        self.assertEqual(next(row for row in before if row['id']==uid)['state'],'ok')
        with unit(True) as s:
            s.add(CompanyUser(company_id=other,user_id=uid,role='finance'))
        after,_=self.read_registry()
        self.assertEqual(next(row for row in after if row['id']==uid)['state'],'conflict')
        filtered=self.client.get('/api/admin/users',params={'q':'synthetic_fresh','company_id':other,
                                 'role':'finance','state':'conflict'},headers=self.h)
        self.assertEqual(filtered.status_code,200,filtered.text)
        self.assertEqual([row['id'] for row in filtered.json()],[uid])
        for cid in (self.company,other):
            response=self.client.get('/api/admin/users',headers={**self.h,'X-Company-ID':str(cid)})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json(),after)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(AdminReadPerformanceTests(name)
        for name in AdminReadPerformanceTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
