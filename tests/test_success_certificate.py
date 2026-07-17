"""Certificate of Achievement (English) for successful final-jury students."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.final_transcript_email import build_final_transcript_email_body
from mne_grade_manager.services.jury_reports import (
    _success_certificate_statement,
    student_eligible_for_success_certificate,
    success_certificate_default_filename,
    write_success_certificate_pdf,
)
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "cert.sqlite3"))


def test_certificate_statement_m1_and_m2() -> None:
    m1 = _success_certificate_statement(
        outcome="pass_m2",
        level="M1",
        track="C",
        full_name="Alice Dupont",
        academic_year="2025-2026",
    )
    assert "Alice Dupont" in m1
    assert "first year (M1)" in m1
    assert "Chemistry and Engineering" in m1
    assert "2025-2026" in m1

    m2 = _success_certificate_statement(
        outcome="validate_year",
        level="M2",
        track="NPD",
        full_name="Bob Martin",
        academic_year="2025-2026",
    )
    assert "Bob Martin" in m2
    assert "successfully completed the Master of Nuclear Energy (M2 Nuclear Plant Design)" in m2


def test_certificate_filename() -> None:
    name = success_certificate_default_filename(
        {"last_name": "Dupont", "first_name": "Alice"},
        level="M1",
        track="C",
    )
    assert name == "Dupont Alice Certificate of Achievement M1C.pdf"


def test_eligibility_and_pdf_generation() -> None:
    repo = _repo()
    ay = "2025-2026"
    cid = repo.add_course("UE1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("M1 C", "M1", "C", ay, "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid_ok = repo.add_student(
        "OK1", "", "", "Dupont", "Alice", academic_year=ay, level="M1", track="C"
    )
    sid_rep = repo.add_student(
        "REP1", "", "", "Martin", "Bob", academic_year=ay, level="M1", track="C"
    )
    repo.enroll_student(sid_ok, tid)
    repo.enroll_student(sid_rep, tid)
    for sid in (sid_ok, sid_rep):
        aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
        repo.upsert_grade(sid, aid, 14.0, status="OK")
    jsid = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(
        sid_ok, tid, jury_session_id=jsid, outcome="pass_m2", mention="bien"
    )
    repo.upsert_jury_student_outcome(
        sid_rep, tid, jury_session_id=jsid, outcome="repeat"
    )

    assert student_eligible_for_success_certificate(
        repo, template_id=tid, student_id=sid_ok, jury_session_id=jsid
    )
    assert not student_eligible_for_success_certificate(
        repo, template_id=tid, student_id=sid_rep, jury_session_id=jsid
    )

    out = Path(tempfile.mkdtemp()) / "Dupont Alice Certificate of Achievement M1C.pdf"
    write_success_certificate_pdf(
        repo,
        template_id=tid,
        student_id=sid_ok,
        path=out,
        jury_session_id=jsid,
    )
    assert out.is_file()
    assert out.stat().st_size > 500
    assert out.read_bytes()[:4] == b"%PDF"

    try:
        write_success_certificate_pdf(
            repo,
            template_id=tid,
            student_id=sid_rep,
            path=Path(tempfile.mkdtemp()) / "no.pdf",
            jury_session_id=jsid,
        )
        raise AssertionError("expected ValueError for repeat student")
    except ValueError as exc:
        assert "success decision" in str(exc).lower()


def test_email_mentions_certificate_when_attached() -> None:
    cert = Path(tempfile.mkdtemp()) / "cert.pdf"
    cert.write_bytes(b"%PDF")
    body = build_final_transcript_email_body(
        first_name="Alice",
        academic_year="2025-2026",
        level="M1",
        track="C",
        program="Core",
        average=14.5,
        mention="Bien",
        ranking_track="3",
        ranking_cohort="12",
        jury_decision="Your first year of the Master (M1) has been validated.",
        outcome_key="pass_m2",
        pdf_path=None,
        contact_emails=[],
        certificate_path=cert,
    )
    assert "Certificate of Achievement" in body
    assert "final transcript and Certificate of Achievement" in body
