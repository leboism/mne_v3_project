"""Tests profil ERASMUS / mobilité."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository
from mne_grade_manager.services.student_mobility import (
    MOBILITY_ERASMUS,
    MOBILITY_MNE,
    is_erasmus_student,
    normalize_mobility_type,
)


def _repo() -> Repository:
    db = Database(Path(tempfile.mkdtemp()) / "mobility_test.sqlite3")
    return Repository(db)


def test_normalize_mobility_type() -> None:
    assert normalize_mobility_type("erasmus") == MOBILITY_ERASMUS
    assert normalize_mobility_type("mobilité") == MOBILITY_ERASMUS
    assert normalize_mobility_type(None) == MOBILITY_MNE
    assert normalize_mobility_type("mne") == MOBILITY_MNE


def test_erasmus_course_enrollment_filters_results() -> None:
    repo = _repo()
    cid1 = repo.add_course("UE1", "Course 1", ects=3)
    cid2 = repo.add_course("UE2", "Course 2", ects=3)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid1, block_name="B1")
    repo.add_course_to_template(tid, cid2, block_name="B1")
    sid = repo.add_student(
        "E1",
        "",
        "",
        "Erasmus",
        "Anna",
        academic_year="2025-2026",
        mobility_type=MOBILITY_ERASMUS,
    )
    assert is_erasmus_student(repo.get_student(sid))
    repo.set_student_erasmus_courses(sid, "2025-2026", [cid1])

    summary = repo.get_student_result_summary(tid)
    row = next(r for r in summary if r["student_id"] == sid)
    assert set(row["ue_detail"].keys()) == {cid1}

    courses = repo.list_template_courses_for_student(tid, sid)
    assert [int(c["course_id"]) for c in courses] == [cid1]

    students = repo.list_students_for_template(tid)
    assert any(int(s["id"]) == sid for s in students)
