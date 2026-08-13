from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Any, Awaitable, Callable

import pypdfium2 as pdfium
import pytesseract
from docx import Document
from PIL import Image
from pypdf import PdfReader

from app.config import get_settings
from app.services.openai_compat import complete_vision_json


logger = logging.getLogger(__name__)

MAX_EXTRACTED_CHARS = 120_000
DEFAULT_OCR_RENDER_SCALE = 2.2
FAST_OCR_RENDER_PIXELS = 800_000
MAX_OCR_RENDER_PIXELS = 2_000_000
MIN_USEFUL_OCR_CHARS = 40
HIGH_RISK_THRESHOLD = 0.55
# Visual review is an enhancement step; keep it bounded so difficult scans
# cannot hold the entire evidence job open indefinitely.
MAX_VISION_REVIEW_PAGES = 3
VISION_PROVIDER_TIMEOUT_SECONDS = 25
VISION_PAGE_TIMEOUT_SECONDS = 45
OCR_PAGE_TIMEOUT_SECONDS = 30
EXTRACTION_TIMEOUT_SECONDS = 5 * 60
TESSERACT_CONFIG = "--oem 1 --psm 6 -c tessedit_do_invert=0"
ExtractionProgress = Callable[[int, int], None]
VisionProgress = Callable[[int, int], None]
VisionComplete = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ExtractedPage:
    number: int
    text: str
    risk_score: float
    used_ocr: bool
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


@dataclass(frozen=True)
class MaterialExtractionResult:
    text: str
    page_count: int
    vision_reviewed_pages: tuple[int, ...]


def _ocr_image(image: Image.Image) -> str:
    prepared = image.convert("L")
    try:
        try:
            return pytesseract.image_to_string(
                prepared,
                lang="chi_sim",
                config=TESSERACT_CONFIG,
                timeout=OCR_PAGE_TIMEOUT_SECONDS,
            ).strip()
        except Exception as error:
            # Tesseract can hang on malformed or unusually large scans. Keep
            # the page available for the visual fallback instead of blocking
            # the whole material job.
            logger.warning("[OCR-SKIPPED] reason=%s", error)
            return ""
    finally:
        if prepared is not image:
            prepared.close()


def _bounded_ocr_scale(width: float, height: float, max_pixels: int = MAX_OCR_RENDER_PIXELS) -> float:
    if width <= 0 or height <= 0:
        return DEFAULT_OCR_RENDER_SCALE
    return min(DEFAULT_OCR_RENDER_SCALE, sqrt(max_pixels / (width * height)))


def _ocr_resized(image: Image.Image, scale: float) -> str:
    if scale >= 1:
        return _ocr_image(image)
    resized = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    try:
        return _ocr_image(resized)
    finally:
        resized.close()


def _ocr_pdf_page(pdf_page: pdfium.PdfPage, max_pixels: int) -> str:
    width, height = pdf_page.get_size()
    rendered = pdf_page.render(scale=_bounded_ocr_scale(width, height, max_pixels)).to_pil()
    try:
        return _ocr_image(rendered)
    finally:
        rendered.close()


def _render_pdf_page(pdf_page: pdfium.PdfPage, max_pixels: int) -> Image.Image:
    width, height = pdf_page.get_size()
    return pdf_page.render(scale=_bounded_ocr_scale(width, height, max_pixels)).to_pil()


def _image_as_jpeg(image: Image.Image) -> bytes:
    scale = _bounded_ocr_scale(image.width, image.height, MAX_OCR_RENDER_PIXELS)
    source = image
    if scale < 1:
        source = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    converted = source.convert("RGB")
    try:
        output = io.BytesIO()
        converted.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()
    finally:
        if converted is not source:
            converted.close()
        if source is not image:
            source.close()


def assess_page_risk(text: str, *, used_ocr: bool) -> float:
    """Estimate whether extracted text needs review without another OCR pass."""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 1.0
    score = 0.25 if used_ocr else 0.0
    if len(compact) < 20:
        score += 0.5
    elif len(compact) < 80:
        score += 0.3
    elif len(compact) < 200:
        score += 0.1
    useful = sum(character.isalnum() or "\u4e00" <= character <= "\u9fff" for character in compact)
    if useful / max(len(compact), 1) < 0.55:
        score += 0.35
    if "�" in compact or re.search(r"([^\w\u4e00-\u9fff])\1{4,}", compact):
        score += 0.35
    return min(round(score, 2), 1.0)


def _adaptive_ocr_text(first_pass: str, retry: Callable[[], str]) -> str:
    if len(first_pass) >= MIN_USEFUL_OCR_CHARS:
        return first_pass
    retry_text = retry()
    return retry_text if len(retry_text) > len(first_pass) else first_pass


def _extract_pdf_pages(path: Path, progress_callback: ExtractionProgress | None = None) -> list[ExtractedPage]:
    reader = PdfReader(path)
    native_texts = [(page.extract_text() or "").strip() for page in reader.pages]
    pages: list[ExtractedPage] = []
    if native_texts:
        document = pdfium.PdfDocument(str(path))
        try:
            total = len(native_texts)
            for index, native_text in enumerate(native_texts):
                if progress_callback:
                    progress_callback(index + 1, total)
                text = native_text
                used_ocr = False
                image_bytes: bytes | None = None
                pdf_page: pdfium.PdfPage | None = None
                rendered: Image.Image | None = None
                native_risk = assess_page_risk(native_text, used_ocr=False)
                needs_ocr = len(native_text) < MIN_USEFUL_OCR_CHARS or native_risk >= HIGH_RISK_THRESHOLD
                try:
                    if needs_ocr:
                        pdf_page = document[index]
                        rendered = _render_pdf_page(pdf_page, FAST_OCR_RENDER_PIXELS)
                        first_pass = _ocr_image(rendered)
                        selected_image = rendered
                        if len(first_pass) < MIN_USEFUL_OCR_CHARS:
                            retry_image = _render_pdf_page(pdf_page, MAX_OCR_RENDER_PIXELS)
                            retry_text = _ocr_image(retry_image)
                            if len(retry_text) > len(first_pass):
                                rendered.close()
                                rendered = retry_image
                                selected_image = retry_image
                                first_pass = retry_text
                            else:
                                retry_image.close()
                        if first_pass and (
                            not native_text
                            or assess_page_risk(first_pass, used_ocr=True) < native_risk
                            or len(first_pass) > len(native_text)
                        ):
                            text = first_pass
                            used_ocr = True
                        risk_score = assess_page_risk(text, used_ocr=used_ocr)
                        if risk_score >= HIGH_RISK_THRESHOLD:
                            image_bytes = _image_as_jpeg(selected_image)
                    else:
                        risk_score = native_risk
                    pages.append(
                        ExtractedPage(
                            number=index + 1,
                            text=text,
                            risk_score=risk_score,
                            used_ocr=used_ocr,
                            image_bytes=image_bytes,
                            image_mime_type="image/jpeg" if image_bytes else None,
                        )
                    )
                finally:
                    if rendered is not None:
                        rendered.close()
                    if pdf_page is not None:
                        pdf_page.close()
        finally:
            document.close()
    return pages


def _extract_pdf(path: Path, progress_callback: ExtractionProgress | None = None) -> str:
    return _pages_text(_extract_pdf_pages(path, progress_callback))


def _extract_docx(path: Path) -> str:
    document = Document(path)
    parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            values = [cell.text.strip() for cell in row.cells]
            if any(values):
                parts.append(" | ".join(values))
    return "\n".join(parts)


def _convert_office(path: Path, target_dir: Path) -> Path:
    settings = get_settings()
    executable = settings.soffice_path or shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise RuntimeError("Office材料转换服务不可用")
    profile = target_dir / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            executable,
            "--headless",
            "--nologo",
            "--norestore",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(target_dir),
            str(path),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    converted = target_dir / f"{path.stem}.pdf"
    if result.returncode != 0 or not converted.exists():
        raise RuntimeError("Office材料转换失败")
    return converted


def _pages_text(pages: list[ExtractedPage]) -> str:
    return "\n\n".join(f"--- 第{page.number}页 ---\n{page.text}" for page in pages)


def extract_material_pages(path: Path, progress_callback: ExtractionProgress | None = None) -> list[ExtractedPage]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_pages(path, progress_callback)
    if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}:
        if progress_callback:
            progress_callback(1, 1)
        with Image.open(path) as image:
            width, height = image.size
            fast_scale = _bounded_ocr_scale(width, height, FAST_OCR_RENDER_PIXELS)
            if fast_scale < 1:
                fast = image.resize((max(1, int(width * fast_scale)), max(1, int(height * fast_scale))))
                retry_scale = _bounded_ocr_scale(width, height, MAX_OCR_RENDER_PIXELS)
                try:
                    first_pass = _ocr_image(fast)
                    retry_text = _ocr_resized(image, retry_scale) if len(first_pass) < MIN_USEFUL_OCR_CHARS else ""
                    text = retry_text if len(retry_text) > len(first_pass) else first_pass
                finally:
                    fast.close()
            else:
                text = _ocr_image(image)
            risk_score = assess_page_risk(text, used_ocr=True)
            return [
                ExtractedPage(
                    number=1,
                    text=text,
                    risk_score=risk_score,
                    used_ocr=True,
                    image_bytes=_image_as_jpeg(image) if risk_score >= HIGH_RISK_THRESHOLD else None,
                    image_mime_type="image/jpeg" if risk_score >= HIGH_RISK_THRESHOLD else None,
                )
            ]
    if suffix == ".docx":
        text = _extract_docx(path)
        return [ExtractedPage(number=1, text=text, risk_score=assess_page_risk(text, used_ocr=False), used_ocr=False)]
    if suffix in {".doc", ".xls", ".xlsx"}:
        with tempfile.TemporaryDirectory(prefix="material-") as temp:
            return _extract_pdf_pages(_convert_office(path, Path(temp)), progress_callback)
    raise RuntimeError(f"暂不支持读取 {suffix or '未知格式'} 材料")


async def extract_material_with_vision(
    path: Path,
    progress_callback: ExtractionProgress | None = None,
    vision_progress_callback: VisionProgress | None = None,
    vision_complete: VisionComplete = complete_vision_json,
) -> MaterialExtractionResult:
    try:
        pages = await asyncio.wait_for(
            asyncio.to_thread(extract_material_pages, path, progress_callback),
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as error:
        raise RuntimeError("material extraction timed out") from error
    candidates = sorted(
        (page for page in pages if page.risk_score >= HIGH_RISK_THRESHOLD and page.image_bytes),
        key=lambda page: (-page.risk_score, page.number),
    )[:MAX_VISION_REVIEW_PAGES]
    candidates = sorted(candidates, key=lambda page: page.number)
    reviewed: list[int] = []
    replacements: dict[int, str] = {}
    for completed, page in enumerate(candidates, start=1):
        if vision_progress_callback:
            vision_progress_callback(completed, len(candidates))
        try:
            parsed = await asyncio.wait_for(vision_complete(
                system="""你是中国劳动争议材料的页面视觉复核助手。结合页面图像与OCR文字纠错，只输出JSON。
corrected_text：按页面原有阅读顺序完整转写正文，保留姓名、日期、金额、案号、表格字段和签章文字；不得概括，不得补造看不清的内容。
uncertain_fragments：仍无法确认的片段数组；notes：可选的简短说明。""",
                user=f"这是第{page.number}页。OCR初稿如下，请以图像为准复核并与OCR结果合并：\n\n{page.text}",
                image_bytes=page.image_bytes or b"",
                mime_type=page.image_mime_type or "image/jpeg",
                timeout=VISION_PROVIDER_TIMEOUT_SECONDS,
                attempts=1,
            ), timeout=VISION_PAGE_TIMEOUT_SECONDS)
        except Exception as error:
            logger.warning("[VISION-REVIEW-SKIPPED] page=%s reason=%s", page.number, error)
            continue
        corrected = str(parsed.get("corrected_text") or "").strip()
        if corrected:
            uncertain = [str(value).strip() for value in parsed.get("uncertain_fragments") or [] if str(value).strip()]
            if uncertain:
                corrected += "\n[待核实：" + "；".join(uncertain[:10]) + "]"
            replacements[page.number] = corrected
            reviewed.append(page.number)

    merged_pages = [
        ExtractedPage(
            number=page.number,
            text=replacements.get(page.number, page.text),
            risk_score=page.risk_score,
            used_ocr=page.used_ocr,
        )
        for page in pages
    ]
    normalized = "\n".join(line.rstrip() for line in _pages_text(merged_pages).splitlines()).strip()
    if not normalized or not any(page.text.strip() for page in merged_pages):
        raise RuntimeError("材料中未识别到可读文字")
    return MaterialExtractionResult(
        text=normalized[:MAX_EXTRACTED_CHARS],
        page_count=len(merged_pages),
        vision_reviewed_pages=tuple(reviewed),
    )


def extract_material_text(path: Path, progress_callback: ExtractionProgress | None = None) -> str:
    text = _pages_text(extract_material_pages(path, progress_callback))
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        raise RuntimeError("材料中未识别到可读文字")
    return normalized[:MAX_EXTRACTED_CHARS]
