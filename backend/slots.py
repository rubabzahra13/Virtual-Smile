"""Clinic slot generation from seasonal schedules + bookings."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _parse_time(value: Any) -> Optional[time]:
    if isinstance(value, time):
        return value
    if isinstance(value, str) and value:
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    return None


def time_to_hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def pick_schedule_for_date(schedules: list[dict], day: date) -> Optional[dict]:
    active = []
    for row in schedules:
        if row.get("active") is False:
            continue
        start = _parse_date(row.get("start_date"))
        end = _parse_date(row.get("end_date"))
        if not start or not end:
            continue
        if start <= day <= end:
            active.append(row)
    if not active:
        return None
    # Prefer the most specific (shortest) window.
    active.sort(
        key=lambda r: (
            (_parse_date(r["end_date"]) - _parse_date(r["start_date"])).days,
            r.get("label") or "",
        )
    )
    return active[0]


def generate_slots_for_date(schedules: list[dict], day: date) -> list[str]:
    schedule = pick_schedule_for_date(schedules, day)
    if not schedule:
        return []
    raw_days = schedule.get("days_of_week") or []
    days: list[int] = []
    for d in raw_days:
        try:
            days.append(int(d))
        except (TypeError, ValueError):
            continue
    if day.weekday() == 6:
        # Python: Mon=0 … Sun=6. Our schema: 0=Sun … 6=Sat
        dow = 0
    else:
        dow = day.weekday() + 1
    if dow not in days:
        return []

    open_t = _parse_time(schedule.get("open_time"))
    close_t = _parse_time(schedule.get("close_time"))
    if not open_t or not close_t:
        return []
    try:
        step = int(schedule.get("slot_minutes") or 30)
    except (TypeError, ValueError):
        step = 30
    if step <= 0:
        step = 30

    slots: list[str] = []
    cursor = datetime.combine(day, open_t)
    end = datetime.combine(day, close_t)
    while cursor <= end:
        slots.append(time_to_hhmm(cursor.time()))
        cursor += timedelta(minutes=step)
    return slots


def booked_times_set(bookings: list[dict]) -> set[str]:
    out: set[str] = set()
    for row in bookings:
        t = _parse_time(row.get("time"))
        if t:
            out.add(time_to_hhmm(t))
        elif isinstance(row.get("time"), str):
            out.add(row["time"][:5])
    return out


def free_slots_for_date(
    schedules: list[dict],
    day: date,
    bookings: list[dict],
) -> tuple[list[str], list[str]]:
    all_slots = generate_slots_for_date(schedules, day)
    booked = booked_times_set(bookings)
    free = [s for s in all_slots if s not in booked]
    return free, sorted(booked)


def month_availability(
    schedules: list[dict],
    year: int,
    month: int,
    bookings: list[dict],
) -> dict[str, dict]:
    """Return { 'YYYY-MM-DD': { free_count, booked_count, closed } } for the month."""
    from calendar import monthrange

    bookings_by_day: dict[str, list[dict]] = {}
    for row in bookings:
        d = _parse_date(row.get("date"))
        if not d:
            continue
        key = d.isoformat()
        bookings_by_day.setdefault(key, []).append(row)

    days_in_month = monthrange(year, month)[1]
    result: dict[str, dict] = {}
    for day_n in range(1, days_in_month + 1):
        day = date(year, month, day_n)
        key = day.isoformat()
        free, booked = free_slots_for_date(schedules, day, bookings_by_day.get(key, []))
        all_slots = generate_slots_for_date(schedules, day)
        result[key] = {
            "free_count": len(free),
            "booked_count": len(booked),
            "closed": len(all_slots) == 0,
            "full": len(all_slots) > 0 and len(free) == 0,
        }
    return result
