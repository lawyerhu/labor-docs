import asyncio
from types import SimpleNamespace

import httpx

from app.services import legal_research


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.content


class _Client:
    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, *_args, **_kwargs):
        return _Response(self.content)


def test_legal_provider_requires_explicit_verified_flag(monkeypatch):
    monkeypatch.setattr(
        legal_research,
        "get_settings",
        lambda: type("Settings", (), {"yuandian_endpoint": "https://example.test", "yuandian_token": "token"})(),
    )
    monkeypatch.setattr(
        legal_research.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client({"legal_basis": [{"citation": "未核验", "verified": True}]}),
    )

    snapshot = asyncio.run(legal_research.YuandianLegalResearchProvider().search("劳动合同法"))

    assert snapshot.verified is False
    assert snapshot.source == "yuandian:unverified"


def test_legal_provider_preserves_verified_snapshot(monkeypatch):
    monkeypatch.setattr(
        legal_research,
        "get_settings",
        lambda: type("Settings", (), {"yuandian_endpoint": "https://example.test", "yuandian_token": "token"})(),
    )
    monkeypatch.setattr(
        legal_research.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(
            {"verified": True, "legal_basis": [{"citation": "来源条款", "status": "现行"}]}
        ),
    )

    snapshot = asyncio.run(legal_research.YuandianLegalResearchProvider().search("劳动合同法"))

    assert snapshot.verified is True
    assert snapshot.source == "yuandian:verified"
    assert snapshot.as_dict()["content"]["legal_basis"][0]["status"] == "现行"


def test_provider_accepts_yuandian_api_key_and_uses_streamable_http(monkeypatch):
    calls = []

    class StreamClient:
        async def __aenter__(self):
            calls.append("transport")
            return (object(), object(), lambda: None)

        async def __aexit__(self, *_):
            return None

    class Session:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def initialize(self):
            calls.append("initialize")

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="yuandian_law_vector_search", inputSchema={"properties": {"query": {}}})])

        async def call_tool(self, _name, arguments):
            calls.append(arguments)
            item = SimpleNamespace(model_dump=lambda **_kwargs: {"type": "text", "text": "现行法条"})
            return SimpleNamespace(content=[item], isError=False)

    monkeypatch.setattr(
        legal_research,
        "get_settings",
        lambda: SimpleNamespace(
            yuandian_token=None,
            yuandian_api_key="api-key",
            yuandian_law_mcp_url="https://example.test/mcp/law/stream",
            yuandian_endpoint=None,
        ),
    )
    monkeypatch.setattr("mcp.client.streamable_http.streamable_http_client", lambda *_args, **_kwargs: StreamClient())
    monkeypatch.setattr("mcp.ClientSession", Session)

    snapshot = asyncio.run(legal_research.YuandianLegalResearchProvider().search_law("劳动合同"))

    assert snapshot.verified is True
    assert snapshot.source == "yuandian:mcp:law"
    assert calls == ["transport", "initialize", {"query": "劳动合同"}]


def test_openapi_company_search_fetches_base_info(monkeypatch):
    responses = [
        httpx.Response(200, json={"code": 200, "status": "success", "data": [{"id": "company-1", "企业名称": "甲有限公司"}]}),
        httpx.Response(200, json={"code": 200, "status": "success", "data": {"企业名称": "甲有限公司", "注册地址": "苏州市吴中区"}}),
    ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return responses.pop(0)

    monkeypatch.setattr(legal_research.httpx, "AsyncClient", lambda **_kwargs: Client())

    snapshot = asyncio.run(legal_research.YuandianLegalResearchProvider()._openapi_search("company", "甲有限公司", "api-key"))

    assert snapshot.verified is True
    assert snapshot.source == "yuandian:openapi:company"
    assert snapshot.content["base_info"]["data"]["注册地址"] == "苏州市吴中区"


def test_openapi_http_200_business_401_is_not_verified(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs):
            return httpx.Response(200, json={"code": 401, "msg": "鉴权失败"})

    monkeypatch.setattr(legal_research.httpx, "AsyncClient", lambda **_kwargs: Client())

    snapshot = asyncio.run(legal_research.YuandianLegalResearchProvider()._openapi_search("law", "劳动合同", "invalid"))

    assert snapshot.verified is False
    assert snapshot.source == "yuandian:openapi:law:auth-failed"
    assert snapshot.content == {"http_status": 200}
