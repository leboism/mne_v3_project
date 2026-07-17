"""Génération de notes pour une promotion réelle (hors préfixe DEMO-)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .demo_seed import (
    _apply_scenario_extras,
    _course_has_grades,
    _gradable_template_courses,
    _seed_course_grades,
)

if TYPE_CHECKING:
    from .repository import Repository

# Profils couvrant les cas de validation / délibération.
SCENARIO_LABELS: dict[str, str] = {
    "alice": "Validé — bonnes notes, note S2 manquante, bonus jury UE",
    "jury_year": "Validé — bonus jury année (+0,25)",
    "average": "Validé — notes dans la moyenne (~10,5)",
    "borderline_ok": "Validé — moyenne juste au-dessus de 10",
    "floor_waiver": "Validé — une UE < 7 avec dérogation jury seuil",
    "benoit": "Non validé — DEF S2 + ABJ + 2ᵉ session",
    "weak_m2": "Non validé — notes faibles (bloc / moyenne)",
}

# Étudiants importés / promotion M1 2026-2027 : id → scénario.
M1_2026_2027_SCENARIOS: dict[int, str] = {
    # M1 P
    198: "alice",  # Ilarion
    195: "alice",  # Rivera Ramirez
    197: "jury_year",  # Hernández
    194: "borderline_ok",  # Jagadeesh
    199: "benoit",  # Kotoko
    202: "weak_m2",  # Olaokun
    204: "floor_waiver",  # Panyopas
    201: "average",  # Salazar
    # M1 C
    203: "alice",  # Acosta
    191: "jury_year",  # XU
    192: "average",  # Suprice
    193: "benoit",  # Ahoua
    196: "weak_m2",  # Fabianiak
    200: "floor_waiver",  # Petrus
}


def _template_for_student(repo: Repository, student: dict[str, Any]) -> dict[str, Any] | None:
    lv = str(student.get("level") or "").strip().upper()
    tr = str(student.get("track") or "").strip().upper()
    ay = str(student.get("academic_year") or "").strip()
    for tpl in repo.list_templates():
        if (
            str(tpl.get("level") or "").strip().upper() == lv
            and str(tpl.get("track") or "").strip().upper() == tr
            and str(tpl.get("academic_year") or "").strip() == ay
        ):
            return dict(tpl)
    for enr in repo.list_enrollments_for_student(int(student["id"])):
        tpl = repo.get_template(int(enr["template_id"]))
        if tpl:
            return dict(tpl)
    return None


def seed_student_grades(
    repo: Repository,
    student_id: int,
    scenario: str,
    *,
    force: bool = False,
) -> bool:
    """Génère les notes d'un étudiant. Retourne False si ignoré (déjà noté)."""
    student = repo.get_student(int(student_id))
    if not student:
        return False
    tpl = _template_for_student(repo, student)
    if tpl is None:
        return False
    tid = int(tpl["id"])
    gradable = _gradable_template_courses(repo, tid)
    if not gradable:
        return False
    sid = int(student_id)
    if force:
        for row in gradable:
            cid = int(row["course_id"])
            for assess in repo.list_assessments(cid):
                repo.db.execute(
                    "DELETE FROM grades WHERE student_id = ? AND assessment_id = ?",
                    (sid, int(assess["id"])),
                )
        repo.db.execute(
            "DELETE FROM jury_adjustments WHERE student_id = ? AND template_id = ?",
            (sid, tid),
        )
        repo.db.execute(
            "DELETE FROM ue_jury_floor_waivers WHERE student_id = ? AND template_id = ?",
            (sid, tid),
        )
        repo.db.execute(
            "DELETE FROM second_session_decisions WHERE student_id = ? AND template_id = ?",
            (sid, tid),
        )
    elif any(_course_has_grades(repo, sid, int(row["course_id"])) for row in gradable):
        return False
    for i, row in enumerate(gradable):
        _seed_course_grades(repo, sid, int(row["course_id"]), i, scenario)
    _apply_scenario_extras(repo, sid, tid, gradable, scenario)
    return True


def run_cohort_grades_seed(
    repo: Repository,
    *,
    academic_year: str = "2026-2027",
    level: str = "M1",
    assignments: dict[int, str] | None = None,
    force: bool = False,
) -> str:
    """Génère des notes variées pour la promotion. Retourne un résumé texte."""
    ay = str(academic_year or "").strip()
    lv = str(level or "M1").strip().upper()
    mapping = dict(assignments or M1_2026_2027_SCENARIOS)
    seeded: list[str] = []
    skipped: list[str] = []
    missing_tpl: list[str] = []

    for student in repo.list_students(include_withdrawn=False):
        sid = int(student["id"])
        if str(student.get("academic_year") or "").strip() != ay:
            continue
        if str(student.get("level") or "").strip().upper() != lv:
            continue
        scenario = mapping.get(sid)
        if not scenario:
            skipped.append(f"{student.get('last_name')} {student.get('first_name')} (pas de scénario)")
            continue
        if _template_for_student(repo, student) is None:
            missing_tpl.append(f"{student.get('last_name')} {student.get('first_name')}")
            continue
        if seed_student_grades(repo, sid, scenario, force=force):
            label = SCENARIO_LABELS.get(scenario, scenario)
            seeded.append(f"  – {student.get('last_name')} {student.get('first_name')} : {label}")
        else:
            skipped.append(f"{student.get('last_name')} {student.get('first_name')} (notes déjà présentes)")

    lines = [f"Notes générées pour le millésime {ay} ({lv}).", ""]
    if seeded:
        lines.append(f"{len(seeded)} étudiant(s) :")
        lines.extend(seeded)
    if skipped:
        lines.append("")
        lines.append(f"Ignorés ({len(skipped)}) :")
        lines.extend(f"  – {s}" for s in skipped)
    if missing_tpl:
        lines.append("")
        lines.append(f"Sans maquette / inscription ({len(missing_tpl)}) :")
        lines.extend(f"  – {s}" for s in missing_tpl)

    lines.append("")
    lines.append("Consultez l'onglet Résultats ou Délibération pour les cas de validation.")
    return "\n".join(lines)


def summarize_validation_outcomes(repo: Repository, *, academic_year: str, level: str = "M1") -> list[str]:
    """Résumé validation année (S2) pour contrôle après seed."""
    ay = str(academic_year or "").strip()
    lv = str(level or "M1").strip().upper()
    out: list[str] = []
    for student in repo.list_students(include_withdrawn=False):
        if str(student.get("academic_year") or "").strip() != ay:
            continue
        if str(student.get("level") or "").strip().upper() != lv:
            continue
        tpl = _template_for_student(repo, student)
        if not tpl:
            continue
        sid, tid = int(student["id"]), int(tpl["id"])
        rows = repo.get_student_result_summary(
            tid, view_session="s2", include_all_students=True, auto_sync_s2=True
        )
        row = next((r for r in rows if int(r.get("student_id") or 0) == sid), None)
        ev = repo.evaluate_student_year_validation(
            sid, tid, view_session="s2", result_row=row, auto_sync_s2=False
        )
        status = "VALIDÉ" if ev.get("validated") else "NON VALIDÉ"
        issues = "; ".join(ev.get("issues") or []) or "—"
        out.append(f"{student.get('last_name')} {student.get('first_name')} : {status} — {issues}")
    return out
