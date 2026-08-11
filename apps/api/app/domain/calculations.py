from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


MONEY = Decimal("0.01")


@dataclass(frozen=True)
class CalculationResult:
    amount: Decimal | None
    formula: str
    display: str


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _missing(*names: str) -> CalculationResult:
    detail = "、".join(names)
    return CalculationResult(None, "", f"[待填入：{detail}]")


def calculate_claim(kind: str, inputs: dict[str, Any]) -> CalculationResult:
    supplied = _decimal(inputs.get("amount"))
    if supplied is not None:
        amount = _money(supplied)
        return CalculationResult(amount, "用户确认金额", f"{amount:,.2f}元")

    if kind == "double_wage":
        wage, months = _decimal(inputs.get("monthly_wage")), _decimal(inputs.get("months"))
        if wage is None or months is None:
            return _missing("月工资、计算月数或金额")
        amount = _money(wage * months)
        return CalculationResult(amount, f"{wage} × {months}", f"{amount:,.2f}元")

    if kind == "annual_leave":
        items = inputs.get("items") or []
        if not items:
            return _missing("应休天数、已休天数、工资基数或金额")
        total = Decimal("0")
        formulas: list[str] = []
        for item in items:
            entitled = _decimal(item.get("entitled_days"))
            taken = _decimal(item.get("taken_days"))
            wage = _decimal(item.get("monthly_wage"))
            if entitled is None or taken is None or wage is None:
                return _missing("应休天数、已休天数、工资基数或金额")
            remaining = max(entitled - taken, Decimal("0"))
            total += remaining * wage / Decimal("21.75") * Decimal("2")
            formulas.append(f"({entitled}-{taken})×{wage}÷21.75×200%")
        amount = _money(total)
        return CalculationResult(amount, " + ".join(formulas), f"{amount:,.2f}元")

    if kind == "overtime":
        wage = _decimal(inputs.get("monthly_wage"))
        hours = _decimal(inputs.get("hours"))
        multiplier = _decimal(inputs.get("multiplier"))
        if wage is None or hours is None or multiplier is None:
            return _missing("月工资、加班小时、计付倍数或金额")
        amount = _money(wage / Decimal("21.75") / Decimal("8") * hours * multiplier)
        return CalculationResult(amount, f"{wage}÷21.75÷8×{hours}×{multiplier}", f"{amount:,.2f}元")

    if kind in {"economic_compensation", "illegal_termination"}:
        wage = _decimal(inputs.get("monthly_wage"))
        years = _decimal(inputs.get("compensation_years"))
        if wage is None or years is None:
            return _missing("月工资、补偿年限或金额")
        factor = Decimal("2") if kind == "illegal_termination" else Decimal("1")
        amount = _money(wage * years * factor)
        return CalculationResult(amount, f"{wage}×{years}×{factor}", f"{amount:,.2f}元")

    return _missing("金额、计算基数、期间或计算方式")

