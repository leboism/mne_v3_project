"""Session affichée sur les transcripts (S1 vs S2 par UE)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "transcript_sess.sqlite3"))


def test_ue_session_label_s1_vs_s2_on_final_view() -> None:
    repo = _repo()
    ay = "2025-2026"
    c_s1 = repo.add_course("S1-UE", "UE session 1", ects=3)
    c_s2 = repo.add_course("S2-UE", "UE session 2", ects=3)
    for cid in (c_s1, c_s2):
        repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
        repo.add_assessment(cid, "EE2", "EE2", 100.0, session=2)
    tid = repo.add_template("M1 C", "M1", "C", ay, "1")
    repo.add_course_to_template(tid, c_s1, block_name="Bloc 1")
    repo.add_course_to_template(tid, c_s2, block_name="Bloc 1")
    sid = repo.add_student("S1", "", "", "Test", "Student", academic_year=ay, level="M1", track="C")
    repo.enroll_student(sid, tid)

    a1 = int(repo.get_grades_for_student_course(sid, c_s1)[0]["assessment_id"])
    a2 = int(repo.get_grades_for_student_course(sid, c_s2)[0]["assessment_id"])
    a2b = int(
        next(r for r in repo.get_grades_for_student_course(sid, c_s2) if int(r["session"]) == 2)[
            "assessment_id"
        ]
    )
    repo.upsert_grade(sid, a1, 14.0, status="OK")
    repo.upsert_grade(sid, a2, 8.0, status="OK")
    repo.set_second_session_decision(sid, tid, c_s2, sent=True)
    repo.upsert_grade(sid, a2b, 12.0, status="OK")

    row = repo.get_student_result_summary(tid, view_session="mixed", include_all_students=True)[0]
    d1 = row["ue_detail"][c_s1]
    d2 = row["ue_detail"][c_s2]
    assert d1["use_s2"] is False
    assert d2["use_s2"] is True

    lbl1 = repo.get_ue_transcript_session_label(
        sid, tid, c_s1, default_view_session="s1", default_academic_year=ay
    )
    lbl2 = repo.get_ue_transcript_session_label(
        sid, tid, c_s2, default_view_session="s2", default_academic_year=ay
    )
    assert lbl1 == "S1 2025/2026"
    assert lbl2 == "S2 2025/2026"


def test_all_transcripts_use_mixed_view_session() -> None:
    """Partial and final transcripts both use mixed grades + per-UE session labels."""
    from mne_grade_manager.services.jury_reports import write_institutional_transcript_pdf

    repo = _repo()
    ay = "2025-2026"
    c_s1 = repo.add_course("S1-ONLY", "UE S1", ects=3)
    c_s2 = repo.add_course("S2-UE", "UE S2", ects=3)
    for cid in (c_s1, c_s2):
        repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
        repo.add_assessment(cid, "EE2", "EE2", 100.0, session=2)
    tid = repo.add_template("M1 C", "M1", "C", ay, "1")
    repo.add_course_to_template(tid, c_s1, block_name="Bloc 1")
    repo.add_course_to_template(tid, c_s2, block_name="Bloc 1")
    sid = repo.add_student("S1", "", "", "Amimer", "Sarah", academic_year=ay, level="M1", track="C")
    repo.enroll_student(sid, tid)

    a1 = int(repo.get_grades_for_student_course(sid, c_s1)[0]["assessment_id"])
    a2 = int(repo.get_grades_for_student_course(sid, c_s2)[0]["assessment_id"])
    a2b = int(
        next(r for r in repo.get_grades_for_student_course(sid, c_s2) if int(r["session"]) == 2)[
            "assessment_id"
        ]
    )
    repo.upsert_grade(sid, a1, 14.0, status="OK")
    repo.upsert_grade(sid, a2, 8.0, status="OK")
    repo.set_second_session_decision(sid, tid, c_s2, sent=True)
    repo.upsert_grade(sid, a2b, 12.0, status="OK")

    out_partial = Path(tempfile.mkdtemp()) / "partial.pdf"
    write_institutional_transcript_pdf(
        repo, template_id=tid, student_id=sid, path=out_partial, final=False, view_session="s2"
    )
    assert out_partial.is_file()

    repo.add_jury_session(tid, "FINAL", label="Final")
    out_final = Path(tempfile.mkdtemp()) / "final.pdf"
    write_institutional_transcript_pdf(
        repo, template_id=tid, student_id=sid, path=out_final, final=True, view_session="s2"
    )
    assert out_final.is_file()

    row = repo.get_student_result_summary(tid, view_session="mixed", include_all_students=True)[0]
    assert row["ue_detail"][c_s1]["use_s2"] is False
    assert row["ue_detail"][c_s2]["use_s2"] is True
    assert (
        repo.get_ue_transcript_session_label(
            sid, tid, c_s1, default_view_session="s1", default_academic_year=ay
        )
        == "S1 2025/2026"
    )
    assert (
        repo.get_ue_transcript_session_label(
            sid, tid, c_s2, default_view_session="s2", default_academic_year=ay
        )
        == "S2 2025/2026"
    )


def test_final_transcript_academic_year_line_omits_retained_grades_wording() -> None:
    from mne_grade_manager.services.jury_reports import _transcript_academic_year_line

    assert _transcript_academic_year_line("2025-2026", "mixed", final=True) == "Academic year 2025-2026"
    assert "Retained grades" not in _transcript_academic_year_line("2025-2026", "mixed", final=True)
    partial = _transcript_academic_year_line("2025-2026", "mixed", final=False)
    assert "Retained grades (S2 when available)" in partial
