from __future__ import annotations

import json
from typing import Any

from app.services.openai_compat import complete_json


MAX_DRAFT_MATERIAL_CHARS = 12_000
_CASE_DATA_KEYS = (
    "parties",
    "court",
    "employment_facts",
    "arbitration",
    "intake",
    "analysis",
    "claims",
)


def fallback_draft_case_documents(
    *,
    case: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    data = case.get("data") if isinstance(case.get("data"), dict) else {}
    existing = data.get("_ai_draft") if isinstance(data.get("_ai_draft"), dict) else {}
    claims = [str(value).strip() for value in existing.get("claims") or [] if str(value).strip()]
    facts = [str(value).strip() for value in existing.get("facts_and_reasons") or [] if str(value).strip()]
    if not claims:
        structured = data.get("claims") if isinstance(data.get("claims"), list) else []
        claims = [
            str(item.get("title") or "").strip()
            for item in structured
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ]
    if not claims:
        claims = ["[待填入：仲裁请求或诉讼请求]"]
    if not facts:
        employment = data.get("employment_facts") if isinstance(data.get("employment_facts"), dict) else {}
        summary = str(employment.get("summary") or "").strip()
        facts = [
            summary or "[待填入：劳动关系、工资支付、工作管理及解除经过等基本案情]",
            "[待核验法律依据]",
        ]
    updates = []
    for item in evidence_items:
        name = str(item.get("name") or "").strip()
        original = str(item.get("original_name") or "").strip()
        if not name or name == original:
            name = "待结合材料正文命名的证据"
        updates.append(
            {
                "id": item["id"],
                "name": name[:255],
                "source": (str(item.get("source") or "").strip() or "待核实")[:255],
                "purpose": (str(item.get("purpose") or "").strip() or "待结合案件事实核实证明目的")[:2000],
            }
        )
    return {
        "claims": claims,
        "facts_and_reasons": facts,
        "data_patch": {},
        "missing_fields": [
            "[待核验：大模型撰写暂不可用，已生成含待填项的正式稿，请核对诉请、事实理由和证据名称]",
            reason,
        ],
        "evidence_updates": updates,
    }


def compact_case_for_draft(case: dict[str, Any]) -> dict[str, Any]:
    data = case.get("data") if isinstance(case.get("data"), dict) else {}
    compact_data = {
        key: data[key]
        for key in _CASE_DATA_KEYS
        if data.get(key) not in (None, "", [], {})
    }
    return {
        "id": case.get("id"),
        "title": case.get("title"),
        "case_stage": case.get("case_stage"),
        "party_side": case.get("party_side"),
        "data": compact_data,
    }


async def draft_case_documents(
    *,
    case: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    material_texts: dict[str, str],
) -> dict[str, Any]:
    evidence = []
    for item in evidence_items:
        evidence.append(
            {
                "id": item["id"],
                "current_name": item.get("name"),
                "current_source": item.get("source"),
                "current_purpose": item.get("purpose"),
                "material_text": material_texts.get(item["id"], "")[:MAX_DRAFT_MATERIAL_CHARS],
            }
        )
    try:
        parsed = await complete_json(
            system="""你是中国劳动争议诉讼文书撰写律师。根据案件结构化信息和证据正文，撰写可以直接进入正式文书的中文内容，只输出JSON。
claims：字符串数组，每项是明确、完整的仲裁请求或诉讼请求；案件信息能确定时不得改写成待填模板。确实缺少金额或计算基础时，只在该处用明确的[待填入：...]。
facts_and_reasons：字符串数组，按劳动关系、争议发生、仲裁前置、起诉理由、法律理由的逻辑组织。不得原样堆叠用户口语，不得仅罗列字段，不得虚构材料没有的事实。
data_patch：从案件说明及材料中能够可靠提取的结构化信息。仅返回有依据的字段，未知字段直接省略，不得猜测。可包含：
- parties.initiating / parties.opposing：type(company/individual)、name、address、credit_code、legal_representative、legal_representative_title、gender、birth_date、id_number、contact；
- court：管辖法院全称；
- employment_facts：start_date、end_date、position、monthly_wage、summary；
- arbitration：committee、award_number、result、service_date、payment_status。
当材料与用户已确认信息冲突时，不覆盖原值，并在 missing_fields 中写明[待核实：具体冲突]。
evidence_items：逐项返回 id、name、source、purpose。name按材料内容命名，不得使用上传文件名；source写形成主体/取得来源；purpose必须结合诉请写出该证据证明的具体事实。
verified_law：只能使用输入中已标记 verified=true 且能从来源内容核对的法条。无法核验时不写具体条号，可在事实理由末尾使用[待核验法律依据]。
missing_fields：仅列影响提交或诉请计算且无法从材料得出的关键信息。不要为可由正文自然表述的信息制造占位符。
语气专业克制，避免“保证胜诉”等结论。""",
            user=json.dumps({"case": compact_case_for_draft(case), "evidence": evidence}, ensure_ascii=False),
            timeout=180,
        )
    except Exception as exc:
        return fallback_draft_case_documents(case=case, evidence_items=evidence_items, reason=str(exc))
    claims = [str(value).strip() for value in parsed.get("claims") or [] if str(value).strip()]
    facts = [str(value).strip() for value in parsed.get("facts_and_reasons") or [] if str(value).strip()]
    updates = []
    known_ids = {item["id"] for item in evidence_items}
    for item in parsed.get("evidence_items") or []:
        if not isinstance(item, dict) or item.get("id") not in known_ids:
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        if name and source and purpose:
            updates.append({"id": item["id"], "name": name[:255], "source": source[:255], "purpose": purpose[:2000]})
    if not claims or not facts:
        return fallback_draft_case_documents(
            case=case,
            evidence_items=evidence_items,
            reason="模型未返回完整的文书正文",
        )
    return {
        "claims": claims,
        "facts_and_reasons": facts,
        "data_patch": parsed.get("data_patch") if isinstance(parsed.get("data_patch"), dict) else {},
        "missing_fields": [str(value) for value in parsed.get("missing_fields") or []],
        "evidence_updates": updates,
    }
