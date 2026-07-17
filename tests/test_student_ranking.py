"""Classement parcours vs cohorte."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "rank.sqlite3"))


def _setup_course(repo: Repository, code: str) -> int:
    cid = repo.add_course(code, code, ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    return cid


def test_track_rank_within_parcours_only() -> None:
    repo = _repo()
    cid = _setup_course(repo, "UE-1")
    tid = repo.add_template("M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    s_top = repo.add_student("S1", "", "", "Alpha", "A", academic_year="2025-2026")
    s_mid = repo.add_student("S2", "", "", "Beta", "B", academic_year="2025-2026")
    repo.enroll_student(s_top, tid)
    repo.enroll_student(s_mid, tid)
    for sid, grade in ((s_top, 16.0), (s_mid, 14.0)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, grade, status="OK")

    assert repo.student_track_rank(tid, s_top, view_session="s2") == 1
    assert repo.student_track_rank(tid, s_mid, view_session="s2") == 2


def test_cohort_rank_across_parcours() -> None:
    repo = _repo()
    cid_c = _setup_course(repo, "UE-C")
    cid_p = _setup_course(repo, "UE-P")
    tid_c = repo.add_template("M1 C", "M1", "C", "2025-2026", "1")
    tid_p = repo.add_template("M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid_c, cid_c, block_name="Bloc C")
    repo.add_course_to_template(tid_p, cid_p, block_name="Bloc P")

    s_c = repo.add_student("C1", "", "", "Core", "One", academic_year="2025-2026")
    s_p_best = repo.add_student("P1", "", "", "Phys", "Top", academic_year="2025-2026")
    s_p_mid = repo.add_student("P2", "", "", "Phys", "Mid", academic_year="2025-2026")
    repo.enroll_student(s_c, tid_c)
    repo.enroll_student(s_p_best, tid_p)
    repo.enroll_student(s_p_mid, tid_p)

    for sid, cid, grade in (
        (s_c, cid_c, 15.0),
        (s_p_best, cid_p, 17.0),
        (s_p_mid, cid_p, 13.0),
    ):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, grade, status="OK")

    assert repo.student_track_rank(tid_p, s_p_best, view_session="s2") == 1
    assert repo.student_track_rank(tid_p, s_p_mid, view_session="s2") == 2
    assert repo.student_cohort_rank(tid_p, s_p_best, view_session="s2") == 1
    assert repo.student_cohort_rank(tid_p, s_p_mid, view_session="s2") == 3
    assert repo.student_cohort_rank(tid_c, s_c, view_session="s2") == 2
