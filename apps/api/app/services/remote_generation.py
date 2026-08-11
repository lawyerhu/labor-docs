from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.documents import build_case_package
from app.services.generation import _cleanup_generated_case
from app.services.storage import delete_if_managed, materialize_for_processing, persist_artifact


class RemoteGenerationError(RuntimeError):
    pass


async def fetch_worker_generation_input(case_id: str) -> dict[str, Any]:
    settings = get_settings()
    worker_url = (settings.worker_internal_url or "").rstrip("/")
    if not worker_url:
        raise RemoteGenerationError("Worker内部地址未配置")

    headers = {"Accept": "application/json"}
    if settings.worker_internal_token:
        headers["X-Internal-Token"] = settings.worker_internal_token
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.get(
                f"{worker_url}/api/internal/cases/{quote(case_id, safe='')}/generation-input",
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise RemoteGenerationError("Worker内部接口不可达") from exc
    if response.status_code != 200:
        raise RemoteGenerationError(f"Worker内部接口返回 {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RemoteGenerationError("Worker内部接口返回格式错误") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("case"), dict):
        raise RemoteGenerationError("Worker案件数据版本不受支持")
    return payload


def _s3_stored_path(object_key: str) -> str:
    settings = get_settings()
    if settings.storage_backend != "s3" or not settings.s3_bucket:
        raise RemoteGenerationError("远程生成服务必须配置 S3 兼容对象存储")
    if not object_key or object_key.startswith("/") or "\x00" in object_key or ".." in Path(object_key).parts:
        raise RemoteGenerationError("证据对象键无效")
    return f"s3://{settings.s3_bucket}/{object_key}"


def _s3_object_key(stored_path: str) -> str:
    settings = get_settings()
    prefix = f"s3://{settings.s3_bucket}/" if settings.s3_bucket else ""
    if not prefix or not stored_path.startswith(prefix):
        raise RemoteGenerationError("生成文件未保存到配置的对象存储")
    return stored_path[len(prefix) :]


def run_remote_generation(worker_payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    case = worker_payload.get("case")
    if not isinstance(case, dict):
        raise RemoteGenerationError("缺少案件数据")
    case_id = str(case.get("id") or "")
    case_stage = case.get("case_stage")
    party_side = case.get("party_side")
    if not case_id or case_stage not in {"arbitration", "litigation"} or party_side not in {"worker", "employer"}:
        raise RemoteGenerationError("案件阶段或主体类型无效")

    evidence_items: list[dict[str, Any]] = []
    processing_paths: list[Path] = []
    stage = "validate"
    try:
        for item in worker_payload.get("evidence") or []:
            if not isinstance(item, dict) or item.get("status") != "ready":
                continue
            evidence_id = str(item.get("id") or "")
            if not evidence_id:
                raise RemoteGenerationError("证据编号无效")
            stage = f"download evidence {evidence_id}"
            stored_path = _s3_stored_path(str(item.get("object_key") or ""))
            processing_path = materialize_for_processing(stored_path, case_id, evidence_id)
            processing_paths.append(processing_path)
            evidence_items.append(
                {
                    "id": evidence_id,
                    "original_name": item.get("original_name") or "材料",
                    "name": item.get("name") or item.get("original_name") or "材料",
                    "source": item.get("source") or "",
                    "purpose": item.get("purpose") or "",
                    "stored_path": str(processing_path),
                }
            )
        stage = "build package"
        result = build_case_package(
            case_id=case_id,
            payload={"case_stage": case_stage, "party_side": party_side, "data": case.get("data") or {}},
            evidence_items=evidence_items,
            output_dir=settings.storage_path / "generated",
        )
        artifacts = []
        for generated in result.artifacts:
            stage = f"upload artifact {generated.kind}"
            stored_path = persist_artifact(case_id, generated.path)
            artifacts.append(
                {
                    "kind": generated.kind,
                    "filename": generated.filename,
                    "object_key": _s3_object_key(stored_path),
                }
            )
    except RemoteGenerationError:
        _cleanup_generated_case(settings, case_id)
        raise
    except Exception as exc:
        _cleanup_generated_case(settings, case_id)
        raise RemoteGenerationError(f"{stage} failed: {type(exc).__name__}") from exc
    finally:
        for processing_path in processing_paths:
            delete_if_managed(str(processing_path))

    return {
        "readiness": result.readiness,
        "missing_fields": result.missing_fields,
        "generation_count": int(case.get("generation_count") or 0) + 1,
        "artifacts": artifacts,
    }
