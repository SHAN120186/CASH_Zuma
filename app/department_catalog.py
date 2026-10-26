"""Company-owned department names; codes remain stable in historical requests."""
import json,secrets
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from .db import Setting,unit
from .security import session_user
from .company_scope import get_setting,setting_key

router=APIRouter()
UZ={'qc':'QC','ml':'ML','engineering':'Muhandislik bo‘limi (ITO)','warehouse':'Ombor','production':'Ishlab chiqarish','office':'Umumiy xaridlar (ofis)','facilities':'Xo‘jalik qismi'}
def catalog(s):
    from .services import REQUEST_DEPARTMENTS
    row=get_setting(s,'departments')
    return json.loads(row.value) if row else {'version':0,'items':[{'code':k,'name_ru':v,'name_uz':UZ[k],'active':True} for k,v in REQUEST_DEPARTMENTS.items()]}
def labels(s):return {x['code']:x['name_ru'] for x in catalog(s)['items']}
def validate(s,code,old=None):
    if not code or code==old:return
    if not any(x['code']==code and x['active'] for x in catalog(s)['items']):raise HTTPException(422,'Выберите действующий отдел.')
class DepartmentIn(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:int=Field(ge=0)
    name_ru:str=Field(min_length=1,max_length=120)
    name_uz:str=Field(min_length=1,max_length=120)
    active:bool=True
def save(s,u,data,code=None):
    from .services import log
    current=catalog(s)
    if data.version!=current['version']:raise HTTPException(409,'Справочник изменён. Обновите список отделов.')
    item=next((x for x in current['items'] if x['code']==code),None)
    if code and item is None:raise HTTPException(404,'Отдел не найден в выбранной компании.')
    if any(x['code']!=code and (x['name_ru'].casefold()==data.name_ru.casefold() or x['name_uz'].casefold()==data.name_uz.casefold()) for x in current['items']):raise HTTPException(409,'Отдел с таким названием уже существует.')
    before=dict(item) if item else None
    if item is None:
        item={'code':'dept_'+secrets.token_hex(6)};current['items'].append(item)
    item.update(name_ru=data.name_ru,name_uz=data.name_uz,active=data.active)
    current['version']+=1
    row=get_setting(s,'departments')
    if row:row.value=json.dumps(current,ensure_ascii=False)
    else:s.add(Setting(key=setting_key(s,'departments'),value=json.dumps(current,ensure_ascii=False)))
    log(s,u,'Изменён справочник отделов','department',item['code'],json.dumps({'before':before,'after':item},ensure_ascii=False))
    return current
def admin(s,request):
    u,_=session_user(s,request,'catalog')
    if u.role!='admin':raise HTTPException(403,'Управление отделами доступно администратору холдинга.')
    return u
@router.get('/api/departments')
def departments(request:Request):
    with unit() as s:
        u,_=session_user(s,request,'catalog')
        return {**catalog(s),'can_edit':u.role=='admin','company_id':s.info['company_id']}
@router.post('/api/departments')
def create(data:DepartmentIn,request:Request):
    with unit(True) as s:return save(s,admin(s,request),data)
@router.put('/api/departments/{code}')
def edit(code:str,data:DepartmentIn,request:Request):
    with unit(True) as s:return save(s,admin(s,request),data,code)
