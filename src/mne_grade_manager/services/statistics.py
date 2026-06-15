"""
Agrégations pour tableaux de bord et exports (évolutif).

Indicateurs prévus / partiellement couverts :

| Catégorie | Exemples | Statut |
|-----------|----------|--------|
| Effectifs | par année, niveau, parcours, maquette | implémenté |
| Diversité | nationalités, pays d'origine, établissements d'origine | implémenté |
| Réussite | moyenne année > 10, blocs validés (règles onglet Résultats) | implémenté |
| Stages | taux convention signée, stages trouvés | à brancher (données ``internship_records``) |
| Parcours | passage M1→M2, redoublements | à définir (historique multi-années) |
| Notes | distribution DEF/ABJ, moyennes par UE | extension future |

Les taux de réussite réutilisent ``Repository.get_student_result_summary`` et
``block_is_validated`` pour rester cohérents avec l'affichage pédagogique.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository

_YEAR_PASS_THRESHOLD = 10.0


def _norm_label(value: Any, *, empty: str = "(non renseigné)") -> str:
    s = str(value or "").strip()
    return s if s else empty


def _pct(num: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return round(100.0 * num / denom, 1)


def filter_students(
    repo: Repository,
    *,
    academic_year: str = "",
    level: str = "",
    track: str = "",
) -> list[dict[str, Any]]:
    ay = str(academic_year or "").strip()
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    out: list[dict[str, Any]] = []
    for s in repo.list_students():
        if ay and str(s.get("academic_year") or "").strip() != ay:
            continue
        if lv and str(s.get("level") or "").strip().upper() != lv:
            continue
        if tr and str(s.get("track") or "").strip().upper() != tr:
            continue
        out.append(s)
    return out


def enrollment_overview(
    repo: Repository,
    *,
    academic_year: str = "",
    level: str = "",
    track: str = "",
) -> dict[str, Any]:
    """Effectifs et répartitions sur la population filtrée."""
    students = filter_students(repo, academic_year=academic_year, level=level, track=track)
    by_level = Counter(_norm_label(s.get("level")) for s in students)
    by_track = Counter(_norm_label(s.get("track")) for s in students)
    by_year = Counter(_norm_label(s.get("academic_year")) for s in students)
    by_nationality = Counter(_norm_label(s.get("nationality")) for s in students)
    by_origin_country = Counter(_norm_label(s.get("origin_institution_country")) for s in students)
    by_origin_inst = Counter(_norm_label(s.get("origin_institution")) for s in students)
    by_enrollment = Counter(_norm_label(s.get("enrollment_institution")) for s in students)

    return {
        "total": len(students),
        "filters": {
            "academic_year": academic_year or "",
            "level": level or "",
            "track": track or "",
        },
        "by_academic_year": dict(sorted(by_year.items(), key=lambda x: (-x[1], x[0]))),
        "by_level": dict(sorted(by_level.items(), key=lambda x: (-x[1], x[0]))),
        "by_track": dict(sorted(by_track.items(), key=lambda x: (-x[1], x[0]))),
        "by_nationality": dict(sorted(by_nationality.items(), key=lambda x: (-x[1], x[0]))),
        "by_origin_country": dict(sorted(by_origin_country.items(), key=lambda x: (-x[1], x[0]))),
        "by_origin_institution": dict(sorted(by_origin_inst.items(), key=lambda x: (-x[1], x[0]))),
        "by_enrollment_institution": dict(sorted(by_enrollment.items(), key=lambda x: (-x[1], x[0]))),
    }


def template_success_summary(
    repo: Repository,
    template_id: int,
    *,
    view_session: str = "s1",
    year_threshold: float = _YEAR_PASS_THRESHOLD,
) -> dict[str, Any]:
    """
    Taux de réussite pour une maquette (même logique que l'onglet Résultats).

    - **Moyenne année > seuil** (défaut 10) parmi les inscrits avec moyenne calculable
    - **Bloc validé** : moyenne bloc > 10 et aucune note < 7 non « Garder »
    """
    tid = int(template_id)
    tpl = repo.get_template(tid) or {}
    vs = str(view_session or "s1").strip().lower()
    if vs not in {"s1", "s2"}:
        vs = "s1"

    rows = repo.get_student_result_summary(tid, view_session=vs)
    blocks = repo.list_template_blocks_with_courses(tid)
    block_names = [bk for bk, _ in blocks if (bk or "").strip() and bk != "(no block)"]

    enrolled = len(rows)
    with_year_avg = sum(1 for r in rows if r.get("global_average") is not None)
    year_pass = sum(
        1
        for r in rows
        if r.get("global_average") is not None and float(r["global_average"]) > year_threshold
    )
    with_jury = sum(1 for r in rows if r.get("global_with_jury") is not None)
    year_pass_jury = sum(
        1
        for r in rows
        if r.get("global_with_jury") is not None and float(r["global_with_jury"]) > year_threshold
    )

    block_stats: dict[str, dict[str, Any]] = {}
    for bk in block_names:
        validated = 0
        with_avg = 0
        for r in rows:
            sid = int(r["student_id"])
            avg = (r.get("blocks") or {}).get(bk)
            if avg is not None:
                with_avg += 1
            if repo.block_is_validated(sid, tid, bk, view_session=vs, block_average=avg):
                validated += 1
        block_stats[bk] = {
            "with_average": with_avg,
            "validated": validated,
            "validation_rate_pct": _pct(validated, enrolled),
        }

    return {
        "template_id": tid,
        "template_name": tpl.get("name"),
        "academic_year": tpl.get("academic_year"),
        "level": tpl.get("level"),
        "track": tpl.get("track"),
        "view_session": vs,
        "year_threshold": year_threshold,
        "enrolled": enrolled,
        "with_year_average": with_year_avg,
        "year_average_above_threshold": year_pass,
        "year_success_rate_pct": _pct(year_pass, with_year_avg),
        "year_success_rate_on_enrolled_pct": _pct(year_pass, enrolled),
        "with_year_average_including_jury": with_jury,
        "year_with_jury_above_threshold": year_pass_jury,
        "year_jury_success_rate_pct": _pct(year_pass_jury, with_jury),
        "blocks": block_stats,
    }


def internship_follow_up_summary(
    repo: Repository,
    template_id: int,
) -> dict[str, Any]:
    """Suivi stages pour les UE marquées « internship » dans une maquette."""
    tid = int(template_id)
    students = repo.list_students_for_template(tid)
    stage_courses = [
        int(c["course_id"])
        for c in repo.list_template_courses(tid)
        if repo.is_internship_course(int(c["course_id"]))
    ]
    by_status: Counter[str] = Counter()
    total_slots = 0
    for s in students:
        sid = int(s["id"])
        for cid in stage_courses:
            total_slots += 1
            rec = repo.get_internship_record(sid, tid, cid) or {}
            st = str(rec.get("follow_up_status") or "").strip() or "non_renseigne"
            by_status[st] += 1
    status_labels = {
        "non_renseigne": "Non renseigné",
        "searching": "En recherche",
        "found": "Stage trouvé",
        "convention_pending": "Convention en cours",
        "convention_signed": "Convention signée",
    }
    return {
        "template_id": tid,
        "internship_course_count": len(stage_courses),
        "student_count": len(students),
        "follow_up_slots": total_slots,
        "by_status": {status_labels.get(k, k): v for k, v in by_status.items()},
    }


def counter_to_rows(title: str, counter: dict[str, int]) -> list[list[Any]]:
    rows = [[title, "Effectif", "%"]]
    total = sum(counter.values()) or 1
    for label, n in counter.items():
        rows.append([label, n, round(100.0 * n / total, 1)])
    return rows
