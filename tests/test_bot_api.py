"""Bot API: service access, group scope, morning summary figures, responsible people, Telegram link.

Uses its own temporary database. Client id/secret here are test-only values."""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TMP.name
os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
os.environ['COOKIE_SECURE']='0'
os.environ['PUBLIC_ORIGIN']=''
os.environ.pop('DATABASE_URL',None)
if os.getenv('TEST_DATABASE_URL'):
    from sqlalchemy.engine import make_url
    if not (make_url(os.environ['TEST_DATABASE_URL']).database or '').startswith('zuma_test_'):raise RuntimeError('Test database must start with zuma_test_')
    os.environ['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
GROUP=-1001234567890
GROUP_BOTH=-1002222222222
GROUP_ZUMA=-1003333333333
BOT=('test-bot','test-only-secret-'+'x'*24)
os.environ['BOT_CLIENT_ID']=BOT[0]
os.environ['BOT_CLIENT_SECRET']=BOT[1]
os.environ['BOT_REPORT_GROUPS']=f'{GROUP}:UZGERMED; {GROUP_BOTH}:uzgermed, ZUMA ;{GROUP_ZUMA}:ZUMA; broken-entry'
os.environ['TELEGRAM_BOT_USERNAME']='ZumaTestBot'
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import *
from app.services import today
from app.security import hash_password
PASSWORD='OnlyForTemporaryTests_9841!'
HASH=hash_password(PASSWORD)

def tearDownModule():
    engine.dispose()
    # app.db is shared when the entire suite runs in one Python process. Keep
    # this temporary directory alive until process exit so later test modules
    # do not inherit an engine pointing at a deleted SQLite file.

class BotApiTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine);initialize()
        with unit(True) as s:
            admin=User(username='admin',name='Test Admin',role='admin',password_hash=HASH);s.add(admin);s.flush()
            for c in s.scalars(select(Company).where(Company.code!='UNASSIGNED')):
                s.add(CompanyUser(company_id=c.id,user_id=admin.id,role='finance'))
        self.client=TestClient(app)
        r=self.client.post('/api/login',json={'username':'admin','password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        self.h={'X-CSRF-Token':r.json()['csrf']}
        self.today=today();self.yesterday=self.today-timedelta(days=1)
        b=self.client.get('/api/bootstrap').json()
        self.companies={c['code']:c['id'] for c in b['companies']}
        self.cat=b['categories'][1]['id'];self.cat_name=b['categories'][1]['name']
        self.acc=self.account('Test bank','bank','UZS','1000000.00')
        self.post('/api/approval-policy',{'amount':'1000000'})
        self.extra=[]
    def tearDown(self):
        for c in self.extra:c.close()
        self.client.close()

    # -- helpers
    def post(self,path,data,client=None,headers=None):
        if path=='/api/requests' and data.get('status','pending')=='pending':
            return self.documented_request(data,client,headers)
        return (client or self.client).post(path,json=data,headers=headers or self.h)
    def documented_request(self,data,client=None,headers=None):
        cl=client or self.client;h=headers or self.h;payload=dict(data);payload['status']='draft'
        created=cl.post('/api/requests',json=payload,headers=h)
        if created.status_code!=200:return created
        rid=created.json()['id'];raw=b'%PDF-1.4\nsynthetic request document'
        for kind,name in [('internal','request.pdf'),('contract','contract.pdf')]:
            uploaded=cl.post(f'/api/requests/{rid}/documents',params={'kind':kind},content=raw,
                             headers={**h,'Content-Type':'application/pdf','X-Filename':name})
            if uploaded.status_code!=200:return uploaded
        return cl.post(f'/api/requests/{rid}/decision',json={'action':'submit','version':created.json()['version']},headers=h)
    def zuma(self,h=None):return {**(h or self.h),'X-Company-ID':str(self.companies['ZUMA'])}
    def account(self,name,kind,currency,opening,opened=None,headers=None):
        r=self.post('/api/accounts',{'name':name,'kind':kind,'currency':currency,'opening':opening,
            'opening_date':str(opened or self.today-timedelta(days=10))},headers=headers)
        self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def category(self,headers):
        return next(c['id'] for c in self.client.get('/api/bootstrap',headers=headers).json()['categories'] if c['type']=='outcome')
    def ledger(self,amount,kind,reference,day=None,account=None,**extra):
        data={'account_id':account or self.acc,'category_id':self.cat,'amount':amount,'kind':kind,'date':str(day or self.yesterday),
              'reference':reference,'note':'Подтверждённая тестовая операция'};data.update(extra)
        r=self.post('/api/ledger',data);self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def make_user(self,role,name,headers=None):
        r=self.post('/api/users',{'username':name,'name':name.title(),'password':PASSWORD,'role':role},headers=headers)
        self.assertEqual(r.status_code,200,r.text)
        c=TestClient(app);self.extra.append(c)
        r=c.post('/api/login',json={'username':name,'password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        return {'id':r.json()['user']['id'],'client':c,'h':{'X-CSRF-Token':r.json()['csrf']}}
    def new_request(self,user,amount='600',day=None,account=None,category=None,headers=None,purpose='Оплата по договору 20208000900123456789'):
        r=self.post('/api/requests',{'account_id':account or self.acc,'category_id':category or self.cat,'counterparty':'Supplier LLC',
            'amount':amount,'date':str(day or self.today),'purpose':purpose},user['client'],headers or user['h'])
        self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def version(self,rid,headers=None):
        return next(r['version'] for r in self.client.get('/api/requests',headers=headers or self.h).json() if r['id']==rid)
    def decide(self,user,rid,action='approve',note='Проверено для тестирования'):
        r=self.post(f'/api/requests/{rid}/decision',{'action':action,'note':note,'version':self.version(rid)},user['client'],user['h'])
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def fully_approve(self,finance,rid,director=None):
        self.decide(finance,rid)
        if director is None:
            if not hasattr(self,'payment_director'):self.payment_director=self.make_user('director','payment_director')
            director=self.payment_director
        return self.decide(director,rid)
    def summary(self,chat=GROUP,day=None,auth=BOT):
        day=day or self.today
        return self.client.get('/api/bot/v1/morning-summary',params={'chat_id':chat,'report_date':str(day-timedelta(days=1)),'today':str(day)},auth=auth)
    def pending(self):
        r=self.client.get('/api/bot/v1/pending-requests',auth=BOT);self.assertEqual(r.status_code,200,r.text);return r.json()['items']
    def link(self,user,telegram_id):
        code=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        r=self.client.post('/api/bot/v1/telegram-links',json={'code':code,'telegram_user_id':telegram_id},auth=BOT)
        self.assertEqual(r.status_code,200,r.text);return r.json()

    # -- access
    def test_service_credentials_are_required_and_checked(self):
        for auth in (None,('test-bot','wrong-secret-'+'y'*30),('other-bot',BOT[1])):
            r=self.client.get('/api/bot/v1/pending-requests',auth=auth)
            self.assertEqual(r.status_code,401,r.text)
        r=self.client.get('/api/bot/v1/pending-requests',headers={'Authorization':'Basic ***not-base64***'})
        self.assertEqual(r.status_code,401)
        # A logged-in administrator's browser session is not a bot key.
        self.assertEqual(self.client.get('/api/bot/v1/pending-requests').status_code,401)
        self.assertEqual(self.summary(auth=None).status_code,401)
        with unit() as s:
            self.assertTrue(s.scalar(select(Audit.id).where(Audit.action=='Бот: отказ в доступе')))

    def test_bot_api_is_off_until_configured_with_a_long_secret(self):
        saved=os.environ['BOT_CLIENT_SECRET']
        try:
            for value in ('', 'short-secret'):
                os.environ['BOT_CLIENT_SECRET']=value
                self.assertEqual(self.client.get('/api/bot/v1/pending-requests',auth=(BOT[0],value)).status_code,503)
        finally:os.environ['BOT_CLIENT_SECRET']=saved

    def test_unregistered_group_and_wrong_dates_are_refused(self):
        self.assertEqual(self.summary(chat=-1009999999999).status_code,403)
        self.assertEqual(self.summary(chat=555001).status_code,403)  # a private chat is never a group
        future=self.client.get('/api/bot/v1/morning-summary',params={'chat_id':GROUP,'report_date':str(self.today),'today':str(self.today+timedelta(days=1))},auth=BOT)
        self.assertEqual(future.status_code,422)
        gap=self.client.get('/api/bot/v1/morning-summary',params={'chat_id':GROUP,'report_date':str(self.today-timedelta(days=2)),'today':str(self.today)},auth=BOT)
        self.assertEqual(gap.status_code,422)
        self.assertEqual(self.client.get('/api/bot/v1/morning-summary',params={'chat_id':GROUP},auth=BOT).status_code,422)

    # -- morning summary
    def test_summary_balances_movements_and_currencies(self):
        cash=self.account('Касса офиса','cash','UZS','50000.00')
        usd=self.account('Счёт 20208840900123454821','bank','USD','1000.00')
        self.ledger('100','in','IN-1')
        self.ledger('30','out','OUT-1',account=cash)
        self.ledger('5','in','IN-USD',account=usd)
        self.ledger('20','transfer','TR-1',to_account_id=cash,category_id=None)
        wrong=self.ledger('7','in','IN-WRONG')
        self.assertEqual(self.post(f'/api/ledger/{wrong}/reverse',{'reason':'Ошибочная тестовая запись'}).status_code,200)
        self.ledger('1000','in','IN-TODAY',day=self.today)
        r=self.summary();self.assertEqual(r.status_code,200,r.text);data=r.json()
        self.assertEqual((data['chat_id'],data['report_date'],data['today']),(GROUP,str(self.yesterday),str(self.today)))
        balances={(b['name'],b['currency']):(b['kind'],b['amount']) for b in data['balances']}
        # Start of today: yesterday's movements are in, today's are not.
        self.assertEqual(balances[('Test bank','UZS')],('bank','1000080.00'))
        self.assertEqual(balances[('Касса офиса','UZS')],('cash','49990.00'))
        self.assertEqual(balances[('Счёт ••4821','USD')],('bank','1005.00'))
        self.assertNotIn('20208840900123454821',r.text)
        self.assertEqual(data['income'],[{'company':'UZGERMED','kind':'bank','currency':'UZS','amount':'100.00'},
                                         {'company':'UZGERMED','kind':'bank','currency':'USD','amount':'5.00'}])
        self.assertEqual(data['expense'],[{'company':'UZGERMED','kind':'cash','currency':'UZS','amount':'30.00'}])
        self.assertEqual([b['currency'] for b in data['balances']],['UZS','USD','UZS'])  # bank UZS, bank USD, then cash
        for mixed in ('105.00','1001085.00','1051075.00'):self.assertNotIn(mixed,r.text)

    def test_missing_movements_are_null_and_empty_lists_mean_none(self):
        data=self.summary().json()
        self.assertIsNone(data['income']);self.assertIsNone(data['expense'])
        self.assertEqual(data['balances'][0]['amount'],'1000000.00')
        self.assertEqual((data['payments_today'],data['pending_approvals'],data['warnings']),([],[],[]))
        zuma=self.summary(chat=GROUP_ZUMA).json()  # no accounts at all
        self.assertIsNone(zuma['balances']);self.assertIsNone(zuma['income'])

    def test_account_opened_today_counts_and_archived_or_future_do_not(self):
        self.account('Новый счёт','bank','EUR','500.00',opened=self.today)
        empty=self.account('Пустой счёт','bank','UZS','0',opened=self.today-timedelta(days=5))
        self.assertEqual(self.post(f'/api/accounts/{empty}/archive',{'archived':True,'reason':'Счёт больше не используется'}).status_code,200)
        names={b['name']:b['amount'] for b in self.summary().json()['balances']}
        self.assertEqual(names['Новый счёт'],'500.00')
        self.assertNotIn('Пустой счёт',names)
        # Opening balances are set for the start of opening_date, so today's opening is today's start.
        with unit(True) as s:s.get(Account,self.acc).opening_date=self.today
        self.assertEqual({b['name']:b['amount'] for b in self.summary().json()['balances']},{'Новый счёт':'500.00','Test bank':'1000000.00'})

    def test_group_receives_only_its_companies(self):
        zh=self.zuma()
        self.account('Zuma bank','bank','UZS','777.00',headers=zh)
        own={b['company'] for b in self.summary().json()['balances']}
        both={b['company'] for b in self.summary(chat=GROUP_BOTH).json()['balances']}
        self.assertEqual(own,{'UZGERMED'})
        self.assertEqual(both,{'UZGERMED','Zuma'})
        self.assertNotIn('777.00',self.summary().text)

    def test_payments_and_pending_approvals_without_personal_details(self):
        author=self.make_user('employee','author')
        finance=self.make_user('finance','checker');director=self.make_user('director','boss')
        due=self.new_request(author,'600');self.fully_approve(finance,due,director)
        late=self.new_request(author,'400',day=self.yesterday);self.fully_approve(finance,late,director)
        waiting=self.new_request(author,'300')
        big=self.new_request(author,'1500000',day=self.today+timedelta(days=3))
        self.decide(finance,big)  # above the limit: goes to the director
        r=self.summary();data=r.json()
        payments={p['number']:p for p in data['payments_today']}
        self.assertEqual(set(payments),{f'CF-{due:05d}',f'CF-{late:05d}'})
        self.assertEqual(payments[f'CF-{due:05d}']['stage_label'],'')
        self.assertEqual(payments[f'CF-{late:05d}']['stage_label'],f'Просрочено с {self.yesterday:%d.%m.%Y}')
        self.assertEqual(payments[f'CF-{due:05d}']['purpose'],self.cat_name)
        pending={p['number']:p['stage_label'] for p in data['pending_approvals']}
        self.assertEqual(pending,{f'CF-{waiting:05d}':'Проверка финансистом',f'CF-{big:05d}':'Утверждение директором'})
        for private in ('Supplier LLC','Оплата по договору','20208000900123456789'):self.assertNotIn(private,r.text)

    def test_warnings_for_shortage_reserve_and_budget(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker');director=self.make_user('director','boss')
        rid=self.new_request(author,'600');self.fully_approve(finance,rid,director)
        admin_id=self.client.get('/api/me').json()['user']['id']
        with unit(True) as s:  # money left the account after the approval
            s.add(Ledger(account_id=self.acc,category_id=None,kind='out',amount=99950000,date=self.yesterday,reference='DIRECT-OUT',creator_id=admin_id))
        self.assertEqual(self.post('/api/reserve',{'currency':'UZS','amount':'2000000'}).status_code,200)
        self.assertEqual(self.post('/api/budgets',{'category_id':self.cat,'month':str(self.today)[:7],'currency':'UZS','amount':'1000','mode':'soft','reason':'Лимит для тестирования','source':'test'}).status_code,200)
        spare=self.account('Резервная касса','cash','UZS','10000.00')
        with unit(True) as s:  # the overspend is paid from another account and does not touch the bank
            s.add(Ledger(account_id=spare,category_id=self.cat,kind='out',amount=150000,date=self.today,reference='OVER-BUDGET',creator_id=admin_id))
        # Budget use = 1500 spent + 600 approved and reserved, against a limit of 1000.
        messages=[w['message'] for w in self.summary().json()['warnings']]
        self.assertIn(f'Test bank: не хватает 100.00 UZS на утверждённые платежи по {self.today:%d.%m.%Y}',messages)
        self.assertTrue(any('ниже минимального резерва 2000000.00 UZS' in m for m in messages),messages)
        self.assertTrue(any(f'«{self.cat_name}» за {self.today:%m.%Y} превышен на 1100.00 UZS' in m for m in messages),messages)

    def test_every_summary_is_in_the_company_audit(self):
        self.assertEqual(self.summary().status_code,200)
        actions=[a['action'] for a in self.client.get('/api/audit').json()]
        self.assertIn('Бот: утренняя сводка',actions)

    # -- Telegram link
    def test_link_code_is_single_use_short_lived_and_hashed(self):
        user=self.make_user('finance','linker')
        status=user['client'].get('/api/telegram',headers=user['h']).json()
        self.assertEqual(status,{'linked':False,'linked_at':None,'telegram_id_tail':None,'bot_username':'ZumaTestBot'})
        issued=self.post('/api/telegram/code',{},user['client'],user['h']).json()
        code=issued['code']
        self.assertRegex(code,r'^[A-HJ-NP-Z2-9]{8}$')
        self.assertEqual(issued['deep_link'],f'https://t.me/ZumaTestBot?start={code}')
        self.assertEqual(issued['expires_in'],600)
        with unit() as s:
            self.assertFalse(any(code in (row.code_hash or '') for row in s.scalars(select(TelegramLinkCode))))
            self.assertFalse(s.scalar(select(Audit.id).where(Audit.detail.contains(code))))
        confirm=lambda c,tg=555001:self.client.post('/api/bot/v1/telegram-links',json={'code':c,'telegram_user_id':tg},auth=BOT)
        self.assertEqual(confirm(code,0).status_code,422)
        self.assertEqual(confirm('bad code!').status_code,422)
        ok=confirm(code.lower());self.assertEqual(ok.status_code,200,ok.text)
        self.assertEqual(ok.json(),{'user_id':user['id'],'name':'Linker'})
        self.assertEqual(confirm(code).status_code,404)  # already used
        linked=user['client'].get('/api/telegram',headers=user['h']).json()
        self.assertEqual((linked['linked'],linked['telegram_id_tail']),(True,'5001'))
        first=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        second=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        self.assertEqual(confirm(first,555002).status_code,404)  # replaced by the newer code
        expired=second
        with unit(True) as s:s.scalar(select(TelegramLinkCode)).expires_at=now()-timedelta(seconds=1)
        self.assertEqual(confirm(expired,555002).status_code,404)

    def test_one_telegram_account_per_user_and_unlink(self):
        first=self.make_user('finance','first');second=self.make_user('director','second')
        self.link(first,555001)
        code=self.post('/api/telegram/code',{},second['client'],second['h']).json()['code']
        conflict=self.client.post('/api/bot/v1/telegram-links',json={'code':code,'telegram_user_id':555001},auth=BOT)
        self.assertEqual(conflict.status_code,409)
        self.link(first,555009)  # the same user moves to another Telegram account
        with unit() as s:self.assertEqual(s.get(TelegramLink,first['id']).telegram_user_id,555009)
        self.link(second,555001)  # the freed account can now be linked by someone else
        r=first['client'].delete('/api/telegram',headers=first['h']);self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(first['client'].get('/api/telegram',headers=first['h']).json()['linked'])

    def test_disabled_user_code_is_rejected_and_other_company_user_can_link(self):
        user=self.make_user('finance','leaver')
        code=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        with unit(True) as s:s.get(User,user['id']).active=False
        self.assertEqual(self.client.post('/api/bot/v1/telegram-links',json={'code':code,'telegram_user_id':555003},auth=BOT).status_code,404)
        outsider=self.make_user('finance','zumaonly',headers=self.zuma())
        self.assertEqual(outsider['client'].get('/api/telegram',headers=outsider['h']).status_code,200)
        self.link(outsider,555004)

    # -- pending requests
    def test_reminders_follow_the_stage_and_the_responsible_role(self):
        self.post('/api/approval-policy',{'amount':'100'})  # 600 UZS needs the director
        author=self.make_user('employee','author')
        finance=self.make_user('finance','checker');director=self.make_user('director','boss')
        accountant=self.make_user('accountant','payer');cashier=self.make_user('cashier','teller')
        self.make_user('finance','silent')  # not linked: never listed
        for user,tg in ((finance,1001),(director,1002),(accountant,1003),(cashier,1004)):self.link(user,tg)
        rid=self.new_request(author,'600')
        items=self.pending()
        self.assertEqual([(i['request_id'],i['stage'],i['assignee_user_id'],i['assignee_telegram_id']) for i in items],[(rid,'finance',finance['id'],1001)])
        item=items[0]
        self.assertEqual((item['number'],item['status'],item['stage_label'],item['amount'],item['currency'],item['company'],item['url']),
                         (f'CF-{rid:05d}','pending','Проверка финансистом','600.00','UZS','UZGERMED',''))
        self.assertTrue(item['purpose'].startswith(self.cat_name+': Оплата по договору ••6789'))
        entered=datetime.fromisoformat(item['stage_entered_at']);self.assertIsNotNone(entered.tzinfo)

        self.decide(finance,rid)
        items=self.pending()
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in items],[('director',director['id'])])
        self.assertGreaterEqual(datetime.fromisoformat(items[0]['stage_entered_at']),entered)

        self.decide(director,rid)
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending()],[('bank_payment',accountant['id'])])

        paid=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600','date':str(self.today),
                                      'reference':'PAY-1','note':'Оплата по заявке','request_id':rid,'request_version':self.version(rid)},
                       accountant['client'],accountant['h'])
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(self.pending(),[])

        cash=self.account('Касса','cash','UZS','5000.00')
        # A cashier sees only their own requests on the site, so only those are theirs to pay.
        foreign=self.new_request(author,'50',account=cash);self.fully_approve(finance,foreign,director)
        own=self.new_request(cashier,'40',account=cash);self.fully_approve(finance,own,director)
        self.assertEqual(sorted((i['request_id'],i['stage'],i['assignee_user_id']) for i in self.pending()),
                         [(foreign,'cash_payment',finance['id']),(own,'cash_payment',cashier['id'])])
        with unit(True) as s:s.get(User,cashier['id']).active=False
        self.assertEqual(sorted((i['request_id'],i['assignee_user_id']) for i in self.pending()),[(foreign,finance['id']),(own,finance['id'])])

    def test_closed_returned_and_unlinked_requests_are_not_reminded(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker')
        self.link(finance,1001)
        returned=self.new_request(author,'100');self.decide(finance,returned,'return','Нужны документы от поставщика')
        cancelled=self.new_request(author,'100');self.decide(self.me(),cancelled,'cancel','Больше не требуется оплата')
        rejected=self.new_request(author,'100')
        with unit(True) as s:s.get(PaymentRequest,rejected).status='rejected'  # legacy closed request
        draft=self.post('/api/requests',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier LLC','amount':'100',
            'date':str(self.today),'purpose':'Черновик заявки','status':'draft'},author['client'],author['h'])
        self.assertEqual(draft.status_code,200,draft.text)
        self.assertEqual(self.pending(),[])
        live=self.new_request(author,'100')
        self.assertEqual([i['request_id'] for i in self.pending()],[live])
        self.assertEqual(finance['client'].delete('/api/telegram',headers=finance['h']).status_code,200)
        self.assertEqual(self.pending(),[])

    def test_author_editor_and_other_companies_are_excluded(self):
        finance=self.make_user('finance','checker');other=self.make_user('finance','other')
        zuma_finance=self.make_user('finance','zumafin',headers=self.zuma())
        for user,tg in ((finance,1001),(other,1002),(zuma_finance,1003)):self.link(user,tg)
        own=self.new_request(finance,'100')  # the financier cannot check their own request
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==own],[other['id']])
        admin2=self.make_user('admin','admin2');self.link(admin2,1004)
        zuma_acc=self.account('Zuma bank','bank','UZS','1000.00',headers=self.zuma())
        zuma_author=self.make_user('employee','zumaauthor',headers=self.zuma())
        zid=self.new_request(zuma_author,'100',account=zuma_acc,category=self.category(self.zuma()),headers=self.zuma(zuma_author['h']))
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==zid],[zuma_finance['id']])
        with unit(True) as s:s.get(User,zuma_finance['id']).active=False
        # No financier left in Zuma: administrators (who see every company) are reminded instead.
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==zid],[admin2['id']])

    def test_request_link_uses_the_public_https_address_only(self):
        finance=self.make_user('finance','checker');self.link(finance,1001)
        self.new_request(self.make_user('employee','author'),'100')
        for origin,expected in (('https://cash.example','https://cash.example/#requests'),('http://cash.example','')):
            os.environ['PUBLIC_ORIGIN']=origin
            try:self.assertEqual(self.pending()[0]['url'],expected)
            finally:os.environ['PUBLIC_ORIGIN']=''

    def test_code_seen_in_a_group_is_revoked(self):
        user=self.make_user('finance','linker')
        code=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        revoke=lambda c:self.client.post('/api/bot/v1/telegram-links/revoke',json={'code':c},auth=BOT)
        self.assertEqual(revoke(code.lower()).json(),{'revoked':True})
        self.assertEqual(revoke(code).json(),{'revoked':False})
        self.assertEqual(self.client.post('/api/bot/v1/telegram-links',json={'code':code,'telegram_user_id':555001},auth=BOT).status_code,404)
        self.assertEqual(self.client.post('/api/bot/v1/telegram-links/revoke',json={'code':code}).status_code,401)

    def test_revoking_sessions_also_unlinks_telegram_and_cancels_codes(self):
        user=self.make_user('finance','victim');self.link(user,777001)
        spare=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        self.assertEqual(self.post(f'/api/users/{user["id"]}/password',{'password':PASSWORD+'x'}).status_code,200)
        with unit() as s:
            self.assertIsNone(s.get(TelegramLink,user['id']))
            self.assertIsNone(s.scalar(select(TelegramLinkCode.user_id)))
        self.assertEqual(self.client.post('/api/bot/v1/telegram-links',json={'code':spare,'telegram_user_id':777002},auth=BOT).status_code,404)
        again=self.make_user('director','another');self.link(again,777003)
        self.assertEqual(self.post(f'/api/users/{again["id"]}',{'role':'director','active':False,'password':''}).status_code,200)
        with unit() as s:self.assertIsNone(s.get(TelegramLink,again['id']))
        own=self.make_user('finance','changer');self.link(own,777004)
        self.assertEqual(self.post('/api/password',{'old_password':PASSWORD,'new_password':PASSWORD+'y'},own['client'],own['h']).status_code,200)
        with unit() as s:self.assertIsNone(s.get(TelegramLink,own['id']))

    def test_payment_reminders_start_on_the_due_date(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker');director=self.make_user('director','boss')
        accountant=self.make_user('accountant','payer');self.link(accountant,1003)
        later=self.new_request(author,'100',day=self.today+timedelta(days=5));self.fully_approve(finance,later,director)
        due=self.new_request(author,'100');self.fully_approve(finance,due,director)
        late=self.new_request(author,'100',day=self.yesterday);self.fully_approve(finance,late,director)
        items={i['request_id']:datetime.fromisoformat(i['stage_entered_at']) for i in self.pending()}
        self.assertEqual(set(items),{due,late})  # not reminded before the due date
        start=lambda d:datetime(d.year,d.month,d.day,3,0,tzinfo=items[due].tzinfo)  # 08:00 Tashkent in UTC
        self.assertGreaterEqual(items[due],start(self.today))
        self.assertGreaterEqual(items[late],start(self.yesterday))

    def test_archived_today_with_money_at_day_start_is_still_shown(self):
        box=self.account('Старая касса','cash','UZS','700.00')
        self.ledger('700','transfer','TR-CLOSE',day=self.today,account=box,to_account_id=self.acc,category_id=None)
        self.assertEqual(self.post(f'/api/accounts/{box}/archive',{'archived':True,'reason':'Касса закрыта сегодня'}).status_code,200)
        balances={b['name']:b['amount'] for b in self.summary().json()['balances']}
        self.assertEqual(balances,{'Test bank':'1000000.00','Старая касса':'700.00'})

    def test_no_reversal_on_an_archived_account(self):
        box=self.account('Закрываемый счёт','bank','UZS','0')
        entry=self.ledger('50','in','IN-A',day=self.today,account=box)
        out=self.ledger('50','out','OUT-A',day=self.today,account=box)
        self.assertEqual(self.post(f'/api/accounts/{box}/archive',{'archived':True,'reason':'Счёт закрыт в банке'}).status_code,200)
        self.assertEqual(self.post(f'/api/ledger/{out}/reverse',{'reason':'Попытка вернуть деньги'}).status_code,409)
        self.assertEqual(self.post(f'/api/ledger/{entry}/reverse',{'reason':'Попытка вернуть деньги'}).status_code,409)

    def test_used_account_kind_is_frozen_and_payment_stage_stays_stable(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker');director=self.make_user('director','boss');self.link(finance,1001)
        rid=self.new_request(author,'100');self.fully_approve(finance,rid,director)
        self.link(self.make_user('accountant','payer'),1003)
        before=self.pending()[0]
        r=self.post(f'/api/accounts/{self.acc}',{'name':'Test bank','kind':'cash','currency':'UZS','opening':'1000000.00',
            'opening_date':str(self.today-timedelta(days=10)),'reason':'Счёт оказался кассой'})
        self.assertEqual(r.status_code,409,r.text)
        after=self.pending()[0]
        self.assertEqual((before['stage'],after['stage']),('bank_payment','bank_payment'))
        self.assertEqual(after['stage_entered_at'],before['stage_entered_at'])

    def test_blank_or_padded_secret_does_not_enable_the_api(self):
        saved=os.environ['BOT_CLIENT_SECRET']
        try:
            os.environ['BOT_CLIENT_SECRET']=' '*40
            self.assertEqual(self.client.get('/api/bot/v1/pending-requests',auth=(BOT[0],' '*40)).status_code,503)
            os.environ['BOT_CLIENT_SECRET']='  '+saved+'  '
            self.assertEqual(self.client.get('/api/bot/v1/pending-requests',auth=BOT).status_code,200)
        finally:os.environ['BOT_CLIENT_SECRET']=saved

    def test_numbers_are_masked_but_amounts_and_dates_are_kept(self):
        from app.bot_api import mask_digits
        cases={'150000000.00':'150000000.00','150000000,00':'150000000,00','27.09.2026':'27.09.2026',
               'счета 12345678901, 20208000900123456789':'счета ••8901, ••6789','Kapitalbank 1234':'Kapitalbank 1234',
               '20208000900123456789':'••6789','8600 1234 5678 9012':'••9012','8600.1234.5678.9012':'••9012',
               'р/с 20208000/900123/456789':'р/с ••6789','Счёт 20208000_9001_2345_6789':'Счёт ••6789'}
        for text,expected in cases.items():self.assertEqual(mask_digits(text),expected,text)

    def me(self):
        return {'client':self.client,'h':self.h}


if __name__=='__main__':unittest.main(verbosity=2)
