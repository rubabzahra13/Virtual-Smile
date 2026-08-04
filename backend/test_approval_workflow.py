"""
test_approval_workflow.py

Automated integration test for the Appointment Manual Approval Workflow.
"""

import sys
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

# Add backend directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import app
from booking_api import require_admin

app.dependency_overrides[require_admin] = lambda: "admin"


def test_booking_approval_workflow():
    client = TestClient(app)

    # Mock DB functions or Supabase calls if needed, or test with mock Supabase client
    test_booking_id = "test-booking-uuid-12345"
    mock_row = {
        "id": test_booking_id,
        "name": "Approval Test Patient",
        "email": "testpatient@example.com",
        "phone": "+923001234567",
        "date": "2026-12-25",
        "time": "14:00",
        "status": "pending",
        "source": "patient",
        "assessment_id": None,
        "created_at": "2026-08-04T10:00:00Z",
    }

    mock_schedules = [{
        "id": "sched-1",
        "label": "Default Clinic Hours",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "open_time": "09:00",
        "close_time": "18:00",
        "slot_minutes": 30,
        "active": True,
    }]

    print("--- 1. Testing POST /api/bookings creation flow with existing assessment ---")
    mock_assessment = {
        "id": "assess-uuid-1",
        "name": "Approval Test Patient",
        "email": "testpatient@example.com",
        "phone": "+923001234567",
        "gender": "male",
        "age": 30,
        "city": "Lahore",
        "created_at": "2026-08-01T10:00:00Z",
    }

    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb, \
         patch("booking_api.fetch_schedules", return_value=mock_schedules), \
         patch("booking_api.fetch_bookings_for_date", return_value=[]), \
         patch("booking_api.find_patient_assessment", return_value=mock_assessment), \
         patch("booking_api.send_booking_email") as mock_email:

        mock_execute = mock_sb.return_value.table.return_value.insert.return_value.execute
        mock_execute.return_value.data = [mock_row]

        payload = {
            "name": "Approval Test Patient",
            "email": "testpatient@example.com",
            "phone": "+923001234567",
            "date": "2026-12-25",
            "time": "14:00",
            "source": "patient"
        }
        res = client.post("/api/bookings", json=payload)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["status"] == "pending", f"Expected status 'pending', got '{data.get('status')}'"
        assert data["email_sent"] is False, "Confirmation email must NOT be sent upon creation"
        mock_email.assert_not_called()
        print("✔ Appointment creation with existing assessment defaults to 'pending'.")

    print("\n--- 1b. Testing POST /api/bookings creation flow WITHOUT assessment (should fail) ---")
    with patch("booking_api._require_db"), \
         patch("booking_api.find_patient_assessment", return_value=None):

        payload_no_assess = {
            "name": "New Patient",
            "email": "newpatient@example.com",
            "phone": "+923009876543",
            "date": "2026-12-25",
            "time": "15:00",
            "source": "patient"
        }
        res = client.post("/api/bookings", json=payload_no_assess)
        assert res.status_code == 409, f"Expected 409, got {res.status_code}: {res.text}"
        assert "assessment" in res.json().get("detail", "").lower(), "Error message must mention assessment requirement"
        print("✔ Patient booking without prior assessment is correctly blocked.")

    print("\n--- 1c. Testing GET /api/patient/verify-assessment endpoint ---")
    with patch("booking_api._require_db"), \
         patch("booking_api.find_patient_assessment") as mock_find:

        mock_find.return_value = mock_assessment
        res = client.get("/api/patient/verify-assessment?email=testpatient@example.com&phone=%2B923001234567")
        assert res.status_code == 200
        assert res.json()["found"] is True
        assert res.json()["patient"]["name"] == "Approval Test Patient"

        mock_find.return_value = None
        res = client.get("/api/patient/verify-assessment?email=unknown@example.com&phone=%2B923000000000")
        assert res.status_code == 200
        assert res.json()["found"] is False
        print("✔ GET /api/patient/verify-assessment correctly identifies existing vs new patients.")

    print("\n--- 2. Testing Admin Approval via PATCH /admin/api/bookings/{id} ---")
    approved_row = {**mock_row, "status": "approved"}
    with patch("booking_api._require_db"), \
         patch("booking_api.require_admin", return_value="admin"), \
         patch("booking_api.get_supabase") as mock_sb, \
         patch("booking_api.send_booking_email", return_value=True) as mock_email:

        # Mock query for cur booking lookup
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [mock_row]
        # Mock update
        mock_sb.return_value.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [approved_row]

        patch_payload = {"status": "approved"}
        res = client.patch(f"/admin/api/bookings/{test_booking_id}", json=patch_payload)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["status"] == "approved", f"Expected status 'approved', got '{data.get('status')}'"
        assert data["email_sent"] is True, "Confirmation email must be sent upon approval"
        mock_email.assert_called_once()
        print("✔ Approving appointment sets status to 'approved' and triggers confirmation email.")

    print("\n--- 3. Testing Admin Rejection via PATCH /admin/api/bookings/{id} ---")
    rejected_row = {**mock_row, "status": "cancelled", "note": "[REJECTED] Test Note"}
    with patch("booking_api._require_db"), \
         patch("booking_api.require_admin", return_value="admin"), \
         patch("booking_api.get_supabase") as mock_sb, \
         patch("booking_api.send_booking_email") as mock_confirm_email, \
         patch("booking_api.send_rejection_email", return_value=True) as mock_reject_email:

        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [mock_row]
        mock_sb.return_value.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [rejected_row]

        patch_payload = {"status": "rejected"}
        res = client.patch(f"/admin/api/bookings/{test_booking_id}", json=patch_payload)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["status"] == "rejected", f"Expected status 'rejected', got '{data.get('status')}'"
        assert data["email_sent"] is True, "Rejection email must be sent upon rejection"
        mock_confirm_email.assert_not_called()
        mock_reject_email.assert_called_once()
        print("✔ Rejecting appointment sets status to 'rejected' and triggers rejection email.")

    print("\nALL APPROVAL & REJECTION WORKFLOW TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_booking_approval_workflow()
