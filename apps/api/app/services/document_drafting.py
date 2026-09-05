from __future__ import annotations

import json
import re
from typing import Any

from app.services.legal_research import grounded_legal_basis
from app.services.openai_compat import complete_json


MAX_DRAFT_MATERIAL_CHARS = 20_000
_PAGE_MARKER_RE = re.compile(r"(?m)^--- 第(\d+)页 ---\s*$")
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

_PAGE_RANGE_RE = re.compile(r"(\d{1,4})\s*(?:[-—–~至到])\s*(\d{1,4})")
_SINGLE_PAGE_RE = re.compile(r"(\d{1,4})")

_CLAIM_LEADING_NUMBER_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.．\s]+|\d+[、.．\s]+|[（(][一二三四五六七八九十\d]+[)）][\s]*|第[一二三四五六七八九十\d]+条[、.．\s]*)"
)


def _strip_claim_number(text: str) -> str:
    cleaned = str(text or "").strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _CLAIM_LEADING_NUMBER_RE.sub("", cleaned).strip()
    return cleaned


def _clean_claim_text(value: Any) -> str:
    text = _strip_claim_number(str(value or "").strip())
    return text


def _parse_page_range(raw: Any) -> list[int] | None:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        lo, hi = int(raw[0]), int(raw[1])
        if lo >= 1 and hi >= lo:
            return [lo, hi]
    text = str(raw or "").strip()
    if not text:
        return None
    match = _PAGE_RANGE_RE.search(text)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
        if lo >= 1 and hi >= lo:
            return [lo, hi]
        return None
    single = _SINGLE_PAGE_RE.search(text)
    if single:
        page = int(single.group(1))
        if page >= 1:
            return [page, page]
    return None


def _material_text_for_draft(text: str, limit: int = MAX_DRAFT_MATERIAL_CHARS) -> str:
    if len(text) <= limit:
        return text
    matches = list(_PAGE_MARKER_RE.finditer(text))
    if len(matches) < 2:
        return text[:limit]
    pages: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((match.group(0).strip(), text[match.end():end].strip()))
    separator = "\n\n"
    overhead = sum(len(marker) + 1 for marker, _ in pages) + len(separator) * (len(pages) - 1)
    per_page = max(1, (limit - overhead) // len(pages))
    return separator.join(f"{marker}\n{content[:per_page]}" for marker, content in pages)


def _material_page_count(text: str) -> int | None:
    pages = [int(match.group(1)) for match in _PAGE_MARKER_RE.finditer(text)]
    return max(pages) if pages else None


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
        material_text = material_texts.get(item["id"], "")
        evidence.append(
            {
                "id": item["id"],
                "current_name": item.get("name"),
                "current_purpose": item.get("purpose"),
                "page_count": _material_page_count(material_text),
                "material_text": _material_text_for_draft(material_text),
            }
        )
    parsed = await complete_json(
        system="""你是中国劳动争议诉讼文书撰写律师。根据案件结构化信息和证据正文，撰写可以直接进入正式文书的中文内容，只输出JSON。
claims：字符串数组，每项是明确、规范、完整的仲裁请求或诉讼请求。注意：用户输入的请求中可能自带了混乱、不规则的序号（例如“一、”“1.”“(1)”“请求1”或完全没有序号），你必须完全剔除用户原始输入中的各种序号，重新按诉讼文书的标准格式逐项规范梳理，并在每项开头严格统一加上“1. ”“2. ”“3. ”的标准阿拉伯数字序号（例如：“1. 裁决/判令被告向原告支付……”，“2. 裁决/判令确认双方存在劳动关系……”）。案件信息能确定时不得改写成待填模板。确实缺少金额或计算基础时，只在该处用明确的[待填入：...]。
facts_and_reasons：字符串数组，每项是一段连贯中文正文。按时间顺序叙述劳动关系、争议发生、仲裁经过、起诉理由和法律依据，但不得使用“劳动关系”“争议发生”“仲裁前置”“起诉理由”“法律理由”等小标题或类似分段标题。法律依据只需体现与本案核心诉求直接相关的核心法律条款（例如解除劳动合同经济补偿金引用《劳动合同法》第46/47条，违法解除引用第87条，未签合同双倍工资引用第82条），不得冗余罗列次要法条或一般性宣示条款，且必须用法言法语自然穿插在叙述中（如“根据《中华人民共和国劳动合同法》第四十七条的规定……”），严禁在文末单独罗列“法律依据：……”或法条清单。不得原样堆叠用户口语，不得仅罗列字段，不得虚构材料没有的事实。
data_patch：从案件说明及材料中能够可靠提取的结构化信息。仅返回有依据的字段，未知字段直接省略，不得猜测。可包含：
- parties.initiating / parties.opposing：type(company/individual)、name、address、credit_code、legal_representative、legal_representative_title、gender、birth_date、ethnicity、id_number、contact；
- court：管辖法院全称；只有企业信息或案件材料足以支持时填写；
- jurisdiction：已核验的用人单位登记地及其来源；
- employment_facts：start_date、end_date、position、monthly_wage、summary；
- arbitration：committee、award_number、result、service_date、payment_status。
- evidence_gaps、evidence_requirements：从请求权和类案中整理的可选证据建议；不要把未提交建议写成已存在的证据。
当材料与用户已确认信息冲突时，不覆盖原值，并在 missing_fields 中写明[待核实：具体冲突]。
evidence_items：逐项返回 id、name、purpose、include、order。id 必须来自输入材料。输入中的 page_count 是该原文件的总页数，页码以原文件为准。当一份材料（正文中的“--- 第N页 ---”分段）确实包含多项相互独立的证据时（例如同一扫描件里既有劳动合同，又有工资流水和解除通知），可对同一 id 返回多项，每项代表一份独立证据，并给出 pages（该项在原文件中的页码范围，如"1-3"或"第1-2页"；单页如"第3页"；不拆分时省略 pages）。拆分后的 pages 必须完整覆盖 1 至 page_count 的全部页码且不得重叠；无法完整覆盖、无法判断边界或单一证据时不要拆。name按材料内容命名，不得使用上传文件名；purpose必须结合诉请写出该证据证明的具体事实。include=false 仅用于与本案请求权和事实无关、重复或无法形成证明作用的材料；有用材料必须为 true。order 是证据在目录和证据合并PDF中的逻辑顺序，不得按上传顺序填写，应按“劳动关系/基础事实→工资考勤及履行→争议发生或解除→仲裁经过及送达→其他补强”的证明链，并结合本案请求权和事实时间线确定。不要返回来源字段。
verified_law：对象数组，每项只包含 citation。只能使用输入 legal_research.law 中已标记 verified=true 且 citation 原文能从其 content 核对的法条；无法核验时返回空数组，并在正文相应位置使用[待核验法律依据]，不得在末尾单独罗列。
missing_fields：仅列影响提交或诉请计算且无法从材料得出的关键信息。不要为可由正文自然表述的信息制造占位符。
语气专业克制，避免“保证胜诉”等结论。""",
        user=json.dumps({"case": compact_case_for_draft(case), "evidence": evidence}, ensure_ascii=False),
        # Keep one provider attempt bounded; complete_json will fail over to
        # the configured fallback providers without multiplying retries.
        timeout=90,
        attempts=1,
    )
    raw_claims = parsed.get("claims") or []
    cleaned_claims = [_clean_claim_text(value) for value in raw_claims if _clean_claim_text(value)]
    # 大模型统一规范梳理后，按规范给每项加上唯一的阿拉伯数字标准序号
    claims = [f"{i}. {item}" for i, item in enumerate(cleaned_claims, 1)]
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
    if legal_basis:
        # 只选取与诉求最直接相关的核心法律规定（最多2-3条核心依据），避免冗余罗列
        core_citations = [item["citation"] for item in legal_basis[:3]]
        if "[待核验法律依据]" in "\n".join(facts):
            index = 0

            def _fill(_match: re.Match[str]) -> str:
                nonlocal index
                token = core_citations[min(index, len(core_citations) - 1)]
                index += 1
                return token

            facts = re.sub(r"\[待核验法律依据\]", _fill, "\n".join(facts)).split("\n")
        missing = [
            citation
            for citation in core_citations
            if citation not in "\n".join(facts)
        ]
        if missing and facts:
            facts[-1] = (
                f"{facts[-1]} 根据{'、'.join(missing)}的规定，用人单位应承担相应的法律责任。"
            )
    elif "[待核验法律依据]" not in "\n".join(facts):
        facts.append("[待核验法律依据]")
    updates = []
    known_ids = {item["id"] for item in evidence_items}
    known_items = {item["id"]: item for item in evidence_items}
    split_counts: dict[str, int] = {}
    for item in parsed.get("evidence_items") or []:
        if not isinstance(item, dict) or item.get("id") not in known_ids:
            continue
        evidence_id = str(item["id"])
        original = known_items[evidence_id]
        split_index = split_counts.get(evidence_id, 0)
        split_counts[evidence_id] = split_index + 1
        name = str(item.get("name") or original.get("name") or original.get("original_name") or "材料").strip()
        purpose = str(item.get("purpose") or original.get("purpose") or "[待核实：证明目的]").strip()
        order_value = item.get("order")
        try:
            order = int(order_value) if order_value is not None else None
        except (TypeError, ValueError):
            order = None
        updates.append(
            {
                "id": evidence_id,
                "split_index": split_index,
                "name": name[:255],
                "purpose": purpose[:2000],
                "included": item.get("include") is not False and str(item.get("include") or "").lower() != "false",
                "order": order,
                "page_range": _parse_page_range(item.get("pages")),
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
