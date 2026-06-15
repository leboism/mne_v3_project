"""Construction de listes d'adresses e-mail à partir d'étudiants."""

from __future__ import annotations

from typing import Any

EMAIL_MODE_INSTITUTIONAL = "institutional"
EMAIL_MODE_PERSONAL = "personal"
EMAIL_MODE_INST_OR_PERSONAL = "inst_or_personal"
EMAIL_MODE_BOTH = "both"

EMAIL_FORMAT_LINES = "lines"
EMAIL_FORMAT_SEMICOLON = "semicolon"
EMAIL_FORMAT_COMMA = "comma"


def _student_email_fields(student: dict[str, Any]) -> tuple[str, str]:
    inst = str(student.get("email_institutional") or "").strip()
    pers = str(student.get("email_personal") or "").strip()
    return inst, pers


def student_institutional_emails(students: list[dict[str, Any]]) -> list[str]:
    """Adresses institutionnelles, sinon personnelles (une par étudiant, sans doublon)."""
    emails, _ = build_student_email_list(students, EMAIL_MODE_INST_OR_PERSONAL)
    return emails


def build_student_email_list(
    students: list[dict[str, Any]],
    mode: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Retourne (adresses uniques triées, étudiants sans adresse selon le mode choisi).
    """
    collected: list[str] = []
    seen: set[str] = set()
    missing: list[dict[str, Any]] = []

    for student in students:
        inst, pers = _student_email_fields(student)
        student_emails: list[str] = []

        if mode == EMAIL_MODE_INSTITUTIONAL:
            if inst:
                student_emails = [inst]
        elif mode == EMAIL_MODE_PERSONAL:
            if pers:
                student_emails = [pers]
        elif mode == EMAIL_MODE_BOTH:
            if inst:
                student_emails.append(inst)
            if pers and pers.lower() != (inst.lower() if inst else ""):
                student_emails.append(pers)
        else:
            chosen = inst or pers
            if chosen:
                student_emails = [chosen]

        if not student_emails:
            missing.append(student)
            continue

        for email in student_emails:
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            collected.append(email)

    collected.sort(key=str.lower)
    return collected, missing


def format_email_block(emails: list[str], fmt: str) -> str:
    if not emails:
        return ""
    if fmt == EMAIL_FORMAT_SEMICOLON:
        return "; ".join(emails)
    if fmt == EMAIL_FORMAT_COMMA:
        return ", ".join(emails)
    return "\n".join(emails)


def parse_email_block(text: str) -> list[str]:
    """Extrait les adresses d'un bloc édité (lignes ou séparateurs , ;)."""
    raw = text.replace(";", "\n").replace(",", "\n")
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        email = line.strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out
