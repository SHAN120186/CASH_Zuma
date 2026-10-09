"""Company-scoped, author-private reusable copies of the original report pair.

Templates are uploaded documents, never public application assets. Saving a
template cannot modify a project or replace a report; each new project gets
its own immutable starting copies and its own subsequent input revisions.
"""
import hashlib
import json
import unicodedata
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from .db import BusinessProject, BusinessSourceFile, BusinessTemplate, now, unit
from .business_projects import (dump, expanded_sources, own, revision_ok,
                                sources, timestamp, validate_source)
from .main import get, log
from .report_archives import authorize
from .native_projects import native_candidate

router = APIRouter()


class SaveTemplateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    request_key: UUID

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('Введите название шаблона одной строкой.')
        return value


def template_meta(record):
    return {'id': record.id, 'title': record.title,
            'xlsx_name': record.xlsx_name, 'docx_name': record.docx_name,
            'created_at': timestamp(record.created_at)}


@router.get('/api/business-templates')
def list_templates(request: Request, company_id: int = Query(gt=0)):
    with unit() as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        records = s.scalars(select(BusinessTemplate).where(
            BusinessTemplate.company_id == company.id,
            BusinessTemplate.created_by == user.id).order_by(
                BusinessTemplate.created_at.desc(), BusinessTemplate.id.desc()))
        return {'items': [template_meta(record) for record in records], 'can_upload': True}


@router.post('/api/business-projects/{id}/save-template')
def save_template(data: SaveTemplateIn, request: Request, id: int = Path(gt=0),
                  company_id: int = Query(gt=0)):
    with unit(True) as s:
        user, company = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, data.revision)
        files = expanded_sources(sources(s, project.id))
        candidate = native_candidate(files)
        if not candidate:
            raise HTTPException(422, 'Добавьте оригинальный Excel, чтобы сохранить шаблон отчёта.')
        words = [item for item in files if PurePosixPath(item['name']).suffix.lower() == '.docx'
                 and any(token in PurePosixPath(item['name']).name.lower() for token in
                         ('лекарство_производство', 'бизнес', 'business'))]
        words = list({hashlib.sha256(item['content']).hexdigest(): item for item in words}.values())
        if len(words) != 1:
            raise HTTPException(422, 'Для шаблона нужны один оригинальный Excel и один Word бизнес-плана.')
        xlsx_name = PurePosixPath(candidate['profile']['source_name']).name
        docx_name = PurePosixPath(words[0]['name']).name
        workbook, word = candidate['content'], words[0]['content']
        validate_source(workbook, xlsx_name)
        validate_source(word, docx_name)
        xlsx_hash = hashlib.sha256(workbook).hexdigest()
        docx_hash = hashlib.sha256(word).hexdigest()
        existing = s.scalar(select(BusinessTemplate).where(
            BusinessTemplate.company_id == company.id,
            BusinessTemplate.request_key == str(data.request_key)))
        if existing:
            if (existing.created_by != user.id or existing.title != data.title or
                    existing.xlsx_sha256 != xlsx_hash or existing.docx_sha256 != docx_hash):
                raise HTTPException(409, 'Ключ сохранения уже используется другим шаблоном.')
            return template_meta(existing)
        count = s.scalar(select(func.count()).select_from(BusinessTemplate).where(
            BusinessTemplate.company_id == company.id, BusinessTemplate.created_by == user.id))
        if count >= 100:
            raise HTTPException(422, 'Уже сохранено 100 шаблонов. Используйте существующий шаблон.')
        record = BusinessTemplate(company_id=company.id, created_by=user.id,
            title=data.title, request_key=str(data.request_key),
            xlsx_name=xlsx_name, xlsx_sha256=xlsx_hash, xlsx_size=len(workbook), xlsx_content=workbook,
            docx_name=docx_name, docx_sha256=docx_hash, docx_size=len(word), docx_content=word)
        s.add(record)
        s.flush()
        log(s, user, 'Сохранён шаблон бизнес-плана', 'business_template', record.id,
            f'Исходный проект {id}; ревизия {data.revision}')
        return template_meta(record)


def use_template(s, project, template_id, user):
    """Called by create_project inside the same write transaction."""
    record = s.scalar(select(BusinessTemplate).where(
        BusinessTemplate.id == template_id,
        BusinessTemplate.company_id == project.company_id,
        BusinessTemplate.created_by == user.id))
    if not record:
        raise HTTPException(404, 'Шаблон отчёта не найден для этой компании и пользователя.')
    pair = [('xlsx', record.xlsx_name, record.xlsx_content, record.xlsx_sha256),
            ('docx', record.docx_name, record.docx_content, record.docx_sha256)]
    if any(hashlib.sha256(content).hexdigest() != sha for _, _, content, sha in pair):
        raise HTTPException(409, 'Файлы шаблона изменились. Сохраните исходную пару заново.')
    if not native_candidate([{'name': record.xlsx_name, 'content': record.xlsx_content}]):
        raise HTTPException(422, 'Шаблон Excel не соответствует исходной модели UZGERMED.')
    if sources(s, project.id):
        raise HTTPException(409, 'Выберите шаблон при создании новой папки проекта.')
    for _, filename, content, sha in pair:
        s.add(BusinessSourceFile(company_id=project.company_id, project_id=project.id,
            relative_path='template/' + filename, filename=filename,
            sha256=sha, size=len(content), content=content))
    extraction = json.loads(project.extraction_json)
    project.extraction_json = dump({**extraction, 'mode': 'files', 'sources_pending': True,
        'template_selected': {'id': record.id, 'title': record.title,
                              'xlsx_sha256': record.xlsx_sha256, 'docx_sha256': record.docx_sha256}})
    project.revision += 1
    project.updated_at = now()
    s.flush()
    log(s, user, 'Выбран шаблон бизнес-плана', 'business_project', project.id,
        f'Шаблон {record.id}')
