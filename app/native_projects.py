"""Saved native-workbook metadata and disabled financial Excel endpoints."""
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
from .security import require_permission
from sqlalchemy import select, func
from uuid import NAMESPACE_URL, uuid5

router = APIRouter()
GENERATOR_VERSION = '1.6'
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
    """Display the last saved extraction; GET never opens source workbooks."""
    from copy import deepcopy
    saved = extraction.get('native')
    if not isinstance(saved, dict) or not saved:
        return None
    return {**deepcopy(saved), 'read_only': True, 'calculation_enabled': False,
            'metadata_source': 'saved_extraction'}


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


def disabled_native(request, id, company_id, revision, download=False):
    # Preserve authorization, tenant ownership and stale-revision boundaries
    # without expanding sources or invoking workbook parsers/recalculation.
    from .business_projects import FINANCIAL_EXCEL_DISABLED
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        if download:
            require_permission(user, 'download')
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, revision)
    raise HTTPException(410, FINANCIAL_EXCEL_DISABLED)


@router.post('/api/business-projects/{id}/native/preview')
def preview_native(data: NativeIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    disabled_native(request, id, company_id, data.revision)


@router.get('/api/business-projects/{id}/native.xlsx')
def download_native(request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0),
                    revision: int = Query(ge=0)):
    disabled_native(request, id, company_id, revision, download=True)


@router.post('/api/business-projects/{id}/native/generate')
def generate_native(data: NativeGenerateIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    disabled_native(request, id, company_id, data.revision)
