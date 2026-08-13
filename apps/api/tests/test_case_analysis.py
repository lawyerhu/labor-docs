import asyncio

import app.services.case_analysis as case_analysis
from app.services.case_analysis import CaseAnalyzer, SENSITIVE_TERMS, _fallback, _ground_legal_basis, _route
from app.services.legal_research import LegalSnapshot


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


def test_analysis_keeps_company_lookup_when_other_yuandian_categories_fail(monkeypatch):
    calls: list[str] = []

    class Provider:
        async def search_law(self, _query):
            calls.append("law")
            raise RuntimeError("law unavailable")

        async def search_cases(self, _query):
            calls.append("case")
            raise RuntimeError("case unavailable")

        async def search_company(self, query):
            calls.append(f"company:{query}")
            return LegalSnapshot(
                query,
                True,
                "2026-08-14T00:00:00Z",
                "yuandian:mcp:company",
                {"registered_address": "苏州市吴中区", "court": "苏州市吴中区人民法院"},
            )

    async def complete_json(**_kwargs):
        return {
            "case_stage": "litigation",
            "party_side": "worker",
            "summary": "已分析",
            "legal_analysis": "类案提示工资和解除材料。",
            "follow_up_questions": ["请补充仲裁裁决送达日期。"],
            "data_patch": {
                "evidence_requirements": [
                    {"suggested_evidence": "工资记录", "fact_to_prove": "证明工资标准"},
                ],
            },
        }

    monkeypatch.setattr(case_analysis, "YuandianLegalResearchProvider", Provider)
    monkeypatch.setattr(case_analysis, "complete_json", complete_json)

    result = asyncio.run(
        CaseAnalyzer().analyze(
            facts="甲有限公司不服仲裁裁决。",
            claims_text="请求确认无需支付违约金。",
            supplement="",
            current_data={"parties": {"opposing": {"type": "company", "name": "甲有限公司"}}},
            round_number=1,
        )
    )

    assert calls == ["law", "case", "company:甲有限公司"]
    assert result["data_patch"]["court"] == "苏州市吴中区人民法院"
    assert result["data_patch"]["evidence_requirements"][0]["suggested_evidence"] == "工资记录"
