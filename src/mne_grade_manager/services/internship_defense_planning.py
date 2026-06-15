"""Planning des soutenances de stage (export CSV / texte)."""

from __future__ import annotations

import csv
import io
from typing import Any

from .dates import format_defense_slot
from .lookups import student_transcript_number

PLANNING_HEADERS = [
    "Date",
    "Heure",
    "N° I.N.E.",
    "Nom",
    "Prénom",
    "Niveau",
    "Parcours",
    "Maquette",
    "Sujet",
    "Encadrant",
    "Établ. encadrant",
    "Rapporteur",
    "Établ. rapporteur",
]


def planning_rows_for_export(rows: list[dict[str, Any]]) -> list[list[str]]:
    out: list[list[str]] = []
    for r in rows:
        out.append(
            [
                str(r.get("defense_date") or ""),
                str(r.get("defense_time") or ""),
                student_transcript_number(r),
                str(r.get("last_name") or ""),
                str(r.get("first_name") or ""),
                str(r.get("level") or ""),
                str(r.get("track") or ""),
                str(r.get("template_name") or ""),
                str(r.get("topic") or "").replace("\n", " "),
                str(r.get("supervisor_name") or ""),
                str(r.get("supervisor_institution") or ""),
                str(r.get("reporter_name") or ""),
                str(r.get("reporter_institution") or ""),
            ]
        )
    return out


def planning_to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PLANNING_HEADERS)
    writer.writerows(planning_rows_for_export(rows))
    return buf.getvalue()


def planning_to_text(
    rows: list[dict[str, Any]], *, course_label: str, academic_year: str
) -> str:
    lines = [f"Planning des soutenances — {course_label}"]
    if academic_year:
        lines.append(f"Millésime : {academic_year}")
    lines.append("")
    if not rows:
        lines.append("(Aucun étudiant inscrit ou aucune maquette pour ce millésime.)")
        return "\n".join(lines)
    for i, r in enumerate(rows, start=1):
        slot = format_defense_slot(
            str(r.get("defense_date") or ""),
            str(r.get("defense_time") or ""),
        )
        who = f"{r.get('last_name', '')} {r.get('first_name', '')}".strip()
        sn = student_transcript_number(r)
        if sn:
            who = f"{who} ({sn})"
        topic = str(r.get("topic") or "").strip()
        rep = str(r.get("reporter_name") or "").strip()
        rep_inst = str(r.get("reporter_institution") or "").strip()
        rep_line = rep
        if rep_inst:
            rep_line = f"{rep} — {rep_inst}" if rep else rep_inst
        lines.append(f"{i}. {slot} — {who}")
        if topic:
            lines.append(f"   Sujet : {topic}")
        if rep_line:
            lines.append(f"   Rapporteur : {rep_line}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
