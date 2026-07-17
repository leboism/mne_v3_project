"""Parcours officiels par niveau (maquettes UPSay MNE)."""

from __future__ import annotations

# Niveaux pour lesquels la liste de parcours est figée dans l’UI.
STANDARD_LEVELS: tuple[str, ...] = ("M1", "M2")

# code stocké en base (templates.track, students.track) → libellé dans les combos
PARCOURS_BY_LEVEL: dict[str, tuple[tuple[str, str], ...]] = {
    "M1": (
        ("P", "P"),
        ("C", "C"),
    ),
    "M2": (
        ("NPD", "NPD"),
        ("NPO", "NPO"),
        ("DWM", "DWM"),
        ("NFC", "NFC"),
        ("NRPE", "NRPE"),
    ),
}

OTHER_LEVEL_DATA = "__OTHER__"
OTHER_TRACK_DATA = "__OTHER__"

# Libellés longs pour documents jury (PDF)
TRACK_PROGRAM_LABEL: dict[str, dict[str, str]] = {
    "M1": {
        "P": "Physics and Engineering",
        "C": "Chemistry and Engineering",
    },
    "M2": {
        "NPD": "Nuclear Plant Design",
        "NPO": "Nuclear Plant Operation",
        "DWM": "Decommissioning & Waste Management",
        "NFC": "Nuclear Fuel Cycle",
        "NRPE": "Nuclear Reactor Physics & Engineering",
    },
}


def track_program_label(level: str, track: str) -> str:
    """Intitulé parcours pour en-têtes PDF jury."""
    lv = (level or "").strip().upper()
    tr = (track or "").strip().upper()
    return TRACK_PROGRAM_LABEL.get(lv, {}).get(tr, track_label(lv, tr))


MNE_INSTITUTIONS_LINE = "Universite Paris-Saclay / IPParis / PSL"


def master_degree_email_title(level: str, track: str) -> str:
    """Intitulé complet pour e-mails jury / transcript (ex. M1 Chemistry and Engineering)."""
    lv = (level or "").strip().upper()
    tr = (track or "").strip().upper()
    specialty = track_program_label(lv, tr)
    if lv and specialty:
        return f"Master of Nuclear Energy ({lv} {specialty})"
    if specialty:
        return f"Master of Nuclear Energy ({specialty})"
    return "Master of Nuclear Energy"


def mne_level_master_line(level: str) -> str:
    """Ligne d'entête / signature : ex. « M1 Master Nuclear Energy »."""
    lv = (level or "").strip().upper()
    if lv:
        return f"{lv} Master Nuclear Energy"
    return "Master Nuclear Energy"


def mne_email_signature(level: str) -> str:
    """Signature des e-mails jury → étudiants."""
    return f"{mne_level_master_line(level)}\n{MNE_INSTITUTIONS_LINE}"


def track_display_label(level: str, track: str) -> str:
    """Libellé UI liste / fiches : intitulé long + acronyme si le nom diffère du code."""
    tr = (track or "").strip()
    if not tr:
        return ""
    long_name = track_program_label(level, tr)
    if long_name and long_name.strip().upper() != tr.strip().upper():
        return f"{long_name} ({tr})"
    return tr


def parcours_choices(level: str) -> tuple[tuple[str, str], ...]:
    """Couples (code, libellé) pour un niveau standard ; tuple vide sinon."""
    return PARCOURS_BY_LEVEL.get(level.strip().upper(), ())


def track_label(level: str, code: str) -> str:
    """Libellé affichage pour un code ; retourne le code si inconnu."""
    if not code:
        return ""
    up = level.strip().upper()
    for c, lab in parcours_choices(up):
        if c == code:
            return lab
    return code


def suggested_maquette_name(academic_year: str, level: str, track: str) -> str:
    y = academic_year.strip()
    lv = level.strip()
    tr = track.strip()
    if y and lv and tr:
        return f"{y} — {lv} {tr}"
    if lv and tr:
        return f"{lv} {tr}"
    return ""
