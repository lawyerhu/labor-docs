import asyncio

import app.services.remote_generation as remote_generation
from app.services.legal_research import LegalSnapshot
from app.services.remote_generation import _merge_known_values, research_for_generation


def test_ai_extracted_case_fields_fill_blanks_without_overwriting_confirmed_values():
    current = {
        "parties": {"initiating": {"name": "已确认的公司", "address": ""}},
        "arbitration": {},
    }
    extracted = {
        "parties": {"initiating": {"name": "材料中的其他名称", "address": "苏州市吴中区"}},
        "arbitration": {"award_number": "苏劳人仲案字〔2026〕1号"},
    }

    merged = _merge_known_values(current, extracted)

    assert merged["parties"]["initiating"]["name"] == "已确认的公司"
    assert merged["parties"]["initiating"]["address"] == "苏州市吴中区"
    assert merged["arbitration"]["award_number"] == "苏劳人仲案字〔2026〕1号"


def test_generation_researches_law_cases_and_company_before_drafting():
    calls: list[tuple[str, str]] = []

    class Provider:
        async def search_law(self, query):
            calls.append(("law", query))
            return LegalSnapshot(query, True, "2026-08-14", "yuandian:mcp:law", {"text": "现行法条"})

        async def search_cases(self, query):
            calls.append(("case", query))
            return LegalSnapshot(query, True, "2026-08-14", "yuandian:mcp:case", {"text": "相关案例"})

        async def search_company(self, query):
            calls.append(("company", query))
            return LegalSnapshot(query, True, "2026-08-14", "yuandian:mcp:company", {"text": "企业信息"})

    case = {
        "case_stage": "litigation",
        "party_side": "employer",
        "data": {
            "parties": {"initiating": {"type": "company", "name": "甲有限公司"}},
            "claims": [{"title": "不承担仲裁裁决确定的违约金"}],
        },
    }

    result = asyncio.run(research_for_generation(case, {"evidence-1": "仲裁裁决认定公司承担违约金。"}, provider=Provider()))

    assert [category for category, _query in calls] == ["law", "case", "company"]
    assert result["law"]["verified"] is True
    assert result["cases"]["verified"] is True
    assert result["company"]["verified"] is True


def test_generation_company_lookup_survives_law_and_case_failures(monkeypatch):
    calls: list[str] = []

    async def extract_company_name(_facts, _claims, _current_data):
        return "甲有限公司"

    monkeypatch.setattr(remote_generation, "extract_company_name", extract_company_name)
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
                "2026-08-14",
                "yuandian:mcp:company",
                {"registered_address": "苏州市吴中区", "court": "苏州市吴中区人民法院"},
            )

    result = asyncio.run(
        research_for_generation(
            {
                "case_stage": "litigation",
                "party_side": "worker",
                "data": {
                    "intake": {"facts": "甲有限公司不服仲裁裁决"},
                    "claims": [{"title": "确认无需支付违约金"}],
                },
            },
            {},
            provider=Provider(),
        )
    )

    assert calls == ["law", "case", "company:甲有限公司"]
    assert result["law"]["verified"] is False
    assert result["company"]["verified"] is True
    assert result["jurisdiction"]["court"] == "苏州市吴中区人民法院"
