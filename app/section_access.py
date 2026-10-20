"""Company-specific section grants, checked again on every authenticated web request."""
import json,re
from fastapi import HTTPException
from sqlalchemy import select
from .db import Setting

WRITE={'write','request','request_edit','request_check','approve','pay_bank','pay_cash','budget','plan','import','schedule','catalog','approval_policy','counterparties','receipt_edit','receipt_delete','request_delete'}
# Navigation key, label, read prerequisites, explicit read grants, edit grants.
SECTIONS={
 'home':('Обзор',{'view','overview'},{'view'},set()),
 'accounts':('Банк и касса',{'view'},{'view'},{'write','catalog'}),
 'ledger':('Операции',{'ledger'},{'ledger'},{'write'}),
 'requests':('Заявки',{'view','request','request_check','pay_bank','pay_cash'},{'view'},{'request','request_edit'}),
 'calendar':('Календарь',{'view','overview','calendar'},{'calendar','overview'},{'receipts','receipt_edit','receipt_delete'}),
 'report':('Cash Flow',{'export'},{'view','export'},{'plan','import','schedule'}),
 'pnl':('P&L',{'export'},{'export'},{'import'}),
 'balance':('Баланс',{'export'},{'export'},{'import'}),
 'rating':('IFRS Rating',{'export'},{'export'},{'import'}),
 'business':('Бизнес-планы',{'export'},{'export'},{'import'}),
 'reports':('Архив отчётов',{'export'},{'export'},{'import'}),
 'budgets':('Бюджеты',{'view'},{'view'},{'budget'}),
 'approval':('Согласование',{'approval_policy'},{'approval_policy'},{'approval_policy'}),
 'counterparties':('Контрагенты',{'counterparties'},{'counterparties'},{'counterparties'}),
 'categories':('Справочники',{'catalog'},{'catalog'},{'catalog'}),
 'users':('Пользователи',{'users'},{'users'},set()),
 'audit':('Журнал',{'audit'},{'audit'},set()),
 'import':('Импорт',{'import'},{'import'},{'import'}),
}
MODES={'default','hidden','view','edit'}
def key(cid,uid):return f'sections:{cid}:{uid}'
def stored(s,cid,uid):
    row=s.get(Setting,key(cid,uid))
    return json.loads(row.value) if row else {'version':0,'sections':{}}
def defaults(permissions):
    result={k:('edit' if edit & permissions else 'view') if read & permissions else 'hidden' for k,(_,read,_,edit) in SECTIONS.items()}
    if {'pay_bank','pay_cash'} & permissions and result['ledger']!='hidden':result['ledger']='edit'
    if {'request_check','approve','pay_bank','pay_cash'} & permissions and result['requests']!='hidden':result['requests']='edit'
    if {'schedule','receipts','receipt_edit','receipt_delete'} & permissions and result['calendar']!='hidden':result['calendar']='edit'
    return result
def apply(s,user,request):
    cid=s.info.get('company_id')
    from .security import perms_of
    base=set(perms_of(user))
    if user.role=='admin':
        user._sections={k:'edit' for k in SECTIONS};user._section_overrides={};return
    policy=stored(s,cid,user.id) if cid else {'sections':{}}
    overrides=policy.get('sections',{})
    if not overrides:
        user._sections=None;user._section_overrides={};return
    modes=defaults(base)
    for name,mode in overrides.items():
        if name in SECTIONS and mode!='default':modes[name]=mode
    if user.role=='founder':modes={k:('view' if v!='hidden' else v) for k,v in modes.items()}
    routed=modules(request.url.path)
    bootstrap=request.url.path in ('/api/me','/api/bootstrap')
    for name,mode in overrides.items():
        if name in SECTIONS and mode in ('view','edit') and (bootstrap or name in routed):
            base|=SECTIONS[name][2]
            if mode=='edit' and user.role!='founder':base|=SECTIONS[name][3]
    user._permissions=frozenset(base);user._sections=modes;user._section_overrides=overrides
    enforce(user,request)
def can_edit_section(user,*names):
    """Capabilities in GET responses must agree with the write-route guards."""
    if user.role=='admin':return True
    if user.role=='founder':return False
    modes=getattr(user,'_sections',None)
    return modes is None or all(modes.get(name)=='edit' for name in names)

def modules(path):
    path=path.replace('/api/bot/v1/','/api/',1)
    if path.startswith('/api/admin/') or path in ('/api/me','/api/bootstrap','/api/companies','/api/logout','/api/password'):return ()
    routes=[(r'/api/(?:request-documents|requests|budget-check|request-rules)(?:/|$)',('requests',)),
      (r'/api/(?:ledger|documents|export/ledger)(?:[/.]|$)',('ledger',)),
      (r'/api/accounts(?:/|$)',('accounts',)),(r'/api/(?:dashboard|reserve|sales-overview)(?:/|$)',('home','calendar')),
      (r'/api/(?:receipts|sales-receipts)(?:/|$)',('calendar',)),
      (r'/api/calendar-days(?:/|$)',('calendar','approval')),
      (r'/api/financial-statements(?:/|$)',('pnl','balance')),
      (r'/api/ifrs-rating(?:/|$)',('rating',)),
      (r'/api/business-(?:projects|templates)(?:/|$)',('business',)),
      (r'/api/report-archives(?:/|$)',('reports','business')),
      (r'/api/budgets(?:/|$)',('budgets',)),
      (r'/api/counterparties(?:/|$)',('counterparties',)),
      (r'/api/categories/[^/]+/(?:director-policy|skip-allowed)',('approval',)),
      (r'/api/categories(?:/|$)',('categories',)),
      (r'/api/(?:approval|director-policy)',('approval',)),
      (r'/api/(?:users|company-users|delegations)(?:/|$)',('users',)),
      (r'/api/audit(?:/|$)',('audit',)),
      (r'/api/(?:report|group-report|export/report|cash-plan|plan-note|plan-import|report-schedules)(?:[/.]|$)',('report',)),
      (r'/api/(?:import|model)(?:/|$)',('import',))]
    return next((names for pattern,names in routes if re.match(pattern,path)),())
def enforce(user,request):
    modes=getattr(user,'_sections',None)
    if not modes or user.role=='admin':return
    names=modules(request.url.path)
    if not names:return
    if request.url.path.endswith('/financial-statements/reconciliation') and not all(modes.get(n)!='hidden' for n in ('pnl','balance')):
        raise HTTPException(403,'Сверка требует доступа к P&L и балансу.')
    # Archive writes belong to the archive, even if business plans remain editable.
    if names==('reports','business') and request.method not in ('GET','HEAD'):names=('reports',)
    available=[n for n in names if modes.get(n,'hidden')!='hidden']
    if not available:raise HTTPException(403,'Раздел скрыт администратором.')
    if request.method not in ('GET','HEAD') and not any(modes[n]=='edit' for n in available):
        raise HTTPException(403,'Для этого раздела доступен только просмотр.')
    if request.url.path.startswith('/api/financial-statements') and request.method not in ('GET','HEAD') and not all(modes.get(n)=='edit' for n in ('pnl','balance')):
        raise HTTPException(403,'Обновление общего источника требует редактирования P&L и баланса.')
    if request.method in ('GET','HEAD'):
        # Do not alter the role's normal read permissions; write endpoints are gated above.
        return
