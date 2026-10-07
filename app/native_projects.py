"""Private recalculation of uploaded native workbooks, with explicit error evidence."""
import hashlib
import math
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .business_projects import (snapshot, sources, expanded_sources, own, revision_ok,
                               detail, dump, MAX_FILE)
from .db import (BusinessProject, BusinessGeneration, ReportArchive, ReportArchiveFile, now, unit)
from .main import get, log
from .report_archives import authorize, MIMES
from sqlalchemy import select, func
from uuid import NAMESPACE_URL, uuid5

router = APIRouter()


class NativeIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    overrides: dict = Field(default_factory=dict, max_length=30)


class NativeGenerateIn(NativeIn):
    start: date


def native_candidate(files):
    from .native_uzgermed import describe_profile
    candidates = []
    for item in files:
        if PurePosixPath(item['name']).suffix.lower() not in ('.xlsx', '.xltx'):
            continue
        profile = describe_profile(item['content'])
        if not profile.get('supported'):
            continue
        profile = {**profile, 'source_name': item['name'],
                   'source_sha256': hashlib.sha256(item['content']).hexdigest()}
        candidates.append({'profile': profile, 'content': item['content']})
    if len(candidates) > 1:
        raise HTTPException(422, 'В папке несколько оригинальных финансовых моделей. Создайте отдельную папку для каждой модели, чтобы не смешивать их цифры.')
    return candidates[0] if candidates else None


def project_native_model(records, extraction):
    files = expanded_sources([record for record in records
                              if PurePosixPath(record.filename).suffix.lower() in ('.xlsx', '.xltx', '.zip')])
    candidate = native_candidate(files)
    if not candidate:
        return None
    profile = candidate['profile']
    saved = extraction.get('native', {})
    if saved.get('source_sha256') == profile['source_sha256']:
        profile = {**profile, 'overrides': saved.get('overrides', {}), 'preview': saved.get('preview'),
                   'quality_notes': saved.get('quality_notes', [])}
        calculated = {item['key']: item.get('calculated') for item in saved.get('metrics', [])}
        profile['metrics'] = [{**item, 'calculated': calculated.get(item['key'])} for item in profile['metrics']]
    return profile


def parameter_overrides(profile, overrides):
    allowed = {item['key']: item for item in profile['parameters']}
    if set(overrides) - allowed.keys():
        raise HTTPException(422, 'Можно менять только показанные исходные параметры оригинала.')
    cells = {}
    for key, value in overrides.items():
        item = allowed[key]
        if not item.get('editable', True):
            raise HTTPException(422, f"{item['label']}: поле доступно только для чтения. Для изменения обновите исходную книгу Excel.")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise HTTPException(422, f"{item['label']}: укажите конечное число.")
        if value < item.get('min', -1e15) or value > item.get('max', 1e15):
            raise HTTPException(422, f"{item['label']}: значение за пределами допустимого диапазона.")
        if item.get('step') == 1 and not float(value).is_integer():
            raise HTTPException(422, f"{item['label']}: укажите целое число.")
        cells.setdefault(item['sheet'], {})[item['cell']] = value
    effective = {key: overrides.get(key, item.get('value')) for key, item in allowed.items()}
    grace, term, use = (effective.get('РКЛ!' + cell) for cell in ('AA7', 'AA8', 'AA10'))
    if grace is not None and term is not None and grace >= term:
        raise HTTPException(422, 'Льготный период должен быть меньше срока нового кредита.')
    if use is not None and term is not None and use > term:
        raise HTTPException(422, 'Период использования кредитной линии не может превышать срок кредита.')
    loading = effective.get('Производ. с учетом загрузки!B19')
    growth = effective.get('Производ. с учетом загрузки!Q19')
    if loading is not None and growth is not None and loading + 35 * growth / 12 > 1 + 1e-12:
        raise HTTPException(422, 'Загрузка производства с указанным приростом превысит 100% в пределах 36 месяцев.')
    return cells


def calculate_candidate(candidate, overrides):
    from .native_workbook import recalculate, dependency_issues
    profile = candidate['profile']
    content, calculated = recalculate(candidate['content'], overrides=parameter_overrides(profile, overrides))
    values = calculated['values']
    metrics = [{**item, 'calculated': values.get(item['sheet'], {}).get(item['cell'])}
               for item in profile['metrics']]
    error_count = calculated.get('error_count', len(calculated.get('issues', [])))
    dependency = dependency_issues(calculated, [{'sheet':item['sheet'],'cell':item['cell']} for item in metrics])
    dependent_errors = [item for item in metrics if isinstance(item['calculated'], bool) or
                        not isinstance(item['calculated'], (int, float)) or not math.isfinite(item['calculated'])]
    preview = {'blocked': bool(dependent_errors) or not dependency['complete'] or bool(profile.get('issues')),
               'dependent_errors': [item['key'] for item in dependent_errors],
               'dependency_count': dependency['dependency_count'],
               'formula_count': calculated.get('formula_count', 0), 'error_count': error_count,
               'issues': [{**item, 'message': {'#REF!':'Ссылка на удалённую ячейку. Восстановите формулу в исходном Excel.',
                                             '#DIV/0!':'Деление на ноль. Проверьте знаменатель и исходные данные.',
                                             '#NAME?':'Функция или имя не распознаны. Проверьте исходную формулу.'}.get(
                                                 item.get('code'), item.get('message') or item.get('reason') or item.get('code')),
                           'error': item.get('code')} for item in calculated.get('issues', [])[:100]]}
    from .native_teo import quality_notes
    return content, {**profile, 'overrides': overrides, 'metrics': metrics, 'preview': preview,
                     'quality_notes': quality_notes(values)}, calculated


@router.post('/api/business-projects/{id}/native/preview')
def preview_native(data: NativeIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    files, _, extraction, _, updated_at = snapshot(request, id, company_id, data.revision)
    candidate = native_candidate(files)
    if not candidate:
        raise HTTPException(422, 'В папке не найдена поддерживаемая оригинальная финансовая модель.')
    _, profile, _ = calculate_candidate(candidate, data.overrides)
    with unit(True) as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, data.revision, updated_at)
        project.extraction_json = dump({**extraction, 'native': profile})
        project.status, project.updated_at = 'needs_data', now()
        project.current_fingerprint = ''
        log(s, user, 'Пересчитаны формулы оригинального Excel', 'business_project', id,
            f"Ревизия {data.revision}; ошибок {profile['preview']['error_count']}")
        return detail(s, project, user)


@router.get('/api/business-projects/{id}/native.xlsx')
def download_native(request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0),
                    revision: int = Query(ge=0)):
    files, _, extraction, _, updated_at = snapshot(request, id, company_id, revision)
    candidate = native_candidate(files)
    saved = extraction.get('native', {})
    if not candidate or not saved.get('preview') or saved.get('source_sha256') != candidate['profile']['source_sha256']:
        raise HTTPException(409, 'Сначала выполните пересчёт оригинала и проверьте замечания.')
    content, profile, _ = calculate_candidate(candidate, saved.get('overrides', {}))
    if len(content) > MAX_FILE:
        raise HTTPException(413, 'Пересчитанный файл больше 20 МБ.')
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, revision, updated_at)
    label = 'Проверка_ошибок_' if profile['preview']['blocked'] else 'Пересчитано_'
    name = label + PurePosixPath(candidate['profile']['source_name']).stem[:170] + '.xlsx'
    return Response(content, media_type=MIMES['xlsx'], headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name),
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/api/business-projects/{id}/native/generate')
def generate_native(data: NativeGenerateIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    if data.start.day != 1 or not 2000 <= data.start.year <= 2097:
        raise HTTPException(422, 'Укажите первый день месяца начала трёхлетнего периода архива.')
    files, source_manifest, extraction, title, updated_at = snapshot(request, id, company_id, data.revision)
    candidate = native_candidate(files)
    if not candidate:
        raise HTTPException(422, 'Оригинальная финансовая модель не найдена.')
    workbook, profile, fresh = calculate_candidate(candidate, data.overrides)
    if profile['preview']['blocked']:
        raise HTTPException(422, 'Ошибки исходных формул влияют на итоговый отчёт. Сначала выполните проверку и исправьте указанные ячейки.')
    # A stored report needs the narrative template from the same private folder.
    from .native_documents import patch_business_docx
    from .native_renderer import docx_to_pdf
    from .native_teo import build_teo_pdf
    templates = [item for item in files if PurePosixPath(item['name']).suffix.lower() == '.docx' and
                 any(token in item['name'].lower() for token in ('лекарство_производство', 'бизнес', 'business'))]
    if len(templates) != 1:
        raise HTTPException(422, 'Добавьте в папку один исходный Word с именем «Бизнес-план» или «Лекарство_производство». Он задаёт оформление PDF.')
    inputs = {'title':title,'start':data.start.isoformat(),'months':36,'currency':'USD','native_overrides':data.overrides}
    signature = hashlib.sha256(dump({'native_generator':'1.0','revision':data.revision,'inputs':inputs,
                                    'sources':source_manifest}).encode()).hexdigest()
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user); revision_ok(project, data.revision, updated_at)
        existing = s.scalar(select(BusinessGeneration).where(BusinessGeneration.project_id==id,
                                                            BusinessGeneration.fingerprint==signature))
        if existing:
            if any(get(s, ReportArchive, aid).status == 'deleted' for aid in
                   (existing.business_archive_id, existing.teo_archive_id)):
                raise HTTPException(409, 'Эта версия отчётов удалена. Восстановите её через архив.')
    output = None
    if not existing:
        try:
            teo_pdf = build_teo_pdf(fresh['values'], profile, source_manifest,
                parameters=data.overrides, start_metadata={'confirmed':True,
                    'label':f'Период архива с {data.start.isoformat()}; расчёт по месяцам 1–36'})
            # Validate all monthly TEO dependencies before starting Office.
            word, _ = patch_business_docx(templates[0]['content'], fresh['values'])
            output = {'business_pdf':docx_to_pdf(word),'business_xlsx':workbook,
                      'teo_pdf':teo_pdf,
                      'teo_xlsx':workbook}
        except ValueError as exc:
            message=str(exc)
            if not any('\u0400'<=character<='\u04ff' for character in message):
                message='Word не соответствует исходному образцу. Проверьте таблицы стоимости проекта, безубыточности и сумму кредита; загрузите исходный бизнес-план DOCX.'
            raise HTTPException(422, message) from exc
        if any(len(value)>MAX_FILE for value in output.values()):
            raise HTTPException(413, 'Один из созданных отчётов больше 20 МБ.')
    end = date(data.start.year + 3, data.start.month, 1)
    from datetime import timedelta
    result = {'period_start':data.start.isoformat(),'period_end':(end-timedelta(days=1)).isoformat(),
              'native':True,'metrics':{'npv':fresh['values']['ВНД']['D6'],'irr_annual':fresh['values']['ВНД']['E6']}}
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project,user); revision_ok(project,data.revision,updated_at)
        existing = s.scalar(select(BusinessGeneration).where(BusinessGeneration.project_id==id,
                                                            BusinessGeneration.fingerprint==signature))
        if existing and any(get(s, ReportArchive, aid).status == 'deleted' for aid in
                            (existing.business_archive_id, existing.teo_archive_id)):
            raise HTTPException(409, 'Эта версия отчётов удалена. Восстановите её через архив.')
        if not existing:
            version = s.scalar(select(func.max(ReportArchive.version)).where(ReportArchive.company_id==company.id)) or 0
            ids=[]
            for index,kind in enumerate(('business','teo'),1):
                label = 'Бизнес-план по оригиналу' if kind=='business' else 'ТЭО по оригинальной модели'
                archive = ReportArchive(company_id=company.id,title=f'{title[:120]} · {label}',
                    period_start=data.start,period_end=end-timedelta(days=1),version=version+index,status='ready',
                    request_key=str(uuid5(NAMESPACE_URL,f'native:{company.id}:{id}:{signature}:{kind}')),
                    created_by=user.id,uploaded_at=now())
                s.add(archive);s.flush();ids.append(archive.id)
                for fmt in ('pdf','xlsx'):
                    content=output[f'{kind}_{fmt}']
                    s.add(ReportArchiveFile(company_id=company.id,archive_id=archive.id,format=fmt,
                        filename=f'project-{id}-native-{kind}-r{data.revision}.{fmt}',mime=MIMES[fmt],
                        sha256=hashlib.sha256(content).hexdigest(),size=len(content),content=content))
            s.add(BusinessGeneration(company_id=company.id,project_id=id,revision=data.revision,fingerprint=signature,
                inputs_json=dump(inputs),result_json=dump(result),source_manifest_json=dump(source_manifest),
                business_archive_id=ids[0],teo_archive_id=ids[1],created_by=user.id))
        project.inputs_json=dump(inputs)
        project.extraction_json=dump({**extraction,'native':profile})
        project.status,project.updated_at='ready',now();project.current_fingerprint=signature
        s.flush();log(s,user,'Созданы бизнес-план и ТЭО по оригинальной модели','business_project',id,f'Ревизия {data.revision}')
        return detail(s,project,user)
