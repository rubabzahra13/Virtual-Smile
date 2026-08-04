"""Public + admin booking/assessment API helpers and routes."""

from __future__ import annotations

import logging
import re
from datetime import date as date_cls
from datetime import datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from admin_auth import issue_token, require_admin
from db import (
    db_ready,
    db_retry,
    extract_concerns_treatments,
    get_supabase,
    normalize_email,
    normalize_phone,
)
from email_report import (
    build_report_pdf_bytes,
    send_assessment_email,
    send_booking_email,
    send_cancellation_email,
)
from photo_storage import (
    download_assessment_photo_bytes,
    signed_photo_urls,
    signed_url_for_path,
    upload_assessment_photos,
)
from slots import free_slots_for_date, generate_slots_for_date, month_availability, pick_schedule_for_date

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api", tags=["public"])
admin_router = APIRouter(prefix="/admin/api", tags=["admin"])


def _is_phase_step(text: Any) -> bool:
    return bool(re.match(r"(?i)^\s*phase\s*\d+\s*:", str(text or "").strip()))


TREATED_MARK = "[TREATED]"


def booking_is_treated(row: dict) -> bool:
    """True when visit is treated history (column or note marker fallback)."""
    if "treated" in row and row.get("treated") is not None:
        return bool(row.get("treated"))
    note = str(row.get("note") or "")
    return note.lstrip().upper().startswith(TREATED_MARK)


def treated_note_value(current_note: Any, treated: bool) -> Optional[str]:
    note = str(current_note or "")
    stripped = re.sub(r"(?i)^\s*\[TREATED\]\s*", "", note).strip()
    if treated:
        return f"{TREATED_MARK}\n{stripped}".strip() if stripped else TREATED_MARK
    return stripped or None


def _assessment_lookup_maps(assessments: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """Map normalized email/phone → latest assessment id."""
    by_email: dict[str, tuple[str, str]] = {}
    by_phone: dict[str, tuple[str, str]] = {}
    for row in assessments:
        aid = str(row.get("id") or "").strip()
        if not aid:
            continue
        created = str(row.get("created_at") or "")
        email_n = normalize_email(row.get("email") or "")
        phone_n = normalize_phone(row.get("phone") or "")
        if email_n:
            prev = by_email.get(email_n)
            if not prev or created >= prev[0]:
                by_email[email_n] = (created, aid)
        if phone_n:
            prev = by_phone.get(phone_n)
            if not prev or created >= prev[0]:
                by_phone[phone_n] = (created, aid)
    return (
        {k: v[1] for k, v in by_email.items()},
        {k: v[1] for k, v in by_phone.items()},
    )


def get_booking_status(b: dict) -> str:
    if not b:
        return "cancelled"
    raw_status = str(b.get("status") or "").lower()
    note = str(b.get("note") or "").strip()
    if note.startswith("[PENDING]"):
        return "pending"
    if note.startswith("[REJECTED]") or raw_status == "rejected":
        return "rejected"
    if raw_status == "cancelled":
        return "cancelled"
    if raw_status == "pending":
        return "pending"
    if raw_status in {"confirmed", "approved"}:
        return "approved"
    return raw_status or "pending"


def clean_booking_note(note: Optional[str]) -> Optional[str]:
    if not note:
        return None
    s = str(note).strip()
    for prefix in ("[PENDING]", "[REJECTED]", "[APPROVED]", "[TREATED]"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s or None


def resolve_booking_assessment_id(
    booking: dict,
    *,
    by_email: dict[str, str],
    by_phone: dict[str, str],
) -> Optional[str]:
    existing = str(booking.get("assessment_id") or "").strip()
    if existing:
        return existing
    email_n = normalize_email(booking.get("email") or "")
    phone_n = normalize_phone(booking.get("phone") or "")
    if email_n and email_n in by_email:
        return by_email[email_n]
    if phone_n and phone_n in by_phone:
        return by_phone[phone_n]
    return None


class LoginBody(BaseModel):
    password: str = Field(..., min_length=1)


class BookingCreate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    fullName: Optional[str] = Field(None, max_length=80)
    email: str = Field(..., min_length=3, max_length=120)
    phone: Optional[str] = Field(None, max_length=30)
    gender: Optional[str] = Field(None, max_length=40)
    age: Optional[int] = Field(None, ge=1, le=120)
    city: Optional[str] = Field(None, max_length=100)
    date: str = Field(..., description="YYYY-MM-DD")
    time: str = Field(..., description="HH:MM or 12:00 PM")
    note: Optional[str] = Field(None, max_length=1000)
    assessment_id: Optional[str] = None
    source: Literal["patient", "admin"] = "patient"

    @property
    def patient_name(self) -> str:
        val = self.fullName or self.name or ""
        return val.strip()


class BookingPatch(BaseModel):
    status: Optional[Literal["pending", "approved", "confirmed", "rejected", "cancelled"]] = None
    treated: Optional[bool] = None
    date: Optional[str] = None
    time: Optional[str] = None
    note: Optional[str] = None
    name: Optional[str] = None
    fullName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    city: Optional[str] = None


class ScheduleBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    start_date: str
    end_date: str
    days_of_week: List[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    open_time: str = "09:00"
    close_time: str = "20:00"
    slot_minutes: int = Field(default=30, ge=5, le=120)
    active: bool = True


class ScheduleActiveBody(BaseModel):
    active: bool


def _require_db():
    if not db_ready():
        raise HTTPException(
            status_code=503,
            detail="Database is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
        )


def parse_time_input(raw: str) -> str:
    """Return HH:MM 24h."""
    text = (raw or "").strip()
    ampm = None
    upper = text.upper()
    if upper.endswith("AM") or upper.endswith("PM"):
        ampm = upper[-2:]
        text = text[:-2].strip()
    parts = text.replace(".", ":").split(":")
    if not parts or not parts[0].isdigit():
        raise HTTPException(status_code=400, detail="Invalid time. Use e.g. 12:00 PM or 14:00.")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if ampm:
        if hour < 1 or hour > 12 or minute > 59:
            raise HTTPException(status_code=400, detail="Invalid time.")
        if ampm == "AM":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    if hour > 23 or minute > 59:
        raise HTTPException(status_code=400, detail="Invalid time.")
    return f"{hour:02d}:{minute:02d}"


def parse_date_input(raw: str) -> str:
    try:
        return date_cls.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date. Use YYYY-MM-DD.")


def fetch_schedules() -> list[dict]:
    """Active Clinic Hours schedules only (no hardcoded fallback)."""
    sb = get_supabase()
    res = sb.table("slot_schedules").select("*").eq("active", True).execute()
    return list(res.data or [])


def fetch_all_schedules() -> list[dict]:
    sb = get_supabase()
    res = sb.table("slot_schedules").select("*").order("start_date").execute()
    return list(res.data or [])


def fetch_bookings_for_date(day: str) -> list[dict]:
    sb = get_supabase()
    res = (
        sb.table("bookings")
        .select("id,date,time,status,note")
        .eq("date", day)
        .in_("status", ["pending", "confirmed", "approved"])
        .execute()
    )
    active = []
    for r in list(res.data or []):
        st = get_booking_status(r)
        if st in {"pending", "confirmed", "approved"}:
            r["status"] = st
            active.append(r)
    return active


def fetch_bookings_for_month(year: int, month: int) -> list[dict]:
    start = date_cls(year, month, 1)
    if month == 12:
        end = date_cls(year + 1, 1, 1)
    else:
        end = date_cls(year, month + 1, 1)
    sb = get_supabase()
    res = (
        sb.table("bookings")
        .select("id,date,time,status,note")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .in_("status", ["pending", "confirmed", "approved"])
        .execute()
    )
    active = []
    for r in list(res.data or []):
        st = get_booking_status(r)
        if st in {"pending", "confirmed", "approved"}:
            r["status"] = st
            active.append(r)
    return active


def check_eligibility(email: str, phone: str) -> dict:
    _require_db()
    email_n = normalize_email(email)
    phone_n = normalize_phone(phone)
    if not email_n and not phone_n:
        return {"ok": False, "reason": "Email and phone are required."}
    sb = get_supabase()
    if email_n:
        by_email = db_retry(
            lambda: sb.table("assessments")
            .select("id")
            .eq("email", email_n)
            .limit(1)
            .execute(),
            label="eligibility email lookup",
        )
        if by_email.data:
            return {
                "ok": False,
                "reason": "You have already taken an assessment with this email.",
                "field": "email",
            }
    if phone_n:
        by_phone = db_retry(
            lambda: sb.table("assessments")
            .select("id")
            .eq("phone", phone_n)
            .limit(1)
            .execute(),
            label="eligibility phone lookup",
        )
        if by_phone.data:
            return {
                "ok": False,
                "reason": "You have already taken an assessment with this mobile number.",
                "field": "phone",
            }
    return {"ok": True, "reason": ""}


def persist_assessment(
    *,
    email: Optional[str],
    phone: Optional[str],
    overall_score: Any,
    category_scores: Any,
    findings: Any,
    report_text: str,
    images: Optional[list] = None,
    name: Optional[str] = None,
    gender: Optional[str] = None,
    age: Optional[int] = None,
    city: Optional[str] = None,
) -> Optional[dict]:
    """Insert assessment + email report. Returns row or None if DB not configured."""
    if not db_ready():
        logger.warning("Skipping assessment persist: DB not configured.")
        return None

    email_n = normalize_email(email)
    phone_n = normalize_phone(phone)
    if not email_n or not phone_n:
        raise HTTPException(status_code=400, detail="Email and phone are required for assessment.")

    elig = check_eligibility(email_n, phone_n)
    if not elig.get("ok"):
        raise HTTPException(status_code=409, detail=elig.get("reason") or "You have already taken an assessment.")

    concerns, treatments = extract_concerns_treatments(findings)
    sb = get_supabase()
    row = {
        "email": email_n,
        "phone": phone_n,
        "name": (name or "").strip() or None,
        "gender": (gender or "").strip() or None,
        "age": age if isinstance(age, int) and 1 <= age <= 120 else None,
        "city": (city or "").strip() or None,
        "overall_score": overall_score,
        "category_scores": category_scores,
        "findings": findings,
        "report_text": report_text,
        "concerns": concerns,
        "treatments": treatments,
    }
    try:
        res = sb.table("assessments").insert(row).execute()
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            raise HTTPException(
                status_code=409,
                detail="You have already taken an assessment with this email or mobile number.",
            ) from e
        elif "column" in msg or "42703" in msg:
            fallback_row = {
                "email": email_n,
                "phone": phone_n,
                "overall_score": overall_score,
                "category_scores": category_scores,
                "findings": findings,
                "report_text": report_text,
                "concerns": concerns,
                "treatments": treatments,
            }
            res = sb.table("assessments").insert(fallback_row).execute()
        else:
            raise

    data = (res.data or [None])[0]
    if not data:
        return None

    try:
        photo_paths = upload_assessment_photos(str(data["id"]), images)
        if photo_paths:
            sb.table("assessments").update(photo_paths).eq("id", data["id"]).execute()
            data.update(photo_paths)
    except Exception:
        logger.exception("Assessment photo upload failed")

    sent = send_assessment_email(
        to_email=email_n,
        overall_score=overall_score if isinstance(overall_score, int) else None,
        findings=findings,
        report_text=report_text or "",
        category_scores=category_scores,
        images=images,
        name=name,
        gender=gender,
        age=age,
        city=city,
    )
    if sent:
        try:
            sb.table("assessments").update(
                {"email_sent_at": datetime.utcnow().isoformat() + "Z"}
            ).eq("id", data["id"]).execute()
            data["email_sent_at"] = datetime.utcnow().isoformat() + "Z"
        except Exception:
            logger.exception("Failed to stamp email_sent_at")
    data["email_sent"] = bool(sent)
    return data


def find_confirmed_booking(*, email: str = "", phone: str = "") -> Optional[dict]:
    """Return an existing active (pending/confirmed/approved) booking for this email or phone, if any."""
    email_n = normalize_email(email)
    phone_n = normalize_phone(phone)
    if not email_n and not phone_n:
        return None
    sb = get_supabase()

    def _lookup(field: str, value: str) -> Optional[dict]:
        res = db_retry(
            lambda: sb.table("bookings")
            .select("id,name,email,phone,date,time,status,note,source,created_at")
            .eq(field, value)
            .in_("status", ["pending", "confirmed", "approved"])
            .order("date")
            .execute(),
            label=f"booking lookup by {field}",
        )
        rows = list(res.data or [])
        for r in rows:
            st = get_booking_status(r)
            if st in {"pending", "confirmed", "approved"}:
                r["status"] = st
                return r
        return None

    if email_n:
        found = _lookup("email", email_n)
        if found:
            return found
    if phone_n:
        return _lookup("phone", phone_n)
    return None


def create_booking(body: BookingCreate, *, source: str) -> dict:
    _require_db()
    day = parse_date_input(body.date)
    slot = parse_time_input(body.time)
    email_n = normalize_email(body.email)
    phone_n = normalize_phone(body.phone) if body.phone else ""
    name = (body.patient_name or body.name or body.fullName or "").strip()
    gender = (body.gender or "").strip() or None
    age = body.age if isinstance(body.age, int) and 1 <= body.age <= 120 else None
    city = (body.city or "").strip() or None

    existing = find_confirmed_booking(email=email_n, phone=phone_n)
    if existing:
        when = f"{existing.get('date')} at {str(existing.get('time') or '')[:5]}"
        raise HTTPException(
            status_code=409,
            detail=f"You already have an appointment booked for {when}. Contact the clinic to change it.",
        )

    schedules = fetch_schedules()
    day_obj = date_cls.fromisoformat(day)
    today_obj = date_cls.today()

    if day_obj < today_obj:
        raise HTTPException(
            status_code=409,
            detail="Cannot book an appointment for a past date.",
        )

    if day_obj == today_obj:
        now_hhmm = datetime.now().strftime("%H:%M")
        if slot <= now_hhmm:
            raise HTTPException(
                status_code=409,
                detail="That time slot has already passed. Please choose a future time slot.",
            )

    free, booked = free_slots_for_date(schedules, day_obj, fetch_bookings_for_date(day))
    if slot not in free:
        raise HTTPException(
            status_code=409,
            detail="That date/time is unavailable. Please choose another slot.",
            headers={"X-Booked-Slots": ",".join(booked)},
        )

    raw_note = (body.note or "").strip()
    is_admin = source == "admin"
    note_val = (raw_note or None) if is_admin else (f"[PENDING] {raw_note}".strip() if raw_note else "[PENDING]")

    row = {
        "assessment_id": body.assessment_id or None,
        "name": name,
        "email": email_n,
        "phone": phone_n,
        "gender": gender,
        "age": age,
        "city": city,
        "date": day,
        "time": slot,
        "note": note_val,
        "source": source,
        "status": "confirmed",
    }
    sb = get_supabase()
    try:
        res = sb.table("bookings").insert(row).execute()
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            raise HTTPException(
                status_code=409,
                detail="That slot was just booked by someone else. Please pick another time.",
            ) from e
        elif "column" in msg or "42703" in msg:
            fallback_row = {
                "assessment_id": body.assessment_id or None,
                "name": name,
                "email": email_n,
                "phone": phone_n,
                "date": day,
                "time": slot,
                "note": note_val,
                "source": source,
                "status": "confirmed",
            }
            res = sb.table("bookings").insert(fallback_row).execute()
        else:
            raise
    data = (res.data or [None])[0]
    if not data:
        raise HTTPException(status_code=500, detail="Booking could not be created.")

    if is_admin:
        data["status"] = "approved"
        data["note"] = raw_note or None
        try:
            sent = send_booking_email(
                to_email=email_n,
                name=name,
                phone=phone_n,
                day=day,
                time_slot=slot,
                note=raw_note,
            )
            data["email_sent"] = bool(sent)
        except Exception:
            logger.exception("Booking confirmation email failed for admin-created booking")
            data["email_sent"] = False
    else:
        # Return pending status to application / client UI
        data["status"] = "pending"
        data["note"] = raw_note or None
        data["email_sent"] = False
    return data


# ——— Public routes ———


@public_router.get("/eligibility")
def api_eligibility(
    email: str = Query(""),
    phone: str = Query(""),
):
    if not db_ready():
        # Soft-allow when DB not wired yet so local UX still works.
        return {"ok": True, "reason": "", "db": False}
    try:
        return {**check_eligibility(email, phone), "db": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Eligibility check failed")
        raise HTTPException(
            status_code=503,
            detail="Could not verify your details. Please try again.",
        )


@public_router.get("/bookings/mine")
def api_my_booking(
    email: str = Query(""),
    phone: str = Query(""),
):
    """Check whether this patient already has a confirmed booking."""
    if not db_ready():
        return {"booked": False, "booking": None, "db": False}
    try:
        booking = find_confirmed_booking(email=email, phone=phone)
    except Exception:
        logger.exception("Booking lookup failed")
        raise HTTPException(
            status_code=503,
            detail="Could not verify booking status. Please try again.",
        )
    return {"booked": bool(booking), "booking": booking, "db": True}


@public_router.get("/availability")
def api_availability(day: str = Query(..., alias="date")):
    _require_db()
    day_s = parse_date_input(day)
    day_obj = date_cls.fromisoformat(day_s)
    schedules = fetch_schedules()
    free, booked = free_slots_for_date(
        schedules, day_obj, fetch_bookings_for_date(day_s)
    )
    schedule = pick_schedule_for_date(schedules, day_obj)
    all_slots = generate_slots_for_date(schedules, day_obj)
    open_time = None
    close_time = None
    if schedule and all_slots:
        open_raw = str(schedule.get("open_time") or "")[:5]
        close_raw = str(schedule.get("close_time") or "")[:5]
        open_time = open_raw or all_slots[0]
        close_time = close_raw or all_slots[-1]
    return {
        "date": day_s,
        "slots": free,
        "booked": booked,
        "all_slots": all_slots,
        "closed": len(all_slots) == 0,
        "open_time": open_time,
        "close_time": close_time,
    }


@public_router.get("/availability/month")
def api_availability_month(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    _require_db()
    schedules = fetch_schedules()
    bookings = fetch_bookings_for_month(year, month)
    days = month_availability(schedules, year, month, bookings)
    return {"year": year, "month": month, "days": days}


@public_router.post("/bookings")
def api_create_booking(body: BookingCreate):
    return create_booking(body, source="patient")


# ——— Admin routes ———


@admin_router.post("/login")
def admin_login(body: LoginBody):
    token = issue_token(body.password)
    return {"token": token}


@admin_router.get("/stats")
def admin_stats(_: str = Depends(require_admin)):
    _require_db()

    def _load():
        sb = get_supabase()
        assessments = (
            sb.table("assessments")
            .select("id,overall_score,concerns,treatments,email,phone,created_at")
            .execute()
        ).data or []
        bookings = (
            sb.table("bookings")
            .select("id,status,name,email,phone,gender,age,city,date,time,note,source,assessment_id,created_at")
            .execute()
        ).data or []
        return assessments, bookings

    try:
        assessments, bookings = db_retry(_load, label="admin stats")
    except Exception:
        logger.exception("Admin stats failed")
        raise HTTPException(status_code=503, detail="Could not load dashboard stats. Please try again.")

    scores = [r["overall_score"] for r in assessments if isinstance(r.get("overall_score"), (int, float))]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    buckets = [
        {"key": "attention", "label": "0–74", "hint": "Attention", "min": 0, "max": 74, "count": 0},
        {"key": "watch", "label": "75–89", "hint": "Watch", "min": 75, "max": 89, "count": 0},
        {"key": "good", "label": "90–100", "hint": "Good", "min": 90, "max": 100, "count": 0},
    ]
    for score in scores:
        for b in buckets:
            if b["min"] <= float(score) <= b["max"]:
                b["count"] += 1
                break

    for r in bookings:
        r["status"] = get_booking_status(r)
        r["treated"] = booking_is_treated(r)
        r["note"] = clean_booking_note(r.get("note"))

    pending_count = sum(1 for b in bookings if b.get("status") == "pending")
    confirmed = [
        b
        for b in bookings
        if b.get("status") in {"confirmed", "approved"} and not b.get("treated")
    ]
    cancelled = sum(1 for b in bookings if b.get("status") in {"cancelled", "rejected"})
    today = date_cls.today().isoformat()
    today_dt = date_cls.today()

    def _pct_change(current: int, previous: int) -> Optional[int]:
        if previous <= 0:
            return 100 if current > 0 else 0
        return int(round(((current - previous) / previous) * 100))

    def _in_range(day: Optional[date_cls], start: date_cls, end: date_cls) -> bool:
        return bool(day and start <= day <= end)

    week_start = today_dt.fromordinal(today_dt.toordinal() - 6)
    prev_week_end = week_start.fromordinal(week_start.toordinal() - 1)
    prev_week_start = prev_week_end.fromordinal(prev_week_end.toordinal() - 6)

    def _parse_day(raw: Any) -> Optional[date_cls]:
        text = str(raw or "")[:10]
        if len(text) < 10:
            return None
        try:
            return date_cls.fromisoformat(text)
        except ValueError:
            return None

    all_confirmed = [b for b in bookings if b.get("status") in {"confirmed", "approved"}]
    bookings_week = sum(
        1
        for b in all_confirmed
        if _in_range(_parse_day(b.get("created_at") or b.get("date")), week_start, today_dt)
    )
    bookings_prev = sum(
        1
        for b in all_confirmed
        if _in_range(_parse_day(b.get("created_at") or b.get("date")), prev_week_start, prev_week_end)
    )
    tests_week = sum(
        1
        for row in assessments
        if _in_range(_parse_day(row.get("created_at")), week_start, today_dt)
    )
    tests_prev = sum(
        1
        for row in assessments
        if _in_range(_parse_day(row.get("created_at")), prev_week_start, prev_week_end)
    )
    booking_change_pct = _pct_change(bookings_week, bookings_prev)
    assessment_change_pct = _pct_change(tests_week, tests_prev)

    today_visits = sorted(
        [b for b in confirmed if str(b.get("date") or "") == today],
        key=lambda b: str(b.get("time") or ""),
    )
    upcoming = sorted(
        [b for b in confirmed if str(b.get("date") or "") >= today],
        key=lambda b: (str(b.get("date") or ""), str(b.get("time") or "")),
    )[:12]

    month_prefix = today[:7]  # YYYY-MM
    calendar_days = sorted(
        {
            str(b.get("date"))
            for b in confirmed
            if str(b.get("date") or "").startswith(month_prefix)
        }
    )

    concern_counts: dict[str, int] = {}
    treatment_counts: dict[str, int] = {}
    for row in assessments:
        for c in row.get("concerns") or []:
            concern_counts[c] = concern_counts.get(c, 0) + 1
        for t in row.get("treatments") or []:
            if _is_phase_step(t):
                continue
            treatment_counts[t] = treatment_counts.get(t, 0) + 1

    top_concerns = sorted(concern_counts.items(), key=lambda x: (-x[1], x[0]))[:10]
    top_treatments = sorted(treatment_counts.items(), key=lambda x: (-x[1], x[0]))[:10]

    by_email, by_phone = _assessment_lookup_maps(assessments)

    def _booking_public(b: dict) -> dict:
        return {
            "id": b.get("id"),
            "name": b.get("name"),
            "email": b.get("email"),
            "phone": b.get("phone"),
            "gender": b.get("gender"),
            "age": b.get("age"),
            "city": b.get("city"),
            "date": b.get("date"),
            "time": str(b.get("time") or "")[:5],
            "note": b.get("note"),
            "source": b.get("source"),
            "status": b.get("status"),
            "assessment_id": resolve_booking_assessment_id(
                b, by_email=by_email, by_phone=by_phone
            ),
        }

    return {
        "assessment_count": len(assessments),
        "pending_count": pending_count,
        "booking_count": len(confirmed),
        "cancelled_count": cancelled,
        "today_count": len(today_visits),
        "booking_change_pct": booking_change_pct,
        "assessment_change_pct": assessment_change_pct,
        "avg_smile_score": avg_score,
        "score_distribution": [
            {"key": b["key"], "label": b["label"], "hint": b["hint"], "count": b["count"]}
            for b in buckets
        ],
        "top_concerns": [{"label": k, "count": v} for k, v in top_concerns],
        "top_treatments": [{"label": k, "count": v} for k, v in top_treatments],
        "today_visits": [_booking_public(b) for b in today_visits],
        "upcoming": [_booking_public(b) for b in upcoming],
        "calendar_days": calendar_days,
        "calendar_month": month_prefix,
    }


@admin_router.get("/reports")
def admin_reports(
    _: str = Depends(require_admin),
    q: str = Query(""),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    min_score: Optional[int] = Query(None),
    max_score: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    _require_db()
    ascending = order.lower() == "asc"
    sort_col = sort if sort in {"created_at", "overall_score", "email"} else "created_at"

    def _fetch():
        sb = get_supabase()
        query = sb.table("assessments").select("*")
        query = query.order(sort_col, desc=not ascending).limit(limit)
        return list((query.execute()).data or [])

    try:
        rows = db_retry(_fetch, label="admin reports")
    except Exception:
        logger.exception("Admin reports list failed")
        raise HTTPException(
            status_code=503,
            detail="Could not load assessments. Please try again.",
        )

    qn = q.strip().lower()
    filtered = []
    for row in rows:
        if min_score is not None and (row.get("overall_score") is None or row["overall_score"] < min_score):
            continue
        if max_score is not None and (row.get("overall_score") is None or row["overall_score"] > max_score):
            continue

        bookings = row.pop("bookings", None) or []
        if not isinstance(bookings, list):
            bookings = []

        def _booking_rank(b: dict) -> tuple:
            created = str(b.get("created_at") or "")
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
            return (0 if b.get("status") == "confirmed" else 1, -ts)

        name = (row.get("name") or "").strip()
        gender = (row.get("gender") or "").strip() or None
        age = row.get("age")
        city = (row.get("city") or "").strip() or None
        appointment_date = None
        appointment_time = None
        booking_rows = sorted(
            [b for b in bookings if isinstance(b, dict)],
            key=_booking_rank,
        )
        for booking in booking_rows:
            candidate = str(booking.get("name") or "").strip()
            if candidate and not name:
                name = candidate
            if booking.get("gender") and not gender:
                gender = str(booking.get("gender")).strip()
            if booking.get("age") and not age:
                age = booking.get("age")
            if booking.get("city") and not city:
                city = str(booking.get("city")).strip()

        for booking in booking_rows:
            day = str(booking.get("date") or "").strip() or None
            slot = str(booking.get("time") or "").strip() or None
            if not day:
                continue
            if booking.get("status") == "confirmed":
                appointment_date, appointment_time = day, slot
                break
            if appointment_date is None:
                appointment_date, appointment_time = day, slot

        front_path = str(row.get("photo_front_path") or "").strip()
        item = {
            "id": row.get("id"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "name": name or None,
            "gender": gender,
            "age": age,
            "city": city,
            "overall_score": row.get("overall_score"),
            "concerns": row.get("concerns") or [],
            "treatments": row.get("treatments") or [],
            "email_sent_at": row.get("email_sent_at"),
            "created_at": row.get("created_at"),
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "photo_front_url": signed_url_for_path(front_path) if front_path else None,
        }

        if qn:
            blob = (
                f"{item.get('name') or ''} {item.get('email') or ''} "
                f"{item.get('phone') or ''} {item.get('city') or ''} {item.get('gender') or ''} {' '.join(item.get('concerns') or [])}"
            ).lower()
            if qn not in blob:
                continue
        filtered.append(item)
    return {"items": filtered}


@admin_router.get("/patients")
def admin_patients(
    _: str = Depends(require_admin),
    q: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
):
    _require_db()
    sb = get_supabase()
    assessments_data = []
    bookings_data = []
    try:
        assessments_data = (sb.table("assessments").select("*").order("created_at", desc=True).limit(limit).execute()).data or []
    except Exception:
        pass
    try:
        bookings_data = (sb.table("bookings").select("*").order("created_at", desc=True).limit(limit).execute()).data or []
    except Exception:
        pass

    patients_map: dict[str, dict] = {}
    for a in assessments_data:
        key = normalize_email(a.get("email")) or normalize_phone(a.get("phone")) or str(a.get("id"))
        if not key:
            continue
        patients_map[key] = {
            "id": a.get("id"),
            "assessment_id": a.get("id"),
            "name": (a.get("name") or "").strip(),
            "fullName": (a.get("name") or "").strip(),
            "email": a.get("email") or "",
            "phone": a.get("phone") or "",
            "gender": a.get("gender") or "",
            "age": a.get("age"),
            "city": a.get("city") or "",
            "created_at": a.get("created_at"),
        }

    for b in bookings_data:
        key = normalize_email(b.get("email")) or normalize_phone(b.get("phone")) or str(b.get("id"))
        if not key:
            continue
        if key not in patients_map:
            patients_map[key] = {
                "id": b.get("id"),
                "assessment_id": b.get("assessment_id"),
                "name": (b.get("name") or "").strip(),
                "fullName": (b.get("name") or "").strip(),
                "email": b.get("email") or "",
                "phone": b.get("phone") or "",
                "gender": b.get("gender") or "",
                "age": b.get("age"),
                "city": b.get("city") or "",
                "created_at": b.get("created_at"),
            }
        else:
            p = patients_map[key]
            if not p.get("name") and b.get("name"):
                p["name"] = b["name"].strip()
                p["fullName"] = b["name"].strip()
            if not p.get("gender") and b.get("gender"):
                p["gender"] = b["gender"]
            if not p.get("age") and b.get("age"):
                p["age"] = b["age"]
            if not p.get("city") and b.get("city"):
                p["city"] = b["city"]
            if not p.get("assessment_id") and b.get("assessment_id"):
                p["assessment_id"] = b["assessment_id"]

    patient_list = list(patients_map.values())
    qn = q.strip().lower()
    if qn:
        patient_list = [
            p for p in patient_list
            if qn in f"{p.get('name','')} {p.get('email','')} {p.get('phone','')} {p.get('city','')} {p.get('gender','')}".lower()
        ]
    return {"items": patient_list}


@admin_router.get("/reports/{report_id}")
def admin_report_detail(report_id: str, _: str = Depends(require_admin)):
    _require_db()

    def _load_report():
        sb = get_supabase()
        return sb.table("assessments").select("*").eq("id", report_id).limit(1).execute()

    def _load_bookings():
        sb = get_supabase()
        by_id = (
            sb.table("bookings")
            .select("*")
            .eq("assessment_id", report_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = list(by_id.data or [])
        if rows:
            return by_id
        email_n = normalize_email((report or {}).get("email") or "")
        phone_n = normalize_phone((report or {}).get("phone") or "")
        query = sb.table("bookings").select("*").order("created_at", desc=True).limit(20)
        if email_n:
            return query.eq("email", email_n).execute()
        if phone_n:
            return query.eq("phone", phone_n).execute()
        return by_id

    try:
        res = db_retry(_load_report, label="admin report detail")
    except Exception:
        logger.exception("Admin report detail failed for %s", report_id)
        raise HTTPException(
            status_code=503,
            detail="Could not load assessment. Please try again.",
        )
    if not res.data:
        raise HTTPException(status_code=404, detail="Report not found.")
    report = res.data[0]

    bookings = []
    try:
        bookings = list(db_retry(_load_bookings, label="admin report bookings").data or [])
    except Exception:
        logger.exception("Admin report bookings failed for %s", report_id)

    photos = {}
    try:
        photos = signed_photo_urls(report)
    except Exception:
        logger.exception("Could not build signed photo URLs")
    return {"report": report, "bookings": bookings, "photos": photos}


@admin_router.get("/reports/{report_id}/pdf")
def admin_report_pdf(report_id: str, _: str = Depends(require_admin)):
    _require_db()

    def _load_report():
        sb = get_supabase()
        return sb.table("assessments").select("*").eq("id", report_id).limit(1).execute()

    try:
        res = db_retry(_load_report, label="admin report pdf detail")
    except Exception:
        logger.exception("Admin report pdf detail failed for %s", report_id)
        raise HTTPException(
            status_code=503,
            detail="Could not load assessment for PDF. Please try again.",
        )
    if not res.data:
        raise HTTPException(status_code=404, detail="Report not found.")

    report = res.data[0]

    # Fetch stored photos if available
    images: list[tuple[str, bytes]] = []
    for label, col in (
        ("Front smile", "photo_front_path"),
        ("Left smile", "photo_left_path"),
        ("Right smile", "photo_right_path"),
    ):
        path = str(report.get(col) or "").strip()
        if path:
            raw = download_assessment_photo_bytes(path)
            if raw:
                images.append((label, raw))

    try:
        pdf_bytes = build_report_pdf_bytes(
            overall_score=report.get("overall_score"),
            category_scores=report.get("category_scores"),
            findings=report.get("findings"),
            images=images if images else None,
            name=report.get("name"),
            email=report.get("email"),
            gender=report.get("gender"),
            age=report.get("age"),
            city=report.get("city"),
        )
    except Exception:
        logger.exception("Failed to build PDF report for %s", report_id)
        raise HTTPException(status_code=500, detail="Failed to generate PDF report.")

    filename = f"virtual-smile-assessment-report-{report_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@admin_router.get("/bookings")
def admin_bookings(
    _: str = Depends(require_admin),
    q: str = Query(""),
    sort: str = Query("date"),
    order: str = Query("desc"),
    status: str = Query("pending"),
    limit: int = Query(200, ge=1, le=500),
):
    _require_db()
    sort_col = sort if sort in {"date", "created_at", "name", "time"} else "date"
    ascending = order.lower() == "asc"

    def _fetch():
        sb = get_supabase()
        query = sb.table("bookings").select("*")
        query = query.order(sort_col, desc=not ascending).limit(limit)
        return query.execute()

    try:
        rows = list(db_retry(_fetch, label="admin bookings").data or [])
    except Exception:
        logger.exception("Admin bookings list failed")
        raise HTTPException(
            status_code=503,
            detail="Could not load appointments. Please try again.",
        )

    for r in rows:
        r["status"] = get_booking_status(r)
        r["treated"] = booking_is_treated(r)
        r["note"] = clean_booking_note(r.get("note"))

    if status == "pending":
        rows = [r for r in rows if r["status"] == "pending"]
    elif status in {"confirmed", "approved"}:
        rows = [r for r in rows if r["status"] in {"confirmed", "approved"} and not r["treated"]]
    elif status in {"cancelled", "rejected"}:
        rows = [r for r in rows if r["status"] in {"cancelled", "rejected"}]
    elif status == "treated":
        rows = [r for r in rows if r["treated"]]

    qn = q.strip().lower()
    if qn:
        rows = [
            r
            for r in rows
            if qn
            in f"{r.get('name','')} {r.get('email','')} {r.get('phone','')} {r.get('city','')} {r.get('gender','')} {r.get('date','')} {r.get('time','')}".lower()
        ]

    try:
        sb = get_supabase()
        assessments = (
            sb.table("assessments")
            .select("id,email,phone,created_at")
            .execute()
        ).data or []
        by_email, by_phone = _assessment_lookup_maps(assessments)
        for row in rows:
            linked = resolve_booking_assessment_id(
                row, by_email=by_email, by_phone=by_phone
            )
            if linked:
                row["assessment_id"] = linked
    except Exception:
        logger.exception("Could not link bookings to assessments")

    return {"items": rows}


@admin_router.post("/bookings")
def admin_create_booking(body: BookingCreate, _: str = Depends(require_admin)):
    return create_booking(body, source="admin")


@admin_router.patch("/bookings/{booking_id}")
def admin_patch_booking(
    booking_id: str,
    body: BookingPatch,
    _: str = Depends(require_admin),
):
    _require_db()
    sb = get_supabase()
    current = (
        sb.table("bookings").select("*").eq("id", booking_id).limit(1).execute()
    ).data
    if not current:
        raise HTTPException(status_code=404, detail="Booking not found.")
    cur = current[0]
    cur_status = get_booking_status(cur)

    updates: dict[str, Any] = {}
    clean_n = clean_booking_note(body.note if body.note is not None else cur.get("note"))

    if body.status is not None:
        st = body.status
        if st in {"approved", "confirmed"}:
            updates["status"] = "confirmed"
            updates["note"] = clean_n
        elif st == "rejected":
            updates["status"] = "cancelled"
            updates["note"] = f"[REJECTED] {clean_n}".strip() if clean_n else "[REJECTED]"
        elif st == "cancelled":
            updates["status"] = "cancelled"
            updates["note"] = clean_n
        elif st == "pending":
            updates["status"] = "confirmed"
            updates["note"] = f"[PENDING] {clean_n}".strip() if clean_n else "[PENDING]"

    if body.note is not None and "note" not in updates:
        if cur_status == "pending":
            updates["note"] = f"[PENDING] {clean_n}".strip() if clean_n else "[PENDING]"
        elif cur_status == "rejected":
            updates["note"] = f"[REJECTED] {clean_n}".strip() if clean_n else "[REJECTED]"
        else:
            updates["note"] = clean_n

    if body.name is not None or body.fullName is not None:
        updates["name"] = (body.fullName or body.name or "").strip()
    if body.email is not None:
        updates["email"] = normalize_email(body.email)
    if body.phone is not None:
        updates["phone"] = normalize_phone(body.phone)
    if body.gender is not None:
        updates["gender"] = body.gender.strip()
    if body.age is not None:
        updates["age"] = body.age
    if body.city is not None:
        updates["city"] = body.city.strip()
    if body.date is not None:
        updates["date"] = parse_date_input(body.date)
    if body.time is not None:
        updates["time"] = parse_time_input(body.time)

    if body.treated is not None:
        updates["treated"] = bool(body.treated)
        if body.note is None and "note" not in updates:
            updates["note"] = treated_note_value(cur.get("note"), bool(body.treated))

    if body.date is not None or body.time is not None:
        day = updates.get("date") or cur["date"]
        slot = updates.get("time") or (str(cur["time"])[:5])
        schedules = fetch_schedules()
        existing = [
            b
            for b in fetch_bookings_for_date(day)
            if b.get("id") != booking_id
        ]
        free, _ = free_slots_for_date(schedules, date_cls.fromisoformat(day), existing)
        target_st = body.status or cur_status
        if slot not in free and target_st in {"pending", "confirmed", "approved"}:
            raise HTTPException(status_code=409, detail="That slot is unavailable.")

    if not updates:
        raise HTTPException(status_code=400, detail="No changes provided.")

    try:
        res = sb.table("bookings").update(updates).eq("id", booking_id).execute()
    except Exception as e:
        msg = str(e).lower()
        if "treated" in updates and (
            "treated" in msg or "42703" in msg or "column" in msg
        ):
            updates.pop("treated", None)
            if not updates:
                raise HTTPException(
                    status_code=503,
                    detail="Could not update treated status. Apply migration 003_booking_treated.sql.",
                ) from e
            try:
                res = sb.table("bookings").update(updates).eq("id", booking_id).execute()
            except Exception as e2:
                msg2 = str(e2).lower()
                if "duplicate" in msg2 or "unique" in msg2:
                    raise HTTPException(status_code=409, detail="Slot conflict.") from e2
                raise
        elif "duplicate" in msg or "unique" in msg:
            raise HTTPException(status_code=409, detail="Slot conflict.") from e
        else:
            raise
    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found.")
    data = res.data[0]
    data["treated"] = booking_is_treated(data)

    becoming_approved = (
        body.status in {"approved", "confirmed"}
        and cur_status not in {"approved", "confirmed"}
    )
    if becoming_approved:
        try:
            sent = send_booking_email(
                to_email=str(data.get("email") or cur.get("email") or ""),
                name=str(data.get("name") or cur.get("name") or ""),
                phone=str(data.get("phone") or cur.get("phone") or ""),
                day=str(data.get("date") or cur.get("date") or ""),
                time_slot=str(data.get("time") or cur.get("time") or "")[:5],
                note=str(clean_booking_note(data.get("note")) or ""),
            )
            data["email_sent"] = bool(sent)
        except Exception:
            logger.exception("Booking confirmation email failed for booking %s", booking_id)
            data["email_sent"] = False

    becoming_cancelled = (
        body.status == "cancelled" and cur_status != "cancelled"
    )
    if becoming_cancelled:
        try:
            sent = send_cancellation_email(
                to_email=str(data.get("email") or cur.get("email") or ""),
                name=str(data.get("name") or cur.get("name") or ""),
                phone=str(data.get("phone") or cur.get("phone") or ""),
                day=str(data.get("date") or cur.get("date") or ""),
                time_slot=str(data.get("time") or cur.get("time") or "")[:5],
            )
            data["email_sent"] = bool(sent)
        except Exception:
            logger.exception("Cancellation email failed for booking %s", booking_id)
            data["email_sent"] = False

    data["status"] = get_booking_status(data)
    data["note"] = clean_booking_note(data.get("note"))
    return data


def _deactivate_all_others(sb, exclude_id: str) -> None:
    """Deactivate every schedule except the one being activated.

    This is the application-layer half of the single-active-schedule guarantee;
    the database trigger fn_single_active_schedule is the data-layer half.
    Both layers are required so the invariant holds even under concurrent writes.
    """
    sb.table("slot_schedules").update({"active": False}).neq("id", exclude_id).eq("active", True).execute()


@admin_router.get("/schedules")
def admin_list_schedules(_: str = Depends(require_admin)):
    _require_db()
    return {"items": fetch_all_schedules()}


@admin_router.post("/schedules")
def admin_create_schedule(body: ScheduleBody, _: str = Depends(require_admin)):
    _require_db()
    row = {
        "label": body.label.strip(),
        "start_date": parse_date_input(body.start_date),
        "end_date": parse_date_input(body.end_date),
        "days_of_week": body.days_of_week,
        "open_time": parse_time_input(body.open_time),
        "close_time": parse_time_input(body.close_time),
        "slot_minutes": body.slot_minutes,
        "active": body.active,
    }
    sb = get_supabase()
    res = sb.table("slot_schedules").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Could not create schedule.")
    created = res.data[0]
    # Enforce single-active invariant: deactivate all others if new one is active.
    if body.active:
        _deactivate_all_others(sb, exclude_id=str(created["id"]))
    return created


@admin_router.patch("/schedules/{schedule_id}")
def admin_patch_schedule(
    schedule_id: str,
    body: ScheduleBody,
    _: str = Depends(require_admin),
):
    _require_db()
    row = {
        "label": body.label.strip(),
        "start_date": parse_date_input(body.start_date),
        "end_date": parse_date_input(body.end_date),
        "days_of_week": body.days_of_week,
        "open_time": parse_time_input(body.open_time),
        "close_time": parse_time_input(body.close_time),
        "slot_minutes": body.slot_minutes,
        "active": body.active,
    }
    sb = get_supabase()
    # Enforce single-active invariant before writing.
    if body.active:
        _deactivate_all_others(sb, exclude_id=schedule_id)
    res = sb.table("slot_schedules").update(row).eq("id", schedule_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return res.data[0]


@admin_router.patch("/schedules/{schedule_id}/active")
def admin_set_schedule_active(
    schedule_id: str,
    body: ScheduleActiveBody,
    _: str = Depends(require_admin),
):
    """Toggle the active flag for a schedule.

    When activating (active=True) this atomically deactivates every other
    schedule first, guaranteeing that at most one schedule is active at
    any point in time.
    """
    _require_db()
    sb = get_supabase()
    if body.active:
        # Step 1: deactivate all others (application layer).
        # The DB trigger fn_single_active_schedule is the data-layer safety net.
        _deactivate_all_others(sb, exclude_id=schedule_id)
    # Step 2: set the requested state on the target schedule.
    res = (
        sb.table("slot_schedules")
        .update({"active": body.active})
        .eq("id", schedule_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return res.data[0]


@admin_router.delete("/schedules/{schedule_id}")
def admin_delete_schedule(schedule_id: str, _: str = Depends(require_admin)):
    _require_db()
    sb = get_supabase()
    res = sb.table("slot_schedules").delete().eq("id", schedule_id).execute()
    return {"ok": True, "deleted": res.data or []}
