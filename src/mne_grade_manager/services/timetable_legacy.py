"""Correspondance codes emploi du temps (S1-C-*, S2-P-*, …) → nomenclature MNE."""

from __future__ import annotations

import re

from ..core.mne_modules import MNE_MODULES_2026_2027, lookup_mne_module, normalize_mne_module_code

_LEGACY_CODE_RE = re.compile(r"^(S[1-4]-[CPX]-[A-Z0-9]+)")

# Exceptions connues (suffixe legacy ≠ acronyme MNE).
_LEGACY_OVERRIDES: dict[str, str] = {
    "S1-C-MATH": "M1B1-C-MME",
    "S1-P-QUAN": "M1B3-P-QUANT",
    "S2-P-DET": "M1B3-P-RADIOMAT",
    "S2-X-SOL": "M1B3-X-CHEM",
    "S1-X-NUMAT": "M1B3-X-NUMMATE",
    "S1-X-CHEM": "M1B3-X-CHEMNUCL",
    "S1-C-NEUT": "M1B3-P-NEUT",
    "S2-C-PROJ": "M1B2-C-PROJ",
    "S2-C-CHEM": "M1B1-C-CHEM",
    "S2-P-FLUI": "M1B3-P-FLUI",
    "S2-P-MECH": "M1B3-P-MECH",
    "S2-X-SPECT": "M1B3-X-SPECT",
    "S2-X-ANCRE": "M1B3-X-ANCRE",
    # M2 tronc commun (grilles futures)
    "S3-C-SAFE": "M2B1-C-SAFE",
    "S3-C-RP": "M2B1-C-RP",
    "S4-C-TRANS": "M2B2-C-TRANS",
    "S4-C-SYS": "M2B2-C-SYS",
    "S4-C-ENER": "M2B2-C-ENER",
}

# Préférence affichage secrétariat quand plusieurs codes legacy pointent vers le même MNE.
_MNE_TO_LEGACY_PREFERRED: dict[str, str] = {
    "M1B3-P-NEUT": "S1-P-NEUT",
}

_BY_LEGACY_SUFFIX: dict[str, list[str]] = {}


def _expand_track_letters(segment: str) -> tuple[str, ...]:
    seg = (segment or "").strip().upper()
    if seg == "C":
        return ("C",)
    if len(seg) == 1:
        return (seg,)
    return tuple(seg)


for mod in MNE_MODULES_2026_2027:
    m = re.match(r"^M([12])B(\d)-([CPXDFORW]+)-([A-Z0-9]+)$", mod.code)
    if not m:
        continue
    level, _blk, track_seg, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
    for tr in _expand_track_letters(track_seg):
        key = f"M{level}-{tr}-{suffix}"
        _BY_LEGACY_SUFFIX.setdefault(key, []).append(mod.code)


def normalize_legacy_code(raw: str) -> str:
    s = re.sub(r"\s+", "", (raw or "").strip().upper())
    m = _LEGACY_CODE_RE.match(s)
    return m.group(1) if m else ""


def legacy_period_to_level(legacy_code: str) -> str:
    c = normalize_legacy_code(legacy_code)
    if not c or not c.startswith("S") or len(c) < 2:
        return ""
    sem = c[1]
    if sem in ("1", "2"):
        return "M1"
    if sem in ("3", "4"):
        return "M2"
    return ""


def map_legacy_timetable_code(legacy_code: str) -> str:
    """
    Convertit un code EdT secrétariat (S1-C-THER, …) en code MNE (M1B1-C-THER, …).
    Retourne une chaîne vide si aucune correspondance fiable.
    """
    code = normalize_legacy_code(legacy_code)
    if not code:
        return ""
    if code in _LEGACY_OVERRIDES:
        return _LEGACY_OVERRIDES[code]

    parts = code.split("-")
    if len(parts) != 3:
        return ""
    semester, track, suffix = parts[0].upper(), parts[1].upper(), parts[2].upper()
    if not semester.startswith("S") or len(semester) != 2:
        return ""
    sem_num = semester[1]
    mne_year_digit = "1" if sem_num in ("1", "2") else "2" if sem_num in ("3", "4") else ""
    if not mne_year_digit:
        return ""

    direct_key = f"M{mne_year_digit}-{track}-{suffix}"
    candidates = _BY_LEGACY_SUFFIX.get(direct_key, [])
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        ranked = sorted(candidates, key=lambda c: (c.count("-"), c))
        return ranked[0]

    pool = [
        m.code
        for m in MNE_MODULES_2026_2027
        if m.code.startswith(f"M{mne_year_digit}") and m.code.endswith(f"-{suffix}")
    ]
    if track == "C":
        pool = [c for c in pool if "-C-" in c]
    elif track == "P":
        pool = [c for c in pool if "-P-" in c]
    elif track == "X":
        pool = [c for c in pool if "-X-" in c]
    if len(pool) == 1:
        return pool[0]
    if pool:
        return sorted(pool)[0]
    return ""


def map_mne_to_legacy_timetable_code(mne_code: str) -> str:
    """
    Convertit un code MNE (M1B1-C-THER, …) en code secrétariat / emploi du temps (S1-C-THER, …).
    """
    mne = normalize_mne_module_code(mne_code)
    if not mne:
        return ""
    if mne in _MNE_TO_LEGACY_PREFERRED:
        return _MNE_TO_LEGACY_PREFERRED[mne]
    for leg, mapped in _LEGACY_OVERRIDES.items():
        if mapped == mne:
            return leg
    m = re.match(r"^M([12])B(\d)-([A-Z0-9-]+)-([A-Z0-9]+)$", mne)
    if not m:
        return ""
    year_digit, blk, track_seg, suffix = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    if year_digit == "1":
        sem_candidates = [1] if blk == 1 else ([2] if blk == 2 else [1, 2])
    else:
        sem_candidates = [3] if blk == 1 else ([4] if blk == 2 else [3, 4])
    letters = _expand_track_letters(track_seg)
    track_candidates = ["C"] if letters == ("C",) else [ch for ch in letters if len(ch) == 1]
    if not track_candidates:
        track_candidates = ["C", "P", "X"]
    for sem in sem_candidates:
        for tr in track_candidates:
            leg = f"S{sem}-{tr}-{suffix}"
            if map_legacy_timetable_code(leg) == mne:
                return leg
    return ""


def course_public_code(course: dict, *, academic_year: str = "") -> str:
    """Code affiché : secrétariat (S1-C/P/X) pour 2025-2026, nomenclature MNE à partir de 2026-2027."""
    from ..core.mne_modules import course_ue_code, is_legacy_semester_ue_code, normalize_mne_module_code
    from .academic_years import millésime_uses_secretariat_course_codes

    use_secretariat = millésime_uses_secretariat_course_codes(academic_year)

    if use_secretariat:
        code = re.sub(r"\s+", "", str(course.get("code") or "").strip().upper())
        if _LEGACY_CODE_RE.match(code):
            return code
        for key in ("mne_module_code", "code"):
            raw = re.sub(r"\s+", "", str(course.get(key) or "").strip().upper())
            if raw and _LEGACY_CODE_RE.match(raw):
                return raw
        mne = normalize_mne_module_code(
            str(course.get("mne_module_code") or course.get("code") or "")
        )
        if mne:
            leg = map_mne_to_legacy_timetable_code(mne)
            if leg:
                return leg
        return str(course.get("code") or "").strip()

    mne = course_ue_code(course)
    if mne and not is_legacy_semester_ue_code(mne):
        return mne
    code = re.sub(r"\s+", "", str(course.get("code") or "").strip().upper())
    if _LEGACY_CODE_RE.match(code):
        mapped = map_legacy_timetable_code(code)
        if mapped:
            return mapped
    return str(course.get("code") or "").strip()


def mne_code_label(code: str) -> str:
    c = normalize_mne_module_code(code)
    mod = lookup_mne_module(c)
    if mod:
        return f"{mod.code} — {mod.title}"
    return c
