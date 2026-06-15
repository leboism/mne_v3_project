"""Jeu de données de démonstration (idempotent, préfixe DEMO-)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .lookups import suggest_institutional_email

if TYPE_CHECKING:
    from .repository import Repository

DEMO_YEAR = "2025-2026"


def _resolve_demo_academic_year(repo: Repository, preferred: str = "") -> str:
    """Millésime cible : session en cours, puis maquettes existantes, puis défaut."""
    pref = (preferred or "").strip()
    if pref:
        return pref
    years = sorted(
        {str(t.get("academic_year") or "").strip() for t in repo.list_templates() if str(t.get("academic_year") or "").strip()},
        reverse=True,
    )
    if years:
        return years[0]
    return DEMO_YEAR


def run_demo_seed(repo: Repository, *, academic_year: str = "") -> str:
    """Crée ou complète les données DEMO. Retourne un résumé texte."""
    demo_year = _resolve_demo_academic_year(repo, academic_year)
    courses = _ensure_demo_courses(repo)
    templates = _ensure_demo_templates(repo, courses, demo_year)
    students = _ensure_demo_students(repo, demo_year)
    _ensure_enrollments(repo, students, templates)
    _seed_all_grades(repo, students, courses, templates)
    _ensure_jury_data(repo, templates, students, courses)
    _ensure_master_team(repo, demo_year)
    _ensure_internships(repo, students, templates, courses)
    _ensure_free_ue_validations(repo, students, templates, courses)

    n_stu = len(students)
    n_tpl = len(templates)
    n_crs = len(courses)
    tracks = ", ".join(sorted({f"{s['level']} {s['track']}" for s in students.values()}))
    tpl_lines = []
    for key in sorted(templates):
        t = templates[key]
        reused = "" if _is_demo_owned_template(t) else " (maquette existante réutilisée)"
        tpl_lines.append(f"  – {t.get('name')} [{t.get('academic_year')}]{reused}")
    tpl_detail = "\n".join(tpl_lines) if tpl_lines else "  – (aucune)"
    return (
        f"Données de démo prêtes (millésime {demo_year}).\n\n"
        f"• {n_stu} étudiants DEMO — parcours : {tracks}\n"
        f"• {n_tpl} maquettes liées :\n{tpl_detail}\n"
        f"• {n_crs} UE catalogue DEMO (complément si besoin)\n"
        f"• Notes sur toutes les UE gradables des maquettes (S1/S2, DEF, ABJ…)\n"
        f"• Jury : compositions, délibérations S1/S2, membres\n"
        f"• Stages : dossier complet + convention papier\n"
        f"• Contrats péd. : la plupart OK, 1 étudiant en alerte\n\n"
        "Inscription étudiant ↔ maquette : onglet Notes → Gérer les inscriptions…\n"
        "Recliquez sur l'action pour compléter les éléments manquants."
    )


def _tpl_name(level: str, track: str, academic_year: str) -> str:
    return f"DEMO {academic_year} — {level} {track}"


def _find_template(repo: Repository, level: str, track: str, academic_year: str) -> dict[str, Any] | None:
    """
    Maquette existante pour (niveau, parcours).
    Priorité : millésime cible, nom non-DEMO, puis autre millésime.
    """
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    target_ay = (academic_year or "").strip()
    matches = [
        t
        for t in repo.list_templates()
        if str(t.get("level") or "").strip().upper() == lv
        and str(t.get("track") or "").strip().upper() == tr
    ]
    if not matches:
        return None

    def _priority(t: dict[str, Any]) -> tuple[int, int, str]:
        ay = str(t.get("academic_year") or "").strip()
        is_demo_name = str(t.get("name") or "").startswith("DEMO")
        year_pri = 2 if ay == target_ay else (1 if ay else 0)
        name_pri = 0 if is_demo_name else 1
        return (year_pri, name_pri, ay)

    return max(matches, key=_priority)


def _is_demo_owned_template(tpl: dict[str, Any]) -> bool:
    return str(tpl.get("name") or "").startswith("DEMO")


def _gradable_template_courses(repo: Repository, template_id: int) -> list[dict[str, Any]]:
    """UE de la maquette utilisables pour des notes (hors stage, UE libre, conteneurs vides)."""
    out: list[dict[str, Any]] = []
    for row in repo.list_template_courses(int(template_id)):
        if int(row.get("free_ue") or 0):
            continue
        cid = int(row["course_id"])
        full = repo.get_course(cid) or {}
        if str(full.get("course_type") or "").strip().lower() == "internship":
            continue
        code = str(full.get("code") or row.get("code") or "").strip().upper()
        if code.startswith("CU") or code.startswith("BC"):
            continue
        if float(full.get("ects") or row.get("ects") or 0) <= 0 and not str(
            full.get("name") or row.get("name") or ""
        ).strip():
            continue
        out.append({**row, **full, "course_id": cid})
    return out


def _internship_course_in_template(repo: Repository, template_id: int) -> dict[str, Any] | None:
    for row in repo.list_template_courses(int(template_id)):
        full = repo.get_course(int(row["course_id"])) or {}
        if str(full.get("course_type") or "").strip().lower() == "internship":
            return {**row, **full, "course_id": int(row["course_id"])}
        name = str(full.get("name") or row.get("name") or "").lower()
        if "internship" in name or "stage" in name:
            return {**row, **full, "course_id": int(row["course_id"])}
    return None


def _free_ue_in_template(repo: Repository, template_id: int) -> dict[str, Any] | None:
    for row in repo.list_template_courses(int(template_id)):
        if int(row.get("free_ue") or 0):
            return {**row, "course_id": int(row["course_id"])}
    return None


def _ensure_demo_courses(repo: Repository) -> dict[str, dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "code": "DEMO-NE101",
            "name": "Nuclear Physics",
            "ects": 6.0,
            "teacher_last_name": "Bernard",
            "teacher_first_name": "Claire",
            "teacher_email": "claire.bernard@universite-paris-saclay.fr",
            "teacher_institution": "Université Paris-Saclay",
            "semester": "S1",
            "mne_module_code": "MNE-PHY",
        },
        {"code": "DEMO-NE102", "name": "Reactor Physics", "ects": 6.0, "semester": "S1"},
        {"code": "DEMO-NE201", "name": "Thermal-hydraulics", "ects": 3.0, "semester": "S2"},
        {
            "code": "DEMO-NE202",
            "name": "Technical English",
            "ects": 0.0,
            "semester": "S2",
            "description": "UE optionnelle (hors moyennes bloc/année)",
        },
        {
            "code": "DEMO-NE301",
            "name": "Seminar (UE libre)",
            "ects": 3.0,
            "semester": "S2",
            "description": "UE libre — validation ECTS sans note",
        },
        {
            "code": "DEMO-STAGE",
            "name": "Research / Engineering Internship",
            "ects": 30.0,
            "course_type": "internship",
            "semester": "S2",
            "carrier_partner": "CEA/INSTN",
        },
    ]
    out: dict[str, dict[str, Any]] = {}
    for sp in specs:
        code = str(sp["code"])
        row = repo.get_course_by_code(code)
        if row is None:
            extras = {
                k: v
                for k, v in sp.items()
                if k not in ("code", "name", "ects", "description")
            }
            repo.add_course(
                code,
                str(sp["name"]),
                float(sp.get("ects", 0) or 0),
                str(sp.get("description") or ""),
                **extras,
            )
            row = repo.get_course_by_code(code)
        out[code] = dict(row)
    return out


def _ensure_demo_templates(
    repo: Repository, courses: dict[str, dict[str, Any]], academic_year: str
) -> dict[str, dict[str, Any]]:
    c1, c2, c3, c4, c5, c6 = (
        courses["DEMO-NE101"],
        courses["DEMO-NE102"],
        courses["DEMO-NE201"],
        courses["DEMO-NE202"],
        courses["DEMO-NE301"],
        courses["DEMO-STAGE"],
    )
    tracks_m1 = ("P", "C")
    tracks_m2 = ("NPD", "NPO", "DWM", "NFC", "NRPE")
    out: dict[str, dict[str, Any]] = {}

    for tr in tracks_m1:
        key = f"M1:{tr}"
        tpl = _find_template(repo, "M1", tr, academic_year)
        if tpl is None:
            repo.add_template(_tpl_name("M1", tr, academic_year), "M1", tr, academic_year, "demo")
            tpl = _find_template(repo, "M1", tr, academic_year)
        tid = int(tpl["id"])
        if _is_demo_owned_template(tpl) or not repo.list_template_courses(tid):
            repo.add_course_to_template(tid, int(c1["id"]), "Bloc 1", 1.0, 1)
            repo.add_course_to_template(tid, int(c2["id"]), "Bloc 1", 1.0, 2)
            repo.add_course_to_template(tid, int(c3["id"]), "Bloc 2", 1.0, 3)
            repo.add_course_to_template(tid, int(c4["id"]), "Bloc 3", 1.0, 4, optional=1)
        out[key] = tpl

    for tr in tracks_m2:
        key = f"M2:{tr}"
        tpl = _find_template(repo, "M2", tr, academic_year)
        if tpl is None:
            repo.add_template(_tpl_name("M2", tr, academic_year), "M2", tr, academic_year, "demo")
            tpl = _find_template(repo, "M2", tr, academic_year)
        tid = int(tpl["id"])
        if _is_demo_owned_template(tpl) or not repo.list_template_courses(tid):
            repo.add_course_to_template(tid, int(c1["id"]), "Bloc 1", 1.0, 1)
            repo.add_course_to_template(tid, int(c2["id"]), "Bloc 1", 1.0, 2)
            repo.add_course_to_template(tid, int(c3["id"]), "Bloc 2", 1.0, 3)
            repo.add_course_to_template(tid, int(c4["id"]), "Bloc 3", 1.0, 4, optional=1)
            repo.add_course_to_template(tid, int(c6["id"]), "Stage", 1.0, 5)
            if tr == "NFC":
                repo.add_course_to_template(tid, int(c5["id"]), "Bloc 4", 1.0, 6, free_ue=1)
        out[key] = tpl

    return out


def _ensure_demo_students(repo: Repository, academic_year: str) -> dict[str, dict[str, Any]]:
    """Clé interne → fiche étudiant (id, level, track, scenario)."""
    specs: list[dict[str, Any]] = [
        {
            "key": "m1p_alice",
            "sn": "DEMO-M1P-01",
            "ine": "18011110001",
            "apogee": "EN00010001",
            "last_name": "Durand",
            "first_name": "Alice",
            "level": "M1",
            "track": "P",
            "gender": "F",
            "nationality": "France",
            "birth_place": "Lyon",
            "birth_date": "2002-05-14",
            "origin_institution": "Université Claude Bernard Lyon 1",
            "origin_institution_country": "France",
            "enrollment_institution": "Université Paris-Saclay",
            "application_platform": "MonMaster",
            "accommodations": "tiers_temps",
            "notes": "Démo : bonnes notes, S2 partielle, note manquante bloc 2",
            "contract_paper": True,
            "scenario": "alice",
        },
        {
            "key": "m1c_benoit",
            "sn": "DEMO-M1C-01",
            "ine": "18011110002",
            "apogee": "EN00010002",
            "last_name": "Martin",
            "first_name": "Benoît",
            "level": "M1",
            "track": "C",
            "gender": "M",
            "nationality": "Belgique",
            "birth_place": "Bruxelles",
            "birth_date": "2001-11-03",
            "origin_institution": "Université libre de Bruxelles",
            "origin_institution_country": "Belgique",
            "enrollment_institution": "Institut Polytechnique de Paris",
            "application_platform": "IPParis",
            "accommodations": "salle_isolee",
            "notes": "Démo : DEF, ABJ, envoi 2ᵉ session",
            "contract_paper": True,
            "scenario": "benoit",
        },
        {
            "key": "m1p_charlie",
            "sn": "DEMO-M1P-02",
            "ine": "18011110003",
            "apogee": "EN00010003",
            "last_name": "Nguyen",
            "first_name": "Linh",
            "level": "M1",
            "track": "P",
            "gender": "F",
            "nationality": "Vietnam",
            "birth_place": "Hanoï",
            "birth_date": "2003-01-20",
            "origin_institution": "HUST",
            "origin_institution_country": "Vietnam",
            "enrollment_institution": "ENSTA Paris",
            "application_platform": "Inception",
            "notes": "Démo : notes moyennes, contrat manquant",
            "contract_paper": False,
            "scenario": "average",
        },
        {
            "key": "m2npd_diana",
            "sn": "DEMO-M2NPD-01",
            "ine": "19022220001",
            "apogee": "EN00020001",
            "last_name": "Petit",
            "first_name": "Diana",
            "level": "M2",
            "track": "NPD",
            "gender": "F",
            "nationality": "France",
            "birth_place": "Toulouse",
            "birth_date": "2000-08-09",
            "origin_institution": "INSA Toulouse",
            "origin_institution_country": "France",
            "enrollment_institution": "Université Paris-Saclay",
            "notes": "Démo : M2 complet, stage renseigné",
            "contract_paper": True,
            "scenario": "strong_m2",
        },
        {
            "key": "m2npo_eric",
            "sn": "DEMO-M2NPO-01",
            "ine": "19022220002",
            "apogee": "EN00020002",
            "last_name": "Schmidt",
            "first_name": "Eric",
            "level": "M2",
            "track": "NPO",
            "gender": "M",
            "nationality": "Allemagne",
            "birth_place": "Munich",
            "birth_date": "1999-12-02",
            "origin_institution": "TU Munich",
            "origin_institution_country": "Allemagne",
            "enrollment_institution": "Institut Polytechnique de Paris",
            "notes": "Démo : envoyé en 2ᵉ session sur une UE",
            "contract_paper": True,
            "scenario": "s2_m2",
        },
        {
            "key": "m2dwm_fatima",
            "sn": "DEMO-M2DWM-01",
            "ine": "19022220003",
            "apogee": "EN00020003",
            "last_name": "El Amrani",
            "first_name": "Fatima",
            "level": "M2",
            "track": "DWM",
            "gender": "F",
            "nationality": "Maroc",
            "birth_place": "Rabat",
            "birth_date": "2000-03-17",
            "origin_institution": "Université Mohammed V",
            "origin_institution_country": "Maroc",
            "enrollment_institution": "Chimie Paris PSL",
            "notes": "Démo : points jury année",
            "contract_paper": True,
            "scenario": "jury_year",
        },
        {
            "key": "m2nfc_guillaume",
            "sn": "DEMO-M2NFC-01",
            "ine": "19022220004",
            "apogee": "EN00020004",
            "last_name": "Roux",
            "first_name": "Guillaume",
            "level": "M2",
            "track": "NFC",
            "gender": "M",
            "nationality": "France",
            "birth_place": "Nantes",
            "birth_date": "2000-06-25",
            "origin_institution": "IMT Atlantique",
            "origin_institution_country": "France",
            "enrollment_institution": "Université Paris-Saclay",
            "notes": "Démo : UE libre validée (ECTS)",
            "contract_paper": True,
            "scenario": "free_ue",
        },
        {
            "key": "m2nrpe_hana",
            "sn": "DEMO-M2NRPE-01",
            "ine": "19022220005",
            "apogee": "EN00020005",
            "last_name": "Ivanova",
            "first_name": "Hana",
            "level": "M2",
            "track": "NRPE",
            "gender": "F",
            "nationality": "Bulgarie",
            "birth_place": "Sofia",
            "birth_date": "1999-10-11",
            "origin_institution": "Sofia University",
            "origin_institution_country": "Bulgarie",
            "enrollment_institution": "ENSTA Paris",
            "notes": "Démo : notes faibles bloc 2, sans contrat péd.",
            "contract_paper": False,
            "scenario": "weak_m2",
        },
    ]

    out: dict[str, dict[str, Any]] = {}
    for sp in specs:
        sn = str(sp["sn"])
        existing = repo.get_student_by_number(sn)
        if existing is None:
            inst = str(sp.get("enrollment_institution") or "")
            email_inst = suggest_institutional_email(
                str(sp["first_name"]), str(sp["last_name"]), inst
            )
            sid = repo.add_student(
                sn,
                str(sp["ine"]),
                str(sp["apogee"]),
                str(sp["last_name"]),
                str(sp["first_name"]),
                f"{sp['first_name'].lower()}@example.org",
                email_inst,
                inst,
                str(sp.get("application_platform") or ""),
                str(sp.get("accommodations") or ""),
                "",
                str(sp.get("notes") or ""),
                str(sp["level"]),
                str(sp["track"]),
                academic_year,
                birth_date=str(sp.get("birth_date") or ""),
                nationality=str(sp.get("nationality") or ""),
                birth_place=str(sp.get("birth_place") or ""),
                gender=str(sp.get("gender") or ""),
                origin_institution=str(sp.get("origin_institution") or ""),
                origin_institution_country=str(sp.get("origin_institution_country") or ""),
            )
            existing = repo.get_student(int(sid))
        else:
            cur_ay = str(existing.get("academic_year") or "").strip()
            if cur_ay != academic_year:
                repo.set_student_academic_year(int(existing["id"]), academic_year)
                existing = repo.get_student(int(existing["id"]))
        if sp.get("contract_paper"):
            repo.set_pedagogical_contract_paper(int(existing["id"]), True)
        rec = dict(existing)
        rec["scenario"] = sp.get("scenario")
        rec["_key"] = sp["key"]
        out[sp["key"]] = rec
    return out


def _ensure_enrollments(
    repo: Repository,
    students: dict[str, dict[str, Any]],
    templates: dict[str, dict[str, Any]],
) -> None:
    for st in students.values():
        lv = str(st.get("level") or "").upper()
        tr = str(st.get("track") or "").upper()
        tpl = templates.get(f"{lv}:{tr}")
        if tpl:
            repo.enroll_student(int(st["id"]), int(tpl["id"]))


def _ensure_assessments_for_course(repo: Repository, course_id: int, *, pattern: str) -> list[dict[str, Any]]:
    """Crée des modalités par défaut si l'UE n'en a pas encore."""
    cid = int(course_id)
    existing = repo.list_assessments(cid)
    if existing:
        return existing

    def add(name: str, kind: str, coef: float, session: int, order: int) -> None:
        repo.add_assessment(cid, name, kind, coef, session, order)

    if pattern == "full":
        add("CC (40%)", "CC", 0.4, 1, 1)
        add("Exam (60%)", "CT", 0.6, 1, 2)
        add("CC Rep (40%)", "CC", 0.4, 2, 3)
        add("Exam (60%)", "CT", 0.6, 2, 4)
    elif pattern == "project":
        add("Project (50%)", "PROJET", 0.5, 1, 1)
        add("Final (50%)", "CT", 0.5, 1, 2)
    elif pattern == "lab":
        add("Lab (30%)", "CCTP", 0.3, 1, 1)
        add("Final (70%)", "CT", 0.7, 1, 2)
    else:
        add("Exam (100%)", "CT", 1.0, 1, 1)
    return repo.list_assessments(cid)


def _assessment_pattern_for_index(index: int) -> str:
    if index == 0:
        return "full"
    if index == 1:
        return "project"
    if index == 2:
        return "lab"
    return "exam"


def _course_has_grades(repo: Repository, student_id: int, course_id: int) -> bool:
    for row in repo.get_grades_for_student_course(int(student_id), int(course_id)):
        if row.get("grade") is not None or str(row.get("status") or "") not in ("", "OK"):
            return True
    return False


def _numeric_demo_grade(scenario: str, course_index: int, assess_index: int) -> float:
    """Note numérique déterministe, légèrement variée selon l'UE et la modalité."""
    bases = {
        "alice": 14.2,
        "benoit": 10.0,
        "average": 10.5,
        "strong_m2": 14.8,
        "s2_m2": 11.2,
        "jury_year": 12.8,
        "free_ue": 13.2,
        "weak_m2": 7.8,
    }
    base = bases.get(scenario, 12.0)
    wobble = ((course_index * 5 + assess_index * 3) % 11) * 0.25 - 0.5
    val = base + wobble
    if scenario in ("alice", "strong_m2", "free_ue"):
        val = max(11.5, min(18.0, val))
    elif scenario == "weak_m2":
        val = max(5.0, min(9.5, val))
    elif scenario == "benoit":
        val = max(8.0, min(12.5, val))
    elif scenario == "s2_m2" and course_index == 1:
        val = max(7.0, min(10.0, val - 2.0))
    else:
        val = max(6.0, min(16.0, val))
    return round(val, 1)


def _demo_assessment_grade(
    scenario: str,
    course_index: int,
    assess_index: int,
    assess: dict[str, Any],
) -> tuple[float | None, str] | None:
    """Retourne (note, statut) ou None si la modalité reste volontairement vide."""
    session = int(assess.get("session") or 1)
    kind = str(assess.get("kind") or "").upper()
    name = str(assess.get("name") or "").lower()

    if scenario == "benoit" and course_index == 0 and session == 2 and kind == "CT":
        return None, "DEF"
    if scenario == "benoit" and course_index == 1 and assess_index == 1:
        return None, "ABJ"
    if scenario == "alice" and course_index == 0 and session == 2 and "cc" in name and "rep" in name:
        return None
    return _numeric_demo_grade(scenario, course_index, assess_index), "OK"


def _seed_course_grades(
    repo: Repository,
    student_id: int,
    course_id: int,
    course_index: int,
    scenario: str,
) -> None:
    if _course_has_grades(repo, student_id, course_id):
        return
    pattern = _assessment_pattern_for_index(course_index)
    assessments = _ensure_assessments_for_course(repo, course_id, pattern=pattern)
    for j, assess in enumerate(assessments):
        cell = _demo_assessment_grade(scenario, course_index, j, assess)
        if cell is None:
            continue
        grade, status = cell
        repo.upsert_grade(student_id, int(assess["id"]), grade, status=status)


def _apply_scenario_extras(
    repo: Repository,
    student_id: int,
    template_id: int,
    courses: list[dict[str, Any]],
    scenario: str,
) -> None:
    if not courses:
        return
    if scenario == "alice" and len(courses) >= 2:
        repo.upsert_jury_adjustment(
            student_id, template_id, "course", course_id=int(courses[1]["course_id"]), points=0.5
        )
    if scenario == "benoit":
        repo.set_second_session_decision(
            student_id, template_id, int(courses[0]["course_id"]), sent=True, comment="Démo DEF S1"
        )
        block2 = str(courses[min(2, len(courses) - 1)].get("block_name") or "Bloc 2")
        repo.upsert_jury_adjustment(
            student_id, template_id, "block", block_name=block2, points=0.3
        )
    if scenario == "s2_m2" and len(courses) >= 2:
        repo.set_second_session_decision(
            student_id,
            template_id,
            int(courses[1]["course_id"]),
            sent=True,
            comment="Démo M2 S2",
        )
    if scenario == "jury_year":
        repo.upsert_jury_adjustment(student_id, template_id, "year", points=0.25)


def _seed_all_grades(
    repo: Repository,
    students: dict[str, dict[str, Any]],
    courses: dict[str, dict[str, Any]],
    templates: dict[str, dict[str, Any]],
) -> None:
    del courses  # notes sur les UE des maquettes réelles, pas seulement DEMO-*

    for st in students.values():
        scenario = str(st.get("scenario") or "average")
        lv = str(st.get("level") or "").upper()
        tr = str(st.get("track") or "").upper()
        tpl = templates.get(f"{lv}:{tr}")
        if tpl is None:
            continue
        tid = int(tpl["id"])
        gradable = _gradable_template_courses(repo, tid)
        sid = int(st["id"])
        for i, row in enumerate(gradable):
            _seed_course_grades(repo, sid, int(row["course_id"]), i, scenario)
        _apply_scenario_extras(repo, sid, tid, gradable, scenario)


def _ensure_jury_data(
    repo: Repository,
    templates: dict[str, dict[str, Any]],
    students: dict[str, dict[str, Any]],
    courses: dict[str, dict[str, Any]],
) -> None:
    tpl_m1p = templates.get("M1:P")
    if tpl_m1p is None:
        return
    tid = int(tpl_m1p["id"])

    rid = repo.ensure_template_roster(tid)
    if not repo.list_jury_roster_members(rid):
        repo.replace_jury_roster_members(
            rid,
            [
                {
                    "last_name": "Lefèvre",
                    "first_name": "Philippe",
                    "title": "Professeur",
                    "institution": "Université Paris-Saclay",
                },
                {
                    "last_name": "Moreau",
                    "first_name": "Isabelle",
                    "title": "Représentante professionnelle",
                    "institution": "CEA",
                },
                {
                    "last_name": "Chen",
                    "first_name": "Wei",
                    "title": "Étudiant MNE",
                    "institution": "Master MNE",
                },
                {
                    "last_name": "Garcia",
                    "first_name": "Elena",
                    "title": "Invitée",
                    "institution": "ENSTA Paris",
                },
            ],
        )

    sessions = {str(s.get("session_kind")): s for s in repo.list_jury_sessions(tid)}
    if "S1" not in sessions:
        js1 = repo.add_jury_session(
            tid,
            "S1",
            label="Délibération bloc 1 — S1",
            scope_text="Bloc 1 — 1ʳᵉ session",
            roster_id=rid,
        )
    else:
        js1 = int(sessions["S1"]["id"])
        if not sessions["S1"].get("roster_id"):
            repo.update_jury_session(js1, roster_id=rid)

    if "S2" not in sessions:
        repo.add_jury_session(
            tid,
            "S2",
            label="Délibération complémentaire — S2",
            scope_text="Reprises et bloc 2",
            roster_id=rid,
        )

    if "FINAL" not in sessions:
        repo.add_jury_session(
            tid,
            "FINAL",
            label="Jury final année",
            scope_text="Validation année M1 P",
            roster_id=rid,
        )

    tpl_m1c = templates.get("M1:C")
    if tpl_m1c:
        tid_c = int(tpl_m1c["id"])
        rid_c = repo.ensure_template_roster(tid_c)
        if not repo.list_jury_roster_members(rid_c):
            repo.replace_jury_roster_members(
                rid_c,
                [
                    {
                        "last_name": "Dupont",
                        "first_name": "Marie",
                        "title": "Professeure",
                        "institution": "Chimie Paris PSL",
                    },
                    {
                        "last_name": "Lambert",
                        "first_name": "Jean",
                        "title": "Maître de conférences",
                        "institution": "Université Paris-Saclay",
                    },
                    {
                        "last_name": "Rossi",
                        "first_name": "Luca",
                        "title": "Représentant professionnel",
                        "institution": "Orano",
                    },
                ],
            )
        sessions_c = {str(s.get("session_kind")): s for s in repo.list_jury_sessions(tid_c)}
        if "S1" not in sessions_c:
            repo.add_jury_session(
                tid_c,
                "S1",
                label="Délibération bloc 1 — S1",
                scope_text="Bloc 1 — 1ʳᵉ session",
                roster_id=rid_c,
            )

    # Composition M2 pour tester import / PV sur autre maquette
    tpl_m2 = templates.get("M2:NPD")
    if tpl_m2:
        tid2 = int(tpl_m2["id"])
        rid2 = repo.ensure_template_roster(tid2)
        if not repo.list_jury_roster_members(rid2):
            repo.replace_jury_roster_members(
                rid2,
                [
                    {
                        "last_name": "Blanc",
                        "first_name": "Henri",
                        "title": "Président",
                        "institution": "IP Paris",
                    },
                    {
                        "last_name": "Keller",
                        "first_name": "Anna",
                        "title": "Rapporteur",
                        "institution": "CEA/INSTN",
                    },
                ],
            )


def _ensure_master_team(repo: Repository, academic_year: str) -> None:
    from ..core.master_team import ROLE_SECRETARIAT, encode_tracks_scope

    ay = (academic_year or "").strip()
    if not ay:
        return
    for slot, ln, fn, title, inst, email in (
        (
            0,
            "Lebois",
            "Matthieu",
            "Maître de conférences",
            "Université Paris-Saclay",
            "matthieu.lebois@universite-paris-saclay.fr",
        ),
        (
            1,
            "Bodineau",
            "Jean-Christophe",
            "Professeur",
            "CEA / INSTN",
            "jean-christophe.bodineau@instn.fr",
        ),
        (
            2,
            "Dalmazzone",
            "Didier",
            "Professeur",
            "ENSTA Paris",
            "didier.dalmazzone@ensta.fr",
        ),
    ):
        directors = repo.list_mention_directors(ay)
        if not directors[slot].get("id"):
            repo.upsert_mention_director(
                ay,
                slot,
                last_name=ln,
                first_name=fn,
                title=title,
                institution=inst,
                email=email,
            )
    for lv, tr, ln, fn, email in (
        ("M1", "P", "Martin", "Paul", "paul.martin@ip-paris.fr"),
        ("M1", "C", "Bernard", "Claire", "claire.bernard@chimieparis.psl.eu"),
        ("M2", "NPD", "Petit", "Luc", "luc.petit@ensta.fr"),
    ):
        if not any(
            str(r.get("level") or "").upper() == lv and str(r.get("track") or "").upper() == tr
            for r in repo.list_master_team_members(ay, role_kind="track")
        ):
            repo.upsert_track_director(
                ay,
                lv,
                tr,
                last_name=ln,
                first_name=fn,
                title=f"Responsable parcours {lv} {tr}",
                email=email,
            )
    if not repo.list_master_team_members(ay, role_kind=ROLE_SECRETARIAT):
        repo.add_master_team_member(
            ay,
            ROLE_SECRETARIAT,
            institution="Université Paris-Saclay",
            tracks_scope=encode_tracks_scope([("M1", "P"), ("M1", "C")]),
            last_name="Leroy",
            first_name="Sophie",
            title="Secrétariat pédagogique",
            email="mne-secretariat@universite-paris-saclay.fr",
        )
        repo.add_master_team_member(
            ay,
            ROLE_SECRETARIAT,
            institution="Institut Polytechnique de Paris",
            tracks_scope=encode_tracks_scope([("M2", "NPD"), ("M2", "NPO")]),
            last_name="Moreau",
            first_name="Julien",
            title="Secrétariat pédagogique",
            email="mne@ip-paris.fr",
        )


def _ensure_internships(
    repo: Repository,
    students: dict[str, dict[str, Any]],
    templates: dict[str, dict[str, Any]],
    courses: dict[str, dict[str, Any]],
) -> None:
    st = students.get("m2npd_diana")
    tpl = templates.get("M2:NPD")
    if not st or not tpl:
        return
    stage = _internship_course_in_template(repo, int(tpl["id"]))
    if stage is None:
        stage = courses.get("DEMO-STAGE")
    if not stage:
        return
    sid, tid, cid = int(st["id"]), int(tpl["id"]), int(stage["course_id"])
    if repo.get_internship_record(sid, tid, cid):
        return
    repo.upsert_internship_record(
        sid,
        tid,
        cid,
        topic="Modélisation thermo-hydraulique d'un réacteur de recherche",
        supervisor_last_name="Lambert",
        supervisor_first_name="Jean",
        supervisor_email="jean.lambert@cea.fr",
        supervisor_institution="CEA Saclay",
        follow_up_status="convention_signed",
        convention_paper=True,
        reporter_last_name="Simon",
        reporter_first_name="Nathalie",
        reporter_institution="Université Paris-Saclay",
        defense_date="2026-06-15",
        defense_time="14:00",
        notes="Démo : soutenance planifiée",
    )


def _ensure_free_ue_validations(
    repo: Repository,
    students: dict[str, dict[str, Any]],
    templates: dict[str, dict[str, Any]],
    courses: dict[str, dict[str, Any]],
) -> None:
    st = students.get("m2nfc_guillaume")
    tpl = templates.get("M2:NFC")
    if not st or not tpl:
        return
    free = _free_ue_in_template(repo, int(tpl["id"]))
    if free is None:
        free = courses.get("DEMO-NE301")
    if not free:
        return
    sid, tid, cid = int(st["id"]), int(tpl["id"]), int(free["course_id"])
    if not repo.has_ue_ects_validation(sid, tid, cid):
        repo.set_ue_ects_validation(
            sid, tid, cid, validated=True, comment="Démo : séminaire validé sans note"
        )
