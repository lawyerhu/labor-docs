from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.config import get_settings
from app.services.legal_research import YuandianLegalResearchProvider
from app.services.openai_compat import complete_json


SENSITIVE_TERMS = ("姓名", "身份证", "联系方式", "手机号", "家庭住址", "详细地址", "信用代码", "法定代表人")


def _route(text: str) -> tuple[str, str]:
    stage = "litigation" if any(word in text for word in ("仲裁裁决", "不服仲裁", "起诉", "送达")) else "arbitration"
    employer_words = ("我司", "本公司", "代表公司", "公司起诉", "用人单位")
    side = "employer" if any(word in text for word in employer_words) else "worker"
    return stage, side


def _company_name(text: str) -> str | None:
    match = re.search(r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,40}(?:有限责任公司|股份有限公司|有限公司))", text)
    return match.group(1) if match else None


def _fallback(facts: str, claims: str, supplement: str, round_number: int) -> dict[str, Any]:
    combined = "\n".join(part for part in (facts, claims, supplement) if part)
    stage, side = _route(combined)
    questions: list[str] = []
    if round_number == 1:
        questions = [
            "请补充入职、离职或解除劳动关系的关键日期、岗位及工资构成。",
            "请说明每项请求的金额、计算期间、计算基数以及仲裁已经支持或驳回的部分。",
            "仲裁裁决何时送达？劳动合同履行地或用人单位所在地在哪个区县？",
            "目前有哪些劳动合同、工资记录、考勤、聊天记录、解除通知或仲裁材料可以上传？",
        ]
    return {
        "case_stage": stage,
        "party_side": side,
        "summary": facts.strip(),
        "claims_summary": claims.strip(),
        "follow_up_questions": questions,
        "evidence_suggestions": ["劳动合同或入职材料", "工资及考勤记录", "解除或离职材料", "仲裁裁决书及送达凭证"],
        "data_patch": {
            "employment_facts": {"summary": "\n".join(part for part in (facts, supplement) if part).strip()},
            "claims": [{"kind": "other", "title": claims.strip(), "basis": "[待填入：金额、计算基数、期间或计算方式]"}],
        },
        "analysis_status": "fallback",
    }


def _ground_legal_basis(parsed: dict[str, Any], research: dict[str, Any]) -> None:
    patch = parsed.get("data_patch")
    if not isinstance(patch, dict):
        parsed["data_patch"] = {}
        return
    law = research.get("law") if isinstance(research.get("law"), dict) else {}
    source_text = json.dumps(law.get("content") or {}, ensure_ascii=False)
    grounded = []
    for item in patch.get("legal_basis") or []:
        if not isinstance(item, dict):
            continue
        citation = str(item.get("citation") or "").strip()
        if law.get("verified") is True and citation and citation in source_text:
            grounded.append({**item, "verified": True, "source": law.get("source"), "retrieved_at": law.get("retrieved_at")})
    patch["legal_basis"] = grounded


class CaseAnalyzer:
    async def analyze(
        self,
        *,
        facts: str,
        claims_text: str,
        supplement: str,
        current_data: dict[str, Any],
        round_number: int,
    ) -> dict[str, Any]:
        fallback = _fallback(facts, claims_text, supplement, round_number)
        research = await self._research(facts, claims_text)
        settings = get_settings()
        if not settings.openai_base_url or not settings.openai_api_key or not settings.openai_model:
            return {**fallback, "legal_research": research}
        prompt = {
            "facts": facts,
            "claims": claims_text,
            "supplement": supplement,
            "current_data": current_data,
            "round": round_number,
            "legal_research": research,
        }
        system = """你是中国劳动争议案件分析器，只输出JSON。识别案件阶段和劳动者/公司一方，整理事实、诉请、证据缺口和管辖线索。
第一轮只提出一轮合并追问（follow_up_questions，最多5项）；第二轮不得继续追问。
严禁询问姓名、身份证号、手机号、联系方式、家庭详细住址、统一社会信用代码、法定代表人；这些由Word保留待填项。
仅依据已核验的元典结果：用企业信息识别用人单位登记地和可能的管辖法院线索；用现行法条识别请求权基础；用相关案例总结支持与不支持诉请的关键事实、举证风险和结果区间。案例只能作为分析参考，不得表述为本案必然结果。
元典结果未核验时不得生成精确法条、案例号或企业信息，应在 legal_analysis 中标记待核验。
legal_analysis 输出一段简明中文，说明请求权、管辖线索、类案倾向及主要举证风险。data_patch 只使用这些字段：employment_facts、arbitration、court、claims、legal_basis、evidence_gaps；legal_basis 每项包含 citation，且只有原文出现在已核验法条结果中才能标 verified=true。"""
        try:
            parsed = await complete_json(
                system=system,
                user=json.dumps(prompt, ensure_ascii=False),
                timeout=60,
            )
            questions = parsed.get("follow_up_questions") if round_number == 1 else []
            parsed["follow_up_questions"] = [
                str(item) for item in (questions or [])
                if not any(term in str(item) for term in SENSITIVE_TERMS)
            ][:5]
            parsed["case_stage"] = parsed.get("case_stage") if parsed.get("case_stage") in {"arbitration", "litigation"} else fallback["case_stage"]
            parsed["party_side"] = parsed.get("party_side") if parsed.get("party_side") in {"worker", "employer"} else fallback["party_side"]
            _ground_legal_basis(parsed, research)
            parsed["legal_research"] = research
            parsed["analysis_status"] = "complete"
            return parsed
        except Exception:
            return {**fallback, "legal_research": research}

    async def _research(self, facts: str, claims: str) -> dict[str, Any]:
        provider = YuandianLegalResearchProvider()
        query = f"劳动争议：{claims[:300]}"
        company = _company_name(facts)
        tasks = [
            provider.search_law(query),
            provider.search_cases(f"{facts[:300]}；争议请求：{claims[:200]}"),
        ]
        if company:
            tasks.append(provider.search_company(company))
        results = await asyncio.gather(*tasks)
        law, cases = results[:2]
        company_result = results[2] if company else None
        return {
            "law": law.as_dict(),
            "cases": cases.as_dict(),
            "company": company_result.as_dict() if company_result else None,
        }
