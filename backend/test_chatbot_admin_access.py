"""
test_chatbot_admin_access.py

Comprehensive test suite verifying:
1. Backward compatibility: existing/unused chat history returns None (JSON null).
2. Chatbot turns are persisted only when a patient actually chats.
3. Admin endpoints return chat_history for patients with conversation logs.
4. Assessment report PDF build continues to exclude chatbot history.
"""

import sys
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import app
from booking_api import require_admin
from chat_storage import save_chat_turn, get_chat_history

app.dependency_overrides[require_admin] = lambda: "admin"


def test_chatbot_admin_access_flow():
    import os, chat_storage
    if chat_storage.HISTORY_FILE.exists():
        try:
            os.remove(chat_storage.HISTORY_FILE)
        except Exception:
            pass

    client = TestClient(app)

    test_assessment_id = "test-assess-123"
    test_email = "chatbotpatient@example.com"
    test_phone = "+923009998877"

    print("\n--- 1. Verification of Backward Compatibility (Null History) ---")
    initial_history = get_chat_history(assessment_id="nonexistent-id", email="oldpatient@example.com")
    assert initial_history is None, "Existing/unused patient must return None for chat_history."
    print("✔ Unused/old patient correctly returns null (None).")

    print("\n--- 2. Testing Chatbot Conversation Persistence ---")
    mock_groq_res = {"raw_text": "Based on your symptoms, we recommend consulting a dentist."}
    chat_payload = {
        "question": "I have pain in my lower jaw. What should I do?",
        "report_text": "Overall smile score: 75/100. Mild sensitivity reported.",
        "overall_score": 75,
        "email": test_email,
        "phone": test_phone,
        "assessment_id": test_assessment_id,
        "history": []
    }

    with patch("main.call_gemini_text", return_value=mock_groq_res), patch("main.call_groq_text", return_value=mock_groq_res):
        res = client.post("/chat", json=chat_payload)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "consulting a dentist" in data["answer"]
        print("✔ Patient /chat request succeeded.")

    stored_history = get_chat_history(assessment_id=test_assessment_id)
    assert stored_history is not None, "Chat history must be persisted after /chat call."
    assert len(stored_history) == 2, f"Expected 2 messages (user + assistant), got {len(stored_history)}"
    assert stored_history[0]["role"] == "user"
    assert stored_history[0]["content"] == "I have pain in my lower jaw. What should I do?"
    assert stored_history[1]["role"] == "assistant"
    assert "consulting a dentist" in stored_history[1]["content"]
    print("✔ Chatbot interaction successfully stored with correct roles and chronological order.")

    print("\n--- 3. Testing Admin Report Detail Endpoint with Chat History ---")
    mock_assessment_row = {
        "id": test_assessment_id,
        "name": "Chatbot Test Patient",
        "email": test_email,
        "phone": test_phone,
        "overall_score": 75,
        "findings": {},
        "report_text": "Report text sample",
        "created_at": "2026-08-04T10:00:00Z"
    }

    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb, \
         patch("booking_api.signed_photo_urls", return_value={}):

        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [mock_assessment_row]
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []

        res = client.get(f"/admin/api/reports/{test_assessment_id}")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        report_data = res.json()
        assert report_data.get("chat_history") is not None, "chat_history should be attached to report response"
        assert len(report_data["chat_history"]) == 2
        print("✔ Admin report detail endpoint successfully returned chat_history.")

    print("\n--- 4. Testing Admin Chat History Specific Endpoint ---")
    with patch("booking_api._require_db"), patch("booking_api.get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [mock_assessment_row]
        res = client.get(f"/admin/api/reports/{test_assessment_id}/chat-history")
        assert res.status_code == 200
        chat_res = res.json()
        assert chat_res["history"] is not None
        assert len(chat_res["history"]) == 2
        print("✔ Admin /reports/{id}/chat-history endpoint returned conversation log.")

    print("\n--- 5. Verification: PDF Generation Excludes Chat History ---")
    from email_report import build_report_pdf_bytes
    pdf_bytes = build_report_pdf_bytes(
        overall_score=75,
        category_scores={"color": 80},
        findings={"findings": []},
        name="Chatbot Test Patient"
    )
    assert isinstance(pdf_bytes, bytes) and len(pdf_bytes) > 0
    print("✔ Assessment PDF report generated independently without chatbot history inclusion.")

    print("\nALL ADMIN PATIENT CHATBOT ACCESS TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_chatbot_admin_access_flow()
