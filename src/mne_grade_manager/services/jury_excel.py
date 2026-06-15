"""Import / modèle Excel pour les compositions du jury."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

JURY_MEMBER_HEADERS: tuple[str, ...] = (
    "last_name",
    "first_name",
    "title",
    "institution",
)

JURY_EXCEL_LABELS_FR: dict[str, str] = {
    "last_name": "Nom (obligatoire)",
    "first_name": "Prénom (obligatoire)",
    "title": "Qualité / titre (ex. Professeur, représentant professionnel)",
    "institution": "Institution",
}

JURY_EXCEL_EXAMPLE_ROWS: tuple[tuple[str, ...], ...] = (
    ("Martin", "Sophie", "Professeure", "Université Paris-Saclay"),
    ("Durand", "Paul", "Représentant professionnel", "CEA"),
    ("Lefebvre", "Anne", "Étudiante", "Master MNE"),
)


def norm_header(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _idx_of(headers: list[str], *names: str) -> int | None:
    for n in names:
        n2 = norm_header(n)
        if n2 in headers:
            return headers.index(n2)
    return None


def parse_jury_members_workbook(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    """
    Lit un fichier Excel et retourne (membres, erreurs).
    Chaque membre : last_name, first_name, title, institution.
    """
    from openpyxl import load_workbook

    errors: list[str] = []
    wb = load_workbook(str(path), data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        return [], ["Fichier vide."]

    headers = [norm_header(h) for h in header_row]
    col_last = _idx_of(headers, "last_name", "nom", "surname", "lastname")
    col_first = _idx_of(headers, "first_name", "prenom", "firstname", "given_name")
    col_title = _idx_of(headers, "title", "qualite", "qualité", "fonction", "role")
    col_inst = _idx_of(headers, "institution", "etablissement", "établissement", "employeur")

    missing = []
    if col_last is None:
        missing.append("last_name (nom)")
    if col_first is None:
        missing.append("first_name (prénom)")
    if missing:
        return [], [f"Colonnes obligatoires manquantes : {', '.join(missing)}."]

    def cell(row: tuple[Any, ...], idx: int | None) -> str:
        if idx is None or idx >= len(row):
            return ""
        v = row[idx]
        return "" if v is None else str(v).strip()

    members: list[dict[str, str]] = []
    for excel_row_idx, row in enumerate(rows_iter, start=2):
        last_name = cell(row, col_last)
        first_name = cell(row, col_first)
        if not last_name and not first_name:
            continue
        if not last_name or not first_name:
            errors.append(f"Ligne {excel_row_idx} : nom et prénom obligatoires.")
            continue
        members.append(
            {
                "last_name": last_name,
                "first_name": first_name,
                "title": cell(row, col_title),
                "institution": cell(row, col_inst),
            }
        )

    if not members and not errors:
        errors.append("Aucun membre valide trouvé (vérifiez les lignes de données).")
    return members, errors


def write_jury_import_template(path: str | Path) -> None:
    """Modèle Excel : une ligne d'en-tête + exemples + onglet Instructions."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Jury"
    ws.append(list(JURY_MEMBER_HEADERS))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in JURY_EXCEL_EXAMPLE_ROWS:
        ws.append(list(row))
    ws.freeze_panes = "A2"

    ins = wb.create_sheet("Instructions")
    ins.append(["Colonne", "Description"])
    ins["A1"].font = Font(bold=True)
    ins["B1"].font = Font(bold=True)
    for key in JURY_MEMBER_HEADERS:
        ins.append([key, JURY_EXCEL_LABELS_FR.get(key, "")])
    ins.append([])
    ins.append(["Usage", "Une ligne = un membre du jury. Importez la liste complète d'un coup."])
    ins.append(["Alias FR", "nom, prénom, qualité, institution sont aussi reconnus."])

    wb.save(str(path))
