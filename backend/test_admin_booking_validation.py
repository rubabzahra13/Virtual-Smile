"""
test_admin_booking_validation.py

Automated test for Admin Booking Form required fields (Gender, Age, City).
"""

import sys
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import app
from booking_api import require_admin

app.dependency_overrides[require_admin] = lambda: "admin"


def test_admin_booking_required_fields():
    client = TestClient(app)

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

    base_payload = {
        "name": "Validation Test Patient",
        "email": "validpatient@example.com",
        "phone": "+923001112233",
        "gender": "Female",
        "age": 25,
        "city": "Lahore",
        "date": "2026-12-25",
        "time": "11:00",
        "source": "admin"
    }

    print("\n--- Scenario 1: Attempt to create booking without Gender ---")
    payload1 = {**base_payload, "gender": ""}
    with patch("booking_api._require_db"), patch("booking_api.find_patient_assessment", return_value=None):
        res = client.post("/admin/api/bookings", json=payload1)
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
        assert "gender" in res.json().get("detail", "").lower()
        print("✔ Successfully blocked submission without Gender.")

    print("\n--- Scenario 2: Attempt to create booking without Age ---")
    payload2 = {**base_payload, "age": None}
    with patch("booking_api._require_db"), patch("booking_api.find_patient_assessment", return_value=None):
        res = client.post("/admin/api/bookings", json=payload2)
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
        assert "age" in res.json().get("detail", "").lower()
        print("✔ Successfully blocked submission without Age.")

    print("\n--- Scenario 3: Attempt to create booking without City ---")
    payload3 = {**base_payload, "city": ""}
    with patch("booking_api._require_db"), patch("booking_api.find_patient_assessment", return_value=None):
        res = client.post("/admin/api/bookings", json=payload3)
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
        assert "city" in res.json().get("detail", "").lower()
        print("✔ Successfully blocked submission without City.")

    print("\n--- Scenario 4: Create booking with all required fields completed ---")
    mock_created_row = {
        "id": "admin-book-1",
        **base_payload,
        "status": "confirmed",
        "created_at": "2026-08-04T10:00:00Z"
    }
    with patch("booking_api._require_db"), \
         patch("booking_api.get_supabase") as mock_sb, \
         patch("booking_api.fetch_schedules", return_value=mock_schedules), \
         patch("booking_api.fetch_bookings_for_date", return_value=[]), \
         patch("booking_api.find_patient_assessment", return_value=None), \
         patch("booking_api.send_booking_email", return_value=True):

        mock_sb.return_value.table.return_value.insert.return_value.execute.return_value.data = [mock_created_row]
        res = client.post("/admin/api/bookings", json=base_payload)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["email_sent"] is True
        print("✔ Successfully created admin booking with all required fields completed.")

    print("\nALL ADMIN BOOKING VALIDATION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_admin_booking_required_fields()
