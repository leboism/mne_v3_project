"""Statut scolarité étudiant (actif / démissionnaire / diplômé)."""

from __future__ import annotations

from typing import Any

STUDENT_STATUS_ACTIVE = "active"
STUDENT_STATUS_WITHDRAWN = "withdrawn"
STUDENT_STATUS_GRADUATED = "graduated"


def normalize_student_status(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    if v in {STUDENT_STATUS_WITHDRAWN, "demission", "demissionnaire", "resigned"}:
        return STUDENT_STATUS_WITHDRAWN
    if v in {
        STUDENT_STATUS_GRADUATED,
        "diplome",
        "diplômé",
        "diplomee",
        "graduated",
        "completed",
        "termine",
        "terminé",
    }:
        return STUDENT_STATUS_GRADUATED
    return STUDENT_STATUS_ACTIVE


def is_student_active(student: dict[str, Any]) -> bool:
    return normalize_student_status(student.get("status")) == STUDENT_STATUS_ACTIVE


def student_status_label_fr(raw: Any) -> str:
    status = normalize_student_status(raw)
    if status == STUDENT_STATUS_WITHDRAWN:
        return "Démissionnaire"
    if status == STUDENT_STATUS_GRADUATED:
        return "Diplômé"
    return "Actif"


def sql_student_is_active(alias: str = "students") -> str:
    """Fragment SQL : vrai si l'étudiant est inscrit activement (non démissionnaire, non diplômé)."""
    return (
        f"COALESCE(NULLIF(TRIM({alias}.status), ''), '{STUDENT_STATUS_ACTIVE}') "
        f"= '{STUDENT_STATUS_ACTIVE}'"
    )
