"""Private recalculation of uploaded native workbooks, with explicit error evidence."""
import hashlib
import math
from datetime import date
from pathlib import PurePosixPath
from typing import Literal
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
GENERATOR_VERSION = '1.4'
WORD_COVERAGE_NOTE = ('В Word обновляются расчётные таблицы, НДС, численность и фразы по проверенным связям с Excel. '
                      'При расхождении финансовых цифр Word и Excel новый PDF использует пересчитанный Excel. '
                      'Описательные разделы, логотипы и сертификаты сохраняются из шаблона; '
                      'при новом проекте проверьте их актуальность.')


class InputDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    choice: Literal['keep', 'source', 'manual']
    proposal_id: str | None = Field(default=None, min_length=1, max_length=128)


class NativeIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    overrides: dict = Field(default_factory=dict, max_length=30)
    data_updates: dict = Field(default_factory=dict, max_length=500)
    review_decisions: dict[str, InputDecision] = Field(default_factory=dict, max_length=500)
    confirm_source_basis: bool = Field(default=False, strict=True)
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class NativeGenerateIn(NativeIn):
    start: date


def native_candidate(files):
    from .native_uzgermed import describe_profile
    candidates = []
    seen = set()
    for item in files:
        if PurePosixPath(item['name']).suffix.lower() not in ('.xlsx', '.xltx'):
            continue
        profile = describe_profile(item['content'])
        if not profile.get('supported'):
            continue
        digest = hashlib.sha256(item['content']).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        profile = {**profile, 'source_name': item['name'], 'source_sha256': digest}
        candidates.append({'profile': profile, 'content': item['content']})
    if len(candidates) > 1:
        raise HTTPException(422, 'В папке несколько оригинальных финансовых моделей. Создайте отдельную папку для каждой модели, чтобы не смешивать их цифры.')
    return candidates[0] if candidates else None


def require_template_candidate(candidate, extraction):
    if extraction.get('template_selected') and not candidate:
        raise HTTPException(422, 'Оригинальный Excel выбранного шаблона не найден или изменена его структура. '
                                 'Добавьте исходную книгу шаблона. Универсальный расчёт не заменяет выбранный формат.')


def _finite_number(value):
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value))
    except (OverflowError, ValueError):
        return False


def _requires_decision(item):
    from .native_template_inputs import numbers_equivalent
    proposals = item.get('proposals', [])
    return bool(item.get('editable') and item.get('required') and item.get('value') is None or
                item.get('requires_decision') or len(proposals) > 1 or
                any(not numbers_equivalent(proposal.get('value'), item.get('value')) for proposal in proposals))


def review_description(candidate, files, saved=None):
    """Source proposals are evidence only; saved decisions never select a new file."""
    from .native_template_inputs import describe_inputs
    raw = describe_inputs(candidate['content'], files)
    saved = saved or {}
    updates, decisions = saved.get('data_updates', {}), saved.get('review_decisions', {})
    overrides = saved.get('overrides', {})
    items = [{**item, 'original_value': item.get('value'),
              'value': updates.get(item['key'], overrides.get(item['key'], item.get('value'))),
              'requires_decision': _requires_decision(item),
              'decision': decisions.get(item['key'])} for item in raw.get('items', [])]
    basis_required = any(
        decisions.get(item['key'], {}).get('choice') == 'source' and
        any(proposal.get('id') == decisions[item['key']].get('proposal_id') and
            proposal.get('basis_confirmation') for proposal in item.get('proposals', []))
        for item in items)
    return {**raw, 'items': items, 'decisions': decisions,
            'source_basis_confirmation_required': basis_required,
            'complete': all(not item['requires_decision'] or item['key'] in decisions for item in items) and
                        (not basis_required or saved.get('confirm_source_basis', False))}


def prepare_candidate(candidate, files, data):
    """Validate every explicit choice against this source revision before recalculation."""
    from .native_template_inputs import describe_inputs, apply_updates, numbers_equivalent
    if data.source_sha256 is not None and data.source_sha256 != candidate['profile']['source_sha256']:
        raise HTTPException(409, 'Исходный Excel изменился. Обновите проект и проверьте предложения документов заново.')
    description = describe_inputs(candidate['content'], files)
    items = {item['key']: item for item in description.get('items', [])}
    decisions = {key: value.model_dump(exclude_none=True) for key, value in data.review_decisions.items()}
    if set(data.data_updates) - items.keys() or set(decisions) - items.keys():
        raise HTTPException(422, 'Можно менять и подтверждать только показанные входные данные выбранного шаблона.')
    for key, value in data.data_updates.items():
        item = items[key]
        if not item.get('editable', False) or not _finite_number(value):
            raise HTTPException(422, f"{item.get('label', key)}: укажите конечное число для доступного входного поля.")
        if item.get('min') is not None and value < item['min'] or item.get('max') is not None and value > item['max']:
            raise HTTPException(422, f"{item.get('label', key)}: значение вне допустимого диапазона.")
        if key in data.overrides and data.overrides[key] != value:
            raise HTTPException(422, f"{item.get('label', key)}: два разных значения одного входного поля.")
    effective = {key: value for key, value in data.overrides.items() if key in items}
    effective.update(data.data_updates)
    required = {key for key, item in items.items() if _requires_decision(item)} | set(data.data_updates)
    unresolved = required - decisions.keys()
    if unresolved:
        labels = [items[key].get('label', key) for key in sorted(unresolved)]
        raise HTTPException(422, 'Проверьте данные источников и выберите решение: ' + ', '.join(labels[:20]) + '.')
    for key, decision in decisions.items():
        item, choice = items[key], decision['choice']
        value = effective.get(key, item.get('value'))
        if choice != 'keep' and not item.get('editable', False):
            raise HTTPException(422, f"{item.get('label', key)}: входное поле недоступно для изменения.")
        if choice != 'source' and 'proposal_id' in decision:
            raise HTTPException(422, f"{item.get('label', key)}: источник выбирается только для решения «Из документа».")
        if choice == 'keep':
            if item.get('required') and item.get('value') is None:
                raise HTTPException(422, f"{item.get('label', key)}: пустое исходное поле нужно заполнить; оно не означает ноль.")
            if value != item.get('value'):
                raise HTTPException(422, f"{item.get('label', key)}: решение «Оставить шаблон» не допускает другого значения.")
        elif choice == 'source':
            proposal = next((entry for entry in item.get('proposals', [])
                             if entry.get('id') == decision.get('proposal_id')), None)
            if not proposal or not _finite_number(proposal.get('value')) or key not in effective or not numbers_equivalent(value, proposal['value']):
                raise HTTPException(422, f"{item.get('label', key)}: выберите предложение текущего документа и его числовое значение.")
            if proposal.get('basis_confirmation') and not data.confirm_source_basis:
                raise HTTPException(422, f"{item.get('label', key)}: подтвердите валюту, единицы, период и базу цен выбранных документов.")
        elif key not in effective or not _finite_number(value):
            raise HTTPException(422, f"{item.get('label', key)}: для ручного решения укажите числовое значение.")
    # Original unused staffing rows remain blank. A newly selected role needs
    # both drivers, rather than interpreting a missing salary or count as zero.
    for key, decision in decisions.items():
        item = items[key]
        if (decision['choice'] == 'keep' or item.get('group') != 'Персонал' or
                item.get('sheet') != 'Труд' or item.get('cell', '')[:1] not in ('B', 'C')):
            continue
        paired_key = 'Труд!' + ('C' if item['cell'].startswith('B') else 'B') + item['cell'][1:]
        paired = items.get(paired_key)
        if paired is not None and not _finite_number(effective.get(paired_key, paired.get('value'))):
            raise HTTPException(422, f"{paired.get('label', paired_key)}: заполните численность и зарплату выбранной должности; пустое поле не означает ноль.")
    try:
        content = apply_updates(candidate['content'], data.data_updates)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, 'Не удалось применить входные данные: ' + str(exc)) from exc
    from .native_uzgermed import describe_profile
    profile = {**describe_profile(content), 'source_name': candidate['profile']['source_name'],
               'source_sha256': candidate['profile']['source_sha256']}
    if not profile.get('supported'):
        raise HTTPException(422, 'Изменённые данные не соответствуют структуре выбранного оригинала.')
    parameter_overrides(profile, data.overrides)
    scalar_overrides = {key: value for key, value in data.overrides.items() if key not in data.data_updates}
    audit = {'data_updates': data.data_updates, 'review_decisions': decisions, 'overrides': scalar_overrides,
             'confirm_source_basis': data.confirm_source_basis}
    audit['input_review'] = review_description(candidate, files, audit)
    return {'profile': profile, 'content': content}, audit


def project_native_model(records, extraction):
    files = expanded_sources(records)
    candidate = native_candidate(files)
    require_template_candidate(candidate, extraction)
    if not candidate:
        return None
    profile = candidate['profile']
    saved = extraction.get('native', {})
    if saved.get('source_sha256') == profile['source_sha256']:
        profile = {**profile, 'overrides': saved.get('overrides', {}), 'preview': saved.get('preview'),
                   'quality_notes': saved.get('quality_notes', [])}
        calculated = {item['key']: item.get('calculated') for item in saved.get('metrics', [])}
        profile['metrics'] = [{**item, 'calculated': calculated.get(item['key'])} for item in profile['metrics']]
    else:
        saved = {}
    scalar_keys = {item['key'] for item in profile['parameters']}
    profile['overrides'] = {**saved.get('overrides', {}),
                            **{key: value for key, value in saved.get('data_updates', {}).items() if key in scalar_keys}}
    profile.update(data_updates=saved.get('data_updates', {}), review_decisions=saved.get('review_decisions', {}),
                   confirm_source_basis=saved.get('confirm_source_basis', False),
                   input_review=review_description(candidate, files, saved))
    profile['quality_notes'] = list(dict.fromkeys([*profile.get('quality_notes', []), WORD_COVERAGE_NOTE]))
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
        if not _finite_number(value):
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
    require_template_candidate(candidate, extraction)
    if not candidate:
        raise HTTPException(422, 'В папке не найдена поддерживаемая оригинальная финансовая модель.')
    prepared, audit = prepare_candidate(candidate, files, data)
    _, profile, _ = calculate_candidate(prepared, audit['overrides'])
    profile.update(audit)
    profile['quality_notes'] = list(dict.fromkeys([*profile.get('quality_notes', []), WORD_COVERAGE_NOTE]))
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
    data = NativeIn(revision=revision, overrides=saved.get('overrides', {}),
                    data_updates=saved.get('data_updates', {}), review_decisions=saved.get('review_decisions', {}),
                    confirm_source_basis=saved.get('confirm_source_basis', False),
                    source_sha256=saved.get('source_sha256'))
    prepared, audit = prepare_candidate(candidate, files, data)
    content, profile, _ = calculate_candidate(prepared, audit['overrides'])
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
    require_template_candidate(candidate, extraction)
    if not candidate:
        raise HTTPException(422, 'Оригинальная финансовая модель не найдена.')
    prepared, audit = prepare_candidate(candidate, files, data)
    workbook, profile, fresh = calculate_candidate(prepared, audit['overrides'])
    profile.update(audit)
    profile['quality_notes'] = list(dict.fromkeys([*profile.get('quality_notes', []), WORD_COVERAGE_NOTE]))
    if profile['preview']['blocked']:
        raise HTTPException(422, 'Ошибки исходных формул влияют на итоговый отчёт. Сначала выполните проверку и исправьте указанные ячейки.')
    # A stored report needs the narrative template from the same private folder.
    from .native_documents import patch_business_docx
    from .native_renderer import docx_to_pdf
    from .native_teo import build_teo_pdf
    templates = list({hashlib.sha256(item['content']).hexdigest(): item for item in files
                      if PurePosixPath(item['name']).suffix.lower() == '.docx' and
                      any(token in PurePosixPath(item['name']).name.lower()
                          for token in ('лекарство_производство', 'бизнес', 'business'))}.values())
    if len(templates) != 1:
        raise HTTPException(422, 'Добавьте в папку один исходный Word с именем «Бизнес-план» или «Лекарство_производство». Он задаёт оформление PDF.')
    inputs = {'title':title,'start':data.start.isoformat(),'months':36,'currency':'USD',
              'native_overrides':audit['overrides'], 'native_data_updates':data.data_updates,
              'native_review_decisions':audit['review_decisions'],
              'native_confirm_source_basis':audit['confirm_source_basis']}
    signature = hashlib.sha256(dump({'native_generator':GENERATOR_VERSION,'revision':data.revision,'inputs':inputs,
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
                parameters=data.overrides, start_metadata={'kind':'archive_period',
                    'label':f'Период архива с {data.start.isoformat()}; расчёт по месяцам 1–36'})
            # Validate all monthly TEO dependencies before starting Office.
            word, _ = patch_business_docx(templates[0]['content'], fresh['values'], profile_id=profile['profile_id'])
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
              'native':True,'native_generator':GENERATOR_VERSION,
              'metrics':{'npv':fresh['values']['ВНД']['D6'],'irr_annual':fresh['values']['ВНД']['E6']}}
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
