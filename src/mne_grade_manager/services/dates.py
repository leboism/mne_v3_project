"""Dates de naissance et calcul d'âge."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def normalize_birth_date_iso(value: Any) -> str:
    """Retourne une date AAAA-MM-JJ ou '' si invalide / vide."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def normalize_time_hhmm(value: Any) -> str:
    """Retourne une heure HH:MM ou '' si invalide / vide."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    s = str(value).strip()
    if not s:
        return ""
    for fmt in ("%H:%M", "%H:%M:%S", "%Hh%M", "%H h %M"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return ""


def format_defense_slot(date_iso: str, time_hhmm: str = "") -> str:
    d = (date_iso or "").strip()
    t = (time_hhmm or "").strip()
    if d and t:
        return f"{d} {t}"
    return d or t or "—"


def age_years_from_iso(birth_iso: str) -> int | None:
    """Âge en années révolues à la date du jour."""
    birth_iso = (birth_iso or "").strip()
    if not birth_iso:
        return None
    try:
        y, m, d = (int(x) for x in birth_iso.split("-")[:3])
        born = date(y, m, d)
    except (ValueError, TypeError):
        return None
    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if age >= 0 else None


def format_age_display(birth_iso: str) -> str:
    a = age_years_from_iso(birth_iso)
    return "" if a is None else str(a)


def suggest_next_academic_year(current: str) -> str:
    """Ex. ``2025-2026`` → ``2026-2027`` ; format non reconnu → ``''``."""
    s = (current or "").strip()
    if "-" not in s:
        return ""
    a, b = s.split("-", 1)
    a, b = a.strip(), b.strip()
    if len(a) == 4 and len(b) == 4 and a.isdigit() and b.isdigit():
        return f"{int(a) + 1}-{int(b) + 1}"
    return ""
