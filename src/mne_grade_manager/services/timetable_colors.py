"""Couleurs de grille emploi du temps (palette proche du fichier secrétariat)."""

from __future__ import annotations

import re

# Tronc commun, physique (P), chimie (X), événements, examens.
_DEFAULT_BY_SCOPE: dict[str, str] = {
    "common": "FBE5D6",  # orange clair — blocs 1–2 communs
    "physics": "D9E2F3",  # bleu — spécialité P
    "chemistry": "E2EFDA",  # vert — spécialité C / X
    "exam": "FFC7CE",  # rose — examens
    "holiday": "D9D9D9",  # gris — vacances / fériés
    "event": "FFF2CC",  # jaune — séminaires, visites
    "other": "EDEDED",
}


def course_color_scope(mne_module_code: str) -> str:
    code = str(mne_module_code or "").strip().upper()
    if not code:
        return "other"
    m = re.match(r"^M[12]B\d+-([CPXDFORW]+)-", code)
    if not m:
        return "other"
    track = m.group(1)
    if "C" in track and track == "C":
        return "common"
    if "P" in track and "C" not in track and "X" not in track:
        return "physics"
    if "X" in track:
        return "chemistry"
    if "C" in track:
        return "common"
    return "other"


def fill_color_for_slot(
    *,
    mne_module_code: str = "",
    slot_kind: str = "",
) -> str:
    kind = str(slot_kind or "").strip().lower()
    if kind == "exam":
        return _DEFAULT_BY_SCOPE["exam"]
    if kind == "holiday":
        return _DEFAULT_BY_SCOPE["holiday"]
    if kind in {"event", "seminar", "visit"}:
        return _DEFAULT_BY_SCOPE["event"]
    scope = course_color_scope(mne_module_code)
    return _DEFAULT_BY_SCOPE.get(scope, _DEFAULT_BY_SCOPE["other"])
