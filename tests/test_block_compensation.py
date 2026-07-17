"""Compensation intra-bloc : pas de compensable si une UE est éliminatoire."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    db = Database(Path(tempfile.mkdtemp()) / "block_comp.sqlite3")
    return Repository(db)


def _block_with_two_ues(repo: Repository) -> tuple[int, int, int, int]:
    cid_ok = repo.add_course("Radio", "Radiation", ects=3)
    repo.add_assessment(cid_ok, "EE", "EE", 100.0, session=1)
    cid_fail = repo.add_course("Math", "Math", ects=3)
    repo.add_assessment(cid_fail, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid_ok, block_name="B1")
    repo.add_course_to_template(tid, cid_fail, block_name="B1")
    sid = repo.add_student("S1", "", "", "Test", "Student", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aid_ok = int(repo.get_grades_for_student_course(sid, cid_ok)[0]["assessment_id"])
    aid_fail = int(repo.get_grades_for_student_course(sid, cid_fail)[0]["assessment_id"])
    repo.upsert_grade(sid, aid_ok, 9.3, status="OK")
    repo.upsert_grade(sid, aid_fail, 5.0, status="OK")
    return sid, tid, cid_ok, cid_fail


def test_block_compensation_blocked_by_failing_sibling() -> None:
    repo = _repo()
    sid, tid, cid_ok, cid_fail = _block_with_two_ues(repo)
    summary = repo.get_student_result_summary(tid, view_session="s1")
    row = next(r for r in summary if r["student_id"] == sid)

    assert repo.block_ue_compensation_status(
        sid, tid, cid_ok, result_row=row, view_session="s1"
    ) == "eliminating"
    assert not repo.block_allows_ue_compensation(
        sid, tid, cid_ok, result_row=row, view_session="s1"
    )
    assert repo.block_ue_compensation_status(
        sid, tid, cid_fail, result_row=row, view_session="s1"
    ) == "eliminating"
