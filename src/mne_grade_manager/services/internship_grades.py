"""MCC et épreuves par défaut pour les UE de stage."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..core.mne_modules import normalize_mne_module_code
from .mcc_parser import parse_mcc_text_to_assessments_dicts

if TYPE_CHECKING:
    from .repository import Repository

# Session 1 : encadrant 50 %, rapport 25 %, soutenance 25 %.
INTERNSHIP_MCC_TEXT = (
    "MCC SESSION 1 : ENCADRANT * 50% + RAPPORT * 25% + SOUTENANCE * 25%"
)

INTERNSHIP_ASSESSMENT_KINDS: frozenset[str] = frozenset({"ENCADRANT", "RAPPORT", "SOUTENANCE"})


def is_internship_course_data(course: dict[str, Any] | None) -> bool:
    """Détecte une UE de stage (type explicite ou libellés / codes MNE usuels)."""
    if not course:
        return False
    if str(course.get("course_type") or "").strip().lower() == "internship":
        return True
    name = str(course.get("name") or "").lower()
    code = str(course.get("code") or "").lower()
    mod = str(course.get("mne_module_code") or "").upper()
    block = str(course.get("block_name") or "").upper()

    if any(token in name for token in ("stage", "internship", "internat")):
        return True
    if "stage" in code or code.endswith("-inter") or "-inter" in code:
        return True
    if mod.endswith("-INTER") or "-INTER" in mod or "INTERNSHIP" in mod:
        return True
    if "STAGE" in block or "INTERNSHIP" in block:
        return True
    return False


def internship_program_level(course: dict[str, Any] | None) -> str:
    """
    Niveau pédagogique du stage : ``M1`` ou ``M2`` (sinon ``''``).

    Le code secrétariat ``S2-C-INTER`` désigne le stage M1 ; le stage M2 est
    en général ``S4-C-INTER`` ou un libellé explicite « M2 … Internship ».
    """
    if not is_internship_course_data(course):
        return ""
    name_up = str(course.get("name") or "").upper()
    if re.search(r"\bM2\b", name_up):
        return "M2"
    if re.search(r"\bM1\b", name_up):
        return "M1"
    mne = normalize_mne_module_code(str(course.get("mne_module_code") or ""))
    if mne.startswith("M2"):
        return "M2"
    if mne.startswith("M1"):
        return "M1"
    for key in ("mne_module_code", "code"):
        raw = re.sub(r"\s+", "", str(course.get(key) or "").strip().upper())
        lm = re.match(r"^S([1-4])-([CPX])-INTER$", raw)
        if lm:
            return "M2" if int(lm.group(1)) >= 3 else "M1"
    if name_up in {"STAGE", "INTERNSHIP"} or re.match(r"^STAGE\b", name_up):
        return "M1"
    return ""


def internship_assessment_dicts() -> list[dict[str, Any]]:
    return parse_mcc_text_to_assessments_dicts(INTERNSHIP_MCC_TEXT, display_order_start=0)


def _internship_assessments_match(existing: list[dict[str, Any]]) -> bool:
    if len(existing) != 3:
        return False
    kinds = {str(a.get("kind") or "").strip().upper() for a in existing}
    return kinds == INTERNSHIP_ASSESSMENT_KINDS


def ensure_internship_mcc_and_assessments(repo: Repository, course_id: int) -> bool:
    """
    Pour une UE stage : type explicite, MCC par défaut, 3 épreuves
    (encadrant / rapport / soutenance) si absentes ou barème incorrect.
    """
    course = repo.get_course(int(course_id))
    if not course or not is_internship_course_data(course):
        return False

    cid = int(course_id)
    changed = False

    if str(course.get("course_type") or "").strip().lower() != "internship":
        repo.db.execute(
            "UPDATE courses SET course_type = 'internship' WHERE id = ?",
            (cid,),
        )
        changed = True

    mcc = str(course.get("mcc_text") or "").strip()
    if not mcc or mcc != INTERNSHIP_MCC_TEXT:
        repo.db.execute(
            "UPDATE courses SET mcc_text = ? WHERE id = ?",
            (INTERNSHIP_MCC_TEXT, cid),
        )
        changed = True
        mcc = INTERNSHIP_MCC_TEXT

    existing = repo.list_assessments(cid)
    if existing and _internship_assessments_match(existing):
        return changed

    if existing:
        repo.delete_assessments_for_course(cid)

    parsed = parse_mcc_text_to_assessments_dicts(mcc, display_order_start=0)
    if not parsed:
        parsed = internship_assessment_dicts()
    for a in parsed:
        repo.add_assessment(
            cid,
            str(a["name"]),
            str(a["kind"]),
            float(a["coefficient"]),
            int(a["session"]),
            int(a["display_order"]),
        )
    return True


def repair_internship_assessments(repo: Repository) -> int:
    """Répare toutes les UE stage de la base (type, MCC, épreuves)."""
    fixed = 0
    for row in repo.db.query_all("SELECT id FROM courses"):
        if ensure_internship_mcc_and_assessments(repo, int(row["id"])):
            fixed += 1
    return fixed
