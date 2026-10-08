"""Bounded, isolated conversion of an approved native DOCX template to PDF."""
from io import BytesIO
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from threading import Lock
from zipfile import ZipFile

from defusedxml import ElementTree as ET

_conversion = Lock()


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
        from .native_documents import prepare_pdf_layout
        render_copy = prepare_pdf_layout(raw)
        with tempfile.TemporaryDirectory(prefix='native-report-') as temporary:
            directory = Path(temporary)
            source = directory / 'business.docx'
            source.write_bytes(render_copy)
            profile = directory / 'profile'
            command = [executable, '-env:UserInstallation=' + profile.as_uri(),
                       '--headless', '--nologo', '--nodefault', '--nofirststartwizard', '--norestore',
                       '--convert-to', 'pdf:writer_pdf_Export', '--outdir', str(directory), str(source)]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=os.name!='nt',
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0)
            try:
                returncode=process.wait(timeout=60)
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
            target = directory / 'business.pdf'
            if returncode or not target.exists():
                raise ValueError('Не удалось преобразовать Word в PDF. Проверьте шаблон документа.')
            content = target.read_bytes()
            if not content.startswith(b'%PDF-') or len(content) > 20 * 1024 * 1024:
                raise ValueError('Конвертер не создал корректный PDF допустимого размера.')
            return content
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Конвертация Word заняла больше минуты. Уменьшите размер шаблона.') from exc
    finally:
        _conversion.release()
