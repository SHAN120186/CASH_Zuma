import json
from datetime import date, datetime, time, timedelta
from typing import Literal
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func
from .db import unit, User, Category, Audit, CashPlan, Company, PlanNote, now
from .security import session_user
from .services import get, amount, log

router=APIRouter()

class PlanItem(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    category_id:int
    kind:Literal['in','out']
    amount:str|None=None

class PlanInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    month:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    currency:Literal['UZS','USD','EUR']
    company_id:int|None=None
    scenario:Literal['A','B','V']='A'
    opening:str|None=None
    version:int=Field(ge=0)
    items:list[PlanItem]=Field(max_length=2000)
    reason:str=Field(min_length=10,max_length=1000)

@router.post('/api/cash-plan')
def save_plan(data:PlanInput,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'budget')
        company_id=data.company_id or s.scalar(select(Company.id).where(Company.code=='UZGERMED'))
        get(s,Company,company_id)
        p=s.scalar(select(CashPlan).where(CashPlan.company_id==company_id,CashPlan.scenario==data.scenario,CashPlan.month==data.month,CashPlan.currency==data.currency))
        if data.version!=(p.version if p else 0):raise HTTPException(409,'План уже изменён. Обновите отчёт.')
        payload={};keys=set()
        for item in data.items:
            get(s,Category,item.category_id);key=f'{item.category_id}:{item.kind}'
            if key in keys:raise HTTPException(422,'Статья и направление повторяются.')
            keys.add(key)
            if item.amount is not None:payload[key]=amount(item.amount,True)*(1 if item.kind=='in' else -1)
        opening=amount(data.opening,True) if data.opening is not None else None
        before={'payload':json.loads(p.payload),'opening':p.opening,'version':p.version} if p else None
        if not p:p=CashPlan(company_id=company_id,scenario=data.scenario,month=data.month,currency=data.currency,version=0);s.add(p)
        p.version+=1;p.opening=opening;p.payload=json.dumps(payload);s.flush()
        log(s,u,'Сохранён план денежных потоков','cash_plan',p.id,json.dumps({'month':data.month,'currency':data.currency,'before':before,'after':{'payload':payload,'opening':opening,'version':p.version},'reason':data.reason},ensure_ascii=False))
        return {'version':p.version}

class PlanNoteInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    company_id:int
    month:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    currency:Literal['UZS','USD','EUR']
    scenario:Literal['A','B','V']='A'
    indicator:str=Field(min_length=1,max_length=80)
    note:str=Field(default='',max_length=2000)

@router.post('/api/plan-note')
def save_plan_note(data:PlanNoteInput,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'budget');get(s,Company,data.company_id)
        n=s.scalar(select(PlanNote).where(PlanNote.company_id==data.company_id,PlanNote.month==data.month,PlanNote.currency==data.currency,PlanNote.scenario==data.scenario,PlanNote.indicator==data.indicator))
        if not n:
            n=PlanNote(company_id=data.company_id,month=data.month,currency=data.currency,scenario=data.scenario,indicator=data.indicator,updated_by=u.id);s.add(n)
        n.note=data.note;n.updated_by=u.id;n.updated_at=now()
        log(s,u,'Изменено примечание к отклонению','plan_note','',f'{data.month}; {data.scenario}; {data.indicator}')
        return {'ok':True}

@router.get('/api/audit/history')
def audit_history(request:Request,date_from:date|None=None,date_to:date|None=None,user_id:int|None=None,action:str='',technical:bool=False,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if date_from and date_to and date_from>date_to:raise HTTPException(422,'Начало периода позже окончания.')
    with unit() as s:
        session_user(s,request,'audit');q=select(Audit)
        # Stored audit timestamps are UTC; date filters refer to the user's Tashkent day.
        if date_from:q=q.where(Audit.created_at>=datetime.combine(date_from,time.min)-timedelta(hours=5))
        if date_to:q=q.where(Audit.created_at<datetime.combine(date_to+timedelta(days=1),time.min)-timedelta(hours=5))
        if user_id is not None:q=q.where(Audit.user_id==user_id)
        if action:q=q.where(Audit.action==action)
        if not technical:q=q.where(~Audit.action.like('API %'))
        total=s.scalar(select(func.count()).select_from(q.subquery()))
        users=list(s.scalars(select(User).order_by(User.name)));names={u.id:u.name for u in users}
        items=[{'id':a.id,'date':(a.created_at+timedelta(hours=5)).strftime('%d.%m.%Y %H:%M'),'user':names.get(a.user_id,'Система'),'action':a.action,'entity':a.entity,'entity_id':a.entity_id,'detail':a.detail} for a in s.scalars(q.order_by(Audit.created_at.desc(),Audit.id.desc()).offset((page-1)*page_size).limit(page_size))]
        return {'items':items,'total':total,'users':[{'id':u.id,'name':u.name} for u in users], 'actions':list(s.scalars(select(Audit.action).distinct().where(~Audit.action.like('API %')).order_by(Audit.action)))}
