from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.material_extraction import extract_material_with_vision
from app.services.openai_compat import complete_json


def _fallback_evidence_name(text: str) -> str:
    """Use only text actually present in the material when the model times out."""
    compact = re.sub(r"\s+", "", text or "")
    labels = (
        (("仲裁", "裁决"), "劳动人事争议仲裁裁决书"),
        (("解除劳动合同",), "解除劳动合同协议"),
        (("劳动合同",), "劳动合同"),
        (("工资", "明细"), "工资支付记录"),
        (("银行", "流水"), "银行流水"),
        (("聊天",), "沟通记录"),
        (("微信",), "微信沟通记录"),
        (("送达", "签收"), "送达签收凭证"),
    )
    for keywords, label in labels:
        if all(keyword in compact for keyword in keywords):
            return label
    first_line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return first_line[:80] or "待识别证据材料"


async def analyze_evidence_text(*, case: dict[str, Any], item: dict[str, Any], text: str) -> dict[str, Any]:
    prompt = {
        "case_stage": case.get("case_stage"),
        "party_side": case.get("party_side"),
        "case_data": case.get("data") or {},
        "original_filename": item.get("original_name"),
        "material_text": text,
    }
    parsed = await complete_json(
        system="""你是中国劳动争议案件的证据审查助手。请阅读材料正文后只输出JSON。
name：用材料的法律性质和核心内容命名，不得照抄文件名，例如“劳动人事争议仲裁裁决书”“解除劳动合同协议”。
purpose：结合本案争议，用一到两句说明该材料具体证明的事实；不得使用“证明案件事实”等空泛表述。
summary：客观概括材料的关键内容。key_facts：字符串数组。confidence：0到1。
不得虚构正文不存在的人名、日期、金额、签章或结论。输出字段必须为 name、purpose、summary、key_facts、confidence。""",
        user=json.dumps(prompt, ensure_ascii=False),
        timeout=30,
        attempts=1,
    )
    name = str(parsed.get("name") or "证据材料").strip()[:255]
    purpose = str(parsed.get("purpose") or "待结合案件事实核实证明目的").strip()[:2000]
    return {
        "name": name,
        "purpose": purpose,
        "analysis": {
            "summary": str(parsed.get("summary") or "").strip(),
            "key_facts": [str(value) for value in parsed.get("key_facts") or []][:20],
            "confidence": parsed.get("confidence"),
        },
    }


async def analyze_material(*, case: dict[str, Any], item: dict[str, Any], path: Path) -> dict[str, Any]:
    extraction = await extract_material_with_vision(path)
    try:
        result = await analyze_evidence_text(case=case, item=item, text=extraction.text)
    except Exception as exc:
        # Naming/purpose enrichment must never discard successfully extracted OCR.
        result = {
            "name": _fallback_evidence_name(extraction.text),
            "purpose": "材料正文已读取；证明目的待结合案件事实核实。",
            "analysis": {
                "summary": extraction.text[:1000],
                "key_facts": [],
                "confidence": None,
                "analysis_fallback": True,
                "model_error": type(exc).__name__,
            },
        }
    result["analysis"].update(
        {
            "extracted_text": extraction.text,
            "extraction_version": 2,
            "vision_reviewed_pages": list(extraction.vision_reviewed_pages),
            "page_count": int(extraction.page_count or 0),
        }
    )
    return result
