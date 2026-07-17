"""Millésimes universitaires (liste d'accueil + années personnalisées)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from ..core.database import CUSTOM_YEARS_FILE

APP_DIR = CUSTOM_YEARS_FILE.parent
HIDDEN_YEARS_FILE = APP_DIR / "hidden_years.json"

# Premier millésime géré dans l'application (liste d'accueil et années proposées).
FIRST_ACADEMIC_YEAR_START = 2025  # → 2025-2026


def normalize_academic_year(value: str) -> str | None:
    """Retourne le libellé normalisé (ex. 2027-2028) si valide, sinon None."""
    raw = str(value or "").strip()
    if not raw:
        return None
    m = re.match(r"^(\d{4})\s*[-–]\s*(\d{4})$", raw)
    if not m:
        return None
    y1, y2 = int(m.group(1)), int(m.group(2))
    if y2 == y1 + 1 and 1990 <= y1 <= 2100:
        return f"{y1}-{y2}"
    return None


# À partir de ce millésime, le code UE officiel est la nomenclature MNE (M1B1-…).
# Les millésimes antérieurs (ex. 2025-2026) utilisent les codes secrétariat (S1-C/P/X).
MNE_COURSE_CODE_FROM_MILLÉSIME = "2026-2027"


def millésime_uses_secretariat_course_codes(academic_year: str) -> bool:
    ay = normalize_academic_year(academic_year) or str(academic_year or "").strip()
    if not ay:
        return False
    return ay < MNE_COURSE_CODE_FROM_MILLÉSIME


def _year_is_allowed(normalized: str) -> bool:
    try:
        y1 = int(str(normalized).split("-", 1)[0])
    except (TypeError, ValueError):
        return False
    return y1 >= FIRST_ACADEMIC_YEAR_START


def generated_academic_years(*, around: date | None = None) -> list[str]:
    """Fenêtre glissante d'années proposées à l'accueil (-2 à +1 par rapport à l'année courante)."""
    today = around or date.today()
    start_year = today.year if today.month >= 9 else today.year - 1
    years = [f"{start_year - i}-{start_year - i + 1}" for i in range(2, -2, -1)]
    return [y for y in years if _year_is_allowed(y)]


def current_academic_year_label(*, around: date | None = None) -> str:
    today = around or date.today()
    start_year = today.year if today.month >= 9 else today.year - 1
    return f"{start_year}-{start_year + 1}"


def load_custom_academic_years() -> list[str]:
    if not CUSTOM_YEARS_FILE.is_file():
        return []
    try:
        data = json.loads(CUSTOM_YEARS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_custom_academic_years(years: list[str]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_YEARS_FILE.write_text(
        json.dumps(years, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_hidden_academic_years() -> list[str]:
    if not HIDDEN_YEARS_FILE.is_file():
        return []
    try:
        data = json.loads(HIDDEN_YEARS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_hidden_academic_years(years: list[str]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    HIDDEN_YEARS_FILE.write_text(
        json.dumps(years, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def hide_academic_year(year: str) -> bool:
    """Masque un millésime de l'écran d'accueil (même les années proposées par défaut)."""
    normalized = normalize_academic_year(year)
    if not normalized:
        return False
    hidden = load_hidden_academic_years()
    if normalized in hidden:
        return False
    hidden.append(normalized)
    hidden.sort()
    save_hidden_academic_years(hidden)
    return True


def unhide_academic_year(year: str) -> bool:
    normalized = normalize_academic_year(year)
    if not normalized:
        return False
    hidden = load_hidden_academic_years()
    if normalized not in hidden:
        return False
    save_hidden_academic_years([y for y in hidden if y != normalized])
    return True


def ensure_welcome_year_floor() -> None:
    """Masque les millésimes antérieurs à FIRST_ACADEMIC_YEAR_START (ex. 2023-2024, 2024-2025)."""
    hidden = load_hidden_academic_years()
    changed = False
    for y1 in range(1990, FIRST_ACADEMIC_YEAR_START):
        label = f"{y1}-{y1 + 1}"
        if label not in hidden:
            hidden.append(label)
            changed = True
    if changed:
        hidden.sort()
        save_hidden_academic_years(hidden)

    custom = load_custom_academic_years()
    pruned = [y for y in custom if _year_is_allowed(normalize_academic_year(y) or "")]
    if pruned != custom:
        save_custom_academic_years(pruned)


def list_academic_year_choices(*, around: date | None = None) -> list[str]:
    hidden = {normalize_academic_year(y) or "" for y in load_hidden_academic_years()}
    hidden.discard("")
    years = generated_academic_years(around=around)
    seen = set(years)
    out = [y for y in years if y not in hidden]
    for y in load_custom_academic_years():
        norm = normalize_academic_year(y)
        if norm and _year_is_allowed(norm) and norm not in seen and norm not in hidden:
            seen.add(norm)
            out.append(norm)
    return out


def remove_custom_academic_year(year: str) -> bool:
    """Retire un millésime de la liste personnalisée. Retourne True si retiré."""
    normalized = normalize_academic_year(year)
    if not normalized:
        return False
    custom = load_custom_academic_years()
    if normalized not in custom:
        return False
    save_custom_academic_years([y for y in custom if y != normalized])
    return True


def ensure_custom_academic_year(year: str) -> str:
    """
    Enregistre le millésime dans custom_years.json s'il n'est pas déjà connu
    (fenêtre d'accueil ou liste personnalisée). Retourne le libellé normalisé.
    """
    normalized = normalize_academic_year(year)
    if not normalized:
        raise ValueError(
            f"Année universitaire invalide : {year!r} (format attendu : AAAA-AAAA)."
        )
    if not _year_is_allowed(normalized):
        raise ValueError(
            f"Le millésime {normalized} est antérieur au premier géré "
            f"({FIRST_ACADEMIC_YEAR_START}-{FIRST_ACADEMIC_YEAR_START + 1})."
        )
    if normalized in generated_academic_years() or normalized in load_custom_academic_years():
        unhide_academic_year(normalized)
        return normalized
    custom = load_custom_academic_years()
    if normalized not in custom:
        custom.append(normalized)
        custom.sort()
        save_custom_academic_years(custom)
    unhide_academic_year(normalized)
    return normalized
