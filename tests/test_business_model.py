"""Independent arithmetic examples for the pure, explicit-input forecast engine."""
import copy
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.business_model import ModelValidationError, calculate_model, validate_model


D = Decimal


def inputs(months=12, **updates):
    result = {
        "title": "Synthetic manufacturing project", "currency": "USD", "start": "2027-01-01", "months": months,
        "tax_rate": "0.20", "discount_rate": "0.12", "opening_cash": "1000",
        "opening_receivables": "0", "opening_inventory": "0", "opening_payables": "0",
        "initial_investment": "1000", "receivable_days": "0", "inventory_days": "0", "payable_days": "0",
        "products": [{"name": "Product", "unit": "pack", "price": "10", "unit_cost": "4", "quantities": "100"}],
        "fixed_costs": "100", "assets": [], "loans": [], "equity": "0",
    }
    result.update(updates)
    return result


def number(row, name):
    return D(row[name])


class BusinessModelTests(unittest.TestCase):
    def test_material_cancellation_between_representable_values_blocks_excel_publication(self):
        model = inputs(1, tax_rate=0, fixed_costs=0, receivable_days=0, inventory_days=0, payable_days=0)
        model['products'][0].update(price='1000000000000000', unit_cost='999999999999999',
                                    quantities='1000000000000000', capacity=None)
        self.assertEqual(validate_model(model), [])
        with self.assertRaises(ModelValidationError) as error:
            calculate_model(model)
        self.assertEqual(error.exception.issues[0]['field'], 'precision')

    def test_inputs_that_excel_would_silently_round_are_rejected(self):
        model = inputs(1)
        for price in ('10000.000000000001', '999999999999999.999999999999'):
            model['products'][0]['price'] = price
            self.assertTrue(any(issue['field'] == 'products[0].price' for issue in validate_model(model)))
            with self.assertRaises(ModelValidationError):
                calculate_model(model)
        model['products'][0]['price'] = '1000000000000000'
        self.assertEqual(validate_model(model), [])

    def test_explicit_zero_complete_model_is_valid_and_json_safe(self):
        model = inputs(1, opening_cash=0, initial_investment=0, tax_rate=0, discount_rate=0,
                       fixed_costs=0, equity=0)
        model["products"][0].update(price=0, unit_cost=0, quantities=0, capacity=0)
        self.assertEqual(validate_model(model), [])
        result = calculate_model(model)
        self.assertEqual(result["rows"][0]["cash"], "0")
        self.assertIsNone(result["metrics"]["irr_annual"])
        self.assertIsNone(result["metrics"]["dscr"])
        self.assertIsNone(result["products"][0]["rows"][0]["utilisation"])
        json.dumps(result, allow_nan=False)

    def test_missing_is_not_zero_and_all_essential_fields_are_required(self):
        model = inputs()
        fields = ("opening_cash", "opening_receivables", "opening_inventory", "opening_payables", "initial_investment",
                  "tax_rate", "discount_rate", "receivable_days", "inventory_days", "payable_days", "fixed_costs", "equity", "assets", "loans")
        for field in fields:
            with self.subTest(field=field):
                incomplete = copy.deepcopy(model)
                del incomplete[field]
                self.assertTrue(any(issue["field"] == field for issue in validate_model(incomplete)))
                with self.assertRaises(ModelValidationError):
                    calculate_model(incomplete)
        model["products"][0]["quantities"] = [100] * 11 + [None]
        issues = validate_model(model)
        self.assertIn("products[0].quantities[11]", {issue["field"] for issue in issues})

    def test_monthly_profit_and_cash_have_independently_known_values(self):
        result = calculate_model(inputs(2))
        for row in result["rows"]:
            # 100*10 revenue - 100*4 COGS - 100 overhead = 500 profit before 20% tax.
            self.assertEqual(number(row, "revenue"), 1000)
            self.assertEqual(number(row, "cogs"), 400)
            self.assertEqual(number(row, "ebitda"), 500)
            self.assertEqual(number(row, "tax"), 100)
            self.assertEqual(number(row, "net_profit"), 400)
            self.assertEqual(number(row, "operating_cash_flow"), 400)
            self.assertEqual(number(row, "free_cash_flow"), 400)
            self.assertEqual(number(row, "balance_difference"), 0)
        self.assertEqual(number(result["rows"][-1], "cash"), 1800)
        self.assertEqual(number(result["rows"][-1], "equity_balance"), 1800)

    def test_two_loans_affect_profit_cash_balance_and_individual_schedules(self):
        model = inputs(2, loans=[
            {"name": "Loan A", "opening_balance": 100, "drawdowns": [0, 100], "principal": [50, 100], "interest": [10, 5]},
            {"name": "Loan B", "opening_balance": 200, "drawdowns": [100, 0], "principal": [50, 250], "interest": [20, 10]},
        ])
        result = calculate_model(model)
        first, last = result["rows"]
        # EBIT 500, interest 30, tax 94, NI 376, receipt 100, principal 100.
        self.assertEqual(number(first, "interest"), 30)
        self.assertEqual(number(first, "tax"), 94)
        self.assertEqual(number(first, "cash_change"), 376)
        self.assertEqual(number(first, "debt"), 300)
        self.assertEqual(number(first, "equity_balance"), 1076)
        # Second month: NI=388, new debt100, principal350 -> cash+138.
        self.assertEqual(number(last, "cash_change"), 138)
        self.assertEqual(number(last, "cash"), 1514)
        self.assertEqual(number(last, "debt"), 50)
        self.assertEqual(number(last, "equity_balance"), 1464)
        self.assertEqual(number(first, "balance_difference"), 0)
        self.assertEqual(number(last, "balance_difference"), 0)
        self.assertEqual(result["loans"][0]["rows"][-1]["closing_balance"], "50")
        self.assertEqual(result["loans"][1]["rows"][-1]["closing_balance"], "0")
        # DSCR adds paid interest back to OCF before dividing by total debt service.
        self.assertEqual(number(first, "cash_available_for_debt_service"), 406)
        self.assertAlmostEqual(number(first, "dscr"), D(406) / D(130), places=24)
        self.assertEqual(number(first, "free_cash_flow"), 400)

    def test_principal_overpayment_is_rejected_in_month_not_hidden_in_negative_debt(self):
        model = inputs(2, loans=[{"name": "Loan", "opening_balance": 100,
                                  "drawdowns": [0, 100], "principal": [101, 0], "interest": 0}])
        issues = validate_model(model)
        self.assertIn("loans[0].principal[0]", {issue["field"] for issue in issues})
        model["loans"][0]["principal"] = [100, 100]
        self.assertEqual(validate_model(model), [])
        self.assertEqual(calculate_model(model)["rows"][-1]["debt"], "0")

    def test_depreciation_includes_buildings_caps_at_zero_and_starts_at_commission(self):
        result = calculate_model(inputs(5, assets=[
            {"name": "Building", "value": 120, "life_months": 3, "commissioning_month": 0},
            {"name": "Equipment", "value": 90, "life_months": 2, "commissioning_month": 2},
        ]))
        self.assertEqual([number(row, "depreciation") for row in result["rows"]], [40, 85, 85, 0, 0])
        self.assertEqual([number(row, "capex") for row in result["rows"]], [0, 90, 0, 0, 0])
        self.assertEqual([number(row, "ppe") for row in result["rows"]], [80, 85, 0, 0, 0])
        self.assertTrue(all(number(row, "balance_difference") == 0 for row in result["rows"]))
        self.assertTrue(all(D(asset["closing_value"]) == 0 for asset in result["assets"]))

    def test_nonterminating_depreciation_never_leaves_negative_residual(self):
        result = calculate_model(inputs(60, assets=[
            {"name": "Machine", "value": 100, "life_months": 3, "commissioning_month": 0}]))
        total = sum((number(row, "depreciation") for row in result["rows"]), D(0))
        self.assertAlmostEqual(total, D(100), places=24)
        self.assertEqual(result["rows"][-1]["ppe"], "0")
        self.assertGreaterEqual(D(result["assets"][0]["closing_value"]), 0)

    def test_working_capital_uses_opening_balances_and_cash_reconciles(self):
        result = calculate_model(inputs(2, receivable_days=30, inventory_days=15, payable_days=30,
                                        opening_cash=100, opening_receivables=100, opening_inventory=50, opening_payables=20))
        first, second = result["rows"]
        # NWC closing: 1000 AR + 200 inventory - 400 AP = 800; opening NWC=130.
        self.assertEqual(number(first, "working_capital_change"), 670)
        self.assertEqual(number(first, "operating_cash_flow"), -270)
        self.assertEqual(number(first, "cash"), -170)
        self.assertEqual(number(second, "working_capital_change"), 0)
        self.assertEqual(number(second, "cash"), 230)
        for row in result["rows"]:
            assets = number(row, "cash") + number(row, "receivables") + number(row, "inventory") + number(row, "ppe")
            liabilities_equity = number(row, "payables") + number(row, "debt") + number(row, "equity_balance")
            self.assertEqual(assets, liabilities_equity)
        self.assertEqual(result["warnings"][0]["code"], "negative_cash")

    def test_equity_cash_receipt_does_not_inflate_profit(self):
        result = calculate_model(inputs(2, equity=[200, 300]))
        self.assertEqual(number(result["totals"], "net_profit"), 800)
        self.assertEqual(number(result["totals"], "equity"), 500)
        self.assertEqual(number(result["totals"], "cash"), 2300)
        self.assertEqual(number(result["totals"], "equity_balance"), 2300)

    def test_no_loss_tax_or_implicit_loss_carryforward(self):
        model = inputs(2, fixed_costs=[700, 100])
        result = calculate_model(model)
        self.assertEqual(number(result["rows"][0], "net_profit"), -100)
        self.assertEqual(number(result["rows"][0], "tax"), 0)
        self.assertEqual(number(result["rows"][1], "tax"), 100)

    def test_npv_uses_each_month_and_effective_annual_discount_not_repeated_first_year(self):
        model = inputs(24, discount_rate="0.12", tax_rate=0,
                       initial_investment=100, fixed_costs=0)
        # Independent per-flow annual fractional powers; values differ between years.
        model["products"][0].update(price=1, unit_cost=0, quantities=[10] * 12 + [20] * 12)
        result = calculate_model(model)
        with localcontext() as ctx:
            ctx.prec = 50
            expected = -D(100) + sum((D(10 if m <= 12 else 20) / D("1.12") ** (D(m) / 12) for m in range(1, 25)), D(0))
        self.assertAlmostEqual(D(result["metrics"]["npv"]), expected, places=24)
        self.assertEqual(result["annual"][0]["revenue"], "120")
        self.assertEqual(result["annual"][1]["revenue"], "240")

    def test_unique_irr_and_annualisation_independent_one_month_example(self):
        model = inputs(1, initial_investment=100, tax_rate=0, fixed_costs=0)
        model["products"][0].update(price=1, unit_cost=0, quantities=110)
        result = calculate_model(model)
        self.assertAlmostEqual(D(result["metrics"]["irr_monthly"]), D("0.1"), places=24)
        self.assertAlmostEqual(D(result["metrics"]["irr_annual"]), D("1.1") ** 12 - 1, places=24)
        self.assertAlmostEqual(D(result["metrics"]["payback_months"]), D(100) / 110, places=24)

    def test_multiple_irr_sign_changes_are_not_reported_as_single_root(self):
        model = inputs(2, initial_investment=100, tax_rate=0, fixed_costs=[0, 132])
        model["products"][0].update(price=1, unit_cost=0, quantities=[230, 0])
        result = calculate_model(model)
        self.assertIsNone(result["metrics"]["irr_monthly"])
        self.assertIsNone(result["metrics"]["irr_annual"])
        self.assertIn("несколько", result["metrics"]["irr_reason"])

    def test_irr_close_to_total_loss_does_not_use_an_incorrect_lower_bound(self):
        model = inputs(1, initial_investment="1000000000000000", tax_rate=0, fixed_costs=0)
        model["products"][0].update(price="0.000000000001", unit_cost=0, quantities="0.000000000001")
        result = calculate_model(model)
        with localcontext() as ctx:
            ctx.prec = 80
            ratio = D(result["metrics"]["irr_monthly"]) + 1
            self.assertLess(abs(ratio - D("1e-39")), D("1e-70"))

    def test_discounted_payback_interpolates_and_absence_of_recovery_is_null(self):
        model = inputs(2, initial_investment=1000, discount_rate=0)
        result = calculate_model(model)
        self.assertIsNone(result["metrics"]["payback_months"])
        self.assertIsNone(result["metrics"]["discounted_payback_months"])
        model["initial_investment"] = 600
        result = calculate_model(model)
        self.assertEqual(result["metrics"]["payback_months"], "1.5")
        self.assertEqual(result["metrics"]["discounted_payback_months"], "1.5")

    def test_capex_without_initial_investment_still_requires_recovery(self):
        model = inputs(2, initial_investment=0, tax_rate=0, assets=[
            {"name": "Machine", "value": 900, "life_months": 900, "commissioning_month": 1}])
        result = calculate_model(model)
        self.assertEqual(result["rows"][0]["free_cash_flow"], "-400")
        self.assertEqual(result["rows"][1]["free_cash_flow"], "500")
        self.assertEqual(result["metrics"]["payback_months"], "1.8")

    def test_break_even_uses_weighted_sales_mix_and_nonpositive_margin_is_null(self):
        model = inputs(1, fixed_costs=200, products=[
            {"name": "A", "unit": "pack", "price": 10, "unit_cost": 2, "quantities": 10},
            {"name": "B", "unit": "pack", "price": 20, "unit_cost": 10, "quantities": 20},
        ])
        result = calculate_model(model)
        # Revenue500, COGS220 -> weighted contribution280/500=56%.
        self.assertEqual(result["metrics"]["contribution_margin"], "0.56")
        self.assertAlmostEqual(D(result["metrics"]["break_even_revenue"]), D(200) / D("0.56"), places=24)
        model["products"][0]["unit_cost"] = 50
        result = calculate_model(model)
        self.assertIsNone(result["metrics"]["break_even_revenue"])
        self.assertTrue(any(w["code"] == "nonpositive_contribution" for w in result["warnings"]))

    def test_annual_totals_sum_flows_close_stocks_and_recompute_dscr(self):
        model = inputs(3, start="2027-12-01", loans=[{"name": "Loan", "opening_balance": 600,
                        "drawdowns": 0, "principal": [50, 100, 150], "interest": [10, 20, 30]}])
        result = calculate_model(model)
        self.assertEqual([item["months"] for item in result["annual"]], [1, 2])
        self.assertEqual(result["annual"][1]["revenue"], "2000")
        self.assertEqual(result["annual"][1]["cash"], result["rows"][-1]["cash"])
        self.assertEqual(result["totals"]["debt"], "300")
        with localcontext() as ctx:
            ctx.prec = 50
            expected = (D(500 - 96) + D(500 - 94)) / D(100 + 150 + 20 + 30)
        self.assertAlmostEqual(D(result["annual"][1]["dscr"]), expected, places=24)

    def test_bad_calendar_array_rates_and_nonfinite_values_report_fields(self):
        for field, invalid in (("start", "2027-02-30"), ("start", "2027-01-02"), ("start", "2100-12-01"),
                               ("months", 0), ("months", 61), ("months", True), ("tax_rate", "1.01"),
                               ("discount_rate", "NaN"), ("opening_cash", float("inf")),
                               ("opening_inventory", -1), ("fixed_costs", [0]), ("equity", None)):
            with self.subTest(field=field, invalid=invalid):
                self.assertTrue(any(issue["field"] == field for issue in validate_model(inputs(**{field: invalid}))))
        self.assertEqual(validate_model(None)[0]["field"], "model")

    def test_duplicate_names_and_capacity_excess_are_explicit_issues(self):
        model = inputs(2)
        model["products"][0]["capacity"] = [100, 90]
        self.assertIn("products[0].quantities[1]", {issue["field"] for issue in validate_model(model)})
        model["products"][0]["capacity"] = 100
        model["products"].append(copy.deepcopy(model["products"][0]))
        self.assertIn("products[1].name", {issue["field"] for issue in validate_model(model)})

    def test_out_of_range_numeric_exponents_are_issues_without_arithmetic_overflow(self):
        model = inputs(1, loans=[{"name": "Loan", "opening_balance": "1e99999999",
                                  "drawdowns": "0", "principal": "0", "interest": "0"}])
        self.assertIn("loans[0].opening_balance", {issue["field"] for issue in validate_model(model)})
        model["loans"][0]["opening_balance"] = 0
        model["loans"][0]["drawdowns"] = "1e-99999999"
        self.assertIn("loans[0].drawdowns", {issue["field"] for issue in validate_model(model)})

    def test_inputs_are_not_mutated_and_identical_models_are_reproducible(self):
        model = inputs(36, assets=[{"name": "Building", "value": 3600, "life_months": 36, "commissioning_month": 0}])
        before = copy.deepcopy(model)
        first = calculate_model(model)
        self.assertEqual(model, before)
        self.assertEqual(first, calculate_model(model))
        self.assertEqual(len(first["rows"]), 36)
        self.assertEqual(first["rows"][-1]["period"], "2029-12-01")
        self.assertEqual(first["period_end"], "2029-12-31")

    def test_report_period_includes_last_day_of_month_and_leap_february(self):
        for start, months, expected in (("2027-01-01", 12, "2027-12-31"),
                                        ("2027-01-01", 2, "2027-02-28"),
                                        ("2028-01-01", 2, "2028-02-29")):
            with self.subTest(start=start, months=months):
                result = calculate_model(inputs(months, start=start))
                self.assertEqual(result["period_end"], expected)
                self.assertEqual(result["annual"][-1]["period_end"], expected)


if __name__ == "__main__":
    unittest.main()
