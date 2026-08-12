from app.services.openai_compat import parse_json_text, response_text


def test_response_text_reads_chat_completions_payload():
    payload = {"choices": [{"message": {"content": '{"ok": true}'}}]}

    assert parse_json_text(response_text(payload)) == {"ok": True}


def test_response_text_reads_responses_payload():
    payload = {"output": [{"content": [{"type": "output_text", "text": '{"ok": true}'}]}]}

    assert parse_json_text(response_text(payload)) == {"ok": True}


def test_parse_json_text_accepts_markdown_fence():
    assert parse_json_text("```json\n{\"ok\": true}\n```") == {"ok": True}
