from app.public_stream import public_commentary, response_chunks


def test_response_chunks_reconstruct_public_answer_without_loss():
    text = "第一段说明事实。" * 20 + "\n\n第二段给出解释，并保留不确定性。"
    chunks = response_chunks(text, target=40, maximum=64)
    assert "".join(chunks) == text
    assert len(chunks) > 2
    assert all(0 < len(chunk) <= 64 for chunk in chunks)


def test_public_commentary_uses_only_safe_summary_and_counts():
    payload = public_commentary("run-1", "search_web", {
        "sourceCount": 8,
        "rawResponse": "private tool payload must not be copied",
    })
    assert payload["status"] == "commentary"
    assert "8 条" in payload["message"]
    assert "private tool payload" not in str(payload)
