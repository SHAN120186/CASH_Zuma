"""Historical company-scoped private templates; new Excel copying is disabled."""
import unicodedata
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from .db import BusinessProject, BusinessTemplate, unit
from .business_projects import own, revision_ok, timestamp
from .main import get
from .report_archives import authorize

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
        return {'items': [template_meta(record) for record in records], 'can_upload': False}


@router.post('/api/business-projects/{id}/save-template')
def save_template(data: SaveTemplateIn, request: Request, id: int = Path(gt=0),
                  company_id: int = Query(gt=0)):
    from .business_projects import FINANCIAL_EXCEL_DISABLED
    with unit() as s:
        user, _ = authorize(s, request, 'import', company_id=company_id)
        project = get(s, BusinessProject, id)
        own(project, user)
        revision_ok(project, data.revision)
    raise HTTPException(410, FINANCIAL_EXCEL_DISABLED)


def use_template(s, project, template_id, user):
    """Retain the old privacy boundary without reading saved workbook blobs."""
    from .business_projects import FINANCIAL_EXCEL_DISABLED
    record = s.scalar(select(BusinessTemplate).where(
        BusinessTemplate.id == template_id,
        BusinessTemplate.company_id == project.company_id,
        BusinessTemplate.created_by == user.id))
    if not record:
        raise HTTPException(404, 'Шаблон отчёта не найден для этой компании и пользователя.')
    raise HTTPException(410, FINANCIAL_EXCEL_DISABLED)
