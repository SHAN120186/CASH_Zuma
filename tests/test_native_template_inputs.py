"""Input review and OOXML preservation, using only synthetic source documents."""
from io import BytesIO
from decimal import Decimal
from pathlib import Path
import re
import sys
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.business_sources import _xlsx
from app.native_template_inputs import apply_updates, describe_inputs, numbers_equivalent
from app.native_uzgermed import PARAMETERS, REQUIRED_SHEETS


S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"


def workbook(sheets=None, *, native=False, formula_attributes=None, cell_attributes=None):
    data = {name: {} for name in sorted(REQUIRED_SHEETS)} if native else {}
    if native:
        for sheet, address, _, _, low, high, step, _ in PARAMETERS:
            value = max(1, low) if step else max(low, min(high, 0.25))
            data[sheet][address] = value
        data["Стоим_проекта"].update({"B4": 'ООО "UZGERMED PHARM"', "A2": "СТОИМОСТЬ ПРОЕКТА"})
        data["Фин_план"]["A2"] = "ФИНАНСОВЫЙ ПЛАН"
        data["ВНД"].update({"A1": "Расчёт NPV и IRR", "W41": 1.5})
        data["План производства"].update({
            "C6": "Объём 2030", "D6": "Объём 2031", "C7": "упаковок", "D7": "упаковок",
            "A8": "Препарат А 10 мг №5", "C8": 100, "D8": 120,
            "F8": (3, "(200/(+курс))*(+ВНД!$W$41)"),
            "B8": (1, "60/(+курс)"),
        })
    for name, cells in (sheets or {}).items():
        data.setdefault(name, {}).update(cells)
    book = ET.Element(f"{{{S}}}workbook")
    book_sheets = ET.SubElement(book, f"{{{S}}}sheets")
    rels = ET.Element(f"{{{P}}}Relationships")
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.comment = b"Synthetic template comment"
        for index, (name, cells) in enumerate(data.items(), 1):
            ET.SubElement(book_sheets, f"{{{S}}}sheet", {"name": name, "sheetId": str(index), f"{{{R}}}id": f"rId{index}"})
            ET.SubElement(rels, f"{{{P}}}Relationship", {"Id": f"rId{index}", "Type": f"{R}/worksheet", "Target": f"worksheets/sheet{index}.xml"})
            tree = ET.Element(f"{{{S}}}worksheet")
            rows = {}
            sheet_data = ET.SubElement(tree, f"{{{S}}}sheetData")
            for address, cell in cells.items():
                value, formula = cell if isinstance(cell, tuple) else (cell, None)
                row_number = re.search(r"\d+", address)[0]
                row = rows.setdefault(row_number, None)
                if row is None:
                    row = ET.SubElement(sheet_data, f"{{{S}}}row", {"r": row_number, "ht": "24", "customHeight": "1"})
                    rows[row_number] = row
                attrs = {"r": address, "s": "0", **(cell_attributes or {}).get((name, address), {})}
                if isinstance(value, bool):
                    attrs["t"] = "b"
                elif isinstance(value, str):
                    attrs["t"] = "str" if formula is not None else "inlineStr"
                node = ET.SubElement(row, f"{{{S}}}c", attrs)
                if formula is not None:
                    ET.SubElement(node, f"{{{S}}}f", (formula_attributes or {}).get((name, address), {})).text = formula
                if value is not None:
                    if attrs.get("t") == "inlineStr":
                        inline = ET.SubElement(node, f"{{{S}}}is")
                        ET.SubElement(inline, f"{{{S}}}t").text = value
                    else:
                        ET.SubElement(node, f"{{{S}}}v").text = str(int(value) if isinstance(value, bool) else value)
            ET.SubElement(tree, f"{{{S}}}pageSetup", {"paperSize": "9", "orientation": "landscape", "fitToWidth": "1"})
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(tree))
        archive.writestr("xl/workbook.xml", ET.tostring(book))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))
        archive.writestr("xl/styles.xml", f'<styleSheet xmlns="{S}"><cellXfs count="1"><xf numFmtId="0"/></cellXfs></styleSheet>')
        archive.writestr("xl/printerSettings/settings.bin", b"Synthetic original print settings")
        archive.writestr("xl/media/image1.png", b"Synthetic unchanged image content")
    return stream.getvalue()


def items(raw, files=None):
    return {item["key"]: item for item in describe_inputs(raw, files)["items"]}


def parts(raw):
    with ZipFile(BytesIO(raw)) as archive:
        return {member.filename: archive.read(member) for member in archive.infolist()}


def production(rows=None, year="2030"):
    values = {"B2": "Наименование препарата", "P2": "Объём " + year, "Q2": "Объём 2031",
              "B3": "  ПРЕПАРАТ  А 10 мг №5 ", "P3": 150, "Q3": 180}
    values.update(rows or {})
    return workbook({"План": values})


def prices(rows=None):
    values = {"C4": "Номенклатура.Код", "D4": "Номенклатура.Наименование", "E4": "Sales Price",
              "C5": "SKU-A", "D5": "Препарат А 10 мг №5", "E5": 250}
    values.update(rows or {})
    return workbook({"Прайс": values})


class NativeTemplateInputTests(unittest.TestCase):
    def test_original_inputs_include_base_literals_and_missing_is_not_zero(self):
        raw = workbook({"Стоим_проекта": {"B28": (6, "(900-300)/курс*1000")},
                        "Труд": {"A15": "Оператор", "B15": 2, "C15": None,
                                 "D34": (5, "D33*10%")}}, native=True)
        fields = items(raw)
        self.assertEqual(fields["План производства!F8"]["value"], 200)
        self.assertEqual(fields["План производства!B8"]["value"], 60)
        self.assertEqual(fields["Стоим_проекта!B28"]["value"], 600)
        self.assertAlmostEqual(fields["Труд!D34"]["value"], .1)
        self.assertIsNone(fields["Труд!C15"]["value"])
        self.assertTrue(fields["Труд!C15"]["editable"])
        self.assertFalse(fields["Труд!C15"]["required"])
        self.assertTrue(fields["Стоим_проекта!B38"]["required"])
        self.assertFalse(fields["РКЛ!AA7"]["editable"])
        self.assertTrue(any(w["code"] == "missing_input" for w in describe_inputs(raw)["warnings"]))

    def test_only_allowed_cells_and_literals_change_and_other_parts_are_exact(self):
        raw = workbook({"Стоим_проекта": {"B28": (6, "(900-300)/курс*1000")},
                        "Труд": {"D34": (4, "D33*10%")}}, native=True)
        updated = apply_updates(raw, {"План производства!C8": 220, "План производства!F8": 350,
                                      "План производства!B8": 85, "Стоим_проекта!B28": 800,
                                      "Труд!D34": .12})
        before, after = parts(raw), parts(updated)
        self.assertEqual(set(before), set(after))
        changed = [path for path in before if before[path] != after[path]]
        self.assertEqual(len(changed), 3)
        self.assertTrue(all(path.startswith("xl/worksheets/") for path in changed))
        for path in changed:
            self.assertIn(b'pageSetup', after[path])
            self.assertIn(b'ht="24"', after[path])
        sheets, _ = _xlsx(updated)
        self.assertEqual(sheets["План производства"]["C8"]["numeric_text"], "220")
        self.assertEqual(sheets["План производства"]["F8"]["formula"], "=(350/(+курс))*(+ВНД!$W$41)")
        self.assertEqual(sheets["План производства"]["B8"]["formula"], "=85/(+курс)")
        self.assertEqual(sheets["Стоим_проекта"]["B28"]["formula"], "=(1100-300)/курс*1000")
        self.assertEqual(sheets["Труд"]["D34"]["formula"], "=D33*12%")
        for sheet, cell in (("План производства", "F8"), ("Стоим_проекта", "B28")):
            self.assertIsNone(sheets[sheet][cell]["value"], "old caches must not survive literal updates")
        with ZipFile(BytesIO(updated)) as archive:
            self.assertEqual(archive.comment, b"Synthetic template comment")

    def test_equal_updates_and_empty_updates_preserve_original_bytes(self):
        raw = workbook(native=True)
        self.assertEqual(apply_updates(raw, {}), raw)
        self.assertEqual(apply_updates(raw, {"План производства!C8": 100, "План производства!F8": 200}), raw)

    def test_unknown_nonfinite_boolean_string_bounds_and_fractional_days_rejected(self):
        raw = workbook(native=True)
        for update in ({"Нет!A1": 1}, {"План производства!F8": True},
                       {"План производства!F8": float("nan")}, {"План производства!F8": float("inf")},
                       {"План производства!F8": "250"}, {"План производства!F8": -1},
                       {"План производства!F8": 1e16}, {"Раб_капит!B5": 2.5},
                       {"План производства!F8": 1e-309},
                       {"РКЛ!AA7": 2}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                apply_updates(raw, update)
        for bad in (None, [], "{}"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                apply_updates(raw, bad)

    def test_zero_literal_with_extreme_decimal_exponent_is_bounded_and_preserved(self):
        raw = workbook(native=True)
        updated = apply_updates(raw, {"План производства!F8": Decimal("0e-1000000000")})
        formula = _xlsx(updated)[0]["План производства"]["F8"]["formula"]
        self.assertEqual(formula, "=(0/(+курс))*(+ВНД!$W$41)")
        self.assertLess(len(updated), len(raw) + 100)

    def test_formula_cache_never_becomes_input_and_unsupported_shape_is_rejected(self):
        raw = workbook({"План производства": {"C8": (999, "SUM(A1:A2)"), "F8": (999, "200/курс+SUM(A1:A2)")}}, native=True)
        fields = items(raw)
        for key in ("План производства!C8", "План производства!F8"):
            self.assertIsNone(fields[key]["value"])
            self.assertFalse(fields[key]["editable"])
            with self.assertRaises(ValueError):
                apply_updates(raw, {key: 100})

    def test_array_followers_shared_formulas_and_boolean_cells_are_protected(self):
        raw = workbook({"План производства": {"C8": (1, "TRANSPOSE(A1:A2)"), "D8": 2, "F8": (1, "(200/(+курс))*(+ВНД!$W$41)")},
                        "Труд": {"A15": "Оператор", "B15": True}}, native=True,
                       formula_attributes={("План производства", "C8"): {"t": "array", "ref": "C8:D8"},
                                           ("План производства", "F8"): {"t": "shared", "si": "1", "ref": "F8:F9"}})
        fields = items(raw)
        for key in ("План производства!C8", "План производства!D8", "План производства!F8", "Труд!B15"):
            self.assertFalse(fields[key]["editable"])
            with self.assertRaises(ValueError):
                apply_updates(raw, {key: 10})

    def test_production_exact_identity_year_mapping_and_explicit_confirmation(self):
        raw = workbook(native=True)
        source = production({"B4": "Препарат А 10 мг №50", "P4": 400, "Q4": 500})
        review = describe_inputs(raw, [{"name": "production.xlsx", "content": source}])
        fields = {i["key"]: i for i in review["items"]}
        self.assertEqual(fields["План производства!C8"]["proposals"][0]["value"], 150)
        self.assertEqual(fields["План производства!D8"]["proposals"][0]["value"], 180)
        self.assertEqual(fields["План производства!C8"]["proposals"][0]["period"], "2030")
        self.assertTrue(fields["План производства!C8"]["requires_decision"])
        self.assertTrue(any(w["code"] == "unmatched_production_product" for w in review["warnings"]))
        self.assertEqual(_xlsx(raw)[0]["План производства"]["C8"]["numeric_text"], "100")

    def test_production_mismatched_period_or_units_are_not_transferred(self):
        raw = workbook({"План производства": {"D7": "тонн"}}, native=True)
        review = describe_inputs(raw, [{"name": "production.xlsx", "content": production(year="2020")}])
        fields = {i["key"]: i for i in review["items"]}
        self.assertEqual(fields["План производства!C8"]["proposals"], [])
        self.assertEqual(fields["План производства!D8"]["proposals"], [])
        self.assertTrue(any(w["code"] == "production_period_mismatch" for w in review["warnings"]))
        self.assertTrue(any(w["code"] == "production_unit_mismatch" for w in review["warnings"]))

    def test_duplicate_price_candidates_have_stable_ids_and_no_automatic_choice(self):
        raw = workbook(native=True)
        source = prices({"C6": "SKU-B", "D6": "Препарат А 10 мг №5", "E6": 300,
                         "D7": "Препарат Б 10 мг №5", "E7": 120})
        files = [{"name": "price.xlsx", "content": source}]
        first, second = describe_inputs(raw, files), describe_inputs(raw, files)
        field = next(i for i in first["items"] if i["key"] == "План производства!F8")
        self.assertEqual([p["value"] for p in field["proposals"]], [250, 300])
        self.assertEqual(len({p["id"] for p in field["proposals"]}), 2)
        self.assertEqual(first, second)
        self.assertTrue(field["requires_decision"])
        self.assertEqual(field["value"], 200)
        codes = {w["code"] for w in first["warnings"]}
        self.assertIn("duplicate_price_product", codes)
        self.assertIn("unmatched_price_product", codes)
        self.assertIn("price_basis_confirmation", codes)

    def test_equal_unique_proposal_does_not_require_decision_to_keep_original(self):
        review = describe_inputs(workbook(native=True), [{"name": "price.xlsx", "content": prices({"E5": 200})}])
        field = next(i for i in review["items"] if i["key"] == "План производства!F8")
        self.assertEqual(field["proposals"][0]["value"], field["value"])
        self.assertTrue(field["proposals"][0]["basis_confirmation"])
        self.assertFalse(field["requires_decision"])
        self.assertIn("price_basis_confirmation", {w["code"] for w in review["warnings"]})

    def test_binary_tail_equivalence_is_bounded_and_never_hides_real_cent_changes(self):
        self.assertTrue(numbers_equivalent(10000.0, 10000.00000000001))
        self.assertFalse(numbers_equivalent(10000.0, 10000.01))
        self.assertFalse(numbers_equivalent(1e12, 1e12 + .01))
        self.assertFalse(numbers_equivalent(1e12, 1e12 + .001))
        self.assertFalse(numbers_equivalent(None, None))
        self.assertFalse(numbers_equivalent(True, 1))
        self.assertFalse(numbers_equivalent(float("nan"), float("nan")))
        review = describe_inputs(workbook(native=True), [{"name": "price.xlsx", "content": prices({"E5": 200.00000000000003})}])
        field = next(i for i in review["items"] if i["key"] == "План производства!F8")
        self.assertTrue(field["proposals"][0]["equivalent_to_original"])
        self.assertFalse(field["requires_decision"])
        self.assertEqual(field["value"], 200)
        # An explicitly chosen manual/source update still writes the selected
        # input; the equivalence check only removes false review conflicts.
        self.assertNotEqual(apply_updates(workbook(native=True), {"План производства!F8": 200.00000000000003}), workbook(native=True))

    def test_template_duplicate_identity_or_packaging_mismatch_blocks_price_matching(self):
        for extra, code in (({"A9": "Препарат А 10 мг №5", "F9": (3, "(200/(+курс))*(+ВНД!$W$41)")}, "duplicate_template_product"),
                            ({"C7": "тонн"}, "price_unit_mismatch")):
            raw = workbook({"План производства": extra}, native=True)
            review = describe_inputs(raw, [{"name": "price.xlsx", "content": prices()}])
            field = next(i for i in review["items"] if i["key"] == "План производства!F8")
            self.assertEqual(field["proposals"], [])
            self.assertIn(code, {w["code"] for w in review["warnings"]})

    def test_multiple_production_rows_remain_separate_candidates(self):
        raw = workbook(native=True)
        review = describe_inputs(raw, [{"name": "production.xlsx", "content": production({
            "B4": "Препарат А 10 мг №5", "P4": 160, "Q4": 190,
        })}])
        field = next(i for i in review["items"] if i["key"] == "План производства!C8")
        self.assertEqual([p["value"] for p in field["proposals"]], [150, 160])
        self.assertTrue(field["requires_decision"])
        self.assertIn("duplicate_production_product", {w["code"] for w in review["warnings"]})

    def test_source_numeric_formulas_and_arrays_are_not_used_as_proposals(self):
        raw = workbook(native=True)
        source = prices({"E5": (250, "200+50")})
        review = describe_inputs(raw, [{"name": "price.xlsx", "content": source}])
        self.assertEqual(next(i for i in review["items"] if i["key"] == "План производства!F8")["proposals"], [])
        self.assertIn("price_value_unavailable", {w["code"] for w in review["warnings"]})
        source = workbook({"План": {"B2": "Наименование препарата", "P2": "Объём 2030", "Q2": "Объём 2031",
                                      "B3": "Препарат А 10 мг №5", "P3": (150, "TRANSPOSE(A1:A2)"), "Q3": 180}},
                          formula_attributes={("План", "P3"): {"t": "array", "ref": "P3:Q3"}})
        fields = items(raw, [{"name": "production.xlsx", "content": source}])
        self.assertEqual(fields["План производства!C8"]["proposals"], [])
        self.assertEqual(fields["План производства!D8"]["proposals"], [])

    def test_cached_external_names_are_flagged_and_price_scalar_remains_evidence(self):
        source = prices({"D5": ("Препарат А 10 мг №5", "[1]External!A1")})
        review = describe_inputs(workbook(native=True), [{"name": "price.xlsx", "content": source}])
        field = next(i for i in review["items"] if i["key"] == "План производства!F8")
        self.assertEqual(field["proposals"][0]["value"], 250)
        self.assertTrue(field["requires_decision"])
        self.assertIn("cached_source_identity", {w["code"] for w in review["warnings"]})

    def test_balance_uses_closing_column_and_explicit_units_and_keeps_formula(self):
        raw = workbook({"Стоим_проекта": {"B28": (6, "(900-300)/курс*1000")}}, native=True)
        source = workbook({"Баланс": {"C2": "Единица измерения, тыс. сум.", "C10": "390", "C20": "600",
                                      "D10": 10, "D20": 5, "E10": 1200, "E20": 400}})
        field = items(raw, [{"name": "form1.xltx", "content": source}])["Стоим_проекта!B28"]
        self.assertEqual(field["proposals"][0]["value"], 800)
        self.assertEqual(field["proposals"][0]["cell"], "E10-E20")
        self.assertEqual(field["proposals"][0]["unit"], "тыс. UZS")
        self.assertTrue(field["requires_decision"])
        updated = apply_updates(raw, {field["key"]: field["proposals"][0]["value"]})
        self.assertEqual(_xlsx(updated)[0]["Стоим_проекта"]["B28"]["formula"], "=(1100-300)/курс*1000")

    def test_balance_ambiguous_codes_missing_currency_or_missing_value_are_not_zero(self):
        raw = workbook({"Стоим_проекта": {"B28": (6, "(900-300)/курс*1000")}}, native=True)
        for extra, expected in (({"C11": "390", "E11": 700}, "duplicate_balance_code"),
                                ({"C2": "USD"}, "balance_unit_unknown"),
                                ({"E20": None}, "balance_value_unavailable")):
            source = workbook({"Баланс": {"C2": "тыс. сум.", "C10": "390", "C20": "600", "E10": 800, "E20": 200, **extra}})
            review = describe_inputs(raw, [{"name": "form1.xlsx", "content": source}])
            field = next(i for i in review["items"] if i["key"] == "Стоим_проекта!B28")
            self.assertEqual(field["proposals"], [])
            self.assertIn(expected, {w["code"] for w in review["warnings"]})

    def test_primary_original_and_duplicate_bytes_are_not_proposed_again(self):
        raw, source = workbook(native=True), prices()
        fields = items(raw, [{"name": "original.xlsx", "content": raw},
                             {"name": "price.xlsx", "content": source},
                             {"name": "price-copy.xlsx", "content": source}])
        self.assertEqual(len(fields["План производства!F8"]["proposals"]), 1)

    def test_uploaded_paths_are_never_read_and_pdf_is_not_claimed_as_parsed(self):
        review = describe_inputs(workbook(native=True), [
            {"name": "price.xlsx", "path": str(Path(__file__).resolve())},
            {"name": "assets.pdf", "content": b"Synthetic source PDF"},
            {"name": "payroll.xlsx", "content": workbook({"Штат": {"A1": "Численность", "B1": 50}})},
        ])
        self.assertTrue(all(not i["proposals"] for i in review["items"]))
        self.assertIn("source_unavailable", {w["code"] for w in review["warnings"]})
        self.assertEqual(sum(w["code"] == "manual_source" for w in review["warnings"]), 2)

    def test_unsupported_workbook_is_diagnostic_and_writes_are_rejected(self):
        raw = workbook({"Лист": {"A1": 1}})
        self.assertEqual(describe_inputs(raw)["items"], [])
        self.assertEqual(describe_inputs(raw)["warnings"][0]["code"], "unsupported_template")
        with self.assertRaises(ValueError):
            apply_updates(raw, {"Лист!A1": 2})


if __name__ == "__main__":
    unittest.main()
