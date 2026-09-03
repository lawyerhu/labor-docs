import asyncio
from pathlib import Path

import app.services.evidence_analysis as evidence_analysis
from app.services.material_extraction import MaterialExtractionResult


def test_model_timeout_keeps_extraction_and_returns_fallback(monkeypatch, tmp_path: Path):
    async def fake_extract(_path, **_kwargs):
        return MaterialExtractionResult(
            text="解除劳动合同协议\n甲方：某公司",
            page_count=1,
            vision_reviewed_pages=(),
        )

    async def failed_analysis(**_kwargs):
        raise TimeoutError("model timeout")

    monkeypatch.setattr(evidence_analysis, "extract_material_with_vision", fake_extract)
    monkeypatch.setattr(evidence_analysis, "analyze_evidence_text", failed_analysis)

    result = asyncio.run(
        evidence_analysis.analyze_material(
            case={"case_stage": "litigation", "party_side": "worker"},
            item={"original_name": "scan.pdf"},
            path=tmp_path / "scan.pdf",
        )
    )

    assert result["name"] == "解除劳动合同协议"
    assert result["analysis"]["extracted_text"].startswith("解除劳动合同协议")
    assert result["analysis"]["analysis_fallback"] is True
    assert result["analysis"]["page_count"] == 1
