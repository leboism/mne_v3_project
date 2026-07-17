"""
Maquettes officielles 2026-2027 — accréditation 2026-2031.

Sources :
- M1 : ``OF_PR1162_2026-2027_2026-05-20_mod.xlsx`` (onglets M1P / M1C)
- M2 : erratum PDF (tronc commun + DWM complet + règles de validation)
  et OF PR1163 pour les parcours NPD, NPO, NFC, NRPE (blocs 3–4 spécialité).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository

AY_ERRATUM = "2026-2027"
TRACK_DWM = "DWM"
M2_TRACKS = ("NPD", "NPO", "DWM", "NFC", "NRPE")

DEFAULT_M1_OF = Path(
    "/Users/lebois/Documents/Documents - mac194/Enseignement/Nuclear Energy/"
    "M1 Nuclear Energy /Organisation/Maquette 2026-2031/"
    "OF_PR1162_2026-2027_2026-05-20_mod.xlsx"
)
DEFAULT_M2_OF = Path(
    "/Users/lebois/Documents/Documents - mac194/Enseignement/Nuclear Energy/"
    "M2 Nuclear Energy/Organisation/Maquette 2026-2031/"
    "OF_PR1163_M2-IngNucleaire_2026-2027_2026-02-13.xlsx"
)

BLOC_1 = (
    "Bloc 1 (common courses) : Safety & Risk Management "
    "(NDWM/NFC/NPO/NPD/NRPE)"
)
BLOC_2 = (
    "Bloc 2 (common courses) :  Electricity Production : tools, needs and "
    "capacities (NDWM/NFC/NPD/NPO/NRPE)"
)
BLOC_3_DWM = "Bloc 3 NDWM : Decommissioning Waste Management: principles and Methodology"
BLOC_4_DWM = "Bloc 4 NDWM : Decommissioning Waste Management: applications"
BLOC_5 = "Bloc 5 : Internship"

COMMON_BLOC_1_CODES = frozenset({"EN00002153", "EN00002156", "EN00002157"})
COMMON_BLOC_2_CODES = frozenset({"EN00002154", "EN00002155", "EN00002176"})
INTERNSHIP_CODE = "EN00018956"

DWM_TEMPLATE_SPEC: list[tuple[str, str, int]] = [
    ("EN00002153", BLOC_1, 1),
    ("EN00002156", BLOC_1, 2),
    ("EN00002157", BLOC_1, 3),
    ("EN00002154", BLOC_2, 4),
    ("EN00002155", BLOC_2, 5),
    ("EN00002176", BLOC_2, 6),
    ("EN00002161", BLOC_3_DWM, 7),
    ("EN00002162", BLOC_3_DWM, 8),
    ("EN00002158", BLOC_3_DWM, 9),
    ("EN00002177", BLOC_3_DWM, 10),
    ("EN00002178", BLOC_4_DWM, 11),
    ("EN00002179", BLOC_4_DWM, 12),
    ("EN00002180", BLOC_4_DWM, 13),
    ("EN00013743", BLOC_4_DWM, 14),
    ("EN00012742", BLOC_4_DWM, 15),
    ("EN00018956", BLOC_5, 16),
]

COURSE_NAME_ERRATUM: dict[str, str] = {
    "EN00002161": "Politics of Decommissioning Nuclear facilities",
    "EN00002162": "Measurement methods and techniques",
    "EN00002177": "Waste management: politics, strategy and methodology",
    "EN00002179": "Risk management of  Dismantling operation",
    "EN00013743": "Dismantlement case studies",
    "EN00002180": "Modelisation",
}

COURSE_ECTS_ERRATUM: dict[str, float] = {
    "EN00002180": 0.0,
}

COURSE_MNE_ERRATUM: dict[str, str] = {
    "EN00002153": "M2B1-C-RP",
    "EN00002156": "M2B1-C-SAFE",
    "EN00002179": "M2B4-W-RMDO",
    "EN00013743": "M2B4-W-DIS",
}

NEW_COURSE_DEFAULTS: dict[str, dict[str, Any]] = {
    "EN00002180": {
        "name": "Modelisation",
        "ects": 0.0,
        "semester": "Semestre 2",
        "ead_flag": "NON",
        "hours_total": 0.0,
        "mcc_text": "",
        "course_type": "standard",
    },
}


def _normalize_code(code: str) -> str:
    return str(code or "").strip().upper()


def _patch_row_block_from_pdf(row: dict[str, Any], *, track: str) -> dict[str, Any]:
    """Applique les libellés de blocs tronc commun / stage de l'erratum PDF."""
    out = dict(row)
    code = _normalize_code(out.get("code"))
    if code in COMMON_BLOC_1_CODES:
        out["block_name"] = BLOC_1
    elif code in COMMON_BLOC_2_CODES:
        out["block_name"] = BLOC_2
    elif code == INTERNSHIP_CODE:
        out["block_name"] = BLOC_5
    elif track == TRACK_DWM and code in {c for c, _, _ in DWM_TEMPLATE_SPEC}:
        for spec_code, bloc, order in DWM_TEMPLATE_SPEC:
            if spec_code == code:
                out["block_name"] = bloc
                out["display_order"] = order
                break
    name = COURSE_NAME_ERRATUM.get(code)
    if name:
        out["name"] = name
    if code in COURSE_ECTS_ERRATUM:
        out["ects"] = COURSE_ECTS_ERRATUM[code]
    return out


def _ensure_erratum_courses(repo: Repository) -> tuple[list[str], list[str]]:
    created: list[str] = []
    updated: list[str] = []

    for code, fields in NEW_COURSE_DEFAULTS.items():
        if repo.get_course_by_code(code):
            continue
        repo.add_course(code, **fields)
        created.append(code)

    for code, name in COURSE_NAME_ERRATUM.items():
        row = repo.get_course_by_code(code)
        if not row:
            continue
        ects = COURSE_ECTS_ERRATUM.get(code)
        if ects is None:
            ects = float(row.get("ects") or 0)
        cur_name = str(row.get("name") or "").strip()
        cur_ects = float(row.get("ects") or 0)
        mne = COURSE_MNE_ERRATUM.get(code, str(row.get("mne_module_code") or ""))
        if (
            cur_name != name
            or abs(cur_ects - float(ects)) > 0.01
            or str(row.get("mne_module_code") or "").strip().upper() != mne.upper()
        ):
            repo.update_course(
                int(row["id"]),
                code,
                name if code in COURSE_NAME_ERRATUM else str(row.get("name") or ""),
                ects=float(ects),
                semester=str(row.get("semester") or ""),
                mcc_text=str(row.get("mcc_text") or ""),
                ead_flag=str(row.get("ead_flag") or ""),
                course_type=str(row.get("course_type") or "standard"),
                mne_module_code=mne,
                hours_total=float(row.get("hours_total") or 0),
                hours_cm=float(row.get("hours_cm") or 0),
                hours_td=float(row.get("hours_td") or 0),
                hours_tp=float(row.get("hours_tp") or 0),
                hours_project=float(row.get("hours_project") or 0),
            )
            updated.append(code)

    for code, mne in COURSE_MNE_ERRATUM.items():
        if code in COURSE_NAME_ERRATUM:
            continue
        row = repo.get_course_by_code(code)
        if not row:
            continue
        cur_mne = str(row.get("mne_module_code") or "").strip().upper()
        if cur_mne != mne.upper():
            repo.update_course(
                int(row["id"]),
                code,
                str(row.get("name") or ""),
                ects=float(row.get("ects") or 0),
                semester=str(row.get("semester") or ""),
                mcc_text=str(row.get("mcc_text") or ""),
                ead_flag=str(row.get("ead_flag") or ""),
                course_type=str(row.get("course_type") or "standard"),
                mne_module_code=mne,
                hours_total=float(row.get("hours_total") or 0),
                hours_cm=float(row.get("hours_cm") or 0),
                hours_td=float(row.get("hours_td") or 0),
                hours_tp=float(row.get("hours_tp") or 0),
                hours_project=float(row.get("hours_project") or 0),
            )
            updated.append(code)
    return created, updated


def _rebuild_template_from_rows(
    repo: Repository,
    template_id: int,
    rows: list[dict[str, Any]],
    *,
    update_existing: bool = True,
) -> int:
    from .maquette_io import import_maquette_row_dicts

    tid = int(template_id)
    repo.db.execute("DELETE FROM template_courses WHERE template_id = ?", (tid,))
    import_maquette_row_dicts(
        repo,
        rows,
        update_existing=update_existing,
        template_id=tid,
        attach_to_template=True,
    )
    return len(repo.list_template_courses(tid))


def _parse_m1_rows(path: Path, sheet: str, track: str, *, academic_year: str) -> list[dict[str, Any]]:
    from .maquette_import import (
        apply_secretariat_course_codes,
        enrich_maquette_rows_mne_codes,
        load_maquette_sheet,
    )

    result = load_maquette_sheet(path, sheet)
    rows = enrich_maquette_rows_mne_codes(result.rows, level="M1", track=track)
    return apply_secretariat_course_codes(rows, academic_year=academic_year)


def _parse_m2_of_rows(path: Path, *, academic_year: str) -> dict[str, list[dict[str, Any]]]:
    from .maquette_import import (
        apply_secretariat_course_codes,
        enrich_maquette_rows_mne_codes,
        list_maquette_sheets,
        plan_consolidated_of_import,
    )

    sheets = list_maquette_sheets(path)
    if not sheets:
        raise ValueError(f"Aucun onglet dans {path}")
    plans = plan_consolidated_of_import(path, sheets[0], academic_year=academic_year)
    out: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        rows = enrich_maquette_rows_mne_codes(plan.rows, level=plan.level, track=plan.track)
        rows = apply_secretariat_course_codes(rows, academic_year=academic_year)
        out[str(plan.track).strip().upper()] = rows
    return out


def apply_m1_official_maquette(
    repo: Repository,
    *,
    academic_year: str = AY_ERRATUM,
    of_path: Path | str = DEFAULT_M1_OF,
) -> dict[str, Any]:
    from .maquette_import import MAQUETTE_SHEET_TO_TRACK, list_maquette_sheets

    ay = str(academic_year or AY_ERRATUM).strip()
    path = Path(of_path)
    if not path.is_file():
        raise FileNotFoundError(f"OF M1 introuvable : {path}")

    summary: dict[str, Any] = {"academic_year": ay, "tracks": {}}
    for sheet in list_maquette_sheets(path):
        track = MAQUETTE_SHEET_TO_TRACK.get(sheet)
        if not track:
            continue
        tpl = repo.find_template_for_year_level_track(academic_year=ay, level="M1", track=track)
        if not tpl:
            raise ValueError(f"Maquette M1 {track} absente pour {ay}.")
        rows = _parse_m1_rows(path, sheet, track, academic_year=ay)
        n = _rebuild_template_from_rows(repo, int(tpl["id"]), rows)
        total = sum(float(r.get("ects") or 0) for r in rows)
        summary["tracks"][track] = {
            "template_id": int(tpl["id"]),
            "rows": n,
            "total_ects": total,
        }
    return summary


def apply_m2_dwm_accreditation_erratum(
    repo: Repository,
    *,
    academic_year: str = AY_ERRATUM,
) -> dict[str, Any]:
    """Maquette M2 DWM : erratum PDF intégral (16 UE)."""
    ay = str(academic_year or AY_ERRATUM).strip()
    tpl = repo.find_template_for_year_level_track(academic_year=ay, level="M2", track=TRACK_DWM)
    if not tpl:
        raise ValueError(f"Aucune maquette M2 {TRACK_DWM} pour {ay}.")

    created, updated = _ensure_erratum_courses(repo)
    tid = int(tpl["id"])
    repo.db.execute("DELETE FROM template_courses WHERE template_id = ?", (tid,))

    linked: list[str] = []
    missing: list[str] = []
    for code, block_name, display_order in DWM_TEMPLATE_SPEC:
        row = repo.get_course_by_code(code)
        if not row:
            missing.append(code)
            continue
        repo.add_course_to_template(
            tid,
            int(row["id"]),
            block_name=block_name,
            display_order=int(display_order),
        )
        linked.append(code)

    if missing:
        raise ValueError("Cours introuvables pour l'erratum DWM : " + ", ".join(missing))

    total_ects = sum(
        float((repo.get_course_by_code(code) or {}).get("ects") or 0)
        for code, _, _ in DWM_TEMPLATE_SPEC
    )
    return {
        "academic_year": ay,
        "track": TRACK_DWM,
        "template_id": tid,
        "courses_created": created,
        "courses_updated": updated,
        "courses_linked": linked,
        "template_rows": len(repo.list_template_courses(tid)),
        "total_ects": total_ects,
    }


def apply_m2_official_maquettes(
    repo: Repository,
    *,
    academic_year: str = AY_ERRATUM,
    of_path: Path | str = DEFAULT_M2_OF,
) -> dict[str, Any]:
    """
    M2 : DWM selon l'erratum PDF ; autres parcours selon OF PR1163
    avec tronc commun / stage harmonisés (libellés PDF).
    """
    ay = str(academic_year or AY_ERRATUM).strip()
    path = Path(of_path)
    if not path.is_file():
        raise FileNotFoundError(f"OF M2 introuvable : {path}")

    created, updated = _ensure_erratum_courses(repo)
    by_track = _parse_m2_of_rows(path, academic_year=ay)
    summary: dict[str, Any] = {
        "academic_year": ay,
        "courses_created": created,
        "courses_updated": updated,
        "tracks": {},
    }

    dwm_result = apply_m2_dwm_accreditation_erratum(repo, academic_year=ay)
    summary["tracks"][TRACK_DWM] = dwm_result

    for track in M2_TRACKS:
        if track == TRACK_DWM:
            continue
        rows = by_track.get(track)
        if not rows:
            raise ValueError(f"Parcours M2 {track} absent de l'OF PR1163.")
        tpl = repo.find_template_for_year_level_track(academic_year=ay, level="M2", track=track)
        if not tpl:
            raise ValueError(f"Maquette M2 {track} absente pour {ay}.")
        patched = [_patch_row_block_from_pdf(r, track=track) for r in rows]
        n = _rebuild_template_from_rows(repo, int(tpl["id"]), patched)
        total = sum(float(r.get("ects") or 0) for r in patched)
        summary["tracks"][track] = {
            "template_id": int(tpl["id"]),
            "rows": n,
            "total_ects": total,
        }
    return summary


def apply_official_accreditation_maquettes_2026_2027(
    repo: Repository,
    *,
    academic_year: str = AY_ERRATUM,
    m1_of_path: Path | str = DEFAULT_M1_OF,
    m2_of_path: Path | str = DEFAULT_M2_OF,
) -> dict[str, Any]:
    """Applique M1 (OF PR1162 mod) + M2 (PDF erratum + OF PR1163)."""
    m1 = apply_m1_official_maquette(repo, academic_year=academic_year, of_path=m1_of_path)
    m2 = apply_m2_official_maquettes(repo, academic_year=academic_year, of_path=m2_of_path)
    return {"academic_year": academic_year, "m1": m1, "m2": m2}
