"""Enregistrement groupé des décisions jury final et bilan de clôture."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "closure.sqlite3"))


def _validated_m1_setup(repo: Repository) -> tuple[int, int, int]:
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid_ok = repo.add_student("S1", "", "", "Dupont", "Alice", academic_year="2025-2026")
    sid_ko = repo.add_student("S2", "", "", "Martin", "Bob", academic_year="2025-2026")
    for sid in (sid_ok, sid_ko):
        repo.enroll_student(sid, tid)
    aid = int(repo.get_grades_for_student_course(sid_ok, cid)[0]["assessment_id"])
    repo.upsert_grade(sid_ok, aid, 14.0, status="OK")
    aid_ko = int(repo.get_grades_for_student_course(sid_ko, cid)[0]["assessment_id"])
    repo.upsert_grade(sid_ko, aid_ko, 6.0, status="OK")
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    return sid_ok, sid_ko, js, tid


def test_persist_suggested_outcomes_saves_non_visited_validated_students() -> None:
    repo = _repo()
    sid_ok, sid_ko, js, tid = _validated_m1_setup(repo)
    result = repo.persist_suggested_final_jury_outcomes(
        tid, jury_session_id=js, view_session="mixed"
    )
    assert result["saved"] == 2
    oc_ok = repo.get_jury_student_outcome(sid_ok, tid, jury_session_id=js)
    oc_ko = repo.get_jury_student_outcome(sid_ko, tid, jury_session_id=js)
    assert oc_ok and oc_ok.get("outcome") == "pass_m2"
    assert oc_ko and oc_ko.get("outcome") == "repeat"
    again = repo.persist_suggested_final_jury_outcomes(
        tid, jury_session_id=js, view_session="mixed"
    )
    assert again["saved"] == 0
    assert again["skipped"] == 2


def test_closure_status_reports_missing_and_complete() -> None:
    repo = _repo()
    sid_ok, sid_ko, js, tid = _validated_m1_setup(repo)
    before = repo.get_final_jury_closure_status(tid, jury_session_id=js)
    assert len(before["missing_outcome"]) == 2
    assert not before["decisions_complete"]
    repo.persist_suggested_final_jury_outcomes(tid, jury_session_id=js)
    after = repo.get_final_jury_closure_status(tid, jury_session_id=js)
    assert after["decisions_complete"]
    assert after["ready_for_pv"]
    assert sid_ok in [int(s["id"]) for s in repo.list_students_for_template(tid)]
