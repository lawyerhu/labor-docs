from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
import re
from typing import Any

import httpx

from app.config import get_settings
from app.services.openai_compat import complete_json


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


def _regex_company_name(text: str) -> str | None:
    """提取公司全称的正则兜底：优先取“被申请人/用人单位/被告/申请人”之后的公司名。"""
    pattern = r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,40}?(?:有限责任公司|股份有限公司|有限公司))"
    for marker in ("被申请人", "用人单位", "被告", "申请人"):
        position = text.rfind(marker)
        if position < 0:
            continue
        match = re.match(pattern, text[position + len(marker):])
        if match:
            return match.group(1)[:100]
    match = re.search(pattern, text)
    return match.group(1)[:100] if match else None


async def extract_company_name(
    facts: str,
    claims: str,
    current_data: dict[str, Any] | None = None,
) -> str | None:
    """让大模型从案情中理解用人单位公司名称；解析失败时回退到正则提取。

    已确认的 parties 优先（无需再调用大模型）；其次用大模型理解案情；
    大模型不可用或未给出公司名时用正则兜底。
    """
    if isinstance(current_data, dict):
        parties = current_data.get("parties")
        if isinstance(parties, dict):
            for party in parties.values():
                if not isinstance(party, dict):
                    continue
                name = str(party.get("name") or "").strip()
                party_type = str(party.get("type") or "").strip().lower()
                if name and (party_type == "company" or party.get("credit_code") or name.endswith(("公司", "事务所", "中心"))):
                    return name[:100]
    combined = "\n".join(part for part in (facts, claims) if part)[:2000]
    if not combined:
        return None
    try:
        parsed = await complete_json(
            system=(
                "你是劳动争议文书助手。请从案情中找出用人单位（被申请人/被告/用人单位一方）的准确公司全称。"
                '只输出JSON：{"company_name": "公司全称"}。若案情中没有公司主体或无法确定公司名称，company_name 输出 null。'
                "不要编造，不要输出其他内容。"
            ),
            user=json.dumps({"case": combined}, ensure_ascii=False),
            timeout=20,
        )
        name = str(parsed.get("company_name") or "").strip()
        if name and name.lower() != "null":
            return name[:100]
    except Exception:
        pass
    return _regex_company_name(combined)


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

    async def probe(self) -> dict[str, dict[str, Any]]:
        settings = get_settings()
        token = getattr(settings, "yuandian_token", None) or getattr(settings, "yuandian_api_key", None)
        if not token:
            return {category: {"ok": False, "reason": "not-configured"} for category in ("law", "case", "company")}
        return dict(
            zip(
                ("law", "case", "company"),
                await asyncio.gather(
                    self._probe_category("law", settings.yuandian_law_mcp_url, token),
                    self._probe_category("case", settings.yuandian_case_mcp_url, token),
                    self._probe_category("company", settings.yuandian_company_mcp_url, token),
                ),
            )
        )

    async def _probe_category(self, category: str, url: str, token: str) -> dict[str, Any]:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
            async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(20.0, connect=8.0)) as client:
                async with streamable_http_client(url, http_client=client) as (read, write, _session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        return {"ok": True, "transport": "streamable_http", "tools_count": len(tools)}
        except Exception as exc:
            detail = self._exception_text(exc).lower()
            reason = "auth-failed" if "401" in detail or "unauthorized" in detail else "unavailable"
            return {"ok": False, "transport": "streamable_http", "reason": reason, "error": type(exc).__name__}

    @classmethod
    def _exception_text(cls, exc: BaseException) -> str:
        nested = getattr(exc, "exceptions", ())
        return " ".join([type(exc).__name__, str(exc), *(cls._exception_text(item) for item in nested)])

    async def _mcp_search(self, category: str, query: str, preferred: tuple[str, ...]) -> LegalSnapshot:
        settings = get_settings()
        now = datetime.now(timezone.utc).isoformat()
        token = getattr(settings, "yuandian_token", None) or getattr(settings, "yuandian_api_key", None)
        url = getattr(settings, f"yuandian_{category}_mcp_url", "")
        if category == "law" and not url and settings.yuandian_endpoint:
            return await self._legacy_search(query)
        if not token:
            if category == "law" and settings.yuandian_endpoint:
                return await self._legacy_search(query)
            return LegalSnapshot(query, False, now, "yuandian:not-configured", {})
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            }
            async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                async with streamable_http_client(url, http_client=client) as (read, write, _session_id):
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
                        result = await session.call_tool(tool.name, arguments=arguments)
                        content = {
                            "tool": tool.name,
                            "items": [item.model_dump(mode="json") for item in result.content],
                            "is_error": bool(result.isError),
                        }
                        snapshot = LegalSnapshot(
                            query,
                            not result.isError and self._mcp_has_results(content),
                            now,
                            f"yuandian:mcp:{category}",
                            content,
                        )
                        if category != "company" and snapshot.verified:
                            return snapshot
                        direct = await self._openapi_search(category, query, token)
                        if direct.verified:
                            if snapshot.verified:
                                return LegalSnapshot(
                                    query,
                                    True,
                                    now,
                                    f"yuandian:mcp+openapi:{category}",
                                    {"mcp": content, "openapi": direct.content},
                                )
                            return direct
                        return snapshot
        except Exception as exc:
            direct = await self._openapi_search(category, query, token)
            if direct.verified:
                return direct
            if direct.source.endswith(":auth-failed"):
                return direct
            detail = str(exc).lower()
            reason = "auth-failed" if "401" in detail or "unauthorized" in detail else "unavailable"
            return LegalSnapshot(query, False, now, f"yuandian:mcp:{category}:{reason}", {"error": type(exc).__name__})

    async def _openapi_search(self, category: str, query: str, token: str) -> LegalSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        headers = {"X-API-Key": token, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                if category == "law":
                    response = await client.post(
                        "https://open.chineselaw.com/open/law_vector_search",
                        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
                        json={
                            "query": query,
                            "rewrite_flag": False,
                            "fatiao_filter": {"sxx": ["现行有效"]},
                            "return_num": 10,
                        },
                    )
                    content = response.json()
                elif category == "case":
                    response = await client.post(
                        "https://open.chineselaw.com/open/case_vector_search",
                        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
                        json={
                            "query": query,
                            "rewrite_flag": False,
                            "wenshu_filter": {"wenshu_type": "民事案件"},
                            "return_num": 10,
                        },
                    )
                    content = response.json()
                elif category == "company":
                    response = await client.get(
                        "https://open.chineselaw.com/open/rh_enterpriseSearch",
                        headers=headers,
                        params={"name": query, "top_k": 5},
                    )
                    search = response.json()
                    if not self._openapi_success(response, search):
                        return LegalSnapshot(query, False, now, "yuandian:openapi:company:unavailable", {"http_status": response.status_code})
                    candidates = search.get("data") if isinstance(search, dict) else None
                    if not isinstance(candidates, list) or not candidates:
                        return LegalSnapshot(
                            query,
                            False,
                            now,
                            "yuandian:openapi:company:no-result",
                            {"search": search, "base_info": {}},
                        )
                    candidate = next(
                        (
                            item for item in candidates
                            if isinstance(item, dict) and str(item.get("企业名称") or "").strip() == query.strip()
                        ),
                        None,
                    )
                    if candidate is None:
                        return LegalSnapshot(
                            query,
                            False,
                            now,
                            "yuandian:openapi:company:no-match",
                            {"search": search, "base_info": {}},
                        )
                    company_id = str(candidate.get("id") or "").strip()
                    if not company_id:
                        return LegalSnapshot(
                            query,
                            False,
                            now,
                            "yuandian:openapi:company:no-match",
                            {"search": search, "base_info": {}},
                        )
                    base_info: dict[str, Any] = {}
                    detail_response = await client.get(
                        "https://open.chineselaw.com/open/rh_enterpriseBaseInfo",
                        headers=headers,
                        params={"id": company_id},
                    )
                    detail_body = detail_response.json()
                    if self._openapi_success(detail_response, detail_body):
                        base_info = detail_body
                    return LegalSnapshot(
                        query,
                        True,
                        now,
                        "yuandian:openapi:company",
                        {"search": search, "base_info": base_info},
                    )
                else:
                    return LegalSnapshot(query, False, now, f"yuandian:openapi:{category}:unsupported", {})
            verified = self._openapi_success(response, content) and self._openapi_has_results(content)
            business_code = content.get("code") if isinstance(content, dict) else None
            auth_failed = response.status_code == 401 or business_code == 401
            source = (
                f"yuandian:openapi:{category}"
                if verified
                else f"yuandian:openapi:{category}:{'auth-failed' if auth_failed else 'unavailable'}"
            )
            safe_content = content if verified and isinstance(content, dict) else {"http_status": response.status_code}
            return LegalSnapshot(query, verified, now, source, safe_content)
        except Exception as exc:
            return LegalSnapshot(
                query,
                False,
                now,
                f"yuandian:openapi:{category}:unavailable",
                {"error": type(exc).__name__},
            )

    @staticmethod
    def _openapi_success(response: httpx.Response, content: Any) -> bool:
        if response.status_code >= 400 or not isinstance(content, dict):
            return False
        code = content.get("code")
        return code in {200, 201} and content.get("status") not in {"failed", "error"}

    @staticmethod
    def _openapi_has_results(content: dict[str, Any]) -> bool:
        """业务成功但返回空数据不能算已核验来源。"""
        data = content.get("data")
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, dict):
            return bool(data)
        return True

    @staticmethod
    def _mcp_has_results(content: dict[str, Any]) -> bool:
        """MCP 返回“未查询到相关数据”等空结果时不算已核验来源。"""
        empty_markers = ("未查询到相关数据", "未查询到", "没有查询到", "暂无相关", "无相关数据", "没有找到", "未找到")
        for item in content.get("items") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if any(marker in text for marker in empty_markers) or any(
                marker in lowered for marker in ("no data", "no result", "not found", "empty result")
            ):
                return False
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                return True
            if isinstance(parsed, dict):
                code = parsed.get("code")
                if code in (200, 201):
                    data = parsed.get("data")
                    if isinstance(data, list):
                        return len(data) > 0
                    if isinstance(data, dict):
                        return bool(data)
                    if data is None and "data" in parsed:
                        return False
                return True
            if isinstance(parsed, list):
                return len(parsed) > 0
        return True

    async def _legacy_search(self, query: str) -> LegalSnapshot:
        settings = get_settings()
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    str(settings.yuandian_endpoint),
                    headers={"Authorization": f"Bearer {getattr(settings, 'yuandian_token', None) or getattr(settings, 'yuandian_api_key', None)}"},
                    json={"query": query},
                )
                response.raise_for_status()
            content = response.json()
            verified = isinstance(content, dict) and content.get("verified") is True
            source = "yuandian:verified" if verified else "yuandian:unverified"
            return LegalSnapshot(query, verified, now, source, content if isinstance(content, dict) else {})
        except Exception:
            return LegalSnapshot(query, False, now, "yuandian:legacy:unavailable", {})
