"""
leads_api.py

Admin Leads Management API for The Global Dentist.

Exposes: GET /admin/api/leads
  - Returns all completed assessments as leads, enriched with:
    - Recommended treatments (from existing assessments.treatments)
    - Chatbot engagement status and Q&A history
    - Booking/appointment status
    - Computed lead score + Cold/Warm/Hot classification

Scoring model (configurable via LEAD_SCORING constant):
  - Assessment completed:              +10  (baseline)
  - Chatbot used (any interaction):    +20
  - Chatbot ≥ 3 questions asked:       +10  (bonus)
  - Before/After simulation generated: +20
  - Appointment booked:                +35
  - Already treated (converted):       +5   (on top of booked)
  - Assessment score < 80:             +5   (higher treatment urgency)

Thresholds:
  - Cold:  0–24
  - Warm:  25–54
  - Hot:   55+

Assessment date used for filtering: assessments.created_at
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from admin_auth import require_admin
from chat_storage import get_chat_history
from db import db_ready, db_retry, extract_concerns_treatments, get_supabase, normalize_email, normalize_phone

logger = logging.getLogger(__name__)

leads_router = APIRouter(prefix="/admin/api", tags=["admin-leads"])

# ─── Scoring configuration (weights + thresholds) ─────────────────────────────
LEAD_SCORING = {
    # Engagement signal weights
    "base":                10,   # baseline — completed assessment
    "chatbot_used":        20,   # chatbot was engaged at all
    "chatbot_deep":        10,   # ≥ 3 user questions (deep engagement)
    "simulation_viewed":   20,   # before/after simulation was generated
    "appointment_booked":  35,   # strongest conversion intent
    "already_treated":      5,   # on top of booked — already converted
    "high_treatment_need":  5,   # score < 80, evaluation required

    # Classification thresholds
    "cold_max":  24,   # ≤ 24  → Cold
    "warm_max":  54,   # ≤ 54  → Warm
                       # > 54  → Hot
}


def compute_lead_score(
    assessment: dict,
    bookings: list[dict],
    chat_history: Optional[list[dict]],
) -> dict:
    """
    Compute a lead score from existing engagement signals.

    Returns a dict with:
      - score (int)
      - status ("Cold" | "Warm" | "Hot")
      - signals (list of detected signal names for transparency)
      - score_breakdown (list of dicts: {"label": str, "points": int, "applied": bool})
    """
    score_breakdown: list[dict] = []
    score = 0
    signals: list[str] = ["assessment_completed"]

    # Base assessment completed signal
    base_points = LEAD_SCORING["base"]
    score += base_points
    score_breakdown.append({
        "label": "Completed assessment",
        "points": base_points,
        "applied": True,
    })

    # Chatbot engagement
    history = chat_history or []
    user_messages = [m for m in history if (m.get("role") or "") in ("user",)]
    chat_used_points = LEAD_SCORING["chatbot_used"]
    chat_used_applied = bool(history)
    if chat_used_applied:
        score += chat_used_points
        signals.append("chatbot_used")
    score_breakdown.append({
        "label": "Used chatbot",
        "points": chat_used_points,
        "applied": chat_used_applied,
    })

    chat_deep_points = LEAD_SCORING["chatbot_deep"]
    chat_deep_applied = len(user_messages) >= 3
    if chat_deep_applied:
        score += chat_deep_points
        signals.append("chatbot_deep_engagement")
    score_breakdown.append({
        "label": "Asked multiple chatbot questions (≥3)",
        "points": chat_deep_points,
        "applied": chat_deep_applied,
    })

    # Simulation viewed
    findings = assessment.get("findings") or {}
    sim_points = LEAD_SCORING["simulation_viewed"]
    sim_applied = False
    if isinstance(findings, dict) and findings.get("photo_simulation_path"):
        sim_applied = True
        score += sim_points
        signals.append("simulation_viewed")
    score_breakdown.append({
        "label": "Viewed before/after simulation",
        "points": sim_points,
        "applied": sim_applied,
    })

    # Booking signals
    def _booking_status(b: dict) -> str:
        from booking_api import get_booking_status
        return get_booking_status(b)

    def _is_treated(b: dict) -> bool:
        from booking_api import booking_is_treated
        return booking_is_treated(b)

    has_booking = False
    has_treated = False
    for b in bookings:
        st = _booking_status(b)
        if st in ("pending", "approved", "confirmed"):
            has_booking = True
        if _is_treated(b):
            has_treated = True

    appt_points = LEAD_SCORING["appointment_booked"]
    appt_applied = has_booking or has_treated
    if appt_applied:
        score += appt_points
        signals.append("appointment_booked")
    score_breakdown.append({
        "label": "Booked appointment",
        "points": appt_points,
        "applied": appt_applied,
    })

    treated_points = LEAD_SCORING["already_treated"]
    treated_applied = has_treated
    if treated_applied:
        score += treated_points
        signals.append("already_treated")
    score_breakdown.append({
        "label": "Marked as treated",
        "points": treated_points,
        "applied": treated_applied,
    })

    # High treatment need / urgency
    overall = assessment.get("overall_score")
    need_points = LEAD_SCORING["high_treatment_need"]
    need_applied = isinstance(overall, (int, float)) and float(overall) < 80
    if need_applied:
        score += need_points
        signals.append("high_treatment_need")
    score_breakdown.append({
        "label": "High treatment urgency (score < 80)",
        "points": need_points,
        "applied": need_applied,
    })

    # Classify
    if score <= LEAD_SCORING["cold_max"]:
        status = "Cold"
    elif score <= LEAD_SCORING["warm_max"]:
        status = "Warm"
    else:
        status = "Hot"

    return {
        "score": score,
        "status": status,
        "signals": signals,
        "score_breakdown": score_breakdown,
    }


def _parse_iso(ts: Any) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string to a UTC-aware datetime. Returns None on failure."""
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _format_time_12h(time_str: str) -> str:
    """Format HH:MM or HH:MM:SS as 12-hour AM/PM string."""
    raw = str(time_str or "").strip()
    if not raw:
        return ""
    parts = raw.split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        h = int(parts[0])
        m = int(parts[1])
        suffix = "PM" if h >= 12 else "AM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {suffix}"
    return raw


def record_lead_event(
    assessment_id: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    event_type: str = "",
    title: str = "",
    description: str = "",
    actor: str = "Patient",
    metadata: Optional[dict] = None,
) -> bool:
    """Helper to append a persistent lead event to Supabase lead_events table."""
    if not db_ready():
        return False
    sb = get_supabase()
    if not sb:
        return False
    payload = {
        "assessment_id": assessment_id or None,
        "email": normalize_email(email or ""),
        "phone": normalize_phone(phone or ""),
        "event_type": event_type,
        "title": title,
        "description": description,
        "actor": actor,
        "metadata": metadata or {},
    }
    try:
        sb.table("lead_events").insert(payload).execute()
        return True
    except Exception as e:
        logger.warning(f"Could not log lead_event: {e}")
        return False


def get_lead_history(
    assessment: dict,
    bookings: list[dict],
    persisted_events: Optional[list[dict]] = None,
) -> list[dict]:
    """
    Build a complete chronological Lead History timeline.
    Merges persisted lead_events with events dynamically reconstructed from
    assessment created_at, bookings, approvals, and treated status.

    Sorted in chronological descending order (latest event top, oldest bottom).
    """
    events: list[dict] = []
    seen_keys: set[str] = set()

    def _add_event(ev_type: str, title: str, dt_iso: str, actor: str, desc: str, meta: Optional[dict] = None):
        key = f"{ev_type}:{dt_iso[:19]}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        events.append({
            "event_type": ev_type,
            "title": title,
            "timestamp": dt_iso,
            "actor": actor,
            "description": desc,
            "metadata": meta or {},
        })

    patient_name = (assessment.get("name") or "").strip() or "Patient"

    # 1. Assessment Completed
    created_at = assessment.get("created_at")
    if created_at:
        overall = assessment.get("overall_score")
        score_desc = f"Score: {overall}/100" if overall is not None else "Smile assessment completed"
        _add_event(
            ev_type="assessment_completed",
            title="Assessment Completed",
            dt_iso=str(created_at),
            actor=patient_name,
            desc=score_desc,
        )

    # 2. Bookings events
    from booking_api import get_booking_status, booking_is_treated
    for bk in bookings:
        bk_created = bk.get("created_at") or created_at
        bk_date = bk.get("date") or ""
        raw_time = bk.get("time") or ""
        bk_time = _format_time_12h(raw_time)
        bk_source = bk.get("source") or "patient"
        actor = "Admin" if bk_source == "admin" else patient_name

        when_str = f"{bk_date} at {bk_time}" if (bk_date and bk_time) else "visit"
        _add_event(
            ev_type="appointment_booked",
            title="Appointment Booked",
            dt_iso=str(bk_created),
            actor=actor,
            desc=f"In-person appointment scheduled for {when_str}",
            meta={"date": bk_date, "time": bk_time},
        )

        st = get_booking_status(bk)
        if st in ("approved", "confirmed"):
            updated_at = bk.get("updated_at") or bk_created
            _add_event(
                ev_type="appointment_approved",
                title="Appointment Approved",
                dt_iso=str(updated_at),
                actor="Admin",
                desc=f"Appointment confirmed for {when_str}",
                meta={"date": bk_date, "time": bk_time},
            )

        if booking_is_treated(bk):
            updated_at = bk.get("updated_at") or bk_created
            _add_event(
                ev_type="patient_treated",
                title="Patient Marked as Treated",
                dt_iso=str(updated_at),
                actor="Admin",
                desc="Completed in-clinic treatment visit",
            )

    # 3. Add any additional persisted lead events
    for pe in (persisted_events or []):
        ts = pe.get("created_at")
        if ts:
            _add_event(
                ev_type=pe.get("event_type") or "activity",
                title=pe.get("title") or "Activity Logged",
                dt_iso=str(ts),
                actor=pe.get("actor") or "Admin",
                desc=pe.get("description") or "",
                meta=pe.get("metadata") or {},
            )

    # Sort descending by parsed timestamp (latest first)
    def _sort_key(e: dict):
        dt = _parse_iso(e.get("timestamp"))
        return dt.timestamp() if dt else 0.0

    events.sort(key=_sort_key, reverse=True)
    return events


@leads_router.get("/leads")
def admin_leads(
    _: str = Depends(require_admin),
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD (inclusive, uses assessment created_at)"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD (inclusive, uses assessment created_at)"),
    status_filter: Optional[str] = Query(None, alias="status", description="Cold | Warm | Hot | all"),
    chatbot_filter: Optional[str] = Query(None, alias="chatbot", description="used | not_used | all"),
    limit: int = Query(500, ge=1, le=2000),
):
    """
    Return all completed assessments as leads, enriched with lead score and classification.

    Date filtering uses assessments.created_at (assessment completion timestamp).
    """
    if not db_ready():
        raise HTTPException(
            status_code=503,
            detail="Database is not configured.",
        )

    sb = get_supabase()

    # ── Fetch assessments (with optional date pre-filter at DB level) ──────
    def _fetch_assessments():
        q = sb.table("assessments").select("*").order("created_at", desc=True).limit(limit)
        if date_from:
            try:
                q = q.gte("created_at", f"{date_from}T00:00:00+00:00")
            except Exception:
                pass
        if date_to:
            try:
                q = q.lte("created_at", f"{date_to}T23:59:59+00:00")
            except Exception:
                pass
        return q.execute()

    try:
        assessments_res = db_retry(_fetch_assessments, label="leads: assessments")
        assessments: list[dict] = list(assessments_res.data or [])
    except Exception:
        logger.exception("Leads: failed to fetch assessments")
        raise HTTPException(status_code=503, detail="Could not load leads. Please try again.")

    if not assessments:
        return {"items": [], "total": 0, "scoring_config": LEAD_SCORING}

    # ── Fetch all bookings in one call ─────────────────────────────────────
    assessment_ids = [str(a["id"]) for a in assessments if a.get("id")]
    all_emails = list({normalize_email(a.get("email") or "") for a in assessments if a.get("email")})
    all_phones = list({normalize_phone(a.get("phone") or "") for a in assessments if a.get("phone")})

    bookings_by_assessment: dict[str, list[dict]] = {}
    bookings_by_email: dict[str, list[dict]] = {}
    bookings_by_phone: dict[str, list[dict]] = {}

    try:
        if assessment_ids:
            bk_res = db_retry(
                lambda: sb.table("bookings")
                .select("*")
                .in_("assessment_id", assessment_ids[:500])
                .execute(),
                label="leads: bookings by id",
            )
            for bk in bk_res.data or []:
                aid = str(bk.get("assessment_id") or "")
                if aid:
                    bookings_by_assessment.setdefault(aid, []).append(bk)

        if all_emails:
            for chunk_start in range(0, len(all_emails), 50):
                chunk = all_emails[chunk_start : chunk_start + 50]
                bk_email_res = db_retry(
                    lambda: sb.table("bookings")
                    .select("*")
                    .in_("email", chunk)
                    .execute(),
                    label="leads: bookings by email chunk",
                )
                for bk in bk_email_res.data or []:
                    em = normalize_email(bk.get("email") or "")
                    if em:
                        bookings_by_email.setdefault(em, []).append(bk)
    except Exception:
        logger.warning("Leads: could not fetch bookings (non-fatal)")

    # ── Fetch persisted lead events if table exists ─────────────────────────
    events_by_assessment: dict[str, list[dict]] = {}
    try:
        if assessment_ids:
            ev_res = sb.table("lead_events").select("*").in_("assessment_id", assessment_ids[:500]).execute()
            for ev in ev_res.data or []:
                aid = str(ev.get("assessment_id") or "")
                if aid:
                    events_by_assessment.setdefault(aid, []).append(ev)
    except Exception:
        pass  # lead_events table optional / fallback to dynamic reconstruction

    # ── Build leads ────────────────────────────────────────────────────────
    sf = (status_filter or "all").strip().lower()
    cf = (chatbot_filter or "all").strip().lower()
    items: list[dict] = []

    for assessment in assessments:
        aid = str(assessment.get("id") or "")
        email_n = normalize_email(assessment.get("email") or "")
        phone_n = normalize_phone(assessment.get("phone") or "")

        # Merge bookings from all lookup paths (deduplicated by id)
        seen_booking_ids: set[str] = set()
        merged_bookings: list[dict] = []
        for src in (
            bookings_by_assessment.get(aid, []),
            bookings_by_email.get(email_n, []),
            bookings_by_phone.get(phone_n, []),
        ):
            for bk in src:
                bk_id = str(bk.get("id") or "")
                if bk_id and bk_id not in seen_booking_ids:
                    seen_booking_ids.add(bk_id)
                    merged_bookings.append(bk)

        # Chat history
        chat_history = get_chat_history(
            assessment_id=aid or None,
            email=email_n or None,
            phone=phone_n or None,
        )

        user_msgs = [m for m in (chat_history or []) if (m.get("role") or "") == "user"]
        chatbot_engaged = bool(chat_history)
        chatbot_question_count = len(user_msgs)

        # Chatbot filter
        if cf == "used" and not chatbot_engaged:
            continue
        if cf == "not_used" and chatbot_engaged:
            continue

        # Score
        lead_info = compute_lead_score(assessment, merged_bookings, chat_history)

        # Status filter
        if sf not in ("all", "", "none") and lead_info["status"].lower() != sf:
            continue

        # Concerns & Treatments
        concerns, extracted_tx = extract_concerns_treatments(assessment.get("findings") or {})
        treatments: list[str] = list(assessment.get("treatments") or [])
        if not treatments:
            treatments = extracted_tx
        concerns_count = len(concerns)
        primary_tx = treatments[0] if treatments else "General Smile Consultation"

        # Appointment info
        appointment_date = None
        appointment_time = None
        appointment_status = None
        for bk in sorted(merged_bookings, key=lambda b: str(b.get("created_at") or ""), reverse=True):
            from booking_api import get_booking_status
            bk_st = get_booking_status(bk)
            if appointment_date is None and bk.get("date"):
                appointment_date = str(bk["date"])
                appointment_time = str(bk.get("time") or "")[:5]
                appointment_status = bk_st

        # Lead History events
        persisted_evs = events_by_assessment.get(aid, [])
        lead_history = get_lead_history(assessment, merged_bookings, persisted_evs)

        item = {
            # Identity
            "id": aid,
            "name": (assessment.get("name") or "").strip() or None,
            "email": assessment.get("email") or "",
            "phone": assessment.get("phone") or "",
            "gender": assessment.get("gender") or "",
            "age": assessment.get("age"),
            "city": assessment.get("city") or "",
            # Assessment
            "assessment_date": assessment.get("created_at"),  # ← source of truth for filtering
            "overall_score": assessment.get("overall_score"),
            "concerns_count": concerns_count,
            "concerns_list": concerns,
            "primary_treatment": primary_tx,
            "treatments": treatments,
            # Chatbot
            "chatbot_engaged": chatbot_engaged,
            "chatbot_status": "Used" if chatbot_engaged else "Not Used",
            "chatbot_question_count": chatbot_question_count,
            "chat_history": chat_history or [],
            # Appointment
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "appointment_status": appointment_status,
            # Lead qualification
            "lead_score": lead_info["score"],
            "lead_status": lead_info["status"],
            "lead_signals": lead_info["signals"],
            "score_breakdown": lead_info["score_breakdown"],
            "lead_history": lead_history,
        }
        items.append(item)

    return {
        "items": items,
        "total": len(items),
        "scoring_config": LEAD_SCORING,
    }

