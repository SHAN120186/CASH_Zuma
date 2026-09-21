"""Management cash flow, single-currency actuals and independently entered monthly plans."""
import json
from datetime import date
from sqlalchemy import select
from .db import Account, Category, Ledger, CashPlan, PlanNote
from .services import effective_cashflows, money

ACTIVITIES = {'operating':'Операционная деятельность', 'investing':'Инвестиционная деятельность', 'financing':'Финансовая деятельность'}

def report(s, year, currency, company_id, scenario='A', as_of=None):
    accounts = list(s.scalars(select(Account).where(Account.currency == currency, Account.company_id == company_id)))
    aids = {a.id for a in accounts}
    categories = list(s.scalars(select(Category).order_by(Category.id)))
    cutoff=min(as_of,date(year,12,31)) if as_of else date(year,12,31)
    entries = [t for t in s.scalars(effective_cashflows(s).where(Ledger.date <= cutoff)) if t.account_id in aids and t.kind != 'transfer']
    periods = {p.month: p for p in s.scalars(select(CashPlan).where(CashPlan.company_id==company_id,CashPlan.scenario==scenario,CashPlan.currency == currency, CashPlan.month >= f'{year}-01', CashPlan.month <= f'{year}-12'))}
    notes = {f'{n.month}:{n.indicator}':n.note for n in s.scalars(select(PlanNote).where(PlanNote.company_id==company_id,PlanNote.scenario==scenario,PlanNote.currency==currency,PlanNote.month>=f'{year}-01',PlanNote.month<=f'{year}-12'))}
    payloads = [json.loads(periods[f'{year}-{m:02}'].payload) if f'{year}-{m:02}' in periods else {} for m in range(1,13)]
    rows = []
    for c in categories:
        kinds = {('in' if c.type == 'income' else 'out')}
        kinds.update(t.kind for t in entries if t.category_id == c.id)
        kinds.update(k.split(':')[1] for p in payloads for k in p if k.split(':')[0] == str(c.id))
        for kind in sorted(kinds):
            key = f'{c.id}:{kind}'
            actual = [sum((t.amount if kind == 'in' else -t.amount) for t in entries if t.category_id == c.id and t.kind == kind and t.date.year == year and t.date.month == m) for m in range(1,13)]
            plan = [p.get(key) for p in payloads]
            rows.append({'key':key,'category_id':c.id,'category':c.name,'kind':kind,'activity':c.activity,'values':actual,'plan':plan})
    def total(values): return None if not values or any(v is None for v in values) else sum(values)
    net = [sum(r['values'][m] for r in rows) for m in range(12)]
    plan_net = [total([r['plan'][m] for r in rows]) for m in range(12)]
    groups = [{'activity':key,'name':name,'values':[sum(r['values'][m] for r in rows if r['activity']==key) for m in range(12)],'plan':[total([r['plan'][m] for r in rows if r['activity']==key]) for m in range(12)]} for key,name in ACTIVITIES.items()]
    # Opening balances are introduced on their original dates, never as operating income.
    opening=[];closing=[];adjustments=[];plan_opening=[];plan_closing=[]
    for m in range(1,13):
        start=date(year,m,1);end=date(year+1,1,1) if m==12 else date(year,m+1,1)
        prior=sum(t.amount if t.kind=='in' else -t.amount for t in entries if t.date<start)
        base=sum(a.opening for a in accounts if a.opening_date<start or a.opening_date==date(year,1,1))
        opening.append(base+prior)
        adjustment=sum(a.opening for a in accounts if start<=a.opening_date<end and a.opening_date>date(year,1,1))
        adjustments.append(adjustment)
        closing.append(opening[-1]+net[m-1]+adjustment)
        p=periods.get(f'{year}-{m:02}')
        po=p.opening if p and p.opening is not None else (plan_closing[-1] if plan_closing else None)
        plan_opening.append(po)
        plan_closing.append(po+plan_net[m-1] if po is not None and plan_net[m-1] is not None else None)
    def formatted(values):return [money(v) if v is not None else None for v in values]
    for r in rows+groups:
        r['values']=formatted(r['values']);r['plan']=formatted(r['plan'])
    return {'year':year,'currency':currency,'company_id':company_id,'scenario':scenario,'as_of':str(cutoff),'notes':notes,'rows':rows,'groups':groups,'totals':formatted(net),'plan_totals':formatted(plan_net),
            'opening':formatted(opening),'closing':formatted(closing),'opening_adjustments':formatted(adjustments),
            'plan_opening':formatted(plan_opening),'plan_closing':formatted(plan_closing),
            'plan_versions':[periods[f'{year}-{m:02}'].version if f'{year}-{m:02}' in periods else 0 for m in range(1,13)],
            'plan_opening_input':[money(periods[f'{year}-{m:02}'].opening) if f'{year}-{m:02}' in periods and periods[f'{year}-{m:02}'].opening is not None else None for m in range(1,13)]}

def export_rows(data):
    rows=[('Остаток на начало',data['opening'],data['plan_opening'])]
    for group in data['groups']:
        rows.append((group['name'],group['values'],group['plan']))
        for r in data['rows']:
            if r['activity']==group['activity']:
                rows.append((('Поступление · ' if r['kind']=='in' else 'Выплата · ')+r['category'],r['values'],r['plan']))
    rows.append(('Чистое изменение денег',data['totals'],data['plan_totals']))
    if any(v!='0.00' for v in data['opening_adjustments']):rows.append(('Ввод начальных остатков',data['opening_adjustments'],[None]*12))
    rows.append(('Остаток на конец',data['closing'],data['plan_closing']))
    return rows
