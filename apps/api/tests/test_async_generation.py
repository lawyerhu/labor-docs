import asyncio

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings
from app.main import create_app
from app.schemas import InternalGenerationJobInput


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
