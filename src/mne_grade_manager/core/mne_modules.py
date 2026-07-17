"""
Nomenclature des modules MNE (Master Nuclear Energy).

Source : fichier « Maquette MNE avec nouvelle nomenclature.xlsx ».

Format module / **Code UE** : ``{Year}{Block}-{Track}-{Course}``  (sans tiret entre année et bloc)
  - Ex. ``M1B1-C-THER``  —  M1, bloc 1, commun, Thermodynamique
  - Ex. ``M2B3-DOR-FLUI`` — M2, bloc 3, NPD+NPO+NRPE (piste « DOR » condensée)

Pistes M1 : P (Physique), X (Chimie), C (commun).
Pistes M2 : D NPD, F NFC, O NPO, R NRPE, W DWM — parfois regroupées (FWDO, DOR, DO, FW).

Les anciens codes par semestre (``S1-C-THER``, …) ne sont plus utilisés.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple


class MneModule(NamedTuple):
    code: str
    ects: float
    title: str


TRACK_LETTERS: dict[str, str] = {
    "P": "M1 Physics track",
    "X": "M1 Chemistry track",
    "C": "Common (all students in year/block)",
    "D": "M2 Nuclear Plant Design (NPD)",
    "F": "M2 Nuclear Fuel Cycle (NFC)",
    "O": "M2 Nuclear Plant Operation (NPO)",
    "R": "M2 Nuclear Reactor Physics & Engineering (NRPE)",
    "W": "M2 Decommissioning & Waste Management (DWM)",
}

# Catalogue officiel — « Maquette MNE avec nouvelle nomenclature.xlsx ».
MNE_MODULES_2026_2027: tuple[MneModule, ...] = (
    MneModule("M1B1-C-THER", 3, "Thermodynamics"),
    MneModule("M1B1-C-MME", 4, "Methode Mathématiques pour l'Ingénierie"),
    MneModule("M1B1-C-RADIOMAT", 3, "Interaction et Détection des rayonnements ionisant"),
    MneModule("M1B1-C-NUCL", 3, "Notion de Physique Nucléaire"),
    MneModule("M1B1-C-CHEM", 4, "Chemical engineering"),
    MneModule("M1B2-C-ENER", 3, "Energy Production Technologies"),
    MneModule("M1B2-C-ECO", 2, "Economics of energy"),
    MneModule("M1B2-C-PROJ", 3, "Project management"),
    MneModule("M1B2-C-REAC", 1, "Notion de Physique des réacteurs"),
    MneModule("M1B3-P-NEUT", 1, "Notion de Neutronique"),
    MneModule("M1B3-P-MATE", 4, "Sciences des matériaux et Macanique"),
    MneModule("M1B3-P-ELEC", 3, "Electrical Power Engineering"),
    MneModule("M1B3-P-QUANT", 3, "Notion de Mécanique quantique"),
    MneModule("M1B3-P-CONT", 3, "Controle des systémes dynamiques"),
    MneModule("M1B3-P-FLUI", 4, "Mécanique des fluide et transferts thermiques"),
    MneModule("M1B3-P-MECH", 1, "Mécanique des Milieux Continus"),
    MneModule("M1B3-P-RADIOMAT", 2, "Détection appliquée à la physique"),
    MneModule("M1B3-X-SOL", 3, "Solution chemistry 1 : speciation and process"),
    MneModule("M1B3-X-RAD", 2, "Radiolysis"),
    MneModule("M1B3-X-NUMMATE", 4, "Chemistry of nuclear materials"),
    MneModule("M1B3-X-CHEM", 3, "Solution chemistry 2 : Separation chemistry"),
    MneModule("M1B3-X-ANCRE", 4, "Analysis Methods Nuclear Field"),
    MneModule("M1B3-X-SPECT", 3, "Atomics & Molecular Spectroscopy"),
    MneModule("M1B3-X-CHEMNUCL", 2, "Chimie dans le cycle électronucléaire"),
    MneModule("M2B1-C-SAFE", 3, "Introduction à la sûreté. Criticité - Sécurité"),
    MneModule("M2B1-C-RP", 3, "Radioprotection"),
    MneModule("M2B2-C-TRANS", 2, "Energy Transition and Flexibility"),
    MneModule("M2B2-C-SYS", 3, "Nuclear Fuel Cycles. Nuclear Reactor Systems"),
    MneModule("M2B2-C-ENER", 3, "PWR Functionnal Description"),
    MneModule("M2B1-FWDO-RISK", 4, "Gestion des risques"),
    MneModule("M2B3-DOR-FLUI", 4, "Thermohydraulics"),
    MneModule("M2B3-DO-NEUT", 3, "Nuclear Physics and Neutronics"),
    MneModule("M2B3-D-CODE", 3, "Conception, calculs & contrôle partie"),
    MneModule("M2B3-D-SYST", 4, "Systems and equipment"),
    MneModule("M2B4-D-DESI", 2, "Conception"),
    MneModule("M2B4-D-NUMDESI", 3, "Conception numérique"),
    MneModule("M2B4-D-SEISM", 2, "De la sismologie à l'ingéniérie sismique"),
    MneModule("M2B4-D-CONCRE", 2, "Physique des matériaux: béton"),
    MneModule("M2B4-D-CORO", 1, "Physique des matériaux: corosion"),
    MneModule("M2B4-O-MAIN", 4, "Maintenance"),
    MneModule("M2B4-O-SAFE", 3, "Safety and production"),
    MneModule("M2B4-O-RPIL", 5, "Reactor Piloting"),
    MneModule("M2B4-O-CODE", 4, "Simulation, Modelling and Control for Nuclear Power Systems"),
    MneModule("M2B3-FW-NPN", 2, "Introduction to Nuclear Physics, Neutronics"),
    MneModule("M2B3-F-SPEC", 3, "Actinides electronic structure and spectroscopy"),
    MneModule("M2B3-F-CMS", 3, "Cooling & Molten Salt"),
    MneModule("M2B4-F-FUEL", 3, "Fuel: from Mine to the Reactor"),
    MneModule("M2B4-F-CODE", 3, "Process Simulation and Process Control"),
    MneModule("M2B4-F-SEPA", 4, "Separation and Recycling"),
    MneModule("M2B4-F-WAST", 3, "Waste Containment Materials"),
    MneModule("M2B4-F-DISPO", 3, "Waste Disposal"),
    MneModule("M2B3-W-DECO", 3, "Politics, Strategie, Management of Decommissioning"),
    MneModule("M2B3-W-WAST", 3, "Waste Management: pollitics, strategy and methodology"),
    MneModule("M2B3-W-MEAS", 3, "Measuremant methods and tecnhiques"),
    MneModule("M2B4-W-DIS", 3, "Dismantling: project case study"),
    MneModule("M2B4-W-DECO", 3, "Methods of Decommissioning"),
    MneModule("M2B4-W-WAST", 3, "Waste operational management"),
    MneModule("M2B4-W-RMDO", 3, "Risk Management of Dismantling"),
    MneModule("M2B3-R-NEUT", 4, "Neutronics 1: Fundamentals"),
    MneModule("M2B3-R-MAT", 4, "Nuclear Materials"),
    MneModule("M2B3-R-NUCL", 4, "Nuclear Physics"),
    MneModule("M2B4-R-FLUI", 4, "Advanced Thermal-hydraulics"),
    MneModule("M2B4-R-MPHYS", 2, "Multiphysics and Uncertainties"),
    MneModule("M2B4-R-NEUT", 4, "Advanced Neutronics"),
    MneModule("M2B4-R-RPS", 2, "Reactor Physics and Simulation"),
)

# Anciennes formes (guide / maquettes) → nomenclature condensée du fichier Excel.
_CODE_ALIASES: dict[str, str] = {
    "M2B1-F-W-D-O-RISK": "M2B1-FWDO-RISK",
    "M2B3-D-O-R-FLUI": "M2B3-DOR-FLUI",
    "M2B3-D-O-NEUT": "M2B3-DO-NEUT",
    "M2B3-F-W-NPN": "M2B3-FW-NPN",
    "M2B4-CONCRE": "M2B4-D-CONCRE",
    "M1B3-P-ELEC5": "M1B3-P-ELEC",
}

_LEGACY_SEMESTER_UE_RE = re.compile(r"^S[1-4]-")

# Pistes M2 regroupées dans la nomenclature condensée.
_CONDENSED_TRACK_SEGMENTS: dict[str, tuple[str, ...]] = {
    "FWDO": ("F", "W", "D", "O"),
    "DOR": ("D", "O", "R"),
    "DO": ("D", "O"),
    "FW": ("F", "W"),
}

_BY_CODE: dict[str, MneModule] = {m.code: m for m in MNE_MODULES_2026_2027}


def _normalize_maquette_title(value: str) -> str:
    s = unicodedata.normalize("NFKD", value or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s.lower())
    return " ".join(s.split())


def _ascii_upper(value: str) -> str:
    s = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).upper()


M2_TRACK_TO_LETTER: dict[str, str] = {
    "NPD": "D",
    "NFC": "F",
    "NPO": "O",
    "NRPE": "R",
    "DWM": "W",
}

# Intitulés maquette UPSay (FR/EN) → code MNE officiel.
_MAQUETTE_TITLE_ALIASES: dict[str, str] = {
    "thermodynamique": "M1B1-C-THER",
    "thermodynamics": "M1B1-C-THER",
    "methodes mathematiques pour l ingenierie": "M1B1-C-MME",
    "mathematics": "M1B1-C-MME",
    "mathematical methods for engineering": "M1B1-C-MME",
    "interaction et detection des rayonnements ionisants": "M1B1-C-RADIOMAT",
    "interaction of radiation with matter": "M1B1-C-RADIOMAT",
    "notions de physique nucleaire": "M1B1-C-NUCL",
    "basic nuclear physics": "M1B1-C-NUCL",
    "technologies de production d energie": "M1B2-C-ENER",
    "energy production technologies": "M1B2-C-ENER",
    "gestion de projet": "M1B2-C-PROJ",
    "project management": "M1B2-C-PROJ",
    "economie de l energie": "M1B2-C-ECO",
    "economics of energy": "M1B2-C-ECO",
    "chemical engineering": "M1B1-C-CHEM",
    "notion de physique des reacteurs": "M1B2-C-REAC",
    "basic reactor operation": "M1B2-C-REAC",
    "notion de neutronique": "M1B3-P-NEUT",
    "basic neutronics": "M1B3-P-NEUT",
    "material science and mechanics": "M1B3-P-MATE",
    "ingenierie electrique": "M1B3-P-ELEC",
    "electrical power engineering": "M1B3-P-ELEC",
    "notions de mecanique quantique": "M1B3-P-QUANT",
    "basic quantum mechanics": "M1B3-P-QUANT",
    "mecanique des fluides et transferts thermiques": "M1B3-P-FLUI",
    "fluid mechanics and heat transfer": "M1B3-P-FLUI",
    "detection appliquee a la physique": "M1B3-P-RADIOMAT",
    "radiation detection and measurement": "M1B3-P-RADIOMAT",
    "controle des systemes dynamiques": "M1B3-P-CONT",
    "control of dynamical systems": "M1B3-P-CONT",
    "mecanique des milieux continus": "M1B3-P-MECH",
    "continuum mechanics": "M1B3-P-MECH",
    "solution chemistry 1 speciation and process": "M1B3-X-SOL",
    "radiolysis": "M1B3-X-RAD",
    "chemistry of nuclear materials": "M1B3-X-NUMMATE",
    "solution chemistry 2 separation chemistry": "M1B3-X-CHEM",
    "chimie analytique des elements radioactifs": "M1B3-X-ANCRE",
    "analytical chemistry of radioactive elements": "M1B3-X-ANCRE",
    "spectroscopie atomique et moleculaire": "M1B3-X-SPECT",
    "atomic and molecular spectroscopy": "M1B3-X-SPECT",
    "chimie dans le cycle electro nucleaire": "M1B3-X-CHEMNUCL",
    "pwr functional description": "M2B2-C-ENER",
    "systems and equipments": "M2B3-D-SYST",
    "simulation modelling and control for nuclear power plants": "M2B4-O-CODE",
    "simulation modelling and control for nuclear power systems": "M2B4-O-CODE",
    "measurements methods and techniques": "M2B3-W-MEAS",
    "actinides electronic structure and spectroscopy": "M2B3-F-SPEC",
    "fuel from mine to reactor": "M2B4-F-FUEL",
    "advanced neutronics": "M2B4-R-NEUT",
    "introduction to neutronics": "M2B3-R-NEUT",
    "introduction to nuclear physics neutronics bases": "M2B3-FW-NPN",
    "nuclear physics and neutronics": "M2B3-DO-NEUT",
    "gestion des risques": "M2B1-FWDO-RISK",
    "risk management": "M2B1-FWDO-RISK",
    "thermohydraulics": "M2B3-DOR-FLUI",
    "nuclear materials under irradiation": "M2B3-R-MAT",
    "process simulation and process control": "M2B4-F-CODE",
}

_BY_NORM_TITLE: dict[str, str] = {
    _normalize_maquette_title(m.title): m.code for m in MNE_MODULES_2026_2027
}
for _alias, _code in _MAQUETTE_TITLE_ALIASES.items():
    _BY_NORM_TITLE.setdefault(_alias, _code)


def infer_maquette_block_number(block_name: str, level: str = "") -> int | None:
    """Déduit B1…B4 (ou B5 stage M2) depuis le libellé de bloc maquette."""
    raw = str(block_name or "").strip()
    if re.fullmatch(r"[1-5]", raw):
        return int(raw)
    b = _ascii_upper(block_name)
    m = re.search(r"BL(?:OC|OCK)\s*(\d)", b)
    if m:
        return int(m.group(1))
    if "TRONC COMMUN" in b and re.search(r"BL(?:OC|OCK)\s*1", b):
        return 1
    if "OUVERTURE" in b or ("TRONC COMMUN" in b and re.search(r"BL(?:OC|OCK)\s*2", b)):
        return 2
    if "SPECIALITE" in b or re.search(r"BL(?:OC|OCK)\s*3", b):
        return 3
    if "STAGE" in b or "INTERNSHIP" in b:
        return 4 if (level or "").upper() == "M1" else 5
    if (level or "").upper() == "M2":
        if "COMMON" in b and re.search(r"BL(?:OC|OCK)\s*1", b):
            return 1
        if re.search(r"BL(?:OC|OCK)\s*2", b):
            return 2
        if re.search(r"BL(?:OC|OCK)\s*3", b):
            return 3
        if re.search(r"BL(?:OC|OCK)\s*4", b):
            return 4
    return None


def _expand_track_segment(segment: str) -> tuple[str, ...]:
    s = (segment or "").strip().upper()
    if not s:
        return ()
    if s in _CONDENSED_TRACK_SEGMENTS:
        return _CONDENSED_TRACK_SEGMENTS[s]
    if "-" in s:
        out: list[str] = []
        for part in s.split("-"):
            out.extend(_expand_track_segment(part))
        return tuple(out)
    if len(s) == 1:
        return (s,)
    if all(ch in "DFORWXPPC" for ch in s):
        return tuple(s)
    return (s,)


def _track_letters_in_mne_code(code: str) -> tuple[str, ...]:
    m = re.match(r"^M[12]B[1-4]-([A-Z0-9-]+)-[A-Z0-9]+$", normalize_mne_module_code(code))
    if not m:
        return ()
    return _expand_track_segment(m.group(1))


def mne_code_applies_to_track(code: str, level: str, track: str) -> bool:
    """Vérifie qu’un code MNE correspond au parcours (P/X/C ou D/F/O/R/W)."""
    c = normalize_mne_module_code(code)
    letters = _track_letters_in_mne_code(c)
    if not letters:
        return False
    lv = (level or "").strip().upper()
    tr = (track or "").strip().upper()
    if lv == "M1":
        if letters == ("C",):
            return True
        if tr == "P":
            return "P" in letters
        if tr == "C":
            return "X" in letters
        return False
    if lv == "M2":
        if letters == ("C",):
            return True
        letter = M2_TRACK_TO_LETTER.get(tr, "")
        return bool(letter and letter in letters)
    return False


def _tracks_mentioned_in_title(name: str) -> set[str]:
    blob = _ascii_upper(name)
    found: set[str] = set()
    for tok, track in (
        ("NDWM", "DWM"),
        ("NRPE", "NRPE"),
        ("NPD", "NPD"),
        ("NFC", "NFC"),
        ("NPO", "NPO"),
    ):
        if re.search(rf"(^|[^A-Z]){tok}([^A-Z]|$)", blob):
            found.add(track)
    return found


def match_mne_module_code(
    name: str,
    *,
    block_name: str = "",
    level: str = "",
    track: str = "",
) -> str:
    """
    Associe un intitulé maquette au code MNE officiel (M1B1-C-*, M2B3-R-*, …).
    Retourne une chaîne vide si aucune correspondance fiable.
    """
    lv = (level or "").strip().upper()
    tr = (track or "").strip().upper()
    if not lv or not tr:
        return ""

    base = re.sub(r"\s*\([^)]*\)\s*", " ", name or "").strip()
    norm = _normalize_maquette_title(base)
    blk = infer_maquette_block_number(block_name, lv)
    title_tracks = _tracks_mentioned_in_title(name)

    candidates: list[str] = []
    if norm in _BY_NORM_TITLE:
        candidates.append(_BY_NORM_TITLE[norm])
    if not candidates:
        for alias, code in _BY_NORM_TITLE.items():
            if alias in norm or norm in alias:
                candidates.append(code)
    if not candidates:
        for mod in MNE_MODULES_2026_2027:
            mt = _normalize_maquette_title(mod.title)
            if mt in norm or norm in mt:
                candidates.append(mod.code)

    # Dédupliquer en conservant l’ordre.
    seen: set[str] = set()
    uniq: list[str] = []
    for code in candidates:
        c = normalize_mne_module_code(code)
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    candidates = uniq

    def _score(code: str) -> int:
        if not code.startswith(lv):
            return -100
        if not mne_code_applies_to_track(code, lv, tr):
            return -50
        if title_tracks and tr not in title_tracks:
            return -40
        s = 0
        if blk is not None:
            m = re.match(r"^M[12]B(\d)", code)
            if m and int(m.group(1)) == blk:
                s += 20
            elif m and int(m.group(1)) != blk:
                s -= 10
        mod = lookup_mne_module(code)
        if mod and abs(mod.ects) > 0:
            s += 2
        if _normalize_maquette_title(lookup_mne_module(code).title if mod else "") == norm:
            s += 30
        return s

    ranked = sorted(candidates, key=_score, reverse=True)
    if ranked and _score(ranked[0]) >= 0:
        return ranked[0]
    return ""


def normalize_mne_module_code(raw: str) -> str:
    """Normalise un code saisi (majuscules, sans espaces, alias connus)."""
    s = re.sub(r"\s+", "", (raw or "").strip().upper())
    return _CODE_ALIASES.get(s, s)


def lookup_mne_module(code: str) -> MneModule | None:
    return _BY_CODE.get(normalize_mne_module_code(code))


def is_legacy_semester_ue_code(code: str) -> bool:
    """Ancien format par semestre (S1-C-THER, S3-D-O-R-FLUI, …) — obsolète."""
    s = re.sub(r"\s+", "", (code or "").strip().upper())
    return bool(_LEGACY_SEMESTER_UE_RE.match(s))


def validate_mne_module_code(code: str) -> bool:
    c = normalize_mne_module_code(code)
    if c in _BY_CODE:
        return True
    return bool(re.match(r"^M[12]B[1-4]-[A-Z0-9-]+-[A-Z0-9]+$", c))


def course_ue_code(course: dict) -> str:
    """Code UE officiel (nomenclature MNE) pour une fiche cours."""
    for key in ("mne_module_code", "code"):
        c = normalize_mne_module_code(str(course.get(key) or ""))
        if c and validate_mne_module_code(c):
            return c
    return normalize_mne_module_code(str(course.get("mne_module_code") or ""))


def mne_module_choices() -> list[tuple[str, str]]:
    """Couples (code, libellé affichage) pour les listes déroulantes."""
    return [(m.code, f"{m.code} — {m.title} ({m.ects:g} ECTS)") for m in MNE_MODULES_2026_2027]
