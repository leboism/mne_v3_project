"""Génération des semaines de grille emploi du temps (format secrétariat MNE)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


def _parse_academic_year(academic_year: str) -> tuple[int, int] | None:
    m = re.match(r"^(\d{4})\s*[-–]\s*(\d{4})$", str(academic_year or "").strip())
    if not m:
        return None
    y1, y2 = int(m.group(1)), int(m.group(2))
    if y2 != y1 + 1:
        return None
    return y1, y2


def _friday_after(monday: date) -> date:
    return monday + timedelta(days=4)


def generate_m1_weeks(academic_year: str, *, period: str) -> list[dict[str, Any]]:
    """
    Semaines M1 alignées sur le modèle Excel/PDF secrétariat.

    S1 : Week 36 → 52 (lundi 31/08/année1)
    S2 : Week 1 → 28 (lundi 28/12/année1)
    """
    parsed = _parse_academic_year(academic_year)
    if not parsed:
        return []
    y1, _y2 = parsed
    code = (period or "S1").strip().upper()
    out: list[dict[str, Any]] = []
    if code == "S1":
        monday = date(y1, 8, 31)
        for week_number in range(36, 53):
            out.append(
                {
                    "week_number": week_number,
                    "week_label": f"Week {week_number}",
                    "monday_date": monday.isoformat(),
                    "friday_date": _friday_after(monday).isoformat(),
                }
            )
            monday += timedelta(days=7)
    elif code == "S2":
        monday = date(y1, 12, 28)
        for week_number in range(1, 29):
            out.append(
                {
                    "week_number": week_number,
                    "week_label": f"Week {week_number}",
                    "monday_date": monday.isoformat(),
                    "friday_date": _friday_after(monday).isoformat(),
                }
            )
            monday += timedelta(days=7)
    return out


def generate_m2_weeks(academic_year: str, *, period: str) -> list[dict[str, Any]]:
    """M2 : S1 (sept.–déc.), S2 (janv.–juin), S3 (juil.–août) — structure type grille commune."""
    parsed = _parse_academic_year(academic_year)
    if not parsed:
        return []
    y1, y2 = parsed
    code = (period or "S1").strip().upper()
    out: list[dict[str, Any]] = []
    if code == "S1":
        monday = date(y1, 9, 7)
        for i in range(1, 15):
            out.append(
                {
                    "week_number": i,
                    "week_label": f"Week {i}",
                    "monday_date": monday.isoformat(),
                    "friday_date": _friday_after(monday).isoformat(),
                }
            )
            monday += timedelta(days=7)
    elif code == "S2":
        monday = date(y2, 1, 4)
        for i in range(1, 25):
            out.append(
                {
                    "week_number": i,
                    "week_label": f"Week {i}",
                    "monday_date": monday.isoformat(),
                    "friday_date": _friday_after(monday).isoformat(),
                }
            )
            monday += timedelta(days=7)
    elif code == "S3":
        monday = date(y2, 7, 6)
        for i in range(1, 10):
            out.append(
                {
                    "week_number": i,
                    "week_label": f"Week {i}",
                    "monday_date": monday.isoformat(),
                    "friday_date": _friday_after(monday).isoformat(),
                }
            )
            monday += timedelta(days=7)
    return out


def generate_weeks_for_level(academic_year: str, *, level: str, period: str) -> list[dict[str, Any]]:
    lv = (level or "M1").strip().upper()
    if lv == "M2":
        return generate_m2_weeks(academic_year, period=period)
    return generate_m1_weeks(academic_year, period=period)


def day_date_for_week(week: dict[str, Any], day_of_week: str) -> str:
    monday = week.get("monday_date") or ""
    if not monday:
        return ""
    try:
        y, m, d = (int(x) for x in str(monday).split("-"))
        base = date(y, m, d)
    except (TypeError, ValueError):
        return ""
    day = str(day_of_week or "").strip()
    if day not in _WEEKDAYS:
        return ""
    offset = _WEEKDAYS.index(day)
    return (base + timedelta(days=offset)).isoformat()
