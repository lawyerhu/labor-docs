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
