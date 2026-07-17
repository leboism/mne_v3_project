"""Import de dossiers de candidature PDF (IPParis, UPSay, Mon Master)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .admission_photo import extract_candidate_photo_from_pdf
from .dates import normalize_birth_date_iso
from .lookups import adapt_institutional_email, normalize_email, normalize_gender, normalize_track_acronym

ADMISSION_SOURCES = ("IPParis", "UPSay", "MonMaster")

_INE_RE = re.compile(r"\b(?:INE\s*(?:maître\s*\(INES\)|maitre\s*\(INES\))?|Numéro\s+INE)\s*[:\s]*([0-9]{9,11}[A-Z]{2})\b", re.I)
_INE_FALLBACK_RE = re.compile(r"\b([0-9]{9,11}[A-Z]{2})\b")
_APOGEE_RE = re.compile(r"N[°o]\s*Etudiant\s*:\s*(\d+)", re.I)
_SESSION_YEAR_RE = re.compile(r"SESSION\s+(\d{4})/(\d{4})", re.I)
_MONMASTER_CAND_RE = re.compile(r"Candidat\s+(CAND[A-Z0-9]+)", re.I)
_IPPARIS_DOSSIER_RE = re.compile(r"Personal information\s*-\s*(\d+)", re.I)
_UPSAY_DOSSIER_RE = re.compile(r"(DF\d{3}(?:-[A-Z0-9]+)?)", re.I)
_MONMASTER_CAND_REF_RE = re.compile(r"(CAND[A-Z0-9]{4,})")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


@dataclass
class AdmissionDossier:
    source: str
    source_file: str
    last_name: str = ""
    first_name: str = ""
    gender: str = ""
    birth_date: str = ""
    birth_place: str = ""
    nationality: str = ""
    student_number_ine: str = ""
    student_number_local: str = ""
    email_personal: str = ""
    email_institutional: str = ""
    application_platform: str = ""
    monmaster_channel: str = ""
    enrollment_institution: str = ""
    origin_institution: str = ""
    origin_institution_country: str = ""
    highest_diploma: str = ""
    level: str = "M1"
    track: str = ""
    academic_year: str = ""
    candidature_ref: str = ""
    notes: str = ""
    photo_found: bool = False
    extracted_photo: object | None = None
    warnings: list[str] = field(default_factory=list)
    parse_error: str = ""
    existing_student_id: int | None = None
    existing_match_reason: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()

    @property
    def has_existing_match(self) -> bool:
        return self.existing_student_id is not None

    @property
    def importable(self) -> bool:
        return bool(self.last_name and self.first_name) and not self.parse_error


def _pdf_text(path: Path, *, max_pages: int | None = None) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = reader.pages[:max_pages] if max_pages else reader.pages
    chunks: list[str] = []
    for page in pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\r", "\n").split("\n")]


def _value_on_line(line: str, label: str) -> str:
    low = line.lower()
    lab = label.lower()
    if lab not in low:
        return ""
    idx = low.find(lab)
    return line[idx + len(label) :].strip(" :\t")


def _find_value(lines: Iterable[str], label: str, *, stop_labels: tuple[str, ...] = ()) -> str:
    label_clean = label.strip().lower()
    stop = tuple(s.lower() for s in stop_labels)
    line_list = [ln.strip() for ln in lines if ln is not None]
    for i, line in enumerate(line_list):
        low = line.lower()
        if any(low.startswith(s) for s in stop if s):
            break
        val = _value_on_line(line, label)
        if val:
            return val
        if low.rstrip(":") == label_clean:
            for j in range(i + 1, min(i + 4, len(line_list))):
                nxt = line_list[j].strip()
                if nxt:
                    return nxt
    return ""


def _session_academic_year(text: str) -> str:
    m = _SESSION_YEAR_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


def _academic_year_from_path(path: Path) -> str:
    """Déduit le millésime depuis le chemin (ex. …/2026/… → 2026-2027)."""
    posix = path.as_posix()
    for pattern in (
        r"/20(\d{2})(?:/|:)",
        r"\\20(\d{2})(?:\\|:)",
        r"admission[/\\]20(\d{2})",
    ):
        m = re.search(pattern, posix, re.I)
        if m:
            y = 2000 + int(m.group(1))
            return f"{y}-{y + 1}"
    m2 = re.search(r"candidatures_20(\d{2})_", path.name, re.I)
    if m2:
        y = 2000 + int(m2.group(1))
        return f"{y}-{y + 1}"
    return ""


def _refs_from_filename(path: Path) -> tuple[str, str]:
    """Références établissement / candidature depuis le nom de fichier."""
    stem = path.stem.strip()
    upsay_ref = ""
    m = _UPSAY_DOSSIER_RE.search(stem)
    if m:
        upsay_ref = m.group(1).upper()
    cand_ref = ""
    m2 = _MONMASTER_CAND_REF_RE.search(stem)
    if m2:
        cand_ref = m2.group(1).upper()
    return upsay_ref, cand_ref


def _slug_part(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())
    return s


def _infer_surname_from_email(email: str, first_name: str) -> str:
    """Ex. angel16acosta@gmail.com + Angel → Acosta."""
    raw = (email or "").split("@")[0].lower()
    if not raw:
        return ""
    fn = _slug_part(first_name)
    compact = re.sub(r"\d+", "", raw)
    if fn and compact.endswith(fn) and len(compact) > len(fn):
        tail = compact[: -len(fn)].strip("._-")
        if len(tail) >= 3:
            return tail.capitalize()
    m = re.search(r"(?:\d+)?([a-z]{3,})$", raw)
    if m:
        cand = m.group(1)
        if cand != fn and len(cand) >= 3:
            return cand.capitalize()
    return ""


def _scan_surname_from_text(text: str, first_name: str) -> str:
    """Repère le nom dans les pièces jointes citées (Angel_Acosta_CV…)."""
    fn = (first_name or "").strip()
    if not fn:
        return ""
    for pattern in (
        rf"\b{re.escape(fn)}_([A-Za-z]{{3,}})",
        rf"\b([A-Za-z]{{3,}})_{re.escape(fn)}\b",
        rf"\b{re.escape(fn)}\s+([A-Z][a-z]{{2,}})",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return ""


def _normalize_person_name(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.isupper() and len(raw) > 2:
        return raw.title()
    return raw


def _finalize_admission_identity(dossier: AdmissionDossier, text: str, path: Path) -> None:
    """Complète nom / n° établissement / année à partir du fichier et du texte."""
    upsay_ref, cand_ref = _refs_from_filename(path)
    if upsay_ref and not dossier.candidature_ref:
        dossier.candidature_ref = upsay_ref
    if cand_ref and not dossier.candidature_ref:
        dossier.candidature_ref = cand_ref

    if not dossier.student_number_local:
        if upsay_ref:
            dossier.student_number_local = upsay_ref
        elif cand_ref:
            dossier.student_number_local = cand_ref

    ln = dossier.last_name.strip()
    fn = dossier.first_name.strip()
    if ln and fn and ln.lower() == fn.lower():
        inferred = _infer_surname_from_email(dossier.email_personal, fn)
        if not inferred:
            inferred = _scan_surname_from_text(text, fn)
        if inferred and inferred.lower() != fn.lower():
            dossier.last_name = inferred

    dossier.last_name = _normalize_person_name(dossier.last_name)
    dossier.first_name = _normalize_person_name(dossier.first_name)

    if not dossier.academic_year:
        dossier.academic_year = _academic_year_from_path(path)

    if not dossier.track:
        dossier.track = _track_from_path(path)
    if dossier.track and not dossier.level:
        dossier.level = "M1"


def _track_from_path(path: Path) -> str:
    name = path.as_posix().upper()
    if "M1C" in name or "/M1 C" in name or "M1_C" in name:
        return "C"
    if "M1P" in name or "/M1 P" in name or "M1_P" in name:
        return "P"
    return ""


def _scan_ine(text: str) -> str:
    for pattern in (_INE_RE, _INE_FALLBACK_RE):
        for m in pattern.finditer(text):
            candidate = m.group(1).upper()
            if len(candidate) >= 11:
                return candidate
    return ""


def _scan_apogee(text: str) -> str:
    m = _APOGEE_RE.search(text)
    return m.group(1).strip() if m else ""


def _is_ipparis_dossier(head: str) -> bool:
    """Ancien format (2025) : « Family Name of the applicant » ; nouveau (2026) : « Family name » + SESSION IP Paris."""
    if "family name of the applicant" in head:
        return True
    if "ip paris" not in head:
        return False
    if re.search(r"session\s+\d{4}/\d{4}", head) and (
        "m1 mne" in head or "nuclear engineering" in head
    ):
        return True
    if re.search(r"\bfamily name\b", head) and re.search(r"\bfirst name\b", head):
        if "m1 mne" in head or "nuclear engineering" in head:
            return True
    return False


def _detect_source(text: str) -> str:
    """Détection sur les premières pages (évite les PDF composites Mon Master + pièces jointes)."""
    head = "\n".join(_lines(text)[:100]).lower()
    if _is_ipparis_dossier(head):
        return "IPParis"
    if "mes informations personnelles" in head and "nom de naissance" in head:
        return "MonMaster"
    if ("état civil" in head or "etat civil" in head) and re.search(r"\bnom\s*:", head, re.I):
        return "UPSay"
    return ""


def _parse_monmaster_channel(text: str, path: Path) -> tuple[str, str]:
    """Retourne (canal Mon Master, établissement d'inscription suggéré)."""
    path_low = path.as_posix().lower()
    if "psl-mm" in path_low or "dossier psl" in path_low:
        return "ChimieParis", "Chimie Paris PSL"
    if "upsay" in path_low or "paris-saclay" in path_low or "saclay" in path_low:
        return "UPSay", "Université Paris-Saclay"
    if "ipparis" in path_low or "ip-paris" in path_low or "/ipparis/" in path_low:
        return "IPParis", "Institut Polytechnique de Paris"

    h = text[:4000]
    if re.search(
        r"université paris.?sciences et lettres|universite paris.?sciences et lettres",
        h,
        re.I,
    ):
        return "ChimieParis", "Chimie Paris PSL"
    if re.search(r"université paris-saclay|universite paris-saclay", h, re.I):
        return "UPSay", "Université Paris-Saclay"
    if re.search(r"\bPSL\b|chimieparis|chimie paris", h, re.I):
        return "ChimieParis", "Chimie Paris PSL"
    if re.search(r"ip paris|institut polytechnique de paris", h, re.I):
        return "IPParis", "Institut Polytechnique de Paris"
    return "", ""


def _join_diploma_parts(*parts: str) -> str:
    cleaned = [
        p.strip()
        for p in parts
        if p and p.strip() and p.strip().lower() not in {"information non fournie", "non fourni"}
    ]
    return " — ".join(cleaned)


def _ipparis_degree_label(value: str) -> str:
    label = value.strip().rstrip(":").strip()
    if not label or label == "-" or label.lower() == "other":
        return ""
    return label


def _ipparis_identity_lines(lines: list[str]) -> list[str]:
    stop_prefixes = (
        "recommendation 1",
        "referee 1",
        "permanent adress",
        "permanent address",
        "emergency contact",
        "page 1/",
    )
    end = min(40, len(lines))
    for i, ln in enumerate(lines):
        low = ln.lower().strip()
        if any(low.startswith(p) for p in stop_prefixes):
            end = i
            break
    return lines[:end]


def _parse_ipparis_highest_diploma(text: str) -> str:
    m = re.search(
        r"Current or last institution.*?Degree/Qualification pursued\s+(.+?)(?:\n|Level)",
        text,
        re.I | re.S,
    )
    if m:
        val = _ipparis_degree_label(m.group(1))
        if val:
            return val

    m2 = re.search(
        r"Current institution\s+Country\s+.+?\s+Name of institution\s+(.+?)\s+"
        r"Degree\s*/\s*Qualification pursued\s+(.+?)\s+Level\s+(.+?)(?:\n|Main)",
        text,
        re.I | re.S,
    )
    if m2:
        inst = m2.group(1).strip()
        degree = _ipparis_degree_label(m2.group(2))
        level = m2.group(3).strip()
        diploma = degree or (level if level != "-" else "")
        return _join_diploma_parts(diploma, inst)

    lines = _lines(text)
    degree = _ipparis_degree_label(
        _find_value(lines, "Degree/Qualification pursued")
        or _find_value(lines, "Degree / Qualification pursued")
    )
    level = _find_value(lines, "Level")
    inst = _find_value(lines, "Name of institution") or _find_value(lines, "Name of the institution")
    diploma = degree or (level if level and level != "-" else "")
    return _join_diploma_parts(diploma, inst)


def _parse_upsay_highest_diploma(text: str) -> str:
    m_actuel = re.search(
        r"Cursus actuel\s+Type de diplôme\s*:\s*(.+?)\s+Formation\s*:\s*(.+?)\s+Etablissement\s*:\s*(.+?)\s+Ville",
        text,
        re.I | re.S,
    )
    if m_actuel:
        dtype = m_actuel.group(1).strip()
        if dtype and not re.match(r"^(formation|etablissement|ville|pays|souhaits)\b", dtype, re.I):
            return _join_diploma_parts(dtype, m_actuel.group(2), m_actuel.group(3))

    m_prev = re.search(
        r"Cursus précédent\s+Type de diplôme\s*:\s*(.+?)\s+Formation\s*:\s*(.+?)\s+Etablissement\s*:\s*(.+?)\s+Ville",
        text,
        re.I | re.S,
    )
    if m_prev:
        return _join_diploma_parts(m_prev.group(1), m_prev.group(2), m_prev.group(3))

    m = re.search(
        r"Cursus actuel.*?Type de diplôme\s*:\s*(.+?)\s+Formation\s*:\s*(.+?)\s+Etablissement",
        text,
        re.I | re.S,
    )
    if m:
        dtype = m.group(1).strip()
        if dtype:
            return _join_diploma_parts(dtype, m.group(2))
    m2 = re.search(
        r"Cursus précédent.*?Type de diplôme\s*:\s*(.+?)\s+Formation\s*:\s*(.+?)\s+Etablissement",
        text,
        re.I | re.S,
    )
    if m2:
        return _join_diploma_parts(m2.group(1), m2.group(2))
    return ""


def _parse_monmaster_highest_diploma(text: str) -> str:
    idx = text.find("Mon cursus post-baccalauréat")
    if idx < 0:
        return ""
    section_lines = _lines(text[idx : idx + 3500])
    return _join_diploma_parts(
        _find_value(section_lines, "Type de formation ou de diplôme préparé"),
        _find_value(section_lines, "Mention ou spécialité"),
        _find_value(section_lines, "Niveau post-bac du diplôme préparé"),
    )


def _parse_monmaster_track(text: str, path: Path) -> str:
    m = re.search(
        r"Précisez la spécialité choisie\s*:?\s*(Physique|Chimie)\b",
        text,
        re.I,
    )
    if m:
        return "P" if m.group(1).lower().startswith("phys") else "C"

    header = text[:2500]
    if re.search(r"Majeure\s+Physique\b", header, re.I):
        return "P"
    if re.search(r"Majeure\s+Chimie\b", header, re.I):
        return "C"

    path_track = _track_from_path(path)
    if path_track:
        return path_track
    return ""


def _parse_monmaster_email(text: str) -> str:
    for pattern in (
        r"Adresse e-mail\s+(\S+@\S+)",
        r"Email\s*\n\s*(\S+@\S+)",
        r"Email\s+(\S+@\S+)",
    ):
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _parse_upsay_etat_civil(text: str) -> tuple[str, str, str, str, str]:
    """Nom, prénom, date naissance, lieu, nationalité (section État civil)."""
    section = text
    m = re.search(r"Etat civil(.*)", text, re.I | re.S)
    if m:
        section = m.group(1)[:3500]

    last_name = ""
    first_name = ""
    m_nom = re.search(r"Nom\s*:\s*\n\s*([^\n]+)", section, re.I)
    if m_nom:
        last_name = m_nom.group(1).strip()
    m_pre = re.search(r"Prénom\s*:\s*\n\s*([^\n]+)", section, re.I)
    if m_pre:
        first_name = m_pre.group(1).strip()

    birth_date = ""
    m_ne = re.search(r"Né\(e\)\s+le\s*:\s*\n?\s*(\d{2}/\d{2}/\d{4})", section, re.I)
    if m_ne:
        birth_date = normalize_birth_date_iso(m_ne.group(1))

    birth_place = ""
    m_a = re.search(r"\bà\s*:\s*\n\s*([^\n]+)", section, re.I)
    if m_a:
        birth_place = m_a.group(1).strip()

    nationality = ""
    m_nat = re.search(r"Nationalité\s*:\s*\n\s*([^\n]+)", section, re.I)
    if m_nat:
        nationality = m_nat.group(1).strip()

    return last_name, first_name, birth_date, birth_place, nationality


def _parse_ipparis_origin(text: str) -> tuple[str, str]:
    lines = _lines(text)
    institution = _find_value(lines, "Name of the institution")
    if institution:
        country = _find_value(lines, "Country", stop_labels=("Name of the institution",))
        return institution, country

    m = re.search(
        r"Current institution\s+Country\s+(.+?)\s+Name of institution\s+(.+?)\s+"
        r"Degree\s*/\s*Qualification pursued",
        text,
        re.I | re.S,
    )
    if m:
        return m.group(2).strip(), m.group(1).strip()

    inst = _find_value(lines, "Name of institution")
    if inst:
        idx = next((i for i, ln in enumerate(lines) if inst in ln), -1)
        country = ""
        if idx > 0:
            country = _value_on_line(lines[idx - 1], "Country")
        return inst, country
    return "", ""


def _parse_ipparis(text: str, path: Path) -> AdmissionDossier:
    lines = _lines(text)
    identity = _ipparis_identity_lines(lines)

    dossier = AdmissionDossier(
        source="IPParis",
        source_file=str(path),
        application_platform="IPParis",
        enrollment_institution="Institut Polytechnique de Paris",
        track=_track_from_path(path) or "P",
    )

    m = _IPPARIS_DOSSIER_RE.search(text)
    if m:
        dossier.candidature_ref = m.group(1)
        dossier.student_number_local = m.group(1)

    dossier.last_name = (
        _find_value(identity, "Family Name of the applicant")
        or _find_value(identity, "Family name")
        or _find_value(identity, "Family Name")
    )
    dossier.first_name = (
        _find_value(identity, "First Name of the applicant")
        or _find_value(identity, "First name")
        or _find_value(identity, "First Name")
    )
    dossier.gender = normalize_gender(
        _find_value(identity, "Gender")
        or _find_value(identity, "Sex")
    )
    dossier.birth_date = normalize_birth_date_iso(
        _find_value(identity, "Date of birth")
    )
    dossier.birth_place = (
        _find_value(identity, "City of birth")
        or _find_value(identity, "City of Birth")
    )
    dossier.nationality = (
        _find_value(identity, "Nationality 1")
        or _find_value(identity, "Nationality")
    )
    email = _find_value(identity, "Email")
    if email.lower().endswith("@ip-paris.fr") or email.lower().endswith("@etu.u-paris.fr"):
        dossier.email_institutional = email
    else:
        dossier.email_personal = email

    dossier.origin_institution, dossier.origin_institution_country = _parse_ipparis_origin(text)
    dossier.highest_diploma = _parse_ipparis_highest_diploma(text)

    return dossier


def _parse_upsay(text: str, path: Path) -> AdmissionDossier:
    dossier = AdmissionDossier(
        source="UPSay",
        source_file=str(path),
        application_platform="UPSay",
        enrollment_institution="Université Paris-Saclay",
        track=_track_from_path(path) or "P",
    )

    ln, fn, birth_date, birth_place, nationality = _parse_upsay_etat_civil(text)
    dossier.last_name = ln
    dossier.first_name = fn
    dossier.birth_date = birth_date
    dossier.birth_place = birth_place
    dossier.nationality = nationality

    mail = re.search(r"Courriel\s*:\s*\n?\s*(\S+@\S+)", text, re.I)
    if mail:
        dossier.email_personal = mail.group(1).strip()

    ine_block = re.search(r"Numéro\s+INE\s*\n\s*([0-9]{9,11}[A-Z]{2})", text, re.I)
    if ine_block:
        dossier.student_number_ine = ine_block.group(1).upper()
    else:
        dossier.student_number_ine = _scan_ine(text)

    etab = re.search(r"Cursus actuel.*?Etablissement\s*:\s*(.+?)\s+Ville", text, re.I | re.S)
    if etab:
        inst = re.sub(r"\s+", " ", etab.group(1)).strip()
        if inst:
            dossier.origin_institution = inst
    if not dossier.origin_institution:
        etab_prev = re.search(
            r"Cursus précédent.*?Etablissement\s*:\s*(.+?)\s+Ville", text, re.I | re.S
        )
        if etab_prev:
            dossier.origin_institution = re.sub(r"\s+", " ", etab_prev.group(1)).strip()

    pays = re.search(r"Cursus actuel.*?Pays\s*:\s*(.+)", text, re.I | re.S)
    if pays:
        dossier.origin_institution_country = pays.group(1).strip().split("\t")[0].strip()
    if not dossier.origin_institution_country:
        pays_prev = re.search(r"Cursus précédent.*?Pays\s*:\s*(.+)", text, re.I | re.S)
        if pays_prev:
            dossier.origin_institution_country = pays_prev.group(1).strip().split("\t")[0].strip()

    dossier.highest_diploma = _parse_upsay_highest_diploma(text)

    upsay_ref, _ = _refs_from_filename(path)
    if upsay_ref:
        dossier.candidature_ref = upsay_ref

    return dossier


def _parse_monmaster(text: str, path: Path) -> AdmissionDossier:
    channel, enrollment = _parse_monmaster_channel(text, path)

    dossier = AdmissionDossier(
        source="MonMaster",
        source_file=str(path),
        application_platform="MonMaster",
        monmaster_channel=channel,
        enrollment_institution=enrollment,
        track=_parse_monmaster_track(text, path),
    )

    m = _MONMASTER_CAND_RE.search(text[:3000])
    if m:
        dossier.candidature_ref = m.group(1)

    dossier.last_name = _find_value(_lines(text), "Nom de naissance")
    dossier.first_name = _find_value(_lines(text), "Prénom")
    civ = _find_value(_lines(text), "Civilité")
    if civ.lower().startswith("mme"):
        dossier.gender = "F"
    elif civ.lower().startswith("m."):
        dossier.gender = "M"

    dossier.birth_date = normalize_birth_date_iso(_find_value(_lines(text), "Date de naissance"))
    dossier.birth_place = _find_value(_lines(text), "Ville / Commune de naissance")
    dossier.nationality = _find_value(_lines(text), "Nationalité")

    ine_master = re.search(r"INE maître \(INES\)\s+([0-9]{9,11}[A-Z]{2})", text, re.I)
    ine_cand = re.search(r"INE/INA/BEA saisi par le candidat\s+([0-9]{9,11}[A-Z]{2})", text, re.I)
    if ine_master:
        dossier.student_number_ine = ine_master.group(1).upper()
    elif ine_cand:
        dossier.student_number_ine = ine_cand.group(1).upper()
    else:
        dossier.student_number_ine = _scan_ine(text)

    dossier.email_personal = _parse_monmaster_email(text)

    dossier.student_number_local = _scan_apogee(text)
    if not dossier.student_number_local and dossier.candidature_ref:
        dossier.student_number_local = dossier.candidature_ref

    dossier.highest_diploma = _parse_monmaster_highest_diploma(text)

    if channel:
        dossier.notes = f"Mon Master (canal {channel})"
    else:
        dossier.warnings.append("Canal Mon Master non identifié (UPSay / ChimieParis / IPParis).")

    if not dossier.track:
        dossier.warnings.append("Parcours M1 P/C non détecté.")

    return dossier


def parse_admission_pdf(path: str | Path, *, full_text_for_ine: bool = True) -> AdmissionDossier:
    pdf_path = Path(path)
    if not pdf_path.is_file():
        return AdmissionDossier(
            source="",
            source_file=str(pdf_path),
            parse_error="Fichier introuvable.",
        )
    if pdf_path.suffix.lower() != ".pdf":
        return AdmissionDossier(
            source="",
            source_file=str(pdf_path),
            parse_error="Extension non PDF.",
        )
    extracted_photo = extract_candidate_photo_from_pdf(pdf_path)

    try:
        detect_text = _pdf_text(pdf_path, max_pages=2)
        preview = _pdf_text(pdf_path, max_pages=12)
        full = _pdf_text(pdf_path) if full_text_for_ine else preview
    except Exception as exc:
        return AdmissionDossier(
            source="",
            source_file=str(pdf_path),
            parse_error=str(exc),
        )

    source = _detect_source(detect_text)
    if not source:
        return AdmissionDossier(
            source="",
            source_file=str(pdf_path),
            parse_error="Format non reconnu (attendu : IPParis, UPSay ou Mon Master).",
        )

    if source == "IPParis":
        dossier = _parse_ipparis(preview, pdf_path)
    elif source == "UPSay":
        dossier = _parse_upsay(preview, pdf_path)
    else:
        dossier = _parse_monmaster(preview, pdf_path)

    dossier.student_number_ine = _scan_ine(full) or dossier.student_number_ine
    apogee = _scan_apogee(full)
    if apogee:
        dossier.student_number_local = apogee

    dossier.track = normalize_track_acronym(dossier.track)
    dossier.academic_year = (
        _session_academic_year(full)
        or _session_academic_year(preview)
        or dossier.academic_year
    )
    _finalize_admission_identity(dossier, full, pdf_path)

    if not dossier.student_number_local:
        upsay_ref, cand_ref = _refs_from_filename(pdf_path)
        if dossier.candidature_ref and _MONMASTER_CAND_REF_RE.fullmatch(dossier.candidature_ref):
            dossier.student_number_local = dossier.candidature_ref
        elif upsay_ref:
            dossier.student_number_local = upsay_ref
        elif cand_ref:
            dossier.student_number_local = cand_ref
        elif source == "UPSay":
            dossier.student_number_local = pdf_path.stem[:40]
            dossier.warnings.append(
                "N° d'inscription établissement absent : identifiant provisoire (nom de fichier)."
            )
        else:
            dossier.warnings.append("N° d'inscription établissement absent.")
    elif (
        dossier.student_number_local
        and not apogee
        and dossier.student_number_local == pdf_path.stem[:40]
    ):
        dossier.warnings.append(
            "N° d'inscription établissement absent : identifiant provisoire (nom de fichier)."
        )

    if not dossier.student_number_ine:
        dossier.warnings.append("INE non trouvé dans le PDF.")

    if dossier.enrollment_institution:
        dossier.email_institutional = adapt_institutional_email(
            dossier.first_name,
            dossier.last_name,
            dossier.enrollment_institution,
            dossier.email_institutional,
        )

    if not dossier.last_name or not dossier.first_name:
        dossier.parse_error = "Nom ou prénom non extrait du PDF."

    if extracted_photo is not None:
        dossier.photo_found = True
        dossier.extracted_photo = extracted_photo
    else:
        dossier.warnings.append("Photo d'identité non détectée dans le PDF.")

    return dossier


def build_existing_student_indexes(
    students: list[dict],
) -> tuple[dict[str, dict], dict[tuple[str, str], dict], dict[str, dict]]:
    """Index INE, (nom, prénom) et email pour repérer une fiche existante."""
    by_ine: dict[str, dict] = {}
    by_name: dict[tuple[str, str], dict] = {}
    by_email: dict[str, dict] = {}
    for row in students:
        data = dict(row)
        ine = str(data.get("student_number_ine") or "").strip().upper()
        if ine:
            by_ine[ine] = data
        ln = str(data.get("last_name") or "").strip().upper()
        fn = str(data.get("first_name") or "").strip().upper()
        if ln and fn:
            by_name[(ln, fn)] = data
        for key in ("email_personal", "email_institutional"):
            em = normalize_email(str(data.get(key) or "")).lower()
            if em:
                by_email[em] = data
    return by_ine, by_name, by_email


def find_existing_student(
    dossier: AdmissionDossier,
    *,
    by_ine: dict[str, dict],
    by_name: dict[tuple[str, str], dict],
    by_email: dict[str, dict],
) -> tuple[dict | None, str]:
    """Associe un dossier à une fiche étudiant déjà en base."""
    ine = dossier.student_number_ine.strip().upper()
    if ine and ine in by_ine:
        return by_ine[ine], f"INE {ine}"

    name_key = (
        dossier.last_name.strip().upper(),
        dossier.first_name.strip().upper(),
    )
    if name_key[0] and name_key[1] and name_key in by_name:
        return by_name[name_key], "nom et prénom"

    for em in (dossier.email_personal, dossier.email_institutional):
        key = normalize_email(em).lower()
        if key and key in by_email:
            return by_email[key], f"email {key}"

    return None, ""


def link_existing_students(
    dossiers: list[AdmissionDossier],
    students: list[dict],
) -> None:
    """Renseigne ``existing_student_id`` sur chaque dossier importable."""
    by_ine, by_name, by_email = build_existing_student_indexes(students)
    for dossier in dossiers:
        dossier.existing_student_id = None
        dossier.existing_match_reason = ""
        if not dossier.importable:
            continue
        match, reason = find_existing_student(
            dossier, by_ine=by_ine, by_name=by_name, by_email=by_email
        )
        if match:
            dossier.existing_student_id = int(match["id"])
            dossier.existing_match_reason = reason


def infer_track_from_admission_file(path: str | Path) -> tuple[str, str, str]:
    """Déduit (niveau, parcours, millésime) depuis un PDF ou chemin de candidature."""
    p = Path(path)
    ay = _academic_year_from_path(p)
    tr = normalize_track_acronym(_track_from_path(p))
    if tr:
        lv, tr = normalize_admission_level_track("M1", tr)
        return lv, tr, ay
    if p.suffix.lower() != ".pdf" or not p.is_file():
        return "", "", ay
    dossier = parse_admission_pdf(p, full_text_for_ine=False)
    if dossier.track:
        lv, tr = normalize_admission_level_track(dossier.level, dossier.track)
        return lv, tr, dossier.academic_year or ay
    return "", "", ay


def normalize_admission_level_track(level: str, track: str) -> tuple[str, str]:
    lv = str(level or "M1").strip().upper()
    tr = str(track or "").strip().upper()
    if lv in {"P", "C", "M1P", "M1C"} and not tr:
        tr = "P" if lv in {"P", "M1P"} else "C"
        lv = "M1"
    elif tr in {"P", "C", "M1P", "M1C"} and lv not in {"M1", "M2"}:
        lv = "M1"
        tr = "P" if tr in {"P", "M1P"} else "C"
    return lv, tr


def collect_admission_pdfs(paths: Iterable[str | Path]) -> list[Path]:
    """Développe une liste de fichiers / dossiers en PDF candidatures."""
    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for pdf in sorted(p.rglob("*.pdf")):
                key = str(pdf.resolve())
                if key not in seen:
                    seen.add(key)
                    out.append(pdf)
        elif p.is_file() and p.suffix.lower() == ".pdf":
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out
