import unittest
from sqlalchemy import event, select
import tests.test_site as base
from app.db import unit, engine, User, Company, Account, Category, Ledger, PaymentRequest, Budget, RequestDocument, Audit
from datetime import date, datetime
from app.services import request_batch
from app.requests_api import request_view

class RequestPerfTests(base.SiteTests):
 def test_batch_matches_legacy_and_bounds_queries(self):
  with unit(True) as s:
   s.info['company_id']=self.company
   admin=s.scalar(select(User).where(User.username=='admin'))
   usd=Account(name='Synthetic USD',company_id=self.company,kind='cash',currency='USD',opening=0,opening_date=date(2026,1,1),created_by=admin.id);s.add(usd);s.flush()
   for n in range(10):
    c=Category(company_id=self.company,name='Synthetic %d'%n,type='outcome',activity='operating');s.add(c);s.flush()
    r=PaymentRequest(creator_id=admin.id,last_editor_id=admin.id,category_id=c.id,account_id=usd.id if n%2 else self.acc,counterparty='synthetic',amount=10000+n,due_date=date(2026,9,1) if n%2 else date(2026,8,31),purpose='synthetic',status=['approved','draft','pending','paid'][n%4])
    s.add(r);s.flush()
    currency='USD' if n%2 else 'UZS'; month='2026-09' if n%2 else '2026-08'
    s.add(Budget(category_id=c.id,month=month,currency=currency,amount=100000,mode='hard',source='test'))
    s.add(Ledger(account_id=r.account_id,category_id=c.id,kind='out',amount=300,date=r.due_date,reference=f'test{n}',creator_id=admin.id,request_id=r.id if r.status=='paid' else None))
    if r.status=='paid':s.add(Audit(company_id=self.company,user_id=admin.id,entity='request',entity_id=str(r.id),action='Оплата заявки',detail='',created_at=datetime(2026,10,1)))
    if n%2==0:s.add(RequestDocument(request_id=r.id,kind='internal',filename='test.pdf',mime='application/pdf',storage_key=f'test{n}',sha256='test',content=b'x',version=1,active=True,created_by=admin.id))
  counts={}
  for count in (1,10):
   for kind in ('legacy','batch'):
    with unit() as s:
     s.info['company_id']=self.company
     admin=s.scalar(select(User).where(User.username=='admin'))
     rs=list(s.scalars(select(PaymentRequest).order_by(PaymentRequest.id).limit(count)))
     statements=[]
     def counter(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
     event.listen(engine,'before_cursor_execute',counter)
     try:
      if kind=='batch':
       b=request_batch(s,rs); values=[request_view(s,admin,r,batch=b) for r in rs]
      else:values=[request_view(s,admin,r) for r in rs]
     finally:event.remove(engine,'before_cursor_execute',counter)
     counts[kind,count]=len(statements)
     if kind=='legacy':baseline=values
     else:self.assertEqual(values,baseline)
  print('SQL_COUNTS',counts)
  self.assertLess(counts['batch',10],counts['legacy',10])
  for rights in [{'request'},{'view'},{'view','approve','request_edit'},{'pay_bank'}]:
   with unit() as s:
    s.info['company_id']=self.company;admin=s.scalar(select(User).where(User.username=='admin'));admin._permissions=rights
    rs=list(s.scalars(select(PaymentRequest).order_by(PaymentRequest.id).limit(10)))
    before=[request_view(s,admin,r) for r in rs];b=request_batch(s,rs)
    self.assertEqual(before,[request_view(s,admin,r,batch=b) for r in rs])

def load_tests(loader,tests,pattern):return unittest.TestSuite([RequestPerfTests('test_batch_matches_legacy_and_bounds_queries')])
def tearDownModule():base.tearDownModule()
