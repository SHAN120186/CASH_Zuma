"""Bounded, isolated conversion of an approved native DOCX template to PDF."""
from io import BytesIO
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from threading import Lock
from time import monotonic
from zipfile import ZipFile

from defusedxml import ElementTree as ET

_conversion = Lock()


def _toc_destination_pages(content,expected):
    """Read actual internal TOC targets, including wrapped link annotations.

    A strict one-to-one count protects templates with unrelated internal links
    from being assigned page numbers by guesswork.
    """
    from pypdf import PdfReader
    try:
        reader=PdfReader(BytesIO(content))
        if not 1<=len(reader.pages)<=1000:raise ValueError()
        references={(page.indirect_reference.idnum,page.indirect_reference.generation):index
                    for index,page in enumerate(reader.pages) if page.indirect_reference is not None}
        links=[];annotations=0
        for index,page in enumerate(reader.pages):
            for reference in page.get('/Annots',[]):
                annotations+=1
                if annotations>10000:raise ValueError()
                annotation=reference.get_object()
                if annotation.get('/Subtype')!='/Link':continue
                destination=annotation.get('/Dest')
                if destination is None:
                    action=annotation.get('/A')
                    if action is not None:
                        action=action.get_object()
                        if action.get('/S')=='/GoTo':destination=action.get('/D')
                if destination is None:continue
                if isinstance(destination,str):
                    named=reader.named_destinations.get(destination)
                    if named is None:raise ValueError()
                    destination=named.dest_array
                destination=destination.get_object()
                if len(destination)<2:raise ValueError()
                target=destination[0]
                if hasattr(target,'idnum'):
                    target_page=references.get((target.idnum,target.generation))
                elif isinstance(target,int):target_page=int(target)
                else:raise ValueError()
                if target_page is None or not 0<=target_page<len(reader.pages):raise ValueError()
                rectangle=[float(value) for value in annotation['/Rect']]
                if len(rectangle)!=4 or not all(math.isfinite(value) for value in rectangle):raise ValueError()
                key=(target_page,*(str(value) for value in destination[1:]))
                links.append((index,-rectangle[3],rectangle[0],key))
        pages=[];previous=None
        for _,_,_,key in sorted(links):
            if key==previous:continue
            pages.append(key[0]+1);previous=key
        if len(pages)!=expected:raise ValueError()
        return pages
    except Exception as exc:
        raise ValueError('Не удалось сопоставить ссылки PDF с оглавлением Word. Проверьте шаблон.') from exc


def docx_to_pdf(raw):
    executable = os.environ.get('NATIVE_SOFFICE') or shutil.which('soffice')
    if not executable:
        raise ValueError('На сервере не установлен конвертер Word в PDF.')
    if not isinstance(raw, bytes) or not raw or len(raw)>20*1024*1024:
        raise ValueError('Нужен непустой DOCX размером до 20 МБ.')
    with ZipFile(BytesIO(raw)) as archive:
        if len(archive.infolist())>2000 or sum(part.file_size for part in archive.infolist())>100*1024*1024:
            raise ValueError('Шаблон Word превышает допустимый размер после распаковки.')
        if 'word/document.xml' not in archive.namelist():
            raise ValueError('В DOCX отсутствует основной документ Word.')
        for part in archive.infolist():
            name = part.filename.lower()
            if 'vbaproject' in name or '/embeddings/' in name:
                raise ValueError('Шаблон Word содержит макросы или встроенные объекты. Сохраните обычный DOCX без них.')
            if name.endswith('.rels'):
                root = ET.fromstring(archive.read(part))
                if any(item.attrib.get('TargetMode') == 'External' and
                       not item.attrib.get('Type', '').endswith('/hyperlink') for item in root):
                    raise ValueError('В шаблоне Word есть внешние изображения или шаблоны. Встройте их в DOCX.')
            if name.startswith('word/') and name.endswith('.xml'):
                if part.file_size>8*1024*1024:
                    raise ValueError('Один XML-компонент Word превышает допустимый размер.')
                root=ET.fromstring(archive.read(part))
                fields=[]
                for node in root.iter():
                    if node.tag.rsplit('}',1)[-1]=='instrText':fields.append(node.text or '')
                    if node.tag.rsplit('}',1)[-1]=='fldSimple':
                        fields.extend(value for key,value in node.attrib.items() if key.rsplit('}',1)[-1]=='instr')
                instructions=''.join(fields).upper()
                if any(token in instructions for token in ('INCLUDETEXT', 'INCLUDEPICTURE', 'DDE')):
                    raise ValueError('Удалите из Word поля загрузки внешних данных.')
    # The free hosting worker renders one document at a time, without a shared
    # office profile or any execution of uploaded formulas/macros.
    if not _conversion.acquire(timeout=2):
        raise ValueError('Конвертер занят другим отчётом. Повторите расчёт через минуту.')
    try:
        from .native_documents import prepare_pdf_layout,toc_page_fields,update_toc_pages
        render_copy = prepare_pdf_layout(raw)
        toc=toc_page_fields(render_copy)
        with tempfile.TemporaryDirectory(prefix='native-report-') as temporary:
            directory = Path(temporary)
            source = directory / 'business.docx'
            profile = directory / 'profile'
            command = [executable, '-env:UserInstallation=' + profile.as_uri(),
                       '--headless', '--nologo', '--nodefault', '--nofirststartwizard', '--norestore',
                       '--convert-to', 'pdf:writer_pdf_Export', '--outdir', str(directory), str(source)]
            target = directory / 'business.pdf'
            started=monotonic()
            for attempt in range(3):
                source.write_bytes(render_copy)
                # Office may refuse to overwrite a prior PDF. Never accept it
                # as the result of the next, corrected DOCX conversion.
                target.unlink(missing_ok=True)
                remaining=60 if attempt==0 else 60-(monotonic()-started)
                if remaining<=0:raise subprocess.TimeoutExpired(command,60)
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=os.name!='nt',
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0)
                try:
                    returncode=process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    if os.name=='nt':
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        try:
                            os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=10)
                    raise
                if returncode or not target.exists():
                    raise ValueError('Не удалось преобразовать Word в PDF. Проверьте шаблон документа.')
                content = target.read_bytes()
                if not content.startswith(b'%PDF-') or len(content) > 20 * 1024 * 1024:
                    raise ValueError('Конвертер не создал корректный PDF допустимого размера.')
                if not toc:return content
                pages=_toc_destination_pages(content,len(toc))
                updated=update_toc_pages(render_copy,pages)
                if updated==render_copy:return content
                render_copy=updated
            raise ValueError('Номера страниц оглавления не стабилизировались. Проверьте разрывы страниц в Word.')
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Конвертация Word заняла больше минуты. Уменьшите размер шаблона.') from exc
    finally:
        _conversion.release()
