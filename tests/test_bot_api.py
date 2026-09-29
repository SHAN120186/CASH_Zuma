"""Bot API: service access, group scope, morning summary figures, responsible people, Telegram link.

Uses its own temporary database. Client id/secret here are test-only values."""
import os, sys, tempfile, unittest
from datetime import date, datetime, timedelta
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
from app import clock
PASSWORD='OnlyForTemporaryTests_9841!'
HASH=hash_password(PASSWORD)
# Среда в середине месяца: сроки заявок в рабочих днях не зависят от даты запуска тестов.
FROZEN_TODAY=date(2026,9,16)
PDF=b'%PDF-1.4\nsynthetic request document'

def tearDownModule():
    engine.dispose()
    # app.db is shared when the entire suite runs in one Python process. Keep
    # this temporary directory alive until process exit so later test modules
    # do not inherit an engine pointing at a deleted SQLite file.

class BotApiTests(unittest.TestCase):
    def setUp(self):
        clock.FROZEN=FROZEN_TODAY
        Base.metadata.drop_all(engine);initialize()
        with unit(True) as s:
            admin=User(username='admin',name='Test Admin',role='admin',password_hash=HASH);s.add(admin);s.flush()
            for c in s.scalars(select(Company).where(Company.code!='UNASSIGNED')):
                s.add(CompanyUser(company_id=c.id,user_id=admin.id,role='finance'))
            # Директор утверждает каждую заявку, как в прежних проверках; политики статей проверяются отдельно.
            for c in s.scalars(select(Category)):c.director_policy='always'
        self.client=TestClient(app)
        r=self.client.post('/api/login',json={'username':'admin','password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        self.h={'X-CSRF-Token':r.json()['csrf']}
        self.today=today();self.yesterday=self.today-timedelta(days=1)
        b=self.client.get('/api/bootstrap').json()
        self.companies={c['code']:c['id'] for c in b['companies']}
        self.cat=b['categories'][1]['id'];self.cat_name=b['categories'][1]['name']
        self.dues=b['due_minimums']
        self.acc=self.account('Test bank','bank','UZS','1000000.00')
        self.extra=[]
    def tearDown(self):
        for c in self.extra:c.close()
        self.client.close()
        clock.FROZEN=None

    # -- helpers
    def post(self,path,data,client=None,headers=None):
        return (client or self.client).post(path,json=data,headers=headers or self.h)
    def zuma(self,h=None):return {**(h or self.h),'X-Company-ID':str(self.companies['ZUMA'])}
    def admin_headers(self,headers=None):
        """Заголовки администратора в той же компании, что и у переданных заголовков."""
        return {**self.h,**({'X-Company-ID':headers['X-Company-ID']} if headers and 'X-Company-ID' in headers else {})}
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
        # Временный пароль уже сменён: обязательная смена проверяется в tests/test_access.py.
        with unit(True) as s:s.scalar(select(User).where(User.username==name).execution_options(company_unscoped=True)).must_change_password=False
        c=TestClient(app);self.extra.append(c)
        r=c.post('/api/login',json={'username':name,'password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        return {'id':r.json()['user']['id'],'client':c,'h':{'X-CSRF-Token':r.json()['csrf']}}
    def request_body(self,amount,purpose,account=None,category=None,priority='urgent',status='draft'):
        with unit() as s:a=s.get(Account,account or self.acc)
        return {'company_id':a.company_id,'account_id':a.id,'channel':a.kind,'currency':a.currency,'category_id':category or self.cat,
                'counterparty':'Supplier LLC','amount':amount,'date':self.dues[priority],'purpose':purpose,'priority':priority,'status':status}
    def new_request(self,user,amount='600',day=None,account=None,category=None,headers=None,purpose='Оплата по договору 20208000900123456789'):
        """Черновик, внутренняя заявка и договор, отправка. Новая заявка не бывает на сегодня или в прошлом,
        поэтому срок оплаты (по умолчанию сегодня) ставится прямо в базе, как у заявок, дождавшихся срока."""
        cl,h=user['client'],headers or user['h']
        r=cl.post('/api/requests',json=self.request_body(amount,purpose,account,category),headers=h)
        self.assertEqual(r.status_code,200,r.text);rid=r.json()['id']
        for kind in ('internal','contract'):
            up=cl.post(f'/api/requests/{rid}/documents',params={'kind':kind},content=PDF,headers={**h,'Content-Type':'application/pdf','X-Filename':kind+'.pdf'})
            self.assertEqual(up.status_code,200,up.text)
        r=cl.post(f'/api/requests/{rid}/decision',json={'action':'submit','version':up.json()['request_version']},headers=h)
        self.assertEqual(r.status_code,200,r.text)
        with unit(True) as s:s.get(PaymentRequest,rid).due_date=day or self.today
        return rid
    def version(self,rid,headers=None):
        return next(r['version'] for r in self.client.get('/api/requests',headers=self.admin_headers(headers)).json() if r['id']==rid)
    def decide(self,user,rid,action='approve',note='Проверено для тестирования',headers=None):
        h=headers or user['h']
        r=self.post(f'/api/requests/{rid}/decision',{'action':action,'note':note,'version':self.version(rid,h)},user['client'],h)
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def checker(self):
        """Расчётный бухгалтер без привязанного Telegram: проверяет реквизиты и комплектность."""
        if not hasattr(self,'stage_checker'):self.stage_checker=self.make_user('accountant','stagebook')
        return self.stage_checker
    def check(self,rid,user=None,headers=None):
        return self.decide(user or self.checker(),rid,'check',headers=headers)
    def fully_approve(self,finance,rid,director=None):
        self.check(rid)
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
    def tg_user(self,telegram_id,auth=BOT):return self.client.get(f'/api/bot/v1/telegram-users/{telegram_id}',auth=auth)
    def groups(self):
        r=self.client.get('/api/bot/v1/report-groups',auth=BOT);self.assertEqual(r.status_code,200,r.text)
        return {g['chat_id']:g for g in r.json()['items']}
    def group(self,chat,telegram_id,auth=BOT):
        return self.client.get(f'/api/bot/v1/report-groups/{chat}',params={'telegram_user_id':telegram_id},auth=auth)
    def connect(self,chat,companies,telegram_id,title='Финансы Zuma',auth=BOT):
        return self.client.post('/api/bot/v1/report-groups',json={'chat_id':chat,'title':title,'companies':companies,'telegram_user_id':telegram_id},auth=auth)
    def disconnect(self,chat,telegram_id,auth=BOT):
        return self.client.post('/api/bot/v1/report-groups/remove',json={'chat_id':chat,'telegram_user_id':telegram_id},auth=auth)
    def audit_rows(self,action):
        with unit() as s:return list(s.scalars(select(Audit).where(Audit.action==action).order_by(Audit.id)))

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
        # The HTTP journal keeps the bot's authenticated calls only: refused calls do not fill it.
        http=lambda:[a.detail for a in self.audit_rows('API GET') if a.detail.startswith('/api/bot/v1/')]
        self.assertEqual(http(),[])
        self.assertEqual(self.pending(),[])
        self.assertEqual(http(),['/api/bot/v1/pending-requests; status=200'])

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
        checked=self.new_request(author,'350');self.check(checked)
        big=self.new_request(author,'1500000',day=self.today+timedelta(days=3))
        self.check(big);self.decide(finance,big)  # the director approves every amount of this category
        later=self.new_request(author,'200',day=self.today+timedelta(days=3));self.fully_approve(finance,later,director)
        r=self.summary();data=r.json()
        payments={p['number']:p for p in data['payments_today']}
        # Only approved requests due today or overdue are listed for payment.
        self.assertEqual(set(payments),{f'CF-{due:05d}',f'CF-{late:05d}'})
        self.assertEqual(payments[f'CF-{due:05d}']['stage_label'],'')
        self.assertEqual(payments[f'CF-{late:05d}']['stage_label'],f'Просрочено с {self.yesterday:%d.%m.%Y}')
        self.assertEqual(payments[f'CF-{due:05d}']['purpose'],self.cat_name)
        pending={p['number']:p['stage_label'] for p in data['pending_approvals']}
        self.assertEqual(pending,{f'CF-{waiting:05d}':'Проверка реквизитов расчётным бухгалтером',
                                  f'CF-{checked:05d}':'Проверка финансовым директором',f'CF-{big:05d}':'Утверждение директором'})
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
        author=self.make_user('employee','author')
        finance=self.make_user('finance','checker');director=self.make_user('director','boss')
        accountant=self.make_user('accountant','payer');cashier=self.make_user('cashier','teller')
        book=self.make_user('accountant','book')
        self.make_user('finance','silent')  # not linked: never listed
        for user,tg in ((finance,1001),(director,1002),(accountant,1003),(cashier,1004),(book,1008)):self.link(user,tg)
        rid=self.new_request(author,'600')
        # The company's settlement accountants check the details and documents first.
        items=self.pending()
        self.assertEqual([(i['request_id'],i['stage'],i['assignee_user_id'],i['assignee_telegram_id']) for i in items],
                         [(rid,'check',accountant['id'],1003),(rid,'check',book['id'],1008)])
        item=items[0]
        self.assertEqual((item['number'],item['status'],item['stage_label'],item['amount'],item['currency'],item['company'],item['url']),
                         (f'CF-{rid:05d}','pending','Проверка реквизитов расчётным бухгалтером','600.00','UZS','UZGERMED',''))
        self.assertTrue(item['purpose'].startswith(self.cat_name+': Оплата по договору ••6789'))
        entered=datetime.fromisoformat(item['stage_entered_at']);self.assertIsNotNone(entered.tzinfo)

        self.check(rid,book)
        items=self.pending()
        self.assertEqual([(i['request_id'],i['stage'],i['assignee_user_id'],i['assignee_telegram_id']) for i in items],[(rid,'finance',finance['id'],1001)])
        self.assertEqual(items[0]['stage_label'],'Проверка финансовым директором')
        self.assertGreaterEqual(datetime.fromisoformat(items[0]['stage_entered_at']),entered)

        self.decide(finance,rid)
        items=self.pending()
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in items],[('director',director['id'])])
        self.assertGreaterEqual(datetime.fromisoformat(items[0]['stage_entered_at']),entered)

        self.decide(director,rid)
        # The accountant who checked the request never pays it: only the other accountant is reminded.
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending()],[('bank_payment',accountant['id'])])

        paid=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600','date':str(self.today),
                                      'reference':'PAY-1','note':'Оплата по заявке','request_id':rid,'request_version':self.version(rid)},
                       accountant['client'],accountant['h'])
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(self.pending(),[])

        cash=self.account('Касса','cash','UZS','5000.00')
        second=self.make_user('cashier','teller2');self.link(second,1005)
        # The cash desk pays cash requests, but never its own (services.check_payer).
        foreign=self.new_request(author,'50',account=cash);self.fully_approve(finance,foreign,director)
        own=self.new_request(cashier,'40',account=cash);self.fully_approve(finance,own,director)
        self.assertEqual(sorted((i['request_id'],i['stage'],i['assignee_user_id']) for i in self.pending()),
                         sorted([(foreign,'cash_payment',cashier['id']),(foreign,'cash_payment',second['id']),(own,'cash_payment',second['id'])]))
        with unit(True) as s:s.get(User,second['id']).active=False
        # Nobody else may pay the cashier's own request, and the financier never pays: no reminder.
        self.assertEqual(sorted((i['request_id'],i['assignee_user_id']) for i in self.pending()),[(foreign,cashier['id'])])

    def test_closed_returned_and_unlinked_requests_are_not_reminded(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker')
        self.link(finance,1001)
        returned=self.new_request(author,'100');self.check(returned);self.decide(finance,returned,'return','Нужны документы от поставщика')
        # «Закрыта без оплаты» (прежнее «cancel»).
        cancelled=self.new_request(author,'100');self.check(cancelled);self.decide(self.me(),cancelled,'close','Больше не требуется оплата')
        rejected=self.new_request(author,'100');self.check(rejected)
        with unit(True) as s:s.get(PaymentRequest,rejected).status='rejected'  # legacy closed request
        draft=self.post('/api/requests',self.request_body('100','Черновик заявки'),author['client'],author['h'])
        self.assertEqual(draft.status_code,200,draft.text);self.assertEqual(draft.json()['status'],'draft')
        self.assertEqual(self.pending(),[])
        live=self.new_request(author,'100');self.check(live)
        self.assertEqual([i['request_id'] for i in self.pending()],[live])
        self.assertEqual(finance['client'].delete('/api/telegram',headers=finance['h']).status_code,200)
        self.assertEqual(self.pending(),[])

    def test_author_editor_and_other_companies_are_excluded(self):
        finance=self.make_user('finance','checker');other=self.make_user('finance','other')
        zuma_finance=self.make_user('finance','zumafin',headers=self.zuma())
        for user,tg in ((finance,1001),(other,1002),(zuma_finance,1003)):self.link(user,tg)
        own=self.new_request(finance,'100');self.check(own)  # the financier cannot approve their own request
        founder=self.make_user('founder','owner');self.link(founder,1006)  # reads everything, acts on nothing
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==own],[other['id']])
        admin2=self.make_user('admin','admin2');self.link(admin2,1004)
        zuma_acc=self.account('Zuma bank','bank','UZS','1000.00',headers=self.zuma())
        zuma_author=self.make_user('employee','zumaauthor',headers=self.zuma())
        zuma_book=self.make_user('accountant','zumabook',headers=self.zuma());self.link(zuma_book,1005)
        zid=self.new_request(zuma_author,'100',account=zuma_acc,category=self.category(self.zuma()),headers=self.zuma(zuma_author['h']))
        # The check stage belongs to the accountant of the request's own company only.
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending() if i['request_id']==zid],[('check',zuma_book['id'])])
        self.check(zid,zuma_book,self.zuma(zuma_book['h']))
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==zid],[zuma_finance['id']])
        with unit(True) as s:s.get(User,zuma_finance['id']).active=False
        # An administrator acts only through a role assigned in that company (security.scope_user).
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==zid],[])
        with unit(True) as s:s.merge(CompanyUser(company_id=self.companies['ZUMA'],user_id=admin2['id'],role='finance'))
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==zid],[admin2['id']])

    def test_the_role_assigned_in_the_company_decides(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker')
        dual=self.make_user('finance','dual');self.link(dual,1007)  # a financier elsewhere, the director here
        with unit(True) as s:s.merge(CompanyUser(company_id=self.companies['UZGERMED'],user_id=dual['id'],role='director'))
        rid=self.new_request(author,'100')
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==rid],[])
        self.check(rid)
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==rid],[])
        self.decide(finance,rid)
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending() if i['request_id']==rid],[('director',dual['id'])])

    def test_request_link_uses_the_public_https_address_only(self):
        finance=self.make_user('finance','checker');self.link(finance,1001)
        self.check(self.new_request(self.make_user('employee','author'),'100'))
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
        stale=self.post('/api/telegram/code',{},user['client'],user['h']).json()['code']
        with unit(True) as s:s.scalar(select(TelegramLinkCode)).expires_at=now()-timedelta(seconds=1)
        self.assertEqual(revoke(stale).json(),{'revoked':False})  # expired: nothing active to cancel
        with unit() as s:self.assertIsNone(s.scalar(select(TelegramLinkCode.user_id)))

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

    # -- «Мои заявки» in the private chat
    def test_my_requests_show_only_the_linked_users_own_items(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker');other=self.make_user('finance','other')
        self.link(finance,555101);self.link(other,555102)
        first=self.new_request(author,'100');second=self.new_request(author,'200');own=self.new_request(finance,'300')
        for rid in (first,second,own):self.check(rid)
        r=self.tg_user(555101);self.assertEqual(r.status_code,200,r.text);data=r.json()
        self.assertEqual({k:data[k] for k in ('linked','user_id','name','is_admin')},{'linked':True,'user_id':finance['id'],'name':'Checker','is_admin':False})
        # The same rows and rules as pending-requests; the financier never checks their own request.
        self.assertEqual(data['items'],[i for i in self.pending() if i['assignee_user_id']==finance['id']])
        self.assertEqual([i['request_id'] for i in data['items']],[first,second])
        self.assertEqual([i['request_id'] for i in self.tg_user(555102).json()['items']],[first,second,own])
        self.link(self.me(),555100)
        admin=self.tg_user(555100).json();self.assertEqual((admin['linked'],admin['is_admin']),(True,True))
        with unit() as s:
            rows=self.audit_rows('Бот: заявки пользователя')
            self.assertEqual({a.user_id for a in rows},{finance['id'],other['id'],admin['user_id']})
            # Telegram ids are personal data: neither the bot journal nor the HTTP log keeps them.
            self.assertFalse(s.scalar(select(Audit.id).where(Audit.detail.contains('55510')|Audit.entity_id.contains('55510'))))
            self.assertTrue(s.scalar(select(Audit.id).where(Audit.detail=='/api/bot/v1/telegram-users/{telegram_user_id}; status=200')))

    def test_my_requests_for_unlinked_disabled_or_invalid_telegram(self):
        empty={'linked':False,'user_id':None,'name':None,'is_admin':False,'items':[]}
        self.assertEqual(self.tg_user(555201).json(),empty)
        finance=self.make_user('finance','checker');self.link(finance,555202)
        self.check(self.new_request(self.make_user('employee','author'),'100'))
        self.assertTrue(self.tg_user(555202).json()['items'])
        with unit(True) as s:s.get(User,finance['id']).active=False
        self.assertEqual(self.tg_user(555202).json(),empty)
        for bad in ('0','-5','abc',str(2**53)):self.assertEqual(self.tg_user(bad).status_code,422,bad)
        for auth in (None,('test-bot','wrong-secret-'+'y'*30)):self.assertEqual(self.tg_user(555202,auth=auth).status_code,401)
        refused=self.audit_rows('Бот: отказ в доступе')
        self.assertEqual(refused[-1].detail,'/api/bot/v1/telegram-users/{telegram_user_id}')
        with unit() as s:self.assertFalse(s.scalar(select(Audit.id).where(Audit.detail.contains('55520'))))

    # -- groups connected from Telegram
    def test_admin_connects_changes_and_removes_a_group(self):
        chat=-1004444444444;self.link(self.me(),555300)
        admin_id=self.client.get('/api/me').json()['user']['id']
        self.account('Zuma bank','bank','UZS','777.00',headers=self.zuma())
        self.assertEqual(self.summary(chat=chat).status_code,403)
        choices=[{'code':'UZGERMED','name':'UZGERMED'},{'code':'ZUMA','name':'Zuma'}]
        self.assertEqual(self.group(chat,555300).json(),{'chat_id':chat,'connected':False,'source':None,'companies':[],'can_manage':True,'choices':choices})
        r=self.connect(chat,['zuma','UZGERMED'],555300,title='  Финансы \n Zuma ');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json(),{'chat_id':chat,'connected':True,'source':'site','companies':['UZGERMED','ZUMA'],'can_manage':True,'choices':choices})
        listed=self.groups()
        self.assertEqual(listed[chat],{'chat_id':chat,'title':'Финансы Zuma','companies':['UZGERMED','ZUMA'],'source':'site','scope_id':listed[chat]['scope_id']})
        self.assertEqual(listed[GROUP],{'chat_id':GROUP,'title':'','companies':['UZGERMED'],'source':'config','scope_id':listed[GROUP]['scope_id']})
        self.assertEqual(listed[GROUP_BOTH]['companies'],['UZGERMED','ZUMA'])
        # A group connected from Telegram gets exactly what a configured group gets (the scope fingerprint is per chat).
        summary=self.summary(chat=chat);self.assertEqual(summary.status_code,200,summary.text)
        own=lambda d:{k:v for k,v in d.items() if k not in ('chat_id','scope_id')}
        self.assertEqual(own(summary.json()),own(self.summary(chat=GROUP_BOTH).json()))
        connected=self.audit_rows('Telegram-группа подключена')
        self.assertEqual(sorted(a.company_id for a in connected),sorted([self.companies['UZGERMED'],self.companies['ZUMA']]))
        for a in connected:
            self.assertEqual((a.user_id,a.entity,a.entity_id),(admin_id,'telegram_group',str(chat)))
            self.assertEqual(a.detail,f'Группа «Финансы Zuma» ({chat}); компании: UZGERMED, ZUMA')
        self.assertIn('Telegram-группа подключена',[a['action'] for a in self.client.get('/api/audit',headers=self.zuma()).json()])

        r=self.connect(chat,['ZUMA'],555300,title='');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['companies'],['ZUMA'])
        self.assertEqual(self.groups()[chat]['title'],'Финансы Zuma')  # an empty title keeps the known one
        self.assertEqual({b['company'] for b in self.summary(chat=chat).json()['balances']},{'Zuma'})
        changed=self.audit_rows('Telegram-группа изменена')
        self.assertEqual(sorted(a.company_id for a in changed),sorted([self.companies['UZGERMED'],self.companies['ZUMA']]))
        self.assertTrue(all(a.detail.endswith('компании: ZUMA; было: UZGERMED, ZUMA') for a in changed))

        self.assertEqual(self.disconnect(chat,555300).json(),{'removed':True})
        self.assertEqual(self.disconnect(chat,555300).json(),{'removed':False})
        self.assertEqual(self.summary(chat=chat).status_code,403)
        self.assertNotIn(chat,self.groups())
        self.assertEqual([a.company_id for a in self.audit_rows('Telegram-группа отключена')],[self.companies['ZUMA']])
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroup,chat))
            self.assertFalse(s.scalar(select(Audit.id).where(Audit.detail.contains('555300')|Audit.entity_id.contains('555300'))))

    def scope(self,chat,auth=BOT):
        return self.client.get(f'/api/bot/v1/report-groups/{chat}/scope',auth=auth)

    def test_scope_check_before_a_resend_follows_the_current_rights(self):
        """Cash_Zuma_Bot stores scope_id with a summary snapshot and asks /scope before every part it
        sends again. Whatever narrows the rights must change or drop the fingerprint."""
        chat=-1009999999999;self.link(self.me(),555950)
        self.account('Zuma bank','bank','UZS','777.00',headers=self.zuma())
        self.assertEqual(self.scope(chat).json(),{'chat_id':chat,'allowed':False,'companies':[],'scope_id':None,'source':None})
        self.assertEqual(self.connect(chat,['ZUMA','UZGERMED'],555950).status_code,200)
        both=self.scope(chat).json()
        self.assertEqual((both['allowed'],both['companies'],both['source']),(True,['UZGERMED','ZUMA'],'site'))
        self.assertRegex(both['scope_id'],r'^[0-9a-f]{16}$')
        summary=self.summary(chat=chat).json()
        self.assertEqual((summary['companies'],summary['scope_id']),(both['companies'],both['scope_id']))
        self.assertEqual(self.groups()[chat]['scope_id'],both['scope_id'])
        self.assertEqual(self.scope(chat).json(),both)  # deterministic: the same rights give the same value
        # The same companies in another group never share a fingerprint: parts cannot be mixed up between chats.
        self.assertEqual(self.groups()[GROUP_BOTH]['companies'],both['companies'])
        self.assertNotEqual(self.groups()[GROUP_BOTH]['scope_id'],both['scope_id'])

        # The confirmed scenario: ZUMA + UZGERMED → UZGERMED between two parts of one summary.
        self.assertEqual(self.connect(chat,['UZGERMED'],555950).status_code,200)
        narrowed=self.scope(chat).json()
        self.assertEqual((narrowed['allowed'],narrowed['companies']),(True,['UZGERMED']))
        self.assertNotEqual(narrowed['scope_id'],both['scope_id'])
        fresh=self.summary(chat=chat).json()
        self.assertEqual(fresh['scope_id'],narrowed['scope_id'])
        self.assertEqual({b['company'] for b in fresh['balances']},{'UZGERMED'})
        # Widened back: the earlier value returns, so an unchanged scope always compares equal.
        self.assertEqual(self.connect(chat,['UZGERMED','ZUMA'],555950).status_code,200)
        self.assertEqual(self.scope(chat).json()['scope_id'],both['scope_id'])

        # A company switched off narrows the scope although nobody touched the group.
        with unit(True) as s:s.scalar(select(Company).where(Company.code=='ZUMA')).active=False
        self.assertEqual(self.scope(chat).json()['scope_id'],narrowed['scope_id'])
        self.assertEqual(self.scope(GROUP_ZUMA).json()['allowed'],False)  # configured group left without companies
        with unit(True) as s:s.scalar(select(Company).where(Company.code=='ZUMA')).active=True
        config=self.scope(GROUP).json()
        self.assertEqual((config['allowed'],config['companies'],config['source']),(True,['UZGERMED'],'config'))

        # Removed on the site: refused, and the refusal is in the journal.
        self.assertEqual(self.site_remove(chat).json(),{'removed':True})
        self.assertEqual(self.scope(chat).json(),{'chat_id':chat,'allowed':False,'companies':[],'scope_id':None,'source':None})
        self.assertIn('Проверка области доступа перед отправкой сохранённой сводки',[a.detail for a in self.audit_rows('Бот: группа не разрешена')])

        # The administrator who connected it loses the holding admin role: the group goes with them.
        admin2=self.make_user('admin','scope_admin');self.link(admin2,555951)
        self.assertEqual(self.connect(chat,['ZUMA'],555951).status_code,200)
        self.assertTrue(self.scope(chat).json()['allowed'])
        r=self.post(f'/api/users/{admin2["id"]}',{'role':'finance','active':True,'password':''});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.scope(chat).json()['allowed'],False)
        self.assertEqual(self.summary(chat=chat).status_code,403)

        for bad in (555001,0):self.assertEqual(self.scope(bad).status_code,422)
        for auth in (None,('test-bot','wrong-secret-'+'y'*30)):self.assertEqual(self.scope(chat,auth=auth).status_code,401)

    def test_only_a_linked_holding_administrator_manages_groups(self):
        chat=-1005555555555
        finance=self.make_user('finance','checker');self.link(finance,555401)
        founder=self.make_user('founder','owner');self.link(founder,555402)
        admin2=self.make_user('admin','admin2');self.link(admin2,555403)
        with unit(True) as s:s.get(User,admin2['id']).active=False
        for tg in (555400,555401,555402,555403):
            self.assertEqual(self.connect(chat,['UZGERMED'],tg).status_code,403,tg)
            self.assertEqual(self.disconnect(chat,tg).status_code,403,tg)
            state=self.group(chat,tg).json();self.assertEqual((state['can_manage'],state['choices']),(False,[]),tg)
        self.assertNotIn(chat,self.groups())
        refused=self.audit_rows('Бот: отказ в настройке группы')
        self.assertIn((finance['id'],'Не администратор холдинга'),[(a.user_id,a.detail) for a in refused])
        self.assertIn((None,'Telegram не привязан к активному пользователю'),[(a.user_id,a.detail) for a in refused])
        for auth in (None,('test-bot','wrong-secret-'+'y'*30)):
            self.assertEqual(self.connect(chat,['UZGERMED'],555401,auth=auth).status_code,401)
            self.assertEqual(self.group(chat,555401,auth=auth).status_code,401)
            self.assertEqual(self.client.get('/api/bot/v1/report-groups',auth=auth).status_code,401)

        # BOT_REPORT_GROUPS is fixed on the site: Telegram cannot change it, and it wins over a stored row.
        self.link(self.me(),555404)
        self.assertEqual(self.group(GROUP,555404).json(),{'chat_id':GROUP,'connected':True,'source':'config','companies':['UZGERMED'],'can_manage':False,'choices':[]})
        self.assertEqual(self.connect(GROUP,['ZUMA'],555404).status_code,409)
        self.assertEqual(self.disconnect(GROUP,555404).status_code,409)
        owner=self.admin_id()  # an HTTP call: never inside the write transaction below
        with unit(True) as s:s.add(TelegramGroup(chat_id=GROUP,title='Старая группа',companies='ZUMA',added_by=owner))
        shadowed=self.groups()[GROUP]
        self.assertEqual(shadowed,{'chat_id':GROUP,'title':'Старая группа','companies':['UZGERMED'],'source':'config','scope_id':shadowed['scope_id']})
        self.assertEqual({b['company'] for b in self.summary().json()['balances']},{'UZGERMED'})

    def test_group_settings_are_validated(self):
        chat=-1006666666666;self.link(self.me(),555500)
        cases=[([],'Выберите хотя бы одну компанию.'),(['UNASSIGNED'],'Служебное пространство «Не распределено» нельзя подключить к сводке.'),
               (['NOPE'],'Компания «NOPE» не найдена.'),(['ZUMA','zuma'],'Компания «zuma» указана дважды.')]
        for companies,message in cases:
            r=self.connect(chat,companies,555500);self.assertEqual((r.status_code,r.json()['detail']),(422,message),companies)
        for bad in (555001,0):
            self.assertEqual(self.connect(bad,['UZGERMED'],555500).status_code,422)
            self.assertEqual(self.disconnect(bad,555500).status_code,422)
            self.assertEqual(self.group(bad,555500).status_code,422)
        self.assertEqual(self.connect(chat,['UZGERMED'],555500,title='x'*256).status_code,422)
        self.assertEqual(self.connect(chat,['UZGERMED'],0).status_code,422)
        self.assertEqual(self.group(chat,0).status_code,422)
        self.assertEqual(self.client.get(f'/api/bot/v1/report-groups/{chat}',auth=BOT).status_code,422)
        extra=self.client.post('/api/bot/v1/report-groups',json={'chat_id':chat,'companies':['UZGERMED'],'telegram_user_id':555500,'members':[1]},auth=BOT)
        self.assertEqual(extra.status_code,422)
        with unit(True) as s:s.scalar(select(Company).where(Company.code=='ZUMA')).active=False
        r=self.connect(chat,['UZGERMED','ZUMA'],555500);self.assertEqual((r.status_code,r.json()['detail']),(422,'Компания «ZUMA» отключена.'))
        self.assertNotIn(chat,self.groups())
        self.assertEqual(self.connect(chat,['UZGERMED'],555500,title='x'*255).status_code,200)

    def test_inactive_company_is_dropped_from_the_group_list(self):
        both,zuma_only=-1007777777777,-1008888888888;self.link(self.me(),555600)
        self.assertEqual(self.connect(both,['UZGERMED','ZUMA'],555600).status_code,200)
        self.assertEqual(self.connect(zuma_only,['ZUMA'],555600).status_code,200)
        with unit(True) as s:s.scalar(select(Company).where(Company.code=='ZUMA')).active=False
        listed=self.groups()
        self.assertEqual(listed[both]['companies'],['UZGERMED'])
        self.assertNotIn(zuma_only,listed);self.assertNotIn(GROUP_ZUMA,listed)
        self.assertEqual(self.summary(chat=zuma_only).status_code,403)
        state=self.group(both,555600).json()
        self.assertEqual((state['companies'],state['choices']),(['UZGERMED'],[{'code':'UZGERMED','name':'UZGERMED'}]))
        # Still registered: the administrator sees it and can switch it off.
        self.assertEqual((self.group(zuma_only,555600).json()['connected'],self.group(zuma_only,555600).json()['companies']),(True,[]))
        self.assertEqual(self.disconnect(zuma_only,555600).json(),{'removed':True})

    # -- summary groups on the site («Пользователи» → «Telegram-группы сводки»)
    def site_groups(self,user=None,headers=None):
        user=user or self.me()
        return user['client'].get('/api/telegram-groups',headers=user['h'] if headers is None else headers)
    def site_remove(self,chat,user=None,headers=None):
        user=user or self.me()
        return user['client'].delete(f'/api/telegram-groups/{chat}',headers=user['h'] if headers is None else headers)
    def migrate(self,old,new,auth=BOT):
        return self.client.post('/api/bot/v1/report-groups/migrate',json={'from_chat_id':old,'to_chat_id':new},auth=auth)
    def admin_id(self):return self.client.get('/api/me').json()['user']['id']

    def test_holding_administrator_sees_and_disconnects_groups_on_the_site(self):
        chat=-1004040404040;self.link(self.me(),555700);admin_id=self.admin_id()
        self.assertEqual(self.connect(chat,['ZUMA','UZGERMED'],555700).status_code,200)
        r=self.site_groups();self.assertEqual(r.status_code,200,r.text)
        items={g['chat_id']:g for g in r.json()['items']}
        self.assertEqual(set(items),{chat,GROUP,GROUP_BOTH,GROUP_ZUMA})
        mine=items[chat];added=datetime.fromisoformat(mine.pop('added_at'))
        self.assertIsNotNone(added.tzinfo)
        self.assertEqual(mine,{'chat_id':chat,'title':'Финансы Zuma','source':'site','added_by':'Test Admin','can_remove':True,
                               'companies':[{'code':'UZGERMED','name':'UZGERMED','active':True},{'code':'ZUMA','name':'Zuma','active':True}]})
        self.assertEqual(items[GROUP_BOTH],{'chat_id':GROUP_BOTH,'title':'','source':'config','added_by':None,'added_at':None,'can_remove':False,
                                            'companies':[{'code':'UZGERMED','name':'UZGERMED','active':True},{'code':'ZUMA','name':'Zuma','active':True}]})
        self.assertEqual(r.json()['items'][0]['chat_id'],chat)  # titled groups first
        # Holding-wide: the same list whatever company is selected on the page.
        self.assertEqual(self.site_groups(headers=self.zuma()).json(),r.json())

        finance=self.make_user('finance','checker');founder=self.make_user('founder','owner')
        for other in (finance,founder):
            self.assertEqual(self.site_groups(other).status_code,403)
            self.assertEqual(self.site_remove(chat,other).status_code,403)
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get('/api/telegram-groups').status_code,401)
            self.assertEqual(anonymous.delete(f'/api/telegram-groups/{chat}').status_code,401)
        self.assertEqual(self.site_remove(chat,headers={}).status_code,403)  # no CSRF token
        self.assertEqual(self.site_remove(GROUP).status_code,409)  # BOT_REPORT_GROUPS is changed on the server only
        for bad in (0,555001):self.assertEqual(self.site_remove(bad).status_code,422,bad)
        self.assertIn(chat,self.groups())

        r=self.site_remove(chat);self.assertEqual((r.status_code,r.json()),(200,{'removed':True}),r.text)
        self.assertEqual(self.site_remove(chat).json(),{'removed':False})
        self.assertNotIn(chat,self.groups());self.assertEqual(self.summary(chat=chat).status_code,403)
        self.assertNotIn(chat,{g['chat_id'] for g in self.site_groups().json()['items']})
        removed=self.audit_rows('Telegram-группа отключена')
        # The page had UZGERMED selected, yet each company of the group gets its own journal row.
        self.assertEqual(sorted(a.company_id for a in removed),sorted([self.companies['UZGERMED'],self.companies['ZUMA']]))
        for a in removed:
            self.assertEqual((a.user_id,a.entity,a.entity_id),(admin_id,'telegram_group',str(chat)))
            self.assertEqual(a.detail,f'Группа «Финансы Zuma» ({chat}); компании: UZGERMED, ZUMA; отключена на сайте')
        self.assertIn('Telegram-группа отключена',[a['action'] for a in self.client.get('/api/audit',headers=self.zuma()).json()])

    def test_group_stops_when_its_administrator_is_disabled_or_demoted(self):
        kept,demoted_chat,disabled_chat,unchanged_chat=-1004141414141,-1004242424242,-1004343434343,-1004444444440
        self.link(self.me(),555800);admin_id=self.admin_id()
        self.assertEqual(self.connect(kept,['UZGERMED'],555800).status_code,200)
        admin2=self.make_user('admin','admin2');self.link(admin2,555801)
        admin3=self.make_user('admin','admin3');self.link(admin3,555802)
        admin4=self.make_user('admin','admin4');self.link(admin4,555803)
        self.assertEqual(self.connect(demoted_chat,['ZUMA','UZGERMED'],555801,title='Группа admin2').status_code,200)
        self.assertEqual(self.connect(disabled_chat,['ZUMA'],555802,title='').status_code,200)
        self.assertEqual(self.connect(unchanged_chat,['ZUMA'],555803).status_code,200)

        # Editing an administrator who stays an active administrator keeps the group
        # (the edit still unlinks their Telegram, as every change of rights does).
        self.assertEqual(self.post(f'/api/users/{admin4["id"]}',{'role':'admin','active':True,'password':''}).status_code,200)
        self.assertIn(unchanged_chat,self.groups())
        self.assertEqual(self.audit_rows('Telegram-группа отключена'),[])

        r=self.post(f'/api/users/{admin2["id"]}',{'role':'finance','active':True,'password':''});self.assertEqual(r.status_code,200,r.text)
        r=self.post(f'/api/users/{admin3["id"]}',{'role':'admin','active':False,'password':''});self.assertEqual(r.status_code,200,r.text)
        listed=self.groups()
        self.assertNotIn(demoted_chat,listed);self.assertNotIn(disabled_chat,listed)
        self.assertIn(kept,listed);self.assertIn(unchanged_chat,listed)
        self.assertEqual(self.summary(chat=demoted_chat).status_code,403)
        with unit() as s:self.assertEqual(sorted(g.chat_id for g in s.scalars(select(TelegramGroup))),sorted([kept,unchanged_chat]))
        rows=[(a.company_id,a.user_id,a.entity_id,a.detail) for a in self.audit_rows('Telegram-группа отключена')]
        uz,zu=self.companies['UZGERMED'],self.companies['ZUMA']
        demoted=f'Группа «Группа admin2» ({demoted_chat}); компании: UZGERMED, ZUMA; автоматически: подключивший её Admin2 больше не администратор холдинга'
        disabled=f'Группа {disabled_chat}; компании: ZUMA; автоматически: подключивший её Admin3 отключён'
        self.assertEqual(sorted(rows),sorted([(uz,admin_id,str(demoted_chat),demoted),(zu,admin_id,str(demoted_chat),demoted),
                                              (zu,admin_id,str(disabled_chat),disabled)]))

    def test_another_administrator_takes_a_group_over(self):
        chat=-1004545454545;self.link(self.me(),555900);admin_id=self.admin_id()
        self.assertEqual(self.connect(chat,['UZGERMED','ZUMA'],555900).status_code,200)
        admin2=self.make_user('admin','admin2');self.link(admin2,555901)
        with unit() as s:self.assertEqual(s.get(TelegramGroup,chat).added_by,admin_id)
        r=self.connect(chat,['UZGERMED'],555901,title='');self.assertEqual(r.status_code,200,r.text)
        with unit() as s:self.assertEqual(s.get(TelegramGroup,chat).added_by,admin2['id'])
        changed=self.audit_rows('Telegram-группа изменена')
        self.assertTrue(changed and all(a.user_id==admin2['id'] and a.detail.endswith('; теперь группа закреплена за Admin2') for a in changed))
        self.assertEqual({g['chat_id']:g['added_by'] for g in self.site_groups().json()['items']}[chat],'Admin2')
        # The first administrator leaves: the group now depends on admin2 and keeps working.
        r=self.post(f'/api/users/{admin_id}',{'role':'admin','active':False,'password':''},admin2['client'],admin2['h'])
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.groups()[chat]['companies'],['UZGERMED'])
        self.assertEqual(self.audit_rows('Telegram-группа отключена'),[])

    def test_only_an_explicit_company_role_brings_reminders(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','checker');self.link(finance,1001)
        rid=self.new_request(author,'100');self.check(rid)
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==rid],[finance['id']])
        with unit(True) as s:  # membership kept, role removed: users.role no longer widens access
            s.get(CompanyUser,(self.companies['UZGERMED'],finance['id'])).role=None
        self.assertEqual([i for i in self.pending() if i['request_id']==rid],[])

    def test_archiving_the_connecting_admin_disconnects_the_group(self):
        other=self.make_user('admin','admin2');self.link(other,1004)
        chat=-1005454545454
        self.assertEqual(self.connect(chat,['UZGERMED'],1004).status_code,200)
        self.assertIn(chat,self.groups())
        r=self.post(f'/api/admin/users/{other["id"]}/archive',{'reason':'Сотрудник уволен из компании'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn(chat,self.groups())
        with unit() as s:self.assertIsNone(s.get(TelegramGroup,chat))

    def test_groups_without_an_active_connecting_admin_get_no_summary_and_are_removed(self):
        from app.bot_api import drop_orphan_groups
        admin_id=self.admin_id();gone=self.make_user('admin','goneadmin')
        orphan,manual,kept=-1005151515151,-1005252525252,-1005353535353
        with unit(True) as s:
            s.add(TelegramGroup(chat_id=orphan,title='Бывшего админа',companies='UZGERMED',added_by=gone['id']))
            s.add(TelegramGroup(chat_id=manual,title='Вручную',companies='UZGERMED',added_by=None))
            s.add(TelegramGroup(chat_id=kept,title='Действующая',companies='UZGERMED',added_by=admin_id))
            # Changed directly in the database, so edit_user never ran.
            s.get(User,gone['id']).active=False
        listed=self.groups()
        self.assertIn(kept,listed);self.assertNotIn(orphan,listed);self.assertNotIn(manual,listed)
        self.assertEqual(self.summary(chat=orphan).status_code,403)
        self.assertEqual(self.summary(chat=kept).status_code,200)
        self.assertEqual(drop_orphan_groups(),2)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroup,orphan));self.assertIsNone(s.get(TelegramGroup,manual))
            self.assertIsNotNone(s.get(TelegramGroup,kept))
        self.assertEqual(len([a for a in self.audit_rows('Telegram-группа отключена') if 'нет действующего администратора' in a.detail]),2)
        self.assertEqual(drop_orphan_groups(),0)  # idempotent

    def test_startup_removes_stored_rows_shadowed_by_the_server_setting(self):
        from app.bot_api import drop_shadowed_groups
        other=-1004646464646;admin_id=self.admin_id()
        with unit(True) as s:
            s.add(TelegramGroup(chat_id=GROUP,title='Старая группа',companies='UZGERMED,ZUMA',added_by=admin_id))
            s.add(TelegramGroup(chat_id=other,title='Своя группа',companies='ZUMA',added_by=admin_id))
        with TestClient(app):pass  # the application's startup
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroup,GROUP))
            self.assertIsNotNone(s.get(TelegramGroup,other))
        replaced=self.audit_rows('Telegram-группа заменена настройкой сервера')
        self.assertEqual(sorted(a.company_id for a in replaced),sorted([self.companies['UZGERMED'],self.companies['ZUMA']]))
        for a in replaced:
            self.assertEqual((a.user_id,a.entity_id),(None,str(GROUP)))
            self.assertEqual(a.detail,f'Группа «Старая группа» ({GROUP}); компании: UZGERMED, ZUMA; группа задана в BOT_REPORT_GROUPS')
        self.assertEqual(drop_shadowed_groups(),0)  # idempotent
        self.assertEqual(len(self.audit_rows('Telegram-группа заменена настройкой сервера')),2)
        saved=os.environ['BOT_REPORT_GROUPS']
        try:
            # The operator removes the entry later: the old, wider Telegram connection does not come back.
            os.environ['BOT_REPORT_GROUPS']=f'{GROUP_ZUMA}:ZUMA'
            self.assertNotIn(GROUP,self.groups());self.assertEqual(self.summary().status_code,403)
            os.environ['BOT_REPORT_GROUPS']=''
            self.assertEqual(drop_shadowed_groups(),0)
        finally:os.environ['BOT_REPORT_GROUPS']=saved

    def test_group_follows_a_supergroup_migration(self):
        basic,supergroup,second=-412345678,-1009990000001,-412345679
        self.link(self.me(),556000);admin_id=self.admin_id()
        self.assertEqual(self.connect(basic,['ZUMA','UZGERMED'],556000,title='Финансы').status_code,200)
        with unit() as s:before=s.get(TelegramGroup,basic);stamp=before.added_at
        r=self.migrate(basic,supergroup);self.assertEqual((r.status_code,r.json()),(200,{'migrated':True}),r.text)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroup,basic))
            moved=s.get(TelegramGroup,supergroup)
            self.assertEqual((moved.title,moved.companies,moved.added_by,moved.added_at),('Финансы','ZUMA,UZGERMED',admin_id,stamp))
        listed=self.groups()
        self.assertNotIn(basic,listed);self.assertEqual(listed[supergroup]['companies'],['UZGERMED','ZUMA'])
        self.assertEqual(self.summary(chat=supergroup).status_code,200);self.assertEqual(self.summary(chat=basic).status_code,403)
        moved_rows=self.audit_rows('Telegram-группа перенесена')
        self.assertEqual(sorted(a.company_id for a in moved_rows),sorted([self.companies['UZGERMED'],self.companies['ZUMA']]))
        for a in moved_rows:
            self.assertEqual((a.user_id,a.entity,a.entity_id),(None,'telegram_group',str(supergroup)))
            self.assertEqual(a.detail,f'Группа «Финансы» ({supergroup}); компании: UZGERMED, ZUMA; стала супергруппой, прежний ID {basic}')

        # Telegram reports a migration twice (old and new chat); nothing is left to move the second time.
        self.assertEqual(self.migrate(basic,supergroup).json(),{'migrated':False})
        self.assertEqual(self.migrate(-400000001,-1009990000002).json(),{'migrated':False})
        self.assertIn('старая группа не была подключена кнопкой',self.audit_rows('Бот: перенос группы')[-1].detail)

        self.assertEqual(self.connect(second,['UZGERMED'],556000).status_code,200)
        for old,new in ((second,supergroup),(GROUP,-1009990000003),(second,GROUP)):
            self.assertEqual(self.migrate(old,new).status_code,409,(old,new))
        with unit() as s:self.assertIsNotNone(s.get(TelegramGroup,second))
        self.assertEqual(len(self.audit_rows('Бот: перенос группы отклонён')),3)
        self.assertEqual(self.groups()[supergroup]['companies'],['UZGERMED','ZUMA'])

        for old,new in ((second,second),(412345679,supergroup),(second,0),(second,1009990000004)):
            self.assertEqual(self.migrate(old,new).status_code,422,(old,new))
        extra=self.client.post('/api/bot/v1/report-groups/migrate',json={'from_chat_id':second,'to_chat_id':-1009990000004,'title':'x'},auth=BOT)
        self.assertEqual(extra.status_code,422)
        self.assertEqual(self.client.post('/api/bot/v1/report-groups/migrate',json={'from_chat_id':second},auth=BOT).status_code,422)
        for auth in (None,('test-bot','wrong-secret-'+'y'*30)):self.assertEqual(self.migrate(second,-1009990000004,auth=auth).status_code,401)
        with unit() as s:self.assertIsNone(s.get(TelegramGroup,-1009990000004))

    # -- reminders along the 2.14 route: accountant check, director policy, ВрИО, earlier rounds
    def category_named(self,name,headers=None):
        return next(c['id'] for c in self.client.get('/api/bootstrap',headers=headers or self.h).json()['categories'] if c['name']==name)
    def pay(self,user,rid,amount,reference):
        return self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':amount,'date':str(self.today),
                                        'reference':reference,'note':'Оплата по заявке','request_id':rid,'request_version':self.version(rid)},
                         user['client'],user['h'])

    def test_check_stage_reminds_only_the_accountants_of_the_requests_company(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','fin')
        book=self.make_user('accountant','book');zbook=self.make_user('accountant','zbook',headers=self.zuma())
        cashier=self.make_user('cashier','teller');director=self.make_user('director','boss')
        for user,tg in ((finance,1001),(book,1002),(zbook,1003),(cashier,1004),(director,1005)):self.link(user,tg)
        rid=self.new_request(author,'600')
        label='Проверка реквизитов расчётным бухгалтером'
        self.assertEqual([(i['request_id'],i['stage'],i['stage_label'],i['assignee_user_id']) for i in self.pending()],
                         [(rid,'check',label,book['id'])])
        # «Мои заявки» of the accountant and the morning summary show the same stage.
        self.assertEqual([(i['request_id'],i['stage']) for i in self.tg_user(1002).json()['items']],[(rid,'check')])
        self.assertEqual(self.tg_user(1001).json()['items'],[])
        self.assertEqual([(p['number'],p['stage_label']) for p in self.summary().json()['pending_approvals']],[(f'CF-{rid:05d}',label)])
        self.check(rid,book)
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending()],[('finance',finance['id'])])
        self.assertEqual([p['stage_label'] for p in self.summary().json()['pending_approvals']],['Проверка финансовым директором'])

    def test_skip_policy_has_no_director_stage_and_finance_completes_the_route(self):
        taxes=self.category_named('Налоги')
        r=self.client.put(f'/api/categories/{taxes}/director-policy',json={'policy':'skip','reason':'Регулярный налоговый платёж'},headers=self.h)
        self.assertEqual(r.status_code,200,r.text)
        author=self.make_user('employee','author');finance=self.make_user('finance','fin');director=self.make_user('director','boss')
        payer=self.make_user('accountant','payer')
        for user,tg in ((finance,1001),(director,1002),(payer,1003)):self.link(user,tg)
        tax=self.new_request(author,'600',category=taxes);usual=self.new_request(author,'700')
        for rid in (tax,usual):self.check(rid)
        self.assertEqual(sorted((i['request_id'],i['stage'],i['assignee_user_id']) for i in self.pending()),
                         sorted([(tax,'finance',finance['id']),(usual,'finance',finance['id'])]))
        done=self.decide(finance,tax)
        self.assertEqual((done['status'],done['approved_by'],done['route']['director_required']),('approved',None,False))
        self.decide(finance,usual)
        # The director is reminded only of the category that needs him; the tax request goes straight to payment.
        self.assertEqual(sorted((i['request_id'],i['stage'],i['assignee_user_id']) for i in self.pending()),
                         sorted([(tax,'bank_payment',payer['id']),(usual,'director',director['id'])]))
        paid=self.post('/api/ledger',{'account_id':self.acc,'category_id':taxes,'kind':'out','amount':'600','date':str(self.today),
                                      'reference':'PAY-TAX','note':'Оплата налога','request_id':tax,'request_version':self.version(tax)},payer['client'],payer['h'])
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual([(i['request_id'],i['stage']) for i in self.pending()],[(usual,'director')])

    def test_acting_director_receives_the_director_reminder(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','fin')
        director=self.make_user('director','boss');deputy=self.make_user('employee','deputy');other=self.make_user('employee','bystander')
        for user,tg in ((finance,1001),(director,1002),(deputy,1003),(other,1004)):self.link(user,tg)
        d=self.post('/api/delegations',{'user_id':deputy['id'],'replaced_user_id':director['id'],'role':'director','starts_on':str(self.today),
                                        'ends_on':str(self.today+timedelta(days=2)),'reason':'Отпуск директора по графику'})
        self.assertEqual(d.status_code,200,d.text)
        rid=self.new_request(author,'600');self.check(rid)
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending()],[('finance',finance['id'])])
        self.decide(finance,rid)
        self.assertEqual([(i['stage'],i['assignee_user_id']) for i in self.pending()],[('director',director['id']),('director',deputy['id'])])
        self.assertEqual([i['stage'] for i in self.tg_user(1003).json()['items']],['director'])
        done=self.decide(deputy,rid);self.assertEqual(done['status'],'approved')
        # A revoked substitution stops the reminders at once.
        second=self.new_request(author,'500');self.check(second);self.decide(finance,second)
        self.assertIn(deputy['id'],[i['assignee_user_id'] for i in self.pending() if i['request_id']==second])
        self.assertEqual(self.post(f"/api/delegations/{d.json()['id']}/revoke",{'reason':'Директор вернулся из отпуска'}).status_code,200)
        self.assertEqual([i['assignee_user_id'] for i in self.pending() if i['request_id']==second],[director['id']])

    def test_payer_who_took_part_in_an_earlier_round_is_not_reminded(self):
        author=self.make_user('employee','author');finance=self.make_user('finance','fin');director=self.make_user('director','boss')
        first=self.make_user('accountant','roundone');second=self.make_user('accountant','roundtwo');payer=self.make_user('accountant','payer')
        for user,tg in ((first,1001),(second,1002),(payer,1003)):self.link(user,tg)
        # Returned after the first check, then sent again (the lead time is checked again) and checked by another accountant.
        rid=self.new_request(author,'600',day=date.fromisoformat(self.dues['urgent']));self.check(rid,first)
        self.decide(finance,rid,'return','Уточните реквизиты получателя')
        self.decide(author,rid,'submit')
        # A new round: every accountant may check it again, the first one included.
        self.assertEqual({i['assignee_user_id'] for i in self.pending() if i['request_id']==rid},{first['id'],second['id'],payer['id']})
        self.check(rid,second);self.decide(finance,rid);self.decide(director,rid)
        with unit(True) as s:s.get(PaymentRequest,rid).due_date=self.today  # the payment day has come
        self.assertEqual([(i['request_id'],i['stage'],i['assignee_user_id']) for i in self.pending()],[(rid,'bank_payment',payer['id'])])
        # A replaced file resets the approvals, but the earlier checker still never pays.
        other=self.new_request(author,'500');self.check(other,first)
        up=author['client'].post(f'/api/requests/{other}/documents',params={'kind':'contract'},content=PDF+b'% corrected invoice\n',
                                 headers={**author['h'],'Content-Type':'application/pdf','X-Filename':'contract-v2.pdf','X-Request-Version':str(self.version(other))})
        self.assertEqual(up.status_code,200,up.text);self.assertTrue(up.json()['approval_reset'])
        self.check(other,second);self.decide(finance,other);self.decide(director,other)
        self.assertEqual(sorted((i['request_id'],i['assignee_user_id']) for i in self.pending()),sorted([(rid,payer['id']),(other,payer['id'])]))
        self.assertEqual(self.pay(first,rid,'600','PAY-EARLIER').status_code,403)
        self.assertEqual(self.pay(payer,rid,'600','PAY-OUTSIDER').status_code,200)

    def me(self):
        return {'client':self.client,'h':self.h}


if __name__=='__main__':unittest.main(verbosity=2)
