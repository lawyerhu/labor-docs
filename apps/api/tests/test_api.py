import os
from datetime import datetime, timedelta, timezone

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./test_api.db"
os.environ["LOCAL_STORAGE_DIR"] = "./test-storage"

from fastapi.testclient import TestClient

from app.main import create_app


def test_user_can_create_incomplete_case_and_generate_formal_documents():
    with TestClient(create_app()) as client:
        requested = client.post("/api/auth/request-code", json={"email": "user@example.com"})
        assert requested.status_code == 200
        code = requested.json()["dev_code"]
        verified = client.post("/api/auth/verify", json={"email": "user@example.com", "code": code})
        assert verified.status_code == 200

        created = client.post(
            "/api/cases",
            json={"title": "不服仲裁裁决", "case_stage": "litigation", "party_side": "employer"},
        )
        assert created.status_code == 201
        case = created.json()
        assert case["access_status"] == "free"

        generated = client.post(f"/api/cases/{case['id']}/generate")
        assert generated.status_code == 200
        body = generated.json()
        assert body["readiness"] == "formal_with_placeholders"
        assert len(body["artifacts"]) == 3


def test_upload_rejects_executable_renamed_as_pdf():
    with TestClient(create_app()) as client:
        code = client.post("/api/auth/request-code", json={"email": "upload@example.com"}).json()["dev_code"]
        client.post("/api/auth/verify", json={"email": "upload@example.com", "code": code})
        case = client.post(
            "/api/cases",
            json={"title": "材料安全检查", "case_stage": "arbitration", "party_side": "worker"},
        ).json()

        response = client.post(
            f"/api/cases/{case['id']}/evidence",
            files={"file": ("恶意材料.pdf", b"MZ-not-a-pdf", "application/pdf")},
        )

        assert response.status_code == 415


def test_legal_search_persists_unverified_snapshot_without_fabricating_basis():
    with TestClient(create_app()) as client:
        code = client.post("/api/auth/request-code", json={"email": "legal@example.com"}).json()["dev_code"]
        client.post("/api/auth/verify", json={"email": "legal@example.com", "code": code})
        case = client.post(
            "/api/cases",
            json={"title": "法律核验", "case_stage": "litigation", "party_side": "worker"},
        ).json()

        response = client.post(
            f"/api/cases/{case['id']}/legal-search",
            json={"query": "劳动合同法"},
        )

        assert response.status_code == 200
        snapshot = response.json()["snapshot"]
        assert snapshot["verified"] is False
        assert snapshot["source"] == "yuandian:not-configured"
        stored = client.get(f"/api/cases/{case['id']}").json()
        assert stored["data"]["legal_snapshots"][0]["verified"] is False
        assert "legal_basis" not in stored["data"]


def test_otp_rejects_repeated_wrong_codes():
    with TestClient(create_app()) as client:
        requested = client.post("/api/auth/request-code", json={"email": "otp-limit@example.com"})
        assert requested.status_code == 200
        for _ in range(4):
            response = client.post("/api/auth/verify", json={"email": "otp-limit@example.com", "code": "000000"})
            assert response.status_code == 400
        blocked = client.post("/api/auth/verify", json={"email": "otp-limit@example.com", "code": "000000"})
        assert blocked.status_code == 429


def test_first_case_free_entitlement_is_not_reused():
    with TestClient(create_app()) as client:
        code = client.post("/api/auth/request-code", json={"email": "free-case@example.com"}).json()["dev_code"]
        client.post("/api/auth/verify", json={"email": "free-case@example.com", "code": code})
        first = client.post(
            "/api/cases",
            json={"title": "首案", "case_stage": "arbitration", "party_side": "worker"},
        )
        second = client.post(
            "/api/cases",
            json={"title": "第二案", "case_stage": "arbitration", "party_side": "worker"},
        )
        assert first.json()["access_status"] == "free"
        assert second.json()["access_status"] == "locked"


def test_expired_case_is_deleted_without_resetting_free_entitlement():
    with TestClient(create_app()) as client:
        code = client.post("/api/auth/request-code", json={"email": "expiry@example.com"}).json()["dev_code"]
        client.post("/api/auth/verify", json={"email": "expiry@example.com", "code": code})
        created = client.post(
            "/api/cases",
            json={"title": "待清理案件", "case_stage": "arbitration", "party_side": "worker"},
        ).json()

        from app.database import SessionLocal
        from app.models import CaseRecord
        from app.tasks import expire_cases

        with SessionLocal() as db:
            case = db.get(CaseRecord, created["id"])
            case.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.commit()

        assert expire_cases() == 1
        assert client.get(f"/api/cases/{created['id']}").status_code == 404
        second = client.post(
            "/api/cases",
            json={"title": "清理后的新案件", "case_stage": "arbitration", "party_side": "worker"},
        )
        assert second.json()["access_status"] == "locked"
