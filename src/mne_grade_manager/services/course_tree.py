"""Regroupement des UE pour l'arborescence de l'onglet Cours."""

from __future__ import annotations

import re
from typing import Any

from ..core.mne_modules import TRACK_LETTERS, _expand_track_segment, course_ue_code, normalize_mne_module_code

_MNE_TREE_RE = re.compile(r"^M([12])B([1-5])-([A-Z0-9-]+)-[A-Z0-9]+$")

_LEVEL_ORDER = {"M1": 0, "M2": 1, "_other": 9}
_BLOCK_ORDER = {"B1": 1, "B2": 2, "B3": 3, "B4": 4, "B5": 5, "_na": 99}
_TRACK_ORDER = {"C": 0, "P": 1, "X": 2, "D": 3, "F": 4, "O": 5, "R": 6, "W": 7, "_na": 99}


def _track_group_label(level: str, segment: str) -> str:
    seg = (segment or "").strip().upper()
    if seg == "C":
        return "Commun"
    if level == "M1":
        if seg == "P":
            return "Physique (P)"
        if seg == "X":
            return "Chimie (X)"
    if level == "M2":
        letters = _expand_track_segment(seg)
        if letters == ("C",):
            return "Commun"
        if len(letters) == 1:
            ch = letters[0]
            return TRACK_LETTERS.get(ch, ch)
        names = [TRACK_LETTERS.get(ch, ch).split("(")[0].strip() for ch in letters[:3]]
        short = " / ".join(n for n in names if n)
        return short or f"Piste {seg}"
    if seg in TRACK_LETTERS:
        return TRACK_LETTERS[seg]
    return seg or "—"


def course_tree_branch(course: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """
    Retourne (level_key, level_label, block_key, block_label, track_key, track_label).
    """
    mne = course_ue_code(course) or normalize_mne_module_code(str(course.get("mne_module_code") or ""))
    m = _MNE_TREE_RE.match(mne) if mne else None
    if m:
        level = f"M{m.group(1)}"
        block_n = m.group(2)
        block_key = f"B{block_n}"
        segment = m.group(3)
        track_key = segment if segment == "C" else segment[:1] if len(segment) == 1 else segment
        level_label = f"Master {m.group(1)} (M{m.group(1)})"
        block_label = f"Bloc {block_n}"
        track_label = _track_group_label(level, segment)
        return level, level_label, block_key, block_label, track_key, track_label

    semester = str(course.get("semester") or "").strip()
    if semester:
        level_key = "_other"
        block_key = "_sem"
        track_key = semester.lower()
        return level_key, "Autres UE", block_key, semester, track_key, semester

    level_key = "_other"
    return level_key, "Autres UE", "_na", "Sans code MNE", "_na", "—"


def branch_sort_key(level_key: str, block_key: str, track_key: str) -> tuple:
    tr_ord = _TRACK_ORDER.get(track_key, 50)
    return (
        _LEVEL_ORDER.get(level_key, 99),
        _BLOCK_ORDER.get(block_key, 99),
        tr_ord,
        track_key,
    )
