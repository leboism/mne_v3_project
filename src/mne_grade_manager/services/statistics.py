"""
Agrégations pour tableaux de bord et exports (évolutif).

Indicateurs prévus / partiellement couverts :

| Catégorie | Exemples | Statut |
|-----------|----------|--------|
| Effectifs | par année, niveau, parcours, maquette | implémenté |
| Diversité | nationalités, pays d'origine, établissements d'origine | implémenté |
| Réussite | moyenne année ≥ 10, blocs validés (règles onglet Résultats) | implémenté |
| Stages | taux convention signée, stages trouvés | à brancher (données ``internship_records``) |
| Parcours | passage M1→M2, redoublements | à définir (historique multi-années) |
| Notes | distribution DEF/ABJ, moyennes par UE | extension future |

Les taux de réussite réutilisent ``Repository.get_student_result_summary`` et
``block_is_validated`` pour rester cohérents avec l'affichage pédagogique.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository

from .lookups import gender_label_fr

_YEAR_PASS_THRESHOLD = 10.0

# Codes genre en base (M, F, O, '') — ordre d'affichage inclusif.
STATISTICS_GENDER_CHOICES: tuple[tuple[str, str], ...] = (
    ("M", "Homme"),
    ("F", "Femme"),
    ("O", "Autre"),
    ("", "(non renseigné)"),
)
ALL_STATISTICS_GENDER_CODES: frozenset[str] = frozenset(code for code, _ in STATISTICS_GENDER_CHOICES)


@dataclass
class StatisticsCriteria:
    """Périmètre et indicateurs pour un rapport statistique."""

    academic_years: list[str] = field(default_factory=list)
    level: str = ""
    track: str = ""
    genders: list[str] | None = None
    include_by_academic_year: bool = True
    include_by_level: bool = True
    include_by_track: bool = True
    include_by_gender: bool = True
    include_nationality: bool = True
    include_origin_country: bool = True
    include_origin_institution: bool = True
    include_enrollment_institution: bool = True
    include_success: bool = True
    include_internship: bool = True
    template_ids: list[int] = field(default_factory=list)
    view_session: str = "s1"

    def summary_label(self) -> str:
        years = ", ".join(self.academic_years) if self.academic_years else "tous millésimes"
        lv = self.level or "tous niveaux"
        tr = self.track or "tous parcours"
        gen = gender_filter_summary(self.genders)
        return f"Millésime(s) : {years} — {lv}, {tr} — {gen}"


def normalized_gender_code(value: Any) -> str:
    g = str(value or "").strip().upper()
    if g in {"M", "F", "O"}:
        return g
    return ""


def gender_stats_label(code: str) -> str:
    label = gender_label_fr(code)
    return label if label else "(non renseigné)"


def gender_filter_summary(genders: list[str] | None) -> str:
    if not genders or set(genders) >= ALL_STATISTICS_GENDER_CODES:
        return "tous genres"
    labels = [gender_stats_label(g) for g in genders]
    return "genres : " + ", ".join(labels)


def student_matches_genders(student: dict[str, Any], genders: list[str] | None) -> bool:
    if not genders or set(genders) >= ALL_STATISTICS_GENDER_CODES:
        return True
    allowed = {normalized_gender_code(g) for g in genders}
    return normalized_gender_code(student.get("gender")) in allowed


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
    academic_years: list[str] | None = None,
    level: str = "",
    track: str = "",
    genders: list[str] | None = None,
) -> list[dict[str, Any]]:
    years_filter: set[str] | None = None
    if academic_years is not None:
        normalized = [str(y).strip() for y in academic_years if str(y).strip()]
        if normalized:
            years_filter = set(normalized)
    elif str(academic_year or "").strip():
        years_filter = {str(academic_year).strip()}

    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    out: list[dict[str, Any]] = []
    for s in repo.list_students(include_withdrawn=False):
        if years_filter is not None:
            if str(s.get("academic_year") or "").strip() not in years_filter:
                continue
        if lv and str(s.get("level") or "").strip().upper() != lv:
            continue
        if tr and str(s.get("track") or "").strip().upper() != tr:
            continue
        if not student_matches_genders(s, genders):
            continue
        out.append(s)
    return out


def enrollment_overview(
    repo: Repository,
    *,
    academic_year: str = "",
    academic_years: list[str] | None = None,
    level: str = "",
    track: str = "",
    genders: list[str] | None = None,
) -> dict[str, Any]:
    """Effectifs et répartitions sur la population filtrée."""
    students = filter_students(
        repo,
        academic_year=academic_year,
        academic_years=academic_years,
        level=level,
        track=track,
        genders=genders,
    )
    by_level = Counter(_norm_label(s.get("level")) for s in students)
    by_track = Counter(_norm_label(s.get("track")) for s in students)
    by_year = Counter(_norm_label(s.get("academic_year")) for s in students)
    by_nationality = Counter(_norm_label(s.get("nationality")) for s in students)
    by_origin_country = Counter(_norm_label(s.get("origin_institution_country")) for s in students)
    by_origin_inst = Counter(_norm_label(s.get("origin_institution")) for s in students)
    by_enrollment = Counter(_norm_label(s.get("enrollment_institution")) for s in students)
    by_gender = Counter(gender_stats_label(normalized_gender_code(s.get("gender"))) for s in students)
    by_gender_ordered = {
        label: by_gender[label]
        for _, label in STATISTICS_GENDER_CHOICES
        if by_gender.get(label)
    }
    for label, count in sorted(by_gender.items()):
        if label not in by_gender_ordered:
            by_gender_ordered[label] = count

    return {
        "total": len(students),
        "filters": {
            "academic_year": academic_year or "",
            "academic_years": list(academic_years or []),
            "level": level or "",
            "track": track or "",
            "genders": list(genders) if genders else [],
        },
        "by_academic_year": dict(sorted(by_year.items(), key=lambda x: (-x[1], x[0]))),
        "by_level": dict(sorted(by_level.items(), key=lambda x: (-x[1], x[0]))),
        "by_track": dict(sorted(by_track.items(), key=lambda x: (-x[1], x[0]))),
        "by_gender": by_gender_ordered,
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
    genders: list[str] | None = None,
) -> dict[str, Any]:
    """
    Taux de réussite pour une maquette (même logique que l'onglet Résultats).

    - **Moyenne année > seuil** (défaut 10) parmi les inscrits avec moyenne calculable
    - **Bloc validé** : moyenne bloc ≥ 10 et aucune note < 7 non « Garder »
    """
    tid = int(template_id)
    tpl = repo.get_template(tid) or {}
    vs = str(view_session or "s1").strip().lower()
    if vs not in {"s1", "s2"}:
        vs = "s1"

    rows = repo.get_student_result_summary(tid, view_session=vs)
    if genders and set(genders) < ALL_STATISTICS_GENDER_CODES:
        students_by_id = {int(s["id"]): s for s in repo.list_students_for_template(tid)}
        rows = [
            r
            for r in rows
            if student_matches_genders(students_by_id.get(int(r["student_id"]), {}), genders)
        ]
    blocks = repo.list_template_blocks_with_courses(tid)
    block_names = [bk for bk, _ in blocks if (bk or "").strip() and bk != "(no block)"]

    enrolled = len(rows)
    with_year_avg = sum(1 for r in rows if r.get("global_average") is not None)
    year_pass = sum(
        1
        for r in rows
        if r.get("global_average") is not None and float(r["global_average"]) >= year_threshold
    )
    with_jury = sum(1 for r in rows if r.get("global_with_jury") is not None)
    year_pass_jury = sum(
        1
        for r in rows
        if r.get("global_with_jury") is not None and float(r["global_with_jury"]) >= year_threshold
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
    *,
    genders: list[str] | None = None,
) -> dict[str, Any]:
    """Suivi stages pour les UE marquées « internship » dans une maquette."""
    tid = int(template_id)
    students = [
        s
        for s in repo.list_students_for_template(tid)
        if student_matches_genders(s, genders)
    ]
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
