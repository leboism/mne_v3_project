"""Regroupement des UE pour l'arborescence de l'onglet Cours."""

from __future__ import annotations

import re
from typing import Any

from ..core.mne_modules import (
    TRACK_LETTERS,
    _expand_track_segment,
    course_ue_code,
    infer_maquette_block_number,
    is_legacy_semester_ue_code,
    normalize_mne_module_code,
)
from .internship_grades import internship_program_level

_MNE_TREE_RE = re.compile(r"^M([12])B([1-5])-([A-Z0-9-]+)-[A-Z0-9]+$")
_LEGACY_SEC_RE = re.compile(r"^S([1-4])-([CPX])-", re.IGNORECASE)

_LEVEL_ORDER = {"M1": 0, "M2": 1, "_other": 9}
_BLOCK_ORDER = {"B1": 1, "B2": 2, "B3": 3, "B4": 4, "B5": 5, "_na": 99}
_TRACK_ORDER = {"C": 0, "P": 1, "X": 2, "D": 3, "F": 4, "O": 5, "R": 6, "W": 7, "_na": 99}

# Maquette 2025-2026 : blocs 1 et 3 communs, blocs 2 et 4 spécialisés P/X.
_SECRETARIAT_COMMON_BLOCKS = frozenset({1, 3})

# Indices M2 dans libellés maquette / intitulés (tronc commun EN000021xx, parcours…).
_M2_LEVEL_HINT = re.compile(
    r"NPD|NDWM|NFC|NPO|NRPE|DWM|"
    r"BLOC\s*3\s+(NPD|NPO|NFC|NRPE|NDWM)|"
    r"BLOC\s*4\s+(NPD|NPO|NFC|NRPE|NDWM)|"
    r"\(NPD[-/]|\(NFC[-/]|\(NPO[-/]|\(NRPE|"
    r"NDWM/NFC/NPO/NPD",
    re.IGNORECASE,
)


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


def _branch_from_m1_m2_block(
    level: str, block_n: int, track_letter: str
) -> tuple[str, str, str, str, str, str]:
    tr = (track_letter or "C").strip().upper()[:1] or "C"
    level_label = f"Master {level[-1]} ({level})"
    block_key = f"B{block_n}"
    block_label = f"Bloc {block_n}"
    track_label = _track_group_label(level, tr)
    return level, level_label, block_key, block_label, tr, track_label


def _track_from_course_codes(course: dict[str, Any]) -> str:
    mne = course_ue_code(course) or normalize_mne_module_code(str(course.get("mne_module_code") or ""))
    m = _MNE_TREE_RE.match(mne) if mne else None
    if m:
        segment = m.group(3)
        if segment == "C":
            return "C"
        return segment[:1] if segment else "C"

    for key in ("mne_module_code", "code"):
        raw = re.sub(r"\s+", "", str(course.get(key) or "").strip().upper())
        if is_legacy_semester_ue_code(raw):
            lm = _LEGACY_SEC_RE.match(raw)
            if lm:
                return lm.group(2).upper()
    return "C"


def _maquette_block_number(course: dict[str, Any]) -> int | None:
    maquette_block = str(course.get("maquette_block") or "").strip()
    if not maquette_block:
        return None
    blk = infer_maquette_block_number(maquette_block, "M1")
    if not blk:
        blk = infer_maquette_block_number(maquette_block, "M2")
    return blk


def _infer_program_level(course: dict[str, Any], maquette_block: str = "") -> str:
    """Déduit M1 / M2 depuis le code MNE, le libellé ou le bloc maquette."""
    mne = course_ue_code(course) or normalize_mne_module_code(
        str(course.get("mne_module_code") or "")
    )
    if mne.startswith("M2"):
        return "M2"
    if mne.startswith("M1"):
        return "M1"

    stage_lv = internship_program_level(course)
    if stage_lv:
        return stage_lv

    name_up = str(course.get("name") or "").upper()
    mb = str(maquette_block or course.get("maquette_block") or "").upper()
    blob = f"{name_up} {mb}"
    if _M2_LEVEL_HINT.search(blob):
        return "M2"
    if "M2" in mb or re.search(r"\bM2\b", name_up):
        return "M2"
    return "M1"


def _level_from_maquette_block(maquette_block: str, course: dict[str, Any]) -> str:
    return _infer_program_level(course, maquette_block)


def _branch_from_secretariat_maquette(
    course: dict[str, Any],
) -> tuple[str, str, str, str, str, str] | None:
    """
    Millésime 2025-2026 : le bloc affiché suit la maquette (1/3 communs, 2/4 P/X),
    pas le numéro de bloc du code catalogue MNE (M1B3 ≠ bloc maquette 2).
    """
    maquette_block = str(course.get("maquette_block") or "").strip()
    blk = _maquette_block_number(course)
    if not blk:
        return None

    if blk in _SECRETARIAT_COMMON_BLOCKS:
        track = "C"
    elif blk in (2, 4):
        track = _track_from_course_codes(course)
    else:
        track = "C"

    level = _level_from_maquette_block(maquette_block, course)
    return _branch_from_m1_m2_block(level, blk, track)


def _branch_from_legacy_or_maquette(course: dict[str, Any]) -> tuple[str, str, str, str, str, str] | None:
    """Codes secrétariat (S1-C-LANG, …) sans bloc maquette explicite."""
    maquette_block = str(course.get("maquette_block") or "").strip()
    blk_from_maquette = _maquette_block_number(course)

    legacy_raw = ""
    for key in ("mne_module_code", "code"):
        raw = re.sub(r"\s+", "", str(course.get(key) or "").strip().upper())
        if is_legacy_semester_ue_code(raw):
            legacy_raw = raw
            break

    if legacy_raw:
        lm = _LEGACY_SEC_RE.match(legacy_raw)
        if lm:
            sem = int(lm.group(1))
            tr = lm.group(2).upper()
            if legacy_raw.endswith("-INTER"):
                stage_lv = internship_program_level(course)
                if stage_lv:
                    blk = blk_from_maquette
                    if blk is None:
                        blk = 5 if stage_lv == "M2" else 4
                    return _branch_from_m1_m2_block(stage_lv, int(blk), tr)
            level = "M1" if sem <= 2 else "M2"
            blk = blk_from_maquette
            if blk is None:
                blk = {1: 1, 2: 2, 3: 1, 4: 4}.get(sem, 1)
            return _branch_from_m1_m2_block(level, int(blk), tr)

    stage_lv = internship_program_level(course)
    if stage_lv:
        blk = blk_from_maquette
        if blk is None:
            blk = 5 if stage_lv == "M2" else 4
        tr = _track_from_course_codes(course)
        return _branch_from_m1_m2_block(stage_lv, int(blk), tr)

    if blk_from_maquette:
        level = _level_from_maquette_block(maquette_block, course)
        return _branch_from_m1_m2_block(level, int(blk_from_maquette), "C")

    return None


def course_tree_branch(
    course: dict[str, Any],
    *,
    academic_year: str | None = None,
) -> tuple[str, str, str, str, str, str]:
    """
    Retourne (level_key, level_label, block_key, block_label, track_key, track_label).
    """
    from .academic_years import millésime_uses_secretariat_course_codes

    ay = str(academic_year or course.get("academic_year") or "").strip()
    if ay and millésime_uses_secretariat_course_codes(ay):
        secretariat_branch = _branch_from_secretariat_maquette(course)
        if secretariat_branch is not None:
            return secretariat_branch

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

    legacy_branch = _branch_from_legacy_or_maquette(course)
    if legacy_branch is not None:
        return legacy_branch

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
