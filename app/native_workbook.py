"""Bounded, local OOXML recalculation; uploaded formulas are never Python code.

This is a deliberately small Excel-compatible function whitelist. Formula caches
are comparison evidence only: every formula result is computed from its inputs.
Unresolved cells remain explicit errors and must block an official dependent
report. ZIP members outside changed caches/explicit overrides are preserved.
"""
from __future__ import annotations

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from io import BytesIO
import math
import posixpath
import re
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZipFile

from defusedxml import ElementTree as ET

from .business_sources import _xlsx, MAX_CELLS, MAX_FILE_BYTES

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
FUNCTIONS = frozenset({"SUM", "IF", "AVERAGEA", "SUMPRODUCT", "EDATE", "IFERROR", "NPV", "IRR", "TRANSPOSE"})
CELL = re.compile(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,4})\Z")
MAX_FORMULA = 16_000
MAX_TOKENS = 4096
MAX_DEPTH = 256
MAX_OPERATIONS = 2_000_000
ERROR_CODES = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!"}
TOKEN = re.compile(r'''\s*(?:(?P<error>\#(?:REF!|DIV/0!|VALUE!|NAME\?|N/A|NUM!|NULL!))|(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|(?P<string>"(?:[^"]|"")*")|(?P<quoted>'(?:[^']|'')*')|(?P<ident>\$?[A-Za-z_\\\u0080-\uffff][A-Za-z0-9_.$\\\u0080-\uffff]*)|(?P<op><>|<=|>=|[+\-*/^%(),:!<>=&]))''')


class ExcelError(Exception):
    def __init__(self, code="#VALUE!", reason=""):
        self.code = code
        self.reason = reason or code
        super().__init__(self.reason)


@dataclass(frozen=True)
class Reference:
    value: Any


@dataclass(frozen=True)
class Matrix:
    rows: tuple[tuple[Any, ...], ...]
    referenced: bool = True

    def flat(self):
        return [v for row in self.rows for v in row]


def _address(value):
    match = CELL.fullmatch(str(value))
    if not match:
        raise ExcelError("#REF!", "Invalid or unsupported cell address")
    col = 0
    for char in match[1].upper():
        col = col * 26 + ord(char) - 64
    row = int(match[2])
    if col > 16384 or row > 10000:
        raise ExcelError("#REF!", "Cell address exceeds supported worksheet bounds")
    return col, row


def _col(number):
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def _canonical(value):
    col, row = _address(value)
    return _col(col) + str(row)


def _scalar(value):
    while isinstance(value, Reference):
        value = value.value
    if isinstance(value, ExcelError):
        raise value
    if isinstance(value, Matrix):
        if len(value.rows) != 1 or len(value.rows[0]) != 1:
            raise ExcelError("#VALUE!", "An array cannot be used as a scalar")
        return _scalar(value.rows[0][0])
    return value


def _number(value):
    value = _scalar(value)
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ExcelError("#VALUE!", "A numeric argument is required") from error
    if not math.isfinite(number):
        raise ExcelError("#NUM!", "Non-finite numeric result")
    return number


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ExcelError("#NUM!", "Numeric calculation overflow")
    return value


def _preflight(raw):
    """Reject extreme numeric exponents before the sparse reader formats them."""
    with ZipFile(BytesIO(raw)) as archive:
        members=archive.infolist()
        if len(members)>2048 or sum(i.file_size for i in members)>100*1024*1024:
            raise ValueError("Workbook exceeds archive bounds")
        cells=0
        for item in members:
            if item.file_size>30*1024*1024 or item.flag_bits&1:
                raise ValueError("Workbook exceeds member bounds or is encrypted")
            if not re.fullmatch(r'xl/worksheets/[^/]+\.xml',item.filename):continue
            for _,node in ET.iterparse(BytesIO(archive.read(item)),events=('end',),forbid_dtd=True,forbid_entities=True,forbid_external=True):
                if node.tag=='{'+NS['s']+'}c':
                    cells+=1
                    if cells>MAX_CELLS:raise ValueError("Workbook exceeds cell bounds")
                if node.tag=='{'+NS['s']+'}v' and node.text:
                    value=node.text
                    if len(value)>256:raise ValueError("Numeric/cache entry exceeds supported precision")
                    exponent=re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE]([+-]?\d+)',value)
                    if exponent and (len(exponent[1].lstrip('+-0'))>4 or not -308<=int(exponent[1])<=308):
                        raise ValueError("Numeric exponent exceeds the Excel double range")
                node.clear()


class Parser:
    """Parse an expression into a bounded AST, with no eval/exec or file access."""
    def __init__(self, text):
        if not isinstance(text, str) or len(text) > MAX_FORMULA:
            raise ExcelError("#VALUE!", "Formula exceeds the supported length")
        text = text.removeprefix("=").strip()
        self.tokens = []
        position = 0
        while position < len(text):
            match = TOKEN.match(text, position)
            if not match:
                raise ExcelError("#NAME?", "Unsupported formula syntax")
            self.tokens.append((match.lastgroup, match.group(match.lastgroup)))
            if len(self.tokens) > MAX_TOKENS:
                raise ExcelError("#VALUE!", "Formula has too many tokens")
            position = match.end()
        self.tokens.append(("end", ""))
        self.index = 0

    def peek(self):
        return self.tokens[self.index][1]

    def take(self, expected=None):
        token = self.tokens[self.index]
        if expected is not None and token[1] != expected:
            raise ExcelError("#VALUE!", "Malformed formula")
        self.index += 1
        return token

    def parse(self):
        result = self.expression(0, 0)
        if self.tokens[self.index][0] != "end":
            raise ExcelError("#NAME?", "Unsupported formula operator or reference")
        return result

    def expression(self, minimum, depth):
        if depth > MAX_DEPTH:
            raise ExcelError("#VALUE!", "Formula nesting limit")
        kind, value = self.take()
        if value in ("+", "-"):
            # Excel evaluates unary minus before exponentiation: -2^2 = 4.
            left = ("unary", value, self.expression(60, depth + 1))
        elif value == "(":
            left = self.expression(0, depth + 1)
            self.take(")")
        elif kind == "number":
            left = ("literal", _finite(float(value)))
        elif kind == "string":
            left = ("literal", value[1:-1].replace('""', '"'))
        elif kind == "error":
            left = ("error", value)
        elif kind in ("ident", "quoted"):
            name = value[1:-1].replace("''", "'") if kind == "quoted" else value
            if self.peek() == "(":
                if kind == "quoted" or name.upper() not in FUNCTIONS:
                    raise ExcelError("#NAME?", "Unsupported function: " + name[:80])
                self.take("(")
                arguments = []
                if self.peek() != ")":
                    while True:
                        if self.peek() in (",", ")"):
                            arguments.append(("literal", None))
                        else:
                            arguments.append(self.expression(0, depth + 1))
                        if self.peek() != ",":
                            break
                        self.take(",")
                self.take(")")
                left = ("function", name.upper(), arguments)
            else:
                sheet = None
                if self.peek() == "!":
                    self.take("!")
                    sheet = name
                    kind, name = self.take()
                    if kind == "error":
                        left = ("error", name)
                    elif kind != "ident":
                        raise ExcelError("#REF!", "Unsupported worksheet reference")
                    else:
                        left = self.reference(sheet, name)
                elif kind == "quoted":
                    raise ExcelError("#NAME?", "Quoted text is not a named reference")
                elif name.upper() in ("TRUE", "FALSE"):
                    left = ("literal", name.upper() == "TRUE")
                else:
                    left = self.reference(sheet, name)
        else:
            raise ExcelError("#VALUE!", "Malformed formula expression")
        powers = {"=": 10, "<>": 10, "<": 10, ">": 10, "<=": 10, ">=": 10,
                  "&": 20, "+": 30, "-": 30, "*": 40, "/": 40, "^": 50, "%": 70}
        while self.peek() in powers and powers[self.peek()] >= minimum:
            operator = self.take()[1]
            if operator == "%":
                left = ("unary", "%", left)
                continue
            # Excel exponentiation associates left to right.
            right = self.expression(powers[operator] + 1, depth + 1)
            left = ("binary", operator, left, right)
        return left

    def reference(self, sheet, name):
        if CELL.fullmatch(name):
            start = _canonical(name)
            if self.peek() == ":":
                self.take(":")
                kind, end = self.take()
                if kind != "ident" or not CELL.fullmatch(end):
                    raise ExcelError("#REF!", "Only rectangular cell ranges are supported")
                return ("range", sheet, start, _canonical(end))
            return ("cell", sheet, start)
        return ("name", sheet, name)


class Workbook:
    def __init__(self, raw, overrides=None, repairs=None):
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_FILE_BYTES:
            raise ValueError("Workbook must be nonempty and no larger than 20 MiB")
        self.raw = raw
        _preflight(raw)
        self.sheets, self.notes = _xlsx(raw)
        if sum(len(cells) for cells in self.sheets.values()) > MAX_CELLS:
            raise ValueError("Workbook exceeds the supported cell limit")
        self.sheet_lookup = {name.casefold(): name for name in self.sheets}
        self.names, self.paths, self.arrays, self.raw_numbers = {}, {}, {}, {}
        self.shared_groups, self.expanded_repairs = {}, {}
        self.overrides, self.repairs = {}, {}
        self.cache, self.visiting, self.ast = {}, set(), {}
        self.dependencies,self.stack = {},[]
        self.operations = 0
        with ZipFile(BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read("xl/workbook.xml"))
            props = root.find("s:workbookPr", NS)
            self.date1904 = props is not None and props.attrib.get("date1904") in ("1", "true")
            rels = {item.attrib.get("Id"): item.attrib.get("Target", "")
                    for item in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                    if item.attrib.get("TargetMode") != "External"}
            ordered = root.findall("s:sheets/s:sheet", NS)
            for node in root.findall("s:definedNames/s:definedName", NS):
                name, definition = node.attrib.get("name", ""), node.text or ""
                local = node.attrib.get("localSheetId")
                scope = ordered[int(local)].attrib["name"] if local is not None and int(local) < len(ordered) else None
                key = (scope, name.casefold())
                if key in self.names:
                    raise ValueError("Duplicate defined name in one scope")
                self.names[key] = definition
            for node in ordered:
                sheet = node.attrib["name"]
                if sheet not in self.sheets:
                    continue
                target = rels.get(node.attrib.get(REL_ID), "")
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
                if not path.startswith("xl/") or ".." in path.split("/"):
                    raise ValueError("Unsafe worksheet relationship")
                self.paths[sheet] = path
                for cell in ET.fromstring(archive.read(path)).findall("s:sheetData/s:row/s:c", NS):
                    address = cell.attrib.get("r", "")
                    value, formula = cell.find("s:v", NS), cell.find("s:f", NS)
                    if cell.attrib.get("t", "n") == "n" and value is not None and value.text:
                        self.raw_numbers[sheet, address] = _finite(float(value.text))
                    if formula is not None and formula.attrib.get("t") == "shared":
                        self.shared_groups.setdefault((sheet, formula.attrib.get("si")), []).append(address)
                    if formula is not None and formula.attrib.get("t") == "array":
                        declared = formula.attrib.get("ref", address)
                        bounds = declared.split(":")
                        c1, r1 = _address(bounds[0]); c2, r2 = _address(bounds[-1])
                        if c2 < c1 or r2 < r1 or (c2-c1+1)*(r2-r1+1) > MAX_CELLS:
                            raise ValueError("Unsupported array-formula bounds")
                        for r in range(r1, r2+1):
                            for c in range(c1, c2+1):
                                key = sheet, _col(c)+str(r)
                                if key in self.arrays:
                                    raise ValueError("Overlapping array formulas")
                                self.arrays[key] = (address, r-r1, c-c1, r2-r1+1, c2-c1+1)
        self._changes(overrides or {}, False)
        self._changes(repairs or {}, True)
        # Repairing a shared anchor must not strand its empty shared followers.
        # Expand that group's existing translated formulas to ordinary formulas.
        for (sheet, _), cells in self.shared_groups.items():
            if any((sheet, cell) in self.repairs for cell in cells):
                for cell in cells:
                    self.expanded_repairs[sheet, cell] = self.repairs.get((sheet, cell), self.sheets[sheet][cell]["formula"])
        self.expanded_repairs.update(self.repairs)

    def sheet(self, name):
        result = self.sheet_lookup.get(str(name).casefold())
        if result is None:
            raise ExcelError("#REF!", "Worksheet does not exist")
        return result

    def _changes(self, changes, formulas):
        if not isinstance(changes, dict):
            raise ValueError("Overrides/repairs must map worksheet names to cell objects")
        for requested, entries in changes.items():
            sheet = self.sheet(requested)
            if not isinstance(entries, dict):
                raise ValueError("A worksheet change must be a cell object")
            for requested_cell, value in entries.items():
                cell = _canonical(requested_cell)
                if cell not in self.sheets[sheet]:
                    raise ValueError("Only existing template cells can be changed")
                key = sheet, cell
                if key in self.arrays:
                    raise ValueError("Array formulas require an explicit new template")
                if formulas:
                    if not isinstance(value, str) or not value.startswith("="):
                        raise ValueError("Repairs require an explicit replacement formula")
                    Parser(value).parse()
                    self.repairs[key] = value
                else:
                    if self.sheets[sheet][cell].get("formula"):
                        raise ValueError("Use repairs to change a formula; an input override cannot erase it")
                    if not isinstance(value, (str, int, float, bool, type(None))) or isinstance(value, str) and len(value) > 120000:
                        raise ValueError("Only bounded scalar input overrides are supported")
                    if isinstance(value, (int, float)) and (not math.isfinite(value) or abs(value) > 1e15):
                        raise ValueError("Input override exceeds the numeric bounds")
                    self.overrides[key] = value

    def value(self, sheet, cell):
        sheet, cell = self.sheet(sheet), _canonical(cell)
        key = sheet, cell
        if self.stack:self.dependencies.setdefault(self.stack[-1],set()).add(key)
        if key in self.arrays:
            anchor, row, col, height, width = self.arrays[key]
            if anchor!=cell:self.dependencies.setdefault(key,set()).add((sheet,anchor))
            result = self._cell(sheet, anchor)
            if isinstance(result, ExcelError):
                return result
            if not isinstance(result, Matrix) or len(result.rows) != height or any(len(r) != width for r in result.rows):
                return ExcelError("#VALUE!", "Array result does not match its declared output range")
            value=result.rows[row][col]
            return 0.0 if value is None else value
        result = self._cell(sheet, cell)
        if isinstance(result, Matrix):
            return ExcelError("#VALUE!", "An array formula needs a declared output range")
        return result

    def _cell(self, sheet, cell):
        key = sheet, cell
        if key in self.cache:
            return self.cache[key]
        if key in self.visiting:
            return ExcelError("#REF!", "Circular dependency")
        if len(self.visiting) >= MAX_DEPTH:
            return ExcelError("#VALUE!", "Cell dependency depth limit")
        self.visiting.add(key)
        self.stack.append(key)
        try:
            source = self.sheets[sheet].get(cell, {})
            if key in self.overrides:
                result = self.overrides[key]
            elif self.repairs.get(key) or source.get("formula"):
                formula = self.repairs.get(key) or source["formula"]
                if formula not in self.ast:
                    self.ast[formula] = Parser(formula).parse()
                result = self.evaluate(self.ast[formula], sheet)
                if isinstance(result, Reference):
                    result = _scalar(result)
                if result is None:result=0.0  # A formula returning a blank reference displays Excel 0.
            elif source.get("error"):
                result = ExcelError(str(source.get("value") or "#VALUE!"), "Explicit source error")
            else:
                result = self.raw_numbers.get(key, source.get("value"))
            if isinstance(result, float):
                _finite(result)
        except ExcelError as error:
            result = error
        except (ArithmeticError, ValueError, TypeError, RecursionError) as error:
            result = ExcelError("#NUM!" if isinstance(error, ArithmeticError) else "#VALUE!", "Unsupported or invalid calculation")
        finally:
            self.stack.pop()
            self.visiting.remove(key)
        self.cache[key] = result
        return result

    def evaluate(self, node, sheet, depth=0):
        self.operations += 1
        if self.operations > MAX_OPERATIONS or depth > MAX_DEPTH:
            raise ExcelError("#VALUE!", "Calculation complexity limit")
        kind = node[0]
        if kind == "literal": return node[1]
        if kind == "error": raise ExcelError(node[1], "Broken reference in formula")
        if kind == "cell": return Reference(self.value(node[1] or sheet, node[2]))
        if kind == "range": return self.range(node[1] or sheet, node[2], node[3])
        if kind == "name":
            scope = self.sheet(node[1]) if node[1] else sheet
            definition = self.names.get((scope, node[2].casefold()), self.names.get((None, node[2].casefold())))
            if definition is None:
                raise ExcelError("#NAME?", "Undefined named reference: " + node[2][:80])
            identity = ("name", scope, node[2].casefold())
            if identity in self.visiting:
                raise ExcelError("#REF!", "Circular named reference")
            self.visiting.add(identity)
            try:
                if definition not in self.ast:
                    self.ast[definition] = Parser(definition).parse()
                return self.evaluate(self.ast[definition], scope, depth+1)
            finally:
                self.visiting.remove(identity)
        if kind == "unary":
            value = self.evaluate(node[2], sheet, depth+1)
            # Excel's unary plus is an identity, including text and array values.
            if node[1] == "+":return value
            if isinstance(value,Matrix):
                rows=[]
                for row in value.rows:
                    line=[]
                    for entry in row:
                        try:
                            number=_number(entry)
                            line.append(-number if node[1]=="-" else number/100)
                        except ExcelError as error:line.append(error)
                    rows.append(tuple(line))
                return Matrix(tuple(rows),False)
            number=_number(value)
            return -number if node[1] == "-" else number/100
        if kind == "binary":
            left = self.evaluate(node[2], sheet, depth+1)
            right = self.evaluate(node[3], sheet, depth+1)
            return self.binary(node[1], left, right)
        if kind == "function":
            return self.function(node[1], node[2], sheet, depth+1)
        raise ExcelError("#NAME?", "Unsupported expression")

    def range(self, sheet, start, end):
        sheet = self.sheet(sheet)
        c1, r1 = _address(start); c2, r2 = _address(end)
        c1, c2 = sorted((c1,c2)); r1,r2 = sorted((r1,r2))
        count = (c2-c1+1)*(r2-r1+1)
        if count > MAX_CELLS or self.operations + count > MAX_OPERATIONS:
            raise ExcelError("#VALUE!", "Range exceeds calculation bounds")
        self.operations += count
        return Matrix(tuple(tuple(self.value(sheet, _col(c)+str(r)) for c in range(c1,c2+1)) for r in range(r1,r2+1)))

    @staticmethod
    def binary(operator, left, right):
        if isinstance(left, Matrix) or isinstance(right, Matrix):
            matrix = left if isinstance(left, Matrix) else right
            if isinstance(left, Matrix) and isinstance(right, Matrix) and (len(left.rows) != len(right.rows) or any(len(a) != len(b) for a,b in zip(left.rows,right.rows))):
                raise ExcelError("#VALUE!", "Array dimensions differ")
            rows=[]
            for r,row in enumerate(matrix.rows):
                line=[]
                for c,_ in enumerate(row):
                    a=left.rows[r][c] if isinstance(left,Matrix) else left
                    b=right.rows[r][c] if isinstance(right,Matrix) else right
                    try: line.append(Workbook.binary(operator,a,b))
                    except ExcelError as error: line.append(error)
                rows.append(tuple(line))
            return Matrix(tuple(rows),False)
        left, right = _scalar(left), _scalar(right)
        if operator == "&":
            return ("" if left is None else str(left)) + ("" if right is None else str(right))
        if operator in ("=","<>","<",">","<=",">="):
            # Excel text comparisons are case-insensitive; blanks equal numeric 0.
            if isinstance(left,str) and isinstance(right,str):
                left,right=left.casefold(),right.casefold()
            elif left is None or right is None:
                left=0 if left is None else left;right=0 if right is None else right
            if type(left) is not type(right) and isinstance(left,str) != isinstance(right,str):
                cmp = 1 if isinstance(left,str) else -1
            else:
                cmp = (left > right) - (left < right)
            return {"=":cmp==0,"<>":cmp!=0,"<":cmp<0,">":cmp>0,"<=":cmp<=0,">=":cmp>=0}[operator]
        a,b=_number(left),_number(right)
        if operator == "+": result=a+b
        elif operator == "-": result=a-b
        elif operator == "*": result=a*b
        elif operator == "/":
            if b == 0: raise ExcelError("#DIV/0!", "Division by zero")
            result=a/b
        elif operator == "^":
            if a == 0 and b < 0: raise ExcelError("#DIV/0!", "Zero raised to a negative power")
            try: result=math.pow(a,b)
            except (ValueError,OverflowError) as error: raise ExcelError("#NUM!", "Invalid exponentiation") from error
        else: raise ExcelError("#NAME?", "Unsupported operator")
        return _finite(result)

    def function(self, name, arguments, sheet, depth):
        if name in ("IF","IFERROR"):
            if name == "IF":
                if not 2 <= len(arguments) <= 3: raise ExcelError("#VALUE!", "IF expects two or three arguments")
                condition=_scalar(self.evaluate(arguments[0],sheet,depth))
                if isinstance(condition,str): raise ExcelError("#VALUE!", "IF condition must be logical or numeric")
                chosen=arguments[1] if bool(condition) else arguments[2] if len(arguments)==3 else ("literal",False)
                return self.evaluate(chosen,sheet,depth)
            if len(arguments)!=2: raise ExcelError("#VALUE!", "IFERROR expects two arguments")
            try:
                value=self.evaluate(arguments[0],sheet,depth)
                if not isinstance(value,Matrix): _scalar(value)
                elif any(isinstance(v,ExcelError) for v in value.flat()):
                    fallback=self.evaluate(arguments[1],sheet,depth)
                    return Matrix(tuple(tuple(fallback if isinstance(v,ExcelError) else v for v in row) for row in value.rows),False)
                return value
            except ExcelError: return self.evaluate(arguments[1],sheet,depth)
        values=[self.evaluate(arg,sheet,depth) for arg in arguments]
        if name in ("SUM","AVERAGEA"):
            items=[]
            for value in values:
                referenced=isinstance(value,(Reference,Matrix))
                for item in value.flat() if isinstance(value,Matrix) else [value]:
                    item=_scalar(item)
                    if item is None: continue
                    if isinstance(item,str) and referenced:
                        if name == "AVERAGEA":items.append(0.0)
                        continue
                    if isinstance(item,bool) and referenced and name == "SUM":continue
                    items.append(_number(item))
            if name == "AVERAGEA" and not items:raise ExcelError("#DIV/0!","AVERAGEA has no values")
            return _finite(math.fsum(items)/(len(items) if name == "AVERAGEA" else 1))
        if name == "SUMPRODUCT":
            if not values:raise ExcelError("#VALUE!","SUMPRODUCT needs arguments")
            matrices=[v if isinstance(v,Matrix) else Matrix(((_scalar(v),),)) for v in values]
            shape=(len(matrices[0].rows),tuple(len(row) for row in matrices[0].rows))
            if any((len(v.rows),tuple(len(row) for row in v.rows))!=shape for v in matrices):raise ExcelError("#VALUE!","SUMPRODUCT dimensions differ")
            def factor(v):
                v=_scalar(v)
                return float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else 0.0
            return _finite(math.fsum(math.prod(factor(v) for v in entries) for entries in zip(*(m.flat() for m in matrices))))
        if name == "TRANSPOSE":
            if len(values)!=1:raise ExcelError("#VALUE!","TRANSPOSE expects one range")
            value=values[0] if isinstance(values[0],Matrix) else Matrix(((_scalar(values[0]),),))
            return Matrix(tuple(tuple(row[i] for row in value.rows) for i in range(len(value.rows[0]))),False)
        if name == "EDATE":
            if len(values)!=2:raise ExcelError("#VALUE!","EDATE expects date and months")
            serial,months=math.trunc(_number(values[0])),math.trunc(_number(values[1]))
            if serial < 0:raise ExcelError("#NUM!","Negative Excel date")
            if not self.date1904 and serial==60:year,month,day=1900,2,29
            else:
                epoch=date(1904,1,1) if self.date1904 else date(1899,12,31)
                actual=epoch+timedelta(days=serial if self.date1904 or serial<60 else serial-1)
                year,month,day=actual.year,actual.month,actual.day
            total=year*12+month-1+months;year,month=total//12,total%12+1
            if not 1<=year<=9999:raise ExcelError("#NUM!","Date is outside the supported calendar")
            maximum=29 if (year,month)==(1900,2) and not self.date1904 else monthrange(year,month)[1]
            day=min(day,maximum)
            if (year,month,day)==(1900,2,29) and not self.date1904:return 60.0
            actual=date(year,month,day)
            epoch=date(1904,1,1) if self.date1904 else date(1899,12,31)
            result=(actual-epoch).days+(int(actual>=date(1900,3,1)) if not self.date1904 else 0)
            if result<0:raise ExcelError("#NUM!","Date precedes the workbook epoch")
            return float(result)
        if name in ("NPV","IRR"):
            if (name=="NPV" and len(values)<2) or (name=="IRR" and not 1<=len(values)<=2):raise ExcelError("#VALUE!","Incorrect financial function arguments")
            arguments=values[1:] if name=="NPV" else values[:1]
            flows=[]
            for value in arguments:
                ref=isinstance(value,(Reference,Matrix))
                for item in value.flat() if isinstance(value,Matrix) else [value]:
                    item=_scalar(item)
                    if ref and (item is None or isinstance(item,(str,bool))):continue
                    flows.append(_number(item))
            if name=="NPV":
                rate=_number(values[0])
                if rate==-1:raise ExcelError("#DIV/0!","NPV rate is -100%")
                try:return _finite(math.fsum(flow/(1+rate)**(index+1) for index,flow in enumerate(flows)))
                except (OverflowError,ZeroDivisionError) as error:raise ExcelError("#NUM!","NPV cannot be represented") from error
            return _irr(flows,_number(values[1]) if len(values)==2 else 0.1)
        raise ExcelError("#NAME?","Unsupported function")


def _irr(flows,guess):
    if not flows or not any(v<0 for v in flows) or not any(v>0 for v in flows) or guess<=-1:
        raise ExcelError("#NUM!","IRR needs positive and negative flows and a guess greater than -100%")
    scale=max(abs(v) for v in flows)
    flows=[v/scale for v in flows]
    def f(rate):
        try:return math.fsum(v/(1+rate)**i for i,v in enumerate(flows))
        except (OverflowError,ZeroDivisionError):return math.nan
    rate=guess
    for _ in range(100):
        value=f(rate)
        try:derivative=math.fsum(-i*v/(1+rate)**(i+1) for i,v in enumerate(flows) if i)
        except (OverflowError,ZeroDivisionError):break
        if not math.isfinite(value) or not math.isfinite(derivative) or derivative==0:break
        candidate=rate-value/derivative
        if not math.isfinite(candidate) or candidate<=-1:break
        if abs(candidate-rate)<=1e-10*max(1,abs(candidate)) and abs(f(candidate))<1e-8:return candidate
        rate=candidate
    # Search near the supplied guess; do not claim a unique root if several exist.
    points=sorted(set([-0.999999,-0.99,-0.9,-0.5,0,0.1,0.5,1,2,5,10,100,10000,guess]))
    brackets=[]
    for left,right in zip(points,points[1:]):
        a,b=f(left),f(right)
        if math.isfinite(a) and a==0:return left
        if math.isfinite(b) and b==0:return right
        if math.isfinite(a) and math.isfinite(b) and a*b<0:brackets.append((left,right))
    if not brackets:raise ExcelError("#NUM!","IRR did not converge within bounded search")
    left,right=min(brackets,key=lambda bounds:abs((bounds[0]+bounds[1])/2-guess))
    a=f(left)
    for _ in range(200):
        mid=(left+right)/2;b=f(mid)
        if not math.isfinite(b):raise ExcelError("#NUM!","IRR numerical overflow")
        if abs(b)<1e-12:return mid
        if a*b<0:right=mid
        else:left,a=mid,b
    raise ExcelError("#NUM!","IRR did not converge")


def inspect_native(raw):
    workbook=Workbook(raw)
    formulas=[c.get("formula") for cells in workbook.sheets.values() for c in cells.values() if c.get("formula")]
    functions=Counter(name.upper() for formula in formulas for name in re.findall(r"([A-Za-z][A-Za-z0-9_.]*)\s*\(",formula))
    with ZipFile(BytesIO(raw)) as archive:
        charts=sum(bool(re.fullmatch(r"xl/charts/chart[0-9]+\.xml",name)) for name in archive.namelist())
    return {"sheets":[{"name":name,"cells":len(cells),"formulas":sum(bool(c.get("formula")) for c in cells.values())} for name,cells in workbook.sheets.items()],
            "formula_count":len(formulas),"functions":dict(functions),"unsupported_functions":sorted(set(functions)-FUNCTIONS),
            "defined_name_count":len(workbook.names),"array_cell_count":len(workbook.arrays),"chart_count":charts,
            "cached_error_counts":dict(Counter(str(c.get("value")) for cells in workbook.sheets.values() for c in cells.values() if c.get("error"))),
            "notes":workbook.notes,"arithmetic_verified":False}


def _xml_value(value):
    if isinstance(value,ExcelError):return "e",value.code
    if value is None:return "",None
    if isinstance(value,bool):return "b","1" if value else "0"
    if isinstance(value,(int,float)):return "",format(_finite(float(value)),".15g")
    return "str",str(value)


def _patch_cell(text,value,formula=None):
    tag,number=_xml_value(value)
    opening=re.match(r"<(?P<prefix>[A-Za-z0-9_]+:)?c\b[^>]*>",text)
    if opening is None:return text
    prefix=opening.group("prefix") or ""
    start=re.sub(r'\s+t=("[^"]*"|\'[^\']*\')',"",opening.group())
    if tag:start=start[:-1]+' t="'+tag+'">'
    body=text[opening.end():]
    body=re.sub(r"<"+re.escape(prefix)+r"(?:v|is)\b[^>]*>.*?</"+re.escape(prefix)+r"(?:v|is)\s*>","",body,flags=re.S)
    body=re.sub(r"<"+re.escape(prefix)+r"v\s*/>","",body)
    if formula is not None:
        fresh="<"+prefix+"f>"+escape(formula.removeprefix("="))+"</"+prefix+"f>"
        body,count=re.subn(r"<"+re.escape(prefix)+r"f\b[^>]*(?:/>|>.*?</"+re.escape(prefix)+r"f\s*>)",lambda _:fresh,body,count=1,flags=re.S)
        if not count:body=fresh+body
    if number is not None:
        body=body.replace("</"+prefix+"c>","<"+prefix+"v>"+escape(number)+"</"+prefix+"v></"+prefix+"c>")
    return start+body


def _patch_sheet(raw,changes,repairs):
    text=raw.decode("utf-8")
    # Match only cell elements; namespace prefix, formula and style text survive.
    pattern=re.compile(r'<(?:[A-Za-z0-9_]+:)?c\b[^>]*\br=(?:"(?P<double>[A-Z]+[1-9][0-9]*)"|\'(?P<single>[A-Z]+[1-9][0-9]*)\')[^>]*?(?:/>|>.*?</(?:[A-Za-z0-9_]+:)?c\s*>)',re.S)
    changed=set()
    def patch(match):
        address=match.group("double") or match.group("single")
        if address not in changes:return match.group()
        original=match.group()
        if original.endswith("/>"):
            prefix=re.match(r"<([A-Za-z0-9_]+:)?c",original).group(1) or ""
            original=original[:-2]+"></"+prefix+"c>"
        changed.add(address)
        return _patch_cell(original,changes[address],repairs.get(address))
    result=pattern.sub(patch,text).encode("utf-8")
    if set(changes)-changed:raise ValueError("A calculated cell is absent from the template XML")
    return result


def _patch_chart(raw,workbook):
    """Refresh only reference caches, retaining chart layout and formatting XML."""
    text=raw.decode("utf-8")
    pattern=re.compile(r'<(?P<prefix>(?:[A-Za-z0-9_]+:)?)(?P<kind>numRef|strRef)\b[^>]*>.*?</(?P=prefix)(?P=kind)\s*>',re.S)
    diagnostics=[];updated=0
    def patch(match):
        nonlocal updated
        text=match.group();prefix=match.group('prefix') or '';kind=match.group('kind')
        formula=re.search(r'<'+re.escape(prefix)+r'f\b[^>]*>(.*?)</'+re.escape(prefix)+r'f\s*>',text,re.S)
        if formula is None:return text
        items=[]
        try:
            ast=Parser(unescape(formula[1])).parse()
            value=workbook.evaluate(ast,next(iter(workbook.sheets)))
            items=value.flat() if isinstance(value,Matrix) else [_scalar(value)]
        except ExcelError as error:
            diagnostics.append({'code':error.code,'reason':error.reason})
        rows=[]
        for index,item in enumerate(items):
            try:
                value=_scalar(item)
                if value is None:continue
                if kind=='numRef':
                    if isinstance(value,(str,bool)):continue
                    value=format(_number(value),'.15g')
                else:value=str(value)
                rows.append('<'+prefix+'pt idx="'+str(index)+'"><'+prefix+'v>'+escape(value)+'</'+prefix+'v></'+prefix+'pt>')
            except ExcelError as error:
                diagnostics.append({'code':error.code,'reason':error.reason,'point':index})
        cache='numCache' if kind=='numRef' else 'strCache'
        cache_pattern=re.compile(r'<'+re.escape(prefix)+cache+r'\b[^>]*>.*?</'+re.escape(prefix)+cache+r'\s*>',re.S)
        found=cache_pattern.search(text)
        old=found.group() if found else '<'+prefix+cache+'></'+prefix+cache+'>'
        old=re.sub(r'<'+re.escape(prefix)+r'ptCount\b[^>]*(?:/>|>.*?</'+re.escape(prefix)+r'ptCount\s*>)','',old,flags=re.S)
        old=re.sub(r'<'+re.escape(prefix)+r'pt\b[^>]*>.*?</'+re.escape(prefix)+r'pt\s*>','',old,flags=re.S)
        fresh=old.replace('</'+prefix+cache+'>','<'+prefix+'ptCount val="'+str(len(items))+'"/>'+''.join(rows)+'</'+prefix+cache+'>')
        updated+=1
        return cache_pattern.sub(lambda _:fresh,text,count=1) if found else text.replace('</'+prefix+kind+'>',fresh+'</'+prefix+kind+'>')
    return pattern.sub(patch,text).encode('utf-8'),updated,diagnostics


def recalculate(raw,overrides=None,repairs=None):
    """Return (new_xlsx_bytes, result), keeping unresolved results explicit.

    result['values'] is a fresh sheet/cell map (errors are their Excel tokens).
    result['unresolved'] includes formula failures; no result uses a cached value.
    Every formula cache receives its fresh value or fresh Excel error token.
    The caller must decide which unresolved dependencies block its
    official output. Original uploaded bytes and all unaffected ZIP parts survive.
    """
    workbook=Workbook(raw,overrides,repairs)
    values={};issues=[];changes={};differences=[];calculated=0
    for sheet,cells in workbook.sheets.items():
        values[sheet]={};changes[sheet]={}
        addresses=set(cells)|{cell for s,cell in workbook.arrays if s==sheet}
        for cell in sorted(addresses):
            value=workbook.value(sheet,cell)
            source=cells.get(cell,{})
            formula=workbook.repairs.get((sheet,cell)) or source.get("formula")
            affected=bool(formula) or (sheet,cell) in workbook.arrays
            if isinstance(value,ExcelError):
                values[sheet][cell]=value.code
                if affected:
                    root = bool(formula and re.search(r'#(?:REF!|DIV/0!|VALUE!|NAME\?|N/A|NUM!|NULL!)',formula)) or value.reason.startswith(('Unsupported','Undefined','Circular','Invalid'))
                    issues.append({"sheet":sheet,"cell":cell,"code":value.code,"reason":value.reason,
                                   "message":value.reason,"root":root,
                                   "action":"Restore the missing reference from an approved source" if value.code=='#REF!' else "Review the source formula and its inputs"})
                    if cell in cells:changes[sheet][cell]=value
                continue
            values[sheet][cell]=value
            if affected:
                calculated+=1
                if cell in cells:changes[sheet][cell]=value
                original=source.get("value")
                if isinstance(value,(int,float)) and not isinstance(value,bool):
                    cached=workbook.raw_numbers.get((sheet,cell))
                    if cached is not None and abs(value-cached)>max(0.01,1e-9*max(abs(value),abs(cached))):
                        differences.append({"sheet":sheet,"cell":cell,"cached":cached,"calculated":value,"delta":value-cached})
                elif original!=value:differences.append({"sheet":sheet,"cell":cell,"cached":original,"calculated":value})
            if (sheet,cell) in workbook.overrides:changes[sheet][cell]=value
    output=BytesIO();chart_count=0;chart_issues=[]
    with ZipFile(BytesIO(raw)) as archive,ZipFile(output,"w") as target:
        for item in archive.infolist():
            content=archive.read(item)
            matching=next((sheet for sheet,path in workbook.paths.items() if path==item.filename),None)
            if matching and changes[matching]:
                content=_patch_sheet(content,changes[matching],{cell:formula for (sheet,cell),formula in workbook.expanded_repairs.items() if sheet==matching})
            if re.fullmatch(r'xl/charts/chart[0-9]+\.xml',item.filename):
                content,count,diagnostics=_patch_chart(content,workbook)
                chart_count+=count
                chart_issues.extend({'part':item.filename,**diagnostic} for diagnostic in diagnostics)
            target.writestr(item,content)
        target.comment=archive.comment
    issues.sort(key=lambda item:(not item['root'],item['sheet'],item['cell']))
    return output.getvalue(),{"values":values,"unresolved":issues,"issues":issues,
            "error_counts":dict(Counter(i["code"] for i in issues)),"calculated_cells":calculated,
            "error_count":len(issues),"root_issues":[i for i in issues if i['root']],
            "formula_count":sum(bool(c.get("formula")) for cells in workbook.sheets.values() for c in cells.values()),
            "differences":differences,"complete":not issues,"used_cached_formula_results":False,
            "chart_cache_status":"fresh_with_gaps" if chart_issues else "fresh",
            "chart_caches_updated":chart_count,"chart_issues":chart_issues,"notes":workbook.notes,
            "dependencies":{sheet+'!'+cell:[s+'!'+c for s,c in sorted(edges)] for (sheet,cell),edges in workbook.dependencies.items()}}


def dependency_issues(result,targets):
    """Filter failures to the transitive dependencies of selected report outputs.

    Targets are [{"sheet": ..., "cell": ...}, ...] or {sheet: [cell, ...]}.
    Inactive-sheet errors remain present in result['issues']; this helper never
    suppresses errors in a requested output or any of its referenced inputs.
    """
    if isinstance(targets,dict):targets=[{'sheet':sheet,'cell':cell} for sheet,cells in targets.items() for cell in cells]
    if not isinstance(targets,list) or len(targets)>MAX_CELLS:raise ValueError('Invalid report output targets')
    pending=[str(t['sheet'])+'!'+_canonical(t['cell']) for t in targets]
    visited=set();graph=result.get('dependencies',{})
    while pending:
        key=pending.pop()
        if key in visited:continue
        visited.add(key)
        if len(visited)>MAX_CELLS:raise ValueError('Report dependency limit')
        pending.extend(graph.get(key,[]))
    issues=[i for i in result.get('issues',[]) if i['sheet']+'!'+i['cell'] in visited]
    known={i['sheet']+'!'+i['cell'] for i in issues}
    for key in visited-known:
        sheet,cell=key.rsplit('!',1)
        value=result.get('values',{}).get(sheet,{}).get(cell)
        if value in ERROR_CODES if isinstance(value,str) else False:
            issues.append({'sheet':sheet,'cell':cell,'code':value,'reason':'Referenced source error',
                           'message':'Referenced source error','root':True,'action':'Restore the source input'})
        elif sheet not in result.get('values',{}):
            issues.append({'sheet':sheet,'cell':cell,'code':'#REF!','reason':'Report worksheet is absent',
                           'message':'Report worksheet is absent','root':True,'action':'Restore the report worksheet'})
    issues.sort(key=lambda i:(not i.get('root'),i['sheet'],i['cell']))
    return {'issues':issues,'root_issues':[i for i in issues if i.get('root')],
            'error_count':len(issues),'complete':not issues,'dependency_count':len(visited)}
