"""Private project folders, deterministic calculation and reproducible report pairs.

Uploaded documents are data: they are never executed or allowed to alter ledgers.
The complete folder is analysed after upload. Incomplete/conflicting inputs stay
visible until the owner supplies or confirms the calculation parameters.
"""
import hashlib
import io
import json
import posixpath
import stat
import unicodedata
from datetime import date, timezone
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote, unquote
from uuid import UUID, NAMESPACE_URL, uuid5
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as ET
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select

from .db import (BusinessGeneration, BusinessProject, BusinessSourceFile, ReportArchive,
                 ReportArchiveFile, now, unit)
from .main import get, log
from .report_archives import (authorize, delete_archive_record, restore_archive_record,
                             MIMES, valid_file)
from .security import perms_of
from .business_model import calculate_model, validate_model, validate_narratives, ModelValidationError

router = APIRouter()
MAX_FILE = 20 * 1024 * 1024
MAX_FOLDER = 100 * 1024 * 1024
MAX_FILES = 100
EXTENSIONS = {'.pdf', '.xlsx', '.xltx', '.docx', '.csv', '.txt', '.json', '.zip', '.png', '.jpg', '.jpeg'}
SOURCE_MIMES = {
    '.pdf': MIMES['pdf'], '.xlsx': MIMES['xlsx'],
    '.xltx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.csv': 'text/csv', '.txt': 'text/plain', '.json': 'application/json', '.zip': 'application/zip',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
}
GENERATOR_VERSION = '1.1'


def dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(422, 'Параметры должны содержать обычные конечные числа и текст.') from exc


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    request_key: UUID
    mode: Literal['manual', 'files'] = 'files'
    template_id: int | None = Field(default=None, gt=0)

    @field_validator('title')
    @classmethod
    def title_clean(cls, value):
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('Введите название проекта одной строкой.')
        return value


class AnalyseIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    overrides: dict = Field(default_factory=dict)


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    inputs: dict
    confirm_sources: bool = False


class DraftIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    inputs: dict


def project_mode(extraction):
    return 'manual' if extraction.get('mode') == 'manual' else 'files'


def manual_extraction(files=None):
    if files:
        from .business_sources import extract_sources
        extraction = extract_sources(files)
    else:
        extraction = {'inputs': {}, 'evidence': [], 'issues': [], 'documents': [], 'narratives': []}
    # A manual model supplies its own financial drivers. Missing a financial
    # workbook is expected; all other source conflicts and errors remain visible.
    extraction['issues'] = [
        {**issue, 'requires_confirmation': False,
         'message': 'В приложениях нет расчётной модели. Финансовые данные вводятся пользователем.'}
        if issue.get('code') == 'no_model' else issue for issue in extraction.get('issues', [])]
    return {**extraction, 'mode': 'manual', 'input_origin': 'manual',
            'input_origin_label': 'Введено пользователем', 'sources_pending': False}


def manual_fields(inputs, extraction):
    return [key for key, value in inputs.items()
            if key not in extraction.get('inputs', {}) or value != extraction['inputs'][key]]


def checked_draft(inputs):
    text_issues = validate_narratives(inputs)
    if text_issues:
        raise HTTPException(422, '; '.join(f"{issue['field']}: {issue['message']}" for issue in text_issues))
    encoded = dump(inputs)
    if len(encoded.encode('utf-8')) > 2 * 1024 * 1024:
        raise HTTPException(413, 'Параметры проекта больше 2 МБ; уменьшите детализацию.')
    return encoded


def own(project, user):
    if project.created_by != user.id:
        raise HTTPException(403, 'Изменять исходные файлы и параметры может автор проекта.')


def active(project):
    if project.status == 'deleted':
        raise HTTPException(409, 'Папка проекта удалена. Сначала восстановите её.')


def revision_ok(project, revision, expected_updated_at=None):
    active(project)
    if project.revision != revision:
        raise HTTPException(409, 'Папка проекта изменилась. Обновите её и проверьте новые исходные данные.')
    if expected_updated_at is not None and project.updated_at != expected_updated_at:
        raise HTTPException(409, 'Проект изменён, удалён или восстановлен во время расчёта. Обновите папку проекта.')


def sources(s, project_id):
    return list(s.scalars(select(BusinessSourceFile).where(BusinessSourceFile.project_id == project_id)
                         .order_by(BusinessSourceFile.relative_path)))


def manifest(records):
    return [{'filename': f.filename, 'relative_path': f.relative_path, 'sha256': f.sha256, 'size': f.size}
            for f in records]


def archive_meta(s, id):
    r = get(s, ReportArchive, id)
    return {'id': r.id, 'company_id': r.company_id, 'title': r.title, 'status': r.status,
            'period_start': str(r.period_start), 'period_end': str(r.period_end),
            'version': r.version, 'uploaded_at': timestamp(r.uploaded_at), 'formats': ['pdf', 'xlsx']}


def summary(s, project, user):
    owner = project.created_by == user.id and 'import' in perms_of(user)
    return {'id': project.id, 'company_id': project.company_id, 'title': project.title,
            'mode': project_mode(json.loads(project.extraction_json)),
            'status': project.status, 'revision': project.revision,
            'source_count': s.scalar(select(func.count()).select_from(BusinessSourceFile)
                                     .where(BusinessSourceFile.project_id == project.id)),
            'can_edit': owner and project.status != 'deleted',
            'can_delete': owner and project.status != 'deleted',
            'can_restore': owner and project.status == 'deleted',
            'created_at': timestamp(project.created_at), 'updated_at': timestamp(project.updated_at),
            'latest_report': latest_report(s, project)}


def latest_report(s, project):
    # The project list offers the newest published business plan directly, so a
    # finished PDF/Excel is found without opening the project form. Files are
    # still served by the report-archive route with its own permission checks.
    if project.status == 'deleted':
        return None
    for gen in s.scalars(select(BusinessGeneration).where(BusinessGeneration.project_id == project.id,
                                                          BusinessGeneration.company_id == project.company_id)
                         .order_by(BusinessGeneration.id.desc()).limit(5)):
        business = archive_meta(s, gen.business_archive_id)
        if business['status'] != 'ready' or business['company_id'] != project.company_id:
            continue
        teo = archive_meta(s, gen.teo_archive_id)
        return {'generation_id': gen.id, 'revision': gen.revision, 'created_at': timestamp(gen.created_at),
                'current': gen.fingerprint == project.current_fingerprint and gen.revision == project.revision,
                'business_archive': business,
                'teo_archive': teo if teo['status'] == 'ready' and teo['company_id'] == project.company_id else None}
    return None


def current_source_issues(extraction, inputs):
    # Parser diagnostics describe the original documents. The owner may already
    # have supplied a missing date or number in the form; validate those current
    # values instead of repeating stale missing-input warnings from extraction.
    invalid_start = any(i['field'] == 'start' for i in validate_model(inputs))
    return [i for i in extraction.get('issues', [])
            if i.get('code') != 'missing_or_invalid' and
            not (i.get('code') == 'forecast_start' and not invalid_start)]


def source_issues(extraction, inputs):
    return [{'field': str(i.get('field') or 'sources'),
             'message': str(i.get('message') or 'Проверьте исходный документ.'),
             'code': 'source_issues', 'source_code': str(i.get('code') or 'source_confirmation')}
            for i in current_source_issues(extraction, inputs)
            if i.get('requires_confirmation') or i.get('severity') in ('error', 'blocking')]


def detail(s, project, user):
    result = summary(s, project, user)
    if project.status == 'deleted' and not result['can_restore']:
        raise HTTPException(404, 'Папка проекта не найдена.')
    # Raw input documents are restricted to their author/importer. Published
    # financial results remain available through existing report permissions.
    editable = result['can_edit'] or result['can_restore']
    records = sources(s, project.id)
    result['files'] = [{**m, 'id': f.id, 'uploaded_at': timestamp(f.uploaded_at),
                        **({'download_url': f'/api/business-projects/{project.id}/sources/{f.id}/download?company_id={project.company_id}'}
                           if editable and f.company_id == project.company_id else {})}
                       for f, m in zip(records, manifest(records))]
    result['inputs'] = json.loads(project.inputs_json) if editable else {}
    result['extraction'] = json.loads(project.extraction_json) if editable else {}
    if editable:
        from .native_projects import project_native_model
        result['native_model'] = project_native_model(records, result['extraction'])
    if editable:
        result['extraction']['issues'] = current_source_issues(result['extraction'], result['inputs'])
    result['validation'] = validate_model(result['inputs']) if editable and not result.get('native_model') else []
    if editable and project.status != 'ready':
        result['validation'] += result['extraction'].get('calculation_issues', [])
    result['validation'] += source_issues(result['extraction'], result['inputs']) if editable and project.status != 'ready' else []
    result['generations'] = []
    result['current_generation_id'] = None
    generations = list(s.scalars(select(BusinessGeneration).where(BusinessGeneration.project_id == project.id)
                                .order_by(BusinessGeneration.id.desc()).limit(30)))
    current = s.scalar(select(BusinessGeneration).where(BusinessGeneration.project_id == project.id,
                                                        BusinessGeneration.fingerprint == project.current_fingerprint))
    if current and all(g.id != current.id for g in generations):
        generations.append(current)
    for gen in generations:
        if current and gen.id == current.id:
            result['current_generation_id'] = gen.id
        result['generations'].append({'id': gen.id, 'revision': gen.revision,
                                     'native': bool(json.loads(gen.result_json).get('native')),
                                     'created_at': timestamp(gen.created_at),
                                     'business_archive': archive_meta(s, gen.business_archive_id),
                                     'teo_archive': archive_meta(s, gen.teo_archive_id),
                                     'metrics': json.loads(gen.result_json).get('metrics', {})})
    return result


@router.get('/api/business-projects')
def list_projects(request: Request, company_id: int = Query(gt=0), include_deleted: bool = False):
    with unit() as s:
        user, company = authorize(s, request, 'export', company_id=company_id)
        q = select(BusinessProject).where(BusinessProject.company_id == company.id)
        if include_deleted and 'import' in perms_of(user):
            q = q.where(or_(BusinessProject.status != 'deleted', BusinessProject.created_by == user.id))
        else:
            q = q.where(BusinessProject.status != 'deleted')
        records = s.scalars(q.order_by(BusinessProject.updated_at.desc()))
        return {'items': [summary(s, p, user) for p in records], 'can_upload': 'import' in perms_of(user)}


@router.delete('/api/business-projects/{id}')
def delete_project(request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0),
                   expected_revision: int = Query(ge=0)):
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        if project.revision != expected_revision:
            raise HTTPException(409, 'Папка проекта изменилась. Обновите её перед удалением.')
        if project.status != 'deleted':
            deleted_archives = []
            generations = s.scalars(select(BusinessGeneration).where(
                BusinessGeneration.project_id == id, BusinessGeneration.company_id == company.id))
            for generation in generations:
                for archive_id in (generation.business_archive_id, generation.teo_archive_id):
                    archive = get(s, ReportArchive, archive_id)
                    if delete_archive_record(s, archive, user):
                        deleted_archives.append(archive_id)
            extraction = json.loads(project.extraction_json)
            extraction['_deleted_state'] = {'status': project.status, 'archive_ids': deleted_archives}
            project.extraction_json = dump(extraction)
            project.status, project.updated_at = 'deleted', now()
            log(s, user, 'Удалена папка бизнес-плана', 'business_project', id,
                f'Ревизия {project.revision}; отчётов {len(deleted_archives)}; исходники сохранены')
        s.flush()
        return detail(s, project, user)


@router.post('/api/business-projects/{id}/restore')
def restore_project(request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0),
                    expected_revision: int = Query(ge=0)):
    with unit(True) as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        if project.revision != expected_revision:
            raise HTTPException(409, 'Папка проекта изменилась. Обновите список перед восстановлением.')
        if project.status == 'deleted':
            extraction = json.loads(project.extraction_json)
            deleted_state = extraction.pop('_deleted_state', {})
            prior_status = deleted_state.get('status')
            if prior_status not in ('draft', 'needs_data', 'ready'):
                raise HTTPException(409, 'Не удалось определить прежнее состояние проекта.')
            for archive_id in deleted_state.get('archive_ids', []):
                restore_archive_record(s, get(s, ReportArchive, archive_id), user)
            project.status, project.updated_at = prior_status, now()
            project.extraction_json = dump(extraction)
            log(s, user, 'Восстановлена папка бизнес-плана', 'business_project', id,
                f'Ревизия {project.revision}; состояние {prior_status}')
        s.flush()
        return detail(s, project, user)


@router.post('/api/business-projects')
def create_project(data: ProjectIn, request: Request):
    if data.template_id is not None and data.mode == 'manual':
        raise HTTPException(422, 'Для выбранного оригинального шаблона используйте проект с файлами.')
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=data.company_id)
        project = s.scalar(select(BusinessProject).where(BusinessProject.request_key == str(data.request_key),
                                                        BusinessProject.company_id == company.id))
        if project and (project.title != data.title or project.created_by != user.id or
                        project_mode(json.loads(project.extraction_json)) != data.mode or
                        json.loads(project.extraction_json).get('template_selected', {}).get('id') != data.template_id):
            raise HTTPException(409, 'Ключ создания уже используется другим проектом.')
        if project is None:
            project = BusinessProject(company_id=company.id, title=data.title, request_key=str(data.request_key),
                                      created_by=user.id)
            project.extraction_json = dump(manual_extraction() if data.mode == 'manual' else {'mode': 'files'})
            if data.mode == 'manual':
                project.inputs_json = dump({'title': data.title})
            s.add(project)
            s.flush()
            if data.template_id is not None:
                from .business_templates import use_template
                use_template(s, project, data.template_id, user)
                s.flush()
            log(s, user, 'Создан проект бизнес-плана', 'business_project', project.id)
        return detail(s, project, user)


@router.get('/api/business-projects/template.xlsx')
def input_template(request: Request, company_id: int = Query(gt=0)):
    with unit() as s:
        authorize(s, request, 'import', company_id=company_id)
    from .business_exports import build_input_template
    return Response(build_input_template(), media_type=MIMES['xlsx'], headers={
        'Content-Disposition': 'attachment; filename="business-plan-inputs.xlsx"', 'Cache-Control': 'no-store'})


@router.get('/api/business-projects/{id}')
def project_detail(request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    with unit() as s:
        user, _ = authorize(s, request, 'export', company_id=company_id)
        return detail(s, get(s, BusinessProject, id), user)


@router.get('/api/business-projects/{id}/sources/{source_id}/download')
def download_source(request: Request, id: int = Path(gt=0), source_id: int = Path(gt=0),
                    company_id: int = Query(gt=0)):
    with unit() as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        project = s.get(BusinessProject, id)
        if project is None or project.company_id != company.id:
            raise HTTPException(404, 'Исходный файл не найден в выбранной компании и проекте.')
        source = s.get(BusinessSourceFile, source_id)
        if source is None or source.project_id != project.id or source.company_id != company.id:
            raise HTTPException(404, 'Исходный файл не найден в выбранной компании и проекте.')
        own(project, user)
        # Preserve native Office parts, formulas, cached results and original
        # formatting. No document parser or calculation runs on this path.
        content, filename = bytes(source.content), source.filename
    extension = PurePosixPath(filename).suffix.lower()
    fallback = 'source' + (extension if extension in SOURCE_MIMES else '.bin')
    return Response(content, media_type=SOURCE_MIMES.get(extension, 'application/octet-stream'), headers={
        'Content-Disposition': f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })


def safe_path(raw):
    value = unquote(raw or '').strip()
    parts = value.split('/')
    if (not value or len(value) > 500 or value.startswith('/') or '\\' in value or ':' in value or
            any(p in ('', '.', '..') for p in parts) or
            any(unicodedata.category(c).startswith('C') for c in value) or
            posixpath.normpath(value) != value):
        raise HTTPException(422, 'Укажите путь файла внутри папки проекта без переходов к другим папкам.')
    name = parts[-1]
    if len(name) > 220 or PurePosixPath(name).suffix.lower() not in EXTENSIONS:
        raise HTTPException(422, 'Поддерживаются PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG.')
    return value, name


def checked_zip(raw, office=None):
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            maximum = 2500 if office else MAX_FILES
            if len(items) > maximum or sum(f.file_size for f in items) > MAX_FOLDER:
                raise ValueError('ZIP bounds')
            names = [f.filename for f in items]
            if len(names) != len(set(names)):
                raise ValueError('duplicate')
            for item in items:
                name = item.filename.rstrip('/')
                if (item.flag_bits & 1 or item.file_size > (30*1024*1024 if office else MAX_FILE) or
                        '\\' in name or name.startswith('/') or ':' in name or
                        any(p in ('', '.', '..') for p in name.split('/')) or
                        stat.S_ISLNK(item.external_attr >> 16) or
                        any(unicodedata.category(c).startswith('C') for c in name)):
                    raise ValueError('unsafe ZIP')
                if office and item.filename.endswith(('.xml', '.rels')):
                    ET.fromstring(archive.read(item))
                if not office and not item.is_dir() and PurePosixPath(name).suffix.lower() not in EXTENSIONS - {'.zip'}:
                    raise ValueError('unsupported ZIP member')
            if office:
                if '[Content_Types].xml' not in names or office not in names or any(n.lower().endswith('vbaproject.bin') for n in names):
                    raise ValueError('invalid Office')
    except Exception as exc:
        # Office/ZIP errors include forbidden XML entities and CRC/decompression
        # failures. Do not expose parser details or paths from an uploaded file.
        raise HTTPException(422, 'Некорректный или небезопасный архив. Без макросов, шифрования и вложенных ZIP; до 100 МБ после распаковки.') from exc


def validate_source(raw, filename):
    if not raw:
        raise HTTPException(422, 'Пустой файл.')
    ext = PurePosixPath(filename).suffix.lower()
    if ext in ('.pdf', '.xlsx', '.xltx'):
        valid_file(raw, quote(filename if ext != '.xltx' else filename[:-5]+'.xlsx'), 'pdf' if ext == '.pdf' else 'xlsx')
    elif ext == '.docx':
        checked_zip(raw, 'word/document.xml')
    elif ext == '.zip':
        checked_zip(raw)
    elif ext == '.png' and not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise HTTPException(422, 'Содержимое не соответствует PNG.')
    elif ext in ('.jpg', '.jpeg') and not raw.startswith(b'\xff\xd8\xff'):
        raise HTTPException(422, 'Содержимое не соответствует JPEG.')
    elif ext in ('.json', '.csv', '.txt'):
        if len(raw) > 2*1024*1024:
            raise HTTPException(413, 'Текстовый источник больше 2 МБ.')
        try:
            text = raw.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError('NUL')
            if ext == '.json':
                value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                if not isinstance(value, dict):
                    raise ValueError('object expected')
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise HTTPException(422, 'Нужен текст UTF-8; JSON должен содержать объект с параметрами проекта.') from exc


@router.put('/api/business-projects/{id}/sources')
async def put_source(request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0),
                     expected_revision: int = Query(ge=0)):
    relative_path, filename = safe_path(request.headers.get('X-Relative-Path') or request.headers.get('X-Filename'))
    if unquote(request.headers.get('X-Filename') or filename) != filename:
        raise HTTPException(422, 'Имя файла не совпадает с путём в папке.')
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        active(project)
        updated_at = project.updated_at
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk) > MAX_FILE:
            raise HTTPException(413, 'Файл больше 20 МБ.')
        raw.extend(chunk)
    content = bytes(raw)
    validate_source(content, filename)
    sha = hashlib.sha256(content).hexdigest()
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        active(project)
        if project.updated_at != updated_at:
            raise HTTPException(409, 'Проект изменён, удалён или восстановлен во время загрузки. Обновите папку проекта.')
        current = s.scalar(select(BusinessSourceFile).where(BusinessSourceFile.project_id == id,
                                                            BusinessSourceFile.relative_path == relative_path))
        if current and current.sha256 == sha:
            return detail(s, project, user)
        revision_ok(project, expected_revision)
        records = sources(s, id)
        if len(records) + int(current is None) > MAX_FILES or sum(f.size for f in records) - (current.size if current else 0) + len(content) > MAX_FOLDER:
            raise HTTPException(413, 'Папка ограничена 100 файлами и 100 МБ.')
        if current:
            current.sha256, current.content, current.size, current.uploaded_at = sha, content, len(content), now()
        else:
            s.add(BusinessSourceFile(company_id=company.id, project_id=id, relative_path=relative_path,
                                     filename=filename, sha256=sha, content=content, size=len(content)))
        project.revision += 1
        prior_extraction = json.loads(project.extraction_json)
        selected_template = prior_extraction.get('template_selected')
        if project_mode(prior_extraction) == 'manual':
            extraction = {**manual_extraction(), 'sources_pending': True}
            extraction['manual_fields'] = list(json.loads(project.inputs_json))
            project.extraction_json = dump(extraction)
            project.status = 'needs_data'
        else:
            extraction = {'mode': 'files', 'sources_pending': True}
            if selected_template:
                extraction['template_selected'] = selected_template
            project.status, project.inputs_json, project.extraction_json = 'draft', '{}', dump(extraction)
        project.current_fingerprint = ''
        project.updated_at = now()
        s.flush()
        log(s, user, 'Загружен источник бизнес-плана', 'business_project', id,
            f'Ревизия {project.revision}; {len(content)} байт')
        return detail(s, project, user)


def expanded_sources(records):
    items, total = [], 0
    for record in records:
        if record.filename.lower().endswith('.zip'):
            checked_zip(record.content)
            with ZipFile(io.BytesIO(record.content)) as archive:
                for part in archive.infolist():
                    if part.is_dir():
                        continue
                    total += part.file_size
                    if total > MAX_FOLDER or len(items) >= MAX_FILES:
                        raise HTTPException(413, 'После распаковки в папке больше 100 файлов или 100 МБ.')
                    raw = archive.read(part)
                    validate_source(raw, PurePosixPath(part.filename).name)
                    path = record.relative_path + '/' + part.filename
                    items.append({'name': path, 'path': path, 'content': raw})
        else:
            total += record.size
            if total > MAX_FOLDER or len(items) >= MAX_FILES:
                raise HTTPException(413, 'После распаковки в папке больше 100 файлов или 100 МБ.')
            items.append({'name': record.relative_path, 'path': record.relative_path, 'content': record.content})
    return items


def snapshot(request, id, company_id, revision, allow_manual=False):
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, revision)
        records = sources(s, id)
        extraction = json.loads(project.extraction_json)
        if not records and not (allow_manual and project_mode(extraction) == 'manual'):
            raise HTTPException(422, 'Сначала загрузите исходные файлы в папку проекта.')
        return (expanded_sources(records), manifest(records), extraction,
                project.title, project.updated_at)


@router.put('/api/business-projects/{id}/inputs')
def save_inputs(data: DraftIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    encoded = checked_draft(data.inputs)
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        active(project)
        records, updated_at = sources(s, id), project.updated_at
        files = expanded_sources(records)
        stored_extraction = json.loads(project.extraction_json)
    from .native_projects import native_candidate, require_template_candidate
    candidate = native_candidate(files)
    require_template_candidate(candidate, stored_extraction)
    if candidate:
        raise HTTPException(409, 'В папке есть оригинальная финансовая модель. Используйте её параметры и пересчёт, чтобы сохранить исходную методику.')
    with unit(True) as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        active(project)
        # A retried save of exactly the same draft is harmless and returns the
        # current revision, without invalidating an already prepared report.
        if project.inputs_json == encoded:
            return detail(s, project, user)
        revision_ok(project, data.revision, updated_at)
        extraction = json.loads(project.extraction_json)
        if project_mode(extraction) == 'manual':
            extraction.update({'input_origin': 'manual', 'input_origin_label': 'Введено пользователем'})
        extraction.update({'manual_fields': manual_fields(data.inputs, extraction),
                           'parameters_confirmed': False, 'calculation_issues': []})
        project.inputs_json, project.extraction_json = encoded, dump(extraction)
        project.revision += 1
        project.status, project.updated_at, project.current_fingerprint = 'needs_data', now(), ''
        s.flush()
        log(s, user, 'Сохранён черновик бизнес-плана', 'business_project', id, f'Ревизия {project.revision}')
        return detail(s, project, user)


def persist_generation(request, id, company_id, revision, inputs, extraction, source_manifest, confirmed=False,
                       expected_updated_at=None):
    # The parser describes its incomplete source candidate, while the report
    # describes the current completed model. Keep the original diagnostics for
    # audit, and publish the same active source issues shown in the project UI.
    extraction = {**extraction,
                  'original_issues': extraction.get('original_issues', extraction.get('issues', [])),
                  'issues': current_source_issues(extraction, inputs)}
    # User changes have their own provenance rather than inheriting a source
    # citation whose value was replaced in the calculation form.
    extraction = {**extraction, 'manual_fields': manual_fields(inputs, extraction),
                  'parameters_confirmed': confirmed}
    issues = validate_model(inputs)
    if not confirmed:
        issues += source_issues(extraction, inputs)
    extraction['calculation_issues'] = []
    result = None
    if not issues:
        try:
            result = calculate_model(inputs)
        except ModelValidationError as exc:
            extraction['calculation_issues'] = exc.issues
            issues += exc.issues
    if issues:
        with unit(True) as s:
            user, _ = authorize(s, request, 'import', company_id=company_id)
            project = get(s, BusinessProject, id)
            own(project, user)
            revision_ok(project, revision, expected_updated_at)
            project.inputs_json, project.extraction_json = dump(inputs), dump(extraction)
            project.status, project.updated_at = 'needs_data', now()
            project.current_fingerprint = ''
            return detail(s, project, user)
    signature = hashlib.sha256(dump({'revision': revision, 'inputs': inputs, 'sources': source_manifest,
                                    'generator': GENERATOR_VERSION}).encode()).hexdigest()
    with unit(True) as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, revision, expected_updated_at)
        if s.scalar(select(BusinessGeneration.id).where(BusinessGeneration.project_id == id,
                                                        BusinessGeneration.fingerprint == signature)):
            project.inputs_json, project.extraction_json = dump(inputs), dump(extraction)
            project.status, project.updated_at = 'ready', now()
            project.current_fingerprint = signature
            s.flush()
            return detail(s, project, user)
    # Heavy parsing/rendering never holds the global financial write lock.
    from .business_exports import build_documents
    output = build_documents(inputs, result, source_manifest, extraction)
    for content in output.values():
        if len(content) > MAX_FILE:
            raise HTTPException(413, 'Созданный документ больше 20 МБ; уменьшите детализацию источников.')
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, revision, expected_updated_at)
        existing = s.scalar(select(BusinessGeneration).where(BusinessGeneration.project_id == id,
                                                            BusinessGeneration.fingerprint == signature))
        if not existing:
            version = (s.scalar(select(func.max(ReportArchive.version)).where(ReportArchive.company_id == company.id)) or 0)
            archives = []
            for number, kind in enumerate(('business', 'teo'), 1):
                label = 'Бизнес-план' if kind == 'business' else 'ТЭО'
                archive = ReportArchive(company_id=company.id, title=f'{project.title[:140]} · {label}',
                    period_start=date.fromisoformat(result['period_start']), period_end=date.fromisoformat(result['period_end']),
                    request_key=str(uuid5(NAMESPACE_URL, f'business:{company.id}:{id}:{signature}:{kind}')),
                    version=version+number, status='ready', created_by=user.id, uploaded_at=now())
                s.add(archive)
                s.flush()
                for fmt in ('pdf', 'xlsx'):
                    content = output[f'{kind}_{fmt}']
                    name = f'project-{id}-{kind}-r{revision}.{fmt}'
                    s.add(ReportArchiveFile(company_id=company.id, archive_id=archive.id, format=fmt,
                                           filename=name, mime=MIMES[fmt], sha256=hashlib.sha256(content).hexdigest(),
                                           size=len(content), content=content))
                archives.append(archive.id)
            s.add(BusinessGeneration(company_id=company.id, project_id=id, revision=revision, fingerprint=signature,
                inputs_json=dump(inputs), result_json=dump(result), source_manifest_json=dump(source_manifest),
                business_archive_id=archives[0], teo_archive_id=archives[1], created_by=user.id))
        project.inputs_json, project.extraction_json = dump(inputs), dump(extraction)
        project.status, project.updated_at = 'ready', now()
        project.current_fingerprint = signature
        s.flush()
        log(s, user, 'Рассчитаны бизнес-план и ТЭО', 'business_project', id, f'Ревизия {revision}')
        return detail(s, project, user)


@router.post('/api/business-projects/{id}/analyse')
def analyse_project(data: AnalyseIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    files, source_manifest, stored_extraction, title, updated_at = snapshot(request, id, company_id, data.revision, allow_manual=True)
    mode = project_mode(stored_extraction)
    from .native_projects import native_candidate, require_template_candidate, review_description
    candidate = native_candidate(files)
    require_template_candidate(candidate, stored_extraction)
    if candidate:
        # Keep the supplied workbook's assumptions and layout. Generic defaults
        # cannot replace an established financial model merely because its
        # sources contain similar labels.
        with unit(True) as s:
            user, _ = authorize(s, request, 'import', company_id=company_id)
            project = get(s, BusinessProject, id)
            own(project, user)
            revision_ok(project, data.revision, updated_at)
            profile = {**candidate['profile'], 'input_review': review_description(candidate, files)}
            extraction = {'native': profile, 'issues': [], 'inputs': {}, 'mode': mode}
            if stored_extraction.get('template_selected'):
                extraction['template_selected'] = stored_extraction['template_selected']
            project.extraction_json = dump(extraction)
            if mode != 'manual':
                project.inputs_json = dump({'title': title, **data.overrides})
            project.status, project.updated_at = 'needs_data', now()
            project.current_fingerprint = ''
            return detail(s, project, user)
    from .business_sources import extract_sources
    extraction = manual_extraction(files) if mode == 'manual' else {**extract_sources(files), 'mode': 'files', 'sources_pending': False}
    if mode == 'manual':
        with unit() as s:
            user, _ = authorize(s, request, 'import', company_id=company_id)
            project = get(s, BusinessProject, id)
            own(project, user)
            revision_ok(project, data.revision, updated_at)
            # Attachments provide evidence; they never overwrite the manual form.
            inputs = {**json.loads(project.inputs_json), **data.overrides}
    else:
        inputs = {**extraction.get('inputs', {}), **data.overrides}
    inputs.setdefault('title', title)
    return persist_generation(request, id, company_id, data.revision, inputs, extraction, source_manifest,
                              expected_updated_at=updated_at)


@router.post('/api/business-projects/{id}/generate')
def generate_project(data: GenerateIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    files, source_manifest, extraction, _, updated_at = snapshot(request, id, company_id, data.revision, allow_manual=True)
    from .native_projects import native_candidate, require_template_candidate
    candidate = native_candidate(files)
    require_template_candidate(candidate, extraction)
    if candidate:
        raise HTTPException(409, 'В папке есть оригинальная финансовая модель. Используйте «Пересчитать оригинал и проверить цифры», чтобы сохранить её формулы и оформление.')
    if project_mode(extraction) == 'manual':
        # Re-read optional attachments before publication so newly uploaded
        # source warnings cannot be skipped by directly calling generate.
        extraction = manual_extraction(files)
    elif 'inputs' not in extraction or extraction.get('sources_pending'):
        raise HTTPException(409, 'Сначала проверьте исходные файлы кнопкой анализа папки.')
    return persist_generation(request, id, company_id, data.revision, data.inputs, extraction, source_manifest,
                              confirmed=data.confirm_sources, expected_updated_at=updated_at)
