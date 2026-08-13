import asyncio

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings
from app.main import create_app
from app.schemas import InternalGenerationJobInput
from app.services.legal_research import LegalSnapshot


def test_health_exposes_safe_git_sha(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = get_settings()
    monkeypatch.setattr(settings, "render_git_commit", "e2e838f0123456789abcdef")
    monkeypatch.setattr(settings, "deepseek_base_url", None)
    monkeypatch.setattr(settings, "deepseek_api_key", None)
    monkeypatch.setattr(settings, "deepseek_model", None)
    monkeypatch.setattr(settings, "openai_base_url", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "openai_model", None)
    monkeypatch.setattr(settings, "grok_base_url", "https://example.invalid/v1")
    monkeypatch.setattr(settings, "grok_api_key", "secret-not-exposed")
    monkeypatch.setattr(settings, "grok_model", "grok-test")

    with TestClient(create_app()) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["git_sha"] == "e2e838f01234"
    assert response.json()["deepseek_primary_configured"] is False
    assert response.json()["deepseek_api_key_env_present"] is False
    assert response.json()["deepseek_api_key_env_nonblank"] is False
    assert response.json()["openai_fallback_configured"] is False
    assert response.json()["grok_fallback_configured"] is True
    assert "secret-not-exposed" not in response.text


def test_internal_generation_request_is_accepted_before_long_running_work(monkeypatch):
    scheduled: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def capture_task(self, func, *args, **kwargs):
        scheduled.append((func, args, kwargs))

    monkeypatch.setattr(BackgroundTasks, "add_task", capture_task)
    settings = get_settings()
    settings.generator_internal_token = "test-generator-token"

    with TestClient(create_app()) as client:
        response = client.post(
            "/internal/generation-jobs",
            headers={"Authorization": "Bearer test-generator-token"},
            json={"version": 1, "job_id": "job-1", "case_id": "case-1"},
        )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "job_id": "job-1",
        "case_id": "case-1",
    }
    assert len(scheduled) == 1


def test_internal_legal_search_uses_yuandian_law_and_case_mcp(monkeypatch):
    class FakeProvider:
        async def search_law(self, query):
            return LegalSnapshot(query, True, "2026-08-14T00:00:00Z", "yuandian:mcp:law", {"items": ["law"]})

        async def search_cases(self, query):
            return LegalSnapshot(query, True, "2026-08-14T00:00:00Z", "yuandian:mcp:case", {"items": ["case"]})

    monkeypatch.setattr(main_module, "YuandianLegalResearchProvider", FakeProvider)
    settings = get_settings()
    settings.generator_internal_token = "test-generator-token"

    with TestClient(create_app()) as client:
        response = client.post(
            "/internal/legal-search",
            headers={"Authorization": "Bearer test-generator-token"},
            json={"query": "劳动争议起诉期限"},
        )

    assert response.status_code == 200
    assert response.json()["law"]["source"] == "yuandian:mcp:law"
    assert response.json()["law"]["verified"] is True
    assert response.json()["cases"]["source"] == "yuandian:mcp:case"


def test_background_generation_reports_completed_result(monkeypatch):
    reported: list[tuple[str, str, dict[str, object]]] = []

    async def fetch_case(case_id):
        return {"case": {"id": case_id}}

    async def generate(worker_payload, job_id):
        return {"artifacts": [], "readiness": "formal_with_placeholders"}

    async def report(job_id, case_id, *, result=None, error=None):
        reported.append((job_id, case_id, {"result": result, "error": error}))

    monkeypatch.setattr(main_module, "fetch_worker_generation_input", fetch_case)
    monkeypatch.setattr(main_module, "run_remote_generation", generate)
    monkeypatch.setattr(main_module, "report_generation_result", report)

    asyncio.run(
        main_module._process_internal_generation_job(
            InternalGenerationJobInput(version=1, job_id="job-1", case_id="case-1")
        )
    )

    assert reported == [
        (
            "job-1",
            "case-1",
            {
                "result": {"artifacts": [], "readiness": "formal_with_placeholders"},
                "error": None,
            },
        )
    ]


def test_background_generation_reports_specific_failure(monkeypatch):
    reported: list[str | None] = []

    async def fetch_case(case_id):
        return {"case": {"id": case_id}}

    async def generate(worker_payload, job_id):
        raise RuntimeError("extract evidence failed: TesseractNotFoundError")

    async def report(job_id, case_id, *, result=None, error=None):
        reported.append(error)

    monkeypatch.setattr(main_module, "fetch_worker_generation_input", fetch_case)
    monkeypatch.setattr(main_module, "run_remote_generation", generate)
    monkeypatch.setattr(main_module, "report_generation_result", report)

    asyncio.run(
        main_module._process_internal_generation_job(
            InternalGenerationJobInput(version=1, job_id="job-1", case_id="case-1")
        )
    )

    assert reported == ["Document generation failed: extract evidence failed: TesseractNotFoundError"]
