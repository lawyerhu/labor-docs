from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.document_drafting import draft_case_documents
from app.services.documents import build_case_package
from app.services.generation import _cleanup_generated_case
from app.services.material_extraction import extract_material_text
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


async def _report_progress(job_id: str, stage: str, progress: int) -> None:
    settings = get_settings()
    worker_url = (settings.worker_internal_url or "").rstrip("/")
    if not worker_url:
        return
    headers = {"Content-Type": "application/json"}
    if settings.worker_internal_token:
        headers["X-Internal-Token"] = settings.worker_internal_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{worker_url}/api/internal/generation-jobs/{quote(job_id, safe='')}/progress",
                headers=headers,
                json={"stage": stage, "progress": progress},
            )
    except httpx.HTTPError:
        return


def _merge_known_values(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in patch.items():
        if value in (None, "", [], {}):
            continue
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_known_values(existing, value)
        elif existing in (None, "", [], {}):
            merged[key] = value
    return merged


async def run_remote_generation(worker_payload: dict[str, Any], job_id: str) -> dict[str, Any]:
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
    material_texts: dict[str, str] = {}
    processing_paths: list[Path] = []
    stage = "validate"
    try:
        await _report_progress(job_id, "正在读取案件与材料", 15)
        for item in worker_payload.get("evidence") or []:
            if not isinstance(item, dict) or item.get("status") == "processing":
                continue
            evidence_id = str(item.get("id") or "")
            if not evidence_id:
                raise RemoteGenerationError("证据编号无效")
            stage = f"download evidence {evidence_id}"
            stored_path = _s3_stored_path(str(item.get("object_key") or ""))
            processing_path = materialize_for_processing(stored_path, case_id, evidence_id)
            processing_paths.append(processing_path)
            material_texts[evidence_id] = extract_material_text(processing_path)
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
        await _report_progress(job_id, "材料文字识别完成", 38)
        stage = "draft documents"
        await _report_progress(job_id, "大模型正在分析全案并撰写文书", 50)
        draft = await draft_case_documents(case=case, evidence_items=evidence_items, material_texts=material_texts)
        update_map = {item["id"]: item for item in draft["evidence_updates"]}
        for item in evidence_items:
            if item["id"] in update_map:
                item.update(update_map[item["id"]])
        data = _merge_known_values(dict(case.get("data") or {}), draft["data_patch"])
        data["_ai_draft"] = {
            "claims": draft["claims"],
            "facts_and_reasons": draft["facts_and_reasons"],
            "missing_fields": draft["missing_fields"],
        }
        await _report_progress(job_id, "文书正文撰写完成", 76)
        stage = "build package"
        await _report_progress(job_id, "正在排版起诉状、目录和证据", 84)
        result = build_case_package(
            case_id=case_id,
            payload={"case_stage": case_stage, "party_side": party_side, "data": data},
            evidence_items=evidence_items,
            output_dir=settings.storage_path / "generated",
        )
        artifacts = []
        await _report_progress(job_id, "正在保存生成文件", 93)
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
        "evidence_updates": draft["evidence_updates"],
    }
