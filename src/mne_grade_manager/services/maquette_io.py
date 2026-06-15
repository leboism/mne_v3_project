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
                "code_ip_paris": "",
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
                            global_coefficient=1.0,
                            display_order=order_val,
                        )
                        linked.add(cid)
                        next_order = max(next_order, order_val + 1)
        except Exception as exc:
            errors.append(f"{code}: {exc}")

    return created, updated, skipped, errors
