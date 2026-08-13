from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.services.material_extraction import extract_material_text
from app.services.openai_compat import complete_json


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
        timeout=120,
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
    text = await asyncio.to_thread(extract_material_text, path)
    result = await analyze_evidence_text(case=case, item=item, text=text)
    result["analysis"]["extracted_text"] = text
    return result
