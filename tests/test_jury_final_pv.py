"""PV jury final : synthèse complète et décisions auto-suggérées."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.jury_reports import write_institutional_pv_pdf
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "jury_final_pv.sqlite3"))


def _validated_student(repo: Repository) -> tuple[int, int, int, int]:
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
    repo.upsert_jury_student_outcome(sid_ok, tid, jury_session_id=js, outcome="pass_m2", mention="bien")
    return sid_ok, sid_ko, js, tid


def test_final_pv_includes_all_students_even_without_saved_outcome() -> None:
    repo = _repo()
    sid_ok, sid_ko, js, tid = _validated_student(repo)
    out = Path(tempfile.mkdtemp()) / "pv_final.pdf"
    write_institutional_pv_pdf(
        repo,
        template_id=tid,
        jury_session_id=js,
        view_session="s1",
        path=out,
    )
    assert out.is_file()
    assert out.stat().st_size > 500

    oc_ok = repo.get_jury_student_outcome(sid_ok, tid, jury_session_id=js)
    assert oc_ok and oc_ok.get("outcome") == "pass_m2"
    oc_ko = repo.get_jury_student_outcome(sid_ko, tid, jury_session_id=js)
    assert oc_ko is None or not str(oc_ko.get("outcome") or "").strip()

    ev = repo.evaluate_student_year_validation(sid_ko, tid, view_session="s1")
    assert not ev.get("validated")
    retake = repo.courses_to_retake_for_student(sid_ko, tid, view_session="s1")
    assert retake["mandatory"]


def test_repeat_progression_clears_mandatory_retake_courses() -> None:
    repo = _repo()
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid = repo.add_student(
        "S1", "", "", "Martin", "Bob",
        academic_year="2025-2026", level="M1", track="C",
    )
    repo.enroll_student(sid, tid)
    aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
    repo.upsert_grade(sid, aid, 6.0, status="OK")
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(sid, tid, jury_session_id=js, outcome="repeat")
    repo.add_template("2026-2027 M1 C", "M1", "C", "2026-2027", "1")
    repo.apply_final_jury_progression(
        sid, tid, jury_session_id=js, new_academic_year="2026-2027"
    )
    grades = repo.get_grades_for_student_course(sid, cid)
    assert not grades or all(g.get("grade") is None for g in grades)


def test_repeat_progression_enrolls_student_in_next_year_template() -> None:
    repo = _repo()
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid = repo.add_student(
        "S1", "", "", "Martin", "Bob",
        academic_year="2025-2026", level="M1", track="C",
    )
    repo.enroll_student(sid, tid)
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(sid, tid, jury_session_id=js, outcome="repeat")
    tid_next = repo.add_template("2026-2027 M1 C", "M1", "C", "2026-2027", "1")
    repo.add_course_to_template(tid_next, cid, block_name="Bloc 1")
    repo.apply_final_jury_progression(
        sid, tid, jury_session_id=js, new_academic_year="2026-2027"
    )
    s = repo.get_student(sid)
    assert s.get("academic_year") == "2026-2027"
    assert s.get("level") == "M1"
    assert s.get("track") == "C"
    enrolled = [int(x["id"]) for x in repo.list_students_for_template(tid_next)]
    assert sid in enrolled


def test_repeat_progression_normalizes_m1c_track_code() -> None:
    repo = _repo()
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid = repo.add_student(
        "S1", "", "", "Martin", "Bob",
        academic_year="2025-2026", level="M1", track="M1C",
    )
    repo.enroll_student(sid, tid)
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(sid, tid, jury_session_id=js, outcome="repeat")
    tid_next = repo.add_template("2026-2027 M1 C", "M1", "C", "2026-2027", "1")
    repo.add_course_to_template(tid_next, cid, block_name="Bloc 1")
    repo.apply_final_jury_progression(
        sid, tid, jury_session_id=js, new_academic_year="2026-2027"
    )
    s = repo.get_student(sid)
    assert s.get("track") == "C"
    enrolled = [int(x["id"]) for x in repo.list_students_for_template(tid_next)]
    assert sid in enrolled
