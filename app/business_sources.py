"""Bounded, offline extraction of explicit forecast inputs and source evidence.

Workbooks are read as OOXML data, never executed. Formula caches remain evidence;
only the small, documented arithmetic expressions of the UZGERMED profile are
reconstructed. Uploaded originals and complete accounting tables are not returned.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from io import BytesIO
import ast
import json
import posixpath
import re
import zipfile
from typing import Any

from defusedxml import ElementTree as ET
from openpyxl.formula.translate import Translator

from .business_model import validate_model


class SourceReadError(ValueError):
    """An intentionally public, non-sensitive source validation message."""


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 150
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 30 * 1024 * 1024
MAX_ZIP_MEMBERS = 2048
MAX_SHEETS = 64
MAX_CELLS = 200_000
MAX_ROWS = 10_000
MAX_TEXT = 120_000
MAX_PDF_PAGES = 100
MAX_NARRATIVE = 16_000
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CANONICAL_FIELDS = {
    "title", "currency", "start", "months", "tax_rate", "discount_rate",
    "opening_cash", "opening_receivables", "opening_inventory", "opening_payables",
    "initial_investment", "receivable_days", "inventory_days", "payable_days",
    "fixed_costs", "equity", "products", "assets", "loans",
    "project_description", "technology", "market", "location", "investment_purpose",
}
TEXT_FIELDS = {"project_description", "technology", "market", "location", "investment_purpose"}
RUSSIAN_FIELDS = {
    "название проекта": "title", "проект": "title", "валюта": "currency",
    "месяц начала": "start", "дата начала": "start", "начало прогноза": "start",
    "месяцев": "months", "период месяцев": "months", "горизонт месяцев": "months",
    "налог на прибыль": "tax_rate", "ставка налога на прибыль": "tax_rate",
    "ставка дисконтирования": "discount_rate", "деньги на начало": "opening_cash",
    "начальные деньги": "opening_cash", "дебиторская задолженность на начало": "opening_receivables",
    "начальная дебиторская задолженность": "opening_receivables",
    "запасы на начало": "opening_inventory", "начальные запасы": "opening_inventory",
    "кредиторская задолженность на начало": "opening_payables",
    "начальная кредиторская задолженность": "opening_payables",
    "начальные инвестиции": "initial_investment", "дни дебиторской задолженности": "receivable_days",
    "дни запасов": "inventory_days", "дни кредиторской задолженности": "payable_days",
    "постоянные расходы": "fixed_costs", "постоянные расходы в месяц": "fixed_costs",
    "взносы в капитал": "equity", "взносы в капитал в месяц": "equity",
    "продукция": "products", "активы": "assets", "кредиты": "loans",
}
PARAMETER_LABELS = {
    "title": "Название проекта", "currency": "Валюта", "start": "Месяц начала", "months": "Месяцев",
    "tax_rate": "Ставка налога на прибыль", "discount_rate": "Ставка дисконтирования",
    "opening_cash": "Деньги на начало", "opening_receivables": "Дебиторская задолженность на начало",
    "opening_inventory": "Запасы на начало", "opening_payables": "Кредиторская задолженность на начало",
    "initial_investment": "Начальные инвестиции", "receivable_days": "Дни дебиторской задолженности",
    "inventory_days": "Дни запасов", "payable_days": "Дни кредиторской задолженности",
    "fixed_costs": "Постоянные расходы в месяц", "equity": "Взносы в капитал в месяц",
    "assets_none": "Активы отсутствуют", "loans_none": "Кредиты отсутствуют",
    "project_description": "Описание проекта", "technology": "Технология", "market": "Рынок",
    "location": "Местоположение", "investment_purpose": "Назначение инвестиций",
}
NORMALIZED_HEADERS = {
    "Продукция": {"name": "Наименование", "unit": "Единица", "price": "Цена", "unit_cost": "Себестоимость", "quantities": "Объём в месяц"},
    "Активы": {"name": "Наименование", "value": "Стоимость", "life_months": "Срок амортизации (месяцы)", "commissioning_month": "Месяц ввода"},
    "Кредиты": {"name": "Наименование", "opening_balance": "Остаток на начало", "drawdowns": "Выдача в месяц", "principal": "Основной долг в месяц", "interest": "Проценты в месяц"},
}
RUSSIAN_FIELDS.update({_label: canonical for canonical, label in PARAMETER_LABELS.items()
                       for _label in [label.casefold().replace("ё", "е")]})
RUSSIAN_FIELDS.update({"assets_none": "assets_none", "loans_none": "loans_none"})
TABLE_FIELDS = {
    "products": {"название": "name", "продукт": "name", "наименование": "name",
                 "единица": "unit", "единица измерения": "unit", "цена": "price",
                 "цена без ндс": "price", "себестоимость": "unit_cost",
                 "удельные затраты": "unit_cost", "объем": "quantities", "объём": "quantities",
                 "объем в месяц": "quantities", "объём в месяц": "quantities", "мощность": "capacity"},
    "loans": {"название": "name", "кредит": "name", "начальный остаток": "opening_balance",
              "долг на начало": "opening_balance", "получение": "drawdowns",
              "получение кредита": "drawdowns", "погашение": "principal", "основной долг": "principal",
              "проценты": "interest"},
    "assets": {"название": "name", "актив": "name", "стоимость": "value",
               "срок месяцев": "life_months", "срок службы месяцев": "life_months",
               "месяц ввода": "commissioning_month"},
}
for _sheet_name, _headers in NORMALIZED_HEADERS.items():
    _collection = {"Продукция": "products", "Активы": "assets", "Кредиты": "loans"}[_sheet_name]
    TABLE_FIELDS[_collection].update({label.casefold().replace("ё", "е"): field for field, label in _headers.items()})
COLLECTION_NAMES = {"products": "products", "продукция": "products",
                    "loans": "loans", "кредиты": "loans", "assets": "assets", "активы": "assets"}


def _label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold().replace("ё", "е")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", "."))
        return parsed if parsed.is_finite() and abs(parsed) <= Decimal("1e15") else None
    except (InvalidOperation, ValueError):
        return None


def _number(value: Decimal | int | float) -> str:
    # The engine accepts at most twelve decimal places. Do not introduce binary
    # float rounding or oversized Decimal coefficients in reconstructed inputs.
    with localcontext() as context:
        context.prec = 50
        result = Decimal(str(value)).quantize(Decimal("0.000000000001"))
    return format(result, "f").rstrip("0").rstrip(".") if result else "0"


def _raw_number(value: Decimal) -> str:
    """Keep explicit source precision for validation instead of rounding it."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _profile_quantity(value: Decimal) -> str:
    """Round only reconstructed quantities to the engine's Excel precision."""
    if not value:
        return "0"
    places = min(12, max(0, 15 - value.adjusted() - 1))
    with localcontext() as context:
        context.prec = 50
        rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return _raw_number(rounded)


def _issue(result: dict, field: str, code: str, message: str, source: dict | None = None,
           confirmation: bool = True) -> None:
    item = {"field": field, "code": code, "message": message, "requires_confirmation": confirmation}
    if source:
        item["source"] = source
    result["issues"].append(item)


def _evidence(result: dict, field: str, value: Any, source: dict, method: str = "explicit",
              status: str = "extracted", note: str = "") -> None:
    item = {"field": field, "value": value, "source": source, "method": method, "status": status}
    if note:
        item["note"] = note
    result["evidence"].append(item)


def _source(name: str, sheet: str | None = None, cell: str | None = None, page: int | None = None) -> dict:
    result = {"filename": name}
    if sheet is not None:
        result["sheet"] = sheet
    if cell is not None:
        result["cell"] = cell
    if page is not None:
        result["page"] = page
    return result


def _archive(content: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(BytesIO(content))
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS or sum(m.file_size for m in members) > MAX_ZIP_BYTES:
        archive.close()
        raise SourceReadError("Архив превышает предел числа файлов или распакованного размера.")
    names = set()
    for member in members:
        parts = member.filename.replace("\\", "/").split("/")
        if (member.file_size > MAX_MEMBER_BYTES or member.flag_bits & 1 or
                member.filename.startswith(("/", "\\")) or ".." in parts or
                member.filename in names):
            archive.close()
            raise SourceReadError("Небезопасная или слишком большая запись ZIP.")
        names.add(member.filename)
    return archive


def _xml(archive: zipfile.ZipFile, name: str):
    return ET.fromstring(archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True)


def _column(address: str) -> int:
    result = 0
    for char in re.match(r"[A-Z]+", address).group():
        result = result * 26 + ord(char) - 64
    return result


def _colname(number: int) -> str:
    text = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        text = chr(65 + remainder) + text
    return text


def _xlsx(content: bytes) -> tuple[dict, list[str]]:
    """Sparse OOXML cells including raw formulas/cache, without external access."""
    with _archive(content) as archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise SourceReadError("Книга не содержит workbook.xml.")
        if any("vbaproject" in n.casefold() or n.casefold().endswith("/vba.bin") for n in names):
            raise SourceReadError("Книги с макросами не принимаются.")
        notes = []
        if any(n.startswith("xl/externalLinks/") for n in names):
            notes.append("external_links")
        strings = []
        if "xl/sharedStrings.xml" in names:
            for node in _xml(archive, "xl/sharedStrings.xml").findall("s:si", NS):
                strings.append("".join(t.text or "" for t in node.iter(f"{{{NS['s']}}}t")))
                if len(strings) > MAX_CELLS or len(strings[-1]) > MAX_TEXT:
                    raise SourceReadError("Слишком большой список текстовых значений Excel.")
        styles = []
        if "xl/styles.xml" in names:
            style_root = _xml(archive, "xl/styles.xml")
            custom = {int(n.attrib["numFmtId"]): n.attrib.get("formatCode", "")
                      for n in style_root.findall("s:numFmts/s:numFmt", NS)}
            for node in style_root.findall("s:cellXfs/s:xf", NS):
                fmt = int(node.attrib.get("numFmtId", "0"))
                code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(fmt, "")).lower()
                styles.append((fmt in range(14, 23) or bool(re.search(r"[yd]", code)),
                               fmt in (9, 10) or "%" in code))
        relationships = {}
        for node in _xml(archive, "xl/_rels/workbook.xml.rels"):
            if node.attrib.get("TargetMode") == "External":
                continue
            target = node.attrib.get("Target", "")
            path = posixpath.normpath("xl/" + target) if not target.startswith("/") else target.lstrip("/")
            if not path.startswith("xl/") or ".." in path.split("/"):
                raise SourceReadError("Недопустимый путь листа.")
            relationships[node.attrib.get("Id")] = path
        root = _xml(archive, "xl/workbook.xml")
        date1904 = root.find("s:workbookPr", NS)
        epoch = datetime(1904, 1, 1) if date1904 is not None and date1904.attrib.get("date1904") in ("1", "true") else datetime(1899, 12, 30)
        sheets = root.findall("s:sheets/s:sheet", NS)
        if len(sheets) > MAX_SHEETS:
            raise SourceReadError("В книге слишком много листов.")
        result = {}
        count = 0
        for sheet in sheets:
            path = relationships.get(sheet.attrib.get(f"{{{DOC_REL_NS}}}id"))
            if path is None or path not in names:
                continue
            cells = {}
            shared_formulas = {}
            for node in _xml(archive, path).findall("s:sheetData/s:row/s:c", NS):
                address = node.attrib.get("r", "")
                if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,4}", address):
                    raise SourceReadError("Некорректный адрес ячейки.")
                if int(re.search(r"[0-9]+", address).group()) > MAX_ROWS or _column(address) > 16384:
                    raise SourceReadError("Данные превышают допустимые границы листа.")
                count += 1
                if count > MAX_CELLS:
                    raise SourceReadError("Книга содержит слишком много ячеек.")
                kind = node.attrib.get("t", "n")
                formula = node.find("s:f", NS)
                raw_value = node.find("s:v", NS)
                value = raw_value.text if raw_value is not None else None
                numeric_text = None
                if kind == "s" and value is not None:
                    index = int(value)
                    if not 0 <= index < len(strings):
                        raise SourceReadError("Неверный индекс строки Excel.")
                    value = strings[index]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in node.findall("s:is//s:t", NS))
                elif kind == "b":
                    value = value == "1"
                elif kind not in ("str", "e", "d") and value is not None:
                    number = _decimal(value)
                    if number is None:
                        raise SourceReadError("Нечисловое значение числовой ячейки.")
                    numeric_text = _raw_number(number)
                    value = _number(number)
                style_index = int(node.attrib.get("s", "0"))
                is_date, is_percent = styles[style_index] if style_index < len(styles) else (False, False)
                if is_date and value is not None and kind == "n":
                    numeric = _decimal(value)
                    if numeric is not None and 0 <= numeric < 100000:
                        value = (epoch + timedelta(days=float(numeric))).date().isoformat()
                        numeric_text = None
                if isinstance(value, str) and len(value) > MAX_TEXT:
                    raise SourceReadError("Слишком длинное значение ячейки.")
                if formula is not None or value is not None:
                    cells[address] = {"value": value, "formula": None if formula is None else "=" + (formula.text or ""),
                                      "error": kind == "e", "percent": is_percent}
                    if numeric_text is not None:
                        cells[address]["numeric_text"] = numeric_text
                    if formula is not None and formula.attrib.get("t") == "shared":
                        shared_id = formula.attrib.get("si")
                        cells[address]["shared_id"] = shared_id
                        if formula.text:
                            shared_formulas[shared_id] = (address, "=" + formula.text)
            for address, cell in cells.items():
                if "shared_id" in cell and cell["formula"] == "=":
                    anchor = shared_formulas.get(cell.pop("shared_id"))
                    if anchor is not None:
                        cell["formula"] = Translator(anchor[1], origin=anchor[0]).translate_formula(address)
            result[sheet.attrib.get("name", "")] = cells
        return result, notes


def _cell_value(cells: dict, address: str) -> Any:
    cell = cells.get(address, {})
    return None if cell.get("formula") or cell.get("error") else cell.get("value")


def _explicit(result: dict, cells: dict, address: str, field: str, name: str, sheet: str) -> Any:
    cell = cells.get(address, {})
    source = _source(name, sheet, address)
    if cell.get("error"):
        _issue(result, field, "excel_error", "В исходной ячейке ошибка Excel; значение не заменено нулём.", source)
        return None
    if cell.get("formula"):
        _issue(result, field, "formula_input", "Введите явное исходное значение: произвольная формула Excel не исполняется.", source)
        _evidence(result, field, cell.get("value"), source, "excel_saved_cache", "needs_confirmation")
        return None
    value = cell.get("numeric_text", cell.get("value"))
    if value not in (None, ""):
        _evidence(result, field, value, source)
    return value


def _parse_value(value: Any, field: str, percent: bool = False) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            array = json.loads(text)
            if not isinstance(array, list) or len(array) > 60:
                raise SourceReadError("Месячный ряд должен содержать не более 60 значений.")
            return array
        if ";" in text:
            return [part.strip().replace(",", ".") for part in text.split(";")]
        if field in ("tax_rate", "discount_rate") and text.endswith("%"):
            numeric = _decimal(text[:-1])
            return None if numeric is None else _raw_number(numeric / 100)
        if field not in ("title", "currency", "start", "name", "unit"):
            numeric = _decimal(text)
            if numeric is not None:
                if field in ("months", "life_months", "commissioning_month") and numeric == numeric.to_integral():
                    return int(numeric)
                return _raw_number(numeric)
        return text
    return value


def _normalised_xlsx(result: dict, sheets: dict, name: str) -> dict | None:
    parameter_sheet = next((key for key in sheets if _label(key) in ("параметры", "parameters")), None)
    if parameter_sheet is None:
        return None
    inputs = {}
    cells = sheets[parameter_sheet]
    none_flags = {}
    for row in range(1, min(MAX_ROWS, max((int(re.search(r"\d+", a).group()) for a in cells), default=0)) + 1):
        key = _cell_value(cells, f"A{row}")
        if key is None:
            continue
        label = _label(key)
        field = label if label in CANONICAL_FIELDS else RUSSIAN_FIELDS.get(label)
        if field is None:
            if label not in ("параметр", "значение", "parameter"):
                _issue(result, "parameters", "unknown_parameter", f"Неизвестный параметр «{str(key)[:120]}».", _source(name, parameter_sheet, f"A{row}"))
            continue
        if field in inputs:
            _issue(result, field, "duplicate_parameter", "Параметр указан несколько раз; проверьте источник.", _source(name, parameter_sheet, f"A{row}"))
            continue
        value = _explicit(result, cells, f"B{row}", field, name, parameter_sheet)
        if value is not None:
            if field in ("assets_none", "loans_none"):
                if value is True or _label(value) in ("да", "yes", "true", "1"):
                    none_flags[field.split("_")[0]] = True
                elif value is not False and _label(value) not in ("нет", "no", "false", "0"):
                    _issue(result, field, "explicit_none", "Для отсутствующего списка укажите «Да» явно.", _source(name, parameter_sheet, f"B{row}"))
            else:
                inputs[field] = _parse_value(value, field, cells.get(f"B{row}", {}).get("percent", False))
    for sheet, table in sheets.items():
        collection = COLLECTION_NAMES.get(_label(sheet))
        if collection is None:
            continue
        rows = sorted({int(re.search(r"\d+", a).group()) for a in table})
        header = None
        mappings = {}
        for row in rows[:10]:
            mappings = {}
            for address in table:
                if int(re.search(r"\d+", address).group()) != row:
                    continue
                label = _label(_cell_value(table, address))
                canonical = label if label in set(TABLE_FIELDS[collection].values()) else TABLE_FIELDS[collection].get(label)
                monthly = re.fullmatch(r"(quantities|capacity|drawdowns|principal|interest)[ _](\d{1,2})", label)
                if monthly:
                    canonical = monthly.group(1) + ":" + monthly.group(2)
                russian_month = re.fullmatch(r"(?:объем|количество|месяц)[ _](\d{1,2})", label)
                if collection == "products" and russian_month:
                    canonical = "quantities:" + russian_month.group(1)
                numeric_month = _decimal(_cell_value(table, address))
                if collection == "products" and numeric_month is not None and numeric_month == numeric_month.to_integral() and 1 <= numeric_month <= 60:
                    canonical = "quantities:" + str(int(numeric_month))
                if canonical:
                    if canonical in mappings.values():
                        _issue(result, collection, "duplicate_column", "В таблице повторяется столбец исходных данных.", _source(name, sheet, address))
                    mappings[_column(address)] = canonical
            if "name" in mappings.values():
                header = row
                break
        if header is None:
            _issue(result, collection, "table_header", "Не найден заголовок таблицы с названием позиции.", _source(name, sheet))
            continue
        entries = []
        for row in rows:
            if row <= header:
                continue
            addresses = [_colname(column) + str(row) for column in mappings]
            if not any(table.get(a, {}).get("value") not in (None, "") or table.get(a, {}).get("formula") for a in addresses):
                continue
            if len(entries) >= 200:
                raise SourceReadError("В таблице допускается не более 200 строк.")
            entry = {}
            arrays = {}
            for column, field in mappings.items():
                address = _colname(column) + str(row)
                root_field, _, month = field.partition(":")
                prefix = f"{collection}[{len(entries)}].{root_field}"
                value = _explicit(result, table, address, prefix, name, sheet)
                if month:
                    arrays.setdefault(root_field, {})[int(month)] = _parse_value(value, root_field)
                elif value is not None:
                    entry[field] = _parse_value(value, field)
            for field, values in arrays.items():
                if field in entry:
                    _issue(result, f"{collection}[{len(entries)}].{field}", "monthly_scalar_conflict", "Указаны одновременно постоянное значение и месячный ряд.", _source(name, sheet))
                entry[field] = [values.get(month) for month in range(1, max(values) + 1)]
            entries.append(entry)
        if collection in inputs and inputs[collection] != entries:
            _issue(result, collection, "collection_conflict", "Список одновременно задан в параметрах и на отдельном листе.", _source(name, sheet))
        if entries:
            inputs[collection] = entries
    for collection in none_flags:
        if inputs.get(collection):
            _issue(result, collection, "none_rows_conflict", "Указано отсутствие списка, но таблица содержит позиции.", _source(name, parameter_sheet))
        else:
            inputs[collection] = []
    schedule_name = next((sheet for sheet in sheets if _label(sheet) in ("график кредитов", "loan schedule")), None)
    if schedule_name is not None:
        schedule = sheets[schedule_name]
        schedule_fields = {"кредит": "name", "loan": "name", "месяц": "month", "month": "month",
                           "выдача": "drawdowns", "drawdowns": "drawdowns", "основной долг": "principal", "principal": "principal",
                           "проценты": "interest", "interest": "interest"}
        mapping = {_column(address): schedule_fields[_label(_cell_value(schedule, address))]
                   for address in schedule if address.endswith("1") and re.fullmatch(r"[A-Z]+1", address)
                   and _label(_cell_value(schedule, address)) in schedule_fields}
        months = inputs.get("months")
        if set(mapping.values()) != {"name", "month", "drawdowns", "principal", "interest"} or not isinstance(months, int) or not 1 <= months <= 60:
            _issue(result, "loans", "loan_schedule_header", "График кредитов требует все пять заголовков и заданное число месяцев.", _source(name, schedule_name))
        else:
            names = {_label(loan.get("name")): loan for loan in inputs.get("loans", []) if isinstance(loan, dict)}
            schedules = {}
            for row in sorted({int(re.search(r"\d+", a).group()) for a in schedule} - {1}):
                raw = {field: _explicit(result, schedule, _colname(column) + str(row), "loans.schedule." + field, name, schedule_name)
                       for column, field in mapping.items()}
                if not any(value is not None for value in raw.values()):
                    continue
                loan_name = _label(raw.get("name"))
                month = _decimal(raw.get("month"))
                if loan_name not in names or month is None or month != month.to_integral() or not 1 <= month <= months:
                    _issue(result, "loans", "loan_schedule_row", "В графике неизвестный кредит или недопустимый номер месяца.", _source(name, schedule_name, f"A{row}:E{row}"))
                    continue
                month = int(month)
                key = (loan_name, month)
                if key in schedules:
                    _issue(result, "loans", "loan_schedule_duplicate", "Месяц кредита указан в графике дважды.", _source(name, schedule_name, f"A{row}:E{row}"))
                    continue
                schedules[key] = raw
            for loan_name, loan in names.items():
                if not any(key[0] == loan_name for key in schedules):
                    continue
                for field in ("drawdowns", "principal", "interest"):
                    if field in loan:
                        _issue(result, "loans." + field, "loan_schedule_scalar_conflict", "У кредита одновременно задана месячная сумма и отдельный график.", _source(name, schedule_name))
                    loan[field] = [_parse_value(schedules.get((loan_name, month), {}).get(field), field) for month in range(1, months + 1)]
    return inputs


def _json_model(result: dict, content: bytes, name: str) -> dict:
    def pairs(entries):
        parsed = {}
        for key, value in entries:
            if key in parsed:
                raise SourceReadError("JSON содержит повторяющееся имя поля.")
            parsed[key] = value
        return parsed
    data = json.loads(content.decode("utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Неконечное число JSON.")))
    if not isinstance(data, dict):
        raise SourceReadError("project.json должен содержать объект расчётных входов.")
    if "inputs" in data:
        data = data["inputs"]
    elif "model" in data:
        data = data["model"]
    if not isinstance(data, dict):
        raise SourceReadError("Поле inputs должно быть объектом.")
    nodes = 0
    def walk(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 12 or nodes > 30_000:
            raise SourceReadError("JSON слишком большой или глубоко вложенный.")
        if isinstance(value, dict):
            for child in value.values():
                walk(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 200:
                raise SourceReadError("Слишком длинный список JSON.")
            for child in value:
                walk(child, depth + 1)
        elif isinstance(value, str) and len(value) > MAX_TEXT:
            raise SourceReadError("Слишком длинный текст JSON.")
    walk(data)
    selected = {key: value for key, value in data.items() if key in CANONICAL_FIELDS}
    record_fields = {"products": {"name", "unit", "price", "unit_cost", "quantities", "capacity"},
                     "assets": {"name", "value", "life_months", "commissioning_month"},
                     "loans": {"name", "opening_balance", "drawdowns", "principal", "interest"}}
    for collection, allowed in record_fields.items():
        if isinstance(selected.get(collection), list):
            selected[collection] = [{key: value for key, value in entry.items() if key in allowed} if isinstance(entry, dict) else entry
                                    for entry in selected[collection]]
    for field, value in selected.items():
        _evidence(result, field, value, _source(name), "explicit_json")
    for key in data.keys() - CANONICAL_FIELDS:
        _issue(result, str(key)[:120], "unknown_json_field", "Поле JSON не входит в расчётную модель.", _source(name), False)
    return selected


def _literal_formula(cell: dict) -> Decimal | None:
    """Only constants and constant divided by the workbook's named FX rate."""
    if cell.get("error"):
        return None
    value = _decimal(cell.get("value")) if not cell.get("formula") else None
    if value is not None:
        return value
    formula = (cell.get("formula") or "").replace(" ", "")
    match = re.fullmatch(r"=\+?\(?([0-9]+(?:\.[0-9]+)?)\)?/(?:\(?\+?курс\)?)", formula)
    return _decimal(match.group(1)) if match else None


def _profile_arithmetic(cells: dict, address: str, seen: frozenset = frozenset(), depth: int = 0,
                        budget: list | None = None) -> Decimal | None:
    """Tiny arithmetic reader for the profile's BOM, not a spreadsheet engine.

    Only numeric cells, + - * /, same-sheet references and bounded SUM ranges.
    No functions outside SUM, strings, attributes, external or cross-sheet refs.
    """
    budget = [2000] if budget is None else budget
    budget[0] -= 1
    if depth > 16 or budget[0] < 0 or address in seen:
        return None
    cell = cells.get(address, {})
    if cell.get("error"):
        return None
    if not cell.get("formula"):
        return _decimal(cell.get("value"))
    expression = cell["formula"][1:].replace(" ", "").upper()
    aliases = {}
    def replace_sum(match):
        first, last = int(match.group(2)), int(match.group(3))
        if last < first or last - first > 200:
            raise SourceReadError("range")
        total = Decimal(0)
        for row in range(first, last + 1):
            target = match.group(1) + str(row)
            target_cell = cells.get(target, {})
            # SUM ignores empty/text cells in its explicit source range. This
            # does not turn a missing business input or Excel error into zero.
            if not target_cell.get("formula") and not target_cell.get("error") and _decimal(target_cell.get("value")) is None:
                continue
            value = _profile_arithmetic(cells, target, seen | {address}, depth + 1, budget)
            if value is None:
                raise SourceReadError("missing")
            total += value
        key = "PROFILE_SUM_" + str(len(aliases))
        aliases[key] = total
        return key
    try:
        expression = re.sub(r"SUM\(([A-Z]{1,3})([1-9][0-9]*):\1([1-9][0-9]*)\)", replace_sum, expression)
        if len(expression) > 400 or not re.fullmatch(r"[A-Z0-9_+*/().-]+", expression):
            return None
        tree = ast.parse(expression, mode="eval")
        def calculate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return _decimal(ast.get_source_segment(expression, node))
            if isinstance(node, ast.Name):
                if node.id in aliases:
                    return aliases[node.id]
                if re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,4}", node.id):
                    return _profile_arithmetic(cells, node.id, seen | {address}, depth + 1, budget)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = calculate(node.operand)
                return None if value is None else (value if isinstance(node.op, ast.UAdd) else -value)
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                left, right = calculate(node.left), calculate(node.right)
                if left is None or right is None or isinstance(node.op, ast.Div) and right == 0:
                    return None
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                return left / right
            return None
        value = calculate(tree.body)
        return value if value is not None and value.is_finite() and abs(value) <= Decimal("1e15") else None
    except (ValueError, SyntaxError, InvalidOperation, ArithmeticError):
        return None


def _uzgermed(result: dict, sheets: dict, name: str) -> dict | None:
    required = {"План производства", "Калькуляцияя", "Стоим_проекта", "ВНД", "Производ. с учетом загрузки"}
    if not required.issubset(sheets):
        return None
    result["documents"][-1]["profile"] = "uzgermed_36m"
    inputs = {"currency": "USD", "months": 36, "products": []}
    project = sheets["Стоим_проекта"]
    title = _cell_value(project, "B4")
    if isinstance(title, str) and title.strip():
        inputs["title"] = title.strip()[:160]
    _evidence(result, "months", 36, _source(name, "План производства", "A8:F47"), "verified_profile_structure")
    fx = _decimal(_cell_value(project, "B38"))
    factor = _decimal(_cell_value(sheets["ВНД"], "W41"))
    vat = _decimal(_cell_value(sheets.get("НДС", {}), "F4"))
    for field, value, sheet, cell in (("fx_uzs_per_usd", fx, "Стоим_проекта", "B38"),
                                     ("price_factor", factor, "ВНД", "W41"),
                                     ("vat_rate", vat, "НДС", "F4")):
        if value is not None:
            _evidence(result, field, _number(value), _source(name, sheet, cell), "profile_input", "needs_confirmation")
    _issue(result, "products.price", "price_factor_vat", "Подтвердите ценовой коэффициент и НДС. Цена модели включает коэффициент ВНД!W41; кандидат без НДС рассчитан делением на 1 + НДС.", _source(name, "ВНД", "W41"))
    _issue(result, "products.quantities", "volume_policy", "Подтвердите объёмы: модель повторяет среднее за два года по месяцам с загрузкой, отдельного плана продаж по каждому из 36 месяцев нет. Восстановленные месячные объёмы округлены до 15 значащих цифр и не более 12 знаков после запятой; значения до округления сохранены в источниках.", _source(name, "План производства", "C8:E47"))
    utilisation = sheets["Производ. с учетом загрузки"]
    initial = _decimal(_cell_value(utilisation, "B19"))
    addition = _decimal(_cell_value(utilisation, "Q19"))
    utilisation_verified = True
    previous = "B19"
    for row in (19, 58, 95):
        for column in range(2, 14):
            address = _colname(column) + str(row)
            if address == "B19":
                continue
            formula = (utilisation.get(address, {}).get("formula") or "").replace(" ", "").upper()
            if formula not in (f"={previous}+$Q$19/12", f"=+{previous}+$Q$19/12"):
                utilisation_verified = False
            previous = address
    if not utilisation_verified:
        _issue(result, "products.quantities", "utilization_formula", "Месячные формулы загрузки отличаются от проверенного профиля; объёмы автоматически не восстановлены.", _source(name, "Производ. с учетом загрузки", "B19:M19,B58:M58,B95:M95"))
    _evidence(result, "utilization", {"initial": None if initial is None else _number(initial),
                                     "annual_addition": None if addition is None else _number(addition)},
              _source(name, "Производ. с учетом загрузки", "B19,Q19"), "profile_input", "needs_confirmation")
    if fx is None or fx <= 0:
        _issue(result, "currency", "missing_fx", "Не найден явный положительный курс UZS/USD.", _source(name, "Стоим_проекта", "B38"))
    products = sheets["План производства"]
    for row in range(8, 48):
        product_name = _cell_value(products, f"A{row}")
        if not isinstance(product_name, str) or not product_name.strip():
            continue
        index = len(inputs["products"])
        prefix = f"products[{index}]"
        entry = {"name": product_name.strip(), "unit": "упаковка"}
        _evidence(result, prefix + ".name", entry["name"], _source(name, "План производства", f"A{row}"))
        raw_price = (products.get(f"F{row}", {}).get("formula") or "").replace(" ", "")
        price_match = re.fullmatch(r"=\(([0-9.]+)/\(\+курс\)\)\*\(\+ВНД!\$W\$41\)", raw_price)
        if price_match and fx and factor is not None and vat is not None and 0 <= vat <= 1:
            entry["price"] = _number(Decimal(price_match.group(1)) / fx * factor / (1 + vat))
            _evidence(result, prefix + ".price", entry["price"], _source(name, "План производства", f"F{row}"), "reconstructed_profile_arithmetic", "needs_confirmation", "Цена без НДС; валютный курс, коэффициент и НДС требуют подтверждения.")
        else:
            _issue(result, prefix + ".price", "price_formula", "Цена не извлечена: нет подтверждённой структуры формулы, курса, коэффициента или НДС.", _source(name, "План производства", f"F{row}"))
        cost_formula = (products.get(f"B{row}", {}).get("formula") or "").replace(" ", "")
        cost_match = re.fullmatch(r"=\+?Калькуляцияя!(F[0-9]+)/\(?\+?курс\)?", cost_formula)
        if cost_match and fx:
            cost = _profile_arithmetic(sheets["Калькуляцияя"], cost_match.group(1))
            if cost is not None:
                entry["unit_cost"] = _number(cost / fx)
                _evidence(result, prefix + ".unit_cost", entry["unit_cost"], _source(name, "Калькуляцияя", cost_match.group(1)), "reconstructed_profile_arithmetic", "needs_confirmation", "Только материалы; переменная зарплата и другие переменные расходы требуют отдельного включения.")
        elif fx:
            numerator = _literal_formula(products.get(f"B{row}", {}))
            if numerator is not None and products.get(f"B{row}", {}).get("formula"):
                entry["unit_cost"] = _number(numerator / fx)
                _evidence(result, prefix + ".unit_cost", entry["unit_cost"], _source(name, "План производства", f"B{row}"), "literal_cost_in_formula", "needs_confirmation", "Материальная себестоимость задана числом в формуле; состав затрат требует подтверждения.")
        first = _decimal(_cell_value(products, f"C{row}"))
        second = _decimal(_cell_value(products, f"D{row}"))
        rule = (products.get(f"E{row}", {}).get("formula") or "").replace(" ", "").upper()
        expected = f"=IF(C{row}=0,D{row}/12,AVERAGEA(C{row}:D{row})/12)"
        if rule == expected and first is not None and second is not None and initial is not None and addition is not None and utilisation_verified:
            with localcontext() as context:
                context.prec = 50
                base = second / 12 if first == 0 else (first + second) / 24
                candidates = [base * (initial + addition * month / 12) for month in range(36)]
                entry["quantities"] = [_profile_quantity(value) for value in candidates]
            _evidence(result, prefix + ".quantities", [_raw_number(value) for value in candidates], _source(name, "План производства", f"C{row}:E{row}"), "reconstructed_profile_arithmetic", "needs_confirmation", "Значения до округления. В расчётных входах применяется Decimal ROUND_HALF_EVEN: до 15 значащих цифр и до 12 знаков после запятой; цены и суммы явных входов не округляются.")
        else:
            _issue(result, prefix + ".quantities", "quantity_formula", "Объём не извлечён: проверьте годовые числа и правило месячной загрузки.", _source(name, "План производства", f"C{row}:E{row}"))
        inputs["products"].append(entry)
    _issue(result, "products.unit_cost", "material_only_cost", "Исходная себестоимость содержит материалы. Подтвердите, как добавить переменную зарплату и расходы цеха без повторного учёта в постоянных расходах.", _source(name, "План производства", "B8:B47"))
    for field, cell in (("receivable_days", "B6"), ("inventory_days", "B7"), ("payable_days", "B18")):
        value = _explicit(result, sheets.get("Раб_капит", {}), cell, field, name, "Раб_капит")
        if value is not None:
            inputs[field] = _parse_value(value, field)
    if "inventory_days" in inputs:
        _issue(result, "inventory_days", "inventory_basis", "В книге отдельно указаны дни сырья и готовой продукции. Подтвердите единые дни запасов для расчётной модели.", _source(name, "Раб_капит", "B7,B14"))
    discount = _explicit(result, sheets.get("РКЛ", {}), "AA6", "discount_rate", name, "РКЛ")
    if discount is not None:
        inputs["discount_rate"] = _parse_value(discount, "discount_rate")
        _issue(result, "discount_rate", "discount_loan_link", "Ставка дисконтирования в книге равна ставке нового кредита. Подтвердите самостоятельную ставку проекта.", _source(name, "ВНД", "C3"))
    profit = sheets.get("Приб_Убыт", {}).get("B17", {})
    match = re.fullmatch(r"=.*\*([0-9]+(?:\.[0-9]+)?)(%)?", (profit.get("formula") or "").replace(" ", ""))
    if match:
        inputs["tax_rate"] = _number(Decimal(match.group(1)) / (100 if match.group(2) else 1))
        _evidence(result, "tax_rate", inputs["tax_rate"], _source(name, "Приб_Убыт", "B17"), "literal_rate_in_formula", "needs_confirmation")
        _issue(result, "tax_rate", "tax_policy", "Подтвердите налоговую ставку и режим проекта; формула исходной книги не подтверждает актуальные правила.", _source(name, "Приб_Убыт", "B17"))
    payroll = []
    labour = sheets.get("Труд", {})
    for row in list(range(15, 33)) + list(range(37, 48)):
        role = _cell_value(labour, f"A{row}")
        count = _decimal(_cell_value(labour, f"B{row}"))
        salary = _decimal(_cell_value(labour, f"C{row}"))
        if role or count is not None or salary is not None:
            payroll.append({"role": role, "headcount": None if count is None else _number(count),
                            "salary": None if salary is None else _number(salary), "row": row})
            if count is not None and count > 0 and salary is None:
                _issue(result, "fixed_costs", "missing_salary", "У должности с указанной численностью отсутствует зарплата.", _source(name, "Труд", f"B{row}:C{row}"))
    _evidence(result, "payroll_candidates", payroll, _source(name, "Труд", "A15:C32,A37:C47"), "explicit_profile_rows", "needs_confirmation")
    utilities = []
    for row in (5, 6, 7, 8):
        record = {"name": _cell_value(labour, f"A{row}"), "unit": _cell_value(labour, f"B{row}"), "row": row}
        for column, field in (("C", "quantity"), ("D", "unit_price"), ("E", "monthly_amount")):
            cell = labour.get(f"{column}{row}", {})
            if cell.get("formula"):
                literal = _literal_formula(cell)
                record[field] = _number(literal / fx) if literal is not None and fx else None
            else:
                record[field] = _cell_value(labour, f"{column}{row}")
        utilities.append(record)
        if record["monthly_amount"] is None:
            _issue(result, "fixed_costs", "missing_utility", "Не указана однозначная месячная сумма коммунальной услуги; пустая ячейка не означает ноль.", _source(name, "Труд", f"E{row}"))
    _evidence(result, "utility_candidates", utilities, _source(name, "Труд", "A5:E8"), "profile_rows", "needs_confirmation")
    _issue(result, "fixed_costs", "overhead_scope", "Подтвердите постоянные и переменные расходы, соцначисления, имущественные налоги и пропущенные строки. Готовые итоги затрат не импортированы.", _source(name, "Цех_стоим", "C12:D24"))
    assets = []
    depreciation = sheets.get("амортизац", {})
    for row in (6, 8, 9, 11, 12):
        raw = _literal_formula(depreciation.get(f"B{row}", {}))
        rate = _decimal(_cell_value(depreciation, f"C{row}"))
        assets.append({"name": _cell_value(depreciation, f"A{row}"), "gross_value_usd": _number(raw / fx) if raw is not None and fx else None,
                       "annual_depreciation_rate": None if rate is None else _number(rate), "row": row})
    _evidence(result, "asset_candidates", assets, _source(name, "амортизац", "A6:C12"), "profile_raw_bases", "needs_confirmation")
    _issue(result, "assets", "asset_opening_capex", "Подтвердите остаточную стоимость, оставшийся срок службы и месяц ввода каждого актива, отдельно новые инвестиции. Базы проекта и амортизации различаются; готовые итоги содержат ошибки.", _source(name, "амортизац", "B6:C12"))
    credit = sheets.get("РКЛ", {})
    loan = {field: _cell_value(credit, address) for field, address in
            (("principal", "AA5"), ("annual_rate", "AA6"), ("grace_months", "AA7"), ("term_months", "AA8"))}
    loan["draw_months"] = [1, 19]
    loan["draw_amounts"] = [_cell_value(credit, "AA14"), _cell_value(credit, "AA32")]
    _evidence(result, "new_loan_candidate", loan, _source(name, "РКЛ", "AA5:AA10,AA14,AA32"), "profile_raw_terms", "needs_confirmation")
    _issue(result, "loans", "revolving_draw", "Подтвердите повторную выдачу кредита в месяце 19 и полный график процентов/погашений. В старых итогах повторная выдача пропущена.", _source(name, "РКЛ", "AA14,AA32"))
    old = sheets.get("ранее пол.кредиты", {})
    fx_old = {field: _cell_value(old, address) for field, address in
              (("UZS_per_USD", "C3"), ("UZS_per_EUR", "C4"), ("UZS_per_CNY", "C5"))}
    _evidence(result, "existing_debt_fx_candidates", fx_old, _source(name, "ранее пол.кредиты", "C3:C5"), "explicit_profile_rows", "needs_confirmation")
    _issue(result, "loans", "existing_debt_asof_fx", "Сверьте дату остатка, валютные курсы и действующие кредитные договоры. Графики могут заменять старые кредиты; суммы из разных документов не складываются автоматически.", _source(name, "ранее пол.кредиты", "D56,Y8:BH8,C3:C5"))
    _issue(result, "start", "forecast_start", "Укажите первый месяц прогноза. Дата графика существующего кредита не задаёт начало проекта.", _source(name))
    return inputs


def _docx(result: dict, content: bytes, name: str) -> None:
    with _archive(content) as archive:
        if "word/document.xml" not in archive.namelist():
            raise SourceReadError("Документ не содержит document.xml.")
        if any("vbaproject" in entry.casefold() for entry in archive.namelist()):
            raise SourceReadError("Документы с макросами не принимаются.")
        root = _xml(archive, "word/document.xml")
        word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        paragraphs = []
        length = 0
        available = min(MAX_NARRATIVE, MAX_TEXT - sum(len(item["text"]) for item in result["narratives"]))
        for node in root.iter(f"{{{word_ns}}}p"):
            text = "".join(n.text or "" for n in node.iter(f"{{{word_ns}}}t")).strip()
            if text:
                if length >= available:
                    break
                paragraphs.append(text[:min(4000, available - length)])
                length += len(paragraphs[-1])
                if length >= available:
                    break
        text = "\n".join(paragraphs)[:MAX_NARRATIVE]
        result["narratives"].append({"filename": name, "text": text, "source": _source(name),
                                    "status": "reference_only", "untrusted": True})
        _issue(result, "narratives", "narrative_reference", "Текст Word сохранён как справочный источник. Отрасль и утверждения нужно сверить; цифры отчёта рассчитываются заново.", _source(name), False)


def _pdf(result: dict, content: bytes, name: str) -> None:
    try:
        from pypdf import PdfReader, apply_configuration
    except ImportError:
        _issue(result, "documents", "pdf_reader_missing", "PDF сохранён, но библиотека чтения текста недоступна.", _source(name))
        return
    with apply_configuration(maximum_declared_stream_length=MAX_MEMBER_BYTES,
                             array_based_stream_maximum_output_length=MAX_MEMBER_BYTES,
                             zlib_maximum_output_length=MAX_MEMBER_BYTES,
                             lzw_maximum_output_length=MAX_MEMBER_BYTES,
                             run_length_maximum_output_length=MAX_MEMBER_BYTES,
                             image_maximum_buffer_size=MAX_MEMBER_BYTES,
                             jbig2_maximum_output_length=MAX_MEMBER_BYTES,
                             jbig2dec_binary=None,
                             page_tree_maximum_entries=1000,
                             xform_maximum_invocations_per_extraction=100):
        _read_pdf(result, content, name, PdfReader)


def _read_pdf(result: dict, content: bytes, name: str, reader_type) -> None:
    reader = reader_type(BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise SourceReadError("Зашифрованный PDF не читается автоматически.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise SourceReadError("В PDF допускается не более 100 страниц.")
    result["documents"][-1]["pages"] = len(reader.pages)
    length = sum(len(item["text"]) for item in result["narratives"])
    blank = 0
    for number, page in enumerate(reader.pages, 1):
        stream = page.get_contents()
        if stream is not None and len(stream.get_data()) > MAX_MEMBER_BYTES:
            raise SourceReadError("Слишком большой поток страницы PDF.")
        text = (page.extract_text() or "").strip()
        if not text:
            blank += 1
            continue
        remaining = min(MAX_TEXT - length, MAX_NARRATIVE)
        if remaining <= 0:
            break
        text = text[:remaining]
        length += len(text)
        result["narratives"].append({"filename": name, "text": text, "source": _source(name, page=number),
                                    "status": "reference_only", "untrusted": True})
    if blank:
        _issue(result, "documents", "pdf_text_missing", "Часть страниц PDF не содержит извлекаемого текста. OCR не выполнялся; проверьте изображения документа.", _source(name))
    _issue(result, "documents", "pdf_financial_reconciliation", "Числа из текстового слоя PDF не включены в расчёт автоматически. Проверьте валюту, единицы, версии договоров и действующие остатки; текстовый слой может содержать ошибки OCR.", _source(name), False)


def _reconcile_profile(result: dict, primary_name: str, primary_sheets: dict, supporting: list) -> None:
    """Exact-name reconciliation only; distinct codes/packaging are not guessed."""
    primary = primary_sheets["План производства"]
    identities = {}
    for index, product in enumerate(result["inputs"].get("products", [])):
        identities.setdefault(_label(product.get("name")), []).append((index, product))
    for name, sheets in supporting:
        for sheet, cells in sheets.items():
            if _label(sheet) == "sales-таннарх":
                _issue(result, "products.price", "secondary_price_basis", "У дополнительного прайс-листа не обозначены валюта, НДС и дата действия. Цены показаны для сверки и не подменяют основной источник.", _source(name, sheet))
                price_rows = list(range(6, 54)) + list(range(57, 76))
                secondary_identities = {}
                for row in price_rows:
                    identity_cell = cells.get(f"D{row}", {})
                    if not identity_cell.get("error"):
                        secondary_identities.setdefault(_label(identity_cell.get("value")), []).append(row)
                for row in price_rows:
                    identity_cell = cells.get(f"D{row}", {})
                    identity = _label(identity_cell.get("value")) if not identity_cell.get("error") else ""
                    matches = identities.get(identity, [])
                    secondary = _decimal(_cell_value(cells, f"E{row}"))
                    if len(matches) != 1 or len(secondary_identities.get(identity, [])) != 1 or secondary is None:
                        continue
                    index, product = matches[0]
                    source_row = next((row for row in range(8, 48) if _label(_cell_value(primary, f"A{row}")) == identity), None)
                    if source_row is None:
                        continue
                    raw = primary.get(f"F{source_row}", {}).get("formula") or ""
                    match = re.fullmatch(r"=\(([0-9.]+)/\(\+курс\)\)\*\(\+ВНД!\$W\$41\)", raw.replace(" ", ""))
                    if match and abs(Decimal(match.group(1)) - secondary) > Decimal("0.01"):
                        _issue(result, f"products[{index}].price", "secondary_price_conflict", "Цена основного файла до коэффициента отличается от дополнительного прайс-листа. Выберите действующую цену и её налоговую базу.", _source(name, sheet, f"E{row}"))
                        _evidence(result, f"products[{index}].secondary_price", {"primary_base": match.group(1), "secondary": _number(secondary)}, _source(name, sheet, f"D{row}:E{row}"), "exact_name_reconciliation", "needs_confirmation", "Название прайс-листа может быть сохранённым результатом внешней ссылки; коды и упаковки должны быть сверены.")
            elif _label(sheet) == "2027 га булимлар режаси":
                _issue(result, "products.quantities", "secondary_quantity_scope", "Дополнительный производственный план содержит отдельные каналы/варианты продукции. Единицы и связь кодов требуют сверки; строки не объединены автоматически.", _source(name, sheet))
                for column, primary_column, year in (("P", "C", "2026"), ("Q", "D", "2027")):
                    total = _decimal(_cell_value(cells, f"{column}61"))
                    if total is None:
                        total = _profile_arithmetic(cells, f"{column}61")
                    primary_total = _profile_arithmetic(primary, f"{primary_column}48")
                    if total is not None and primary_total is not None and abs(total - primary_total) > Decimal("0.01"):
                        _issue(result, "products.quantities", "secondary_quantity_total_conflict", f"Общий объём за {year} в дополнительном плане отличается от основной модели. Проверьте дополнительные позиции и упаковки.", _source(name, sheet, f"{column}61"))
                        _evidence(result, "secondary_quantity_total." + year, {"primary": _number(primary_total), "secondary": _number(total)}, _source(name, sheet, f"{column}61"), "reconstructed_totals", "needs_confirmation")


def extract_sources(files: list[dict]) -> dict:
    """Return inputs/evidence/issues/documents/narratives without I/O or mutation.

    `content` is authoritative; `path` is deliberately never read. Conflicting
    complete models are not silently merged. All source reading is local.
    """
    result = {"inputs": {}, "evidence": [], "issues": [], "documents": [], "narratives": []}
    if not isinstance(files, list) or len(files) > MAX_FILES:
        _issue(result, "documents", "file_limit", "Передайте список не более 150 файлов.")
        return result
    candidates = []
    total_cells = 0
    total_bytes = 0
    profile_sheets = {}
    supporting = []
    text_inputs = {}
    for item in files:
        if not isinstance(item, dict):
            _issue(result, "documents", "file_format", "Некорректное описание файла.")
            continue
        name = str(item.get("name") or "document")
        name = "/".join(part for part in name.replace("\\", "/").split("/") if part not in ("", ".", ".."))[-240:]
        content = item.get("content")
        suffix = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
        document = {"filename": name, "format": suffix, "bytes": len(content) if isinstance(content, bytes) else 0,
                    "status": "received"}
        result["documents"].append(document)
        if not isinstance(content, bytes) or not content or len(content) > MAX_FILE_BYTES:
            document["status"] = "rejected"
            _issue(result, "documents", "file_size", "Файл должен содержать данные размером не более 20 МБ.", _source(name))
            continue
        total_bytes += len(content)
        if total_bytes > MAX_ZIP_BYTES:
            document["status"] = "rejected"
            _issue(result, "documents", "total_size", "Суммарный размер источников превышает 100 МБ.", _source(name))
            continue
        try:
            if name.rsplit("/", 1)[-1].casefold() == "project.json":
                candidates.append((0, name, _json_model(result, content, name)))
            elif suffix in ("xlsx", "xltx"):
                sheets, notes = _xlsx(content)
                total_cells += sum(len(cells) for cells in sheets.values())
                if total_cells > MAX_CELLS:
                    raise SourceReadError("Суммарное число ячеек источников превышает допустимый предел.")
                document["sheets"] = len(sheets)
                if "external_links" in notes:
                    _issue(result, "documents", "external_links", "Книга содержит внешние ссылки. Они не открывались; сохранённые результаты формул не являются свежим пересчётом.", _source(name), False)
                model = _normalised_xlsx(result, sheets, name)
                if model is not None:
                    candidates.append((1, name, model))
                    document["profile"] = "normalised_template"
                else:
                    model = _uzgermed(result, sheets, name)
                    if model is not None:
                        candidates.append((2, name, model))
                        profile_sheets[name] = sheets
                    else:
                        selected_sheets = {sheet: cells for sheet, cells in sheets.items()
                                           if _label(sheet) in ("sales-таннарх", "2027 га булимлар режаси")}
                        if selected_sheets:
                            supporting.append((name, selected_sheets))
                        _issue(result, "documents", "supporting_workbook", "Таблица сохранена как подтверждающий источник; для автоматического расчёта нужен выбранный основной файл или лист «Параметры».", _source(name), False)
            elif suffix == "docx":
                _docx(result, content, name)
            elif suffix == "pdf":
                _pdf(result, content, name)
            elif suffix == "zip":
                with _archive(content) as archive:
                    document["entries"] = len(archive.infolist())
                _issue(result, "documents", "archive_reference", "ZIP сохранён как исходный документ. Для извлечения текста загрузите нужные договоры отдельными PDF; суммы рамочных договоров не являются планом продаж.", _source(name), False)
            elif suffix in ("png", "jpg", "jpeg", "webp", "gif"):
                document["status"] = "reference_only"
                _issue(result, "documents", "image_reference", "Изображение сохранено как источник. Распознавание текста и финансовых чисел не выполнялось.", _source(name), False)
            elif suffix in ("txt", "csv"):
                document["status"] = "reference_only"
                stems = {"описание": "project_description", "технология": "technology", "рынок": "market",
                         "местоположение": "location", "назначение инвестиций": "investment_purpose"}
                stem = _label(name.rsplit("/", 1)[-1].rsplit(".", 1)[0])
                if suffix == "txt" and stem in stems:
                    text = content.decode("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig").strip()
                    if len(text) > 10000:
                        raise SourceReadError("Описание должно содержать не более 10000 символов.")
                    if text:
                        text_inputs.setdefault(stems[stem], []).append((name, text))
                else:
                    _issue(result, "documents", "text_reference", "Текстовый файл сохранён как справочный источник. Финансовые таблицы автоматически читаются из нормализованного XLSX.", _source(name), False)
            else:
                document["status"] = "unsupported"
                _issue(result, "documents", "unsupported_format", "Поддерживаются XLSX/XLTX, DOCX, PDF, ZIP и project.json.", _source(name), False)
            if document["status"] == "received":
                document["status"] = "parsed"
        except Exception as error:
            document["status"] = "rejected"
            # Parser errors can include raw document text: never expose it.
            reason = str(error) if isinstance(error, ValueError) and type(error) is SourceReadError else "Не удалось безопасно прочитать структуру документа."
            _issue(result, "documents", "parse_error", reason[:240], _source(name))
    if candidates:
        candidates.sort(key=lambda candidate: candidate[0])
        priority = candidates[0][0]
        primary = [candidate for candidate in candidates if candidate[0] == priority]
        if len(primary) > 1 and any(candidate[2] != primary[0][2] for candidate in primary[1:]):
            _issue(result, "model", "multiple_primary_models", "Найдено несколько основных расчётных моделей. Выберите одну; их входы не объединены.")
        else:
            _, name, result["inputs"] = primary[0]
            # Explicit canonical drivers take precedence. Historical workbooks
            # remain inspectable sources, not blockers for an approved model.
            for item in result["issues"]:
                if item.get("source", {}).get("filename") not in (None, name):
                    item["requires_confirmation"] = False
            for item in result["evidence"]:
                if item.get("source", {}).get("filename") != name:
                    item["status"] = "reference_only"
            if priority == 2:
                _reconcile_profile(result, name, profile_sheets[name], supporting)
            if len(candidates) > 1:
                _issue(result, "model", "source_precedence", f"Основные входы взяты из «{name}». Другие расчётные книги сохранены как справочные источники; их входы не смешаны.", _source(name), False)
    else:
        _issue(result, "model", "no_model", "Не найден расчётный источник. Добавьте нормализованный Excel с листом «Параметры» и таблицами продукции, активов и кредитов.")
    for field, entries in text_inputs.items():
        if field in result["inputs"]:
            for name, value in entries:
                _evidence(result, field, value, _source(name), "explicit_text", "reference_only")
            continue
        if len({value for _, value in entries}) > 1:
            _issue(result, field, "text_conflict", "Найдено несколько разных текстовых описаний одного раздела. Выберите действующий источник.")
        else:
            name, value = entries[0]
            result["inputs"][field] = value
            _evidence(result, field, value, _source(name), "explicit_text")
    for field in TEXT_FIELDS & result["inputs"].keys():
        value = result["inputs"][field]
        if not isinstance(value, str) or len(value) > 10000:
            _issue(result, field, "text_limit", "Текст раздела должен содержать не более 10000 символов.")
            del result["inputs"][field]
    for issue in validate_model(result["inputs"]):
        if not any(item["field"] == issue["field"] and item["code"] == "missing_or_invalid" for item in result["issues"]):
            _issue(result, issue["field"], "missing_or_invalid", issue["message"])
    return result
