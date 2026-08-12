from __future__ import annotations

import json
from typing import Any

from app.services.openai_compat import complete_json


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
                "material_text": material_texts.get(item["id"], "")[:45_000],
            }
        )
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
        user=json.dumps({"case": case, "evidence": evidence}, ensure_ascii=False),
        timeout=180,
    )
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
        raise RuntimeError("模型未返回完整的文书正文")
    return {
        "claims": claims,
        "facts_and_reasons": facts,
        "data_patch": parsed.get("data_patch") if isinstance(parsed.get("data_patch"), dict) else {},
        "missing_fields": [str(value) for value in parsed.get("missing_fields") or []],
        "evidence_updates": updates,
    }
