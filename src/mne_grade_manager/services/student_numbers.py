"""Numéro étudiant MNE interne — identifiant alphanumérique dérivé de l'identité."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.database import Database


def _ascii_slug(text: str, *, max_len: int = 10) -> str:
    """Nom / prénom → segment ASCII majuscules (accents retirés)."""
    s = unicodedata.normalize("NFKD", (text or "").strip())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Za-z0-9]", "", s).upper()
    if not s:
        return ""
    return s[:max_len]


def _first_initials(first_name: str, count: int = 2) -> str:
    parts = [p for p in re.split(r"[\s\-']+", (first_name or "").strip()) if p]
    if not parts:
        return "X" * count
    if len(parts) == 1:
        slug = _ascii_slug(parts[0], max_len=count)
    else:
        slug = _ascii_slug("".join(p[0] for p in parts), max_len=count)
    return (slug + "X" * count)[:count]


def identity_fingerprint(
    last_name: str,
    first_name: str,
    *,
    birth_date: str = "",
    email_institutional: str = "",
    student_number_ine: str = "",
    student_number_local: str = "",
) -> str:
    """Chaîne stable pour hacher — même étudiant → même empreinte."""
    return "|".join(
        [
            _ascii_slug(last_name, max_len=40),
            _ascii_slug(first_name, max_len=40),
            (birth_date or "").strip(),
            (email_institutional or "").strip().lower(),
            (student_number_ine or "").strip(),
            (student_number_local or "").strip(),
        ]
    )


def derive_student_number(
    last_name: str,
    first_name: str,
    *,
    birth_date: str = "",
    email_institutional: str = "",
    student_number_ine: str = "",
    student_number_local: str = "",
    salt: int = 0,
    code_len: int = 4,
) -> str:
    """
    Identifiant lisible du type ``MNE-DUPONT-JE-A7K2``.

    - ``DUPONT`` : nom (jusqu'à 10 caractères)
    - ``JE`` : initiales du prénom
    - ``A7K2`` : code alphanumérique issu d'un hash de l'identité (nom, prénom,
      date de naissance, email institutionnel, I.N.E., n° établissement)
    """
    nom = _ascii_slug(last_name, max_len=10) or "ETU"
    pre = _first_initials(first_name, 2)
    material = (
        f"{identity_fingerprint(last_name, first_name, birth_date=birth_date, email_institutional=email_institutional, student_number_ine=student_number_ine, student_number_local=student_number_local)}|{salt}"
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest().upper()
    code = digest[:code_len]
    return f"MNE-{nom}-{pre}-{code}"


def allocate_student_number(
    db: Database,
    *,
    last_name: str,
    first_name: str,
    birth_date: str = "",
    email_institutional: str = "",
    student_number_ine: str = "",
    student_number_local: str = "",
) -> str:
    """Numéro unique dérivé de l'étudiant (pas d'ordre d'enregistrement)."""
    for code_len in (4, 5, 6):
        for salt in range(0, 500):
            candidate = derive_student_number(
                last_name,
                first_name,
                birth_date=birth_date,
                email_institutional=email_institutional,
                student_number_ine=student_number_ine,
                student_number_local=student_number_local,
                salt=salt,
                code_len=code_len,
            )
            if not db.query_one("SELECT 1 FROM students WHERE student_number = ?", (candidate,)):
                return candidate
    raise RuntimeError("Impossible de générer un numéro étudiant unique.")
