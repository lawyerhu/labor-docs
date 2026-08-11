from decimal import Decimal

from app.domain.calculations import calculate_claim
from app.domain.readiness import assess_readiness


def test_incomplete_litigation_case_is_still_formally_generatable():
    result = assess_readiness({"case_stage": "litigation", "party_side": "employer", "data": {}})

    assert result.readiness == "formal_with_placeholders"
    assert "原告名称" in result.missing_fields
    assert "仲裁裁决送达日期" in result.missing_fields
    assert result.can_generate is True


def test_complete_case_without_conflicts_is_marked_complete():
    result = assess_readiness(
        {
            "case_stage": "arbitration",
            "party_side": "worker",
            "data": {
                "parties": {
                    "initiating": {
                        "name": "张某",
                        "address": "苏州市",
                        "contact": "13800000000",
                        "id_number": "320500示例",
                    },
                    "opposing": {"name": "某公司", "address": "苏州市", "credit_code": "9132示例"},
                },
                "employment_facts": {
                    "start_date": "2025-01-01",
                    "position": "工程师",
                    "summary": "双方存在劳动关系。",
                },
                "arbitration": {"committee": "苏州市某劳动人事争议仲裁委员会"},
                "claims": [{"kind": "wages", "title": "支付工资", "amount": 5000, "basis": "2025年1月工资"}],
                "legal_basis": [{"citation": "已核验示例", "verified": True}],
            },
        }
    )

    assert result.readiness == "formal_complete"
    assert result.missing_fields == []
    assert result.can_generate is True


def test_supported_claim_calculations_use_known_results():
    assert calculate_claim("double_wage", {"monthly_wage": 4452, "months": 11}).amount == Decimal("48972.00")
    assert calculate_claim(
        "annual_leave",
        {"items": [{"entitled_days": 3, "taken_days": 0, "monthly_wage": 4452}]},
    ).amount == Decimal("1228.14")


def test_missing_calculation_basis_returns_placeholder_instead_of_guessing():
    result = calculate_claim("overtime", {"hours": 12})

    assert result.amount is None
    assert "待填入" in result.display
