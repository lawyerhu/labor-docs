import asyncio
from pathlib import Path

from pypdf import PdfWriter

from app.services import material_extraction


def test_ocr_render_scale_caps_large_scanned_pages():
    scale = material_extraction._bounded_ocr_scale(1239, 1754)
    fast = material_extraction._bounded_ocr_scale(1239, 1754, material_extraction.FAST_OCR_RENDER_PIXELS)

    assert scale < material_extraction.DEFAULT_OCR_RENDER_SCALE
    assert fast < scale
    assert 1239 * scale * 1754 * scale <= material_extraction.MAX_OCR_RENDER_PIXELS + 1
    assert 1239 * fast * 1754 * fast <= material_extraction.FAST_OCR_RENDER_PIXELS + 1


def test_pdf_extraction_reports_each_ocr_page(tmp_path: Path, monkeypatch):
    path = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=1239, height=1754)
    writer.add_blank_page(width=1239, height=1754)
    with path.open("wb") as output:
        writer.write(output)

    monkeypatch.setattr(material_extraction, "_ocr_image", lambda image: "识别出的正文" * 20)
    progress: list[tuple[int, int]] = []

    text = material_extraction._extract_pdf(path, lambda completed, total: progress.append((completed, total)))

    assert "识别出的正文" in text
    assert progress == [(1, 2), (2, 2)]


def test_adaptive_ocr_retries_only_when_first_pass_is_too_short():
    calls: list[str] = []

    enough = "x" * material_extraction.MIN_USEFUL_OCR_CHARS
    first = material_extraction._adaptive_ocr_text(enough, lambda: calls.append("retry") or "retry")
    second = material_extraction._adaptive_ocr_text("short", lambda: calls.append("retry") or "retry text that is longer")

    assert first == enough
    assert second == "retry text that is longer"
    assert calls == ["retry"]


def test_low_risk_page_does_not_call_vision_model(tmp_path: Path, monkeypatch):
    path = tmp_path / "material.pdf"
    path.write_bytes(b"placeholder")
    page = material_extraction.ExtractedPage(
        number=1,
        text="这是一页内容清晰且长度足够的劳动合同正文。" * 10,
        risk_score=0.1,
        used_ocr=False,
    )
    monkeypatch.setattr(material_extraction, "extract_material_pages", lambda *_args, **_kwargs: [page])
    calls: list[str] = []

    async def fake_vision(**_kwargs):
        calls.append("vision")
        return {"corrected_text": "不应调用"}

    result = asyncio.run(material_extraction.extract_material_with_vision(path, vision_complete=fake_vision))

    assert "劳动合同正文" in result.text
    assert result.vision_reviewed_pages == ()
    assert calls == []


def test_high_risk_page_uses_visual_correction(tmp_path: Path, monkeypatch):
    path = tmp_path / "scan.png"
    path.write_bytes(b"placeholder")
    page = material_extraction.ExtractedPage(
        number=1,
        text="仲栽金额1B00元",
        risk_score=0.9,
        used_ocr=True,
        image_bytes=b"image-bytes",
        image_mime_type="image/jpeg",
    )
    monkeypatch.setattr(material_extraction, "extract_material_pages", lambda *_args, **_kwargs: [page])

    async def fake_vision(**kwargs):
        assert kwargs["image_bytes"] == b"image-bytes"
        assert "仲栽金额1B00元" in kwargs["user"]
        return {"corrected_text": "仲裁金额1800元", "uncertain_fragments": []}

    result = asyncio.run(material_extraction.extract_material_with_vision(path, vision_complete=fake_vision))

    assert "仲裁金额1800元" in result.text
    assert "仲栽金额1B00元" not in result.text
    assert result.vision_reviewed_pages == (1,)


def test_visual_failure_keeps_ocr_text(tmp_path: Path, monkeypatch):
    path = tmp_path / "scan.png"
    path.write_bytes(b"placeholder")
    page = material_extraction.ExtractedPage(
        number=1,
        text="原始OCR文字",
        risk_score=0.9,
        used_ocr=True,
        image_bytes=b"image-bytes",
        image_mime_type="image/jpeg",
    )
    monkeypatch.setattr(material_extraction, "extract_material_pages", lambda *_args, **_kwargs: [page])

    async def failed_vision(**_kwargs):
        raise RuntimeError("两个视觉模型均不可用")

    result = asyncio.run(material_extraction.extract_material_with_vision(path, vision_complete=failed_vision))

    assert "原始OCR文字" in result.text
    assert result.vision_reviewed_pages == ()
