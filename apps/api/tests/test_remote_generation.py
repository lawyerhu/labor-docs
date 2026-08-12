from app.services.remote_generation import _merge_known_values


def test_ai_extracted_case_fields_fill_blanks_without_overwriting_confirmed_values():
    current = {
        "parties": {"initiating": {"name": "已确认的公司", "address": ""}},
        "arbitration": {},
    }
    extracted = {
        "parties": {"initiating": {"name": "材料中的其他名称", "address": "苏州市吴中区"}},
        "arbitration": {"award_number": "苏劳人仲案字〔2026〕1号"},
    }

    merged = _merge_known_values(current, extracted)

    assert merged["parties"]["initiating"]["name"] == "已确认的公司"
    assert merged["parties"]["initiating"]["address"] == "苏州市吴中区"
    assert merged["arbitration"]["award_number"] == "苏劳人仲案字〔2026〕1号"
