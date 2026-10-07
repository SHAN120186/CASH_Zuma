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
from urllib.parse import quote, unquote
from uuid import UUID, NAMESPACE_URL, uuid5
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as ET
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from .db import (BusinessGeneration, BusinessProject, BusinessSourceFile, ReportArchive,
                 ReportArchiveFile, now, unit)
from .main import get, log
from .report_archives import authorize, MIMES, valid_file
from .security import perms_of
from .business_model import calculate_model, validate_model, ModelValidationError

router = APIRouter()
MAX_FILE = 20 * 1024 * 1024
MAX_FOLDER = 100 * 1024 * 1024
MAX_FILES = 100
EXTENSIONS = {'.pdf', '.xlsx', '.xltx', '.docx', '.csv', '.txt', '.json', '.zip', '.png', '.jpg', '.jpeg'}
GENERATOR_VERSION = '1.0'


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


def own(project, user):
    if project.created_by != user.id:
        raise HTTPException(403, 'Изменять исходные файлы и параметры может автор проекта.')


def revision_ok(project, revision):
    if project.revision != revision:
        raise HTTPException(409, 'Папка проекта изменилась. Обновите её и проверьте новые исходные данные.')


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
    return {'id': project.id, 'company_id': project.company_id, 'title': project.title,
            'status': project.status, 'revision': project.revision,
            'source_count': s.scalar(select(func.count()).select_from(BusinessSourceFile)
                                     .where(BusinessSourceFile.project_id == project.id)),
            'can_edit': project.created_by == user.id and 'import' in perms_of(user),
            'created_at': timestamp(project.created_at), 'updated_at': timestamp(project.updated_at)}


def source_issues(extraction):
    return [{'field': str(i.get('field') or 'sources'), 'message': str(i.get('message') or 'Проверьте исходный документ.')}
            for i in extraction.get('issues', [])
            if i.get('code') != 'missing_or_invalid' and
            (i.get('requires_confirmation') or i.get('severity') in ('error', 'blocking'))]


def detail(s, project, user):
    result = summary(s, project, user)
    result['files'] = [{**m, 'id': f.id, 'uploaded_at': timestamp(f.uploaded_at)}
                       for f, m in zip(sources(s, project.id), manifest(sources(s, project.id)))]
    # Raw input documents are restricted to their author/importer. Published
    # financial results remain available through existing report permissions.
    editable = result['can_edit']
    result['inputs'] = json.loads(project.inputs_json) if editable else {}
    result['extraction'] = json.loads(project.extraction_json) if editable else {}
    result['validation'] = validate_model(result['inputs']) if editable else []
    if editable and project.status != 'ready':
        result['validation'] += result['extraction'].get('calculation_issues', [])
    result['validation'] += source_issues(result['extraction']) if editable and project.status != 'ready' else []
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
                                     'created_at': timestamp(gen.created_at),
                                     'business_archive': archive_meta(s, gen.business_archive_id),
                                     'teo_archive': archive_meta(s, gen.teo_archive_id),
                                     'metrics': json.loads(gen.result_json).get('metrics', {})})
    return result


@router.get('/api/business-projects')
def list_projects(request: Request, company_id: int = Query(gt=0)):
    with unit() as s:
        user, company = authorize(s, request, 'export', company_id=company_id)
        records = s.scalars(select(BusinessProject).where(BusinessProject.company_id == company.id)
                            .order_by(BusinessProject.updated_at.desc()))
        return {'items': [summary(s, p, user) for p in records], 'can_upload': 'import' in perms_of(user)}


@router.post('/api/business-projects')
def create_project(data: ProjectIn, request: Request):
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=data.company_id)
        project = s.scalar(select(BusinessProject).where(BusinessProject.request_key == str(data.request_key),
                                                        BusinessProject.company_id == company.id))
        if project and (project.title != data.title or project.created_by != user.id):
            raise HTTPException(409, 'Ключ создания уже используется другим проектом.')
        if project is None:
            project = BusinessProject(company_id=company.id, title=data.title, request_key=str(data.request_key),
                                      created_by=user.id)
            s.add(project)
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
        own(get(s, BusinessProject, id), user)
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
        project.status, project.inputs_json, project.extraction_json = 'draft', '{}', '{}'
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


def snapshot(request, id, company_id, revision):
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, revision)
        records = sources(s, id)
        if not records:
            raise HTTPException(422, 'Сначала загрузите исходные файлы в папку проекта.')
        return expanded_sources(records), manifest(records), json.loads(project.extraction_json), project.title


def persist_generation(request, id, company_id, revision, inputs, extraction, source_manifest, confirmed=False):
    # User changes have their own provenance rather than inheriting a source
    # citation whose value was replaced in the calculation form.
    extraction = {**extraction, 'manual_fields': [key for key, value in inputs.items()
                   if key not in extraction.get('inputs', {}) or value != extraction['inputs'][key]],
                  'parameters_confirmed': confirmed}
    issues = validate_model(inputs)
    if not confirmed:
        issues += source_issues(extraction)
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
            revision_ok(project, revision)
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
        revision_ok(project, revision)
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
        revision_ok(project, revision)
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
    files, source_manifest, _, title = snapshot(request, id, company_id, data.revision)
    from .business_sources import extract_sources
    extraction = extract_sources(files)
    inputs = {**extraction.get('inputs', {}), **data.overrides}
    inputs.setdefault('title', title)
    return persist_generation(request, id, company_id, data.revision, inputs, extraction, source_manifest)


@router.post('/api/business-projects/{id}/generate')
def generate_project(data: GenerateIn, request: Request, id: int = Path(gt=0), company_id: int = Query(gt=0)):
    _, source_manifest, extraction, _ = snapshot(request, id, company_id, data.revision)
    if not extraction:
        raise HTTPException(409, 'Сначала проверьте исходные файлы кнопкой анализа папки.')
    return persist_generation(request, id, company_id, data.revision, data.inputs, extraction, source_manifest,
                              confirmed=data.confirm_sources)
