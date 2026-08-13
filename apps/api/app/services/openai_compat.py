from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings


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


async def complete_json(*, system: str, user: str, timeout: float = 60) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_base_url or not settings.openai_api_key or not settings.openai_model:
        raise RuntimeError("大模型未配置")

    wire_api = settings.openai_wire_api.strip().lower()
    if wire_api == "responses":
        url = settings.openai_base_url.rstrip("/") + "/responses"
        body: dict[str, Any] = {
            "model": settings.openai_model,
            "instructions": system,
            "input": user,
            "text": {"format": {"type": "json_object"}},
        }
    elif wire_api in {"chat", "chat_completions", "chat-completions"}:
        url = settings.openai_base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": settings.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    else:
        raise RuntimeError("OPENAI_WIRE_API 只能是 chat 或 responses")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=body,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"模型接口返回 {response.status_code}")
    return parse_json_text(response_text(response.json()))
