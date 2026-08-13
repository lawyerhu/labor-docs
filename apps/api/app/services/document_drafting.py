from __future__ import annotations

import json
import re
from typing import Any

from app.services.legal_research import grounded_legal_basis
from app.services.openai_compat import complete_json


MAX_DRAFT_MATERIAL_CHARS = 20_000
_CASE_DATA_KEYS = (
    "parties",
    "court",
    "jurisdiction",
    "employment_facts",
    "arbitration",
    "intake",
    "analysis",
    "claims",
    "legal_basis",
    "legal_research",
    "evidence_gaps",
    "evidence_requirements",
)

_PRECISE_LAW_CITATION = re.compile(
    r"《[^》]{2,80}》\s*第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十百千万两0-9]+)?"
    r"|第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十百千万两0-9]+)?"
)


def _compact_research(value: Any, limit: int) -> Any:
    if not isinstance(value, dict):
        return value
    result = {key: item for key, item in value.items() if key != "content"}
    content = json.dumps(value.get("content") or {}, ensure_ascii=False)
    result["content"] = content[:limit]
    return result


def _sanitize_law_citations(text: str, verified: list[dict[str, Any]]) -> str:
    allowed = {str(item.get("citation") or "").strip() for item in verified}
    allowed_spans = []
    for citation in allowed:
        start = text.find(citation)
        while citation and start >= 0:
            allowed_spans.append((start, start + len(citation)))
            start = text.find(citation, start + 1)

    def replace(match: re.Match[str]) -> str:
        citation = match.group(0).strip()
        inside_verified_citation = any(
            start <= match.start() and match.end() <= end
            for start, end in allowed_spans
        )
        return citation if citation in allowed or inside_verified_citation else "[待核验法律依据]"

    return _PRECISE_LAW_CITATION.sub(replace, text)


def compact_case_for_draft(case: dict[str, Any]) -> dict[str, Any]:
    data = case.get("data") if isinstance(case.get("data"), dict) else {}
    compact_data = {
        key: data[key]
        for key in _CASE_DATA_KEYS
        if data.get(key) not in (None, "", [], {})
    }
    research = compact_data.get("legal_research")
    if isinstance(research, dict):
        compact_data["legal_research"] = {
            "law": _compact_research(research.get("law"), 24_000),
            "cases": _compact_research(research.get("cases"), 12_000),
            "company": _compact_research(research.get("company"), 6_000),
            "jurisdiction": research.get("jurisdiction"),
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
                "current_purpose": item.get("purpose"),
                "material_text": material_texts.get(item["id"], "")[:MAX_DRAFT_MATERIAL_CHARS],
            }
        )
    parsed = await complete_json(
        system="""你是中国劳动争议诉讼文书撰写律师。根据案件结构化信息和证据正文，撰写可以直接进入正式文书的中文内容，只输出JSON。
claims：字符串数组，每项是明确、完整的仲裁请求或诉讼请求；案件信息能确定时不得改写成待填模板。确实缺少金额或计算基础时，只在该处用明确的[待填入：...]。
facts_and_reasons：字符串数组，每项是一段连贯中文正文。按时间顺序叙述劳动关系、争议发生、仲裁经过、起诉理由和法律依据，但不得使用“劳动关系”“争议发生”“仲裁前置”“起诉理由”“法律理由”等小标题或类似分段标题。不得原样堆叠用户口语，不得仅罗列字段，不得虚构材料没有的事实。
data_patch：从案件说明及材料中能够可靠提取的结构化信息。仅返回有依据的字段，未知字段直接省略，不得猜测。可包含：
- parties.initiating / parties.opposing：type(company/individual)、name、address、credit_code、legal_representative、legal_representative_title、gender、birth_date、id_number、contact；
- court：管辖法院全称；只有企业信息或案件材料足以支持时填写；
- jurisdiction：已核验的用人单位登记地及其来源；
- employment_facts：start_date、end_date、position、monthly_wage、summary；
- arbitration：committee、award_number、result、service_date、payment_status。
- evidence_gaps、evidence_requirements：从请求权和类案中整理的可选证据建议；不要把未提交建议写成已存在的证据。
当材料与用户已确认信息冲突时，不覆盖原值，并在 missing_fields 中写明[待核实：具体冲突]。
evidence_items：逐项返回 id、name、purpose、include、order。name按材料内容命名，不得使用上传文件名；purpose必须结合诉请写出该证据证明的具体事实。include=false 仅用于与本案请求权和事实无关、重复或无法形成证明作用的材料；有用材料必须为 true。order 是证据在目录和证据合并PDF中的逻辑顺序，不得按上传顺序填写，应按“劳动关系/基础事实→工资考勤及履行→争议发生或解除→仲裁经过及送达→其他补强”的证明链，并结合本案请求权和事实时间线确定。不要返回来源字段。
verified_law：对象数组，每项只包含 citation。只能使用输入 legal_research.law 中已标记 verified=true 且 citation 原文能从其 content 核对的法条。无法核验时返回空数组，不写具体条号，并在事实理由末尾使用[待核验法律依据]。
missing_fields：仅列影响提交或诉请计算且无法从材料得出的关键信息。不要为可由正文自然表述的信息制造占位符。
语气专业克制，避免“保证胜诉”等结论。""",
        user=json.dumps({"case": compact_case_for_draft(case), "evidence": evidence}, ensure_ascii=False),
        # Keep one provider attempt bounded; complete_json will fail over to
        # the configured fallback providers without multiplying retries.
        timeout=90,
        attempts=1,
    )
    claims = [str(value).strip() for value in parsed.get("claims") or [] if str(value).strip()]
    research = (case.get("data") or {}).get("legal_research") if isinstance(case.get("data"), dict) else {}
    law_snapshot = research.get("law") if isinstance(research, dict) else None
    candidates = list(parsed.get("verified_law") or [])
    patch = parsed.get("data_patch") if isinstance(parsed.get("data_patch"), dict) else {}
    candidates.extend(patch.get("legal_basis") or [])
    legal_basis = grounded_legal_basis(candidates, law_snapshot)
    patch["legal_basis"] = legal_basis
    facts = [
        _sanitize_law_citations(str(value).strip(), legal_basis)
        for value in parsed.get("facts_and_reasons") or []
        if str(value).strip()
    ]
    combined_facts = "\n".join(facts)
    if legal_basis:
        missing_citations = [item["citation"] for item in legal_basis if item["citation"] not in combined_facts]
        if missing_citations:
            facts.append(f"法律依据：{'；'.join(missing_citations)}。")
    elif "[待核验法律依据]" not in combined_facts:
        facts.append("[待核验法律依据]")
    updates = []
    known_ids = {item["id"] for item in evidence_items}
    known_items = {item["id"]: item for item in evidence_items}
    for item in parsed.get("evidence_items") or []:
        if not isinstance(item, dict) or item.get("id") not in known_ids:
            continue
        original = known_items[item["id"]]
        name = str(item.get("name") or original.get("name") or original.get("original_name") or "材料").strip()
        purpose = str(item.get("purpose") or original.get("purpose") or "[待核实：证明目的]").strip()
        order_value = item.get("order")
        try:
            order = int(order_value) if order_value is not None else None
        except (TypeError, ValueError):
            order = None
        updates.append(
            {
                "id": item["id"],
                "name": name[:255],
                "purpose": purpose[:2000],
                "included": item.get("include") is not False and str(item.get("include") or "").lower() != "false",
                "order": order,
            }
        )
    if not claims or not facts:
        raise RuntimeError("模型未返回完整的文书正文")
    return {
        "claims": claims,
        "facts_and_reasons": facts,
        "data_patch": patch,
        "legal_basis": legal_basis,
        "missing_fields": [str(value) for value in parsed.get("missing_fields") or []],
        "evidence_updates": updates,
    }
