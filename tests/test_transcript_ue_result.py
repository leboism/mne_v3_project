"""Colonne Result des transcripts : Passed / Compensated / Validated / Failed."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.jury_reports import _transcript_ue_result
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "tr_res.sqlite3"))


def _setup_block_with_two_ues(repo: Repository) -> tuple[int, int, int, dict]:
    ay = "2025-2026"
    c_ok = repo.add_course("UE-OK", "UE OK", ects=3)
    c_mid = repo.add_course("UE-MID", "UE MID", ects=3)
    for cid in (c_ok, c_mid):
        repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("M1 C", "M1", "C", ay, "1")
    repo.add_course_to_template(tid, c_ok, block_name="Bloc 1")
    repo.add_course_to_template(tid, c_mid, block_name="Bloc 1")
    sid = repo.add_student("S1", "", "", "Test", "Student", academic_year=ay, level="M1", track="C")
    repo.enroll_student(sid, tid)
    a_ok = int(repo.get_grades_for_student_course(sid, c_ok)[0]["assessment_id"])
    a_mid = int(repo.get_grades_for_student_course(sid, c_mid)[0]["assessment_id"])
    repo.upsert_grade(sid, a_ok, 15.0, status="OK")
    repo.upsert_grade(sid, a_mid, 8.0, status="OK")
    row = repo.get_student_result_summary(tid, view_session="mixed", include_all_students=True)[0]
    course_mid = next(c for c in repo.list_template_courses(tid) if int(c["course_id"]) == c_mid)
    return sid, tid, c_mid, row, course_mid


def test_transcript_ue_result_compensated_between_7_and_10() -> None:
    repo = _repo()
    sid, tid, c_mid, row, course = _setup_block_with_two_ues(repo)
    result = _transcript_ue_result(
        repo,
        student_id=sid,
        template_id=tid,
        course=course,
        row=row,
        view_session="mixed",
    )
    assert result == "Compensated"


def test_transcript_ue_result_validated_below_7_with_waiver() -> None:
    repo = _repo()
    sid, tid, c_mid, row, course = _setup_block_with_two_ues(repo)
    repo.set_ue_jury_floor_waiver(sid, tid, c_mid, waived=True)
    result = _transcript_ue_result(
        repo,
        student_id=sid,
        template_id=tid,
        course=course,
        row=row,
        view_session="mixed",
    )
    assert result == "Validated"
