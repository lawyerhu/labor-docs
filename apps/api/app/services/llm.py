import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import ExtractionPatch


SYSTEM_PROMPT = """你是劳动争议案情信息提取器。只输出JSON，不作法律结论，不编造事实。
输出对象可包含 parties、employment_facts、arbitration、claims、unresolved_conflicts、evidence_gaps。
不确定的信息不要填写；材料矛盾时写入 unresolved_conflicts。"""


class OpenAICompatibleExtractor:
    async def extract(self, message: str, current_data: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        if not settings.openai_base_url or not settings.openai_api_key or not settings.openai_model:
            return {}
        url = settings.openai_base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": settings.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"current_data": current_data, "message": message}, ensure_ascii=False)},
            ],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {settings.openai_api_key}"}, json=body)
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        try:
            return ExtractionPatch.model_validate(parsed).model_dump(exclude_none=True)
        except ValidationError:
            return {}


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
