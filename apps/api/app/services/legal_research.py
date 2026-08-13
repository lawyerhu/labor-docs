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


def unavailable_snapshot(category: str, query: str, error: Exception | None = None) -> LegalSnapshot:
    detail = {"error": type(error).__name__} if error is not None else {}
    return LegalSnapshot(
        query,
        False,
        datetime.now(timezone.utc).isoformat(),
        f"yuandian:{category}:unavailable",
        detail,
    )


async def safe_research(provider: Any, category: str, query: str) -> LegalSnapshot:
    """Keep one failed YuanDian category from cancelling the other searches."""
    try:
        method_name = "search_cases" if category == "case" else f"search_{category}"
        result = await getattr(provider, method_name)(query)
    except Exception as exc:
        return unavailable_snapshot(category, query, exc)
    if isinstance(result, LegalSnapshot):
        return result
    return unavailable_snapshot(category, query)


def _find_text(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, candidate in value.items():
            if str(key).strip().lower() in keys and isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            found = _find_text(candidate, keys)
            if found:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _find_text(candidate, keys)
            if found:
                return found
    return None


def verified_company_jurisdiction(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract only explicitly verified company address/court fields.

    An address is a jurisdiction clue, not permission to invent a court name.
    A court is copied only when the company MCP returns one explicitly; the
    drafting model can use the verified address to propose a court separately.
    """
    if not isinstance(snapshot, dict) or snapshot.get("verified") is not True:
        return None
    content = snapshot.get("content") or {}
    address = _find_text(
        content,
        {
            "address",
            "company_address",
            "domicile",
            "registered_address",
            "registeredaddress",
            "reg_address",
            "registration_address",
            "registrationaddress",
            "companyregisteraddress",
            "registered_place",
            "注册地址",
            "登记地址",
            "注册住所",
            "企业住所",
            "住所",
            "住所地",
        },
    )
    court = _find_text(content, {"court", "court_name", "jurisdiction_court", "法院名称", "管辖法院", "受诉法院"})
    if not address and not court:
        return None
    result: dict[str, Any] = {
        "verified": True,
        "source": snapshot.get("source"),
        "retrieved_at": snapshot.get("retrieved_at"),
    }
    if address:
        result["registered_address"] = address
    if court:
        result["court"] = court
    return result


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
