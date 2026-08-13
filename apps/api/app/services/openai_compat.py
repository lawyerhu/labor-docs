from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelProvider:
    name: str
    base_url: str
    api_key: str
    model: str
    wire_api: str
    reasoning_effort: str | None = None


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def response_text(payload: dict[str, Any]) -> str:
    """Read JSON text from both Chat Completions and Responses payloads."""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            text = _content_text(message.get("content"))
            if text:
                return text

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                    text = content.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        if parts:
            return "".join(parts)
    raise ValueError("模型响应中没有文本内容")


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("模型输出不是JSON对象")
    return value


RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _configured_provider(settings: Any, prefix: str, name: str) -> ModelProvider | None:
    base_url = getattr(settings, f"{prefix}_base_url", None)
    api_key = getattr(settings, f"{prefix}_api_key", None)
    model = getattr(settings, f"{prefix}_model", None)
    if not base_url or not api_key or not model:
        return None
    return ModelProvider(
        name=name,
        base_url=str(base_url),
        api_key=str(api_key),
        model=str(model),
        wire_api=str(getattr(settings, f"{prefix}_wire_api", "chat")),
        reasoning_effort=getattr(settings, f"{prefix}_reasoning_effort", None),
    )


async def _complete_with_provider(
    provider: ModelProvider,
    *,
    system: str,
    user: str,
    timeout: float,
    attempts: int,
) -> dict[str, Any]:
    wire_api = provider.wire_api.strip().lower()
    if wire_api == "responses":
        url = provider.base_url.rstrip("/") + "/responses"
        body: dict[str, Any] = {
            "model": provider.model,
            "instructions": system,
            "input": user,
        }
        if provider.reasoning_effort:
            body["reasoning"] = {"effort": provider.reasoning_effort}
    elif wire_api in {"chat", "chat_completions", "chat-completions"}:
        url = provider.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": provider.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if provider.reasoning_effort:
            body["reasoning_effort"] = provider.reasoning_effort
    else:
        raise RuntimeError(f"{provider.name} WIRE_API 只能是 chat 或 responses")

    last_error = "模型接口失败"
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            last_error = f"模型接口超时（{int(timeout)}秒）"
            if attempt >= attempts:
                raise RuntimeError(last_error) from exc
        except httpx.HTTPError as exc:
            last_error = f"模型接口不可达：{type(exc).__name__}"
            if attempt >= attempts:
                raise RuntimeError(last_error) from exc
        else:
            if response.status_code < 400:
                return parse_json_text(response_text(response.json()))
            last_error = f"模型接口返回 {response.status_code}"
            if response.status_code not in RETRYABLE_STATUS or attempt >= attempts:
                raise RuntimeError(last_error)
        await asyncio.sleep(min(8, 2 * attempt))
    raise RuntimeError(last_error)


async def complete_json(*, system: str, user: str, timeout: float = 60, attempts: int = 3) -> dict[str, Any]:
    settings = get_settings()
    providers = [
        _configured_provider(settings, "deepseek", "DeepSeek 主模型"),
        _configured_provider(settings, "openai", "OpenAI 第一备用模型"),
        _configured_provider(settings, "grok", "Grok 第二备用模型"),
    ]
    configured = [provider for provider in providers if provider is not None]
    if not configured:
        raise RuntimeError("大模型未配置")

    errors: list[str] = []
    for index, provider in enumerate(configured):
        try:
            return await _complete_with_provider(
                provider,
                system=system,
                user=user,
                timeout=timeout,
                attempts=attempts,
            )
        except RuntimeError as error:
            if len(configured) == 1:
                raise
            errors.append(f"{provider.name}失败：{error}")
            if index + 1 < len(configured):
                logger.warning(
                    "[MODEL-FAILOVER] current=%s fallback=%s reason=%s",
                    provider.name,
                    configured[index + 1].name,
                    error,
                )

    raise RuntimeError("；".join(errors))
