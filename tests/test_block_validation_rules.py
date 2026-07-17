"""Règles MNE : note d'UE (MCC), moyenne de bloc ECTS, seuils 7 et 10."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "rules.sqlite3"))


def _two_ue_block(repo: Repository, *, g1: float, g2: float) -> tuple[int, int, int, int, int]:
    cid_a = repo.add_course("UE-A", "UE A", ects=3)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("UE-B", "UE B", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid_a, block_name="Bloc test")
    repo.add_course_to_template(tid, cid_b, block_name="Bloc test")
    sid = repo.add_student("S1", "", "", "Dupont", "Jean", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for cid, g in ((cid_a, g1), (cid_b, g2)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, g, status="OK")
    return sid, tid, cid_a, cid_b, "Bloc test"


def test_block_validated_when_avg_ge_10_and_no_ue_below_7() -> None:
    repo = _repo()
    sid, tid, cid_a, cid_b, bk = _two_ue_block(repo, g1=11.0, g2=9.2)
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert float(avg) >= 10.0
    assert repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
    assert repo.block_allows_ue_compensation(sid, tid, cid_b, result_row=row, view_session="s1")


def test_block_not_validated_when_ue_below_7() -> None:
    repo = _repo()
    sid, tid, cid_a, cid_b, bk = _two_ue_block(repo, g1=11.0, g2=5.0)
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert not repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
    assert not repo.block_allows_ue_compensation(sid, tid, cid_a, result_row=row, view_session="s1")


def test_waiver_below_7_restores_compensation_when_block_valid() -> None:
    repo = _repo()
    cid_a = repo.add_course("UE-A", "UE A", ects=3)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("UE-B", "UE B", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    cid_c = repo.add_course("UE-C", "UE C", ects=3)
    repo.add_assessment(cid_c, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    bk = "Bloc test"
    for cid in (cid_a, cid_b, cid_c):
        repo.add_course_to_template(tid, cid, block_name=bk)
    sid = repo.add_student("S1", "", "", "Dupont", "Jean", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for cid, g in ((cid_a, 9.3), (cid_b, 5.0), (cid_c, 16.0)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, g, status="OK")
    repo.set_ue_jury_floor_waiver(sid, tid, cid_b, waived=True)
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert float(avg) >= 10.0
    assert repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
    assert repo.block_allows_ue_compensation(sid, tid, cid_a, result_row=row, view_session="s1")


def test_year_average_is_block_weighted_not_flat_ue_mix() -> None:
    """Moyenne année = moyenne ECTS des moyennes de bloc (les blocs ne se mélangent pas autrement)."""
    repo = _repo()
    cid_a = repo.add_course("UE-A", "UE A", ects=6)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("UE-B", "UE B", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid_a, block_name="Bloc 1")
    repo.add_course_to_template(tid, cid_b, block_name="Bloc 2")
    sid = repo.add_student("S1", "", "", "Martin", "Paul", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for cid, g in ((cid_a, 16.0), (cid_b, 10.0)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, g, status="OK")
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    # Bloc 1 (6 ECTS) = 16, Bloc 2 (3 ECTS) = 10 → année = (16*6 + 10*3) / 9 = 14
    assert abs(float(row["global_average"]) - 14.0) < 0.01


def test_block_not_validated_when_avg_below_10_even_if_all_ue_ge_7() -> None:
    repo = _repo()
    sid, tid, _cid_a, cid_b, bk = _two_ue_block(repo, g1=9.5, g2=9.5)
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert float(avg) < 10.0
    assert not repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
    assert not repo.block_allows_ue_compensation(sid, tid, cid_b, result_row=row, view_session="s1")


def test_block_jury_validation_waiver_allows_avg_below_10() -> None:
    repo = _repo()
    sid, tid, _cid_a, cid_b, bk = _two_ue_block(repo, g1=9.5, g2=9.5)
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    avg = row["blocks"][bk]
    assert float(avg) < 10.0
    assert not repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
    repo.set_block_jury_validation_waiver(sid, tid, bk, waived=True)
    assert repo.has_block_jury_validation_waiver(sid, tid, bk)
    assert repo.block_is_validated(sid, tid, bk, view_session="s1", block_average=avg)
    assert repo.block_allows_ue_compensation(sid, tid, cid_b, result_row=row, view_session="s1")


def test_courses_to_retake_splits_mandatory_and_recommended() -> None:
    repo = _repo()
    cid_a = repo.add_course("UE-A", "UE A", ects=3)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("UE-B", "UE B", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    cid_c = repo.add_course("UE-C", "UE C", ects=3)
    repo.add_assessment(cid_c, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    bk_ok = "Bloc validé"
    bk_ko = "Bloc non validé"
    repo.add_course_to_template(tid, cid_a, block_name=bk_ok)
    repo.add_course_to_template(tid, cid_b, block_name=bk_ok)
    repo.add_course_to_template(tid, cid_c, block_name=bk_ko)
    sid = repo.add_student("S1", "", "", "Martin", "Paul", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for cid, g in ((cid_a, 12.0), (cid_b, 8.0), (cid_c, 8.0)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, g, status="OK")
    row = next(r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid)
    retake = repo.courses_to_retake_for_student(sid, tid, view_session="s1", result_row=row)
    mand_codes = {c["code"] for c in retake["mandatory"]}
    rec_codes = {c["code"] for c in retake["recommended"]}
    assert "UE-C" in mand_codes
    assert "UE-B" in rec_codes
    assert "UE-A" not in mand_codes and "UE-A" not in rec_codes
    txt = repo.format_courses_to_retake_text(retake)
    assert "Obligatoire" in txt
    assert "Recommandé" in txt


def test_courses_to_retake_skips_validated_ue_and_uses_block_validation() -> None:
    repo = _repo()
    cid_a = repo.add_course("S1-C-RAD", "Radiation", ects=3)
    repo.add_assessment(cid_a, "EE", "EE", 100.0, session=1)
    cid_b = repo.add_course("S1-C-MATH", "Math", ects=3)
    repo.add_assessment(cid_b, "EE", "EE", 100.0, session=1)
    cid_c = repo.add_course("S1-C-CHEM", "Chem", ects=3)
    repo.add_assessment(cid_c, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    bk = "Bloc 1"
    for cid in (cid_a, cid_b, cid_c):
        repo.add_course_to_template(tid, cid, block_name=bk)
    sid = repo.add_student("S1", "", "", "Amoyal", "Louis", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    for cid, g in ((cid_a, 12.0), (cid_b, 8.0), (cid_c, 9.0)):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, g, status="OK")
    repo.set_ue_ects_validation(sid, tid, cid_a, validated=True)
    row = next(
        r for r in repo.get_student_result_summary(tid, view_session="s1") if r["student_id"] == sid
    )
    retake = repo.courses_to_retake_for_student(sid, tid, view_session="s1", result_row=row)
    codes = {c["code"] for c in retake["mandatory"] + retake["recommended"]}
    assert "S1-C-RAD" not in codes
    assert "S1-C-MATH" in codes or "S1-C-CHEM" in codes
