from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery import Celery
from sqlalchemy import delete, select, update

from app.config import get_settings
from app.database import SessionLocal
from app.models import CaseRecord, GenerationJob, Notification, RedemptionCode
from app.services.generation import run_generation
from app.services.storage import delete_if_managed


settings = get_settings()
celery_app = Celery("labor_docs", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.beat_schedule = {
    "expiry-reminders-daily": {"task": "app.tasks.create_expiry_reminders", "schedule": 24 * 60 * 60},
    "expire-cases-hourly": {"task": "app.tasks.expire_cases", "schedule": 60 * 60},
}


@celery_app.task(name="app.tasks.generate_documents")
def generate_documents(job_id: str) -> dict:
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if not job:
            return {"status": "missing"}
        case = db.get(CaseRecord, job.case_id)
        if not case:
            job.status = "failed"
            job.error = "案件不存在"
            db.commit()
            return {"status": "failed"}
        job.status = "running"
        db.commit()
        try:
            result = run_generation(db, case)
            job = db.get(GenerationJob, job_id)
            job.status = "completed"
            job.result = result
            db.commit()
            return result
        except Exception:
            db.rollback()
            job = db.get(GenerationJob, job_id)
            case = db.get(CaseRecord, job.case_id) if job else None
            if job:
                job.status = "failed"
                job.error = "文书生成失败"
            if case:
                case.status = "ready_to_generate"
            db.commit()
            return {"status": "failed", "error": "文书生成失败"}


@celery_app.task
def create_expiry_reminders() -> int:
    today = datetime.now(timezone.utc)
    created = 0
    with SessionLocal() as db:
        cases = db.scalars(select(CaseRecord).where(CaseRecord.expires_at > today)).all()
        for case in cases:
            days = (_aware(case.expires_at) - today).days
            if days not in {1, 3, 7}:
                continue
            title = f"案件将在{days}天后到期"
            exists = db.scalar(
                select(Notification).where(
                    Notification.user_id == case.user_id,
                    Notification.case_id == case.id,
                    Notification.title == title,
                )
            )
            if not exists:
                db.add(Notification(user_id=case.user_id, case_id=case.id, title=title, message="请及时下载文书和原始材料。"))
                created += 1
        db.commit()
    return created


@celery_app.task
def expire_cases() -> int:
    now = datetime.now(timezone.utc)
    expired = 0
    with SessionLocal() as db:
        cases = db.scalars(select(CaseRecord).where(CaseRecord.expires_at <= now)).all()
        for case in cases:
            for evidence in list(case.evidence):
                delete_if_managed(evidence.stored_path)
                db.delete(evidence)
            for artifact in list(case.artifacts):
                delete_if_managed(artifact.stored_path)
                db.delete(artifact)
            db.execute(delete(GenerationJob).where(GenerationJob.case_id == case.id))
            db.execute(delete(Notification).where(Notification.case_id == case.id))
            db.execute(update(RedemptionCode).where(RedemptionCode.redeemed_case_id == case.id).values(redeemed_case_id=None))
            db.delete(case)
            expired += 1
        db.commit()
    return expired


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
