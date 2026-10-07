"""Deterministic monthly business-plan / feasibility-study calculations.

This module reads a complete, explicit input model. It never reads the ledger,
executes workbook formulas, makes network requests, or substitutes missing data
with zero. Monetary inputs are net of VAT and share one declared currency.
Values in the returned JSON-compatible model are decimal strings. Calculations
retain precision; presentation/export code may round values for display.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any


ZERO = Decimal(0)
ONE = Decimal(1)
MAX_AMOUNT = Decimal("1000000000000000")
PRECISION = 80
SCHEMA_VERSION = "1.0"

ASSUMPTIONS = [
    "Все суммы указаны в одной валюте, без НДС; пересчёт валют не выполняется.",
    "Оборотный капитал: дебиторская задолженность = выручка × дни / 30, запасы и кредиторская задолженность = себестоимость × дни / 30.",
    "Производство равно реализации. Себестоимость продукции не включает постоянные расходы, амортизацию и проценты.",
    "Амортизация линейная, без ликвидационной стоимости, с месяца ввода актива; остаточная стоимость не может быть отрицательной.",
    "Проценты и погашение кредитов задаются явным графиком; налог рассчитывается только с положительной прибыли, без переноса убытков.",
    "DSCR = денежный поток до уплаты процентов / (проценты + погашение основного долга). При отсутствии обслуживания долга DSCR не определён.",
    "Денежный поток проекта рассчитан до финансирования: EBIT минус налог без процентов, плюс амортизация, минус прирост оборотного капитала и CAPEX.",
    "Начальные инвестиции являются отдельным оттоком в момент 0. CAPEX периода включается в месяц ввода актива; один расход нельзя указывать одновременно в обоих местах.",
    "Начальный капитал выведен из начального баланса. Взносы в капитал не являются доходом; получение и погашение кредита не являются прибылью.",
    "Годовая ставка дисконтирования переведена в эффективную месячную ставку. Потоки периода относятся к концу месяца.",
    "В NPV, IRR и окупаемость не включены продажа активов, возврат оборотного капитала и другие терминальные поступления после последнего месяца.",
    "Безубыточность рассчитана при неизменных ценах, удельных затратах и структуре продаж; показатель break_even_revenue — безубыточность по EBITDA, без амортизации, процентов и налога.",
]


class ModelValidationError(ValueError):
    """Structured validation failure for callers to return as input issues."""

    def __init__(self, issues: list[dict[str, str]]):
        self.issues = issues
        super().__init__("; ".join(f"{i['field']}: {i['message']}" for i in issues))


def _month(start: date, offset: int) -> date:
    total = start.year * 12 + start.month - 1 + offset
    return date(total // 12, total % 12 + 1, 1)


def _month_end(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def _text(value: Any, field: str, issues: list, maximum: int = 160) -> str:
    if not isinstance(value, str) or not value.strip():
        issues.append({"field": field, "message": "Обязательное непустое текстовое значение."})
        return ""
    result = value.strip()
    if len(result) > maximum or any(ord(c) < 32 for c in result):
        issues.append({"field": field, "message": f"Допустимо до {maximum} символов без управляющих знаков."})
    return result


def _number(value: Any, field: str, issues: list, maximum: Decimal = MAX_AMOUNT) -> Decimal:
    if value is None or value == "":
        issues.append({"field": field, "message": "Значение отсутствует. Для отсутствующего движения укажите явный 0."})
        return ZERO
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        issues.append({"field": field, "message": "Ожидается число."})
        return ZERO
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        issues.append({"field": field, "message": "Ожидается конечное число."})
        return ZERO
    if not number.is_finite():
        issues.append({"field": field, "message": "Число должно быть конечным."})
        return ZERO
    if number < ZERO or number > maximum:
        issues.append({"field": field, "message": f"Допустимый диапазон: 0–{maximum}."})
        return ZERO
    # Bound exponent/precision as well as magnitude to keep untrusted models small.
    digits = list(number.as_tuple().digits)
    while digits and digits[-1] == 0:
        digits.pop()
    if number != ZERO and (number.as_tuple().exponent < -12 or len(digits) > 15):
        issues.append({"field": field, "message": "Для совместимости с Excel допустимо не более 12 знаков после запятой и 15 значащих цифр."})
        return ZERO
    return number


def _integer(value: Any, field: str, issues: list, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        issues.append({"field": field, "message": f"Ожидается целое число от {minimum} до {maximum}."})
        return minimum
    return value


def _array(value: Any, field: str, months: int, issues: list) -> list[Decimal]:
    if isinstance(value, list):
        if len(value) != months:
            issues.append({"field": field, "message": f"Ожидается {months} месячных значений; получено {len(value)}."})
            return [ZERO] * months
        return [_number(v, f"{field}[{i}]", issues) for i, v in enumerate(value)]
    # An explicit scalar means the same value for every month; absence is an issue.
    return [_number(value, field, issues)] * months


def _normalise(inputs: Any) -> tuple[dict, list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    if not isinstance(inputs, dict):
        return {}, [{"field": "model", "message": "Ожидается объект исходных данных."}]
    result: dict = {"title": _text(inputs.get("title"), "title", issues)}
    currency = inputs.get("currency")
    if currency not in ("USD", "UZS", "EUR"):
        issues.append({"field": "currency", "message": "Укажите одну валюту: USD, UZS или EUR."})
    result["currency"] = currency
    months = _integer(inputs.get("months"), "months", issues, 1, 60)
    result["months"] = months
    start_text = inputs.get("start")
    try:
        if not isinstance(start_text, str) or not re.fullmatch(r"\d{4}-\d{2}-01", start_text):
            raise ValueError
        start = date.fromisoformat(start_text)
        if not 2000 <= start.year <= 2100 or _month(start, months - 1).year > 2100:
            raise ValueError
    except ValueError:
        issues.append({"field": "start", "message": "Начало — первое число месяца YYYY-MM-01, весь период в пределах 2000–2100 годов."})
        start = date(2000, 1, 1)
    result["start"] = start
    for field in ("opening_cash", "opening_receivables", "opening_inventory", "opening_payables", "initial_investment"):
        result[field] = _number(inputs.get(field), field, issues)
    result["tax_rate"] = _number(inputs.get("tax_rate"), "tax_rate", issues, ONE)
    result["discount_rate"] = _number(inputs.get("discount_rate"), "discount_rate", issues, Decimal(10))
    for field in ("receivable_days", "inventory_days", "payable_days"):
        result[field] = _number(inputs.get(field), field, issues, Decimal(3650))
    result["fixed_costs"] = _array(inputs.get("fixed_costs"), "fixed_costs", months, issues)
    result["equity"] = _array(inputs.get("equity"), "equity", months, issues)
    for collection in ("products", "assets", "loans"):
        entries = inputs.get(collection)
        result[collection] = []
        if not isinstance(entries, list) or (collection == "products" and not entries):
            message = "Нужна хотя бы одна продукция." if collection == "products" else "Укажите список; если нет, явно укажите []."
            issues.append({"field": collection, "message": message})
            continue
        if len(entries) > 200:
            issues.append({"field": collection, "message": "Допустимо до 200 строк."})
            continue
        names: set[str] = set()
        for index, entry in enumerate(entries):
            prefix = f"{collection}[{index}]"
            if not isinstance(entry, dict):
                issues.append({"field": prefix, "message": "Ожидается объект строки."})
                continue
            name = _text(entry.get("name"), f"{prefix}.name", issues)
            if name.casefold() in names:
                issues.append({"field": f"{prefix}.name", "message": "Название должно быть уникальным внутри списка."})
            names.add(name.casefold())
            normal = {"name": name}
            if collection == "products":
                normal["unit"] = _text(entry.get("unit"), f"{prefix}.unit", issues, 40)
                for field in ("price", "unit_cost"):
                    normal[field] = _number(entry.get(field), f"{prefix}.{field}", issues)
                normal["quantities"] = _array(entry.get("quantities"), f"{prefix}.quantities", months, issues)
                if "capacity" in entry and entry["capacity"] is not None:
                    normal["capacity"] = _array(entry["capacity"], f"{prefix}.capacity", months, issues)
                    for month, (quantity, capacity) in enumerate(zip(normal["quantities"], normal["capacity"])):
                        if quantity > capacity:
                            issues.append({"field": f"{prefix}.quantities[{month}]", "message": "Объём превышает заданную мощность."})
                else:
                    normal["capacity"] = None
            elif collection == "assets":
                normal["value"] = _number(entry.get("value"), f"{prefix}.value", issues)
                normal["life_months"] = _integer(entry.get("life_months"), f"{prefix}.life_months", issues, 1, 1200)
                normal["commissioning_month"] = _integer(entry.get("commissioning_month"), f"{prefix}.commissioning_month", issues, 0, months)
            else:
                normal["opening_balance"] = _number(entry.get("opening_balance"), f"{prefix}.opening_balance", issues)
                for field in ("drawdowns", "principal", "interest"):
                    normal[field] = _array(entry.get(field), f"{prefix}.{field}", months, issues)
                balance = normal["opening_balance"]
                for month, (drawdown, principal) in enumerate(zip(normal["drawdowns"], normal["principal"])):
                    balance += drawdown - principal
                    if balance < ZERO:
                        issues.append({"field": f"{prefix}.principal[{month}]", "message": "Погашение превышает остаток долга с учётом получения кредита этого месяца."})
            result[collection].append(normal)
    return result, issues


def validate_model(inputs: dict) -> list[dict[str, str]]:
    """Return every identified missing/invalid field; explicit zero is valid."""
    with localcontext() as context:
        context.prec = PRECISION
        return _normalise(inputs)[1]


def _serialise(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == ZERO:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    return value


def _npv(flows: list[Decimal], rate: Decimal) -> Decimal:
    factor = ONE
    total = ZERO
    for value in flows:
        total += value / factor
        factor *= ONE + rate
    return total


def _irr(flows: list[Decimal]) -> tuple[Decimal | None, str | None]:
    signs = [1 if item > ZERO else -1 for item in flows if item != ZERO]
    if sum(a != b for a, b in zip(signs, signs[1:])) > 1:
        return None, "IRR не показана: несколько смен знака потока могут давать несколько корней."
    if not signs or signs[0] != -1 or signs[-1] != 1:
        return None, "IRR не определена: нужен начальный отток и последующие положительные потоки."
    low = -ONE + Decimal("1e-70")
    high = ONE
    while _npv(flows, high) > ZERO and high < Decimal("1e60"):
        high = high * 2 + ONE
    if _npv(flows, low) < ZERO or _npv(flows, high) > ZERO:
        return None, "IRR выходит за числовые пределы расчёта."
    for _ in range(320):
        midpoint = (low + high) / 2
        value = _npv(flows, midpoint)
        if value > ZERO:
            low = midpoint
        else:
            high = midpoint
        if high - low < Decimal("1e-35") * max(Decimal("1e-60"), abs(ONE + midpoint)):
            break
    return (low + high) / 2, None


def _payback(flows: list[Decimal], rate: Decimal = ZERO) -> Decimal | None:
    """First cumulative recovery; interpolate a positive month-end flow."""
    cumulative = flows[0]
    invested = cumulative < ZERO
    factor = ONE
    for month, value in enumerate(flows[1:], 1):
        factor *= ONE + rate
        discounted = value / factor
        before = cumulative
        cumulative += discounted
        if cumulative < ZERO:
            invested = True
        if invested and before < ZERO and cumulative >= ZERO and discounted > ZERO:
            return Decimal(month - 1) + (-before / discounted)
    return None if invested else ZERO


FLOW_FIELDS = (
    "revenue", "cogs", "fixed_costs", "ebitda", "depreciation", "ebit", "interest", "tax", "tax_unlevered", "net_profit",
    "working_capital_change", "operating_cash_flow", "cash_available_for_debt_service", "capex", "loan_drawdowns", "loan_principal", "equity", "cash_change", "free_cash_flow",
)
STOCK_FIELDS = ("receivables", "inventory", "payables", "working_capital", "cash", "debt", "ppe", "equity_balance", "balance_difference")


def _summary(rows: list[dict]) -> dict:
    result = {field: sum((row[field] for row in rows), ZERO) for field in FLOW_FIELDS}
    result.update({field: rows[-1][field] for field in STOCK_FIELDS})
    debt_service = result["loan_principal"] + result["interest"]
    result["debt_service"] = debt_service
    result["dscr"] = result["cash_available_for_debt_service"] / debt_service if debt_service > ZERO else None
    result["net_margin"] = result["net_profit"] / result["revenue"] if result["revenue"] > ZERO else None
    return result


def calculate_model(inputs: dict) -> dict:
    """Calculate complete monthly statements; reject partial models explicitly."""
    with localcontext() as context:
        context.prec = PRECISION
        model, issues = _normalise(inputs)
        if issues:
            raise ModelValidationError(issues)
        months = model["months"]
        assets = [{**entry, "remaining": entry["value"], "depreciation": entry["value"] / entry["life_months"]}
                  for entry in model["assets"]]
        loans = [{**entry, "balance": entry["opening_balance"], "rows": []} for entry in model["loans"]]
        ppe = sum((asset["value"] for asset in assets if asset["commissioning_month"] == 0), ZERO)
        debt = sum((loan["opening_balance"] for loan in loans), ZERO)
        cash = model["opening_cash"]
        receivables = model["opening_receivables"]
        inventory = model["opening_inventory"]
        payables = model["opening_payables"]
        working_capital = receivables + inventory - payables
        equity_balance = cash + receivables + inventory + ppe - payables - debt
        opening = {"cash": cash, "receivables": receivables, "inventory": inventory, "payables": payables,
                   "ppe": ppe, "debt": debt, "equity_balance": equity_balance, "balance_difference": ZERO,
                   "working_capital": working_capital}
        products = [{"name": product["name"], "unit": product["unit"], "price": product["price"],
                     "unit_cost": product["unit_cost"], "rows": []} for product in model["products"]]
        rows: list[dict] = []
        warnings: list[dict] = []
        for index in range(months):
            month = index + 1
            period = _month(model["start"], index).isoformat()
            revenue = ZERO
            cogs = ZERO
            for product, output in zip(model["products"], products):
                quantity = product["quantities"][index]
                sales = quantity * product["price"]
                cost = quantity * product["unit_cost"]
                capacity = product["capacity"][index] if product["capacity"] is not None else None
                output["rows"].append({"month": month, "period": period, "quantity": quantity, "revenue": sales, "cogs": cost,
                                       "contribution": sales - cost, "capacity": capacity,
                                       "utilisation": quantity / capacity if capacity is not None and capacity > ZERO else None})
                revenue += sales
                cogs += cost
            capex = sum((asset["value"] for asset in assets if asset["commissioning_month"] == month), ZERO)
            depreciation = ZERO
            for asset in assets:
                if asset["commissioning_month"] <= month:
                    amount = min(asset["remaining"], asset["depreciation"])
                    asset["remaining"] -= amount
                    depreciation += amount
            ppe += capex - depreciation
            if abs(ppe) < Decimal("1e-24"):
                ppe = ZERO
            drawdowns = ZERO
            principal = ZERO
            interest = ZERO
            for loan in loans:
                opening_debt = loan["balance"]
                loan["balance"] += loan["drawdowns"][index] - loan["principal"][index]
                loan["rows"].append({"month": month, "period": period, "opening_balance": opening_debt,
                                     "drawdowns": loan["drawdowns"][index], "principal": loan["principal"][index],
                                     "interest": loan["interest"][index], "closing_balance": loan["balance"]})
                drawdowns += loan["drawdowns"][index]
                principal += loan["principal"][index]
                interest += loan["interest"][index]
            debt = sum((loan["balance"] for loan in loans), ZERO)
            fixed_costs = model["fixed_costs"][index]
            ebitda = revenue - cogs - fixed_costs
            ebit = ebitda - depreciation
            tax = max(ebit - interest, ZERO) * model["tax_rate"]
            tax_unlevered = max(ebit, ZERO) * model["tax_rate"]
            net_profit = ebit - interest - tax
            receivables = revenue * model["receivable_days"] / 30
            inventory = cogs * model["inventory_days"] / 30
            payables = cogs * model["payable_days"] / 30
            next_working_capital = receivables + inventory - payables
            working_capital_change = next_working_capital - working_capital
            working_capital = next_working_capital
            operating_cash_flow = net_profit + depreciation - working_capital_change
            equity = model["equity"][index]
            cash_change = operating_cash_flow - capex + drawdowns - principal + equity
            cash += cash_change
            equity_balance += equity + net_profit
            balance_difference = cash + receivables + inventory + ppe - payables - debt - equity_balance
            if abs(balance_difference) < Decimal("1e-24"):
                balance_difference = ZERO
            free_cash_flow = ebit - tax_unlevered + depreciation - working_capital_change - capex
            debt_service = principal + interest
            cash_available_for_debt_service = operating_cash_flow + interest
            dscr = cash_available_for_debt_service / debt_service if debt_service > ZERO else None
            contribution_margin = (revenue - cogs) / revenue if revenue > ZERO else None
            break_even_revenue = fixed_costs / contribution_margin if contribution_margin is not None and contribution_margin > ZERO else None
            row = {"month": month, "period": period, "revenue": revenue, "cogs": cogs, "fixed_costs": fixed_costs,
                   "ebitda": ebitda, "depreciation": depreciation, "ebit": ebit, "interest": interest, "tax": tax,
                   "tax_unlevered": tax_unlevered, "net_profit": net_profit, "receivables": receivables,
                   "inventory": inventory, "payables": payables, "working_capital": working_capital,
                   "working_capital_change": working_capital_change, "operating_cash_flow": operating_cash_flow,
                   "cash_available_for_debt_service": cash_available_for_debt_service,
                   "capex": capex, "loan_drawdowns": drawdowns, "loan_principal": principal, "equity": equity,
                   "cash_change": cash_change, "cash": cash, "debt": debt, "ppe": ppe, "equity_balance": equity_balance,
                   "balance_difference": balance_difference, "free_cash_flow": free_cash_flow, "dscr": dscr,
                   "debt_service": debt_service, "contribution_margin": contribution_margin,
                   "break_even_revenue": break_even_revenue}
            rows.append(row)
            if cash < ZERO:
                warnings.append({"code": "negative_cash", "month": month, "period": period,
                                 "message": "Отрицательный остаток денег: требуются финансирование или изменение плана."})
            if dscr is not None and dscr < ONE:
                warnings.append({"code": "low_dscr", "month": month, "period": period,
                                 "message": "Операционный денежный поток не покрывает обслуживание долга (DSCR < 1)."})
            if revenue > ZERO and revenue <= cogs:
                warnings.append({"code": "nonpositive_contribution", "month": month, "period": period,
                                 "message": "Цена и структура продаж не дают положительной маржи для покрытия постоянных расходов."})
        annual = []
        for year in sorted({row["period"][:4] for row in rows}):
            year_rows = [row for row in rows if row["period"].startswith(year)]
            annual.append({"year": int(year), "months": len(year_rows), "period_start": year_rows[0]["period"],
                           "period_end": _month_end(date.fromisoformat(year_rows[-1]["period"])), **_summary(year_rows)})
        totals = _summary(rows)
        flows = [-model["initial_investment"]] + [row["free_cash_flow"] for row in rows]
        monthly_discount_rate = (ONE + model["discount_rate"]) ** (ONE / 12) - ONE
        monthly_irr, irr_reason = _irr(flows)
        annual_irr = (ONE + monthly_irr) ** 12 - ONE if monthly_irr is not None else None
        # Excel and browser numeric displays use finite double precision. Keep
        # unsupported annualisation explicit rather than exporting an empty cell.
        if annual_irr is not None and abs(annual_irr) > Decimal('1e300'):
            annual_irr = None
            irr_reason = 'Годовая IRR выходит за числовые пределы экспорта; показатель не представлен.'
        total_contribution = totals["revenue"] - totals["cogs"]
        weighted_margin = total_contribution / totals["revenue"] if totals["revenue"] > ZERO else None
        break_even = totals["fixed_costs"] / weighted_margin if weighted_margin is not None and weighted_margin > ZERO else None
        metrics = {"npv": _npv(flows, monthly_discount_rate), "monthly_discount_rate": monthly_discount_rate,
                   "discount_rate": model["discount_rate"], "irr_monthly": monthly_irr,
                   "irr_annual": annual_irr,
                   "irr_reason": irr_reason, "payback_months": _payback(flows),
                   "discounted_payback_months": _payback(flows, monthly_discount_rate),
                   "initial_investment": model["initial_investment"], "contribution_margin": weighted_margin,
                   "break_even_revenue": break_even,
                   "break_even_average_monthly_revenue": break_even / months if break_even is not None else None,
                   "dscr": totals["dscr"], "net_margin": totals["net_margin"],
                   "minimum_cash": min(row["cash"] for row in rows),
                   "maximum_balance_difference": max(abs(row["balance_difference"]) for row in rows)}
        for loan in loans:
            loan.pop("balance")
        for product in products:
            product["total_quantity"] = sum((row["quantity"] for row in product["rows"]), ZERO)
            product["total_revenue"] = sum((row["revenue"] for row in product["rows"]), ZERO)
            product["total_cogs"] = sum((row["cogs"] for row in product["rows"]), ZERO)
        return _serialise({"schema_version": SCHEMA_VERSION, "title": model["title"], "currency": model["currency"],
                           "months": months, "period_start": model["start"], "period_end": _month_end(_month(model["start"], months - 1)),
                           "opening_balance": opening, "rows": rows, "annual": annual, "totals": totals,
                           "metrics": metrics, "products": products, "loans": loans,
                           "assets": [{"name": asset["name"], "value": asset["value"], "life_months": asset["life_months"],
                                       "commissioning_month": asset["commissioning_month"], "closing_value": asset["remaining"]} for asset in assets],
                           "assumptions": ASSUMPTIONS.copy(), "warnings": warnings})
