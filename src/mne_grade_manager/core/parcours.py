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
        "P": "Physics",
        "C": "Chemistry and Chemical Engineering",
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
