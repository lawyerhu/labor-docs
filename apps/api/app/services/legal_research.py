from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

import httpx

from app.config import get_settings


@dataclass(frozen=True)
class LegalSnapshot:
    query: str
    verified: bool
    retrieved_at: str
    source: str
    content: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "verified": self.verified,
            "retrieved_at": self.retrieved_at,
            "source": self.source,
            "content": self.content,
        }


def grounded_legal_basis(candidates: list[Any], law_snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Keep only citations that can be found in a verified YuanDian law snapshot."""
    law = law_snapshot if isinstance(law_snapshot, dict) else {}
    if law.get("verified") is not True:
        return []
    source_text = json.dumps(law.get("content") or {}, ensure_ascii=False)
    grounded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        value = item if isinstance(item, dict) else {"citation": item}
        citation = str(value.get("citation") or "").strip()
        if not citation or citation in seen or citation not in source_text:
            continue
        seen.add(citation)
        grounded.append(
            {
                "citation": citation,
                "verified": True,
                "source": law.get("source"),
                "retrieved_at": law.get("retrieved_at"),
            }
        )
    return grounded


class YuandianLegalResearchProvider:
    """元典检索边界。MCP 或旧网关失败时返回未核验快照，不阻断文书生成。"""

    async def search(self, query: str) -> LegalSnapshot:
        return await self.search_law(query)

    async def search_law(self, query: str) -> LegalSnapshot:
        return await self._mcp_search("law", query, ("vector", "search"))

    async def search_cases(self, query: str) -> LegalSnapshot:
        return await self._mcp_search("case", query, ("semantic", "search"))

    async def search_company(self, query: str) -> LegalSnapshot:
        return await self._mcp_search("company", query, ("enterprise", "search"))

    async def _mcp_search(self, category: str, query: str, preferred: tuple[str, ...]) -> LegalSnapshot:
        settings = get_settings()
        now = datetime.now(timezone.utc).isoformat()
        token = settings.yuandian_token
        url = getattr(settings, f"yuandian_{category}_mcp_url", "")
        if category == "law" and not url and settings.yuandian_endpoint:
            return await self._legacy_search(query)
        if not token:
            if category == "law" and settings.yuandian_endpoint:
                return await self._legacy_search(query)
            return LegalSnapshot(query, False, now, "yuandian:not-configured", {})
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    tool = next(
                        (item for item in tools if all(part in item.name.lower() for part in preferred)),
                        next((item for item in tools if "search" in item.name.lower()), None),
                    )
                    if tool is None:
                        raise RuntimeError("元典 MCP 未提供检索工具")
                    properties = (tool.inputSchema or {}).get("properties", {})
                    query_key = next((key for key in ("query", "keyword", "keywords", "text", "name") if key in properties), "query")
                    arguments: dict[str, Any] = {query_key: query}
                    if "sxx" in properties:
                        arguments["sxx"] = "现行有效"
                    result = await session.call_tool(tool.name, arguments=arguments)
                    content = {
                        "tool": tool.name,
                        "items": [item.model_dump(mode="json") for item in result.content],
                        "is_error": bool(result.isError),
                    }
                    return LegalSnapshot(query, not result.isError, now, f"yuandian:mcp:{category}", content)
        except Exception as exc:
            return LegalSnapshot(query, False, now, f"yuandian:mcp:{category}:unavailable", {"error": type(exc).__name__})

    async def _legacy_search(self, query: str) -> LegalSnapshot:
        settings = get_settings()
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    str(settings.yuandian_endpoint),
                    headers={"Authorization": f"Bearer {settings.yuandian_token}"},
                    json={"query": query},
                )
                response.raise_for_status()
            content = response.json()
            verified = isinstance(content, dict) and content.get("verified") is True
            source = "yuandian:verified" if verified else "yuandian:unverified"
            return LegalSnapshot(query, verified, now, source, content if isinstance(content, dict) else {})
        except Exception:
            return LegalSnapshot(query, False, now, "yuandian:legacy:unavailable", {})
