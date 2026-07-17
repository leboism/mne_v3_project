"""Emails de contact : jusqu'à 2 pro + 1 personnel."""

from __future__ import annotations

from typing import Any

EMAIL_WORK = "email_work"
EMAIL_WORK_2 = "email_work_2"
EMAIL_PERSONAL = "email_personal"

EMAIL_KEYS: tuple[str, str, str] = (EMAIL_WORK, EMAIL_WORK_2, EMAIL_PERSONAL)

EMAIL_LABELS_FR: dict[str, str] = {
    EMAIL_WORK: "Email professionnel 1",
    EMAIL_WORK_2: "Email professionnel 2",
    EMAIL_PERSONAL: "Email personnel",
}


def prefixed_email_keys(prefix: str = "") -> tuple[str, str, str]:
    if not prefix:
        return EMAIL_KEYS
    p = prefix if prefix.endswith("_") else f"{prefix}_"
    return tuple(f"{p}{k}" for k in EMAIL_KEYS)


def read_emails(row: dict[str, Any] | None, *, prefix: str = "") -> tuple[str, str, str]:
    """Lit (pro1, pro2, personnel) avec repli sur l'ancien champ unique."""
    data = row or {}
    keys = prefixed_email_keys(prefix)
    work = str(data.get(keys[0]) or "").strip()
    work2 = str(data.get(keys[1]) or "").strip()
    personal = str(data.get(keys[2]) or "").strip()
    if not work:
        legacy = "teacher_email" if prefix == "teacher" else "email"
        work = str(data.get(legacy) or "").strip()
    return work, work2, personal


def primary_email(row: dict[str, Any] | None, *, prefix: str = "") -> str:
    for val in read_emails(row, prefix=prefix):
        if val:
            return val
    return ""


def all_emails(row: dict[str, Any] | None, *, prefix: str = "") -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for val in read_emails(row, prefix=prefix):
        v = str(val or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def any_email(*values: str) -> bool:
    return any(str(v or "").strip() for v in values)


def format_emails_line(row: dict[str, Any] | None, *, prefix: str = "") -> str:
    parts: list[str] = []
    keys = prefixed_email_keys(prefix)
    for key, label in zip(keys, EMAIL_LABELS_FR.values()):
        val = str((row or {}).get(key) or "").strip()
        if val:
            parts.append(f"{label} : {val}")
    if not parts:
        leg = primary_email(row, prefix=prefix)
        if leg:
            parts.append(f"{EMAIL_LABELS_FR[EMAIL_WORK]} : {leg}")
    return " · ".join(parts)


def email_storage_values(
    work: str = "",
    work2: str = "",
    personal: str = "",
    *,
    prefix: str = "",
    legacy_fallback: str = "",
) -> dict[str, str]:
    keys = prefixed_email_keys(prefix)
    legacy = "teacher_email" if prefix == "teacher" else "email"
    w = str(work or "").strip() or str(legacy_fallback or "").strip()
    w2 = str(work2 or "").strip()
    p = str(personal or "").strip()
    return {keys[0]: w, keys[1]: w2, keys[2]: p, legacy: w}


def merge_email_row(row: dict[str, Any] | None, updates: dict[str, Any], *, prefix: str = "") -> dict[str, str]:
    merged = dict(row or {})
    merged.update(updates)
    work, w2, pers = read_emails(merged, prefix=prefix)
    return email_storage_values(work, w2, pers, prefix=prefix)
