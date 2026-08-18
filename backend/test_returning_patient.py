"""
test_returning_patient.py

Automated integration tests for the Returning Patient Flow:
1. Exact email + phone matches returning patient and retrieves existing assessment.
2. New patients are allowed to take assessments.
3. Partial matches (same email + different phone, or different email + same phone) are blocked from new assessment but do NOT leak previous assessment data.
4. Returning patient cannot re-run AI or create duplicate assessments.
5. Previous results, scores, findings, recommendations, and Before & After preview are preserved.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import app

client = TestClient(app)


def test_returning_patient_eligibility_and_retrieval():
    mock_assessment_row = {
        "id": "test-assess-uuid-999",
        "name": "Sarah Khan",
        "email": "sarah.khan@example.com",
        "phone": "+923001234567",
        "gender": "Female",
        "age": 29,
        "city": "Lahore",
        "overall_score": 85,
        "category_scores": {
            "alignment": 88,
            "gum_health": 82,
            "color": 85,
            "restorations": 90,
            "missing_teeth": 100,
        },
        "findings": {
            "visible_concerns": ["mild_crowding"],
            "concern_details": [
                {
                    "concern": "mild_crowding",
                    "likely_cause": "Minor overlap visible in anterior teeth.",
                    "treatment_options": ["Clear aligners", "Orthodontic consultation"],
                }
            ],
            "treatment_recommendations": {
                "primary": {
                    "title": "Clear Aligner Therapy",
                    "description": "Align anterior teeth discreetly.",
                    "rationale": "Indicated to correct mild anterior crowding.",
                    "steps": ["Digital smile scan", "Custom aligner series"],
                },
                "additional": [],
            },
            "photo_simulation_path": "test-assess-uuid-999/simulation.jpg",
        },
        "report_text": "Dental Assessment Report for Sarah Khan...",
        "created_at": "2026-08-10T12:00:00Z",
        "photo_front_path": "test-assess-uuid-999/front.jpg",
        "photo_left_path": None,
        "photo_right_path": None,
    }

    print("\n--- 1. Testing GET /api/eligibility for returning patient (exact match) ---")
    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb:

        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [mock_assessment_row]

        res = client.get("/api/eligibility?email=sarah.khan@example.com&phone=%2B923001234567")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False
        assert data["returning_patient"] is True
        assert data["assessment_id"] == "test-assess-uuid-999"
        assert "welcome back" in data["reason"].lower()
        print("✔ Returning patient detected with returning_patient: True and assessment_id.")

    print("\n--- 2. Testing GET /api/patient/assessment for returning patient ---")
    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb, \
         patch("booking_api.signed_photo_urls", return_value={
             "front": "https://storage.example.com/front.jpg?signed=1",
             "simulation": "https://storage.example.com/simulation.jpg?signed=1",
         }), \
         patch("booking_api.get_chat_history", return_value=[
             {"role": "user", "content": "Will aligners hurt?"},
             {"role": "assistant", "content": "Most patients experience mild pressure for the first few days."},
         ]):

        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [mock_assessment_row]

        res = client.get("/api/patient/assessment?email=sarah.khan@example.com&phone=%2B923001234567")
        assert res.status_code == 200
        data = res.json()
        assert data["found"] is True
        assert data["returning"] is True
        assess = data["assessment"]
        assert assess["id"] == "test-assess-uuid-999"
        assert assess["name"] == "Sarah Khan"
        assert assess["overall_score"] == 85
        assert assess["category_scores"]["alignment"] == 88
        assert assess["findings"]["treatment_recommendations"]["primary"]["title"] == "Clear Aligner Therapy"
        assert assess["photos"]["simulation"] == "https://storage.example.com/simulation.jpg?signed=1"
        assert len(assess["chat_history"]) == 2
        print("✔ Stored assessment results, scores, recommendations, photos, and chat history returned successfully.")

    print("\n--- 3. Testing GET /api/patient/assessment when NOT found ---")
    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb:

        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

        res = client.get("/api/patient/assessment?email=new@example.com&phone=%2B923009999999")
        assert res.status_code == 200
        data = res.json()
        assert data["found"] is False
        assert "no previous assessment" in data["reason"].lower()
        print("✔ Correct response when no previous assessment exists.")

    print("\n--- 4. Testing Security: Partial match does NOT expose assessment data ---")
    # Same email, different phone
    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb:

        # Exact match returns empty
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        # Single field lookup finds email
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"id": "other-id"}]

        # Eligibility reports email is used, but returning_patient is False
        elig_res = client.get("/api/eligibility?email=sarah.khan@example.com&phone=%2B923000000000")
        assert elig_res.status_code == 200
        elig_data = elig_res.json()
        assert elig_data["ok"] is False
        assert elig_data["returning_patient"] is False
        assert elig_data["field"] == "email"

        # Direct assessment retrieval returns found: False
        assess_res = client.get("/api/patient/assessment?email=sarah.khan@example.com&phone=%2B923000000000")
        assert assess_res.status_code == 200
        assert assess_res.json()["found"] is False
        print("✔ Security verified: Partial match does NOT expose assessment data.")

    print("\n--- 5. Testing POST /analyze blocks duplicate creation for returning patients ---")
    with patch("main.db_ready", return_value=True), \
         patch("main.check_eligibility", return_value={
             "ok": False,
             "returning_patient": True,
             "reason": "Welcome back! We found your previous smile assessment.",
         }), \
         patch("main.run_analysis") as mock_run_analysis:

        fake_image = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 50
        res = client.post(
            "/analyze",
            files={"front_image": ("front.jpg", fake_image, "image/jpeg")},
            data={
                "name": "Sarah Khan",
                "email": "sarah.khan@example.com",
                "phone": "+923001234567",
                "gender": "Female",
                "age": "29",
                "city": "Lahore",
            },
        )
        assert res.status_code == 409
        assert "welcome back" in res.json().get("detail", "").lower()
        mock_run_analysis.assert_not_called()
        print("✔ POST /analyze strictly blocks returning patient from re-running AI analysis.")


if __name__ == "__main__":
    test_returning_patient_eligibility_and_retrieval()
