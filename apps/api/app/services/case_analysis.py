from __future__ import annotations

import asyncio
import json
from typing import Any

from app.services.legal_research import (
    YuandianLegalResearchProvider,
    extract_company_name,
    grounded_legal_basis,
    safe_research,
    unavailable_snapshot,
    verified_company_jurisdiction,
)
from app.services.openai_compat import complete_json


SENSITIVE_TERMS = ("姓名", "身份证", "联系方式", "手机号", "家庭住址", "详细地址", "信用代码", "法定代表人")


def _route(text: str) -> tuple[str, str]:
    stage = "litigation" if any(word in text for word in ("仲裁裁决", "不服仲裁", "起诉", "送达")) else "arbitration"
    employer_words = ("我司", "本公司", "代表公司", "公司起诉", "用人单位")
    side = "employer" if any(word in text for word in employer_words) else "worker"
    return stage, side


def _normalise_evidence_requirements(value: Any) -> list[dict[str, str]]:
    requirements: list[dict[str, str]] = []
    values = value if isinstance(value, list) else []
    for item in values[:20]:
        if isinstance(item, str):
            suggested = item.strip()
            if suggested:
                requirements.append({"suggested_evidence": suggested, "status": "not_submitted"})
            continue
        if not isinstance(item, dict):
            continue
        suggested = str(
            item.get("suggested_evidence")
            or item.get("evidence")
            or item.get("name")
            or ""
        ).strip()
        if not suggested:
            continue
        requirement = {
            "suggested_evidence": suggested[:255],
            "claim": str(item.get("claim") or "").strip()[:255],
            "fact_to_prove": str(item.get("fact_to_prove") or item.get("purpose") or "").strip()[:500],
            "status": str(item.get("status") or "not_submitted").strip()[:40],
        }
        requirements.append({key: value for key, value in requirement.items() if value})
    return requirements


def _normalise_data_patch(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "employment_facts",
        "arbitration",
        "court",
        "claims",
        "legal_basis",
        "evidence_gaps",
        "evidence_requirements",
        "jurisdiction",
    }
    patch = {key: item for key, item in value.items() if key in allowed and item not in (None, "", [], {})}
    if "employment_facts" in patch and not isinstance(patch["employment_facts"], dict):
        patch["employment_facts"] = {"summary": str(patch["employment_facts"]).strip()}
    if "arbitration" in patch and not isinstance(patch["arbitration"], dict):
        patch.pop("arbitration")
    if "claims" in patch:
        claims = patch["claims"] if isinstance(patch["claims"], list) else []
        normalised_claims = []
        for item in claims[:20]:
            if isinstance(item, dict):
                normalised_claims.append(item)
            elif str(item).strip():
                normalised_claims.append(
                    {
                        "kind": "other",
                        "title": str(item).strip()[:500],
                        "basis": "[待填入：金额、计算基数、期间或计算方式]",
                    }
                )
        patch["claims"] = normalised_claims
    if "evidence_gaps" in patch:
        gaps = patch["evidence_gaps"] if isinstance(patch["evidence_gaps"], list) else []
        patch["evidence_gaps"] = [str(item).strip()[:255] for item in gaps if str(item).strip()][:20]
    if "evidence_requirements" in patch:
        patch["evidence_requirements"] = _normalise_evidence_requirements(patch["evidence_requirements"])
    if patch.get("evidence_requirements") and not patch.get("evidence_gaps"):
        patch["evidence_gaps"] = [item["suggested_evidence"] for item in patch["evidence_requirements"]]
    if patch.get("evidence_gaps") and not patch.get("evidence_requirements"):
        patch["evidence_requirements"] = [
            {"suggested_evidence": item, "status": "not_submitted"}
            for item in patch["evidence_gaps"]
        ]
    return patch


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
    evidence_requirements = [
        {"suggested_evidence": "劳动合同或入职材料", "fact_to_prove": "证明劳动关系成立、入职时间和岗位", "status": "not_submitted"},
        {"suggested_evidence": "工资及考勤记录", "fact_to_prove": "证明工资标准、出勤和请求金额的计算基础", "status": "not_submitted"},
        {"suggested_evidence": "解除、离职或协商材料", "fact_to_prove": "证明争议发生、解除原因及时间", "status": "not_submitted"},
        {"suggested_evidence": "仲裁裁决书及送达凭证", "fact_to_prove": "证明仲裁结果和起诉期限", "status": "not_submitted"},
    ]
    return {
        "case_stage": stage,
        "party_side": side,
        "summary": facts.strip(),
        "claims_summary": claims.strip(),
        "follow_up_questions": questions,
        "evidence_suggestions": [item["suggested_evidence"] for item in evidence_requirements],
        "data_patch": {
            "employment_facts": {"summary": "\n".join(part for part in (facts, supplement) if part).strip()},
            "claims": [{"kind": "other", "title": claims.strip(), "basis": "[待填入：金额、计算基数、期间或计算方式]"}],
            "evidence_gaps": [item["suggested_evidence"] for item in evidence_requirements],
            "evidence_requirements": evidence_requirements,
        },
        "analysis_status": "fallback",
    }


def _ground_legal_basis(parsed: dict[str, Any], research: dict[str, Any]) -> None:
    patch = parsed.get("data_patch")
    if not isinstance(patch, dict):
        parsed["data_patch"] = {}
        return
    law = research.get("law") if isinstance(research.get("law"), dict) else None
    patch["legal_basis"] = grounded_legal_basis(patch.get("legal_basis") or [], law)


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
        research = await self._research(facts, claims_text, current_data)
        prompt = {
            "facts": facts,
            "claims": claims_text,
            "supplement": supplement,
            "current_data": current_data,
            "round": round_number,
            "legal_research": research,
        }
        system = """你是中国劳动争议案件分析器，只输出JSON。识别案件阶段和劳动者/公司一方，整理事实、诉请、证据缺口和管辖线索。
梳理诉讼请求（claims）时：不论用户是否输入了序号、输入的序号是否规范（如“一、”“1.”“(1)”或无序号），你都必须剥离并剔除用户原本不规范的序号，重新梳理出清晰独立的请求项，并在每项开头统一规范生成标准序号（如“1. ”“2. ”）。
第一轮只提出一轮合并追问（follow_up_questions，最多5项）；第二轮不得继续追问。
严禁询问姓名、身份证号、手机号、联系方式、家庭详细住址、统一社会信用代码、法定代表人；这些由Word保留待填项。
仅依据已核验的元典结果：用企业信息识别用人单位登记地和可能的管辖法院线索；用现行法条识别请求权基础；用相关案例总结支持与不支持诉请的关键事实、举证风险和结果区间。案例只能作为分析参考，不得表述为本案必然结果。
元典结果未核验时不得生成精确法条、案例号或企业信息，应在 legal_analysis 中标记待核验。
legal_analysis 输出一段简明中文，说明请求权、管辖线索、类案倾向及主要举证风险。请从类案结果中整理 evidence_requirements，每项包含 suggested_evidence、fact_to_prove、claim、status；status 初始为 not_submitted。data_patch 只使用这些字段：employment_facts、arbitration、court、claims、legal_basis、evidence_gaps、evidence_requirements、jurisdiction；legal_basis 每项包含 citation，且只有原文出现在已核验法条结果中才能标 verified=true。只有企业信息已核验时，才可把公司登记地址或明确返回的法院写入 jurisdiction/court；无法确定时保留待核实状态，不得猜测。"""
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
            parsed["data_patch"] = _normalise_data_patch(parsed.get("data_patch"))
            fallback_patch = fallback["data_patch"]
            if not parsed["data_patch"].get("evidence_requirements"):
                parsed["data_patch"]["evidence_requirements"] = fallback_patch["evidence_requirements"]
            if not parsed["data_patch"].get("evidence_gaps"):
                parsed["data_patch"]["evidence_gaps"] = [
                    item["suggested_evidence"] for item in parsed["data_patch"]["evidence_requirements"]
                ]
            jurisdiction = research.get("jurisdiction")
            if isinstance(jurisdiction, dict):
                parsed["data_patch"].setdefault("jurisdiction", jurisdiction)
                if jurisdiction.get("court") and not parsed["data_patch"].get("court"):
                    parsed["data_patch"]["court"] = jurisdiction["court"]
            _ground_legal_basis(parsed, research)
            parsed["legal_research"] = research
            parsed["analysis_status"] = "complete"
            return parsed
        except Exception:
            fallback_result = {**fallback, "legal_research": research}
            jurisdiction = research.get("jurisdiction")
            if isinstance(jurisdiction, dict):
                fallback_result["data_patch"] = _normalise_data_patch(fallback_result.get("data_patch"))
                fallback_result["data_patch"]["jurisdiction"] = jurisdiction
                if jurisdiction.get("court"):
                    fallback_result["data_patch"]["court"] = jurisdiction["court"]
            return fallback_result

    async def _research(self, facts: str, claims: str, current_data: dict[str, Any]) -> dict[str, Any]:
        provider = YuandianLegalResearchProvider()
        query = f"劳动争议：{claims[:300]}"
        company = await extract_company_name(facts, claims, current_data)
        tasks = [
            safe_research(provider, "law", query),
            safe_research(provider, "case", f"{facts[:300]}；争议请求：{claims[:200]}"),
        ]
        if company:
            tasks.append(safe_research(provider, "company", company))
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=45)
        except asyncio.TimeoutError:
            results = [
                unavailable_snapshot("law", query),
                unavailable_snapshot("case", f"{facts[:300]}；争议请求：{claims[:200]}"),
            ]
            if company:
                results.append(unavailable_snapshot("company", company))
        law, cases = results[:2]
        company_result = results[2] if company else None
        company_payload = company_result.as_dict() if company_result else None
        return {
            "law": law.as_dict(),
            "cases": cases.as_dict(),
            "company": company_payload,
            "jurisdiction": verified_company_jurisdiction(company_payload),
        }
