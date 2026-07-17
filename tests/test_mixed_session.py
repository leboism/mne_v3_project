"""Vue « mixed » et filtre session 2 (notes S2 sans envoi formel)."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository, _compute_course_average_from_rows


def _repo() -> Repository:
    db = Database(Path(tempfile.mkdtemp()) / "mixed.sqlite3")
    return Repository(db)


def _minimal_template(repo: Repository) -> tuple[int, int, int, int]:
    cid = repo.add_course("UE1", "Course 1", ects=3)
    repo.add_assessment(cid, "Examen", "EE", 100.0, session=1)
    repo.add_assessment(cid, "Examen S2", "EE", 100.0, session=2)
    tid = repo.add_template("2025-2026 M1 NE", "M1", "NE", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc A")
    sid = repo.add_student("INE001", "", "", "Dupont", "Jean", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aid_s1 = int(
        next(
            r["assessment_id"]
            for r in repo.get_grades_for_student_course(sid, cid)
            if int(r["session"]) == 1
        )
    )
    aid_s2 = int(
        next(
            r["assessment_id"]
            for r in repo.get_grades_for_student_course(sid, cid)
            if int(r["session"]) == 2
        )
    )
    return tid, sid, cid, aid_s1, aid_s2


def test_s2_list_includes_student_with_s2_grade_without_send_flag() -> None:
    repo = _repo()
    tid, sid, cid, aid_s1, aid_s2 = _minimal_template(repo)
    repo.upsert_grade(sid, aid_s1, 8.0, status="OK")
    repo.upsert_grade(sid, aid_s2, 11.0, status="OK")

    s2_only = repo.get_student_result_summary(tid, view_session="s2")
    assert any(r["student_id"] == sid for r in s2_only)

    mixed = repo.get_student_result_summary(tid, view_session="mixed")
    row = next(r for r in mixed if r["student_id"] == sid)
    d = row["ue_detail"][cid]
    assert d["use_s2"] is True
    assert d["s2"] == 11.0
    assert row["courses"][f"c:{cid}"] == 11.0


def test_mixed_keeps_s1_when_no_s2_activity() -> None:
    repo = _repo()
    tid, sid, cid, aid_s1, _aid_s2 = _minimal_template(repo)
    repo.upsert_grade(sid, aid_s1, 12.0, status="OK")

    row = next(
        r
        for r in repo.get_student_result_summary(tid, view_session="mixed")
        if r["student_id"] == sid
    )
    d = row["ue_detail"][cid]
    assert d["use_s2"] is False
    assert row["courses"][f"c:{cid}"] == 12.0


def test_s2_floor_check_uses_student_ue_note_not_superseded_s1() -> None:
    """En S2, la note étudiant suit la session 2 (ex. EO remplace EE pour la moyenne)."""
    repo = _repo()
    cid = repo.add_course("RADIO", "Radiation", ects=4)
    repo.add_assessment(cid, "CCTP (20%)", "CCTP", 20.0, session=1)
    repo.add_assessment(cid, "EE (80%)", "EE", 80.0, session=1)
    repo.add_assessment(cid, "EO (100%)", "EO", 100.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc A")
    sid = repo.add_student("INE002", "", "", "Aboko", "Peter", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    rows = repo.get_grades_for_student_course(sid, cid)
    aid_ee = int(next(r["assessment_id"] for r in rows if r["kind"] == "EE" and int(r["session"]) == 1))
    aid_cctp = int(next(r["assessment_id"] for r in rows if r["kind"] == "CCTP"))
    aid_eo = int(next(r["assessment_id"] for r in rows if r["kind"] == "EO"))
    repo.upsert_grade(sid, aid_cctp, 6.0, status="OK")
    repo.upsert_grade(sid, aid_ee, 5.5, status="OK")
    repo.upsert_grade(sid, aid_eo, 9.3, status="OK")

    assert repo._course_has_unlocked_grade_below(
        sid, cid, view_session="s2", floor=7.0, template_id=tid
    ) is False
    assert repo._course_has_unlocked_grade_below(
        sid, cid, view_session="s1", floor=7.0, template_id=tid
    ) is True


def test_s2_average_uses_s2_mcc_with_cc_carry_when_sent() -> None:
    """Moyenne S2 = barème S2 avec CC repris seulement après envoi S2."""
    repo = _repo()
    cid = repo.add_course("MATH", "Math", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=2)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc A")
    sid = repo.add_student("INE003", "", "", "Test", "CC", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for r in repo.get_grades_for_student_course(sid, cid):
        aid = int(r["assessment_id"])
        if int(r["session"]) == 1 and r["kind"] == "CC":
            repo.upsert_grade(sid, aid, 12.0, status="OK")

    repo.set_second_session_decision(sid, tid, cid, sent=True)
    for r in repo.get_grades_for_student_course(sid, cid):
        aid = int(r["assessment_id"])
        if int(r["session"]) == 2 and r["kind"] == "EE":
            repo.upsert_grade(sid, aid, 14.0, status="OK")

    rows = repo.get_grades_for_student_course(sid, cid)
    s2 = _compute_course_average_from_rows(rows, mode="s2", allow_s1_reprise_carry=True)
    assert abs(float(s2) - 13.4) < 0.01


def test_carry_over_blocked_without_send() -> None:
    repo = _repo()
    cid = repo.add_course("MATH", "Math", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    tid = repo.add_template("T", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B")
    sid = repo.add_student("X", "", "", "Bayat", "Iman", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aid_s1 = int(next(r["assessment_id"] for r in repo.get_grades_for_student_course(sid, cid) if int(r["session"])==1))
    repo.upsert_grade(sid, aid_s1, 12.0, status="OK")
    assert repo.carry_over_reprise_grades_from_session1(sid, cid, template_id=tid) == 0
    n = repo.purge_carried_s2_reprises_without_send(sid, cid, template_id=tid)
    assert n == 0


def test_student_has_second_session_presence() -> None:
    repo = _repo()
    tid, sid, cid, _aid_s1, aid_s2 = _minimal_template(repo)
    assert repo.student_has_second_session_presence(sid, tid) is False
    repo.upsert_grade(sid, aid_s2, 10.0, status="OK")
    assert repo.student_has_second_session_presence(sid, tid) is True


def test_s2_carried_cc_can_be_cleared_and_stays_empty() -> None:
    """Après envoi S2, le CC reporté peut être effacé en saisie S2 sans être recopié."""
    repo = _repo()
    cid = repo.add_course("MATH", "Math", ects=4)
    repo.add_assessment(cid, "CC (30%)", "CC", 30.0, session=1)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=1)
    repo.add_assessment(cid, "CC Rep (30%)", "CC", 30.0, session=2)
    repo.add_assessment(cid, "EE (70%)", "EE", 70.0, session=2)
    tid = repo.add_template("T", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="B")
    sid = repo.add_student("X", "", "", "Bayat", "Iman", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    rows = repo.get_grades_for_student_course(sid, cid)
    aids = {
        (int(r["session"]), str(r["kind"])): int(r["assessment_id"]) for r in rows
    }
    repo.upsert_grade(sid, aids[(1, "CC")], 12.0, status="OK")
    repo.set_second_session_decision(sid, tid, cid, sent=True)
    assert repo.carry_over_reprise_grades_from_session1(sid, cid, template_id=tid) == 0

    repo.upsert_grade(
        sid,
        aids[(2, "CC")],
        None,
        status="OK",
        trigger_carry_over=False,
    )
    row = next(
        r
        for r in repo.get_grades_for_student_course(sid, cid)
        if int(r["session"]) == 2 and r["kind"] == "CC"
    )
    assert row["grade"] is None
    # Pas de recopie implicite : la case reste vide tant qu'on ne rappelle pas carry manuellement.


def test_results_s2_view_does_not_show_jury_points_without_average() -> None:
    from mne_grade_manager.tabs.results_tab import _ue_cell_text, _ue_total_numeric

    row = {
        "ue_detail": {
            1: {
                "s1": 10.0,
                "s2": None,
                "use_s2": True,
                "sent_s2": True,
                "jury": -0.5,
                "display": "",
            }
        }
    }
    assert _ue_total_numeric(row, 1, "s2") is None
    assert _ue_cell_text(row, 1, "s2") == "—"
