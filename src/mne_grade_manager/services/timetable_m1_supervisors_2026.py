"""Responsables d'UE M1 — référentiel Timetable 2026-2027 v0 (PDF secrétariat)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TimetableSupervisorSeed:
    mne_module_code: str
    teacher_last_name: str
    teacher_first_name: str
    teacher_institution: str
    supervisors_text: str = ""


# Source : Timetable-M1-26-27- v0d1007erasmus.pdf (page référentiel superviseurs).
M1_2026_2027_SUPERVISORS: tuple[TimetableSupervisorSeed, ...] = (
    TimetableSupervisorSeed("M1B1-C-NUCL", "Lebois", "Matthieu", "UPSAY"),
    TimetableSupervisorSeed("M1B1-C-THER", "Baklouti", "Donia", "UPSAY"),
    TimetableSupervisorSeed(
        "M1B1-C-RADIOMAT",
        "Bodineau",
        "Jean-Christophe",
        "INSTN / CEA",
        "Jean-Christophe Bodineau (INSTN / CEA), Jonathan Dumazert (CEA), "
        "Nicolas Dufour (CEA), Stéphanie Pellegrino (INSTN / CEA)",
    ),
    TimetableSupervisorSeed("M1B1-C-MME", "Lafage", "Vincent", "UPSAY"),
    TimetableSupervisorSeed(
        "M1B1-C-CHEM",
        "Puel",
        "François",
        "CS",
        "François Puel (CS), Didier Dalmazzone (IPPARIS), Patrice Paricaud (IPPARIS)",
    ),
    TimetableSupervisorSeed("M1B2-C-ENER", "Dalmazzone", "Didier", "ENSTA / IPParis"),
    TimetableSupervisorSeed(
        "M1B2-C-ECO",
        "Perez",
        "Yannick",
        "CS",
        "Yannick Perez (CS), Diego-Manuel Cebreros Saettone (CS)",
    ),
    TimetableSupervisorSeed("M1B2-C-PROJ", "", "", ""),
    TimetableSupervisorSeed("M1B2-C-REAC", "Patricot", "Cyril", "CEA"),
    TimetableSupervisorSeed("M1B2-C-CHOICE", "Prud'homme", "Nathalie", "UPSAY"),
    TimetableSupervisorSeed("M1B3-P-NEUT", "Patricot", "Cyril", "CEA"),
    TimetableSupervisorSeed(
        "M1B3-P-ELEC",
        "Dai",
        "Jing",
        "CS",
        "Jing Dai (CS), Simon Meunier (CS)",
    ),
    TimetableSupervisorSeed(
        "M1B3-P-MATE",
        "Gloannec",
        "Anne-Lise",
        "ENSTA",
        "Anne-Lise Gloannec (ENSTA), Bertrand Reynier (ENSTA), Servane Coste (INSTN), "
        "Clotaire Chevalier (INSTN), Baris Telmen (ENSTA)",
    ),
    TimetableSupervisorSeed("M1B3-P-QUANT", "Anzari", "Résa", "UPSAY"),
    TimetableSupervisorSeed("M1B3-P-CONT", "Lhachemi", "Hugo", "CS"),
    TimetableSupervisorSeed(
        "M1B3-P-FLUI",
        "Mallick",
        "Nicolas",
        "CS",
        "Nicolas Mallick (CS), Salvatore Iavarone (CS), Michael Kirkpatrick (CS)",
    ),
    TimetableSupervisorSeed("M1B3-P-MECH", "Gloannec", "Anne-Lise", "ENSTA"),
    TimetableSupervisorSeed("M1B3-P-RADIOMAT", "Lebois", "Matthieu", "UPSAY"),
    TimetableSupervisorSeed(
        "M1B3-X-SOL",
        "Lefevre",
        "Grégory",
        "Chimie ParisTech",
        "Grégory Lefevre (Chimie ParisTech), Romain Dagnelie (CEA), Marion Roy (CEA), "
        "Thomas Dumas (CEA), Pascal Reiller (CEA)",
    ),
    TimetableSupervisorSeed("M1B3-X-NUMMATE", "Garrido", "Frédérico", "UPSAY"),
    TimetableSupervisorSeed("M1B3-X-RAD", "Denisov", "Sergey", "CNRS"),
    TimetableSupervisorSeed(
        "M1B3-X-CHEM",
        "Bion",
        "Lionel",
        "INSTN",
        "Lionel Bion (INSTN), Romain Dagnelie (CEA)",
    ),
    TimetableSupervisorSeed("M1B3-X-SPECT", "Maloubier", "Mélody", "CNRS"),
    TimetableSupervisorSeed(
        "M1B3-X-ANCRE",
        "Cannes",
        "Céline",
        "UPSAY",
        "Céline Cannes (UPSAY), Sylvie Delpech (CNRS), Atanas Dinkov (UPSAY), "
        "Charly Carrière (UPSAY)",
    ),
    TimetableSupervisorSeed(
        "M1B3-X-CHEMNUCL",
        "Prud'homme",
        "Nathalie",
        "UPSAY",
        "Nathalie Prud'homme (UPSAY), Veronika Zinovyeva (UPSAY)",
    ),
)


def apply_m1_2026_supervisors(repo) -> dict[str, Any]:
    """Met à jour les fiches UE (responsable) à partir du référentiel PDF."""
    updated = 0
    missing: list[str] = []
    skipped: list[str] = []
    for seed in M1_2026_2027_SUPERVISORS:
        mne = seed.mne_module_code.strip().upper()
        rows = repo.db.query_all(
            """
            SELECT id, teacher_last_name, teacher_first_name, teacher_institution, mcc_text
            FROM courses
            WHERE UPPER(TRIM(mne_module_code)) = ?
            """,
            (mne,),
        )
        if not rows:
            missing.append(mne)
            continue
        if not seed.teacher_last_name and not seed.supervisors_text:
            skipped.append(mne)
            continue
        mcc = seed.supervisors_text or (
            f"{seed.teacher_first_name} {seed.teacher_last_name} ({seed.teacher_institution})".strip()
        )
        for row in rows:
            c = repo.get_course(int(row["id"])) or {}
            repo.update_course(
                int(row["id"]),
                str(c.get("code") or ""),
                str(c.get("name") or ""),
                float(c.get("ects") or 0),
                str(c.get("description") or ""),
                hours_total=float(c.get("hours_total") or 0),
                hours_cm=float(c.get("hours_cm") or 0),
                hours_td=float(c.get("hours_td") or 0),
                hours_tp=float(c.get("hours_tp") or 0),
                hours_project=float(c.get("hours_project") or 0),
                hours_pt=float(c.get("hours_pt") or 0),
                hours_aa=float(c.get("hours_aa") or 0),
                code_ip_paris=str(c.get("code_ip_paris") or ""),
                code_other=str(c.get("code_other") or ""),
                semester=str(c.get("semester") or ""),
                mcc_text=mcc,
                ead_flag=str(c.get("ead_flag") or ""),
                course_type=str(c.get("course_type") or "standard"),
                teacher_last_name=seed.teacher_last_name,
                teacher_first_name=seed.teacher_first_name,
                teacher_email=str(c.get("teacher_email") or ""),
                teacher_email_work=str(c.get("teacher_email_work") or ""),
                teacher_email_work_2=str(c.get("teacher_email_work_2") or ""),
                teacher_email_personal=str(c.get("teacher_email_personal") or ""),
                teacher_phone=str(c.get("teacher_phone") or ""),
                teacher_phone_work=str(c.get("teacher_phone_work") or ""),
                teacher_phone_work_2=str(c.get("teacher_phone_work_2") or ""),
                teacher_phone_mobile=str(c.get("teacher_phone_mobile") or ""),
                teacher_institution=seed.teacher_institution,
                carrier_partner=str(c.get("carrier_partner") or ""),
                carrier_partner_other=str(c.get("carrier_partner_other") or ""),
                mne_module_code=str(c.get("mne_module_code") or mne),
            )
            updated += 1
    return {"updated": updated, "missing": missing, "skipped": skipped}
