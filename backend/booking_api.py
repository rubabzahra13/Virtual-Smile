"""Public + admin booking/assessment API helpers and routes."""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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
from email_report import send_assessment_email, send_booking_email
from slots import free_slots_for_date, generate_slots_for_date, month_availability, pick_schedule_for_date

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api", tags=["public"])
admin_router = APIRouter(prefix="/admin/api", tags=["admin"])


class LoginBody(BaseModel):
    password: str = Field(..., min_length=1)


class BookingCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    email: str = Field(..., min_length=3, max_length=120)
    phone: str = Field(..., min_length=7, max_length=30)
    date: str = Field(..., description="YYYY-MM-DD")
    time: str = Field(..., description="HH:MM or 12:00 PM")
    note: Optional[str] = Field(None, max_length=1000)
    assessment_id: Optional[str] = None
    source: Literal["patient", "admin"] = "patient"


class BookingPatch(BaseModel):
    status: Optional[Literal["confirmed", "cancelled"]] = None
    date: Optional[str] = None
    time: Optional[str] = None
    note: Optional[str] = None
    name: Optional[str] = None


class ScheduleBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    start_date: str
    end_date: str
    days_of_week: List[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    open_time: str = "09:00"
    close_time: str = "20:00"
    slot_minutes: int = Field(default=30, ge=5, le=120)
    active: bool = True


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
        .select("id,date,time,status")
        .eq("date", day)
        .eq("status", "confirmed")
        .execute()
    )
    return list(res.data or [])


def fetch_bookings_for_month(year: int, month: int) -> list[dict]:
    start = date_cls(year, month, 1)
    if month == 12:
        end = date_cls(year + 1, 1, 1)
    else:
        end = date_cls(year, month + 1, 1)
    sb = get_supabase()
    res = (
        sb.table("bookings")
        .select("id,date,time,status")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .eq("status", "confirmed")
        .execute()
    )
    return list(res.data or [])


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
        raise

    data = (res.data or [None])[0]
    if not data:
        return None

    sent = send_assessment_email(
        to_email=email_n,
        overall_score=overall_score if isinstance(overall_score, int) else None,
        findings=findings,
        report_text=report_text or "",
        category_scores=category_scores,
        images=images,
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
    """Return an existing confirmed booking for this email or phone, if any."""
    email_n = normalize_email(email)
    phone_n = normalize_phone(phone)
    if not email_n and not phone_n:
        return None
    sb = get_supabase()

    def _lookup(field: str, value: str) -> Optional[dict]:
        res = db_retry(
            lambda: sb.table("bookings")
            .select("id,name,email,phone,date,time,status,source,created_at")
            .eq(field, value)
            .eq("status", "confirmed")
            .order("date")
            .limit(1)
            .execute(),
            label=f"booking lookup by {field}",
        )
        rows = list(res.data or [])
        return rows[0] if rows else None

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
    phone_n = normalize_phone(body.phone)
    name = body.name.strip()

    existing = find_confirmed_booking(email=email_n, phone=phone_n)
    if existing:
        when = f"{existing.get('date')} at {str(existing.get('time') or '')[:5]}"
        raise HTTPException(
            status_code=409,
            detail=f"You already have an appointment booked for {when}. Contact the clinic to change it.",
        )

    schedules = fetch_schedules()
    day_obj = date_cls.fromisoformat(day)
    free, booked = free_slots_for_date(schedules, day_obj, fetch_bookings_for_date(day))
    if slot not in free:
        raise HTTPException(
            status_code=409,
            detail="That date/time is unavailable. Please choose another slot.",
            headers={"X-Booked-Slots": ",".join(booked)},
        )

    row = {
        "assessment_id": body.assessment_id or None,
        "name": name,
        "email": email_n,
        "phone": phone_n,
        "date": day,
        "time": slot,
        "note": (body.note or "").strip() or None,
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
        raise
    data = (res.data or [None])[0]
    if not data:
        raise HTTPException(status_code=500, detail="Booking could not be created.")

    try:
        sent = send_booking_email(
            to_email=email_n,
            name=name,
            phone=phone_n,
            day=day,
            time_slot=slot,
            note=(body.note or "").strip(),
        )
        data["email_sent"] = bool(sent)
    except Exception:
        logger.exception("Booking confirmation email failed")
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
    sb = get_supabase()
    assessments = (
        sb.table("assessments")
        .select("overall_score,concerns,treatments")
        .execute()
    ).data or []
    bookings = (
        sb.table("bookings")
        .select("id,status,date")
        .eq("status", "confirmed")
        .execute()
    ).data or []

    scores = [r["overall_score"] for r in assessments if isinstance(r.get("overall_score"), (int, float))]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    concern_counts: dict[str, int] = {}
    treatment_counts: dict[str, int] = {}
    for row in assessments:
        for c in row.get("concerns") or []:
            concern_counts[c] = concern_counts.get(c, 0) + 1
        for t in row.get("treatments") or []:
            treatment_counts[t] = treatment_counts.get(t, 0) + 1

    top_concerns = sorted(concern_counts.items(), key=lambda x: (-x[1], x[0]))[:12]
    top_treatments = sorted(treatment_counts.items(), key=lambda x: (-x[1], x[0]))[:12]

    return {
        "assessment_count": len(assessments),
        "booking_count": len(bookings),
        "avg_smile_score": avg_score,
        "top_concerns": [{"label": k, "count": v} for k, v in top_concerns],
        "top_treatments": [{"label": k, "count": v} for k, v in top_treatments],
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
    sb = get_supabase()
    query = sb.table("assessments").select(
        "id,email,phone,overall_score,concerns,treatments,email_sent_at,created_at"
    )
    ascending = order.lower() == "asc"
    sort_col = sort if sort in {"created_at", "overall_score", "email"} else "created_at"
    query = query.order(sort_col, desc=not ascending).limit(limit)
    rows = list((query.execute()).data or [])

    qn = q.strip().lower()
    filtered = []
    for row in rows:
        if min_score is not None and (row.get("overall_score") is None or row["overall_score"] < min_score):
            continue
        if max_score is not None and (row.get("overall_score") is None or row["overall_score"] > max_score):
            continue
        if qn:
            blob = f"{row.get('email','')} {row.get('phone','')} {' '.join(row.get('concerns') or [])}".lower()
            if qn not in blob:
                continue
        filtered.append(row)
    return {"items": filtered}


@admin_router.get("/reports/{report_id}")
def admin_report_detail(report_id: str, _: str = Depends(require_admin)):
    _require_db()
    sb = get_supabase()
    res = sb.table("assessments").select("*").eq("id", report_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Report not found.")
    bookings = (
        sb.table("bookings")
        .select("*")
        .eq("assessment_id", report_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    return {"report": res.data[0], "bookings": bookings}


@admin_router.get("/bookings")
def admin_bookings(
    _: str = Depends(require_admin),
    q: str = Query(""),
    sort: str = Query("date"),
    order: str = Query("desc"),
    status: str = Query("confirmed"),
    limit: int = Query(200, ge=1, le=500),
):
    _require_db()
    sort_col = sort if sort in {"date", "created_at", "name", "time"} else "date"
    ascending = order.lower() == "asc"

    def _fetch():
        sb = get_supabase()
        query = sb.table("bookings").select("*")
        if status in {"confirmed", "cancelled"}:
            query = query.eq("status", status)
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
    qn = q.strip().lower()
    if qn:
        rows = [
            r
            for r in rows
            if qn
            in f"{r.get('name','')} {r.get('email','')} {r.get('phone','')} {r.get('date','')} {r.get('time','')}".lower()
        ]
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
    updates: dict[str, Any] = {}
    if body.status is not None:
        updates["status"] = body.status
    if body.note is not None:
        updates["note"] = body.note
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.date is not None:
        updates["date"] = parse_date_input(body.date)
    if body.time is not None:
        updates["time"] = parse_time_input(body.time)

    if body.date is not None or body.time is not None:
        # Validate new slot availability (ignore this booking itself).
        sb = get_supabase()
        current = (
            sb.table("bookings").select("*").eq("id", booking_id).limit(1).execute()
        ).data
        if not current:
            raise HTTPException(status_code=404, detail="Booking not found.")
        cur = current[0]
        day = updates.get("date") or cur["date"]
        slot = updates.get("time") or (str(cur["time"])[:5])
        schedules = fetch_schedules()
        existing = [
            b
            for b in fetch_bookings_for_date(day)
            if b.get("id") != booking_id
        ]
        free, _ = free_slots_for_date(schedules, date_cls.fromisoformat(day), existing)
        if slot not in free and updates.get("status", cur.get("status")) == "confirmed":
            raise HTTPException(status_code=409, detail="That slot is unavailable.")

    if not updates:
        raise HTTPException(status_code=400, detail="No changes provided.")

    sb = get_supabase()
    try:
        res = sb.table("bookings").update(updates).eq("id", booking_id).execute()
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            raise HTTPException(status_code=409, detail="Slot conflict.") from e
        raise
    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found.")
    return res.data[0]


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
    return res.data[0]


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
    res = sb.table("slot_schedules").update(row).eq("id", schedule_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return res.data[0]


@admin_router.delete("/schedules/{schedule_id}")
def admin_delete_schedule(schedule_id: str, _: str = Depends(require_admin)):
    _require_db()
    sb = get_supabase()
    res = sb.table("slot_schedules").delete().eq("id", schedule_id).execute()
    return {"ok": True, "deleted": res.data or []}
