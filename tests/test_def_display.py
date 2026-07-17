"""DEF doit s'afficher comme statut, pas comme 0 + jury (ex. −0,1)."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.grade_status import STATUS_ABJ, STATUS_DEF
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    db = Database(Path(tempfile.mkdtemp()) / "def_display.sqlite3")
    return Repository(db)


def _setup_math_ue(repo: Repository) -> tuple[int, int, int, int]:
    cid = repo.add_course("Math", "Mathématiques", ects=4)
    repo.add_assessment(cid, "Examen", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("S1", "", "", "Test", "Student", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    rows = repo.get_grades_for_student_course(sid, cid)
    aid = int(rows[0]["assessment_id"])
    repo.upsert_grade(sid, aid, None, status=STATUS_DEF)
    return sid, tid, cid, aid


def test_sync_second_session_clears_when_def_removed() -> None:
    repo = _repo()
    sid, tid, cid, aid = _setup_math_ue(repo)
    repo.sync_second_session_obligations(tid)
    assert repo.is_sent_to_second_session(sid, tid, cid)

    repo.upsert_grade(sid, aid, 8.0, status="OK")
    assert repo.maybe_clear_second_session_without_trigger(sid, tid, cid)
    assert not repo.is_sent_to_second_session(sid, tid, cid)


def test_s1_jury_allows_send_despite_existing_s2_grades() -> None:
    repo = _repo()
    cid = repo.add_course("NUCL", "Nuclear", ects=4)
    repo.add_assessment(cid, "CC (25%)", "CC", 25.0, session=1)
    repo.add_assessment(cid, "EE (75%)", "EE", 75.0, session=1)
    repo.add_assessment(cid, "CC Rep (25%)", "CC", 25.0, session=2)
    repo.add_assessment(cid, "EE (75%)", "EE", 75.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("S3", "", "", "Test", "Jury", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aid_s2 = int(
        next(
            r["assessment_id"]
            for r in repo.get_grades_for_student_course(sid, cid)
            if int(r["session"]) == 2 and r["kind"] == "EE"
        )
    )
    repo.upsert_grade(sid, aid_s2, 11.0, status="OK")
    assert repo.course_has_session2_activity(sid, cid)
    assert repo.second_session_decision_locked(sid, tid, cid) is True
    assert repo.second_session_decision_locked(sid, tid, cid, s1_jury=True) is False
    assert repo.can_send_to_second_session(sid, tid, cid, s1_jury=True)
    repo.set_second_session_decision(sid, tid, cid, sent=True, s1_jury=True)
    assert repo.is_sent_to_second_session(sid, tid, cid)


def test_course_ue_display_label_s2_incomplete_shows_s1_def() -> None:
    repo = _repo()
    cid = repo.add_course("ENER", "Energy", ects=4)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("S4", "", "", "Gupta", "Test", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aid_s1 = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
    repo.upsert_grade(sid, aid_s1, None, status=STATUS_DEF)
    label = repo.course_ue_display_label(
        sid, tid, cid, view_session="s2", session_average=None, sent_s2=True, use_s2=True
    )
    assert label == STATUS_DEF


def test_manual_second_session_two_courses_persist() -> None:
    repo = _repo()
    cid1 = repo.add_course("Radio", "Radiation", ects=4)
    repo.add_assessment(cid1, "EE", "EE", 100.0, session=1)
    cid2 = repo.add_course("Neut", "Neutronics", ects=4)
    repo.add_assessment(cid2, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid1, block_name="B1")
    repo.add_course_to_template(tid, cid2, block_name="B1")
    sid = repo.add_student("S2", "", "", "Amoyal", "Louis", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for cid in (cid1, cid2):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, 5.0, status="OK")

    repo.set_second_session_decision(sid, tid, cid1, sent=True)
    repo.set_second_session_decision(sid, tid, cid2, sent=True)
    repo.sync_second_session_obligations(tid)
    assert repo.is_sent_to_second_session(sid, tid, cid1)
    assert repo.is_sent_to_second_session(sid, tid, cid2)


def test_def_display_s1_with_negative_jury() -> None:
    repo = _repo()
    sid, tid, cid, _aid = _setup_math_ue(repo)
    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid, points=-0.1)

    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    detail = row["ue_detail"][cid]
    assert detail["display"] == STATUS_DEF
    assert detail["total"] is None
    assert detail["s1"] == 0.0


def test_def_display_s2_view_before_s2_grades() -> None:
    repo = _repo()
    sid, tid, cid, _aid = _setup_math_ue(repo)
    repo.set_second_session_decision(sid, tid, cid, sent=True)
    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid, points=-0.1)

    row = next(
        r
        for r in repo.get_student_result_summary(
            tid, view_session="s2", include_all_students=True
        )
        if r["student_id"] == sid
    )
    detail = row["ue_detail"][cid]
    assert detail["display"] == STATUS_DEF
    assert detail["total"] is None


def test_s2_def_with_carried_cc_shows_def_not_dash() -> None:
    """DEF en examen S2 + CC reporté : afficher DEF (pas « — » ni le ABJ S1 du CC)."""
    repo = _repo()
    cid = repo.add_course("THER", "Thermal", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("S5", "", "", "Test", "S2Def", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    rows = repo.get_grades_for_student_course(sid, cid)
    aids = {
        (int(r["session"]), str(r["kind"])): int(r["assessment_id"]) for r in rows
    }
    repo.upsert_grade(sid, aids[(1, "CC")], 12.0, status="OK")
    repo.upsert_grade(sid, aids[(1, "EE")], 8.0, status="OK")
    repo.set_second_session_decision(sid, tid, cid, sent=True)
    repo.carry_over_reprise_grades_from_session1(sid, cid, template_id=tid)
    repo.upsert_grade(sid, aids[(2, "EE")], None, status=STATUS_DEF)

    for view in ("mixed", "s2"):
        row = next(
            r
            for r in repo.get_student_result_summary(
                tid, view_session=view, include_all_students=True
            )
            if r["student_id"] == sid
        )
        detail = row["ue_detail"][cid]
        assert detail["display"] == STATUS_DEF
        assert detail["total"] is None


def test_s2_abj_with_carried_cc_shows_abj() -> None:
    """ABJ en examen S2 + CC reporté : afficher ABJ malgré une moyenne S2 partielle sur le CC."""
    repo = _repo()
    cid = repo.add_course("FLUI", "Fluids", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("S6", "", "", "Test", "S2Abj", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    rows = repo.get_grades_for_student_course(sid, cid)
    aids = {
        (int(r["session"]), str(r["kind"])): int(r["assessment_id"]) for r in rows
    }
    repo.upsert_grade(sid, aids[(1, "CC")], 14.0, status="OK")
    repo.upsert_grade(sid, aids[(1, "EE")], 6.0, status="OK")
    repo.set_second_session_decision(sid, tid, cid, sent=True)
    repo.carry_over_reprise_grades_from_session1(sid, cid, template_id=tid)
    repo.upsert_grade(sid, aids[(2, "EE")], None, status=STATUS_ABJ)

    row = next(
        r
        for r in repo.get_student_result_summary(tid, view_session="mixed")
        if r["student_id"] == sid
    )
    detail = row["ue_detail"][cid]
    assert detail["display"] == STATUS_ABJ
    assert detail["total"] is None


def test_s2_def_overrides_s1_cc_abj_when_s2_average_incomplete() -> None:
    """Sans CC reporté en S2, un DEF à l'examen S2 prime sur un ABJ S1 au CC."""
    repo = _repo()
    cid = repo.add_course("MAT", "Materials", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("S7", "", "", "Test", "Priority", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    rows = repo.get_grades_for_student_course(sid, cid)
    aids = {
        (int(r["session"]), str(r["kind"])): int(r["assessment_id"]) for r in rows
    }
    repo.upsert_grade(sid, aids[(1, "CC")], None, status=STATUS_ABJ)
    repo.upsert_grade(sid, aids[(1, "EE")], 8.0, status="OK")
    repo.set_second_session_decision(sid, tid, cid, sent=True)
    repo.upsert_grade(sid, aids[(2, "EE")], None, status=STATUS_DEF)

    label = repo.course_ue_display_label(
        sid, tid, cid, view_session="s2", session_average=None, sent_s2=True, use_s2=True
    )
    assert label == STATUS_DEF


def test_s2_ee_def_persists_after_database_reopen() -> None:
    """Un DEF saisi sur l'examen S2 ne doit pas être effacé au redémarrage de l'app."""
    from mne_grade_manager.services.grade_status import format_grade_display, normalize_grade_status

    path = Path(tempfile.mkdtemp()) / "persist_def.sqlite3"
    db = Database(path)
    repo = Repository(db)
    cid = repo.add_course("MATE", "Materials", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B1")
    sid = repo.add_student("ALT", "", "", "Altmeyer", "Test", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aids = {
        (int(r["session"]), str(r["kind"])): int(r["assessment_id"])
        for r in repo.get_grades_for_student_course(sid, cid)
    }
    repo.set_second_session_decision(sid, tid, cid, sent=True)
    repo.upsert_grade(sid, aids[(2, "EE")], None, status=STATUS_DEF)

    db2 = Database(path)
    repo2 = Repository(db2)
    row = next(
        r
        for r in repo2.get_grades_for_student_course(sid, cid)
        if int(r["session"]) == 2 and r["kind"] == "EE"
    )
    assert normalize_grade_status(row.get("status")) == STATUS_DEF
    assert (
        format_grade_display(
            row.get("grade"),
            row.get("status"),
            assessment_session=2,
            assessment_kind="EE",
            assessment_name="EE (70%)",
        )
        == STATUS_DEF
    )
