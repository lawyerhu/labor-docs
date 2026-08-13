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


def test_complete_json_falls_back_to_grok(monkeypatch):
    class FakeSettings:
        openai_base_url = "https://primary.test/v1"
        openai_api_key = "primary-key"
        openai_model = "primary-model"
        openai_wire_api = "responses"
        grok_base_url = "https://fallback.test/v1"
        grok_api_key = "fallback-key"
        grok_model = "grok-model"
        grok_wire_api = "chat"
        grok_reasoning_effort = "high"

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    requests = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            requests.append((url, headers, json))
            if url == "https://primary.test/v1/responses":
                return FakeResponse(503)
            return FakeResponse(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(openai_compat, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    parsed = asyncio.run(openai_compat.complete_json(system="sys", user="user", attempts=1))

    assert parsed == {"ok": True}
    assert requests[0][0] == "https://primary.test/v1/responses"
    assert requests[1][0] == "https://fallback.test/v1/chat/completions"
    assert requests[1][1]["Authorization"] == "Bearer fallback-key"
    assert requests[1][2]["model"] == "grok-model"
    assert requests[1][2]["reasoning_effort"] == "high"


def test_complete_json_uses_deepseek_then_openai_then_grok(monkeypatch):
    class FakeSettings:
        deepseek_base_url = "https://deepseek.test/v1"
        deepseek_api_key = "deepseek-key"
        deepseek_model = "deepseek-v4-flash"
        deepseek_wire_api = "chat"
        deepseek_reasoning_effort = "high"
        openai_base_url = "https://openai.test/v1"
        openai_api_key = "openai-key"
        openai_model = "gpt-5.6-sol"
        openai_wire_api = "responses"
        grok_base_url = "https://grok.test/v1"
        grok_api_key = "grok-key"
        grok_model = "grok-4.6"
        grok_wire_api = "chat"
        grok_reasoning_effort = "high"

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    requests = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            requests.append((url, headers, json))
            if url in {
                "https://deepseek.test/v1/chat/completions",
                "https://openai.test/v1/responses",
            }:
                return FakeResponse(503)
            return FakeResponse(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(openai_compat, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    parsed = asyncio.run(openai_compat.complete_json(system="sys", user="user", attempts=1))

    assert parsed == {"ok": True}
    assert [request[0] for request in requests] == [
        "https://deepseek.test/v1/chat/completions",
        "https://openai.test/v1/responses",
        "https://grok.test/v1/chat/completions",
    ]
    assert requests[0][2]["model"] == "deepseek-v4-flash"
    assert requests[0][2]["reasoning_effort"] == "high"
