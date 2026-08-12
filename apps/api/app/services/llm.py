import json
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.schemas import ExtractionPatch
from app.services.openai_compat import complete_json


SYSTEM_PROMPT = """你是劳动争议案情信息提取器。只输出JSON，不作法律结论，不编造事实。
输出对象可包含 parties、employment_facts、arbitration、claims、unresolved_conflicts、evidence_gaps。
不确定的信息不要填写；材料矛盾时写入 unresolved_conflicts。"""


class OpenAICompatibleExtractor:
    async def extract(self, message: str, current_data: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        if not settings.openai_base_url or not settings.openai_api_key or not settings.openai_model:
            return {}
        parsed = await complete_json(
            system=SYSTEM_PROMPT,
            user=json.dumps({"current_data": current_data, "message": message}, ensure_ascii=False),
            timeout=30,
        )
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
