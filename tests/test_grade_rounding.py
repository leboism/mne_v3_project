"""Arrondi MNE (2 décimales) pour seuils 7 et 10."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.calculations import (
    grade_below_threshold,
    grade_meets_minimum,
    round_grade_mne,
)
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "rounding.sqlite3"))


def test_round_grade_mne_two_decimals() -> None:
    assert round_grade_mne(9.994) == 9.99
    assert round_grade_mne(9.995) == 10.0
    assert round_grade_mne(9.996) == 10.0


def test_grade_meets_minimum_uses_rounding() -> None:
    assert grade_meets_minimum(9.996, 10.0)
    assert not grade_meets_minimum(9.994, 10.0)


def test_s2_view_uses_s1_grade_when_course_not_in_second_session() -> None:
    """Vue S2 : une UE sans activité S2 garde la note S1 (+ jury) pour le seuil 7."""
    repo = _repo()
    cid_a = repo.add_course("UE-A", "UE A", ects=3)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("UE-B", "UE B", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    repo.add_assessment(cid_b, "EE-S2", "EE S2", 100.0, session=2)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    bk = "Bloc test"
    repo.add_course_to_template(tid, cid_a, block_name=bk)
    repo.add_course_to_template(tid, cid_b, block_name=bk)
    sid = repo.add_student("S1", "", "", "Benaziz", "Youness", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    aid_a = int(repo.get_grades_for_student_course(sid, cid_a)[0]["assessment_id"])
    repo.upsert_grade(sid, aid_a, 9.975, status="OK")
    aids_b = repo.get_grades_for_student_course(sid, cid_b)
    s1_b = next(a for a in aids_b if int(a["session"]) == 1)
    s2_b = next(a for a in aids_b if int(a["session"]) == 2)
    repo.upsert_grade(sid, int(s1_b["assessment_id"]), 8.0, status="OK")
    repo.upsert_grade(sid, int(s2_b["assessment_id"]), 12.0, status="OK")
    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid_a, points=0.025)
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s2") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert grade_meets_minimum(avg, 10.0)
    assert repo.block_is_validated(sid, tid, bk, view_session="s2", block_average=avg)


def test_block_validated_when_rounded_avg_is_10() -> None:
    repo = _repo()
    cid_a = repo.add_course("UE-A", "UE A", ects=3)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("UE-B", "UE B", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    bk = "Bloc test"
    repo.add_course_to_template(tid, cid_a, block_name=bk)
    repo.add_course_to_template(tid, cid_b, block_name=bk)
    sid = repo.add_student("S1", "", "", "Benaziz", "Youness", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    # Moyenne brute 9.998 → arrondi 10.00
    for cid, g in ((cid_a, 10.0), (cid_b, 9.996)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, g, status="OK")
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert grade_meets_minimum(avg, 10.0)
    assert repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
