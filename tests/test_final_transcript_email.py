"""E-mails transcript final en anglais."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.core.master_team import ROLE_SECRETARIAT, encode_tracks_scope
from mne_grade_manager.services.final_transcript_email import (
    build_final_transcript_email_body,
    build_final_transcript_email_subject,
    gather_final_transcript_notification,
    jury_decision_english,
    mention_for_student,
)
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "final_email.sqlite3"))


def test_mention_french_in_email() -> None:
    repo = _repo()
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    sid = repo.add_student("S0", "", "", "Test", "Mention", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(
        sid, tid, jury_session_id=js, outcome="pass_m2", mention="bien"
    )
    assert mention_for_student(repo, student_id=sid, template_id=tid, grade=15.0) == "Bien"
    assert mention_for_student(repo, student_id=sid, template_id=tid, grade=17.0) == "Bien"


def test_email_subject_and_body_english() -> None:
    subject = build_final_transcript_email_subject(
        academic_year="2025-2026",
        level="M1",
        track="C",
        last_name="Dupont",
        first_name="Alice",
    )
    assert "MNE Final Transcript" in subject
    assert "M1C" in subject
    assert "Dupont" in subject

    pdf = Path(tempfile.mkdtemp()) / "Dupont Alice Final Transcript M1C.pdf"
    pdf.write_bytes(b"%PDF")
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
        pdf_path=pdf,
        contact_emails=["track.lead@example.org", "track.co-lead@example.org"],
    )
    assert "Dear Alice" in body
    assert "please find below your official results." in body
    assert "pleased" not in body.lower()
    assert "14.50/20" in body
    assert "Bien" in body
    assert "Track ranking: 3" in body
    assert "Cohort ranking: 12" in body
    assert "first year of the Master (M1) has been validated" in body
    assert "admitted to the second year" not in body
    assert "Master of Nuclear Energy (M1 Chemistry and Engineering)" in body
    assert "M1 Master Nuclear Energy" in body
    assert "Universite Paris-Saclay / IPParis / PSL" in body
    assert "Master of Science" not in body
    assert "INSTN" not in body
    assert "Please attach your final transcript PDF" not in body
    assert "track responsibles" in body
    assert "track.lead@example.org" in body
    assert "track.co-lead@example.org" in body
    assert "contact the mne secretariat" not in body.lower()
    assert "generate it from the application" not in body
    assert "parcours responsibles" not in body.lower()


def test_m1_track_responsible_email_in_final_mail() -> None:
    repo = _repo()
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.upsert_track_director(
        "2025-2026",
        "M1",
        "C",
        0,
        last_name="",
        first_name="",
        email_work="m1c.director@example.org",
    )
    sid = repo.add_student(
        "S1",
        "",
        "",
        "Dupont",
        "Alice",
        academic_year="2025-2026",
        level="M1",
        track="C",
        email_institutional="alice@example.org",
    )
    repo.enroll_student(sid, tid)
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(sid, tid, jury_session_id=js, outcome="pass_m2", mention="bien")

    notif = gather_final_transcript_notification(
        repo, template_id=tid, student_id=sid, jury_session_id=js
    )
    assert "m1c.director@example.org" in notif.body
    assert "track responsibles" in notif.body.lower()


def test_gather_notification_for_validated_student() -> None:
    repo = _repo()
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid = repo.add_student(
        "S1",
        "",
        "",
        "Dupont",
        "Alice",
        academic_year="2025-2026",
        level="M1",
        track="C",
        email_institutional="alice.dupont@universite.fr",
    )
    repo.enroll_student(sid, tid)
    aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
    repo.upsert_grade(sid, aid, 15.0, status="OK")
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(
        sid, tid, jury_session_id=js, outcome="pass_m2", mention="bien", progression_track="P"
    )

    notif = gather_final_transcript_notification(
        repo, template_id=tid, student_id=sid, jury_session_id=js
    )
    assert notif.has_email
    assert notif.mention == "Bien"
    assert isinstance(notif.cc_emails, list)
    assert "first year of the Master (M1) has been validated" in notif.jury_decision
    assert "M2 track" not in notif.jury_decision
    assert jury_decision_english(
        repo, student_id=sid, template_id=tid, jury_session_id=js
    ).startswith("Your first year of the Master (M1)")


def test_jury_notification_cc_includes_pedagogical_secretariat() -> None:
    repo = _repo()
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.upsert_mention_director(
        "2025-2026",
        0,
        last_name="Dir",
        first_name="Mention",
        email_work="mention.director@example.org",
    )
    repo.upsert_track_director(
        "2025-2026",
        "M1",
        "C",
        0,
        last_name="Dir",
        first_name="Track",
        email_work="track.director@example.org",
    )
    repo.add_master_team_member(
        "2025-2026",
        ROLE_SECRETARIAT,
        institution="Université Paris-Saclay",
        tracks_scope=encode_tracks_scope([("M1", "C")]),
        last_name="Boutu",
        first_name="Anne",
        email_work="anne.boutu@example.org",
    )
    cc = repo.jury_notification_cc_emails(tid)
    assert "mention.director@example.org" in cc
    assert "track.director@example.org" in cc
    assert "anne.boutu@example.org" in cc


def test_repeat_and_refuse_repeat_decisions_stay_short_without_auto_reasons() -> None:
    repo = _repo()
    cid = repo.add_course("UE-1", "UE 1", ects=6)
    repo.add_assessment(cid, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 C", "M1", "C", "2025-2026", "1")
    repo.add_course_to_template(tid, cid, block_name="Bloc 1")
    sid = repo.add_student(
        "S1",
        "",
        "",
        "Martin",
        "Paul",
        academic_year="2025-2026",
        level="M1",
        track="C",
    )
    repo.enroll_student(sid, tid)
    aid = int(repo.get_grades_for_student_course(sid, cid)[0]["assessment_id"])
    repo.upsert_grade(sid, aid, 6.0, status="OK")
    js = repo.add_jury_session(tid, "FINAL", label="Jury final")
    repo.upsert_jury_student_outcome(sid, tid, jury_session_id=js, outcome="repeat")

    repeat_decision = jury_decision_english(
        repo, student_id=sid, template_id=tid, jury_session_id=js, view_session="mixed"
    )
    assert repeat_decision == "You are authorized to repeat the year."
    assert "Reason" not in repeat_decision
    assert "below 10/20" not in repeat_decision.lower()

    repo.upsert_jury_student_outcome(sid, tid, jury_session_id=js, outcome="refuse_repeat")
    refuse_decision = jury_decision_english(
        repo, student_id=sid, template_id=tid, jury_session_id=js, view_session="mixed"
    )
    assert refuse_decision == "Repeat of the year has not been authorized by the jury."

    body = build_final_transcript_email_body(
        first_name="Paul",
        academic_year="2025-2026",
        level="M1",
        track="C",
        program="Chemistry and Engineering",
        average=6.0,
        mention="Not applicable (average below 12/20)",
        ranking_track="—",
        ranking_cohort="—",
        jury_decision=refuse_decision,
        outcome_key="refuse_repeat",
        pdf_path=None,
        contact_emails=["track.lead@example.org"],
    )
    assert "please contact your track responsibles" in body.lower()
    assert "below 10/20" not in body.lower()
