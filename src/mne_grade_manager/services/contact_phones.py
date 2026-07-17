"""Téléphones de contact : jusqu'à 2 pro + 1 portable."""

from __future__ import annotations

from typing import Any

PHONE_WORK = "phone_work"
PHONE_WORK_2 = "phone_work_2"
PHONE_MOBILE = "phone_mobile"

PHONE_KEYS: tuple[str, str, str] = (PHONE_WORK, PHONE_WORK_2, PHONE_MOBILE)

PHONE_LABELS_FR: dict[str, str] = {
    PHONE_WORK: "Tél. professionnel 1",
    PHONE_WORK_2: "Tél. professionnel 2",
    PHONE_MOBILE: "Téléphone portable",
}

# Préfixe « teacher_ » pour la fiche cours.
TEACHER_PHONE_KEYS: tuple[str, str, str] = (
    "teacher_phone_work",
    "teacher_phone_work_2",
    "teacher_phone_mobile",
)


def prefixed_phone_keys(prefix: str = "") -> tuple[str, str, str]:
    if not prefix:
        return PHONE_KEYS
    p = prefix if prefix.endswith("_") else f"{prefix}_"
    return tuple(f"{p}{k}" for k in PHONE_KEYS)


def read_phones(row: dict[str, Any] | None, *, prefix: str = "") -> tuple[str, str, str]:
    """Lit (pro1, pro2, portable) avec repli sur l'ancien champ unique."""
    data = row or {}
    keys = prefixed_phone_keys(prefix)
    work = str(data.get(keys[0]) or "").strip()
    work2 = str(data.get(keys[1]) or "").strip()
    mobile = str(data.get(keys[2]) or "").strip()
    if not mobile:
        legacy = "teacher_phone" if prefix == "teacher" else "phone"
        mobile = str(data.get(legacy) or "").strip()
    return work, work2, mobile


def any_phone(*values: str) -> bool:
    return any(str(v or "").strip() for v in values)


def format_phones_line(row: dict[str, Any] | None, *, prefix: str = "") -> str:
    parts: list[str] = []
    keys = prefixed_phone_keys(prefix)
    labels = PHONE_LABELS_FR
    for key, label in zip(keys, labels.values()):
        val = str((row or {}).get(key) or "").strip()
        if val:
            parts.append(f"{label} : {val}")
    if not parts and prefix == "teacher":
        leg = str((row or {}).get("teacher_phone") or "").strip()
        if leg:
            parts.append(f"{PHONE_LABELS_FR[PHONE_MOBILE]} : {leg}")
    elif not parts and not prefix:
        leg = str((row or {}).get("phone") or "").strip()
        if leg:
            parts.append(f"{PHONE_LABELS_FR[PHONE_MOBILE]} : {leg}")
    return " · ".join(parts)


def legacy_single_phone(mobile: str, *, fallback: str = "") -> str:
    """Ancienne colonne unique = portable (ou repli)."""
    m = str(mobile or "").strip()
    if m:
        return m
    return str(fallback or "").strip()


def phone_storage_values(
    work: str = "",
    work2: str = "",
    mobile: str = "",
    *,
    prefix: str = "",
    legacy_fallback: str = "",
) -> dict[str, str]:
    """Valeurs normalisées pour INSERT/UPDATE (inclut l'ancienne colonne unique)."""
    keys = prefixed_phone_keys(prefix)
    legacy = "teacher_phone" if prefix == "teacher" else "phone"
    w = str(work or "").strip()
    w2 = str(work2 or "").strip()
    m = legacy_single_phone(mobile, fallback=legacy_fallback)
    return {keys[0]: w, keys[1]: w2, keys[2]: m, legacy: m}


def merge_phone_row(row: dict[str, Any] | None, updates: dict[str, Any], *, prefix: str = "") -> dict[str, str]:
    """Fusionne ligne + mises à jour partielles, retourne les 4 colonnes téléphone."""
    merged = dict(row or {})
    merged.update(updates)
    work, w2, mob = read_phones(merged, prefix=prefix)
    return phone_storage_values(work, w2, mob, prefix=prefix)
