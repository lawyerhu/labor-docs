from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadinessAssessment:
    readiness: str
    can_generate: bool
    missing_fields: list[str]
    unresolved_conflicts: list[str]
    unverified_law: list[str]
    evidence_gaps: list[str]


def _value(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def assess_readiness(payload: dict[str, Any]) -> ReadinessAssessment:
    stage = payload.get("case_stage")
    side = payload.get("party_side")
    data = payload.get("data") or {}
    missing: list[str] = []

    route_labels = {"case_stage": "案件阶段", "party_side": "申请人/原告一方"}
    for key, label in route_labels.items():
        if not payload.get(key):
            missing.append(label)

    # 姓名、证件号码、联系方式和详细住址不在网站收集，统一留在 Word 中由用户本地补写。
    required = {
        "employment_facts.start_date": "入职日期",
        "employment_facts.position": "工作岗位",
        "employment_facts.summary": "基本案情",
    }

    if stage == "arbitration":
        required["arbitration.committee"] = "劳动人事争议仲裁委员会"
    elif stage == "litigation":
        required.update(
            {
                "court": "管辖法院",
                "arbitration.committee": "仲裁委员会",
                "arbitration.award_number": "仲裁裁决书案号",
                "arbitration.result": "仲裁裁决结果",
                "arbitration.service_date": "仲裁裁决送达日期",
            }
        )

    for path, label in required.items():
        if _value(data, path) in (None, "", []):
            missing.append(label)

    claims = data.get("claims") or []
    if not claims:
        missing.append("仲裁请求" if stage == "arbitration" else "诉讼请求")
    for index, claim in enumerate(claims, start=1):
        if not claim.get("title"):
            missing.append(f"第{index}项请求内容")
        if claim.get("amount") in (None, "") and not claim.get("basis"):
            missing.append(f"第{index}项请求的金额、基数、期间或计算方式")

    conflicts = [str(item) for item in data.get("unresolved_conflicts", []) if item]
    legal_basis = data.get("legal_basis") or []
    unverified = [str(item.get("citation") or "法律依据") for item in legal_basis if not item.get("verified")]
    if not legal_basis:
        unverified.append("法律依据")
    evidence_gaps = [str(item) for item in data.get("evidence_gaps", []) if item]
    readiness = "formal_complete" if not missing and not conflicts and not unverified else "formal_with_placeholders"
    return ReadinessAssessment(readiness, bool(stage and side), missing, conflicts, unverified, evidence_gaps)
