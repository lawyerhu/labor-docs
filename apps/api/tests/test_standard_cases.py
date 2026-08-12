from collections import Counter
from pathlib import Path

from app.domain.readiness import assess_readiness
from app.services.documents import build_case_package
from docx import Document
from tests.fixtures.standard_cases import standard_cases


def test_release_v1_standard_case_catalog_has_expected_route_coverage():
    cases = standard_cases()

    assert len(cases) == 40
    assert Counter((case["case_stage"], case["party_side"]) for case in cases) == {
        ("arbitration", "worker"): 10,
        ("arbitration", "employer"): 10,
        ("litigation", "worker"): 10,
        ("litigation", "employer"): 10,
    }


def test_release_v1_standard_cases_are_generatable_and_preserve_expected_readiness():
    for case in standard_cases():
        assessment = assess_readiness(
            {
                "case_stage": case["case_stage"],
                "party_side": case["party_side"],
                "data": case["data"],
            }
        )

        assert assessment.can_generate is True, case["case_id"]
        assert assessment.readiness == case["expected_readiness"], case["case_id"]
        if case["variant"] == 9:
            assert assessment.unresolved_conflicts == ["工资支付日期存在两个虚构版本"]
        if case["variant"] == 10:
            assert "原告名称" not in assessment.missing_fields
            assert "申请人名称" not in assessment.missing_fields


def test_release_v1_standard_cases_generate_the_expected_artifact_set(tmp_path: Path):
    for case in standard_cases():
        result = build_case_package(
            case_id=case["case_id"],
            payload={
                "case_stage": case["case_stage"],
                "party_side": case["party_side"],
                "data": case["data"],
            },
            evidence_items=[],
            output_dir=tmp_path,
        )

        expected = (
            {"01-劳动人事争议仲裁申请书.docx", "02-证据目录.docx"}
            if case["case_stage"] == "arbitration"
            else {
                "01A-民事起诉状（要素式）.docx",
                "01B-民事起诉状（普通式）.docx",
                "02-证据目录.docx",
            }
        )
        assert {artifact.filename for artifact in result.artifacts} == expected
        assert all(artifact.path.exists() for artifact in result.artifacts)

        if case["variant"] == 9:
            documents = [
                Document(artifact.path)
                for artifact in result.artifacts
                if artifact.filename.endswith(".docx")
            ]
            text = "\n".join(paragraph.text for document in documents for paragraph in document.paragraphs)
            assert "[待核实：工资支付日期存在两个虚构版本]" in text
