"""Authorized archive of ready-made report pairs, with separate period and upload date.

The original bytes are retained in the database. This module neither recalculates Excel
nor imports financial facts. A pair is published only when both formats are present.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from urllib.parse import quote, unquote
from uuid import UUID
from zipfile import ZipFile

from defusedxml import ElementTree as ET
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select

from .bot_api import authenticate_bot, linked_user
from .clock import LOCAL_TZ
from .company_scope import SERVICE_CODE, active_delegations, available_companies
from .db import Company, CompanyUser, ReportArchive, ReportArchiveFile, now, unit
from .security import act_with_right, client_ip, perms_of, scope_user, session_user
from .services import get, log

router = APIRouter()
MAX_REPORT_SIZE = 20 * 1024 * 1024
MIMES = {'pdf': 'application/pdf', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
TelegramId = int | None


class ArchiveIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    company_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    period_start: date
    period_end: date
    request_key: UUID

    @model_validator(mode='after')
    def valid_period(self):
        if not (2000 <= self.period_start.year <= self.period_end.year <= 2100):
            raise ValueError('Период должен быть между 2000 и 2100 годами.')
        if self.period_end < self.period_start or (self.period_end - self.period_start).days > 5 * 366:
            raise ValueError('Конец периода должен быть не раньше начала; максимум пять лет.')
        if any(unicodedata.category(c).startswith('C') for c in self.title):
            raise ValueError('Название не должно содержать управляющие символы.')
        return self


def is_bot(request):
    return request.url.path.startswith('/api/bot/v1/')


def bot_user(s, request, telegram_user_id):
    # authenticate_bot uses its own write transaction for key throttling. It must
    # run before the business transaction, never while holding the write guard.
    if not getattr(request.state, 'bot', False):
        raise HTTPException(401, 'Неверный ключ бота.')
    if telegram_user_id is None or not 0 < telegram_user_id < 2**53:
        raise HTTPException(422, 'Укажите telegram_user_id пользователя.')
    user = linked_user(s, telegram_user_id)
    if user is None:
        raise HTTPException(403, 'Привяжите Telegram к активному пользователю сайта.')
    if user.must_change_password:
        raise HTTPException(403, 'Сначала смените временный пароль на сайте.')
    request.state.user_id = user.id
    user._ip = client_ip(request)
    user._forwarded = (request.headers.get('X-Forwarded-For') or '')[:200] or None
    return user


def scope_bot(s, request, user, company_id, permission=None):
    company = next((c for c in available_companies(s, user) if c.id == company_id and c.code != SERVICE_CODE), None)
    if company is None:
        raise HTTPException(403, 'Нет доступа к выбранной компании.')
    membership = s.get(CompanyUser, (company_id, user.id))
    acting = [{'role': d.role, 'replaced_user_id': d.replaced_user_id, 'ends_on': str(d.ends_on)}
              for d in active_delegations(s, user.id, company_id)]
    scope_user(user, membership.role if membership else None, acting)
    if permission and permission not in perms_of(user):
        raise HTTPException(403, 'У вашей роли нет прав на это действие.')
    if permission:
        act_with_right(user, permission)
    s.info['company_id'] = company_id
    request.state.company_id = company_id
    return company


def authorize(s, request, permission, telegram_user_id=None, company_id=None, archive_id=None):
    if is_bot(request):
        user = bot_user(s, request, telegram_user_id)
        if archive_id is not None:
            record = s.get(ReportArchive, archive_id)
            if record is None:
                raise HTTPException(404, 'Отчёт не найден.')
            if company_id is not None and company_id != record.company_id:
                raise HTTPException(404, 'Отчёт не найден в выбранной компании.')
            company_id = record.company_id
        company = scope_bot(s, request, user, company_id, permission)
    else:
        user, _ = session_user(s, request, permission)
        if company_id is not None and company_id != s.info['company_id']:
            raise HTTPException(409, 'Компания отчёта не совпадает с выбранной.')
        company = get(s, Company, s.info['company_id'])
        if company.code == SERVICE_CODE:
            raise HTTPException(403, 'Выберите действующую компанию.')
    return user, company


def record_json(s, record, company, user):
    formats = list(s.scalars(select(ReportArchiveFile.format).where(ReportArchiveFile.archive_id == record.id)))
    return {'id': record.id, 'title': record.title, 'company_id': company.id,
            'company_code': company.code, 'company_name': company.name,
            'period_start': str(record.period_start), 'period_end': str(record.period_end),
            'uploaded_at': record.uploaded_at.replace(tzinfo=timezone.utc).isoformat(),
            'formats': [f for f in ('pdf', 'xlsx') if f in formats], 'status': record.status,
            'version': record.version,
            'can_upload': record.status == 'draft' and record.created_by == user.id and 'import' in perms_of(user)}


@router.get('/api/bot/v1/report-archive-companies')
def bot_companies(request: Request, telegram_user_id: int = Query(gt=0, lt=2**53)):
    authenticate_bot(request)
    with unit() as s:
        user = linked_user(s, telegram_user_id)
        if user is None:
            return {'linked': False, 'items': []}
        if user.must_change_password:
            raise HTTPException(403, 'Сначала смените временный пароль на сайте.')
        items = []
        # Each candidate is checked before a session is scoped; there is no holding-wide file access.
        for company in available_companies(s, user):
            if company.code == SERVICE_CODE:
                continue
            scope_bot(s, request, user, company.id)
            if 'export' in perms_of(user):
                items.append({'id': company.id, 'code': company.code, 'name': company.name,
                              'can_upload': 'import' in perms_of(user)})
            s.info.pop('company_id', None)
        return {'linked': True, 'items': items}


@router.get('/api/report-archives')
@router.get('/api/bot/v1/report-archives')
def list_archives(request: Request, company_id: int = Query(gt=0), date_from: date | None = None,
                  date_to: date | None = None, uploaded_on: date | None = None, include_drafts: bool = False,
                  telegram_user_id: TelegramId = Query(default=None, gt=0, lt=2**53)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, 'Начало фильтра должно быть не позже конца.')
    if any(d is not None and not 2000 <= d.year <= 2100 for d in (date_from, date_to, uploaded_on)):
        raise HTTPException(422, 'Даты фильтра должны быть между 2000 и 2100 годами.')
    if is_bot(request):
        authenticate_bot(request)
    with unit() as s:
        user, company = authorize(s, request, 'export', telegram_user_id, company_id)
        q = select(ReportArchive).where(ReportArchive.company_id == company.id)
        can_upload = 'import' in perms_of(user)
        if can_upload and (not is_bot(request) or include_drafts):
            q = q.where(or_(ReportArchive.status == 'ready', ReportArchive.created_by == user.id))
        else:
            q = q.where(ReportArchive.status == 'ready')
        if date_from:
            q = q.where(ReportArchive.period_end >= date_from)
        if date_to:
            q = q.where(ReportArchive.period_start <= date_to)
        if uploaded_on:
            start = datetime.combine(uploaded_on, time.min, LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
            q = q.where(ReportArchive.uploaded_at >= start, ReportArchive.uploaded_at < start + timedelta(days=1))
        return {'items': [record_json(s, r, company, user) for r in s.scalars(q.order_by(ReportArchive.version.desc()))],
                'can_upload': can_upload}


@router.post('/api/report-archives')
@router.post('/api/bot/v1/report-archives')
def create_archive(data: ArchiveIn, request: Request,
                   telegram_user_id: TelegramId = Query(default=None, gt=0, lt=2**53)):
    if is_bot(request):
        authenticate_bot(request)
    with unit(True) as s:
        user, company = authorize(s, request, 'import', telegram_user_id, data.company_id)
        record = s.scalar(select(ReportArchive).where(ReportArchive.company_id == company.id,
                                                     ReportArchive.request_key == str(data.request_key)))
        if record:
            if (record.created_by != user.id or record.title != data.title or
                    record.period_start != data.period_start or record.period_end != data.period_end):
                raise HTTPException(409, 'Ключ загрузки уже использован другим отчётом.')
        else:
            version = (s.scalar(select(func.max(ReportArchive.version)).where(ReportArchive.company_id == company.id)) or 0) + 1
            record = ReportArchive(company_id=company.id, title=data.title, period_start=data.period_start,
                                   period_end=data.period_end, request_key=str(data.request_key), version=version,
                                   created_by=user.id)
            s.add(record)
            s.flush()
            log(s, user, 'Создан черновик отчёта', 'report_archive', record.id, f'Версия {version}')
        return record_json(s, record, company, user)


def valid_file(raw, filename, format):
    filename = unquote(filename or '').strip()
    if (not filename or len(filename) > 220 or '/' in filename or '\\' in filename or ':' in filename or
            any(unicodedata.category(c).startswith('C') for c in filename) or filename in ('.', '..')):
        raise HTTPException(422, 'Укажите безопасное имя файла без пути.')
    if not filename.lower().endswith('.' + format):
        raise HTTPException(422, f'Нужен файл .{format}.')
    if not raw:
        raise HTTPException(422, 'Пустой файл.')
    if format == 'pdf':
        if not raw.startswith(b'%PDF-'):
            raise HTTPException(422, 'Содержимое не соответствует PDF.')
    else:
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                parts = archive.infolist()
                if len(parts) > 2500 or sum(p.file_size for p in parts) > 100 * 1024 * 1024:
                    raise ValueError('Слишком большой распакованный XLSX.')
                names = [p.filename for p in parts]
                if len(names) != len(set(names)):
                    raise ValueError('Повторяющиеся части XLSX.')
                for p in parts:
                    if (p.flag_bits & 1 or p.file_size > 30 * 1024 * 1024 or '\\' in p.filename or
                            p.filename.startswith('/') or ':' in p.filename or
                            '..' in p.filename.split('/') or posixpath.normpath(p.filename).startswith('../')):
                        raise ValueError('Небезопасная структура XLSX.')
                    if p.filename.endswith(('.xml', '.rels')):
                        ET.fromstring(archive.read(p))
                if not {'[Content_Types].xml', 'xl/workbook.xml'} <= set(names):
                    raise ValueError('Не найден документ Excel.')
                if any(name.lower().endswith('vbaproject.bin') for name in names):
                    raise ValueError('Нужен XLSX без макросов.')
            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            try:
                if not book.worksheets:
                    raise ValueError('В книге нет листов.')
            finally:
                book.close()
        except Exception as exc:
            # Do not return parsing errors containing user-provided document text.
            raise HTTPException(422, 'Нужен корректный, не зашифрованный XLSX без макросов (до 100 МБ после распаковки).') from exc
    return filename


def own_draft(record, user):
    if record.created_by != user.id:
        raise HTTPException(403, 'Завершать загрузку может только автор черновика.')


@router.put('/api/report-archives/{id}/files/{format}')
@router.put('/api/bot/v1/report-archives/{id}/files/{format}')
async def upload_file(request: Request, format: Literal['pdf', 'xlsx'], id: int = Path(gt=0),
                      company_id: int = Query(gt=0),
                      telegram_user_id: TelegramId = Query(default=None, gt=0, lt=2**53)):
    # Authenticate before reading bytes, then repeat inside the write transaction after streaming.
    if is_bot(request):
        authenticate_bot(request)
    with unit() as s:
        user, _ = authorize(s, request, 'import', telegram_user_id, company_id, archive_id=id)
        own_draft(get(s, ReportArchive, id), user)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REPORT_SIZE:
            raise HTTPException(413, 'Файл больше 20 МБ.')
        raw.extend(chunk)
    content = bytes(raw)
    filename = valid_file(content, request.headers.get('X-Filename'), format)
    sha = hashlib.sha256(content).hexdigest()
    with unit(True) as s:
        user, company = authorize(s, request, 'import', telegram_user_id, company_id, archive_id=id)
        record = get(s, ReportArchive, id)
        own_draft(record, user)
        existing = s.scalar(select(ReportArchiveFile).where(ReportArchiveFile.archive_id == id, ReportArchiveFile.format == format))
        if existing and existing.sha256 == sha:
            return record_json(s, record, company, user)
        if record.status == 'ready':
            raise HTTPException(409, 'Готовая версия не изменяется. Создайте новую загрузку.')
        if existing:
            existing.filename, existing.sha256, existing.size, existing.content = filename, sha, len(content), content
        else:
            s.add(ReportArchiveFile(company_id=company.id, archive_id=id, format=format, filename=filename,
                                    mime=MIMES[format], sha256=sha, size=len(content), content=content))
        s.flush()
        formats = set(s.scalars(select(ReportArchiveFile.format).where(ReportArchiveFile.archive_id == id)))
        if formats == {'pdf', 'xlsx'}:
            record.status = 'ready'
            record.uploaded_at = now()
        log(s, user, 'Загружен файл отчёта', 'report_archive', id,
            f'Версия {record.version}; {format.upper()}; {len(content)} байт; статус {record.status}')
        return record_json(s, record, company, user)


@router.get('/api/report-archives/{id}/files/{format}')
@router.get('/api/bot/v1/report-archives/{id}/files/{format}')
def download_file(request: Request, format: Literal['pdf', 'xlsx'], id: int = Path(gt=0),
                  company_id: int = Query(gt=0),
                  telegram_user_id: TelegramId = Query(default=None, gt=0, lt=2**53)):
    if is_bot(request):
        authenticate_bot(request)
    with unit() as s:
        user, _ = authorize(s, request, 'export', telegram_user_id, company_id, archive_id=id)
        record = get(s, ReportArchive, id)
        if record.status != 'ready' and (is_bot(request) or record.created_by != user.id):
            raise HTTPException(404, 'Готовый отчёт не найден.')
        file = s.scalar(select(ReportArchiveFile).where(ReportArchiveFile.archive_id == id, ReportArchiveFile.format == format))
        if file is None:
            raise HTTPException(404, 'Файл ещё не загружен.')
        content, filename = file.content, file.filename
        log(s, user, 'Скачан файл отчёта', 'report_archive', id, f'Версия {record.version}; {format.upper()}')
    # No public storage URL, Telegram file id, or filesystem path can bypass the next authorization.
    return Response(content, media_type=MIMES[format], headers={
        'Content-Disposition': f'attachment; filename="report-{id}.{format}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
        'Cache-Control': 'no-store',
    })
