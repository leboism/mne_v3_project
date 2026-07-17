"""Tests MCC et épreuves stage."""

from __future__ import annotations

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.internship_grades import (
    INTERNSHIP_MCC_TEXT,
    ensure_internship_mcc_and_assessments,
    internship_assessment_dicts,
    internship_program_level,
    is_internship_course_data,
)
from mne_grade_manager.services.repository import Repository


def test_internship_mcc_parses_three_assessments() -> None:
    specs = internship_assessment_dicts()
    assert len(specs) == 3
    kinds = {s["kind"] for s in specs}
    assert kinds == {"ENCADRANT", "RAPPORT", "SOUTENANCE"}
    coefs = sorted(float(s["coefficient"]) for s in specs)
    assert coefs == [25.0, 25.0, 50.0]


def test_detects_s2_c_inter_internship_by_name_and_code() -> None:
    assert is_internship_course_data(
        {"name": "Internship", "code": "S2-C-INTER", "course_type": "standard"}
    )
    assert is_internship_course_data(
        {"name": "Stage M2", "code": "STAGE-M2", "mne_module_code": "S2-C-INTER"}
    )
    assert not is_internship_course_data({"name": "Nuclear physics", "code": "NE101"})


def test_internship_program_level_m1_vs_m2() -> None:
    assert internship_program_level({"name": "Stage", "mne_module_code": "S2-C-INTER"}) == "M1"
    assert (
        internship_program_level(
            {
                "name": "M2 Nuclear Engineering Internship",
                "mne_module_code": "S2-C-INTER",
                "course_type": "internship",
            }
        )
        == "M2"
    )
    assert internship_program_level({"name": "Internship", "mne_module_code": "S4-C-INTER"}) == "M2"


def test_ensure_internship_assessments_for_s2_c_inter(tmp_path) -> None:
    db = Database(tmp_path / "stage.sqlite3")
    repo = Repository(db)
    cid = repo.add_course(
        "S2-C-INTER",
        "Internship",
        30.0,
        course_type="standard",
    )
    assert ensure_internship_mcc_and_assessments(repo, cid)
    course = repo.get_course(cid) or {}
    assert str(course.get("course_type") or "") == "internship"
    assert INTERNSHIP_MCC_TEXT in str(course.get("mcc_text") or "")
    assessments = repo.list_assessments(cid)
    assert len(assessments) == 3
    kinds = {a["kind"] for a in assessments}
    assert kinds == {"ENCADRANT", "RAPPORT", "SOUTENANCE"}
