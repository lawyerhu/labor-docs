import shutil

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ArtifactRecord, CaseRecord
from app.services.documents import build_case_package
from app.services.storage import delete_if_managed, materialize_for_processing, persist_artifact


def _cleanup_generated_case(settings, case_id: str) -> None:
    root = (settings.storage_path / "generated").resolve()
    target = (root / case_id).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    shutil.rmtree(target, ignore_errors=True)


def run_generation(db: Session, case: CaseRecord) -> dict:
    settings = get_settings()
    evidence = []
    processing_paths = []
    for item in case.evidence:
        if item.status != "ready":
            continue
        processing_path = materialize_for_processing(item.stored_path, case.id, item.id)
        evidence.append(
            {
                "id": item.id,
                "original_name": item.original_name,
                "name": item.name,
                "purpose": item.purpose,
                "stored_path": str(processing_path),
            }
        )
        if item.stored_path.startswith("s3://"):
            processing_paths.append(processing_path)
    try:
        result = build_case_package(
            case_id=case.id,
            payload={"case_stage": case.case_stage, "party_side": case.party_side, "data": case.data or {}},
            evidence_items=evidence,
            output_dir=settings.storage_path / "generated",
        )
        for old in list(case.artifacts):
            db.delete(old)
        db.flush()
        artifacts: list[ArtifactRecord] = []
        for generated in result.artifacts:
            artifact = ArtifactRecord(
                case_id=case.id,
                filename=generated.filename,
                kind=generated.kind,
                stored_path=persist_artifact(case.id, generated.path),
            )
            db.add(artifact)
            artifacts.append(artifact)
        case.generation_count += 1
        case.status = "generated"
        db.flush()
        return {
            "readiness": result.readiness,
            "missing_fields": result.missing_fields,
            "generation_count": case.generation_count,
            "artifacts": [{"id": item.id, "filename": item.filename, "kind": item.kind} for item in artifacts],
        }
    except Exception:
        _cleanup_generated_case(settings, case.id)
        raise
    finally:
        for processing_path in processing_paths:
            delete_if_managed(str(processing_path))
