"""Import / export Excel maquette (cours + rattachement optionnel à un template)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository


def import_maquette_row_dicts(
    repo: Repository,
    rows: list[dict[str, Any]],
    *,
    update_existing: bool,
    template_id: int | None = None,
    attach_to_template: bool = False,
) -> tuple[int, int, int, list[str]]:
    """
    Insère ou met à jour les cours à partir des dicts (format ``parse_maquette_rows``).
    Si ``attach_to_template`` et ``template_id`` : ajoute chaque cours à la maquette
    s’il n’y est pas encore (ordre = suite du max ``display_order`` existant).
    """
    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    linked: set[int] = set()
    next_order = 0
    if template_id is not None and attach_to_template:
        tc = repo.list_template_courses(template_id)
        linked = {int(r["course_id"]) for r in tc}
        next_order = max((int(r["display_order"]) for r in tc), default=-1) + 1

    for row in rows:
        code = row["code"]
        try:
            existing = repo.get_course_by_code(code)
            fields = {
                "name": row["name"],
                "ects": float(row.get("ects") or 0),
                "description": "",
                "hours_total": float(row.get("hours_total") or 0),
                "hours_cm": float(row.get("hours_cm") or 0),
                "hours_td": float(row.get("hours_td") or 0),
                "hours_tp": float(row.get("hours_tp") or 0),
                "hours_project": float(row.get("hours_project") or 0),
                "hours_pt": float(row.get("hours_pt") or 0),
                "hours_aa": float(row.get("hours_aa") or 0),
                "code_ip_paris": str(row.get("code_ip_paris") or "").strip(),
                "code_other": str(row.get("code_other") or "").strip(),
                "mne_module_code": str(row.get("mne_module_code") or "").strip(),
                "semester": str(row.get("semester") or "").strip(),
                "mcc_text": str(row.get("mcc_text") or "").strip(),
                "ead_flag": str(row.get("ead_flag") or "").strip(),
            }
            if existing:
                if update_existing:
                    repo.update_course(int(existing["id"]), code, **fields)
                    updated += 1
                else:
                    skipped += 1
            else:
                repo.add_course(code, **fields)
                created += 1

            if template_id is not None and attach_to_template:
                c = repo.get_course_by_code(code)
                if c is not None:
                    cid = int(c["id"])
                    if cid not in linked:
                        block_name = str(row.get("block_name") or "").strip()
                        display_order = row.get("display_order")
                        try:
                            order_val = int(display_order) if display_order is not None else next_order
                        except (TypeError, ValueError):
                            order_val = next_order
                        repo.add_course_to_template(
                            template_id,
                            cid,
                            block_name=block_name,
                            display_order=order_val,
                        )
                        linked.add(cid)
                        next_order = max(next_order, order_val + 1)
        except Exception as exc:
            errors.append(f"{code}: {exc}")

    return created, updated, skipped, errors


def reimport_consolidated_ofs_for_millésime(
    repo: Repository,
    path: str,
    sheet_name: str,
    academic_year: str,
    *,
    template_ids_by_track: dict[str, int],
    update_existing: bool = True,
) -> dict[str, Any]:
    """
    Réimporte une OF consolidée (PR1162/PR1163) pour un millésime :
    vide les maquettes cibles, réinjecte les UE avec nomenclature secrétariat si applicable.
    """
    from pathlib import Path

    from .maquette_import import (
        apply_secretariat_course_codes,
        enrich_maquette_rows_mne_codes,
        plan_consolidated_of_import,
    )

    ay = str(academic_year or "").strip()
    if not ay:
        raise ValueError("Millésime obligatoire.")
    plans = plan_consolidated_of_import(Path(path), sheet_name, academic_year=ay)
    summary: dict[str, Any] = {"academic_year": ay, "tracks": {}}

    for plan in plans:
        tr = str(plan.track or "").strip().upper()
        tid = int(template_ids_by_track.get(tr) or 0)
        if not tid:
            summary["tracks"][tr] = {"error": "maquette introuvable"}
            continue
        repo.update_template_metadata(
            tid,
            name=plan.name or f"{ay} — {plan.level} {tr}",
            level=plan.level,
            track=tr,
            academic_year=ay,
            version="1",
        )
        repo.db.execute("DELETE FROM template_courses WHERE template_id = ?", (tid,))
        rows = enrich_maquette_rows_mne_codes(plan.rows, level=plan.level, track=plan.track)
        rows = apply_secretariat_course_codes(rows, academic_year=ay)
        created, updated, skipped, errors = import_maquette_row_dicts(
            repo,
            rows,
            update_existing=update_existing,
            template_id=tid,
            attach_to_template=True,
        )
        summary["tracks"][tr] = {
            "template_id": tid,
            "courses": len(rows),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors[:8],
            "codes": [r.get("code") for r in rows],
        }
    return summary
