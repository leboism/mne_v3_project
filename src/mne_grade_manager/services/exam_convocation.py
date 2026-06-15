"""Génération de convocation d'examen — e-mail en anglais adressé aux étudiants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .student_emails import student_institutional_emails

_EN_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_EN_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def format_exam_date_english(year: int, month: int, day: int) -> str:
    """e.g. Monday 15 June 2026"""
    from datetime import date

    d = date(int(year), int(month), int(day))
    wd = _EN_WEEKDAYS[d.weekday()]
    mo = _EN_MONTHS[d.month - 1]
    return f"{wd} {d.day} {mo} {d.year}"


@dataclass
class ConvocationParams:
    academic_year: str
    mne_module_code: str
    course_title: str
    exam_date: str
    start_time: str
    end_time: str
    location: str
    exam_format: str
    session: int
    extra_notes: str
    teacher_name: str
    teacher_email: str
    apogee_code: str = ""
    curricula_summary: str = ""


def build_email_subject(params: ConvocationParams) -> str:
    """
    Objet du mail : le code MNE en tête (ex. M1B1-C-NUCL) pour que les étudiants
    reconnaissent le module sur l'emploi du temps, puis l'intitulé.
    """
    code = (params.mne_module_code or "").strip()
    title = (params.course_title or "").strip()
    year = (params.academic_year or "").strip()
    sess = int(params.session) if params.session in (1, 2) else params.session

    if code and title:
        base = f"{code} — {title} — MNE examination (session {sess})"
    elif code:
        base = f"{code} — MNE examination convocation (session {sess})"
    elif title:
        base = f"{title} — MNE examination convocation (session {sess})"
    else:
        base = f"MNE examination convocation (session {sess})"

    return f"{base} — {year}" if year else base


def format_curricula_summary(templates: list[dict[str, Any]]) -> str:
    """Libellé des maquettes / parcours concernés (module commun)."""
    parts: list[str] = []
    for t in templates:
        name = str(t.get("name") or "").strip()
        lv = str(t.get("level") or "").strip()
        tr = str(t.get("track") or "").strip()
        bit = " ".join(x for x in (lv, tr) if x)
        if name and bit:
            parts.append(f"{name} ({bit})")
        elif name:
            parts.append(name)
        elif bit:
            parts.append(bit)
    return "; ".join(parts)


def build_convocation_email(
    params: ConvocationParams,
    students: list[dict[str, Any]],
) -> tuple[str, str, str]:
    """
    Retourne (subject, body, emails_block).

    Le corps du message s'adresse directement aux étudiants.
    ``emails_block`` : adresses institutionnelles (une par ligne) pour le champ To/Bcc.
    """
    emails = student_institutional_emails(students)
    year = (params.academic_year or "").strip() or "—"
    code = (params.mne_module_code or "").strip() or "—"
    title = (params.course_title or "").strip() or "—"
    loc = (params.location or "").strip() or "INSTN (CEA Saclay) — room to be confirmed"
    fmt = (params.exam_format or "").strip() or "Written examination"
    sess = f"Session {int(params.session)}" if params.session in (1, 2) else str(params.session)
    date_s = (params.exam_date or "").strip() or "—"
    t0 = (params.start_time or "").strip() or "—"
    t1 = (params.end_time or "").strip() or "—"
    teacher = (params.teacher_name or "").strip()
    teacher_mail = (params.teacher_email or "").strip()
    sig = ""
    if teacher:
        sig = f"\n{teacher}"
    if teacher_mail:
        sig += f"\n{teacher_mail}"

    subject = build_email_subject(params)

    body = f"""Dear students,

You are hereby convoked to the following examination for the Master of Science Nuclear Energy (MNE), academic year {year}.

Module acronym (MNE): {code}
Module title: {title}
"""

    curricula = (params.curricula_summary or "").strip()
    if curricula:
        body += f"This examination concerns students enrolled in: {curricula}.\n"

    body += f"""
Examination date: {date_s}
Time: {t0} – {t1}
Location: {loc}
Assessment: {fmt} ({sess})

Please arrive on time and bring your student card and a valid ID (national identity card or passport) if required for campus access.
Standard MNE time slots (if applicable): 9:00–12:15 (morning) or 13:15–16:30 (afternoon), including a 15-minute break.
If the room is not specified above, the examination takes place at INSTN unless otherwise stated.

"""

    if params.extra_notes.strip():
        body += f"{params.extra_notes.strip()}\n\n"

    body += (
        "If you have any questions or cannot attend, please contact the course instructor "
        "or the MNE secretariat as soon as possible.\n\n"
        f"Best regards,{sig}"
    )

    emails_block = (
        "\n".join(emails)
        if emails
        else "(no institutional e-mail on file — check student records in the application)"
    )

    return subject, body.strip(), emails_block
