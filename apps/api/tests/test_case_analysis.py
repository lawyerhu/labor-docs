from app.services.case_analysis import SENSITIVE_TERMS, _fallback, _ground_legal_basis, _route


def test_fallback_asks_one_non_sensitive_follow_up_round():
    result = _fallback("2025年入职，仲裁裁决已经送达。", "不服仲裁裁决中的违约金。", "", 1)

    assert result["case_stage"] == "litigation"
    assert result["follow_up_questions"]
    assert all(not any(term in question for term in SENSITIVE_TERMS) for question in result["follow_up_questions"])


def test_second_round_never_asks_again():
    result = _fallback("入职后发生工资争议。", "支付工资。", "工资每月一万元。", 2)

    assert result["follow_up_questions"] == []


def test_route_recognizes_company_litigation_side():
    assert _route("本公司不服仲裁裁决，拟向法院起诉") == ("litigation", "employer")


def test_legal_basis_must_be_present_in_verified_yuandian_result():
    parsed = {"data_patch": {"legal_basis": [{"citation": "劳动合同法第四十七条"}, {"citation": "虚构法条"}]}}
    research = {"law": {"verified": True, "source": "yuandian:mcp:law", "retrieved_at": "2026-08-12", "content": {"text": "劳动合同法第四十七条"}}}

    _ground_legal_basis(parsed, research)

    assert [item["citation"] for item in parsed["data_patch"]["legal_basis"]] == ["劳动合同法第四十七条"]
    assert parsed["data_patch"]["legal_basis"][0]["verified"] is True
