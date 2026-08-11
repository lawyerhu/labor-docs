import asyncio

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
