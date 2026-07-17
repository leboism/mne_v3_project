"""Règles de chevauchement et suggestions de créneaux pour l'emploi du temps."""

from __future__ import annotations

import re
from typing import Any

from .timetable_calendar import _WEEKDAYS, generate_weeks_for_level


def course_track_scope(mne_module_code: str) -> set[str]:
    """
    Portée parcours d'une UE pour les conflits horaires.

    - ``common`` : tronc commun (M1B*-C-*) — bloque P et C
    - ``P`` / ``C`` : spécialité
    """
    code = str(mne_module_code or "").strip().upper()
    if not code:
        return set()
    m = re.match(r"^M[12]B\d+-([CPXDFORW]+)-", code)
    if not m:
        return set()
    seg = m.group(1)
    if seg == "C":
        return {"common"}
    scopes: set[str] = set()
    if "P" in seg:
        scopes.add("P")
    if "X" in seg or (seg == "C" and "P" not in seg):
        scopes.add("C")
    if "C" in seg and seg != "C":
        scopes.add("common")
    return scopes or {"common"}


def display_track_for_scope(scopes: set[str], *, grid_track: str) -> bool:
    """Un créneau est visible sur la grille P/C/X selon sa portée."""
    if not scopes:
        return True
    tr = (grid_track or "").strip().upper()
    if "common" in scopes:
        return True
    if tr == "P":
        return "P" in scopes
    if tr == "C":
        return "C" in scopes
    if tr == "X":
        return "C" in scopes or "common" in scopes
    return True


def scopes_conflict(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    if "common" in a or "common" in b:
        return True
    return bool(a & b)


def slot_conflicts_with(
    candidate: dict[str, Any],
    existing: dict[str, Any],
) -> bool:
    if int(existing.get("is_cancelled") or 0):
        return False
    if int(candidate.get("week_number") or 0) != int(existing.get("week_number") or 0):
        return False
    if str(candidate.get("day_of_week") or "") != str(existing.get("day_of_week") or ""):
        return False
    if str(candidate.get("time_slot") or "") != str(existing.get("time_slot") or ""):
        return False
    cand_scope = course_track_scope(str(candidate.get("mne_module_code") or ""))
    exist_scope = course_track_scope(str(existing.get("mne_module_code") or ""))
    return scopes_conflict(cand_scope, exist_scope)


def find_slot_conflicts(
    candidate: dict[str, Any],
    slots: list[dict[str, Any]],
    *,
    exclude_slot_id: int | None = None,
) -> list[dict[str, Any]]:
    cid = int(exclude_slot_id) if exclude_slot_id is not None else None
    out: list[dict[str, Any]] = []
    for s in slots:
        if cid is not None and int(s.get("id") or 0) == cid:
            continue
        if slot_conflicts_with(candidate, s):
            out.append(s)
    return out


def suggest_next_available_slots(
    *,
    academic_year: str,
    level: str,
    period: str,
    track: str,
    mne_module_code: str,
    slots: list[dict[str, Any]],
    from_week_number: int,
    limit: int = 12,
) -> list[dict[str, str]]:
    """Prochains créneaux libres (même règles de conflit tronc commun / spécialité)."""
    weeks = generate_weeks_for_level(academic_year, level=level, period=period)
    week_nums = [int(w["week_number"]) for w in weeks if int(w["week_number"]) >= int(from_week_number)]
    time_slots = ("9:00-12:15", "1:15-4:30")
    cand_scope = course_track_scope(mne_module_code)
    suggestions: list[dict[str, str]] = []
    for wn in week_nums:
        for day in _WEEKDAYS:
            for ts in time_slots:
                candidate = {
                    "week_number": wn,
                    "day_of_week": day,
                    "time_slot": ts,
                    "mne_module_code": mne_module_code,
                    "track": track,
                }
                conflicts = find_slot_conflicts(candidate, slots)
                # Parallèle P/C autorisé : filtrer conflits réels
                real = [
                    c
                    for c in conflicts
                    if scopes_conflict(
                        cand_scope,
                        course_track_scope(str(c.get("mne_module_code") or "")),
                    )
                ]
                if real:
                    continue
                suggestions.append(
                    {
                        "week_number": str(wn),
                        "day_of_week": day,
                        "time_slot": ts,
                        "label": f"Sem. {wn} — {day} — {ts}",
                    }
                )
                if len(suggestions) >= limit:
                    return suggestions
    return suggestions
