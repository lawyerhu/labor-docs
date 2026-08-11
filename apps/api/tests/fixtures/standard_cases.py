"""Deterministic, fictional cases used as the release-v1 regression baseline."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _party_data(side: str) -> dict[str, dict[str, str]]:
    worker = {
        "name": "张某（虚构）",
        "address": "苏州市工业园区（虚构）",
        "contact": "13800000000",
        "id_number": "320500000000000000（虚构）",
    }
    employer = {
        "name": "苏州示例科技有限公司（虚构）",
        "address": "苏州市姑苏区（虚构）",
        "contact": "051200000000",
        "credit_code": "91320500XXXXXXXXXX（虚构）",
    }
    return {
        "initiating": deepcopy(worker if side == "worker" else employer),
        "opposing": deepcopy(employer if side == "worker" else worker),
    }


def _claim(side: str, variant: int) -> dict[str, Any]:
    worker_claims = [
        {"kind": "wages", "title": "支付拖欠工资", "amount": 6800, "basis": "2025年2月至3月工资"},
        {
            "kind": "double_wage",
            "title": "支付未签书面劳动合同二倍工资",
            "inputs": {"monthly_wage": 4452, "months": 11},
            "basis": "4452元×11个月",
        },
        {
            "kind": "overtime",
            "title": "支付工作日延时加班工资",
            "inputs": {"monthly_wage": 4452, "hours": 12, "multiplier": 1.5},
            "basis": "月工资、加班小时和计付倍数已确认",
        },
        {
            "kind": "annual_leave",
            "title": "支付未休年休假工资",
            "inputs": {"items": [{"entitled_days": 3, "taken_days": 0, "monthly_wage": 4452}]},
            "basis": "3日未休年休假",
        },
        {
            "kind": "economic_compensation",
            "title": "支付经济补偿",
            "inputs": {"monthly_wage": 4452, "compensation_years": 2},
            "basis": "4452元×2年",
        },
        {
            "kind": "illegal_termination",
            "title": "支付违法解除劳动合同赔偿金",
            "inputs": {"monthly_wage": 4452, "compensation_years": 2},
            "basis": "4452元×2年×2",
        },
        {"kind": "commission", "title": "支付销售提成", "amount": 12500, "basis": "2025年度已确认提成表"},
        {"kind": "non_compete", "title": "支付竞业限制补偿", "amount": 9600, "basis": "竞业限制期间补偿"},
    ]
    employer_claims = [
        {"kind": "reimbursement", "title": "返还公司垫付款", "amount": 3600, "basis": "差旅垫付款明细"},
        {"kind": "training_penalty", "title": "支付培训服务期违约金", "amount": 18000, "basis": "培训费用及剩余服务期"},
        {"kind": "non_compete_penalty", "title": "承担竞业限制违约责任", "amount": 30000, "basis": "竞业限制协议约定"},
        {"kind": "confidentiality_penalty", "title": "承担保密违约责任", "amount": 20000, "basis": "保密协议约定"},
        {"kind": "wages", "title": "确认已足额支付工资", "amount": 0, "basis": "工资支付记录"},
        {"kind": "economic_compensation", "title": "确认经济补偿计算金额", "amount": 8904, "basis": "4452元×2年"},
        {"kind": "illegal_termination", "title": "确认解除责任不成立", "amount": 0, "basis": "解除通知和考核记录"},
        {"kind": "overtime", "title": "确认不存在未支付加班费", "amount": 0, "basis": "考勤和工资记录"},
    ]
    return deepcopy((worker_claims if side == "worker" else employer_claims)[(variant - 1) % 8])


def _case(stage: str, side: str, variant: int) -> dict[str, Any]:
    data: dict[str, Any] = {
        "parties": _party_data(side),
        "employment_facts": {
            "start_date": "2023-01-01",
            "position": "产品专员",
            "summary": "双方建立劳动关系，工资、工作安排及解除经过均为虚构测试事实。",
        },
        "arbitration": {
            "committee": "苏州市示例劳动人事争议仲裁委员会",
            "award_number": f"苏劳人仲案字（2026）第{variant:03d}号",
            "result": "仲裁裁决结果为虚构测试内容。",
            "service_date": "2026-08-07",
        },
        "claims": [_claim(side, variant)],
        "court": "苏州市姑苏区人民法院",
        "legal_basis": [{"citation": "测试法律依据，不作为真实法律意见", "verified": True}],
    }
    if stage == "arbitration":
        data["arbitration"].pop("award_number")
        data["arbitration"].pop("result")
        data["arbitration"].pop("service_date")
        data.pop("court")
    if variant == 9:
        data["unresolved_conflicts"] = ["工资支付日期存在两个虚构版本"]
    if variant == 10:
        return {
            "case_id": f"std-{stage}-{side}-{variant}",
            "case_stage": stage,
            "party_side": side,
            "variant": variant,
            "expected_readiness": "formal_with_placeholders",
            "data": {},
        }
    return {
        "case_id": f"std-{stage}-{side}-{variant}",
        "case_stage": stage,
        "party_side": side,
        "variant": variant,
        "expected_readiness": "formal_with_placeholders" if variant == 9 else "formal_complete",
        "data": data,
    }


def standard_cases() -> list[dict[str, Any]]:
    """Return 40 cases: 10 for each stage/party-side route."""

    return [
        _case(stage, side, variant)
        for stage in ("arbitration", "litigation")
        for side in ("worker", "employer")
        for variant in range(1, 11)
    ]
