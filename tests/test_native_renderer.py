"""Converter boundary checks; no real Office process or private document."""
from io import BytesIO
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from app import native_renderer as renderer


WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
PDF = b'%PDF-1.4\n% synthetic converter output\n%%EOF\n'


def docx(extra=None, document=None):
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml', document or f'<w:document xmlns:w="{WORD}"><w:body><w:p><w:r><w:t>Test template</w:t></w:r></w:p></w:body></w:document>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


def mock_converter(monkeypatch, timeout=False, pdf=PDF):
    calls = []
    monkeypatch.setenv('NATIVE_SOFFICE', 'synthetic-soffice')

    class Process:
        pid = 87654321
        returncode = None

        def __init__(self, command, **kwargs):
            self.command = command
            self.timed_out = False
            calls.append(('launch', command, kwargs))
            directory = Path(command[command.index('--outdir') + 1])
            assert (directory / 'business.docx').read_bytes().startswith(b'PK')
            (directory / 'business.pdf').write_bytes(pdf)

        def communicate(self, timeout=None):
            if mocked_timeout and not self.timed_out:
                self.timed_out = True
                raise subprocess.TimeoutExpired(self.command, timeout)
            self.returncode = 0
            return b'', b''

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            calls.append(('wait', timeout))
            if mocked_timeout and not self.timed_out:
                self.timed_out = True
                raise subprocess.TimeoutExpired(self.command, timeout)
            self.returncode = 0
            return 0

        def kill(self):
            calls.append(('kill', self.pid))
            self.returncode = -9

        def terminate(self):
            calls.append(('terminate', self.pid))
            self.returncode = -15

    mocked_timeout = timeout

    def run(command, **kwargs):
        if '--convert-to' not in command:
            calls.append(('cleanup', command, kwargs))
            return subprocess.CompletedProcess(command, 0, b'', b'')
        process = Process(command, **kwargs)
        if timeout:
            raise subprocess.TimeoutExpired(command, kwargs.get('timeout', 60))
        process.returncode = 0
        return subprocess.CompletedProcess(command, 0, b'', b'')

    monkeypatch.setattr(renderer.subprocess, 'run', run)
    monkeypatch.setattr(renderer.subprocess, 'Popen', Process)
    monkeypatch.setattr(renderer.os, 'killpg', lambda pid, sig: calls.append(('cleanup_group', pid, sig)), raising=False)
    return calls


def test_safe_conversion_preserves_bytes_isolated_profile_and_bounded_process(monkeypatch):
    calls = mock_converter(monkeypatch)
    source = docx()
    original = bytes(source)
    assert renderer.docx_to_pdf(source) == PDF
    assert source == original
    launches = [item for item in calls if item[0] == 'launch']
    assert len(launches) == 1
    command = launches[0][1]
    assert '--headless' in command and '--norestore' in command
    assert any(argument.startswith('-env:UserInstallation=file:') for argument in command)
    assert not launches[0][2].get('shell', False)
    assert launches[0][2]['stdout'] == subprocess.DEVNULL
    assert launches[0][2]['stderr'] == subprocess.DEVNULL
    assert ('wait', 60) in calls
    assert renderer._conversion.acquire(blocking=False)
    renderer._conversion.release()


@pytest.mark.parametrize('extra', [
    {'word/vbaProject.bin': b'not-a-macro'},
    {'word/embeddings/object.bin': b'not-an-object'},
    {'word/_rels/document.xml.rels': '<Relationships><Relationship TargetMode="External" Type="image" Target="https://example.invalid/image"/></Relationships>'},
])
def test_unsafe_parts_never_launch_office(monkeypatch, extra):
    calls = mock_converter(monkeypatch)
    with pytest.raises(ValueError):
        renderer.docx_to_pdf(docx(extra))
    assert calls == []


@pytest.mark.parametrize('part,xml', [
    ('word/document.xml', f'<w:document xmlns:w="{WORD}"><w:body><w:p><w:r><w:instrText>INCL</w:instrText></w:r><w:r><w:instrText>UDETEXT https://example.invalid/data</w:instrText></w:r></w:p></w:body></w:document>'),
    ('word/header1.xml', f'<w:hdr xmlns:w="{WORD}"><w:p><w:r><w:instrText>DDE data source</w:instrText></w:r></w:p></w:hdr>'),
    ('word/footer1.xml', f'<w:ftr xmlns:w="{WORD}"><w:p><w:fldSimple w:instr="INCLUDE&#80;ICTURE https://example.invalid/image"/></w:p></w:ftr>'),
])
def test_decoded_split_or_header_external_fields_never_launch_office(monkeypatch, part, xml):
    calls = mock_converter(monkeypatch)
    raw = docx(document=xml) if part == 'word/document.xml' else docx({part: xml})
    with pytest.raises(ValueError):
        renderer.docx_to_pdf(raw)
    assert calls == []


def test_timeout_returns_safe_error_and_releases_converter_lock(monkeypatch):
    calls = mock_converter(monkeypatch, timeout=True)
    with pytest.raises(ValueError, match='минут'):
        renderer.docx_to_pdf(docx())
    assert any(item[0] == 'launch' for item in calls)
    assert any(item[0] in ('cleanup', 'cleanup_group') for item in calls)
    assert ('wait', 10) in calls
    assert renderer._conversion.acquire(blocking=False)
    renderer._conversion.release()


def test_invalid_pdf_is_rejected_without_leaking_process_output(monkeypatch):
    mock_converter(monkeypatch, pdf=b'private process output must stay private')
    with pytest.raises(ValueError) as error:
        renderer.docx_to_pdf(docx())
    assert 'private process output' not in str(error.value)
