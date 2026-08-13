import asyncio

import httpx

from app.services import openai_compat
from app.services.openai_compat import parse_json_text, response_text


def test_response_text_reads_chat_completions_payload():
    payload = {"choices": [{"message": {"content": '{"ok": true}'}}]}

    assert parse_json_text(response_text(payload)) == {"ok": True}


def test_response_text_reads_responses_payload():
    payload = {"output": [{"content": [{"type": "output_text", "text": '{"ok": true}'}]}]}

    assert parse_json_text(response_text(payload)) == {"ok": True}


def test_parse_json_text_accepts_markdown_fence():
    assert parse_json_text("```json\n{\"ok\": true}\n```") == {"ok": True}


def test_compact_case_for_draft_drops_bulky_unused_fields():
    from app.services.document_drafting import compact_case_for_draft

    compact = compact_case_for_draft(
        {
            "id": "case-1",
            "title": "劳动争议案件",
            "case_stage": "litigation",
            "party_side": "worker",
            "status": "generating",
            "access_status": "free",
            "generation_count": 1,
            "data": {
                "parties": {"initiating": {"name": "曹某"}},
                "legal_snapshots": [{"query": "应省略"}],
                "_ai_draft": {"claims": ["旧稿"]},
            },
        }
    )

    assert compact["data"] == {"parties": {"initiating": {"name": "曹某"}}}
    assert "legal_snapshots" not in compact["data"]
    assert "_ai_draft" not in compact["data"]


def test_complete_json_includes_model_status_code(monkeypatch):
    class FakeSettings:
        openai_base_url = "https://example.test/v1"
        openai_api_key = "test-key"
        openai_model = "test-model"
        openai_wire_api = "responses"

    class FakeResponse:
        status_code = 429
        text = '{"error":{"message":"rate limited"}}'

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(openai_compat, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    try:
        asyncio.run(openai_compat.complete_json(system="sys", user="user"))
    except RuntimeError as exc:
        assert str(exc) == "模型接口返回 429"
    else:
        raise AssertionError("expected RuntimeError")


def test_fallback_draft_keeps_placeholder_package_when_model_is_down():
    from app.services.document_drafting import fallback_draft_case_documents

    draft = fallback_draft_case_documents(
        case={"data": {}},
        evidence_items=[{"id": "ev-1", "original_name": "scan.pdf", "name": "scan.pdf"}],
        reason="模型接口返回 503",
    )

    assert draft["claims"]
    assert draft["facts_and_reasons"]
    assert draft["evidence_updates"][0]["name"] != "scan.pdf"
    assert any("503" in item for item in draft["missing_fields"])


def test_complete_json_retries_service_unavailable(monkeypatch):
    class FakeSettings:
        openai_base_url = "https://example.test/v1"
        openai_api_key = "test-key"
        openai_model = "test-model"
        openai_wire_api = "responses"

    class Unavailable:
        status_code = 503

    class Success:
        status_code = 200

        def json(self):
            return {"output_text": '{"ok": true}'}

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return Unavailable() if calls["n"] == 1 else Success()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(openai_compat, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(openai_compat.asyncio, "sleep", no_sleep)

    parsed = asyncio.run(openai_compat.complete_json(system="sys", user="user"))
    assert parsed == {"ok": True}
    assert calls["n"] == 2


def test_complete_json_maps_timeout(monkeypatch):
    class FakeSettings:
        openai_base_url = "https://example.test/v1"
        openai_api_key = "test-key"
        openai_model = "test-model"
        openai_wire_api = "responses"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(openai_compat, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    try:
        asyncio.run(openai_compat.complete_json(system="sys", user="user", timeout=12))
    except RuntimeError as exc:
        assert str(exc) == "模型接口超时（12秒）"
    else:
        raise AssertionError("expected RuntimeError")
