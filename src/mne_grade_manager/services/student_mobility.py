"""Profil mobilité / ERASMUS (étudiants hors parcours MNE complet)."""

from __future__ import annotations

from typing import Any

MOBILITY_MNE = "mne"
MOBILITY_ERASMUS = "erasmus"

MOBILITY_CHOICES: tuple[tuple[str, str], ...] = (
    (MOBILITY_MNE, "Étudiant MNE (parcours complet)"),
    (MOBILITY_ERASMUS, "ERASMUS / mobilité (cours à la carte)"),
)


def normalize_mobility_type(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    if v in {MOBILITY_ERASMUS, "erasmus", "mobility", "mobilite", "mobilité"}:
        return MOBILITY_ERASMUS
    return MOBILITY_MNE


def is_erasmus_student(student: dict[str, Any] | None) -> bool:
    if not student:
        return False
    return normalize_mobility_type(student.get("mobility_type")) == MOBILITY_ERASMUS


def mobility_label_fr(raw: Any) -> str:
    return dict(MOBILITY_CHOICES).get(normalize_mobility_type(raw), "MNE")
