"""E-mail en anglais : notification transcript final, mention, classement et décision de jury."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.parcours import master_degree_email_title, mne_email_signature, track_program_label
from .jury_reports import (
    JURY_OUTCOME_LABELS,
    resolve_transcript_mention,
)
from .student_emails import EMAIL_MODE_INST_OR_PERSONAL, build_student_email_list

MENTION_BELOW_12 = "Not applicable (average below 12/20)"


def _transcript_attach_hint(pdf_path: Path | None, *, final: bool) -> str:
    """Plus de consigne « joindre manuellement » : l'app joint le PDF à l'ouverture du mail."""
    return ""


def _track_responsibles_contact_block(contact_emails: list[str]) -> str:
    if not contact_emails:
        return ""
    lines = "\n".join(f"  {e}" for e in contact_emails)
    return f"\nFor any questions, please contact your track responsibles:\n{lines}\n"


def mention_for_student(
    repo,
    *,
    student_id: int,
    template_id: int,
    grade: float | None,
) -> str:
    """Mention en français (Assez bien, Bien, …) pour le corps du mail."""
    mention_fr = resolve_transcript_mention(
        repo,
        student_id=int(student_id),
        template_id=int(template_id),
        grade=grade,
    )
    if not mention_fr or mention_fr == "—":
        return MENTION_BELOW_12
    return mention_fr


JURY_OUTCOME_ENGLISH: dict[str, str] = {
    "validate_year": "Your Master year has been validated. Congratulations on completing the programme.",
    "pass_m2": "Your first year of the Master (M1) has been validated.",
    "repeat": "You are authorized to repeat the year.",
    "refuse_repeat": "Repeat of the year has not been authorized by the jury.",
}


def rankings_english(
    repo,
    *,
    template_id: int,
    student_id: int,
    view_session: str = "s2",
) -> tuple[str, str]:
    """Classement parcours et cohorte (texte anglais pour e-mails / transcripts)."""
    sid, tid = int(student_id), int(template_id)
    vs = str(view_session or "s2").strip().lower()
    na = "Not applicable (second session retakes)."
    if not repo.student_eligible_for_ranking(sid, tid):
        return na, na
    track = repo.student_track_rank(tid, sid, view_session=vs)
    cohort = repo.student_cohort_rank(tid, sid, view_session=vs)
    track_txt = str(track) if track is not None else "—"
    cohort_txt = str(cohort) if cohort is not None else "—"
    return track_txt, cohort_txt


def ranking_english(
    repo,
    *,
    template_id: int,
    student_id: int,
    view_session: str = "s2",
) -> str:
    track, cohort = rankings_english(
        repo,
        template_id=template_id,
        student_id=student_id,
        view_session=view_session,
    )
    if track.startswith("Not applicable"):
        return track
    return f"{track} (track) / {cohort} (cohort)"


def jury_decision_english(
    repo,
    *,
    student_id: int,
    template_id: int,
    jury_session_id: int | None,
    result_row: dict[str, Any] | None = None,
    view_session: str = "s2",
) -> str:
    sid, tid = int(student_id), int(template_id)
    jsid = jury_session_id
    if jsid is None:
        jsid = repo.get_final_jury_session_id(tid)
    oc = repo.get_jury_student_outcome(sid, tid, jury_session_id=jsid)
    outcome = str((oc or {}).get("outcome") or "").strip()
    if not outcome:
        row = result_row
        if row is None:
            rows = repo.get_student_result_summary(tid, view_session=view_session)
            row = next((r for r in rows if int(r.get("student_id") or 0) == sid), None)
        ev = repo.evaluate_student_year_validation(
            sid, tid, view_session=view_session, result_row=row
        )
        outcome = str(ev.get("suggested_outcome") or "")
    return JURY_OUTCOME_ENGLISH.get(outcome, JURY_OUTCOME_LABELS.get(outcome, outcome or "—"))


def build_final_transcript_email_subject(
    *,
    academic_year: str,
    level: str,
    track: str,
    last_name: str,
    first_name: str,
) -> str:
    ay = str(academic_year or "").strip()
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    name = f"{str(last_name or '').strip()} {str(first_name or '').strip()}".strip()
    bits = ["MNE Final Transcript"]
    if lv and tr:
        bits.append(f"{lv}{tr}")
    elif lv:
        bits.append(lv)
    if ay:
        bits.append(ay)
    if name:
        bits.append(name)
    return " — ".join(bits)


@dataclass
class FinalTranscriptNotification:
    student_id: int
    student_name: str
    first_name: str
    email: str
    has_email: bool
    subject: str
    body: str
    pdf_path: Path | None
    average: float | None
    mention: str
    ranking: str
    jury_decision: str
    cc_emails: list[str]
    certificate_path: Path | None = None


def _jury_session_label_en(session_kind: str) -> str:
    kind = str(session_kind or "FINAL").strip().upper()
    if kind == "S1":
        return "Session 1 jury"
    if kind == "S2":
        return "Session 2 jury"
    return "final jury"


def retake_courses_english(
    repo,
    *,
    template_id: int,
    student_id: int,
    view_session: str = "s1",
) -> str:
    retake = repo.courses_to_retake_for_student(
        int(student_id),
        int(template_id),
        view_session=str(view_session or "s1").strip().lower(),
    )
    mand = retake.get("mandatory") or []
    rec = retake.get("recommended") or []
    if not mand and not rec:
        return "No courses to retake in the second session."
    parts: list[str] = []

    def _fmt(courses: list[dict], *, label: str) -> None:
        if not courses:
            return
        bits: list[str] = []
        for c in courses[:12]:
            code = str(c.get("code") or c.get("name") or "Course").strip()
            st = str(c.get("status") or "").strip()
            n = c.get("note")
            if st:
                bits.append(f"{code} ({st})")
            elif n is not None:
                bits.append(f"{code} ({float(n):.2f}/20)")
            else:
                bits.append(code)
        txt = ", ".join(bits)
        if len(courses) > 12:
            txt += f" (+{len(courses) - 12} more)"
        parts.append(f"{label}: {txt}")

    _fmt(mand, label="Mandatory retakes")
    _fmt(rec, label="Recommended retakes")
    return "\n".join(parts)


def build_intermediate_jury_email_subject(
    *,
    session_kind: str,
    academic_year: str,
    level: str,
    track: str,
    last_name: str,
    first_name: str,
) -> str:
    ay = str(academic_year or "").strip()
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    name = f"{str(last_name or '').strip()} {str(first_name or '').strip()}".strip()
    kind = str(session_kind or "S1").strip().upper()
    bits = [f"MNE {_jury_session_label_en(kind).title()}"]
    if lv and tr:
        bits.append(f"{lv}{tr}")
    elif lv:
        bits.append(lv)
    if ay:
        bits.append(ay)
    if name:
        bits.append(name)
    return " — ".join(bits)


def build_intermediate_jury_email_body(
    *,
    first_name: str,
    academic_year: str,
    level: str,
    track: str,
    session_kind: str,
    average: float | None,
    retake_text: str,
    pdf_path: Path | None,
    contact_emails: list[str],
) -> str:
    fn = str(first_name or "").strip() or "Student"
    ay = str(academic_year or "").strip() or "—"
    lv = str(level or "").strip().upper()
    degree = master_degree_email_title(lv, track)
    jury_lab = _jury_session_label_en(session_kind)

    avg_line = "—"
    if average is not None:
        avg_line = f"{float(average):.2f}/20"

    attach_hint = _transcript_attach_hint(pdf_path, final=False)
    contact = _track_responsibles_contact_block(contact_emails)
    signature = mne_email_signature(lv)

    return f"""Dear {fn},

Following the {jury_lab} of the {degree}, academic year {ay}, please find below your official results.

Average (including jury deliberation): {avg_line}
Courses to retake in the second session:
{retake_text}
{attach_hint}
This message is sent individually. The attached document is your official transcript in English (MNE format).
{contact}
Best regards,

{signature}
""".strip()


def build_final_transcript_email_body(
    *,
    first_name: str,
    academic_year: str,
    level: str,
    track: str,
    program: str,
    average: float | None,
    mention: str,
    ranking_track: str,
    ranking_cohort: str,
    jury_decision: str,
    outcome_key: str = "",
    pdf_path: Path | None,
    contact_emails: list[str],
    certificate_path: Path | None = None,
) -> str:
    fn = str(first_name or "").strip() or "Student"
    ay = str(academic_year or "").strip() or "—"
    lv = str(level or "").strip().upper()
    degree = master_degree_email_title(lv, track)

    avg_line = "—"
    if average is not None:
        avg_line = f"{float(average):.2f}/20"

    attach_hint = _transcript_attach_hint(pdf_path, final=True)
    contact = _track_responsibles_contact_block(contact_emails)
    signature = mne_email_signature(lv)

    oc = str(outcome_key or "").strip().lower()
    if oc in {"repeat", "refuse_repeat"}:
        intro = (
            f"Following the final jury of the {degree}, "
            f"academic year {ay}, please find below your official results and the jury decision."
        )
        decision_note = (
            "\nIf you have any questions regarding this decision, "
            "please contact your track responsibles (see below)."
        )
    else:
        intro = (
            f"Following the final jury of the {degree}, "
            f"academic year {ay}, please find below your official results."
        )
        decision_note = ""

    has_cert = certificate_path is not None and Path(certificate_path).is_file()
    if has_cert:
        attach_line = (
            "This message is sent individually. The attached documents are your official "
            "final transcript and Certificate of Achievement in English (MNE format)."
        )
    else:
        attach_line = (
            "This message is sent individually. The attached document is your official "
            "final transcript in English (MNE format)."
        )

    return f"""Dear {fn},

{intro}

Final average (including jury deliberation): {avg_line}
Honours (mention): {mention}
Track ranking: {ranking_track}
Cohort ranking: {ranking_cohort}
Jury decision: {jury_decision}{decision_note}
{attach_hint}
{attach_line}
{contact}
Best regards,

{signature}
""".strip()


def gather_final_transcript_notification(
    repo,
    *,
    template_id: int,
    student_id: int,
    jury_session_id: int | None = None,
    pdf_path: Path | None = None,
    view_session: str = "s2",
    certificate_path: Path | None = None,
) -> FinalTranscriptNotification:
    tid, sid = int(template_id), int(student_id)
    tpl = repo.get_template(tid) or {}
    stu = repo.get_student(sid) or {}
    vs = str(view_session or "mixed").strip().lower()
    row = next(
        (
            r
            for r in repo.get_student_result_summary(
                tid, view_session=vs, include_all_students=True
            )
            if int(r.get("student_id") or 0) == sid
        ),
        None,
    )
    gwj = row.get("global_with_jury") if row else None
    avg = float(gwj) if gwj is not None else None

    emails, _missing = build_student_email_list([stu], EMAIL_MODE_INST_OR_PERSONAL)
    email = emails[0] if emails else ""
    has_email = bool(email)

    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    program = track_program_label(lv, tr)

    mention = mention_for_student(repo, student_id=sid, template_id=tid, grade=avg)
    ranking_track, ranking_cohort = rankings_english(
        repo, template_id=tid, student_id=sid, view_session=vs
    )
    ranking = ranking_english(repo, template_id=tid, student_id=sid, view_session=vs)
    decision = jury_decision_english(
        repo,
        student_id=sid,
        template_id=tid,
        jury_session_id=jury_session_id,
        result_row=row,
        view_session=vs,
    )
    oc = repo.get_jury_student_outcome(
        sid, tid, jury_session_id=jury_session_id
    )
    outcome_key = str((oc or {}).get("outcome") or "").strip()
    if not outcome_key and row is not None:
        ev = repo.evaluate_student_year_validation(
            sid, tid, view_session=vs, result_row=row
        )
        outcome_key = str(ev.get("suggested_outcome") or "")
    contact = repo.transcript_header_emails(tid)
    cc_emails = repo.jury_notification_cc_emails(tid)

    subject = build_final_transcript_email_subject(
        academic_year=ay,
        level=lv,
        track=tr,
        last_name=str(stu.get("last_name") or ""),
        first_name=str(stu.get("first_name") or ""),
    )
    body = build_final_transcript_email_body(
        first_name=str(stu.get("first_name") or ""),
        academic_year=ay,
        level=lv,
        track=tr,
        program=program,
        average=avg,
        mention=mention,
        ranking_track=ranking_track,
        ranking_cohort=ranking_cohort,
        jury_decision=decision,
        outcome_key=outcome_key,
        pdf_path=pdf_path,
        contact_emails=contact,
        certificate_path=certificate_path,
    )
    name = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip()
    return FinalTranscriptNotification(
        student_id=sid,
        student_name=name,
        first_name=str(stu.get("first_name") or ""),
        email=email,
        has_email=has_email,
        subject=subject,
        body=body,
        pdf_path=pdf_path,
        average=avg,
        mention=mention,
        ranking=ranking,
        jury_decision=decision,
        cc_emails=cc_emails,
        certificate_path=certificate_path,
    )


def gather_jury_student_notification(
    repo,
    *,
    template_id: int,
    student_id: int,
    jury_session_id: int | None = None,
    session_kind: str = "FINAL",
    pdf_path: Path | None = None,
    view_session: str | None = None,
    certificate_path: Path | None = None,
) -> FinalTranscriptNotification:
    kind = str(session_kind or "FINAL").strip().upper()
    if kind == "FINAL":
        vs = str(view_session or "mixed").strip().lower()
        notif = gather_final_transcript_notification(
            repo,
            template_id=int(template_id),
            student_id=int(student_id),
            jury_session_id=jury_session_id,
            pdf_path=pdf_path,
            view_session=vs,
            certificate_path=certificate_path,
        )
        return notif

    tid, sid = int(template_id), int(student_id)
    tpl = repo.get_template(tid) or {}
    stu = repo.get_student(sid) or {}
    vs = str(view_session or ("s1" if kind == "S1" else "s2")).strip().lower()
    row = next(
        (
            r
            for r in repo.get_student_result_summary(
                tid, view_session=vs, include_all_students=True
            )
            if int(r.get("student_id") or 0) == sid
        ),
        None,
    )
    gwj = row.get("global_with_jury") if row else None
    avg = float(gwj) if gwj is not None else None

    emails, _missing = build_student_email_list([stu], EMAIL_MODE_INST_OR_PERSONAL)
    email = emails[0] if emails else ""
    has_email = bool(email)

    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    program = track_program_label(lv, tr)
    retake_text = retake_courses_english(
        repo, template_id=tid, student_id=sid, view_session=vs
    )
    contact = repo.transcript_header_emails(tid)
    cc_emails = repo.jury_notification_cc_emails(tid)

    subject = build_intermediate_jury_email_subject(
        session_kind=kind,
        academic_year=ay,
        level=lv,
        track=tr,
        last_name=str(stu.get("last_name") or ""),
        first_name=str(stu.get("first_name") or ""),
    )
    body = build_intermediate_jury_email_body(
        first_name=str(stu.get("first_name") or ""),
        academic_year=ay,
        level=lv,
        track=tr,
        session_kind=kind,
        average=avg,
        retake_text=retake_text,
        pdf_path=pdf_path,
        contact_emails=contact,
    )
    name = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip()
    return FinalTranscriptNotification(
        student_id=sid,
        student_name=name,
        first_name=str(stu.get("first_name") or ""),
        email=email,
        has_email=has_email,
        subject=subject,
        body=body,
        pdf_path=pdf_path,
        average=avg,
        mention="—",
        ranking="—",
        jury_decision=retake_text[:120],
        cc_emails=cc_emails,
    )
