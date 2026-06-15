"""Codes de statut pour les notes (ex. absence justifiée, défaillant)."""

from __future__ import annotations

from typing import Any

STATUS_OK = "OK"
STATUS_ABJ = "ABJ"
STATUS_DEF = "DEF"
STATUS_NEUT = "NEUT"
STATUS_VAL = "VAL"

_SPECIAL_STATUSES = frozenset({STATUS_ABJ, STATUS_DEF, STATUS_NEUT, STATUS_VAL})


def normalize_grade_status(raw: Any) -> str:
    if raw is None:
        return STATUS_OK
    s = str(raw).strip().upper()
    if s in ("", STATUS_OK):
        return STATUS_OK
    if s in _SPECIAL_STATUSES:
        return s
    return s


def status_skips_average(status: Any) -> bool:
    """Statuts exclus de la moyenne pondérée (pas de 0 implicite)."""
    return normalize_grade_status(status) in (STATUS_ABJ, STATUS_NEUT, STATUS_VAL)


def status_counts_as_zero(status: Any) -> bool:
    return normalize_grade_status(status) == STATUS_DEF


def status_blocks_validation(status: Any) -> bool:
    """ABJ et DEF empêchent la validation UE / bloc (comme une note sous le seuil non gardée)."""
    return normalize_grade_status(status) in (STATUS_ABJ, STATUS_DEF)


def parse_grade_cell(text: str) -> tuple[float | None, str, str | None]:
    """
    Interprète la saisie utilisateur pour une note d’assessment.

    Retourne (grade, status, erreur). ``erreur`` non vide si saisie invalide.
    - ABJ / DEF / NEUT / VAL : pas de note numérique (grade=None).
    - Vide : efface (grade=None, OK).
    """
    raw = (text or "").strip()
    if not raw:
        return None, STATUS_OK, None
    up = raw.upper().replace(" ", "")
    if up == STATUS_ABJ:
        return None, STATUS_ABJ, None
    if up == STATUS_DEF:
        return None, STATUS_DEF, None
    if up == STATUS_NEUT:
        return None, STATUS_NEUT, None
    if up == STATUS_VAL:
        return None, STATUS_VAL, None
    try:
        v = float(raw.replace(",", "."))
    except ValueError:
        return (
            None,
            STATUS_OK,
            f"Valeur non reconnue: {raw!r} (nombre, ABJ, DEF, NEUT ou VAL)",
        )
    return v, STATUS_OK, None


def format_grade_display(
    grade: Any,
    status: Any,
    *,
    assessment_session: int | None = None,
) -> str:
    """
    Affichage dans les grilles de saisie.

    En session 2, une ligne ``grade is None`` + ``DEF`` affiche vide : en pratique ce sont
    quasi toujours d’anciennes cases laissées vides enregistrées à tort comme DEF ; la moyenne
    reprend alors la S1 via les règles MCC (reprise / fallback).
    Un vrai échec S2 se saisit à nouveau explicitement ``DEF``.
    """
    st = normalize_grade_status(status)
    if assessment_session == 2 and grade is None and st == STATUS_DEF:
        return ""
    if st in _SPECIAL_STATUSES:
        return st
    if grade is None:
        return ""
    try:
        v = float(grade)
    except (TypeError, ValueError):
        return str(grade)
    return f"{v:.3f}"
