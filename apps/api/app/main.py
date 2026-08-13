from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.auth import authenticate_test_admin, get_current_user, is_test_admin, issue_otp, set_session_cookie, verify_otp
from app.config import get_settings
from app.database import Base, engine, get_db
from app.domain.calculations import calculate_claim
from app.domain.readiness import assess_readiness
from app.models import ArtifactRecord, CaseRecord, EvidenceRecord, GenerationJob, Notification, RedemptionCode, User
from app.schemas import (
    ChatInput,
    ClaimCalculationInput,
    CreateCaseInput,
    InternalEvidenceAnalysisInput,
    InternalGenerationJobInput,
    InternalCaseAnalysisInput,
    InternalLegalSearchInput,
    InternalOtpInput,
    LegalSearchInput,
    PasswordLoginInput,
    RedeemInput,
    RequestCodeInput,
    UpdateCaseInput,
    UpdateEvidenceInput,
    VerifyCodeInput,
)
from app.services.email import send_otp_email
from app.services.case_analysis import CaseAnalyzer
from app.services.generation import run_generation
from app.services.llm import OpenAICompatibleExtractor, deep_merge
from app.services.legal_research import YuandianLegalResearchProvider
from app.services.evidence_analysis import analyze_material
from app.services.remote_generation import (
    RemoteGenerationError,
    _s3_stored_path,
    fetch_worker_generation_input,
    report_generation_result,
    run_remote_generation,
)
from app.services.storage import delete_if_managed, materialize_for_processing, presigned_download, save_upload


logger = logging.getLogger(__name__)


async def _process_internal_generation_job(payload: InternalGenerationJobInput) -> None:
    try:
        worker_payload = await fetch_worker_generation_input(payload.case_id)
        worker_case = worker_payload.get("case") or {}
        if worker_case.get("id") != payload.case_id:
            raise RemoteGenerationError("Worker returned a mismatched case id")
        result = await run_remote_generation(worker_payload, payload.job_id)
        await report_generation_result(payload.job_id, payload.case_id, result=result)
    except Exception as exc:
        logger.exception("[GENERATION-ERROR] background generation failure")
        try:
            await report_generation_result(
                payload.job_id,
                payload.case_id,
                error=f"Document generation failed: {str(exc) or type(exc).__name__}",
            )
        except Exception:
            logger.exception("[GENERATION-CALLBACK-ERROR] failed to report generation failure")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _case_payload(case: CaseRecord) -> dict:
    readiness = assess_readiness({"case_stage": case.case_stage, "party_side": case.party_side, "data": case.data})
    unlimited_generation = is_test_admin(case.user) if case.user else False
    return {
        "id": case.id,
        "title": case.title,
        "case_stage": case.case_stage,
        "party_side": case.party_side,
        "status": case.status,
        "access_status": case.access_status,
        "data": case.data or {},
        "generation_count": case.generation_count,
        "unlimited_generation": unlimited_generation,
        "generation_version": case.generation_version,
        "created_at": case.created_at,
        "expires_at": case.expires_at,
        "readiness": readiness.readiness,
        "missing_fields": readiness.missing_fields,
        "unresolved_conflicts": readiness.unresolved_conflicts,
        "unverified_law": readiness.unverified_law,
        "evidence_gaps": readiness.evidence_gaps,
    }


def _evidence_payload(item: EvidenceRecord) -> dict:
    return {
        "id": item.id,
        "original_name": item.original_name,
        "name": item.name,
        "purpose": item.purpose,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "status": item.status,
        "created_at": item.created_at,
    }


def _owned_case(db: Session, case_id: str, user: User) -> CaseRecord:
    case = db.scalar(select(CaseRecord).where(CaseRecord.id == case_id, CaseRecord.user_id == user.id))
    if not case:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案件不存在")
    if _aware(case.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_410_GONE, "案件已经到期")
    return case


def _hash_code(code: str) -> str:
    settings = get_settings()
    return hashlib.sha256(f"{settings.session_secret}:redeem:{code.strip().upper()}".encode()).hexdigest()


def _ensure_runtime_schema() -> None:
    Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("otp_codes")}
    if "attempts" not in columns:
        try:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE otp_codes ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"))
        except Exception:
            if "attempts" not in {column["name"] for column in inspect(engine).get_columns("otp_codes")}:
                raise
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "free_case_used" not in user_columns:
        try:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN free_case_used BOOLEAN NOT NULL DEFAULT FALSE"))
        except Exception:
            if "free_case_used" not in {column["name"] for column in inspect(engine).get_columns("users")}:
                raise
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET free_case_used = TRUE "
                "WHERE free_case_used = FALSE AND EXISTS "
                "(SELECT 1 FROM cases WHERE cases.user_id = users.id)"
            )
        )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.app_env == "test":
            Base.metadata.drop_all(engine)
        _ensure_runtime_schema()
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        commit = (settings.render_git_commit or "").strip()
        deepseek_env_value = os.environ.get("DEEPSEEK_API_KEY")
        return {
            "status": "ok",
            "version": "0.1.0",
            "generation_mode": "async",
            "git_sha": commit[:12] or None,
            "deepseek_primary_configured": all(
                (
                    settings.deepseek_base_url,
                    settings.deepseek_api_key,
                    settings.deepseek_model,
                )
            ),
            "deepseek_api_key_env_present": deepseek_env_value is not None,
            "deepseek_api_key_env_nonblank": bool((deepseek_env_value or "").strip()),
            "yuandian_mcp_configured": bool(
                settings.yuandian_token
                and settings.yuandian_law_mcp_url
                and settings.yuandian_case_mcp_url
            ),
            "openai_fallback_configured": all(
                (
                    settings.openai_base_url,
                    settings.openai_api_key,
                    settings.openai_model,
                )
            ),
            "grok_fallback_configured": all(
                (
                    settings.grok_base_url,
                    settings.grok_api_key,
                    settings.grok_model,
                )
            ),
        }

    @app.post("/internal/generation-jobs", include_in_schema=False, status_code=status.HTTP_202_ACCEPTED)
    async def internal_generation_job(
        payload: InternalGenerationJobInput,
        background_tasks: BackgroundTasks,
        authorization: str | None = Header(default=None),
    ):
        expected = settings.generator_internal_token
        if not expected:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "生成服务内部令牌尚未配置")
        if not secrets.compare_digest(authorization or "", f"Bearer {expected}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未授权")
        background_tasks.add_task(_process_internal_generation_job, payload)
        return {"status": "accepted", "job_id": payload.job_id, "case_id": payload.case_id}

    @app.post("/internal/evidence-analysis", include_in_schema=False)
    async def internal_evidence_analysis(
        payload: InternalEvidenceAnalysisInput,
        authorization: str | None = Header(default=None),
    ):
        expected = settings.generator_internal_token
        if not expected or not secrets.compare_digest(authorization or "", f"Bearer {expected}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未授权")
        processing_path = None
        try:
            worker_payload = await fetch_worker_generation_input(payload.case_id)
            case = worker_payload.get("case") or {}
            item = next(
                (value for value in worker_payload.get("evidence") or [] if value.get("id") == payload.evidence_id),
                None,
            )
            if not item:
                raise RemoteGenerationError("证据不存在")
            object_key = str(item.get("object_key") or "")
            processing_path = await asyncio.to_thread(
                materialize_for_processing,
                _s3_stored_path(object_key),
                payload.case_id,
                payload.evidence_id,
            )
            return await analyze_material(case=case, item=item, path=processing_path)
        except RemoteGenerationError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except Exception as exc:
            logger.exception("[EVIDENCE-ANALYSIS-ERROR]")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "材料读取或分析失败") from exc
        finally:
            if processing_path is not None:
                delete_if_managed(str(processing_path))

    @app.post("/internal/case-analysis", include_in_schema=False)
    async def internal_case_analysis(
        payload: InternalCaseAnalysisInput,
        authorization: str | None = Header(default=None),
    ):
        expected = settings.generator_internal_token
        if not expected or not secrets.compare_digest(authorization or "", f"Bearer {expected}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未授权")
        return await CaseAnalyzer().analyze(
            facts=payload.facts,
            claims_text=payload.claims_text,
            supplement=payload.supplement,
            current_data=payload.current_data,
            round_number=payload.round,
        )

    @app.post("/internal/legal-search", include_in_schema=False)
    async def internal_legal_search(
        payload: InternalLegalSearchInput,
        authorization: str | None = Header(default=None),
    ):
        expected = settings.generator_internal_token
        if not expected or not secrets.compare_digest(authorization or "", f"Bearer {expected}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未授权")
        provider = YuandianLegalResearchProvider()
        law, cases = await asyncio.gather(
            provider.search_law(payload.query),
            provider.search_cases(payload.query),
        )
        return {"law": law.as_dict(), "cases": cases.as_dict()}

    @app.post("/internal/auth/send-otp", include_in_schema=False)
    def internal_send_otp(
        payload: InternalOtpInput,
        authorization: str | None = Header(default=None),
    ):
        expected = settings.generator_internal_token
        if not expected or not secrets.compare_digest(authorization or "", f"Bearer {expected}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未授权")
        send_otp_email(str(payload.email), payload.code)
        return {"status": "sent"}

    @app.post("/api/auth/request-code")
    def request_code(payload: RequestCodeInput, db: Session = Depends(get_db)):
        if settings.test_admin_enabled and settings.app_env != "test":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "当前仅开放测试管理员登录")
        code = issue_otp(db, str(payload.email))
        send_otp_email(str(payload.email), code)
        response = {"message": "验证码已发送，有效期10分钟"}
        if settings.app_env in {"development", "test"}:
            response["dev_code"] = code
        return response

    @app.post("/api/auth/verify")
    def verify_code(payload: VerifyCodeInput, response: Response, db: Session = Depends(get_db)):
        if settings.test_admin_enabled and settings.app_env != "test":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "当前仅开放测试管理员登录")
        user = verify_otp(db, str(payload.email), payload.code)
        set_session_cookie(response, user)
        return {"id": user.id, "email": user.email}

    @app.post("/api/auth/login")
    def password_login(payload: PasswordLoginInput, response: Response, db: Session = Depends(get_db)):
        if not settings.test_admin_enabled:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "当前仅开放测试管理员登录")
        email = str(payload.email).lower()
        if not authenticate_test_admin(email, payload.password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误")
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, free_case_used=True)
            db.add(user)
            db.commit()
            db.refresh(user)
        set_session_cookie(response, user)
        return {"id": user.id, "email": user.email, "unlimited_generation": True}

    @app.post("/api/auth/logout", status_code=204)
    def logout(response: Response):
        response.delete_cookie("labor_session", path="/")

    @app.get("/api/auth/me")
    def me(user: User = Depends(get_current_user)):
        return {"id": user.id, "email": user.email, "unlimited_generation": is_test_admin(user)}

    @app.get("/api/cases")
    def list_cases(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        cases = db.scalars(select(CaseRecord).where(CaseRecord.user_id == user.id).order_by(CaseRecord.created_at.desc())).all()
        return [_case_payload(case) for case in cases]

    @app.post("/api/cases", status_code=201)
    def create_case(payload: CreateCaseInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        unlimited_generation = is_test_admin(user)
        first_case_free = not user.free_case_used
        case = CaseRecord(
            user_id=user.id,
            title=payload.title,
            case_stage=payload.case_stage,
            party_side=payload.party_side,
            access_status="free" if first_case_free or unlimited_generation else "locked",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            data={"conversation": []},
        )
        user.free_case_used = True
        db.add(case)
        db.commit()
        db.refresh(case)
        return _case_payload(case)

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        case = _owned_case(db, case_id, user)
        result = _case_payload(case)
        result["evidence"] = [_evidence_payload(item) for item in case.evidence]
        result["artifacts"] = [
            {"id": artifact.id, "filename": artifact.filename, "kind": artifact.kind, "created_at": artifact.created_at}
            for artifact in case.artifacts
        ]
        return result

    @app.patch("/api/cases/{case_id}")
    def update_case(payload: UpdateCaseInput, case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        case = _owned_case(db, case_id, user)
        if payload.title is not None:
            case.title = payload.title
        if payload.data is not None:
            case.data = payload.data
        case.status = "ready_to_generate"
        db.commit()
        db.refresh(case)
        return _case_payload(case)

    @app.post("/api/cases/{case_id}/chat")
    async def chat(payload: ChatInput, case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        case = _owned_case(db, case_id, user)
        data = dict(case.data or {})
        conversation = list(data.get("conversation") or [])
        conversation.append({"role": "user", "content": payload.message})
        if payload.consent_cloud_processing:
            extracted = await OpenAICompatibleExtractor().extract(payload.message, data)
            data = deep_merge(data, extracted)
        assessment = assess_readiness({"case_stage": case.case_stage, "party_side": case.party_side, "data": data})
        if assessment.missing_fields:
            reply = f"已记录。下一项请补充：{assessment.missing_fields[0]}。你也可以跳过，系统仍会以待填项生成正式稿。"
        else:
            reply = "信息已经较完整。请在结构化确认页复核，也可以直接生成正式稿。"
        conversation.append({"role": "assistant", "content": reply})
        data["conversation"] = conversation
        case.data = data
        case.status = "pending_confirmation"
        db.commit()
        return {"reply": reply, "data": data, "readiness": assessment.readiness, "missing_fields": assessment.missing_fields}

    @app.get("/api/cases/{case_id}/evidence")
    def list_evidence(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        case = _owned_case(db, case_id, user)
        return [_evidence_payload(item) for item in case.evidence]

    @app.post("/api/cases/{case_id}/evidence", status_code=201)
    async def upload_evidence(
        case_id: str,
        file: UploadFile = File(...),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        case = _owned_case(db, case_id, user)
        if len(case.evidence) >= settings.max_case_files:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "每个案件最多上传20个文件")
        current_bytes = sum(item.size_bytes for item in case.evidence)
        saved = await save_upload(case.id, file)
        if current_bytes + saved["size_bytes"] > settings.max_case_bytes:
            delete_if_managed(saved["stored_path"])
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "每个案件材料总量不能超过300MB")
        item = EvidenceRecord(case_id=case.id, name=Path(saved["original_name"]).stem, **saved)
        db.add(item)
        case.status = "materials_processing"
        db.commit()
        db.refresh(item)
        return _evidence_payload(item)

    @app.patch("/api/cases/{case_id}/evidence/{evidence_id}")
    def update_evidence(
        payload: UpdateEvidenceInput,
        case_id: str,
        evidence_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        case = _owned_case(db, case_id, user)
        item = db.scalar(select(EvidenceRecord).where(EvidenceRecord.id == evidence_id, EvidenceRecord.case_id == case.id))
        if not item:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "证据不存在")
        for field in ("name", "purpose"):
            value = getattr(payload, field)
            if value is not None:
                setattr(item, field, value)
        db.commit()
        return _evidence_payload(item)

    @app.delete("/api/cases/{case_id}/evidence/{evidence_id}", status_code=204)
    def delete_evidence(
        case_id: str,
        evidence_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        case = _owned_case(db, case_id, user)
        item = db.scalar(select(EvidenceRecord).where(EvidenceRecord.id == evidence_id, EvidenceRecord.case_id == case.id))
        if not item:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "证据不存在")
        delete_if_managed(item.stored_path)
        db.delete(item)
        db.commit()

    @app.post("/api/cases/{case_id}/redeem")
    def redeem(payload: RedeemInput, case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        case = _owned_case(db, case_id, user)
        record = db.scalar(select(RedemptionCode).where(RedemptionCode.code_hash == _hash_code(payload.code), RedemptionCode.active.is_(True)))
        now = datetime.now(timezone.utc)
        if not record or record.redeemed_case_id or (record.expires_at and _aware(record.expires_at) <= now):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "兑换码无效、已使用或已过期")
        record.redeemed_case_id = case.id
        case.access_status = "redeemed"
        case.redeemed_at = now
        case.expires_at = min(_aware(case.expires_at), now + timedelta(days=30))
        db.commit()
        return _case_payload(case)

    @app.post("/api/cases/{case_id}/generate")
    def generate(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        case = _owned_case(db, case_id, user)
        unlimited_generation = is_test_admin(user)
        if case.access_status == "locked" and not unlimited_generation:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "请先使用兑换码解锁该案件")
        if case.generation_count >= 3 and not unlimited_generation:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "本案件最多成功生成3次")
        case.status = "generating"
        if settings.app_env == "production":
            running = db.scalar(
                select(GenerationJob).where(
                    GenerationJob.case_id == case.id,
                    GenerationJob.status.in_(["queued", "running"]),
                )
            )
            if running:
                raise HTTPException(status.HTTP_409_CONFLICT, "已有生成任务正在处理中")
            job = GenerationJob(case_id=case.id)
            db.add(job)
            db.commit()
            db.refresh(job)
            from app.tasks import celery_app

            celery_app.send_task("app.tasks.generate_documents", args=[job.id])
            return JSONResponse(status_code=202, content={"job_id": job.id, "status": "queued"})
        db.commit()
        try:
            response = run_generation(db, case)
            db.commit()
            return response
        except Exception as exc:
            db.rollback()
            case = db.get(CaseRecord, case_id)
            if case:
                case.status = "ready_to_generate"
            db.commit()
            detail = "生成失败，请检查材料后重试"
            if settings.app_env in {"development", "test"}:
                detail = f"{detail}：{exc}"
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from exc

    @app.get("/api/cases/{case_id}/generation-jobs/{job_id}")
    def generation_job(
        case_id: str,
        job_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        case = _owned_case(db, case_id, user)
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.case_id == case.id))
        if not job:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "生成任务不存在")
        return {"id": job.id, "status": job.status, "error": job.error, "result": job.result}

    @app.get("/api/cases/{case_id}/artifacts/{artifact_id}")
    def download_artifact(
        case_id: str,
        artifact_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        case = _owned_case(db, case_id, user)
        artifact = db.scalar(select(ArtifactRecord).where(ArtifactRecord.id == artifact_id, ArtifactRecord.case_id == case.id))
        if not artifact:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
        if artifact.stored_path.startswith("s3://"):
            return RedirectResponse(presigned_download(artifact.stored_path, artifact.filename), status_code=307)
        if not Path(artifact.stored_path).exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
        return FileResponse(artifact.stored_path, filename=artifact.filename, media_type="application/octet-stream")

    @app.post("/api/calculations/preview")
    def preview_calculation(payload: ClaimCalculationInput, _: User = Depends(get_current_user)):
        result = calculate_claim(payload.kind, payload.inputs)
        return {"amount": float(result.amount) if result.amount is not None else None, "formula": result.formula, "display": result.display}

    @app.post("/api/cases/{case_id}/legal-search")
    async def legal_search(
        payload: LegalSearchInput,
        case_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        case = _owned_case(db, case_id, user)
        snapshot = await YuandianLegalResearchProvider().search(payload.query)
        data = dict(case.data or {})
        snapshots = list(data.get("legal_snapshots") or [])
        snapshots.append(snapshot.as_dict())
        data["legal_snapshots"] = snapshots[-20:]
        if snapshot.verified:
            basis = snapshot.content.get("legal_basis")
            if isinstance(basis, list) and all(isinstance(item, dict) for item in basis):
                data["legal_basis"] = [dict(item, verified=True) for item in basis]
        case.data = data
        db.commit()
        return {"snapshot": snapshot.as_dict(), "readiness": _case_payload(case)["readiness"]}

    @app.get("/api/notifications")
    def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())).all()
        return [{"id": row.id, "case_id": row.case_id, "title": row.title, "message": row.message, "read": row.read} for row in rows]

    @app.post("/api/admin/redemption-codes", status_code=201)
    def create_redemption_code(
        note: str = "",
        x_admin_key: str | None = Header(default=None),
        db: Session = Depends(get_db),
    ):
        if not secrets.compare_digest(x_admin_key or "", settings.admin_key):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问")
        code = "LD-" + secrets.token_hex(6).upper()
        db.add(RedemptionCode(code_hash=_hash_code(code), note=note))
        db.commit()
        return {"code": code, "note": note}

    return app


app = create_app()
