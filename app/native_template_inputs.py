"""Explicit, reviewable numeric inputs for the original UZGERMED workbook.

Source workbooks supply proposals, never automatic writes. Only documented
scalar inputs and a few exact literal-bearing formulas are writable. The ZIP
parts and all other cell XML survive unchanged; no formula cache is an input.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
import math
import posixpath
import re
from typing import Any
from zipfile import ZipFile

from .business_sources import (
    _archive, _xml, _xlsx, MAX_FILE_BYTES, MAX_FILES, MAX_ZIP_BYTES,
    MAX_NUMERIC_TEXT, MIN_NUMERIC_EXPONENT,
    NS, DOC_REL_NS, SourceReadError,
)
from .native_uzgermed import PARAMETERS, READ_ONLY_PARAMETERS, _recognized
from .native_workbook import _patch_sheet


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_UNIT_FORMULA = re.compile(r"=\+?(?P<input>" + _NUMBER + r")/(?:курс|\(\+курс\))\Z")
_PRICE_FORMULA = re.compile(
    r"=\((?P<input>" + _NUMBER + r")/\(\+курс\)\)\*\(\+ВНД!\$W\$41\)\Z"
)
_NET_FORMULA = re.compile(
    r"=\((?P<input>" + _NUMBER + r")-(?P<deduction>" + _NUMBER + r")\)/курс\*1000\Z"
)
_PAYROLL_RATE = {
    "D34": re.compile(r"=D33\*(?P<input>" + _NUMBER + r")%\Z"),
    "D50": re.compile(r"=D48\*(?P<input>" + _NUMBER + r")%\Z"),
}
_CELL = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,4})\Z")
_MAX_VALUE = Decimal("1e15")


def _normal(value: Any) -> str:
    """Exact identity normalization; punctuation and pack sizes stay significant."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold().replace("ё", "е")


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        token = str(value)
        if len(token) > MAX_NUMERIC_TEXT:
            return None
        number = Decimal(token)
        result = float(number)
        if (not number.is_finite() or abs(number) > _MAX_VALUE or
                not math.isfinite(result) or
                (number and (not result or number.adjusted() < MIN_NUMERIC_EXPONENT))):
            return None
        return result
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _decimal_literal(value: str) -> Decimal | None:
    try:
        number = Decimal(value)
        if _numeric(number) is not None:
            return number
    except (InvalidOperation, ValueError):
        pass
    return None


def numbers_equivalent(left: Any, right: Any) -> bool:
    """Recognize bounded Excel binary tails for review, never for overwriting.

    Decimal literals and source caches sometimes differ by a few last binary
    places after Excel conversion. Thirty-two ULPs cover that round trip; an
    absolute cap prevents the tolerance growing with a large financial amount.
    This is far below one hundredth of a currency unit, without relative
    tolerances or decimal rounding. Missing/invalid inputs are never equivalent.
    """
    first, second = _numeric(left), _numeric(right)
    if first is None or second is None:
        return False
    if first == second:
        return True
    tolerance = min(32 * max(math.ulp(first), math.ulp(second)), 1e-7)
    return abs(first - second) <= tolerance


def _address(address: str) -> tuple[int, int]:
    match = _CELL.fullmatch(address)
    if not match:
        raise SourceReadError("Недопустимый адрес ячейки шаблона.")
    column = 0
    for character in match[1]:
        column = column * 26 + ord(character) - 64
    row = int(match[2])
    if column > 16384 or row > 10000:
        raise SourceReadError("Адрес выходит за пределы поддерживаемого шаблона.")
    return column, row


def _layout(raw: bytes) -> dict:
    """Cell kinds/array extents are necessary even when a follower lacks an f node."""
    result = {}
    with _archive(raw) as archive:
        book = _xml(archive, "xl/workbook.xml")
        relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
        rels = {node.attrib["Id"]: node.attrib.get("Target", "") for node in relationships}
        for sheet in book.findall("s:sheets/s:sheet", NS):
            name = sheet.attrib["name"]
            if name in result:
                raise SourceReadError("Названия листов шаблона должны быть уникальными.")
            target = rels.get(sheet.attrib.get("{" + DOC_REL_NS + "}id"), "")
            path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            if not path.startswith("xl/") or "/../" in path:
                raise SourceReadError("Некорректная ссылка на лист шаблона.")
            tree = _xml(archive, path)
            cells, arrays = {}, []
            for node in tree.findall("s:sheetData/s:row/s:c", NS):
                address = node.attrib.get("r", "")
                _address(address)
                if address in cells:
                    raise SourceReadError("Повторный адрес ячейки в шаблоне.")
                formula = node.find("s:f", NS)
                cells[address] = {
                    "kind": node.attrib.get("t", "n"),
                    "formula_attributes": dict(formula.attrib) if formula is not None else {},
                    "has_formula": formula is not None,
                    "blank": formula is None and node.find("s:v", NS) is None and node.find("s:is", NS) is None,
                }
                if formula is not None and formula.attrib.get("t") == "array":
                    ref = formula.attrib.get("ref", address)
                    start, _, end = ref.replace("$", "").partition(":")
                    first, last = _address(start), _address(end or start)
                    if first[0] > last[0] or first[1] > last[1]:
                        raise SourceReadError("Некорректная область формулы массива.")
                    arrays.append((first[0], first[1], last[0], last[1]))
            result[name] = {"path": path, "cells": cells, "arrays": arrays}
    return result


def _load(raw: bytes) -> tuple[dict, dict, list]:
    if not isinstance(raw, bytes) or len(raw) > MAX_FILE_BYTES:
        raise SourceReadError("Шаблон должен быть Excel-файлом допустимого размера.")
    sheets, notes = _xlsx(raw)
    if not _recognized(sheets):
        raise SourceReadError("Нужен исходный финансовый шаблон UZGERMED с узнаваемой структурой.")
    layout = _layout(raw)
    return sheets, layout, notes


def _warning(warnings: list, code: str, message: str, **location) -> None:
    warnings.append({"code": code, "message": message, **location})


def _inside_array(layout: dict, address: str) -> bool:
    column, row = _address(address)
    return any(a <= column <= c and b <= row <= d for a, b, c, d in layout["arrays"])


def _inputs(sheets: dict, layout: dict) -> tuple[list, dict, list]:
    items, bindings, warnings = [], {}, []

    def add(sheet, cell, group, label, unit, low=0, high=1e15, *,
            mode="scalar", required=False, readonly=False, integer=False, period=""):
        if sheet not in layout:
            return
        node = layout[sheet]["cells"].get(cell)
        if node is None and not required:
            return
        source = sheets[sheet].get(cell, {})
        key = sheet + "!" + cell
        formula = source.get("formula")
        value = None
        binding = None
        protected = node is None or _inside_array(layout[sheet], cell) or bool(node["formula_attributes"])
        if not protected and mode == "scalar" and not node["has_formula"]:
            # Date-formatted numeric cells have no numeric_text; never convert a
            # serial date or a boolean into a financial input.
            if node["kind"] in ("n", "") and not source.get("error"):
                value = _numeric(_decimal_literal(source["numeric_text"])) if source.get("numeric_text") is not None else None
                if value is not None or node["blank"]:
                    binding = {"mode": "scalar"}
        elif not protected and formula:
            pattern = {"unit": _UNIT_FORMULA, "price": _PRICE_FORMULA,
                       "net": _NET_FORMULA}.get(mode)
            if mode == "payroll_rate":
                pattern = _PAYROLL_RATE.get(cell)
            match = pattern.fullmatch(formula) if pattern else None
            if match:
                literal = _decimal_literal(match["input"])
                if literal is not None:
                    effective = literal
                    if mode == "net":
                        deduction = _decimal_literal(match["deduction"])
                        effective = literal - deduction if deduction is not None else None
                    elif mode == "payroll_rate":
                        effective = literal / 100
                    value = _numeric(effective)
                    if value is not None:
                        binding = {"mode": mode, "formula": formula,
                                   "span": match.span("input")}
                        if mode == "net":
                            binding["deduction"] = deduction
        # A documented reference-only parameter may display its original
        # scalar, while derived formulas never display cached results as inputs.
        if readonly and not formula and source.get("numeric_text") is not None:
            value = _numeric(_decimal_literal(source["numeric_text"]))
        editable = binding is not None and not readonly
        item = {"key": key, "group": group, "label": label, "value": value,
                "unit": unit, "sheet": sheet, "cell": cell, "editable": editable,
                "min": low, "max": high, "required": bool(required),
                "proposals": [], "requires_decision": False}
        if integer:
            item["integer"] = True
        if period:
            item["period"] = period
        if mode in ("unit", "price", "net", "payroll_rate"):
            item["help"] = "Меняется исходное число внутри формулы; расчётная формула сохраняется."
        if mode == "price":
            item["help"] = "Базовая цена в UZS до коэффициента ВНД!W41. Формула переводит в USD по курсу и умножает на этот коэффициент. НДС автоматически не пересчитывается: проверьте, что прайс содержит базовую цену."
        if mode == "net":
            item["help"] = "Чистый оборотный капитал: строка 390 минус строка 600, тыс. сум. Меняется чистая сумма в числителе исходной формулы."
        if not editable and not readonly:
            item["help"] = "Ввод недоступен: отсутствующая ячейка или расчётная/неподдерживаемая формула. Исправьте исходный Excel."
            if required or node is not None:
                _warning(warnings, "input_unavailable", item["help"], sheet=sheet, cell=cell, keys=[key])
        elif editable and value is None:
            _warning(warnings, "missing_input", "Не заполнено поле: " + label, sheet=sheet, cell=cell, keys=[key])
        if value is not None and not low <= value <= high:
            _warning(warnings, "original_out_of_range", "Исходное значение вне допустимого диапазона: " + label,
                     sheet=sheet, cell=cell, keys=[key])
        items.append(item)
        if editable:
            bindings[key] = {**binding, "item": item}

    for sheet, cell, label, unit, low, high, step, _ in PARAMETERS:
        add(sheet, cell, "Основные условия", label, unit, low, high, required=True,
            readonly=(sheet, cell) in READ_ONLY_PARAMETERS, integer=bool(step))
    add("ВНД", "W41", "Цены", "Общий коэффициент базовых цен", "коэффициент", 0, 1000)
    if items and items[-1]["key"] == "ВНД!W41":
        items[-1]["help"] = "Общий множитель для базовых цен всех препаратов. Это исходный коэффициент модели; перенос прайса сам его не меняет."
    plan = sheets.get("План производства", {})
    for row in range(8, 48):
        name = str(plan.get("A" + str(row), {}).get("value") or "").strip()
        if not name:
            continue
        product_unit = str(plan.get("C7", {}).get("value") or "").strip()
        price_unit = "UZS/упаковка" if _normal(product_unit) in ("упаковок", "упаковка", "упак.", "упак") else "UZS/" + (product_unit or "единица не указана")
        for column in ("C", "D"):
            header = str(plan.get(column + "6", {}).get("value") or "").strip()
            year = re.search(r"\b(?:19|20)\d{2}\b", header)
            period = year[0] if year else ""
            unit = str(plan.get(column + "7", {}).get("value") or "").strip() or "единица не указана"
            add("План производства", column + str(row), "Производство", name + " — " + (period or column),
                unit, 0, 1e12, period=period)
        add("План производства", "F" + str(row), "Цены", name + " — базовая цена", price_unit, 0, 1e12, mode="price")
        add("План производства", "B" + str(row), "Себестоимость", name + " — базовая себестоимость", price_unit, 0, 1e12, mode="unit")

    for cell, label in {"B10": "Оборудование", "B14": "Транспорт", "B18": "Здания и сооружения",
                        "B21": "Прочие капитальные затраты"}.items():
        add("Стоим_проекта", cell, "Активы и инвестиции", label + " — исходная стоимость", "UZS", mode="unit")
    add("Стоим_проекта", "B28", "Оборотный капитал", "Чистый оборотный капитал (390−600)", "тыс. UZS", -1e15, 1e15, mode="net")
    add("Стоим_проекта", "B33", "Активы и инвестиции", "Прочие затраты проекта", "USD")
    for row in (5, 6, 7, 8, 9, 10, 11, 12, 13):
        caption = str(sheets.get("амортизац", {}).get("A" + str(row), {}).get("value") or "Группа " + str(row)).strip()
        add("амортизац", "C" + str(row), "Амортизация", caption + " — годовая норма", "доля", 0, 1)
    for row in (6, 8, 9, 11, 12):
        caption = str(sheets.get("амортизац", {}).get("A" + str(row), {}).get("value") or "Группа " + str(row)).strip()
        add("амортизац", "B" + str(row), "Амортизация", caption + " — стоимость для амортизации", "UZS", mode="unit")
    for row in (*range(15, 33), *range(37, 48)):
        caption = str(sheets.get("Труд", {}).get("A" + str(row), {}).get("value") or "").strip()
        if not caption:
            continue
        add("Труд", "B" + str(row), "Персонал", caption + " — численность", "человек", 0, 1e6, integer=True)
        add("Труд", "C" + str(row), "Персонал", caption + " — зарплата за месяц", "USD/мес.", 0, 1e9)
    for row in (5, 6, 8):
        label = str(sheets.get("Труд", {}).get("A" + str(row), {}).get("value") or "Коммунальные расходы " + str(row)).strip()
        add("Труд", "D" + str(row), "Коммунальные расходы", label + " — тариф", "UZS/ед.", mode="unit")
        add("Труд", "E" + str(row), "Коммунальные расходы", label + " — затраты за месяц", "UZS/мес.", mode="unit")
    for cell in ("D34", "D50"):
        add("Труд", cell, "Персонал", "Социальный налог — " + ("производство" if cell == "D34" else "АУП"),
            "доля", 0, 1, mode="payroll_rate")
    for row, label in {13: "Готовая продукция: экспорт", 14: "Готовая продукция: местные продажи",
                       15: "Запасы импортных запчастей", 18: "Оплата местного сырья", 19: "Оплата импортного сырья"}.items():
        add("Раб_капит", "B" + str(row), "Оборотный капитал", label, "дней", 1, 365, integer=True)
    for row in (6, 7, *range(12, 25)):
        caption = str(sheets.get("Цех_стоим", {}).get("A" + str(row), {}).get("value") or "Группа " + str(row)).strip()
        add("Цех_стоим", "C" + str(row), "Операционные расходы", caption + " — постоянная доля", "доля", 0, 1)
    for row in (35, 36, 40, 41):
        caption = str(sheets.get("Затраты_на сырье", {}).get("A" + str(row), {}).get("value") or "Расходы " + str(row)).strip()
        add("Затраты_на сырье", "B" + str(row), "Операционные расходы", caption + " — доля выручки", "доля", 0, 1)
    for start in (16, 39, 62):
        for index, column in enumerate("BCDEFGHIJKLM", 1):
            add("Приб_Убыт", column + str(start), "Налоги", "Налог на имущество — месяц " + str((start - 16) // 23 * 12 + index), "USD/мес.")
    return items, bindings, warnings


def _scalar(cells: dict, address: str) -> float | None:
    cell = cells.get(address, {})
    if cell.get("formula") or cell.get("error") or cell.get("numeric_text") is None:
        return None
    return _numeric(_decimal_literal(cell["numeric_text"]))


def _proposal(item: dict, value: float, source: str, digest: str, sheet: str,
              cell: str, unit: str, period: str, *, confirm=False, **evidence) -> None:
    if not item["editable"] or _numeric(value) is None:
        return
    payload = {"key": item["key"], "value": value, "source": source,
               "source_sha256": digest, "sheet": sheet, "cell": cell,
               "unit": unit, "period": period, **evidence}
    proposal_id = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    if any(p["id"] == proposal_id for p in item["proposals"]):
        return
    proposal = {"id": proposal_id, **{key: value for key, value in payload.items() if key != "key"}}
    proposal["equivalent_to_original"] = numbers_equivalent(item["value"], value)
    if confirm:
        proposal["basis_confirmation"] = True
    item["proposals"].append(proposal)
    # Equal evidence does not force a row-by-row confirmation merely to keep
    # the uploaded original. Adopting a source remains an explicit API choice.
    item["requires_decision"] = bool(item["requires_decision"] or
                                      not proposal["equivalent_to_original"] or len(item["proposals"]) > 1)


def _products(sheets: dict, warnings: list) -> dict:
    names = defaultdict(list)
    for row in range(8, 48):
        cell = sheets.get("План производства", {}).get("A" + str(row), {})
        if cell.get("error"):
            continue
        name = _normal(cell.get("value"))
        if name:
            names[name].append(row)
    for name, rows in names.items():
        if len(rows) > 1:
            _warning(warnings, "duplicate_template_product", "Повторное наименование в шаблоне: " + name + ". Сопоставление требует уникального наименования/SKU.",
                     sheet="План производства", cell=",".join("A" + str(row) for row in rows))
    return {name: rows[0] for name, rows in names.items() if len(rows) == 1}


def _production(source_sheets, source, digest, products, items, warnings) -> bool:
    recognized = False
    for sheet, cells in source_sheets.items():
        if _normal(cells.get("B2", {}).get("value")) != "наименование препарата":
            continue
        recognized = True
        columns = {}
        for column in ("P", "Q"):
            header = str(cells.get(column + "2", {}).get("value") or "")
            year = re.search(r"\b(?:19|20)\d{2}\b", header)
            if year and ("объем" in _normal(header) or "объём" in header.casefold()):
                columns[column] = year[0]
            else:
                _warning(warnings, "production_period_unknown", "В заголовке объёма нет однозначного года. Значения не предлагаются.", source=source, sheet=sheet, cell=column + "2")
        source_names = defaultdict(list)
        for address, cell in cells.items():
            match = re.fullmatch(r"B([3-9]|[1-9][0-9]{1,3})", address)
            if match and not cell.get("error") and isinstance(cell.get("value"), str):
                source_names[_normal(cell["value"])].append(int(match[1]))
        matched_keys = []
        for name, rows in source_names.items():
            if not name or "итого" in name or "жами" in name:
                continue
            if name not in products:
                _warning(warnings, "unmatched_production_product", "В шаблоне нет уникального точного соответствия: " + name, source=source, sheet=sheet, cell="B" + str(rows[0]))
                continue
            row = products[name]
            if len(rows) > 1:
                _warning(warnings, "duplicate_production_product", "Повторное наименование в плане производства; выберите строку вручную: " + name, source=source, sheet=sheet, cell=",".join("B" + str(r) for r in rows))
            for column, period in columns.items():
                candidates = [items.get("План производства!" + c + str(row)) for c in ("C", "D")]
                targets = [item for item in candidates if item and item.get("period") == period]
                if len(targets) != 1:
                    _warning(warnings, "production_period_mismatch", "Год источника не совпадает с единственным годом шаблона: " + period, source=source, sheet=sheet, cell=column + "2")
                    continue
                item = targets[0]
                if _normal(item["unit"]) not in ("упаковок", "упаковка", "упак.", "упак"):
                    _warning(warnings, "production_unit_mismatch", "Единица шаблона отличается от упаковок или не указана. Объём не предлагается.", source=source, sheet=sheet, keys=[item["key"]])
                    continue
                # This source layout has no explicit unit column. A source
                # choice must confirm packaging units, including equal values.
                matched_keys.append(item["key"])
                for source_row in rows:
                    address = column + str(source_row)
                    value = _scalar(cells, address)
                    if value is None or not item["min"] <= value <= item["max"]:
                        _warning(warnings, "production_value_unavailable", "Объём пустой, вне допустимого диапазона или вычисляется формулой; нужен явный ввод.", source=source, sheet=sheet, cell=address, keys=[item["key"]])
                        continue
                    _proposal(item, value, source, digest, sheet, address, "упаковка", period, confirm=True)
        if matched_keys:
            _warning(warnings, "production_units_confirmation", "План производства не содержит явной единицы в колонках объёма. Подтвердите, что значения — упаковки за указанный год.",
                     source=source, sheet=sheet, keys=sorted(set(matched_keys)))
    return recognized


def _prices(source_sheets, source, digest, products, items, warnings) -> bool:
    recognized = False
    for sheet, cells in source_sheets.items():
        if (_normal(cells.get("D4", {}).get("value")) != "номенклатура.наименование" or
                _normal(cells.get("E4", {}).get("value")) != "sales price"):
            continue
        recognized = True
        names = defaultdict(list)
        for address, cell in cells.items():
            match = re.fullmatch(r"D([5-9]|[1-9][0-9]{1,3})", address)
            if match and not cell.get("error") and isinstance(cell.get("value"), str):
                names[_normal(cell["value"])].append(int(match[1]))
        keys = []
        for name, rows in names.items():
            if not name or "итого" in name or "жами" in name:
                continue
            if name not in products:
                _warning(warnings, "unmatched_price_product", "В шаблоне нет уникального точного соответствия прайсу: " + name, source=source, sheet=sheet, cell="D" + str(rows[0]))
                continue
            item = items.get("План производства!F" + str(products[name]))
            if not item or not item["editable"]:
                continue
            if item["unit"] != "UZS/упаковка":
                _warning(warnings, "price_unit_mismatch", "Единица продукции в шаблоне отличается от упаковки или не указана. Цены прайса не предлагаются.", source=source, sheet=sheet, keys=[item["key"]])
                continue
            keys.append(item["key"])
            if len(rows) > 1:
                _warning(warnings, "duplicate_price_product", "У продукта несколько цен/каналов. Выберите конкретную цену: " + name,
                         source=source, sheet=sheet, cell=",".join("E" + str(row) for row in rows), keys=[item["key"]])
            for row in rows:
                address = "E" + str(row)
                value = _scalar(cells, address)
                if value is None or not item["min"] <= value <= item["max"]:
                    _warning(warnings, "price_value_unavailable", "Цена пустая, вне допустимого диапазона или вычисляется формулой; cached результат не переносится.", source=source, sheet=sheet, cell=address, keys=[item["key"]])
                    continue
                _proposal(item, value, source, digest, sheet, address, "UZS/упаковка", "не указан", confirm=True,
                          identity_cell="D" + str(row), sku=str(cells.get("C" + str(row), {}).get("value") or ""))
                if cells.get("D" + str(row), {}).get("formula"):
                    _warning(warnings, "cached_source_identity", "Наименование прайса сохранено из внешней формулы. Подтвердите соответствие продукта; внешние ссылки не обновлялись.", source=source, sheet=sheet, cell="D" + str(row), keys=[item["key"]])
        if keys:
            _warning(warnings, "price_basis_confirmation", "У прайса нет явной валюты, единицы упаковки, даты и признака НДС. Перед выбором источника подтвердите UZS за упаковку, период и базу цены относительно коэффициента шаблона.",
                     source=source, sheet=sheet, keys=sorted(set(keys)))
    return recognized


def _balance(source_sheets, source, digest, items, warnings) -> bool:
    recognized = False
    item = items.get("Стоим_проекта!B28")
    for sheet, cells in source_sheets.items():
        codes = defaultdict(list)
        for address, cell in cells.items():
            match = re.fullmatch(r"C([1-9][0-9]{0,3})", address)
            if match and not cell.get("formula") and not cell.get("error"):
                code = str(cell.get("value", "")).strip().removesuffix(".0")
                if code in ("390", "600"):
                    codes[code].append(int(match[1]))
        if "390" not in codes or "600" not in codes:
            continue
        recognized = True
        if len(codes["390"]) != 1 or len(codes["600"]) != 1:
            _warning(warnings, "duplicate_balance_code", "В Форме 1 строки 390/600 повторяются. Чистый оборотный капитал нужно ввести вручную.", source=source, sheet=sheet, keys=["Стоим_проекта!B28"])
            continue
        unit = _normal(cells.get("C2", {}).get("value"))
        if "тыс" not in unit or "сум" not in unit:
            _warning(warnings, "balance_unit_unknown", "Форма 1 должна явно указывать тыс. сум. Значение не предлагается.", source=source, sheet=sheet, cell="C2", keys=["Стоим_проекта!B28"])
            continue
        asset_cell, debt_cell = "E" + str(codes["390"][0]), "E" + str(codes["600"][0])
        asset, debt = _scalar(cells, asset_cell), _scalar(cells, debt_cell)
        if asset is None or debt is None or asset < 0 or debt < 0:
            _warning(warnings, "balance_value_unavailable", "Нужны явные числовые остатки строк 390 и 600 на конец периода в колонке E.", source=source, sheet=sheet, cell=asset_cell + "," + debt_cell, keys=["Стоим_проекта!B28"])
            continue
        if item and item["editable"]:
            _proposal(item, float(Decimal(str(asset)) - Decimal(str(debt))), source, digest,
                      sheet, asset_cell + "-" + debt_cell, "тыс. UZS", "на конец отчётного периода", confirm=True,
                      components=[{"cell": asset_cell, "value": asset}, {"cell": debt_cell, "value": debt}])
            _warning(warnings, "balance_period_confirmation", "Подтвердите отчётную дату Формы 1 и применение остатков на конец периода к старту проекта.", source=source, sheet=sheet, keys=[item["key"]])
    return recognized


def describe_inputs(raw: bytes, files: list | None = None) -> dict:
    """Describe original values and separately labelled, explicitly chosen evidence."""
    try:
        sheets, layout, notes = _load(raw)
    except Exception:
        # Parser diagnostics are deliberately public and exclude uploaded data.
        return {"items": [], "warnings": [{"code": "unsupported_template", "message": "Не удалось безопасно прочитать исходный шаблон UZGERMED."}]}
    items, _, warnings = _inputs(sheets, layout)
    by_key = {item["key"]: item for item in items}
    products = _products(sheets, warnings)
    if notes:
        _warning(warnings, "template_external_links", "Шаблон содержит внешние ссылки. Они сохраняются и не обновляются из сети.")
    if files is None:
        files = []
    if not isinstance(files, (list, tuple)) or len(files) > MAX_FILES:
        _warning(warnings, "too_many_sources", "Недопустимый список подтверждающих документов.")
        files = []
    seen, total, expanded_total = set(), 0, 0
    for file in files:
        if not isinstance(file, dict):
            _warning(warnings, "invalid_source", "Документ должен содержать имя и загруженные байты.")
            continue
        source = str(file.get("name") or "Документ")[:240]
        content = file.get("content", file.get("raw"))
        # Paths are metadata; never read an uploaded path from this API.
        if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
            _warning(warnings, "source_unavailable", "Загруженные байты документа отсутствуют или превышают допустимый размер.", source=source)
            continue
        total += len(content)
        if total > MAX_ZIP_BYTES:
            _warning(warnings, "source_total_limit", "Общий объём источников превышает допустимый размер.")
            break
        digest = sha256(content).hexdigest()
        if digest == sha256(raw).hexdigest() or digest in seen:
            continue
        seen.add(digest)
        if not source.casefold().endswith((".xlsx", ".xltx")):
            _warning(warnings, "manual_source", "Документ используется для проверки вручную; автоматический числовой перенос для этого формата не поддерживается.", source=source)
            continue
        try:
            with _archive(content) as source_archive:
                expanded_total += sum(member.file_size for member in source_archive.infolist())
            if expanded_total > MAX_ZIP_BYTES:
                _warning(warnings, "source_total_limit", "Общий распакованный объём Excel-источников превышает допустимый размер.")
                break
            source_sheets, _ = _xlsx(content)
            # Reject ambiguous duplicate cells/array-cache inputs in sources,
            # too. These layouts supply evidence, never formula execution.
            source_layout = _layout(content)
            for sheet, meta in source_layout.items():
                for address, cell in source_sheets[sheet].items():
                    if _inside_array(meta, address):
                        cell["formula"] = cell.get("formula") or "=ARRAY_SOURCE"
            found = _production(source_sheets, source, digest, products, by_key, warnings)
            found = _prices(source_sheets, source, digest, products, by_key, warnings) or found
            found = _balance(source_sheets, source, digest, by_key, warnings) or found
            if not found:
                _warning(warnings, "manual_source", "Не найден подтверждённый формат плана производства, прайса или Формы 1. Себестоимость, персонал, активы и кредиты заполняются/проверяются вручную по документам.", source=source)
        except Exception:
            _warning(warnings, "source_read_failed", "Не удалось безопасно прочитать Excel-источник. Числа из него не перенесены.", source=source)
    for item in items:
        if len(item["proposals"]) > 1:
            _warning(warnings, "multiple_input_proposals", "Для поля есть несколько подтверждающих значений; выберите источник или оставьте исходное.", keys=[item["key"]])
            item["requires_decision"] = bool(item["editable"])
    return {"items": items, "warnings": warnings}


def apply_updates(raw: bytes, updates: dict) -> bytes:
    """Apply numeric whitelist updates only; callers must validate review decisions."""
    if not isinstance(updates, dict):
        raise ValueError("Изменения должны быть словарём числовых входных полей.")
    sheets, layout, _ = _load(raw)
    _, bindings, _ = _inputs(sheets, layout)
    changes, formulas = defaultdict(dict), defaultdict(dict)
    for key, value in updates.items():
        if not isinstance(key, str) or key not in bindings:
            raise ValueError("Поле не разрешено для изменения: " + str(key)[:200])
        number = _numeric(value)
        item = bindings[key]["item"]
        if number is None or not item["min"] <= number <= item["max"]:
            raise ValueError("Недопустимое числовое значение поля: " + key)
        if item.get("integer") and not number.is_integer():
            raise ValueError("Поле требует целого числа: " + key)
        binding = bindings[key]
        sheet, address = item["sheet"], item["cell"]
        if binding["mode"] == "scalar":
            if item["value"] != number:
                changes[sheet][address] = number
            continue
        replacement = Decimal(str(value))
        if binding["mode"] == "net":
            replacement += binding["deduction"]
        elif binding["mode"] == "payroll_rate":
            replacement *= 100
        if _numeric(replacement) is None:
            raise ValueError("Результат изменения исходного литерала вне допустимого диапазона: " + key)
        if item["value"] == number:
            continue
        # Only the matched literal changes. Cached results are removed until
        # the caller recalculates; references/operators remain live and intact.
        first, last = binding["span"]
        formula = binding["formula"]
        literal = format(replacement, "f") if replacement else "0"
        if "." in literal:
            literal = literal.rstrip("0").rstrip(".")
        formulas[sheet][address] = formula[:first] + literal + formula[last:]
        changes[sheet][address] = None
    if not changes:
        return raw
    changed_parts = {layout[sheet]["path"]: sheet for sheet in changes}
    output = BytesIO()
    with _archive(raw) as archive, ZipFile(output, "w") as target:
        target.comment = archive.comment
        for member in archive.infolist():
            content = archive.read(member)
            if member.filename in changed_parts:
                sheet = changed_parts[member.filename]
                content = _patch_sheet(content, changes[sheet], formulas[sheet])
            target.writestr(member, content)
    return output.getvalue()
