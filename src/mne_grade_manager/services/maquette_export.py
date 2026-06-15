"""Export d’une maquette (template) au format proche du fichier Excel UPSay."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository


def export_template_to_maquette_xlsx(
    repo: Repository,
    template_id: int,
    dest_path: Path | str,
    *,
    sheet_title: str = "Maquette",
) -> None:
    from openpyxl import Workbook

    p = Path(dest_path)
    courses = repo.list_template_courses(template_id)
    wb = Workbook()
    ws = wb.active
    safe_title = sheet_title.replace("/", "-")[:31] or "Maquette"
    ws.title = safe_title

    ws.append(
        [
            "Code",
            "Enseignements",
            "Semestre",
            "Durée",
            None,
            None,
            None,
            None,
            None,
            "Total",
            "EAD",
            "ECTS",
            "Code UE",
            "Seuil",
            "Contrôle de connaissances",
        ]
    )
    ws.append(
        [
            None,
            None,
            None,
            "CM",
            "TD",
            "TP",
            "Projet",
            "PT",
            "AA",
            None,
            None,
            None,
            None,
            None,
            None,
        ]
    )

    for r in courses:
        full = repo.get_course(int(r["course_id"])) or {}
        ws.append(
            [
                full.get("code") or r.get("code") or "",
                str(full.get("name") or r.get("name") or "").replace("\n", " "),
                str(full.get("semester") or ""),
                float(full.get("hours_cm") or 0),
                float(full.get("hours_td") or 0),
                float(full.get("hours_tp") or 0),
                float(full.get("hours_project") or 0),
                float(full.get("hours_pt") or 0),
                float(full.get("hours_aa") or 0),
                float(full.get("hours_total") or 0),
                str(full.get("ead_flag") or ""),
                float(full.get("ects") or r.get("ects") or 0),
                str(full.get("mne_module_code") or ""),
                "",
                str(full.get("mcc_text") or ""),
            ]
        )

    wb.save(str(p))
