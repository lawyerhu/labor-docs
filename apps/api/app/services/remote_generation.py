from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.document_drafting import draft_case_documents
from app.services.documents import build_case_package
from app.services.generation import _cleanup_generated_case
from app.services.legal_research import LegalSnapshot, YuandianLegalResearchProvider
from app.services.material_extraction import extract_material_with_vision
from app.services.storage import delete_if_managed, materialize_for_processing, persist_artifact


logger = logging.getLogger(__name__)


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


async def _cache_extracted_text(case_id: str, evidence_id: str, analysis: dict[str, Any], text: str) -> None:
    settings = get_settings()
    worker_url = (settings.worker_internal_url or "").rstrip("/")
    if not worker_url:
        return
    headers = {"Content-Type": "application/json"}
    if settings.worker_internal_token:
        headers["X-Internal-Token"] = settings.worker_internal_token
    payload = {**analysis, "extracted_text": text, "extraction_version": 2}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{worker_url}/api/internal/cases/{quote(case_id, safe='')}/evidence/{quote(evidence_id, safe='')}/analysis",
                headers=headers,
                json={"analysis": payload},
            )
    except httpx.HTTPError:
        return


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


async def report_generation_result(
    job_id: str,
    case_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    settings = get_settings()
    worker_url = (settings.worker_internal_url or "").rstrip("/")
    if not worker_url:
        raise RemoteGenerationError("Worker internal URL is not configured")
    headers = {"Content-Type": "application/json"}
    if settings.worker_internal_token:
        headers["X-Internal-Token"] = settings.worker_internal_token
    payload: dict[str, Any] = {
        "case_id": case_id,
        "status": "completed" if result is not None else "failed",
    }
    if result is not None:
        payload["result"] = result
    else:
        payload["error"] = (error or "Document generation failed")[:1000]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.post(
                f"{worker_url}/api/internal/generation-jobs/{quote(job_id, safe='')}/result",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise RemoteGenerationError("Unable to report generation result to Worker") from exc
    if response.status_code != 200:
        raise RemoteGenerationError(f"Worker generation result endpoint returned {response.status_code}")


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


def _generation_research_terms(case: dict[str, Any]) -> tuple[str, str | None]:
    data = case.get("data") if isinstance(case.get("data"), dict) else {}
    claims = data.get("claims") if isinstance(data.get("claims"), list) else []
    claim_terms = []
    for claim in claims[:8]:
        if not isinstance(claim, dict):
            continue
        value = claim.get("title") or claim.get("kind")
        if value:
            claim_terms.append(str(value)[:120])
    stage = "仲裁后起诉" if case.get("case_stage") == "litigation" else "劳动仲裁"
    side = "用人单位" if case.get("party_side") == "employer" else "劳动者"
    query = f"劳动争议 {stage} {side} {'；'.join(claim_terms) or '请求权基础、举证责任及处理规则'}"

    parties = data.get("parties") if isinstance(data.get("parties"), dict) else {}
    company_name = None
    for role in ("initiating", "opposing"):
        party = parties.get(role) if isinstance(parties.get(role), dict) else {}
        name = str(party.get("name") or "").strip()
        if name and (party.get("type") == "company" or party.get("credit_code") or name.endswith(("公司", "事务所", "中心"))):
            company_name = name[:100]
            break
    return query[:500], company_name


async def research_for_generation(
    case: dict[str, Any],
    material_texts: dict[str, str],
    *,
    provider: Any | None = None,
) -> dict[str, Any]:
    del material_texts  # 法律检索不发送证据正文或个人信息。
    search = provider or YuandianLegalResearchProvider()
    query, company_name = _generation_research_terms(case)
    tasks = [
        search.search_law(query),
        search.search_cases(f"{query} 类案裁判规则 举证责任"),
    ]
    if company_name:
        tasks.append(search.search_company(company_name))
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=45)
    except asyncio.TimeoutError:
        # YuanDian is advisory. A slow MCP must not prevent document drafting;
        # the draft will carry an explicit unverified marker instead.
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        results = [
            LegalSnapshot(query, False, now, "yuandian:timeout", {"error": "timeout"}),
            LegalSnapshot(f"{query} 绫绘瑁佸垽瑙勫垯 涓捐瘉璐ｄ换", False, now, "yuandian:timeout", {"error": "timeout"}),
        ]
        if company_name:
            results.append(LegalSnapshot(company_name, False, now, "yuandian:timeout", {"error": "timeout"}))
    return {
        "law": results[0].as_dict(),
        "cases": results[1].as_dict(),
        "company": results[2].as_dict() if company_name else None,
    }


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
        source_items = [item for item in worker_payload.get("evidence") or [] if isinstance(item, dict)]
        total_items = max(len(source_items), 1)
        loop = asyncio.get_running_loop()
        for item_index, item in enumerate(source_items):
            if not isinstance(item, dict) or item.get("status") == "processing":
                continue
            evidence_id = str(item.get("id") or "")
            if not evidence_id:
                raise RemoteGenerationError("证据编号无效")
            stage = f"download evidence {evidence_id}"
            item_start = 15 + (item_index * 23 // total_items)
            item_end = 15 + ((item_index + 1) * 23 // total_items)
            await _report_progress(job_id, f"正在读取第 {item_index + 1}/{total_items} 份材料", item_start)
            started = time.perf_counter()
            stored_path = _s3_stored_path(str(item.get("object_key") or ""))
            processing_path = await asyncio.to_thread(materialize_for_processing, stored_path, case_id, evidence_id)
            processing_paths.append(processing_path)
            logger.info(
                "[GENERATION-PERF] job=%s evidence=%s materialize_seconds=%.2f",
                job_id,
                evidence_id,
                time.perf_counter() - started,
            )
            analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
            cached_text = analysis.get("extracted_text") if isinstance(analysis, dict) else None
            if (
                isinstance(cached_text, str)
                and cached_text.strip()
                and analysis.get("extraction_version") == 2
            ):
                material_texts[evidence_id] = cached_text
            else:
                stage = f"extract evidence {evidence_id}"
                progress_updates = []
                progress_done = asyncio.Event()

                async def flush_progress() -> None:
                    flushed = 0
                    while not progress_done.is_set() or flushed < len(progress_updates):
                        while flushed < len(progress_updates):
                            await asyncio.wrap_future(progress_updates[flushed])
                            flushed += 1
                        await asyncio.sleep(0.1)

                def report_page(completed: int, total: int) -> None:
                    fraction = completed / max(total, 1)
                    progress = min(item_end, item_start + round((item_end - item_start) * 0.75 * fraction))
                    progress_updates.append(
                        asyncio.run_coroutine_threadsafe(
                            _report_progress(
                                job_id,
                                f"正在识别第 {item_index + 1}/{total_items} 份材料（第 {completed}/{total} 页）",
                                progress,
                            ),
                            loop,
                        )
                    )

                def report_vision(completed: int, total: int) -> None:
                    fraction = completed / max(total, 1)
                    progress = min(item_end, item_start + round((item_end - item_start) * (0.75 + 0.25 * fraction)))
                    progress_updates.append(
                        asyncio.run_coroutine_threadsafe(
                            _report_progress(
                                job_id,
                                f"视觉模型正在复核第 {item_index + 1}/{total_items} 份材料（第 {completed}/{total} 个疑难页）",
                                progress,
                            ),
                            loop,
                        )
                    )

                started = time.perf_counter()
                progress_task = asyncio.create_task(flush_progress())
                try:
                    extraction = await extract_material_with_vision(
                        processing_path,
                        progress_callback=report_page,
                        vision_progress_callback=report_vision,
                    )
                finally:
                    progress_done.set()
                    try:
                        await asyncio.wait_for(progress_task, timeout=5)
                    except asyncio.TimeoutError:
                        # Progress callbacks are best-effort.  Never make a
                        # completed OCR run wait for a slow Worker callback.
                        progress_task.cancel()
                        await asyncio.gather(progress_task, return_exceptions=True)
                material_texts[evidence_id] = extraction.text
                logger.info(
                    "[GENERATION-PERF] job=%s evidence=%s extraction_seconds=%.2f chars=%s vision_pages=%s",
                    job_id,
                    evidence_id,
                    time.perf_counter() - started,
                    len(material_texts[evidence_id]),
                    list(extraction.vision_reviewed_pages),
                )
                analysis["extraction_version"] = 2
                analysis["vision_reviewed_pages"] = list(extraction.vision_reviewed_pages)
                await _cache_extracted_text(case_id, evidence_id, analysis, material_texts[evidence_id])
            evidence_items.append(
                {
                    "id": evidence_id,
                    "original_name": item.get("original_name") or "材料",
                    "name": item.get("name") or item.get("original_name") or "材料",
                    "purpose": item.get("purpose") or "",
                    "stored_path": str(processing_path),
                    "analysis": analysis,
                }
            )
            await _report_progress(job_id, f"第 {item_index + 1}/{total_items} 份材料读取完成", item_end)
        await _report_progress(job_id, "材料文字识别完成", 38)
        stage = "research law and cases"
        await _report_progress(job_id, "正在通过元典核验现行法条与相关案例", 42)
        legal_research = await research_for_generation(case, material_texts)
        case = {
            **case,
            "data": {**dict(case.get("data") or {}), "legal_research": legal_research},
        }
        stage = "draft documents"
        await _report_progress(job_id, "大模型正在分析全案并撰写文书", 50)
        draft = await draft_case_documents(case=case, evidence_items=evidence_items, material_texts=material_texts)
        update_map = {item["id"]: item for item in draft["evidence_updates"]}
        for item in evidence_items:
            if item["id"] in update_map:
                item.update(update_map[item["id"]])
        data = _merge_known_values(dict(case.get("data") or {}), draft["data_patch"])
        data["legal_research"] = legal_research
        data["legal_basis"] = draft["legal_basis"]
        data["_ai_draft"] = {
            "claims": draft["claims"],
            "facts_and_reasons": draft["facts_and_reasons"],
            "missing_fields": draft["missing_fields"],
        }
        await _report_progress(job_id, "文书正文撰写完成", 76)
        stage = "build package"
        await _report_progress(job_id, "正在排版起诉状、目录和证据", 84)
        result = await asyncio.to_thread(
            build_case_package,
            case_id=case_id,
            payload={"case_stage": case_stage, "party_side": party_side, "data": data},
            evidence_items=evidence_items,
            output_dir=settings.storage_path / "generated",
        )
        artifacts = []
        await _report_progress(job_id, "正在保存生成文件", 93)
        for generated in result.artifacts:
            stage = f"upload artifact {generated.kind}"
            stored_path = await asyncio.to_thread(persist_artifact, case_id, generated.path)
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
        detail = str(exc).strip() or type(exc).__name__
        raise RemoteGenerationError(f"{stage} failed: {detail}") from exc
    finally:
        for processing_path in processing_paths:
            delete_if_managed(str(processing_path))

    evidence_updates = []
    for update in draft["evidence_updates"]:
        evidence_id = str(update.get("id") or "")
        original = next((item for item in evidence_items if item["id"] == evidence_id), None)
        analysis = dict(original.get("analysis") or {}) if original else {}
        if evidence_id in material_texts:
            analysis["extracted_text"] = material_texts[evidence_id]
            analysis["extraction_version"] = 2
        evidence_updates.append({**update, "analysis": analysis})

    return {
        "readiness": result.readiness,
        "missing_fields": result.missing_fields,
        "generation_count": int(case.get("generation_count") or 0) + 1,
        "artifacts": artifacts,
        "evidence_updates": evidence_updates,
    }
