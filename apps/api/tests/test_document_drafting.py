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
