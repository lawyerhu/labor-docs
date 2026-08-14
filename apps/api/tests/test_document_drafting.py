import asyncio

import app.services.document_drafting as drafting


def _model_result() -> dict:
    return {
        "claims": ["请求判令被告不承担违约金。"],
        "facts_and_reasons": [
            "根据《中华人民共和国劳动争议调解仲裁法》第五十条的规定，原告在法定期限内起诉。"
        ],
        "data_patch": {},
        "evidence_items": [],
        "verified_law": [
            {"citation": "《中华人民共和国劳动争议调解仲裁法》第五十条"}
        ],
        "missing_fields": [],
    }


def test_draft_keeps_only_law_grounded_in_verified_yuandian_snapshot(monkeypatch):
    async def complete_json(**_kwargs):
        return _model_result()

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {
        "id": "case-1",
        "case_stage": "litigation",
        "party_side": "employer",
        "data": {
            "legal_research": {
                "law": {
                    "verified": True,
                    "source": "yuandian:mcp:law",
                    "retrieved_at": "2026-08-14T00:00:00+00:00",
                    "content": {
                        "text": "《中华人民共和国劳动争议调解仲裁法》第五十条 当事人对本法第四十七条规定以外的其他劳动争议案件的仲裁裁决不服的……"
                    },
                }
            }
        },
    }

    result = asyncio.run(drafting.draft_case_documents(case=case, evidence_items=[], material_texts={}))

    assert result["legal_basis"] == [
        {
            "citation": "《中华人民共和国劳动争议调解仲裁法》第五十条",
            "verified": True,
            "source": "yuandian:mcp:law",
            "retrieved_at": "2026-08-14T00:00:00+00:00",
        }
    ]
    assert "第五十条" in result["facts_and_reasons"][0]


def test_draft_removes_precise_article_when_yuandian_did_not_verify_it(monkeypatch):
    async def complete_json(**_kwargs):
        return _model_result()

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {
        "id": "case-1",
        "case_stage": "litigation",
        "party_side": "employer",
        "data": {
            "legal_research": {
                "law": {
                    "verified": False,
                    "source": "yuandian:mcp:law:unavailable",
                    "retrieved_at": "2026-08-14T00:00:00+00:00",
                    "content": {},
                }
            }
        },
    }

    result = asyncio.run(drafting.draft_case_documents(case=case, evidence_items=[], material_texts={}))

    assert result["legal_basis"] == []
    assert "第五十条" not in result["facts_and_reasons"][0]
    assert "[待核验法律依据]" in result["facts_and_reasons"][0]


def test_draft_removes_unbracketed_precise_article_when_not_verified(monkeypatch):
    async def complete_json(**_kwargs):
        result = _model_result()
        result["facts_and_reasons"] = [
            "根据中华人民共和国劳动争议调解仲裁法第五十条，原告在法定期限内起诉。"
        ]
        return result

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {
        "id": "case-1",
        "case_stage": "litigation",
        "party_side": "employer",
        "data": {"legal_research": {"law": {"verified": False, "content": {}}}},
    }

    result = asyncio.run(drafting.draft_case_documents(case=case, evidence_items=[], material_texts={}))

    assert "第五十条" not in result["facts_and_reasons"][0]
    assert "[待核验法律依据]" in result["facts_and_reasons"][0]


def test_draft_weaves_grounded_citations_inline_without_legal_basis_label(monkeypatch):
    async def complete_json(**_kwargs):
        return {
            "claims": ["请求判令被告支付二倍工资。"],
            "facts_and_reasons": [
                "根据[待核验法律依据]及[待核验法律依据]的规定，用人单位应当支付二倍工资。"
            ],
            "data_patch": {},
            "evidence_items": [],
            "verified_law": [
                {"citation": "《中华人民共和国劳动合同法》第八十七条"},
                {"citation": "《中华人民共和国劳动合同法实施条例》第七条"},
            ],
            "missing_fields": [],
        }

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {
        "id": "case-1",
        "case_stage": "litigation",
        "party_side": "worker",
        "data": {
            "legal_research": {
                "law": {
                    "verified": True,
                    "source": "yuandian:mcp:law",
                    "retrieved_at": "2026-08-14T00:00:00+00:00",
                    "content": {
                        "text": "《中华人民共和国劳动合同法》第八十七条 用人单位违反本法规定解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。 《中华人民共和国劳动合同法实施条例》第七条 用人单位自用工之日起满一年未与劳动者订立书面劳动合同的，自用工之日起满一个月的次日至满一年的前一日应当依照劳动合同法第八十二条的规定向劳动者每月支付两倍的工资。"
                    },
                }
            }
        },
    }

    result = asyncio.run(drafting.draft_case_documents(case=case, evidence_items=[], material_texts={}))

    text = "\n".join(result["facts_and_reasons"])
    assert (
        "根据《中华人民共和国劳动合同法》第八十七条及《中华人民共和国劳动合同法实施条例》第七条的规定"
        in text
    )
    assert "法律依据：" not in text
    assert "[待核验法律依据]" not in text
    assert len(result["facts_and_reasons"]) == 1


def test_draft_woven_citation_fills_extra_placeholders_with_last_verified_law(monkeypatch):
    async def complete_json(**_kwargs):
        return {
            "claims": ["请求判令被告支付赔偿金。"],
            "facts_and_reasons": ["根据[待核验法律依据]及[待核验法律依据]、[待核验法律依据]的规定支付。"],
            "data_patch": {},
            "evidence_items": [],
            "verified_law": [{"citation": "《中华人民共和国劳动合同法》第八十七条"}],
            "missing_fields": [],
        }

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {
        "id": "case-1",
        "case_stage": "litigation",
        "party_side": "worker",
        "data": {
            "legal_research": {
                "law": {
                    "verified": True,
                    "source": "yuandian:mcp:law",
                    "retrieved_at": "2026-08-14T00:00:00+00:00",
                    "content": {
                        "text": "《中华人民共和国劳动合同法》第八十七条 用人单位违反本法规定解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。"
                    },
                }
            }
        },
    }

    result = asyncio.run(drafting.draft_case_documents(case=case, evidence_items=[], material_texts={}))

    assert "[待核验法律依据]" not in "\n".join(result["facts_and_reasons"])
    assert "法律依据：" not in "\n".join(result["facts_and_reasons"])


def test_draft_splits_multi_evidence_material_into_separate_catalog_entries(monkeypatch):
    async def complete_json(**_kwargs):
        return {
            "claims": ["请求判令被告支付二倍工资。"],
            "facts_and_reasons": ["原告与被告存在劳动关系。"],
            "data_patch": {},
            "evidence_items": [
                {"id": "bundle", "name": "劳动合同", "purpose": "证明劳动关系。", "include": True, "order": 1, "pages": "1-2"},
                {"id": "bundle", "name": "微信支付明细", "purpose": "证明工资标准。", "include": True, "order": 2, "pages": "第3页"},
                {"id": "bundle", "name": "离职通知", "purpose": "证明解除事实。", "include": True, "order": 3, "pages": "4-5"},
                {"id": "other", "name": "仲裁裁决", "purpose": "证明仲裁前置。", "include": True, "order": 4, "pages": "1"},
            ],
            "verified_law": [],
            "missing_fields": [],
        }

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {
        "id": "case-1",
        "case_stage": "litigation",
        "party_side": "worker",
        "data": {},
    }
    result = asyncio.run(
        drafting.draft_case_documents(
            case=case,
            evidence_items=[
                {"id": "bundle", "name": "材料A", "purpose": "", "original_name": "a.pdf"},
                {"id": "other", "name": "材料B", "purpose": "", "original_name": "b.pdf"},
            ],
            material_texts={"bundle": "--- 第1页 ---\n劳动合同\n\n--- 第2页 ---\n工资表", "other": "裁决书"},
        )
    )

    updates = result["evidence_updates"]
    bundle = [update for update in updates if update["id"] == "bundle"]
    other = [update for update in updates if update["id"] == "other"]
    assert len(bundle) == 3
    assert [update["page_range"] for update in bundle] == [[1, 2], [3, 3], [4, 5]]
    assert [update["split_index"] for update in bundle] == [0, 1, 2]
    assert [update["name"] for update in bundle] == ["劳动合同", "微信支付明细", "离职通知"]
    assert [update["order"] for update in bundle] == [1, 2, 3]
    assert other[0]["page_range"] == [1, 1]


def test_draft_ignores_unparsable_pages_without_dropping_evidence(monkeypatch):
    async def complete_json(**_kwargs):
        return {
            "claims": ["请求判令被告支付赔偿金。"],
            "facts_and_reasons": ["原告与被告存在劳动关系。"],
            "data_patch": {},
            "evidence_items": [
                {"id": "bundle", "name": "劳动合同", "purpose": "证明劳动关系。", "include": True, "order": 1, "pages": "看不清"},
                {"id": "bundle", "name": "离职通知", "purpose": "证明解除事实。", "include": True, "order": 2, "pages": "2-1"},
            ],
            "verified_law": [],
            "missing_fields": [],
        }

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    case = {"id": "case-1", "case_stage": "litigation", "party_side": "worker", "data": {}}
    result = asyncio.run(
        drafting.draft_case_documents(
            case=case,
            evidence_items=[{"id": "bundle", "name": "材料A", "purpose": "", "original_name": "a.pdf"}],
            material_texts={"bundle": "--- 第1页 ---\n劳动合同"},
        )
    )

    updates = result["evidence_updates"]
    assert [update["page_range"] for update in updates] == [None, None]
    assert len(updates) == 2


def test_draft_returns_logical_evidence_order_and_selection(monkeypatch):
    async def complete_json(**_kwargs):
        result = _model_result()
        result["evidence_items"] = [
            {"id": "salary", "name": "工资记录", "purpose": "证明工资标准。", "include": True, "order": 2},
            {"id": "irrelevant", "name": "无关材料", "purpose": "与本案无关。", "include": False, "order": 9},
            {"id": "contract", "name": "劳动合同", "purpose": "证明劳动关系。", "include": True, "order": 1},
        ]
        return result

    monkeypatch.setattr(drafting, "complete_json", complete_json)
    result = asyncio.run(
        drafting.draft_case_documents(
            case={"id": "case-1", "case_stage": "arbitration", "party_side": "worker", "data": {}},
            evidence_items=[
                {"id": "salary", "name": "材料A", "purpose": "", "original_name": "a.pdf"},
                {"id": "irrelevant", "name": "材料B", "purpose": "", "original_name": "b.pdf"},
                {"id": "contract", "name": "材料C", "purpose": "", "original_name": "c.pdf"},
            ],
            material_texts={"salary": "工资", "irrelevant": "其他", "contract": "劳动合同"},
        )
    )

    updates = {item["id"]: item for item in result["evidence_updates"]}
    assert updates["contract"]["order"] == 1
    assert updates["salary"]["order"] == 2
    assert updates["irrelevant"]["included"] is False
