from __future__ import annotations

import re
import unicodedata
from typing import Any

TRACKS: dict[str, str] = {
    # M1
    "C": "Chemistry & Engineering",
    "P": "Physics & Engineering",
    "NFC": "Nuclear Fuel Cycle",
    "DWM": "Dismantling & Waste Management",
    "NPO": "Nuclear Plant Operation",
    "NPD": "Nuclear Plant Design",
    "NRPE": "Nuclear Reactor Physics & Engineering",
}

INSTITUTIONAL_EMAIL_DOMAINS: tuple[str, ...] = (
    "@universite-paris-saclay.fr",  # UPSay
    "@etu.chimieparistech.psl.eu",  # PSL / Chimie Paris
    "@ensta.fr",
    "@ip-paris.fr",  # IP Paris
    "@etu.u-paris.fr",  # IP Paris (variante dossiers candidature)
)

_INSTITUTION_TO_DOMAIN: dict[str, str] = {
    "Université Paris-Saclay": "universite-paris-saclay.fr",
    "Institut Polytechnique de Paris": "ip-paris.fr",
    "ENSTA Paris": "ensta.fr",
    "Chimie Paris PSL": "etu.chimieparistech.psl.eu",
}

_DEFAULT_INSTITUTIONAL_DOMAIN = "universite-paris-saclay.fr"


def _slug_email_part(value: str) -> str:
    """
    Transforme un prénom/nom en partie email ASCII.
    Ex: "Prud'homme" -> "prudhomme", "Jean Marc" -> "jean.marc".
    """
    s = (value or "").strip().lower()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'")
    s = re.sub(r"[\s\-]+", ".", s)
    s = re.sub(r"[^a-z0-9.']", "", s)
    s = s.replace("'", "")
    s = re.sub(r"\.{2,}", ".", s).strip(".")
    return s


def institutional_email_domain(enrollment_institution: str) -> str:
    """Domaine email étudiant attendu pour un établissement d'inscription."""
    inst = (enrollment_institution or "").strip()
    if inst in _INSTITUTION_TO_DOMAIN:
        return _INSTITUTION_TO_DOMAIN[inst]

    low = inst.lower().replace("’", "'")
    compact = re.sub(r"[^a-z0-9]+", "", low)
    if "saclay" in low or "upsay" in low or compact.startswith("ufrsciences"):
        return _INSTITUTION_TO_DOMAIN["Université Paris-Saclay"]
    if "ensta" in low:
        return _INSTITUTION_TO_DOMAIN["ENSTA Paris"]
    if ("chimie" in low and "psl" in low) or "chimieparis" in compact:
        return _INSTITUTION_TO_DOMAIN["Chimie Paris PSL"]
    if (
        "ip paris" in low
        or "ip-paris" in low
        or "polytechnique de paris" in low
        or "institutpolytechniquedeparis" in compact
    ):
        return _INSTITUTION_TO_DOMAIN["Institut Polytechnique de Paris"]
    return _DEFAULT_INSTITUTIONAL_DOMAIN


def _email_domain(email: str) -> str:
    v = normalize_email(email).lower()
    if "@" not in v:
        return ""
    return v.rsplit("@", 1)[-1]


def institutional_email_matches_institution(email: str, enrollment_institution: str) -> bool:
    """Vrai si le domaine de l'email correspond à l'établissement d'inscription."""
    domain = _email_domain(email)
    if not domain:
        return False
    expected = institutional_email_domain(enrollment_institution)
    if domain == expected:
        return True
    if expected == "ip-paris.fr" and domain == "etu.u-paris.fr":
        return True
    return False


def suggest_institutional_email(first_name: str, last_name: str, enrollment_institution: str) -> str:
    """Suggestion `prenom.nom@domaine` selon établissement d'inscription."""
    fn = _slug_email_part(first_name)
    ln = _slug_email_part(last_name)
    if not fn or not ln:
        return ""
    domain = institutional_email_domain(enrollment_institution)
    return f"{fn}.{ln}@{domain}"


def adapt_institutional_email(
    first_name: str,
    last_name: str,
    enrollment_institution: str,
    current_email: str = "",
) -> str:
    """
    Conserve l'email s'il correspond déjà à l'établissement ;
    sinon propose ``prenom.nom@domaine`` adapté.
    """
    current = normalize_email(current_email)
    suggested = suggest_institutional_email(first_name, last_name, enrollment_institution)
    if not suggested:
        return current
    if not current:
        return suggested
    if institutional_email_matches_institution(current, enrollment_institution):
        return current
    return suggested


def normalize_level(value: str) -> str:
    """Normalise l'année (M1/M2). Retourne '' si vide."""
    v = (value or "").strip().upper()
    if not v:
        return ""
    if v in {"M1", "M2"}:
        return v
    # Tolérances fréquentes
    v = v.replace(" ", "")
    if v in {"MASTER1", "MASTER01"}:
        return "M1"
    if v in {"MASTER2", "MASTER02"}:
        return "M2"
    return v


def normalize_track_acronym(value: str) -> str:
    """Normalise l'acronyme de parcours. Retourne '' si vide."""
    v = (value or "").strip().upper().replace(" ", "")
    # Harmonisation : on stocke désormais M1 en codes courts P/C
    if v == "M1P":
        return "P"
    if v == "M1C":
        return "C"
    return v


def track_full_name(acronym: str) -> str:
    """Nom complet du parcours à partir de l'acronyme (ou acronyme si inconnu)."""
    a = normalize_track_acronym(acronym)
    return TRACKS.get(a, a)


def normalize_email(value: str) -> str:
    return (value or "").strip()


def is_valid_institutional_email(value: str) -> bool:
    v = normalize_email(value).lower()
    if not v:
        return True
    if "@" not in v:
        return False
    return any(v.endswith(d) for d in INSTITUTIONAL_EMAIL_DOMAINS)


# Genre en base : M, F, O ou chaîne vide (non renseigné).
GENDER_LABEL_FR: dict[str, str] = {
    "": "",
    "M": "Homme",
    "F": "Femme",
    "O": "Autre",
}


def gender_label_fr(code: str | None) -> str:
    c = (code or "").strip().upper()
    return GENDER_LABEL_FR.get(c, "")


def normalize_gender(value: Any) -> str:
    """Retourne M, F, O ou '' pour la colonne students.gender."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    s = str(value).strip().lower()
    if not s:
        return ""
    if s in {"homme", "h", "masculin", "male", "man", "monsieur", "mr", "m."}:
        return "M"
    if s in {"femme", "féminin", "feminin", "f", "f.", "female", "woman", "madame", "mme", "mlle", "mademoiselle"}:
        return "F"
    if s in {"autre", "other", "non-binaire", "non binaire", "nb", "x", "neutre", "non_specifie", "non spécifié", "non specifie"}:
        return "O"
    u = s.upper().replace(" ", "")
    if u in {"M", "F", "O"}:
        return u
    if u in {"H", "1"}:
        return "M"
    if u in {"2"}:
        return "F"
    return ""


def student_transcript_number(student: dict[str, Any]) -> str:
    """Numéro figurant sur les relevés : I.N.E. si renseigné, sinon n° MNE interne."""
    ine = str(student.get("student_number_ine") or "").strip()
    if ine:
        return ine
    return str(student.get("student_number") or "").strip()


def student_combo_label(student: dict[str, Any]) -> str:
    """Libellé liste déroulante : « Nom Prénom (I.N.E.) »."""
    name = f"{student.get('last_name', '')} {student.get('first_name', '')}".strip()
    num = student_transcript_number(student)
    return f"{name} ({num})" if num else name

