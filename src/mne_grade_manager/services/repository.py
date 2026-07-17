from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.database import Database
from .student_numbers import allocate_student_number
from .attachments import (
    delete_stored_file,
    store_internship_convention,
    store_student_document,
    store_student_photo,
)
from .calculations import (
    grade_below_threshold,
    grade_meets_minimum,
    round_grade_mne,
    strict_weighted_average,
    weighted_average,
)
from .grade_status import (
    STATUS_ABJ,
    STATUS_DEF,
    STATUS_NEUT,
    STATUS_OK,
    STATUS_VAL,
    normalize_grade_status,
    status_blocks_validation,
    status_counts_as_zero,
    status_skips_average,
)
from .lookups import normalize_level, normalize_track_acronym, suggest_institutional_email
from .jury_scope import block_key_bloc_number, extract_bloc_numbers, scope_text_to_block_keys
from .student_status import (
    STUDENT_STATUS_ACTIVE,
    STUDENT_STATUS_GRADUATED,
    STUDENT_STATUS_WITHDRAWN,
    is_student_active,
    normalize_student_status,
    sql_student_is_active,
)
from .student_mobility import (
    MOBILITY_ERASMUS,
    MOBILITY_MNE,
    is_erasmus_student,
    normalize_mobility_type,
)


def _template_course_weight(course: dict[str, Any]) -> float:
    """Pondération pour moyennes bloc / année : ECTS si renseigné, sinon `global_coefficient`."""
    e = float(course.get("ects") or 0)
    if e > 0:
        return e
    return float(course.get("global_coefficient") or 0) or 1.0


def _placement_coefficient_for_ects(ects: float, global_coefficient: float | None = None) -> float:
    """Coefficient de maquette aligné sur les ECTS (repli : valeur explicite ou 1)."""
    e = float(ects or 0)
    if e > 0:
        return e
    gc = float(global_coefficient if global_coefficient is not None else 0)
    return gc if gc > 0 else 1.0


def _block_key(course: dict[str, Any]) -> str:
    return (course.get("block_name") or "").strip() or "(no block)"


def _lookup_block_average(block_avgs: dict[str, Any], block_name: str) -> float | None:
    """Retrouve la moyenne de bloc malgré d'éventuelles différences d'espaces / casse."""
    bn = (block_name or "").strip()
    if not bn:
        return None
    if bn in block_avgs:
        v = block_avgs[bn]
        return float(v) if v is not None else None
    for k, v in block_avgs.items():
        if str(k or "").strip() == bn:
            return float(v) if v is not None else None
    return None


def _session_grade_plus_jury(base: float | None, jury_points: float) -> float | None:
    """Note agrégée UE pour blocs / année : moyenne de session + points de jury UE (si base absente, jury seul)."""
    jp = float(jury_points or 0.0)
    if base is not None:
        return float(base) + jp
    if abs(jp) > 1e-12:
        return jp
    return None


def _fmt_validation_num(value: Any) -> str:
    if value is None:
        return "—"
    try:
        rounded = round_grade_mne(float(value))
        return "—" if rounded is None else f"{rounded:.2f}"
    except (TypeError, ValueError):
        return str(value)


def _extract_tag_from_assessment_name(name: str) -> str:
    """2ᵉ mot avant '(' — identifiant de reprise (ex. « EE RapM (40%) » → « RapM »)."""
    base = str(name or "").split("(", 1)[0].strip()
    parts = [p for p in base.split(" ") if p]
    if len(parts) >= 2:
        return parts[1].strip()
    return ""


def _grade_cell_empty(grade: Any, status: Any) -> bool:
    """Case non saisie (reprise S1 peut être recopiée)."""
    return grade is None and normalize_grade_status(status) == STATUS_OK


def _grade_by_kind_session1(session1_rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in session1_rows:
        st = normalize_grade_status(r.get("status"))
        if status_skips_average(st):
            continue
        kind = str(r["kind"])
        if status_counts_as_zero(st):
            out.setdefault(kind, 0.0)
            continue
        if r["grade"] is not None:
            out.setdefault(kind, float(r["grade"]))
    return out


def _grade_by_tag_session1(session1_rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in session1_rows:
        st = normalize_grade_status(r.get("status"))
        if status_skips_average(st):
            continue
        tag = _extract_tag_from_assessment_name(str(r.get("name") or ""))
        if not tag:
            continue
        if status_counts_as_zero(st):
            out.setdefault(tag, 0.0)
            continue
        if r["grade"] is not None:
            out.setdefault(tag, float(r["grade"]))
    return out


def _is_s2_reprise_assessment_row(r: dict[str, Any]) -> bool:
    """Épreuve S2 « reprise » (CC Rep, CCTP Rep, …) — report possible depuis S1."""
    if int(r.get("session") or 0) != 2:
        return False
    name = str(r.get("name") or "").lower()
    kind = str(r.get("kind") or "")
    return "rep" in name or kind in {"CC", "CCTP"}


def _resolve_assessment_grade(
    r: dict[str, Any],
    *,
    fallback_by_kind: dict[str, float] | None,
    fallback_by_tag: dict[str, float] | None,
    allow_s1_reprise_carry: bool = True,
) -> float | None:
    st = normalize_grade_status(r.get("status"))
    if status_skips_average(st):
        return None
    if status_counts_as_zero(st):
        return 0.0
    g = r.get("grade")
    if g is not None:
        return float(g)
    if fallback_by_kind is None or not allow_s1_reprise_carry:
        return None
    name = str(r.get("name") or "")
    kind = str(r["kind"])
    if "rep" in name.lower():
        val = fallback_by_kind.get(kind)
        return float(val) if val is not None else None
    if int(r.get("session") or 0) == 2 and kind in {"CC", "CCTP"}:
        val = fallback_by_kind.get(kind)
        if val is not None:
            return float(val)
    tag = _extract_tag_from_assessment_name(name)
    if tag and fallback_by_tag and tag in fallback_by_tag:
        return float(fallback_by_tag[tag])
    return None


def _compute_course_average_from_rows(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    use_session2: bool | None = None,
    allow_s1_reprise_carry: bool = True,
) -> float | None:
    """
    Moyenne d'UE à partir des lignes ``get_grades_for_student_course``.

    Règles MCC utilisées :
    - **ABJ / NEUT / VAL** : exclus de la moyenne (pas de pondération).
    - **DEF** : compte comme **0** pour la moyenne pondérée de l’UE.

    ``mode`` :
    - ``s1`` : session 1 uniquement (tout sauf ``session == 2``).
    - ``s2`` : barème MCC de session 2 uniquement ; reprise S1 (Rep / CC / CCTP) seulement si
      ``allow_s1_reprise_carry`` (envoi S2 / convocation sur l'UE).
    - ``final`` : si ``use_session2`` : même logique que ``s2``, sinon ``s1``.
    """
    if not rows:
        return None

    session2 = [r for r in rows if int(r["session"]) == 2]
    session1 = [r for r in rows if int(r["session"]) != 2]

    def items_for_session(
        session_rows: list[dict[str, Any]],
        *,
        fallback_by_kind: dict[str, float] | None,
    ) -> list[tuple[float | None, float]]:
        fb = fallback_by_kind or {}
        fb_tag = _grade_by_tag_session1(session1) if fallback_by_kind is not None else {}
        items: list[tuple[float | None, float]] = []
        carry = allow_s1_reprise_carry and fallback_by_kind is not None
        for r in session_rows:
            st = normalize_grade_status(r.get("status"))
            coef = float(r["coefficient"])
            if status_skips_average(st):
                continue
            if status_counts_as_zero(st):
                items.append((0.0, coef))
                continue
            g = _resolve_assessment_grade(
                r,
                fallback_by_kind=fallback_by_kind if carry else None,
                fallback_by_tag=fb_tag if carry else None,
                allow_s1_reprise_carry=carry,
            )
            items.append((g, coef))
        return items

    if mode == "s1":
        if not session1:
            return None
        return strict_weighted_average(items_for_session(session1, fallback_by_kind=None))

    if mode == "s2":
        fb = _grade_by_kind_session1(session1) if allow_s1_reprise_carry else None
        if session2:
            return strict_weighted_average(items_for_session(session2, fallback_by_kind=fb))
        if not session1:
            return None
        return strict_weighted_average(items_for_session(session1, fallback_by_kind=None))

    # mode == "final"
    if use_session2:
        fb = _grade_by_kind_session1(session1) if allow_s1_reprise_carry else None
        if session2:
            return strict_weighted_average(items_for_session(session2, fallback_by_kind=fb))
        if not session1:
            return None
        return strict_weighted_average(items_for_session(session1, fallback_by_kind=None))
    return strict_weighted_average(items_for_session(session1, fallback_by_kind=None))


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # Students
    def list_students(self, *, include_withdrawn: bool = True) -> list[dict[str, Any]]:
        if include_withdrawn:
            rows = self.db.query_all(
                "SELECT * FROM students ORDER BY academic_year DESC, last_name, first_name"
            )
        else:
            rows = self.db.query_all(
                f"""
                SELECT * FROM students
                WHERE {sql_student_is_active("students")}
                ORDER BY academic_year DESC, last_name, first_name
                """
            )
        return [dict(r) for r in rows]

    def set_student_withdrawn(self, student_id: int, withdrawn: bool = True) -> None:
        status = STUDENT_STATUS_WITHDRAWN if withdrawn else STUDENT_STATUS_ACTIVE
        self.db.execute("UPDATE students SET status = ? WHERE id = ?", (status, int(student_id)))

    def set_student_graduated(self, student_id: int, *, graduated: bool = True) -> None:
        status = STUDENT_STATUS_GRADUATED if graduated else STUDENT_STATUS_ACTIVE
        self.db.execute("UPDATE students SET status = ? WHERE id = ?", (status, int(student_id)))

    def mark_students_withdrawn(self, student_ids: list[int]) -> None:
        for sid in student_ids:
            self.set_student_withdrawn(int(sid), True)

    def restore_students_active(self, student_ids: list[int]) -> None:
        for sid in student_ids:
            self.set_student_withdrawn(int(sid), False)

    def get_student(self, student_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM students WHERE id = ?", (student_id,))
        return dict(row) if row else None

    def get_student_by_number(self, student_number: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM students WHERE student_number = ?",
            (str(student_number or "").strip(),),
        )
        return dict(row) if row else None

    def add_student(
        self,
        student_number: str,
        student_number_ine: str,
        student_number_local: str,
        last_name: str,
        first_name: str,
        email_personal: str = "",
        email_institutional: str = "",
        phone: str = "",
        enrollment_institution: str = "",
        application_platform: str = "",
        mon_master_ranking: str = "",
        accommodations: str = "",
        accommodations_other: str = "",
        funding: str = "",
        funding_other: str = "",
        notes: str = "",
        level: str = "",
        track: str = "",
        academic_year: str = "",
        birth_date: str = "",
        nationality: str = "",
        birth_place: str = "",
        gender: str = "",
        origin_institution: str = "",
        origin_institution_country: str = "",
        highest_diploma: str = "",
        photo_path: str = "",
        mobility_type: str = MOBILITY_MNE,
    ) -> int:
        sn = str(student_number or "").strip()
        if not sn:
            sn = allocate_student_number(
                self.db,
                last_name=last_name,
                first_name=first_name,
                birth_date=str(birth_date or ""),
                email_institutional=str(email_institutional or ""),
                student_number_ine=str(student_number_ine or ""),
                student_number_local=str(student_number_local or ""),
            )
        cur = self.db.execute(
            """
            INSERT INTO students(
                student_number, student_number_ine, student_number_local, last_name, first_name, gender,
                birth_date, nationality, birth_place,
                email_personal, email_institutional, phone, enrollment_institution,
                origin_institution, origin_institution_country, highest_diploma, photo_path,
                application_platform, mon_master_ranking, funding, funding_other,
                accommodations, accommodations_other,
                notes,
                level, track, academic_year, mobility_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sn,
                str(student_number_ine or "").strip(),
                student_number_local,
                last_name,
                first_name,
                gender,
                birth_date,
                nationality,
                birth_place,
                email_personal,
                email_institutional,
                str(phone or "").strip(),
                enrollment_institution,
                str(origin_institution or "").strip(),
                str(origin_institution_country or "").strip(),
                str(highest_diploma or "").strip(),
                str(photo_path or "").strip(),
                application_platform,
                str(mon_master_ranking or "").strip(),
                funding,
                funding_other,
                accommodations,
                accommodations_other,
                notes,
                level,
                track,
                academic_year,
                normalize_mobility_type(mobility_type),
            ),
        )
        return int(cur.lastrowid)

    def update_student(
        self,
        student_id: int,
        student_number: str,
        student_number_ine: str,
        student_number_local: str,
        last_name: str,
        first_name: str,
        email_personal: str = "",
        email_institutional: str = "",
        phone: str = "",
        enrollment_institution: str = "",
        application_platform: str = "",
        mon_master_ranking: str = "",
        accommodations: str = "",
        accommodations_other: str = "",
        funding: str = "",
        funding_other: str = "",
        notes: str = "",
        level: str = "",
        track: str = "",
        academic_year: str = "",
        birth_date: str = "",
        nationality: str = "",
        birth_place: str = "",
        gender: str = "",
        origin_institution: str = "",
        origin_institution_country: str = "",
        highest_diploma: str = "",
        photo_path: str | None = None,
        mobility_type: str | None = None,
    ) -> None:
        row = self.get_student(int(student_id)) or {}
        photo = row.get("photo_path") if photo_path is None else photo_path
        mob = (
            normalize_mobility_type(mobility_type)
            if mobility_type is not None
            else normalize_mobility_type(row.get("mobility_type"))
        )
        self.db.execute(
            """
            UPDATE students SET
                student_number = ?,
                student_number_ine = ?,
                student_number_local = ?,
                last_name = ?,
                first_name = ?,
                gender = ?,
                birth_date = ?,
                nationality = ?,
                birth_place = ?,
                email_personal = ?,
                email_institutional = ?,
                phone = ?,
                enrollment_institution = ?,
                origin_institution = ?,
                origin_institution_country = ?,
                highest_diploma = ?,
                photo_path = ?,
                application_platform = ?,
                mon_master_ranking = ?,
                funding = ?,
                funding_other = ?,
                accommodations = ?,
                accommodations_other = ?,
                notes = ?,
                level = ?,
                track = ?,
                academic_year = ?,
                mobility_type = ?
            WHERE id = ?
            """,
            (
                student_number,
                str(student_number_ine or "").strip(),
                student_number_local,
                last_name,
                first_name,
                gender,
                birth_date,
                nationality,
                birth_place,
                email_personal,
                email_institutional,
                str(phone or "").strip(),
                enrollment_institution,
                str(origin_institution or "").strip(),
                str(origin_institution_country or "").strip(),
                str(highest_diploma or "").strip(),
                str(photo or "").strip(),
                application_platform,
                str(mon_master_ranking or "").strip(),
                funding,
                funding_other,
                accommodations,
                accommodations_other,
                notes,
                level,
                track,
                academic_year,
                mob,
                student_id,
            ),
        )

    def set_student_photo_path(self, student_id: int, photo_path: str) -> None:
        self.db.execute(
            "UPDATE students SET photo_path = ? WHERE id = ?",
            (str(photo_path or "").strip(), int(student_id)),
        )

    def import_student_photo(self, student_id: int, src_path: str | Path) -> str:
        old = str((self.get_student(int(student_id)) or {}).get("photo_path") or "")
        rel = store_student_photo(int(student_id), src_path)
        self.set_student_photo_path(int(student_id), rel)
        if old and old != rel:
            delete_stored_file(old)
        return rel

    def clear_student_photo(self, student_id: int) -> None:
        old = str((self.get_student(int(student_id)) or {}).get("photo_path") or "")
        self.set_student_photo_path(int(student_id), "")
        if old:
            delete_stored_file(old)

    def has_pedagogical_contract_pdf(self, student_id: int) -> bool:
        from ..core.institutions import PEDAGOGICAL_CONTRACT_CATEGORY

        row = self.db.query_one(
            """
            SELECT 1 FROM student_attachments
            WHERE student_id = ? AND category = ?
            LIMIT 1
            """,
            (int(student_id), PEDAGOGICAL_CONTRACT_CATEGORY),
        )
        return row is not None

    def has_pedagogical_contract_paper(self, student_id: int) -> bool:
        row = self.get_student(int(student_id)) or {}
        return bool(int(row.get("pedagogical_contract_paper") or 0))

    def has_pedagogical_contract(self, student_id: int) -> bool:
        return self.has_pedagogical_contract_pdf(int(student_id)) or self.has_pedagogical_contract_paper(
            int(student_id)
        )

    def set_pedagogical_contract_paper(self, student_id: int, on: bool) -> None:
        self.db.execute(
            "UPDATE students SET pedagogical_contract_paper = ? WHERE id = ?",
            (1 if on else 0, int(student_id)),
        )

    def reset_pedagogical_contract(self, student_id: int) -> None:
        """Efface le contrat pédagogique M1 (PDF + case papier) — nouveau contrat M2 requis."""
        from ..core.institutions import PEDAGOGICAL_CONTRACT_CATEGORY

        sid = int(student_id)
        self.set_pedagogical_contract_paper(sid, False)
        for att in self.list_student_attachments(sid, category=PEDAGOGICAL_CONTRACT_CATEGORY):
            self.delete_student_attachment(int(att["id"]))

    def list_m2_students_with_pedagogical_contract(self) -> list[dict[str, Any]]:
        """Étudiants en M2 ayant encore un contrat pédagogique (souvent M1 non effacé au passage)."""
        out: list[dict[str, Any]] = []
        for row in self.list_students():
            if str(row.get("level") or "").strip().upper() != "M2":
                continue
            sid = int(row["id"])
            if self.has_pedagogical_contract(sid):
                out.append(dict(row))
        out.sort(
            key=lambda s: (
                str(s.get("last_name") or "").lower(),
                str(s.get("first_name") or "").lower(),
            )
        )
        return out

    def reset_pedagogical_contracts_for_m2_students(
        self, student_ids: list[int] | None = None
    ) -> tuple[int, list[str]]:
        """Réinitialise le contrat pédagogique des M2 qui en ont encore un (passages antérieurs)."""
        candidates = self.list_m2_students_with_pedagogical_contract()
        if student_ids is not None:
            allowed = {int(x) for x in student_ids}
            candidates = [s for s in candidates if int(s["id"]) in allowed]
        names: list[str] = []
        for row in candidates:
            sid = int(row["id"])
            self.reset_pedagogical_contract(sid)
            label = f"{row.get('last_name', '')} {row.get('first_name', '')}".strip()
            names.append(label or f"#{sid}")
        return len(names), names

    def student_ids_missing_pedagogical_contract(
        self, student_ids: list[int] | None = None
    ) -> set[int]:
        from ..core.institutions import PEDAGOGICAL_CONTRACT_CATEGORY

        if student_ids is not None:
            if not student_ids:
                return set()
            placeholders = ",".join("?" * len(student_ids))
            have_rows = self.db.query_all(
                f"""
                SELECT DISTINCT student_id FROM student_attachments
                WHERE category = ? AND student_id IN ({placeholders})
                """,
                (PEDAGOGICAL_CONTRACT_CATEGORY, *student_ids),
            )
            have_pdf = {int(r["student_id"]) for r in have_rows}
            return {
                int(sid)
                for sid in student_ids
                if int(sid) not in have_pdf and not self.has_pedagogical_contract_paper(int(sid))
            }

        rows = self.db.query_all(
            """
            SELECT id FROM students
            WHERE COALESCE(pedagogical_contract_paper, 0) = 0
              AND id NOT IN (
                SELECT DISTINCT student_id FROM student_attachments
                WHERE category = ?
            )
            """,
            (PEDAGOGICAL_CONTRACT_CATEGORY,),
        )
        return {int(r["id"]) for r in rows}

    def list_student_attachments(
        self, student_id: int, *, category: str | None = None
    ) -> list[dict[str, Any]]:
        if category:
            rows = self.db.query_all(
                """
                SELECT * FROM student_attachments
                WHERE student_id = ? AND category = ?
                ORDER BY uploaded_at DESC, id DESC
                """,
                (int(student_id), str(category)),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT * FROM student_attachments
                WHERE student_id = ?
                ORDER BY uploaded_at DESC, id DESC
                """,
                (int(student_id),),
            )
        return [dict(r) for r in rows]

    def add_student_attachment(
        self,
        student_id: int,
        category: str,
        src_path: str | Path,
        *,
        label: str = "",
    ) -> int:
        rel, orig = store_student_document(int(student_id), str(category), src_path)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cur = self.db.execute(
            """
            INSERT INTO student_attachments(
                student_id, category, file_path, original_filename, label, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(student_id),
                str(category or "other").strip(),
                rel,
                orig,
                str(label or "").strip(),
                now,
            ),
        )
        return int(cur.lastrowid)

    def delete_student_attachment(self, attachment_id: int) -> None:
        row = self.db.query_one(
            "SELECT * FROM student_attachments WHERE id = ?", (int(attachment_id),)
        )
        if row:
            delete_stored_file(str(row["file_path"] or ""))
            self.db.execute("DELETE FROM student_attachments WHERE id = ?", (int(attachment_id),))

    def delete_student(self, student_id: int) -> None:
        self.db.execute("DELETE FROM students WHERE id = ?", (student_id,))

    def delete_students(self, student_ids: list[int]) -> None:
        if not student_ids:
            return
        placeholders = ",".join("?" * len(student_ids))
        self.db.execute(f"DELETE FROM students WHERE id IN ({placeholders})", tuple(student_ids))

    def clear_student_enrollments(self, student_id: int) -> None:
        self.db.execute("DELETE FROM enrollments WHERE student_id = ?", (int(student_id),))

    def delete_grades_for_student_courses(self, student_id: int, course_ids: list[int]) -> int:
        """Supprime toutes les notes de l'étudiant pour les évaluations des UE listées."""
        if not course_ids:
            return 0
        ids = [int(x) for x in course_ids]
        placeholders = ",".join("?" * len(ids))
        cur = self.db.execute(
            f"""
            DELETE FROM grades
            WHERE student_id = ?
              AND assessment_id IN (
                  SELECT id FROM assessments WHERE course_id IN ({placeholders})
              )
            """,
            (int(student_id), *ids),
        )
        for cid in ids:
            self.db.execute(
                "DELETE FROM ue_transcript_sessions WHERE student_id = ? AND course_id = ?",
                (int(student_id), int(cid)),
            )
        n = cur.rowcount
        return int(n) if n is not None and n >= 0 else 0

    def list_courses_with_grades_for_student(self, student_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT DISTINCT c.id AS course_id, c.code, c.name
            FROM grades g
            JOIN assessments a ON a.id = g.assessment_id
            JOIN courses c ON c.id = a.course_id
            WHERE g.student_id = ?
            ORDER BY c.code
            """,
            (int(student_id),),
        )
        return [dict(r) for r in rows]

    def promote_student_to_m2(self, student_id: int, new_academic_year: str, m2_track: str) -> None:
        """Passe l'étudiant en M2 : nouvelle année, parcours M2, réinscription maquette, notes conservées.

        Le contrat pédagogique M1 est effacé (PDF + version papier) : un contrat M2 distinct est requis.
        """
        from .academic_years import ensure_custom_academic_year

        s = self.get_student(student_id)
        if not s:
            raise ValueError("Student not found")
        ay = ensure_custom_academic_year(str(new_academic_year or "").strip())
        tr = normalize_track_acronym(m2_track)
        if not tr:
            raise ValueError("Academic year and M2 track are required")
        tpl = self.find_template_for_year_level_track(academic_year=ay, level="M2", track=tr)
        if tpl is None:
            raise ValueError(
                f"Aucune maquette M2 « {tr} » pour {ay}. "
                "Créez-la (onglet Maquette) ou reportez-la depuis l'année précédente, "
                "puis relancez le passage en M2."
            )
        self.update_student(
            student_id,
            str(s.get("student_number") or ""),
            str(s.get("student_number_ine") or ""),
            str(s.get("student_number_local") or ""),
            str(s.get("last_name") or ""),
            str(s.get("first_name") or ""),
            str(s.get("email_personal") or ""),
            str(s.get("email_institutional") or ""),
            str(s.get("phone") or ""),
            str(s.get("enrollment_institution") or ""),
            str(s.get("application_platform") or ""),
            str(s.get("accommodations") or ""),
            str(s.get("accommodations_other") or ""),
            str(s.get("notes") or ""),
            level="M2",
            track=tr,
            academic_year=ay,
            birth_date=str(s.get("birth_date") or ""),
            nationality=str(s.get("nationality") or ""),
            birth_place=str(s.get("birth_place") or ""),
            gender=str(s.get("gender") or ""),
        )
        self.enroll_student(int(student_id), int(tpl["id"]))
        self.sync_enrollments_for_student(student_id)
        self.reset_pedagogical_contract(student_id)

    def repeat_student_same_level(
        self,
        student_id: int,
        new_academic_year: str,
        course_ids_clear_grades: list[int],
    ) -> None:
        """
        Nouvelle année sans changer de niveau (ex. redoublement M1) : réinscription à la maquette,
        effacement des notes pour les UE dont les cases n'ont pas été cochées « conserver ».
        """
        from .academic_years import ensure_custom_academic_year

        s = self.get_student(student_id)
        if not s:
            raise ValueError("Student not found")
        ay = ensure_custom_academic_year(str(new_academic_year or "").strip())
        lv = normalize_level(s.get("level"))
        tr = normalize_track_acronym(s.get("track"))
        if not (lv and tr):
            from .student_parcours_repair import infer_student_parcours

            inferred = infer_student_parcours(
                self.db, int(student_id), str(s.get("academic_year") or "")
            )
            if inferred:
                inf_lv, inf_tr, _ = inferred
                lv = lv or normalize_level(inf_lv)
                tr = tr or normalize_track_acronym(inf_tr)
        if not lv:
            raise ValueError("Student level is empty; set it in the student record first")
        if not tr:
            raise ValueError("Student track is empty; set it in the student record first")
        tpl = self.find_template_for_year_level_track(academic_year=ay, level=lv, track=tr)
        if tpl is None:
            raise ValueError(
                f"Aucune maquette {lv} « {tr} » pour {ay}. "
                "Créez-la ou reportez-la depuis l'année précédente avant le redoublement."
            )
        old_ay = str(s.get("academic_year") or "").strip()
        old_tpl = self.find_template_for_year_level_track(
            academic_year=old_ay, level=lv, track=tr
        )
        if course_ids_clear_grades:
            self.delete_grades_for_student_courses(student_id, course_ids_clear_grades)
        if old_tpl:
            old_tid = int(old_tpl["id"])
            course_ids_old = {
                int(c["course_id"]) for c in self.list_template_courses(old_tid)
            }
            graded = {
                int(c["course_id"])
                for c in self.list_courses_with_grades_for_student(int(student_id))
            }
            clear_set = {int(x) for x in course_ids_clear_grades}
            for cid in course_ids_old & graded - clear_set:
                sent = self.is_sent_to_second_session(int(student_id), old_tid, int(cid))
                vs = "s2" if sent else "s1"
                self.upsert_ue_transcript_session(
                    int(student_id),
                    int(cid),
                    academic_year=old_ay,
                    view_session=vs,
                    source_template_id=old_tid,
                )
        self.update_student(
            student_id,
            str(s.get("student_number") or ""),
            str(s.get("student_number_ine") or ""),
            str(s.get("student_number_local") or ""),
            str(s.get("last_name") or ""),
            str(s.get("first_name") or ""),
            str(s.get("email_personal") or ""),
            str(s.get("email_institutional") or ""),
            str(s.get("phone") or ""),
            str(s.get("enrollment_institution") or ""),
            str(s.get("application_platform") or ""),
            str(s.get("accommodations") or ""),
            str(s.get("accommodations_other") or ""),
            str(s.get("notes") or ""),
            level=lv,
            track=tr,
            academic_year=ay,
            birth_date=str(s.get("birth_date") or ""),
            nationality=str(s.get("nationality") or ""),
            birth_place=str(s.get("birth_place") or ""),
            gender=str(s.get("gender") or ""),
        )
        self.enroll_student(int(student_id), int(tpl["id"]))
        self.sync_enrollments_for_student(student_id)

    # Courses
    def list_courses(self) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM courses ORDER BY code")
        return [dict(r) for r in rows]

    def list_courses_for_academic_year(self, academic_year: str) -> list[dict[str, Any]]:
        """UE présentes dans au moins une maquette du millésime (hors UE partagées avec un autre millésime)."""
        ay = str(academic_year or "").strip()
        if not ay:
            return self.list_courses()
        rows = self.db.query_all(
            """
            SELECT DISTINCT c.*,
              (
                SELECT tc.block_name
                FROM template_courses tc
                JOIN templates t ON t.id = tc.template_id
                WHERE tc.course_id = c.id
                  AND TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
                ORDER BY LENGTH(tc.block_name) DESC, tc.display_order
                LIMIT 1
              ) AS maquette_block
            FROM courses c
            JOIN template_courses tc ON tc.course_id = c.id
            JOIN templates t ON t.id = tc.template_id
            WHERE TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
              AND NOT EXISTS (
                SELECT 1
                FROM template_courses tc2
                JOIN templates t2 ON t2.id = tc2.template_id
                WHERE tc2.course_id = c.id
                  AND TRIM(IFNULL(t2.academic_year, '')) != ''
                  AND TRIM(t2.academic_year) != TRIM(?)
              )
            ORDER BY c.code
            """,
            (ay, ay, ay),
        )
        return [dict(r) for r in rows]

    def get_course(self, course_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM courses WHERE id = ?", (course_id,))
        return dict(row) if row else None

    def get_course_by_code(self, code: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM courses WHERE code = ?", (code.strip(),))
        return dict(row) if row else None

    def add_course(
        self,
        code: str,
        name: str,
        ects: float = 0.0,
        description: str = "",
        *,
        hours_total: float = 0.0,
        hours_cm: float = 0.0,
        hours_td: float = 0.0,
        hours_tp: float = 0.0,
        hours_project: float = 0.0,
        hours_pt: float = 0.0,
        hours_aa: float = 0.0,
        code_ip_paris: str = "",
        code_other: str = "",
        semester: str = "",
        mcc_text: str = "",
        ead_flag: str = "",
        course_type: str = "standard",
        teacher_last_name: str = "",
        teacher_first_name: str = "",
        teacher_email: str = "",
        teacher_email_work: str = "",
        teacher_email_work_2: str = "",
        teacher_email_personal: str = "",
        teacher_phone: str = "",
        teacher_phone_work: str = "",
        teacher_phone_work_2: str = "",
        teacher_phone_mobile: str = "",
        teacher_institution: str = "",
        carrier_partner: str = "",
        carrier_partner_other: str = "",
        mne_module_code: str = "",
    ) -> int:
        from .contact_emails import email_storage_values
        from .contact_phones import phone_storage_values

        emails = email_storage_values(
            teacher_email_work,
            teacher_email_work_2,
            teacher_email_personal,
            prefix="teacher",
            legacy_fallback=teacher_email,
        )
        phones = phone_storage_values(
            teacher_phone_work,
            teacher_phone_work_2,
            teacher_phone_mobile or teacher_phone,
            prefix="teacher",
            legacy_fallback=teacher_phone,
        )
        cur = self.db.execute(
            """
            INSERT INTO courses(
                code, name, ects, description, active,
                hours_total, hours_cm, hours_td, hours_tp, hours_project, hours_pt, hours_aa,
                code_ip_paris, code_other, mne_module_code, semester, mcc_text, ead_flag,
                course_type, teacher_last_name, teacher_first_name, teacher_email,
                teacher_email_work, teacher_email_work_2, teacher_email_personal,
                teacher_phone, teacher_phone_work, teacher_phone_work_2, teacher_phone_mobile,
                teacher_institution, carrier_partner, carrier_partner_other
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code.strip(),
                name.strip(),
                ects,
                description,
                hours_total,
                hours_cm,
                hours_td,
                hours_tp,
                hours_project,
                hours_pt,
                hours_aa,
                code_ip_paris.strip(),
                code_other.strip(),
                str(mne_module_code or "").strip(),
                semester.strip(),
                mcc_text,
                ead_flag.strip(),
                str(course_type or "standard").strip() or "standard",
                str(teacher_last_name or "").strip(),
                str(teacher_first_name or "").strip(),
                emails["teacher_email"],
                emails["teacher_email_work"],
                emails["teacher_email_work_2"],
                emails["teacher_email_personal"],
                phones["teacher_phone"],
                phones["teacher_phone_work"],
                phones["teacher_phone_work_2"],
                phones["teacher_phone_mobile"],
                str(teacher_institution or "").strip(),
                str(carrier_partner or "").strip(),
                str(carrier_partner_other or "").strip(),
            ),
        )
        new_id = int(cur.lastrowid)
        if str(course_type or "").strip().lower() == "internship":
            from .internship_grades import ensure_internship_mcc_and_assessments

            ensure_internship_mcc_and_assessments(self, new_id)
        return new_id

    def fork_course_for_millésime(self, course_id: int) -> int:
        """
        Duplique une fiche UE avec le code secrétariat (S1-C-*, …) si le code actuel est MNE.
        Réutilise la fiche existante si le code secrétariat est déjà en base.
        """
        from ..core.mne_modules import course_ue_code, is_legacy_semester_ue_code
        from .timetable_legacy import map_mne_to_legacy_timetable_code

        c = self.get_course(int(course_id))
        if not c:
            return int(course_id)
        current = str(c.get("code") or "").strip()
        if is_legacy_semester_ue_code(current):
            return int(course_id)
        mne = str(c.get("mne_module_code") or "").strip() or course_ue_code(c)
        if is_legacy_semester_ue_code(mne):
            legacy = mne
        else:
            legacy = map_mne_to_legacy_timetable_code(mne) if mne else ""
        if not legacy or legacy == current:
            return int(course_id)
        existing = self.get_course_by_code(legacy)
        if existing:
            return int(existing["id"])
        return self.add_course(
            legacy,
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
            mcc_text=str(c.get("mcc_text") or ""),
            ead_flag=str(c.get("ead_flag") or ""),
            course_type=str(c.get("course_type") or "standard"),
            teacher_last_name=str(c.get("teacher_last_name") or ""),
            teacher_first_name=str(c.get("teacher_first_name") or ""),
            teacher_email=str(c.get("teacher_email") or ""),
            teacher_email_work=str(c.get("teacher_email_work") or ""),
            teacher_email_work_2=str(c.get("teacher_email_work_2") or ""),
            teacher_email_personal=str(c.get("teacher_email_personal") or ""),
            teacher_phone=str(c.get("teacher_phone") or ""),
            teacher_phone_work=str(c.get("teacher_phone_work") or ""),
            teacher_phone_work_2=str(c.get("teacher_phone_work_2") or ""),
            teacher_phone_mobile=str(c.get("teacher_phone_mobile") or ""),
            teacher_institution=str(c.get("teacher_institution") or ""),
            carrier_partner=str(c.get("carrier_partner") or ""),
            carrier_partner_other=str(c.get("carrier_partner_other") or ""),
            mne_module_code=mne,
        )

    def repair_millésime_course_nomenclature(self, academic_year: str) -> tuple[int, int]:
        """
        Pour un millésime : bascule les codes MNE (M1B1-…) vers codes secrétariat (S1-C-…)
        sur les UE rattachées uniquement à ce millésime.
        Retourne (fiches dupliquées, maquettes mises à jour).
        """
        ay = str(academic_year or "").strip()
        if not ay:
            return 0, 0
        from .academic_years import millésime_uses_secretariat_course_codes

        if not millésime_uses_secretariat_course_codes(ay):
            raise ValueError(
                f"La réparation secrétariat ne s'applique qu'aux millésimes avant "
                f"2026-2027 (ex. 2025-2026). Pour {ay}, les codes MNE sont déjà la référence."
            )
        courses = self.list_courses_for_academic_year(ay)
        forked = 0
        relinked = 0
        id_map: dict[int, int] = {}
        for c in courses:
            old_id = int(c["id"])
            new_id = self.fork_course_for_millésime(old_id)
            if new_id != old_id:
                id_map[old_id] = new_id
                forked += 1
        if not id_map:
            return 0, 0
        tpl_ids = [int(t["id"]) for t in self.list_templates(academic_year=ay)]
        for tid in tpl_ids:
            for row in self.list_template_courses(tid):
                old_cid = int(row["course_id"])
                if old_cid not in id_map:
                    continue
                new_cid = id_map[old_cid]
                self.db.execute(
                    "DELETE FROM template_courses WHERE template_id = ? AND course_id = ?",
                    (tid, old_cid),
                )
                try:
                    self.add_course_to_template(
                        tid,
                        new_cid,
                        str(row.get("block_name") or ""),
                        float(row.get("global_coefficient") or 1.0),
                        int(row.get("display_order") or 0),
                        int(row.get("optional") or 0),
                        int(row.get("free_ue") or 0),
                    )
                    relinked += 1
                except Exception:
                    pass
        return forked, relinked

    def _course_ids_linked_to_other_millésimes(self, academic_year: str) -> list[int]:
        ay = str(academic_year or "").strip()
        if not ay:
            return []
        rows = self.db.query_all(
            """
            SELECT DISTINCT c.id AS course_id
            FROM courses c
            JOIN template_courses tc ON tc.course_id = c.id
            JOIN templates t ON t.id = tc.template_id
            WHERE TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
              AND EXISTS (
                SELECT 1
                FROM template_courses tc2
                JOIN templates t2 ON t2.id = tc2.template_id
                WHERE tc2.course_id = c.id
                  AND TRIM(IFNULL(t2.academic_year, '')) != ''
                  AND TRIM(t2.academic_year) != TRIM(?)
              )
            """,
            (ay, ay),
        )
        return [int(r["course_id"]) for r in rows]

    def _relink_template_courses(self, template_ids: list[int], id_map: dict[int, int]) -> int:
        relinked = 0
        for tid in template_ids:
            for row in self.list_template_courses(tid):
                old_cid = int(row["course_id"])
                if old_cid not in id_map:
                    continue
                new_cid = id_map[old_cid]
                self.db.execute(
                    "DELETE FROM template_courses WHERE template_id = ? AND course_id = ?",
                    (tid, old_cid),
                )
                try:
                    self.add_course_to_template(
                        tid,
                        new_cid,
                        str(row.get("block_name") or ""),
                        float(row.get("global_coefficient") or 1.0),
                        int(row.get("display_order") or 0),
                        int(row.get("optional") or 0),
                        int(row.get("free_ue") or 0),
                    )
                    relinked += 1
                except Exception:
                    pass
        return relinked

    def isolate_shared_courses_for_millésime(self, academic_year: str) -> tuple[int, int]:
        """
        Duplique les UE encore partagées entre millésimes pour que chaque année
        ait ses propres fiches (secrétariat pour 2025-2026, MNE pour 2026-2027).
        """
        ay = str(academic_year or "").strip()
        if not ay:
            return 0, 0
        from .academic_years import millésime_uses_secretariat_course_codes

        shared = self._course_ids_linked_to_other_millésimes(ay)
        if not shared:
            return 0, 0
        id_map: dict[int, int] = {}
        for old_id in shared:
            if millésime_uses_secretariat_course_codes(ay):
                new_id = self.fork_course_for_millésime(old_id)
            else:
                new_id = self._duplicate_course_for_mne_millésime(old_id)
            if new_id != old_id:
                id_map[old_id] = new_id
        if not id_map:
            return 0, 0
        tpl_ids = [int(t["id"]) for t in self.list_templates(academic_year=ay)]
        relinked = self._relink_template_courses(tpl_ids, id_map)
        return len(id_map), relinked

    def _duplicate_course_for_mne_millésime(self, course_id: int) -> int:
        """Copie une fiche UE pour un millésime MNE (code Apogée conservé si possible)."""
        from ..core.mne_modules import is_legacy_semester_ue_code

        c = self.get_course(int(course_id))
        if not c:
            return int(course_id)
        code = str(c.get("code") or "").strip()
        if is_legacy_semester_ue_code(code):
            return int(course_id)
        if self.get_course_by_code(code):
            suffix = 2
            base = code
            while self.get_course_by_code(f"{base}#{suffix}"):
                suffix += 1
            code = f"{base}#{suffix}"
        mne = str(c.get("mne_module_code") or "").strip()
        if is_legacy_semester_ue_code(mne):
            mne = ""
        return self.add_course(
            code,
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
            mcc_text=str(c.get("mcc_text") or ""),
            ead_flag=str(c.get("ead_flag") or ""),
            course_type=str(c.get("course_type") or "standard"),
            teacher_last_name=str(c.get("teacher_last_name") or ""),
            teacher_first_name=str(c.get("teacher_first_name") or ""),
            teacher_email=str(c.get("teacher_email") or ""),
            teacher_email_work=str(c.get("teacher_email_work") or ""),
            teacher_email_work_2=str(c.get("teacher_email_work_2") or ""),
            teacher_email_personal=str(c.get("teacher_email_personal") or ""),
            teacher_phone=str(c.get("teacher_phone") or ""),
            teacher_phone_work=str(c.get("teacher_phone_work") or ""),
            teacher_phone_work_2=str(c.get("teacher_phone_work_2") or ""),
            teacher_phone_mobile=str(c.get("teacher_phone_mobile") or ""),
            teacher_institution=str(c.get("teacher_institution") or ""),
            carrier_partner=str(c.get("carrier_partner") or ""),
            carrier_partner_other=str(c.get("carrier_partner_other") or ""),
            mne_module_code=mne,
        )

    def _fork_course_for_mne_millésime(self, course_id: int) -> int:
        """Copie une fiche UE pour un millésime MNE (M1B1-…), y compris depuis codes secrétariat."""
        import re

        from ..core.mne_modules import course_ue_code, is_legacy_semester_ue_code, normalize_mne_module_code
        from .timetable_legacy import map_legacy_timetable_code

        c = self.get_course(int(course_id))
        if not c:
            return int(course_id)

        current = re.sub(r"\s+", "", str(c.get("code") or "").strip().upper())
        mne = normalize_mne_module_code(str(c.get("mne_module_code") or ""))
        if (not mne or is_legacy_semester_ue_code(mne)) and is_legacy_semester_ue_code(current):
            mne = normalize_mne_module_code(map_legacy_timetable_code(current))
        if not mne or is_legacy_semester_ue_code(mne):
            mne = normalize_mne_module_code(course_ue_code(c) or "")

        apogee = str(c.get("code_ip_paris") or "").strip()
        if not apogee and current and not is_legacy_semester_ue_code(current):
            apogee = str(c.get("code") or "").strip()

        if mne and not is_legacy_semester_ue_code(mne):
            dup_code = mne
            if self.get_course_by_code(dup_code):
                suffix = 2
                while self.get_course_by_code(f"{mne}#{suffix}"):
                    suffix += 1
                dup_code = f"{mne}#{suffix}"
            return self.add_course(
                dup_code,
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
                code_ip_paris=apogee,
                code_other=str(c.get("code_other") or ""),
                semester=str(c.get("semester") or ""),
                mcc_text=str(c.get("mcc_text") or ""),
                ead_flag=str(c.get("ead_flag") or ""),
                course_type=str(c.get("course_type") or "standard"),
                teacher_last_name=str(c.get("teacher_last_name") or ""),
                teacher_first_name=str(c.get("teacher_first_name") or ""),
                teacher_email=str(c.get("teacher_email") or ""),
                teacher_email_work=str(c.get("teacher_email_work") or ""),
                teacher_email_work_2=str(c.get("teacher_email_work_2") or ""),
                teacher_email_personal=str(c.get("teacher_email_personal") or ""),
                teacher_phone=str(c.get("teacher_phone") or ""),
                teacher_phone_work=str(c.get("teacher_phone_work") or ""),
                teacher_phone_work_2=str(c.get("teacher_phone_work_2") or ""),
                teacher_phone_mobile=str(c.get("teacher_phone_mobile") or ""),
                teacher_institution=str(c.get("teacher_institution") or ""),
                carrier_partner=str(c.get("carrier_partner") or ""),
                carrier_partner_other=str(c.get("carrier_partner_other") or ""),
                mne_module_code=mne,
            )

        return self._duplicate_course_for_mne_millésime(course_id)

    def _fork_course_for_target_millésime(self, course_id: int, target_academic_year: str) -> int:
        """Prépare une fiche UE indépendante pour le millésime cible (secrétariat ou MNE)."""
        from .academic_years import millésime_uses_secretariat_course_codes

        tgt_ay = str(target_academic_year or "").strip()
        if millésime_uses_secretariat_course_codes(tgt_ay):
            return self.fork_course_for_millésime(course_id)
        return self._fork_course_for_mne_millésime(course_id)

    def sanitize_millésime_course_metadata(self, academic_year: str) -> int:
        """Retire les codes secrétariat (S1-C-…) des fiches d'un millésime MNE."""
        ay = str(academic_year or "").strip()
        if not ay:
            return 0
        from .academic_years import millésime_uses_secretariat_course_codes
        from ..core.mne_modules import is_legacy_semester_ue_code

        if millésime_uses_secretariat_course_codes(ay):
            return 0
        updated = 0
        for c in self.list_courses_for_academic_year(ay):
            mne = str(c.get("mne_module_code") or "").strip()
            if mne and is_legacy_semester_ue_code(mne):
                self.db.execute(
                    "UPDATE courses SET mne_module_code = '' WHERE id = ?",
                    (int(c["id"]),),
                )
                updated += 1
        return updated

    def repair_maquette_block_labels(self, academic_year: str) -> int:
        """Corrige libellés de bloc invalides (ex. « 1 ») et placements manifestement erronés."""
        ay = str(academic_year or "").strip()
        if not ay:
            return 0
        fixed = 0
        templates = self.list_templates(academic_year=ay)
        for tpl in templates:
            tid = int(tpl["id"])
            track = str(tpl.get("track") or "").strip().upper()
            rows = self.list_template_courses(tid)
            dominant_block_1 = next(
                (
                    str(r.get("block_name") or "").strip()
                    for r in rows
                    if "block 1" in str(r.get("block_name") or "").lower()
                ),
                "Common courses 1 (block 1)",
            )
            for row in rows:
                bk = str(row.get("block_name") or "").strip()
                cid = int(row["course_id"])
                course = self.get_course(cid) or {}
                mne = str(course.get("mne_module_code") or "").strip().upper()
                if bk.isdigit():
                    self.db.execute(
                        """
                        UPDATE template_courses
                        SET block_name = ?
                        WHERE template_id = ? AND course_id = ?
                        """,
                        (dominant_block_1, tid, cid),
                    )
                    fixed += 1
                    bk = dominant_block_1
                if track == "C" and mne.endswith("-P-NEUT"):
                    self.db.execute(
                        "DELETE FROM template_courses WHERE template_id = ? AND course_id = ?",
                        (tid, cid),
                    )
                    fixed += 1
        return fixed

    def repair_m2_internship_maquette_placements(self, academic_year: str) -> int:
        """
        Retire les stages M2 des maquettes M1 et les rattache à toutes les maquettes M2
        du millésime (bloc 5 / stage).
        """
        from .internship_grades import internship_program_level

        ay = str(academic_year or "").strip()
        if not ay:
            return 0
        m1_templates = [
            t
            for t in self.list_templates(academic_year=ay)
            if normalize_level(t.get("level")) == "M1"
        ]
        m2_templates = [
            t
            for t in self.list_templates(academic_year=ay)
            if normalize_level(t.get("level")) == "M2"
        ]
        if not m2_templates:
            return 0

        stage_ids: list[int] = []
        for c in self.list_courses_for_academic_year(ay):
            if internship_program_level(c) == "M2":
                stage_ids.append(int(c["id"]))
        if not stage_ids:
            return 0

        block_label = "Internship (block 5)"
        for tpl in m2_templates:
            for row in self.list_template_courses(int(tpl["id"])):
                bk = str(row.get("block_name") or "").upper()
                if "INTERNSHIP" in bk or "STAGE" in bk:
                    block_label = str(row.get("block_name") or "").strip() or block_label
                    break

        fixed = 0
        for cid in stage_ids:
            for tpl in m1_templates:
                tid = int(tpl["id"])
                cur = self.db.execute(
                    "DELETE FROM template_courses WHERE template_id = ? AND course_id = ?",
                    (tid, cid),
                )
                if cur.rowcount and int(cur.rowcount) > 0:
                    fixed += int(cur.rowcount)
            for tpl in m2_templates:
                tid = int(tpl["id"])
                exists = self.db.query_one(
                    """
                    SELECT 1 FROM template_courses
                    WHERE template_id = ? AND course_id = ?
                    """,
                    (tid, cid),
                )
                if exists:
                    continue
                rows = self.list_template_courses(tid)
                order = max((int(r.get("display_order") or 0) for r in rows), default=0) + 1
                self.add_course_to_template(
                    tid,
                    cid,
                    block_name=block_label,
                    display_order=order,
                )
                fixed += 1
        return fixed

    def ensure_millésime_course_integrity(self, academic_year: str) -> dict[str, int]:
        """Réparation complète : isolation, nomenclature, métadonnées et blocs maquette."""
        ay = str(academic_year or "").strip()
        from .academic_years import millésime_uses_secretariat_course_codes

        out = {
            "isolated_courses": 0,
            "relinked_placements": 0,
            "forked_secretariat": 0,
            "sanitized_mne_fields": 0,
            "fixed_block_labels": 0,
            "fixed_m2_internship_placements": 0,
        }
        if not ay:
            return out
        if millésime_uses_secretariat_course_codes(ay):
            forked, relinked = self.repair_millésime_course_nomenclature(ay)
            out["forked_secretariat"] = forked
            out["relinked_placements"] += relinked
        iso_n, relink = self.isolate_shared_courses_for_millésime(ay)
        out["isolated_courses"] = iso_n
        out["relinked_placements"] += relink
        if not millésime_uses_secretariat_course_codes(ay):
            out["sanitized_mne_fields"] = self.sanitize_millésime_course_metadata(ay)
        out["fixed_block_labels"] = self.repair_maquette_block_labels(ay)
        out["fixed_m2_internship_placements"] = self.repair_m2_internship_maquette_placements(ay)
        return out

    def apply_m2_dwm_accreditation_erratum(
        self, *, academic_year: str = "2026-2027"
    ) -> dict[str, Any]:
        """Aligne la maquette M2 DWM sur l'erratum PDF accréditation 2026-2031."""
        from .maquette_erratum import apply_m2_dwm_accreditation_erratum

        return apply_m2_dwm_accreditation_erratum(self, academic_year=academic_year)

    def apply_official_accreditation_maquettes(
        self,
        *,
        academic_year: str = "2026-2027",
        m1_of_path: str = "",
        m2_of_path: str = "",
    ) -> dict[str, Any]:
        """M1 (OF PR1162 mod) + M2 (erratum PDF + OF PR1163) pour le millésime."""
        from pathlib import Path

        from .maquette_erratum import (
            DEFAULT_M1_OF,
            DEFAULT_M2_OF,
            apply_official_accreditation_maquettes_2026_2027,
        )

        return apply_official_accreditation_maquettes_2026_2027(
            self,
            academic_year=academic_year,
            m1_of_path=Path(m1_of_path) if str(m1_of_path or "").strip() else DEFAULT_M1_OF,
            m2_of_path=Path(m2_of_path) if str(m2_of_path or "").strip() else DEFAULT_M2_OF,
        )

    def update_course(
        self,
        course_id: int,
        code: str,
        name: str,
        ects: float = 0.0,
        description: str = "",
        *,
        hours_total: float = 0.0,
        hours_cm: float = 0.0,
        hours_td: float = 0.0,
        hours_tp: float = 0.0,
        hours_project: float = 0.0,
        hours_pt: float = 0.0,
        hours_aa: float = 0.0,
        code_ip_paris: str = "",
        code_other: str = "",
        semester: str = "",
        mcc_text: str = "",
        ead_flag: str = "",
        course_type: str = "standard",
        teacher_last_name: str = "",
        teacher_first_name: str = "",
        teacher_email: str = "",
        teacher_email_work: str = "",
        teacher_email_work_2: str = "",
        teacher_email_personal: str = "",
        teacher_phone: str = "",
        teacher_phone_work: str = "",
        teacher_phone_work_2: str = "",
        teacher_phone_mobile: str = "",
        teacher_institution: str = "",
        carrier_partner: str = "",
        carrier_partner_other: str = "",
        mne_module_code: str = "",
    ) -> None:
        from .contact_emails import email_storage_values
        from .contact_phones import phone_storage_values

        emails = email_storage_values(
            teacher_email_work,
            teacher_email_work_2,
            teacher_email_personal,
            prefix="teacher",
            legacy_fallback=teacher_email,
        )
        phones = phone_storage_values(
            teacher_phone_work,
            teacher_phone_work_2,
            teacher_phone_mobile or teacher_phone,
            prefix="teacher",
            legacy_fallback=teacher_phone,
        )
        self.db.execute(
            """
            UPDATE courses SET
                code = ?,
                name = ?,
                ects = ?,
                description = ?,
                hours_total = ?,
                hours_cm = ?,
                hours_td = ?,
                hours_tp = ?,
                hours_project = ?,
                hours_pt = ?,
                hours_aa = ?,
                code_ip_paris = ?,
                code_other = ?,
                mne_module_code = ?,
                semester = ?,
                mcc_text = ?,
                ead_flag = ?,
                course_type = ?,
                teacher_last_name = ?,
                teacher_first_name = ?,
                teacher_email = ?,
                teacher_email_work = ?,
                teacher_email_work_2 = ?,
                teacher_email_personal = ?,
                teacher_phone = ?,
                teacher_phone_work = ?,
                teacher_phone_work_2 = ?,
                teacher_phone_mobile = ?,
                teacher_institution = ?,
                carrier_partner = ?,
                carrier_partner_other = ?
            WHERE id = ?
            """,
            (
                code.strip(),
                name.strip(),
                ects,
                description,
                hours_total,
                hours_cm,
                hours_td,
                hours_tp,
                hours_project,
                hours_pt,
                hours_aa,
                code_ip_paris.strip(),
                code_other.strip(),
                str(mne_module_code or "").strip(),
                semester.strip(),
                mcc_text,
                ead_flag.strip(),
                str(course_type or "standard").strip() or "standard",
                str(teacher_last_name or "").strip(),
                str(teacher_first_name or "").strip(),
                emails["teacher_email"],
                emails["teacher_email_work"],
                emails["teacher_email_work_2"],
                emails["teacher_email_personal"],
                phones["teacher_phone"],
                phones["teacher_phone_work"],
                phones["teacher_phone_work_2"],
                phones["teacher_phone_mobile"],
                str(teacher_institution or "").strip(),
                str(carrier_partner or "").strip(),
                str(carrier_partner_other or "").strip(),
                course_id,
            ),
        )
        if str(course_type or "").strip().lower() == "internship":
            from .internship_grades import ensure_internship_mcc_and_assessments

            ensure_internship_mcc_and_assessments(self, int(course_id))

    def is_internship_course(self, course_id: int) -> bool:
        from .internship_grades import is_internship_course_data

        c = self.get_course(int(course_id))
        return bool(c and is_internship_course_data(c))

    def repair_internship_assessments(self) -> int:
        from .internship_grades import repair_internship_assessments

        return repair_internship_assessments(self)

    def list_student_internship_slots(self, student_id: int) -> list[dict[str, Any]]:
        """Créneaux stage : une entrée par UE stage dans chaque maquette où l'étudiant est inscrit."""
        sid = int(student_id)
        rows = self.db.query_all(
            """
            SELECT
                t.id AS template_id,
                t.name AS template_name,
                t.academic_year,
                t.level,
                t.track,
                c.id AS course_id,
                c.code AS course_code,
                c.name AS course_name,
                c.course_type,
                tc.display_order
            FROM enrollments e
            JOIN templates t ON t.id = e.template_id
            JOIN template_courses tc ON tc.template_id = t.id
            JOIN courses c ON c.id = tc.course_id
            WHERE e.student_id = ?
            ORDER BY t.academic_year, t.level, t.id, tc.display_order, c.name
            """,
            (sid,),
        )
        slots: list[dict[str, Any]] = []
        for row in rows:
            cid = int(row["course_id"])
            if not self.is_internship_course(cid):
                continue
            tid = int(row["template_id"])
            rec = self.get_internship_record(sid, tid, cid)
            slots.append(
                {
                    "template": {
                        "id": tid,
                        "name": row["template_name"],
                        "academic_year": row["academic_year"],
                        "level": row["level"],
                        "track": row["track"],
                    },
                    "course": {
                        "id": cid,
                        "code": row["course_code"],
                        "name": row["course_name"],
                    },
                    "record": rec,
                }
            )
        return slots

    def get_internship_record(
        self, student_id: int, template_id: int, course_id: int
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            """
            SELECT * FROM internship_records
            WHERE student_id = ? AND template_id = ? AND course_id = ?
            """,
            (int(student_id), int(template_id), int(course_id)),
        )
        return dict(row) if row else None

    def upsert_internship_record(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        topic: str = "",
        supervisor_last_name: str = "",
        supervisor_first_name: str = "",
        supervisor_email: str = "",
        supervisor_institution: str = "",
        supervisor_phone: str = "",
        follow_up_status: str = "",
        notes: str = "",
        convention_path: str | None = None,
        convention_paper: bool | None = None,
        reporter_last_name: str = "",
        reporter_first_name: str = "",
        reporter_institution: str = "",
        defense_date: str = "",
        defense_time: str = "",
    ) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        existing = self.get_internship_record(int(student_id), int(template_id), int(course_id))
        conv = (
            existing.get("convention_path")
            if convention_path is None and existing
            else (convention_path or "")
        )
        paper = (
            bool(int(existing.get("convention_paper") or 0))
            if convention_paper is None and existing
            else bool(convention_paper)
        )
        if existing:
            self.db.execute(
                """
                UPDATE internship_records SET
                    topic = ?, supervisor_last_name = ?, supervisor_first_name = ?,
                    supervisor_email = ?, supervisor_institution = ?, supervisor_phone = ?,
                    follow_up_status = ?, convention_path = ?, convention_paper = ?,
                    reporter_last_name = ?, reporter_first_name = ?, reporter_institution = ?,
                    defense_date = ?, defense_time = ?,
                    notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(topic or "").strip(),
                    str(supervisor_last_name or "").strip(),
                    str(supervisor_first_name or "").strip(),
                    str(supervisor_email or "").strip(),
                    str(supervisor_institution or "").strip(),
                    str(supervisor_phone or "").strip(),
                    str(follow_up_status or "").strip(),
                    str(conv or "").strip(),
                    1 if paper else 0,
                    str(reporter_last_name or "").strip(),
                    str(reporter_first_name or "").strip(),
                    str(reporter_institution or "").strip(),
                    str(defense_date or "").strip(),
                    str(defense_time or "").strip(),
                    str(notes or "").strip(),
                    now,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cur = self.db.execute(
            """
            INSERT INTO internship_records(
                student_id, template_id, course_id, topic,
                supervisor_last_name, supervisor_first_name, supervisor_email,
                supervisor_institution, supervisor_phone, follow_up_status,
                convention_path, convention_paper,
                reporter_last_name, reporter_first_name, reporter_institution,
                defense_date, defense_time,
                notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(student_id),
                int(template_id),
                int(course_id),
                str(topic or "").strip(),
                str(supervisor_last_name or "").strip(),
                str(supervisor_first_name or "").strip(),
                str(supervisor_email or "").strip(),
                str(supervisor_institution or "").strip(),
                str(supervisor_phone or "").strip(),
                str(follow_up_status or "").strip(),
                str(conv or "").strip(),
                1 if paper else 0,
                str(reporter_last_name or "").strip(),
                str(reporter_first_name or "").strip(),
                str(reporter_institution or "").strip(),
                str(defense_date or "").strip(),
                str(defense_time or "").strip(),
                str(notes or "").strip(),
                now,
            ),
        )
        return int(cur.lastrowid)

    def list_internship_defense_planning(
        self, course_id: int, *, academic_year: str = ""
    ) -> list[dict[str, Any]]:
        """Planning des soutenances pour une UE stage (toutes maquettes du millésime)."""
        cid = int(course_id)
        if not self.is_internship_course(cid):
            return []
        course = self.get_course(cid) or {}
        templates = self.list_templates_containing_course(cid, academic_year=academic_year)
        sid_to_tpl: dict[int, dict[str, Any]] = {}
        for tpl in templates:
            tid = int(tpl["id"])
            for s in self.list_students_for_template(tid):
                sid_to_tpl[int(s["id"])] = tpl
        students = self.list_students_for_course_in_templates(
            cid, [int(t["id"]) for t in templates]
        )
        rows: list[dict[str, Any]] = []
        for s in students:
            sid = int(s["id"])
            tpl = sid_to_tpl.get(sid) or {}
            tid = int(tpl["id"]) if tpl.get("id") is not None else 0
            rec = self.get_internship_record(sid, tid, cid) if tid else None
            rec = rec or {}
            rep_name = " ".join(
                x
                for x in (
                    str(rec.get("reporter_first_name") or "").strip(),
                    str(rec.get("reporter_last_name") or "").strip(),
                )
                if x
            )
            sup_name = " ".join(
                x
                for x in (
                    str(rec.get("supervisor_first_name") or "").strip(),
                    str(rec.get("supervisor_last_name") or "").strip(),
                )
                if x
            )
            rows.append(
                {
                    "student_id": sid,
                    "student_number": s.get("student_number"),
                    "student_number_ine": s.get("student_number_ine"),
                    "last_name": s.get("last_name"),
                    "first_name": s.get("first_name"),
                    "level": s.get("level"),
                    "track": s.get("track"),
                    "course_code": course.get("code"),
                    "course_name": course.get("name"),
                    "template_id": tid,
                    "template_name": tpl.get("name"),
                    "academic_year": tpl.get("academic_year"),
                    "topic": rec.get("topic"),
                    "supervisor_name": sup_name,
                    "supervisor_institution": rec.get("supervisor_institution"),
                    "reporter_name": rep_name,
                    "reporter_institution": rec.get("reporter_institution"),
                    "defense_date": str(rec.get("defense_date") or "").strip(),
                    "defense_time": str(rec.get("defense_time") or "").strip(),
                }
            )

        def _sort_key(r: dict[str, Any]) -> tuple:
            d = str(r.get("defense_date") or "9999-12-31")
            t = str(r.get("defense_time") or "99:99")
            return (d, t, str(r.get("last_name") or ""), str(r.get("first_name") or ""))

        rows.sort(key=_sort_key)
        return rows

    def set_internship_convention_paper(
        self, student_id: int, template_id: int, course_id: int, on: bool
    ) -> None:
        existing = self.get_internship_record(int(student_id), int(template_id), int(course_id))
        if existing:
            self.db.execute(
                "UPDATE internship_records SET convention_paper = ? WHERE id = ?",
                (1 if on else 0, int(existing["id"])),
            )
        else:
            self.upsert_internship_record(
                int(student_id), int(template_id), int(course_id), convention_paper=on
            )

    def import_internship_convention(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        src_path: str | Path,
    ) -> str:
        existing = self.get_internship_record(int(student_id), int(template_id), int(course_id))
        old = str((existing or {}).get("convention_path") or "")
        rel = store_internship_convention(
            int(student_id), int(course_id), int(template_id), src_path
        )
        if existing:
            self.db.execute(
                "UPDATE internship_records SET convention_path = ?, updated_at = ? WHERE id = ?",
                (
                    rel,
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    int(existing["id"]),
                ),
            )
        else:
            self.upsert_internship_record(
                int(student_id),
                int(template_id),
                int(course_id),
                convention_path=rel,
            )
        if old and old != rel:
            delete_stored_file(old)
        return rel

    def clear_internship_convention(
        self, student_id: int, template_id: int, course_id: int
    ) -> None:
        rec = self.get_internship_record(int(student_id), int(template_id), int(course_id))
        if not rec:
            return
        old = str(rec.get("convention_path") or "")
        self.db.execute(
            "UPDATE internship_records SET convention_path = '' WHERE id = ?",
            (int(rec["id"]),),
        )
        if old:
            delete_stored_file(old)

    def import_course_syllabus(self, course_id: int, src: str | Path) -> str:
        from .attachments import delete_stored_file, store_course_syllabus

        row = self.get_course(int(course_id)) or {}
        old = str(row.get("syllabus_path") or "").strip()
        rel, orig = store_course_syllabus(int(course_id), src)
        self.db.execute(
            "UPDATE courses SET syllabus_path = ?, syllabus_filename = ? WHERE id = ?",
            (rel, str(orig or "").strip(), int(course_id)),
        )
        if old and old != rel:
            delete_stored_file(old)
        return rel

    def clear_course_syllabus(self, course_id: int) -> None:
        from .attachments import delete_stored_file

        row = self.get_course(int(course_id)) or {}
        old = str(row.get("syllabus_path") or "").strip()
        self.db.execute(
            "UPDATE courses SET syllabus_path = '', syllabus_filename = '' WHERE id = ?",
            (int(course_id),),
        )
        if old:
            delete_stored_file(old)

    def delete_course(self, course_id: int) -> None:
        row = self.get_course(int(course_id)) or {}
        old_syllabus = str(row.get("syllabus_path") or "").strip()
        self.db.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        if old_syllabus:
            from .attachments import delete_stored_file

            delete_stored_file(old_syllabus)

    def delete_assessments_for_course(self, course_id: int) -> None:
        """
        Supprime les assessments d’un cours.

        Note : les notes (`grades`) sont supprimées en cascade via la FK sur `assessment_id`.
        """
        self.db.execute("DELETE FROM assessments WHERE course_id = ?", (course_id,))

    def list_assessments(self, course_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM assessments WHERE course_id = ? ORDER BY session, display_order, id",
            (course_id,),
        )
        return [dict(r) for r in rows]

    def add_assessment(self, course_id: int, name: str, kind: str, coefficient: float,
                       session: int = 1, display_order: int = 0) -> None:
        self.db.execute(
            """
            INSERT INTO assessments(course_id, name, kind, coefficient, session, display_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (course_id, name, kind, coefficient, session, display_order),
        )

    # Templates
    def list_templates(self, *, academic_year: str | None = None) -> list[dict[str, Any]]:
        ay = str(academic_year or "").strip()
        if ay:
            rows = self.db.query_all(
                """
                SELECT * FROM templates
                WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)
                ORDER BY level, track, name
                """,
                (ay,),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM templates ORDER BY academic_year DESC, level, track, name"
            )
        return [dict(r) for r in rows]

    def summarize_academic_year_deletion(self, academic_year: str) -> dict[str, int]:
        ay = str(academic_year or "").strip()
        if not ay:
            return {"students": 0, "templates": 0, "timetable_imports": 0, "master_team_members": 0}

        def _count(sql: str) -> int:
            row = self.db.query_one(sql, (ay,))
            return int(row[0]) if row else 0

        return {
            "students": _count(
                "SELECT COUNT(*) FROM students WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)"
            ),
            "templates": _count(
                "SELECT COUNT(*) FROM templates WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)"
            ),
            "timetable_imports": _count(
                "SELECT COUNT(*) FROM timetable_imports WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)"
            ),
            "master_team_members": _count(
                "SELECT COUNT(*) FROM master_team_members WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)"
            ),
        }

    def delete_academic_year_data(self, academic_year: str) -> dict[str, int]:
        """Supprime toutes les données rattachées à un millésime (étudiants, maquettes, EdT, équipe)."""
        ay = str(academic_year or "").strip()
        if not ay:
            raise ValueError("Millésime invalide.")
        summary = self.summarize_academic_year_deletion(ay)
        self.db.execute(
            "DELETE FROM students WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)",
            (ay,),
        )
        self.db.execute(
            "DELETE FROM templates WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)",
            (ay,),
        )
        self.db.execute(
            "DELETE FROM timetable_imports WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)",
            (ay,),
        )
        self.db.execute(
            "DELETE FROM master_team_members WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)",
            (ay,),
        )
        self.db.execute(
            "DELETE FROM ue_transcript_sessions WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)",
            (ay,),
        )
        return summary

    def get_template(self, template_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM templates WHERE id = ?", (int(template_id),))
        return dict(row) if row else None

    def add_template(
        self,
        name: str,
        level: str,
        track: str,
        academic_year: str,
        version: str = "1",
        *,
        parent_template_id: int | None = None,
        change_note: str = "",
    ) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cur = self.db.execute(
            """
            INSERT INTO templates(
                name, level, track, academic_year, version,
                parent_template_id, change_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                level,
                track,
                academic_year,
                version,
                int(parent_template_id) if parent_template_id is not None else None,
                str(change_note or "").strip(),
                now,
            ),
        )
        return int(cur.lastrowid)

    def get_template_lineage(self, template_id: int) -> list[dict[str, Any]]:
        """Chaîne de filiation de l'ancêtre le plus ancien jusqu'à la maquette demandée."""
        chain: list[dict[str, Any]] = []
        seen: set[int] = set()
        cur_id: int | None = int(template_id)
        while cur_id is not None and cur_id not in seen:
            seen.add(cur_id)
            tpl = self.get_template(cur_id)
            if not tpl:
                break
            chain.append(tpl)
            parent = tpl.get("parent_template_id")
            try:
                cur_id = int(parent) if parent is not None else None
            except (TypeError, ValueError):
                cur_id = None
        chain.reverse()
        return chain

    def format_template_provenance(self, template_id: int) -> str:
        """Texte lisible pour PDF / UI : id, année, version, filiation."""
        tpl = self.get_template(int(template_id))
        if not tpl:
            return ""
        parts = [
            f"Maquette #{int(tpl['id'])}",
            str(tpl.get("academic_year") or "").strip(),
            f"v{str(tpl.get('version') or '1').strip() or '1'}",
        ]
        head = " · ".join(p for p in parts if p)
        lineage = self.get_template_lineage(int(template_id))
        if len(lineage) > 1:
            ancestors = " → ".join(
                f"#{int(t['id'])} ({t.get('academic_year') or '?'} v{t.get('version') or '1'})"
                for t in lineage[:-1]
            )
            head += f" — issue de {ancestors}"
        note = str(tpl.get("change_note") or "").strip()
        if note:
            head += f" — {note}"
        return head

    def find_template_for_year_level_track(
        self, *, academic_year: str, level: str, track: str
    ) -> dict[str, Any] | None:
        ay = str(academic_year or "").strip()
        lv = normalize_level(level)
        tr = normalize_track_acronym(track)
        if not ay or not lv or not tr:
            return None
        row = self.db.query_one(
            """
            SELECT * FROM templates
            WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)
              AND UPPER(TRIM(IFNULL(level, ''))) = ?
              AND UPPER(TRIM(IFNULL(track, ''))) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (ay, lv, tr),
        )
        return dict(row) if row else None

    def build_template_snapshot(self, template_id: int) -> dict[str, Any]:
        """Instantané structurel pour archivage lors d'un export de relevé."""
        tpl = self.get_template(int(template_id)) or {}
        courses = self.list_template_courses(int(template_id))
        lineage = self.get_template_lineage(int(template_id))
        return {
            "template_id": int(template_id),
            "name": tpl.get("name"),
            "academic_year": tpl.get("academic_year"),
            "level": tpl.get("level"),
            "track": tpl.get("track"),
            "version": tpl.get("version"),
            "parent_template_id": tpl.get("parent_template_id"),
            "change_note": tpl.get("change_note"),
            "lineage": [
                {
                    "id": int(t["id"]),
                    "academic_year": t.get("academic_year"),
                    "version": t.get("version"),
                    "name": t.get("name"),
                }
                for t in lineage
            ],
            "courses": [
                {
                    "course_id": int(c["course_id"]),
                    "code": c.get("code"),
                    "name": c.get("name"),
                    "block_name": c.get("block_name"),
                    "global_coefficient": c.get("global_coefficient"),
                    "display_order": c.get("display_order"),
                    "optional": c.get("optional"),
                    "ects": c.get("ects"),
                }
                for c in courses
            ],
        }

    def log_transcript_export(
        self,
        *,
        student_id: int,
        template_id: int,
        view_session: str,
        file_path: str = "",
        snapshot: dict[str, Any] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        snap = snapshot if snapshot is not None else self.build_template_snapshot(int(template_id))
        cur = self.db.execute(
            """
            INSERT INTO transcript_exports(
                student_id, template_id, view_session, generated_at, file_path, template_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(student_id),
                int(template_id),
                str(view_session or "s1").lower(),
                now,
                str(file_path or "").strip(),
                json.dumps(snap, ensure_ascii=False),
            ),
        )
        return int(cur.lastrowid)

    def has_final_jury_session(self, template_id: int) -> bool:
        return self.get_final_jury_session_id(int(template_id)) is not None

    def get_final_jury_session_id(self, template_id: int) -> int | None:
        for s in self.list_jury_sessions(int(template_id)):
            if str(s.get("session_kind") or "").strip().upper() == "FINAL":
                return int(s["id"])
        return None

    def upsert_ue_transcript_session(
        self,
        student_id: int,
        course_id: int,
        *,
        academic_year: str,
        view_session: str,
        source_template_id: int | None = None,
    ) -> None:
        """Session / année d'origine d'une UE (ex. ECTS conservés après redoublement)."""
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        vs = "s2" if str(view_session or "s1").lower() == "s2" else "s1"
        self.db.execute(
            """
            INSERT INTO ue_transcript_sessions(
                student_id, course_id, academic_year, view_session,
                source_template_id, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, course_id) DO UPDATE SET
                academic_year = excluded.academic_year,
                view_session = excluded.view_session,
                source_template_id = excluded.source_template_id,
                recorded_at = excluded.recorded_at
            """,
            (
                int(student_id),
                int(course_id),
                str(academic_year or "").strip(),
                vs,
                int(source_template_id) if source_template_id is not None else None,
                now,
            ),
        )

    def delete_ue_transcript_session(self, student_id: int, course_id: int) -> None:
        self.db.execute(
            "DELETE FROM ue_transcript_sessions WHERE student_id = ? AND course_id = ?",
            (int(student_id), int(course_id)),
        )

    def get_ue_transcript_session(
        self, student_id: int, course_id: int
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            """
            SELECT * FROM ue_transcript_sessions
            WHERE student_id = ? AND course_id = ?
            """,
            (int(student_id), int(course_id)),
        )
        return dict(row) if row else None

    def student_eligible_for_ranking(self, student_id: int, template_id: int) -> bool:
        """Pas de classement si l'étudiant a au moins une UE en seconde session."""
        return not self.student_has_second_session_presence(
            int(student_id), int(template_id)
        )

    def get_ue_transcript_session_label(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        default_view_session: str,
        default_academic_year: str,
    ) -> str:
        """Libellé « S1 2024/2025 » pour une UE (report redoublement ou session courante)."""
        from .jury_reports import format_transcript_session_label

        rec = self.get_ue_transcript_session(int(student_id), int(course_id))
        if rec and str(rec.get("academic_year") or "").strip():
            return format_transcript_session_label(
                str(rec.get("view_session") or "s1"),
                str(rec.get("academic_year") or ""),
            )

        tpl = self.get_template(int(template_id)) or {}
        cur_ay = str(tpl.get("academic_year") or default_academic_year or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        cid = int(course_id)

        for enr in reversed(self.list_enrollments_for_student(int(student_id))):
            ay = str(enr.get("academic_year") or "").strip()
            if not ay or (cur_ay and ay >= cur_ay):
                continue
            if str(enr.get("level") or "").strip().upper() != lv:
                continue
            ptid = int(enr["template_id"])
            if not any(
                int(c["course_id"]) == cid for c in self.list_template_courses(ptid)
            ):
                continue
            data = self.get_student_result_summary(
                ptid, view_session="mixed", include_all_students=True
            )
            row = next(
                (r for r in data if int(r.get("student_id") or 0) == int(student_id)),
                None,
            )
            if row is None:
                continue
            d = (row.get("ue_detail") or {}).get(cid) or {}
            disp = str(d.get("display") or "").strip()
            use_s2_hist = bool(d.get("use_s2"))
            grade = d.get("s2") if use_s2_hist else d.get("s1")
            passed = disp == "VAL" or (
                grade is not None and float(grade) >= 10.0 and disp not in ("DEF", "ABJ")
            )
            if not passed:
                continue
            vs = "s2" if use_s2_hist else "s1"
            return format_transcript_session_label(vs, ay)

        return format_transcript_session_label(
            str(default_view_session or "s1"),
            str(default_academic_year or cur_ay),
        )

    def get_track_director(
        self, academic_year: str, level: str, track: str
    ) -> dict[str, Any] | None:
        """Premier responsable renseigné du parcours (slot 0 par défaut)."""
        directors = self.list_track_directors(academic_year, level, track)
        for row in directors:
            if str(row.get("last_name") or "").strip() or str(row.get("first_name") or "").strip():
                return dict(row)
        return dict(directors[0]) if directors and directors[0] else None

    def list_track_directors(
        self, academic_year: str, level: str, track: str
    ) -> list[dict[str, Any]]:
        """Responsables du parcours (1 slot en M1, 2 en M2), ordonnés par slot."""
        from ..core.master_team import ROLE_TRACK, track_director_slot_count

        ay = str(academic_year or "").strip()
        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        n = track_director_slot_count(lv)
        matching = [
            dict(row)
            for row in self.list_master_team_members(ay, role_kind=ROLE_TRACK)
            if str(row.get("level") or "").strip().upper() == lv
            and str(row.get("track") or "").strip().upper() == tr
        ]
        by_slot: dict[int, dict[str, Any]] = {}
        overflow: list[dict[str, Any]] = []
        for row in matching:
            slot = int(row.get("display_order") or 0)
            if 0 <= slot < n and slot not in by_slot:
                by_slot[slot] = row
            else:
                overflow.append(row)
        for row in overflow:
            for slot in range(n):
                if slot not in by_slot:
                    by_slot[slot] = row
                    break
        return [by_slot.get(i, {}) for i in range(n)]

    def transcript_header_emails(self, template_id: int) -> list[str]:
        """E-mails des responsables de parcours (M1P/M1C, M2…), y compris si seul le mail est renseigné."""
        tpl = self.get_template(int(template_id)) or {}
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        tr = str(tpl.get("track") or "").strip().upper()
        from .contact_emails import primary_email

        emails: list[str] = []
        for director in self.list_track_directors(ay, lv, tr):
            if not director.get("id"):
                continue
            em = primary_email(director)
            if em and em not in emails:
                emails.append(em)
        return emails

    def jury_notification_cc_emails(self, template_id: int) -> list[str]:
        """Cc des mails jury → étudiants : directeurs, responsables de parcours et secrétariat."""
        tpl = self.get_template(int(template_id)) or {}
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        tr = str(tpl.get("track") or "").strip().upper()
        from .contact_emails import primary_email

        emails: list[str] = []
        seen: set[str] = set()

        def _add(row: dict[str, Any] | None) -> None:
            if not row:
                return
            if not str(row.get("last_name") or "").strip() and not str(
                row.get("first_name") or ""
            ).strip():
                return
            em = primary_email(row)
            if em and em.lower() not in seen:
                seen.add(em.lower())
                emails.append(em)

        for director in self.list_mention_directors(ay):
            _add(director)
        for director in self.list_track_directors(ay, lv, tr):
            _add(director)
        for secretary in self.secretariats_for_track(ay, lv, tr):
            _add(secretary)
        return emails

    def _students_scored_for_ranking(
        self, template_id: int, *, view_session: str
    ) -> list[tuple[int, float]]:
        tid = int(template_id)
        vs = str(view_session or "s2").lower()
        rows = self.get_student_result_summary(
            tid,
            view_session=vs,
            include_all_students=(vs == "s2"),
        )
        scored: list[tuple[int, float]] = []
        for r in rows:
            sid = int(r["student_id"])
            if not self.student_eligible_for_ranking(sid, tid):
                continue
            g = r.get("global_with_jury")
            if g is None:
                continue
            scored.append((sid, float(g)))
        return scored

    @staticmethod
    def _rank_from_scored(scored: list[tuple[int, float]], student_id: int) -> int | None:
        ordered = sorted(scored, key=lambda item: (-item[1], item[0]))
        want = int(student_id)
        for rank, (sid, _) in enumerate(ordered, 1):
            if sid == want:
                return rank
        return None

    def student_track_rank(
        self, template_id: int, student_id: int, *, view_session: str = "s2"
    ) -> int | None:
        """Classement dans le parcours (maquette / template)."""
        tid, want = int(template_id), int(student_id)
        if not self.student_eligible_for_ranking(want, tid):
            return None
        return self._rank_from_scored(
            self._students_scored_for_ranking(tid, view_session=view_session),
            want,
        )

    def student_global_rank(
        self, template_id: int, student_id: int, *, view_session: str = "s2"
    ) -> int | None:
        """Alias historique — classement parcours."""
        return self.student_track_rank(
            int(template_id), int(student_id), view_session=view_session
        )

    def student_cohort_rank(
        self, template_id: int, student_id: int, *, view_session: str = "s2"
    ) -> int | None:
        """Classement dans la cohorte (tous parcours du même niveau et millésime)."""
        tid, want = int(template_id), int(student_id)
        if not self.student_eligible_for_ranking(want, tid):
            return None
        tpl = self.get_template(tid) or {}
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        if not ay or not lv:
            return None
        vs = str(view_session or "s2").lower()
        by_student: dict[int, float] = {}
        for t in self.list_templates_for_year_level(ay, lv):
            ptid = int(t["id"])
            for sid, grade in self._students_scored_for_ranking(ptid, view_session=vs):
                prev = by_student.get(sid)
                if prev is None or float(grade) > float(prev):
                    by_student[sid] = float(grade)
        return self._rank_from_scored(list(by_student.items()), want)

    def list_transcript_exports(
        self, *, student_id: int | None = None, template_id: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(int(student_id))
        if template_id is not None:
            clauses.append("template_id = ?")
            params.append(int(template_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query_all(
            f"""
            SELECT * FROM transcript_exports
            {where}
            ORDER BY generated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        )
        return [dict(r) for r in rows]

    def rename_template(self, template_id: int, new_name: str) -> None:
        self.db.execute(
            "UPDATE templates SET name = ? WHERE id = ?",
            (str(new_name).strip(), int(template_id)),
        )

    def update_template_metadata(
        self,
        template_id: int,
        *,
        name: str,
        level: str,
        track: str,
        academic_year: str,
        version: str = "1",
    ) -> None:
        """Met à jour les champs structurés d'une maquette (année/niveau/parcours/nom/version)."""
        self.db.execute(
            """
            UPDATE templates
            SET name = ?, level = ?, track = ?, academic_year = ?, version = ?
            WHERE id = ?
            """,
            (
                str(name or "").strip(),
                str(level or "").strip(),
                str(track or "").strip(),
                str(academic_year or "").strip(),
                str(version or "").strip() or "1",
                int(template_id),
            ),
        )

    def clone_template(
        self,
        source_template_id: int,
        *,
        name: str,
        academic_year: str,
        version: str,
        level: str | None = None,
        track: str | None = None,
        change_note: str = "",
    ) -> int:
        """Duplique une maquette (structure UE/blocs/ordre/optional), sans inscriptions ni notes."""
        src = self.db.query_one("SELECT * FROM templates WHERE id = ?", (int(source_template_id),))
        if not src:
            raise ValueError("Source template not found")
        lv = str(level if level is not None else (src["level"] or "")).strip()
        tr = str(track if track is not None else (src["track"] or "")).strip()
        src_ay = str(src["academic_year"] or "").strip()
        tgt_ay = str(academic_year).strip()
        fork_courses = bool(src_ay and tgt_ay and src_ay != tgt_ay)
        new_id = self.add_template(
            str(name).strip(),
            lv,
            tr,
            str(academic_year).strip(),
            str(version).strip() or "1",
            parent_template_id=int(source_template_id),
            change_note=change_note,
        )
        rows = self.list_template_courses(int(source_template_id))
        for r in rows:
            cid = int(r["course_id"])
            if fork_courses:
                cid = self._fork_course_for_target_millésime(cid, tgt_ay)
            self.add_course_to_template(
                int(new_id),
                cid,
                str(r.get("block_name") or ""),
                float(r.get("global_coefficient") or 1.0),
                int(r.get("display_order") or 0),
                int(r.get("optional") or 0),
                int(r.get("free_ue") or 0),
            )
        return int(new_id)

    def rollover_template_to_year(
        self,
        source_template_id: int,
        target_academic_year: str,
        *,
        change_note: str = "",
    ) -> int:
        """Report annuel : clone la maquette vers un nouveau millésime (version 1, lien parent)."""
        from ..core.parcours import suggested_maquette_name

        src = self.get_template(int(source_template_id))
        if not src:
            raise ValueError("Maquette source introuvable.")
        ay = str(target_academic_year or "").strip()
        if not ay:
            raise ValueError("L'année universitaire cible est obligatoire.")
        lv = str(src.get("level") or "").strip()
        tr = str(src.get("track") or "").strip()
        existing = self.find_template_for_year_level_track(academic_year=ay, level=lv, track=tr)
        if existing:
            raise ValueError(
                f"Une maquette existe déjà pour {ay} {lv} {tr} (#{int(existing['id'])})."
            )
        name = suggested_maquette_name(ay, lv, tr) or str(src.get("name") or f"Maquette #{source_template_id}")
        note = str(change_note or "").strip() or f"Report depuis {src.get('academic_year') or '?'} (maquette #{int(source_template_id)})"
        return self.clone_template(
            int(source_template_id),
            name=name,
            academic_year=ay,
            version="1",
            change_note=note,
        )

    def rollover_all_templates_for_year(
        self,
        source_academic_year: str,
        target_academic_year: str,
        *,
        change_note: str = "",
    ) -> tuple[list[int], list[str]]:
        """Reporte toutes les maquettes d'un millésime vers le suivant. Retourne (ids créés, erreurs)."""
        src_ay = str(source_academic_year or "").strip()
        tgt_ay = str(target_academic_year or "").strip()
        if not src_ay or not tgt_ay:
            raise ValueError("Les années source et cible sont obligatoires.")
        created: list[int] = []
        errors: list[str] = []
        sources = [
            t
            for t in self.list_templates()
            if str(t.get("academic_year") or "").strip() == src_ay
        ]
        if not sources:
            raise ValueError(f"Aucune maquette pour le millésime {src_ay}.")
        for src in sources:
            tid = int(src["id"])
            label = f"{src.get('level', '')} {src.get('track', '')}".strip() or str(src.get("name") or tid)
            try:
                new_id = self.rollover_template_to_year(
                    tid,
                    tgt_ay,
                    change_note=change_note or f"Report annuel {src_ay} → {tgt_ay}",
                )
                created.append(int(new_id))
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
        return created, errors

    def delete_template(self, template_id: int) -> None:
        """
        Supprime une maquette.

        Effets :
        - `template_courses` et `enrollments` sont supprimés en cascade (FK).
        - Les cours (bibliothèque) et leurs assessments ne sont pas supprimés.
        """
        self.db.execute("DELETE FROM templates WHERE id = ?", (template_id,))

    def list_template_courses(self, template_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT tc.*, c.code, c.name, c.ects, c.hours_total, c.mne_module_code, c.code_other
            FROM template_courses tc
            JOIN courses c ON c.id = tc.course_id
            WHERE tc.template_id = ?
            ORDER BY tc.display_order, c.mne_module_code, c.code
            """,
            (template_id,),
        )
        return [dict(r) for r in rows]

    def add_course_to_template(
        self,
        template_id: int,
        course_id: int,
        block_name: str = "",
        global_coefficient: float | None = None,
        display_order: int = 0,
        optional: int = 0,
        free_ue: int = 0,
    ) -> None:
        course = self.get_course(int(course_id)) or {}
        coef = _placement_coefficient_for_ects(
            float(course.get("ects") or 0), global_coefficient
        )
        self.db.execute(
            """
            INSERT OR IGNORE INTO template_courses(
                template_id, course_id, block_name, global_coefficient, display_order, optional, free_ue
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (template_id, course_id, block_name, coef, display_order, optional, free_ue),
        )

    def remove_course_from_template(self, template_id: int, course_id: int) -> None:
        self.db.execute(
            "DELETE FROM template_courses WHERE template_id = ? AND course_id = ?",
            (template_id, course_id),
        )

    def update_template_course_placement(
        self,
        template_id: int,
        course_id: int,
        *,
        block_name: str = "",
        global_coefficient: float | None = None,
        display_order: int = 0,
        optional: int = 0,
        free_ue: int = 0,
    ) -> None:
        course = self.get_course(int(course_id)) or {}
        coef = _placement_coefficient_for_ects(
            float(course.get("ects") or 0), global_coefficient
        )
        self.db.execute(
            """
            UPDATE template_courses SET
                block_name = ?,
                global_coefficient = ?,
                display_order = ?,
                optional = ?,
                free_ue = ?
            WHERE template_id = ? AND course_id = ?
            """,
            (block_name, coef, display_order, optional, free_ue, template_id, course_id),
        )

    def sync_template_course_coefficients_from_ects(
        self, template_id: int | None = None
    ) -> int:
        """Aligne `global_coefficient` sur les ECTS de chaque UE."""
        if template_id is not None:
            rows = self.db.query_all(
                """
                SELECT tc.template_id, tc.course_id, c.ects
                FROM template_courses tc
                JOIN courses c ON c.id = tc.course_id
                WHERE tc.template_id = ?
                """,
                (int(template_id),),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT tc.template_id, tc.course_id, c.ects
                FROM template_courses tc
                JOIN courses c ON c.id = tc.course_id
                """
            )
        updated = 0
        for row in rows:
            ects = float(row["ects"] or 0)
            if ects <= 0:
                continue
            self.db.execute(
                """
                UPDATE template_courses
                SET global_coefficient = ?
                WHERE template_id = ? AND course_id = ?
                """,
                (ects, int(row["template_id"]), int(row["course_id"])),
            )
            updated += 1
        return updated

    def is_template_course_free_ue(self, template_id: int, course_id: int) -> bool:
        row = self.db.query_one(
            """
            SELECT free_ue FROM template_courses
            WHERE template_id = ? AND course_id = ?
            """,
            (int(template_id), int(course_id)),
        )
        return bool(int(row["free_ue"] or 0)) if row else False

    def has_ue_ects_validation(self, student_id: int, template_id: int, course_id: int) -> bool:
        row = self.db.query_one(
            """
            SELECT 1 FROM ue_ects_validations
            WHERE student_id = ? AND template_id = ? AND course_id = ?
            """,
            (int(student_id), int(template_id), int(course_id)),
        )
        return row is not None

    def set_ue_ects_validation(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        validated: bool,
        comment: str = "",
    ) -> None:
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if not validated:
            self.db.execute(
                """
                DELETE FROM ue_ects_validations
                WHERE student_id = ? AND template_id = ? AND course_id = ?
                """,
                (sid, tid, cid),
            )
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.db.execute(
            """
            INSERT INTO ue_ects_validations(student_id, template_id, course_id, validated_at, comment)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, course_id) DO UPDATE SET
                validated_at = excluded.validated_at,
                comment = excluded.comment
            """,
            (sid, tid, cid, now, str(comment or "").strip()),
        )

    def has_ue_jury_floor_waiver(
        self, student_id: int, template_id: int, course_id: int
    ) -> bool:
        """Dérogation jury : l'UE peut être compensée malgré une note étudiant < 7."""
        row = self.db.query_one(
            """
            SELECT 1 FROM ue_jury_floor_waivers
            WHERE student_id = ? AND template_id = ? AND course_id = ?
            """,
            (int(student_id), int(template_id), int(course_id)),
        )
        return row is not None

    def set_ue_jury_floor_waiver(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        waived: bool,
        comment: str = "",
    ) -> None:
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if not waived:
            self.db.execute(
                """
                DELETE FROM ue_jury_floor_waivers
                WHERE student_id = ? AND template_id = ? AND course_id = ?
                """,
                (sid, tid, cid),
            )
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.db.execute(
            """
            INSERT INTO ue_jury_floor_waivers(student_id, template_id, course_id, waived_at, comment)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, course_id) DO UPDATE SET
                waived_at = excluded.waived_at,
                comment = excluded.comment
            """,
            (sid, tid, cid, now, str(comment or "").strip()),
        )

    def has_block_jury_validation_waiver(
        self, student_id: int, template_id: int, block_name: str
    ) -> bool:
        """Dérogation jury : le bloc peut être validé malgré une moyenne < 10."""
        bn = (block_name or "").strip()
        row = self.db.query_one(
            """
            SELECT 1 FROM block_jury_validation_waivers
            WHERE student_id = ? AND template_id = ? AND block_name = ?
            """,
            (int(student_id), int(template_id), bn),
        )
        return row is not None

    def set_block_jury_validation_waiver(
        self,
        student_id: int,
        template_id: int,
        block_name: str,
        *,
        waived: bool,
        comment: str = "",
    ) -> None:
        sid, tid = int(student_id), int(template_id)
        bn = (block_name or "").strip()
        if not waived:
            self.db.execute(
                """
                DELETE FROM block_jury_validation_waivers
                WHERE student_id = ? AND template_id = ? AND block_name = ?
                """,
                (sid, tid, bn),
            )
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.db.execute(
            """
            INSERT INTO block_jury_validation_waivers(
                student_id, template_id, block_name, waived_at, comment
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, block_name) DO UPDATE SET
                waived_at = excluded.waived_at,
                comment = excluded.comment
            """,
            (sid, tid, bn, now, str(comment or "").strip()),
        )

    def list_enrollments_for_student(self, student_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT e.*, t.name, t.academic_year, t.level, t.track
            FROM enrollments e
            JOIN templates t ON t.id = e.template_id
            WHERE e.student_id = ?
            ORDER BY t.academic_year, t.level, t.name
            """,
            (int(student_id),),
        )
        return [dict(r) for r in rows]

    def enroll_student(self, student_id: int, template_id: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO enrollments(student_id, template_id) VALUES (?, ?)",
            (student_id, template_id),
        )

    def sync_enrollments_for_academic_year(self, academic_year: str) -> tuple[int, int]:
        """
        Inscrit automatiquement les étudiants dans les maquettes correspondant à :
        - même `academic_year` (si vide côté étudiant, on prend l'année passée en param)
        - même `level` et `track`

        Retourne (matched_students, created_enrollments).
        """
        ay = str(academic_year or "").strip()
        if not ay:
            return 0, 0
        templates = [t for t in self.list_templates() if str(t.get("academic_year") or "").strip() == ay]
        by_key: dict[tuple[str, str], int] = {}
        for t in templates:
            lv = str(t.get("level") or "").strip().upper()
            tr = str(t.get("track") or "").strip().upper()
            if lv and tr:
                by_key[(lv, tr)] = int(t["id"])

        students = self.list_students()
        matched = 0
        created = 0
        for s in students:
            if not is_student_active(s):
                continue
            s_ay = str(s.get("academic_year") or "").strip()
            if s_ay and s_ay != ay:
                continue
            lv = normalize_level(s.get("level"))
            tr = normalize_track_acronym(s.get("track"))
            if not (lv and tr):
                continue
            tid = by_key.get((lv, tr))
            if tid is None:
                continue
            matched += 1
            cur = self.db.execute(
                "INSERT OR IGNORE INTO enrollments(student_id, template_id) VALUES (?, ?)",
                (int(s["id"]), int(tid)),
            )
            # sqlite3 doesn't give affected rows reliably with OR IGNORE; use changes()
            ch = self.db.query_one("SELECT changes() AS c")
            if ch and int(ch["c"]) > 0:
                created += 1
        return matched, created

    def sync_enrollments_for_student(self, student_id: int) -> int:
        """
        Inscrit l'étudiant à la maquette de son parcours pour son année universitaire
        (même logique que ``sync_enrollments_for_academic_year`` : une maquette par couple niveau/parcours).

        Retourne 1 si une nouvelle inscription a été créée, 0 sinon.
        """
        s = self.get_student(student_id)
        if not s or not is_student_active(s):
            return 0
        if is_erasmus_student(s):
            return 0
        s_ay = str(s.get("academic_year") or "").strip()
        if not s_ay:
            return 0
        lv = normalize_level(s.get("level"))
        tr = normalize_track_acronym(s.get("track"))
        if not (lv and tr):
            return 0
        templates = [
            t for t in self.list_templates() if str(t.get("academic_year") or "").strip() == s_ay
        ]
        by_key: dict[tuple[str, str], int] = {}
        for t in templates:
            t_lv = str(t.get("level") or "").strip().upper()
            t_tr = str(t.get("track") or "").strip().upper()
            if t_lv and t_tr:
                by_key[(t_lv, t_tr)] = int(t["id"])
        tid = by_key.get((lv, tr))
        if tid is None:
            return 0
        self.db.execute(
            "INSERT OR IGNORE INTO enrollments(student_id, template_id) VALUES (?, ?)",
            (int(student_id), int(tid)),
        )
        ch = self.db.query_one("SELECT changes() AS c")
        return 1 if ch and int(ch["c"]) > 0 else 0

    def list_students_for_course_template(
        self, template_id: int, course_id: int
    ) -> list[dict[str, Any]]:
        """Étudiants inscrits à la maquette qui contient ce cours."""
        link = self.db.query_one(
            """
            SELECT 1 FROM template_courses
            WHERE template_id = ? AND course_id = ?
            """,
            (int(template_id), int(course_id)),
        )
        if not link:
            return []
        return self.list_students_for_template(int(template_id))

    def list_students_for_template(self, template_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            f"""
            SELECT s.*
            FROM enrollments e
            JOIN students s ON s.id = e.student_id
            WHERE e.template_id = ?
              AND {sql_student_is_active("s")}
            ORDER BY s.last_name, s.first_name
            """,
            (template_id,),
        )
        by_id = {int(r["id"]): dict(r) for r in rows}
        for s in self.list_erasmus_students_for_template(int(template_id)):
            by_id[int(s["id"])] = s
        return sorted(
            by_id.values(),
            key=lambda s: (str(s.get("last_name") or ""), str(s.get("first_name") or "")),
        )

    def list_student_erasmus_course_ids(
        self, student_id: int, academic_year: str = ""
    ) -> list[int]:
        ay = str(academic_year or "").strip()
        if ay:
            rows = self.db.query_all(
                """
                SELECT course_id FROM student_course_enrollments
                WHERE student_id = ? AND TRIM(academic_year) = TRIM(?)
                ORDER BY course_id
                """,
                (int(student_id), ay),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT course_id FROM student_course_enrollments
                WHERE student_id = ?
                ORDER BY course_id
                """,
                (int(student_id),),
            )
        return [int(r["course_id"]) for r in rows]

    def list_student_erasmus_courses(
        self, student_id: int, academic_year: str = ""
    ) -> list[dict[str, Any]]:
        ids = self.list_student_erasmus_course_ids(int(student_id), academic_year)
        out: list[dict[str, Any]] = []
        for cid in ids:
            row = self.get_course(int(cid))
            if row:
                out.append(dict(row))
        return sorted(out, key=lambda c: str(c.get("code") or ""))

    def set_student_erasmus_courses(
        self, student_id: int, academic_year: str, course_ids: list[int]
    ) -> None:
        ay = str(academic_year or "").strip()
        sid = int(student_id)
        self.db.execute(
            "DELETE FROM student_course_enrollments WHERE student_id = ? AND TRIM(academic_year) = TRIM(?)",
            (sid, ay),
        )
        seen: set[int] = set()
        for raw in course_ids:
            try:
                cid = int(raw)
            except (TypeError, ValueError):
                continue
            if cid in seen:
                continue
            seen.add(cid)
            self.db.execute(
                """
                INSERT OR IGNORE INTO student_course_enrollments(student_id, course_id, academic_year)
                VALUES (?, ?, ?)
                """,
                (sid, cid, ay),
            )

    def list_available_erasmus_courses(self, academic_year: str) -> list[dict[str, Any]]:
        """UE proposées sur les maquettes du millésime (catalogue mobilité)."""
        ay = str(academic_year or "").strip()
        if not ay:
            return []
        rows = self.db.query_all(
            """
            SELECT DISTINCT c.*
            FROM template_courses tc
            JOIN templates t ON t.id = tc.template_id
            JOIN courses c ON c.id = tc.course_id
            WHERE TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
              AND NOT EXISTS (
                SELECT 1
                FROM template_courses tc2
                JOIN templates t2 ON t2.id = tc2.template_id
                WHERE tc2.course_id = c.id
                  AND TRIM(IFNULL(t2.academic_year, '')) != ''
                  AND TRIM(t2.academic_year) != TRIM(?)
              )
            ORDER BY c.code
            """,
            (ay, ay),
        )
        return [dict(r) for r in rows]

    def list_erasmus_students_for_course(
        self, course_id: int, academic_year: str = ""
    ) -> list[dict[str, Any]]:
        ay = str(academic_year or "").strip()
        if ay:
            rows = self.db.query_all(
                f"""
                SELECT DISTINCT s.*
                FROM students s
                JOIN student_course_enrollments e ON e.student_id = s.id
                WHERE e.course_id = ?
                  AND TRIM(e.academic_year) = TRIM(?)
                  AND LOWER(TRIM(COALESCE(s.mobility_type, ''))) = ?
                  AND {sql_student_is_active("s")}
                ORDER BY s.last_name, s.first_name
                """,
                (int(course_id), ay, MOBILITY_ERASMUS),
            )
        else:
            rows = self.db.query_all(
                f"""
                SELECT DISTINCT s.*
                FROM students s
                JOIN student_course_enrollments e ON e.student_id = s.id
                WHERE e.course_id = ?
                  AND LOWER(TRIM(COALESCE(s.mobility_type, ''))) = ?
                  AND {sql_student_is_active("s")}
                ORDER BY s.last_name, s.first_name
                """,
                (int(course_id), MOBILITY_ERASMUS),
            )
        return [dict(r) for r in rows]

    def list_erasmus_students_for_template(self, template_id: int) -> list[dict[str, Any]]:
        t_row = self.db.query_one("SELECT * FROM templates WHERE id = ?", (int(template_id),))
        if not t_row:
            return []
        ay = str(t_row["academic_year"] or "").strip()
        course_ids = [
            int(c["course_id"]) for c in self.list_template_courses(int(template_id))
        ]
        if not ay or not course_ids:
            return []
        ph = ",".join("?" * len(course_ids))
        rows = self.db.query_all(
            f"""
            SELECT DISTINCT s.*
            FROM students s
            JOIN student_course_enrollments e ON e.student_id = s.id
            WHERE e.course_id IN ({ph})
              AND TRIM(e.academic_year) = TRIM(?)
              AND LOWER(TRIM(COALESCE(s.mobility_type, ''))) = ?
              AND {sql_student_is_active("s")}
            ORDER BY s.last_name, s.first_name
            """,
            (*course_ids, ay, MOBILITY_ERASMUS),
        )
        return [dict(r) for r in rows]

    def list_template_courses_for_student(
        self, student_id: int, template_id: int
    ) -> list[dict[str, Any]]:
        courses = self.list_template_courses(int(template_id))
        student = self.get_student(int(student_id)) or {}
        if not is_erasmus_student(student):
            return courses
        followed = set(self.list_student_erasmus_course_ids(int(student_id)))
        t_row = self.db.query_one("SELECT academic_year FROM templates WHERE id = ?", (int(template_id),))
        ay = str((t_row["academic_year"] if t_row else "") or "").strip()
        if ay:
            followed = set(self.list_student_erasmus_course_ids(int(student_id), ay))
        return [c for c in courses if int(c["course_id"]) in followed]

    def list_students_for_level(self, *, academic_year: str, level: str) -> list[dict[str, Any]]:
        """
        Tous les étudiants d'un niveau (M1/M2) pour un millésime donné.
        Utilisé pour les UE de tronc commun (saisie des notes sur tout le niveau).
        """
        ay = str(academic_year or "").strip()
        lv = str(level or "").strip().upper()
        if not ay or not lv:
            return []
        rows = self.db.query_all(
            f"""
            SELECT *
            FROM students
            WHERE {sql_student_is_active("students")}
              AND (TRIM(IFNULL(academic_year, '')) = TRIM(?) OR TRIM(IFNULL(academic_year, '')) = '')
              AND UPPER(TRIM(level)) = ?
            ORDER BY last_name, first_name
            """,
            (ay, lv),
        )
        return [dict(r) for r in rows]

    def list_template_ids_with_course(self, course_id: int, *, academic_year: str = "") -> list[int]:
        """
        Retourne les maquettes qui contiennent l'UE (`template_courses.course_id`).
        Si `academic_year` est renseigné, filtre sur ce millésime.
        """
        ay = str(academic_year or "").strip()
        if ay:
            rows = self.db.query_all(
                """
                SELECT DISTINCT t.id
                FROM templates t
                JOIN template_courses tc ON tc.template_id = t.id
                WHERE tc.course_id = ?
                  AND TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
                ORDER BY t.id
                """,
                (int(course_id), ay),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT DISTINCT t.id
                FROM templates t
                JOIN template_courses tc ON tc.template_id = t.id
                WHERE tc.course_id = ?
                ORDER BY t.id
                """,
                (int(course_id),),
            )
        return [int(r["id"]) for r in rows]

    def infer_levels_for_course(self, course_id: int, *, academic_year: str = "") -> list[str]:
        """
        Déduit les niveaux (M1/M2/…) associés à une UE en regardant les maquettes qui la contiennent.
        Utile quand une maquette n'a pas son champ `level` renseigné.
        """
        ay = str(academic_year or "").strip()
        if ay:
            rows = self.db.query_all(
                """
                SELECT DISTINCT UPPER(TRIM(IFNULL(t.level, ''))) AS level
                FROM templates t
                JOIN template_courses tc ON tc.template_id = t.id
                WHERE tc.course_id = ?
                  AND TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
                ORDER BY level
                """,
                (int(course_id), ay),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT DISTINCT UPPER(TRIM(IFNULL(t.level, ''))) AS level
                FROM templates t
                JOIN template_courses tc ON tc.template_id = t.id
                WHERE tc.course_id = ?
                ORDER BY level
                """,
                (int(course_id),),
            )
        out = []
        for r in rows:
            lv = str(r.get("level") or "").strip().upper()
            if lv:
                out.append(lv)
        return out

    def infer_academic_years_for_course(self, course_id: int) -> list[str]:
        """
        Déduit les millésimes associés à une UE en regardant les maquettes qui la contiennent.
        Utile si la maquette courante n'a pas son champ `academic_year` renseigné.
        """
        rows = self.db.query_all(
            """
            SELECT DISTINCT TRIM(IFNULL(t.academic_year, '')) AS ay
            FROM templates t
            JOIN template_courses tc ON tc.template_id = t.id
            WHERE tc.course_id = ?
            ORDER BY ay
            """,
            (int(course_id),),
        )
        out: list[str] = []
        for r in rows:
            ay = str(r.get("ay") or "").strip()
            if ay:
                out.append(ay)
        return out

    def list_templates_containing_course(
        self, course_id: int, *, academic_year: str = ""
    ) -> list[dict[str, Any]]:
        """Maquettes qui incluent ce cours (optionnellement filtrées par millésime)."""
        out: list[dict[str, Any]] = []
        for tid in self.list_template_ids_with_course(int(course_id), academic_year=academic_year):
            t = self.get_template(int(tid))
            if t:
                out.append(t)
        return out

    def student_templates_for_course(
        self, course_id: int, template_ids: list[int]
    ) -> dict[int, int]:
        """Pour chaque étudiant inscrit : maquette d'inscription parmi celles qui contiennent l'UE."""
        if not template_ids:
            return {}
        out: dict[int, int] = {}
        for tid in template_ids:
            for s in self.list_students_for_template(int(tid)):
                sid = int(s["id"])
                if sid not in out:
                    out[sid] = int(tid)
        return out

    def student_template_for_course(
        self,
        student_id: int,
        course_id: int,
        *,
        academic_year: str = "",
    ) -> int | None:
        """Première maquette (inscription + UE) pour cet étudiant."""
        for tid in self.list_template_ids_with_course(
            int(course_id), academic_year=academic_year
        ):
            row = self.db.query_one(
                "SELECT 1 FROM enrollments WHERE student_id = ? AND template_id = ?",
                (int(student_id), int(tid)),
            )
            if row:
                return int(tid)
        return None

    def list_students_for_course_in_templates(
        self, course_id: int, template_ids: list[int], *, academic_year: str = ""
    ) -> list[dict[str, Any]]:
        """
        Étudiants inscrits à au moins une maquette de `template_ids`.

        Note: on passe volontairement par une union Python (via `list_students_for_template`)
        pour être robuste aux cas où le lien SQL `template_courses` / `enrollments` serait
        incomplet ou temporairement incohérent.
        """
        if not template_ids:
            return []
        by_id: dict[int, dict[str, Any]] = {}
        for tid in template_ids:
            for s in self.list_students_for_template(int(tid)):
                by_id[int(s["id"])] = s
        ay = str(academic_year or "").strip()
        if not ay and template_ids:
            t_row = self.db.query_one(
                "SELECT academic_year FROM templates WHERE id = ?",
                (int(template_ids[0]),),
            )
            ay = str((t_row["academic_year"] if t_row else "") or "").strip()
        for s in self.list_erasmus_students_for_course(int(course_id), ay):
            by_id[int(s["id"])] = s
        return sorted(
            by_id.values(),
            key=lambda s: (str(s.get("last_name") or ""), str(s.get("first_name") or "")),
        )

    def list_students_matching_template(self, template_id: int) -> list[dict[str, Any]]:
        """Étudiants correspondant à (academic_year, level, track) de la maquette."""
        t = self.db.query_one("SELECT * FROM templates WHERE id = ?", (int(template_id),))
        if not t:
            return []
        ay = str(t["academic_year"] or "").strip()
        lv = str(t["level"] or "").strip().upper()
        tr = str(t["track"] or "").strip().upper()
        rows = self.db.query_all(
            f"""
            SELECT *
            FROM students
            WHERE {sql_student_is_active("students")}
              AND (? = '' OR TRIM(academic_year) = TRIM(?))
              AND UPPER(TRIM(level)) = ?
              AND UPPER(TRIM(track)) = ?
            ORDER BY last_name, first_name
            """,
            (ay, ay, lv, tr),
        )
        return [dict(r) for r in rows]

    def list_students_for_enrollment_editor(self, template_id: int) -> list[dict[str, Any]]:
        """
        Étudiants proposés pour la gestion d'inscriptions : même millésime que la maquette
        (ou critères niveau/parcours si l'année de la maquette est vide), plus toute personne
        déjà inscrite à cette maquette (même autre année) pour ne pas les « perdre ».
        """
        tid = int(template_id)
        t_row = self.db.query_one("SELECT * FROM templates WHERE id = ?", (tid,))
        if not t_row:
            return []
        t = dict(t_row)
        ay = str(t.get("academic_year") or "").strip()
        enrolled_ids = [
            int(r["student_id"])
            for r in self.db.query_all(
                "SELECT student_id FROM enrollments WHERE template_id = ?", (tid,)
            )
        ]
        if ay:
            if enrolled_ids:
                ph = ",".join("?" * len(enrolled_ids))
                rows = self.db.query_all(
                    f"""
                    SELECT * FROM students
                    WHERE {sql_student_is_active("students")}
                      AND (
                        TRIM(IFNULL(academic_year, '')) = TRIM(?)
                        OR id IN ({ph})
                      )
                    ORDER BY last_name, first_name
                    """,
                    (ay, *enrolled_ids),
                )
            else:
                rows = self.db.query_all(
                    f"""
                    SELECT * FROM students
                    WHERE {sql_student_is_active("students")}
                      AND TRIM(IFNULL(academic_year, '')) = TRIM(?)
                    ORDER BY last_name, first_name
                    """,
                    (ay,),
                )
            return [dict(r) for r in rows]
        by_id: dict[int, dict[str, Any]] = {
            int(s["id"]): dict(s) for s in self.list_students_matching_template(tid)
        }
        for sid in enrolled_ids:
            if sid not in by_id:
                extra = self.get_student(sid)
                if extra:
                    by_id[sid] = dict(extra)
        return sorted(
            (s for s in by_id.values() if is_student_active(s)),
            key=lambda s: (
                str(s.get("last_name") or ""),
                str(s.get("first_name") or ""),
            ),
        )

    def student_matches_template_parcours(
        self, student: dict[str, Any], template_id: int
    ) -> bool:
        t_row = self.db.query_one("SELECT * FROM templates WHERE id = ?", (int(template_id),))
        if not t_row:
            return False
        t = dict(t_row)
        ay_t = str(t.get("academic_year") or "").strip()
        ay_s = str(student.get("academic_year") or "").strip()
        if ay_t and ay_s != ay_t:
            return False
        lv_t = str(t.get("level") or "").strip().upper()
        tr_t = str(t.get("track") or "").strip().upper()
        lv_s = str(student.get("level") or "").strip().upper()
        tr_s = str(student.get("track") or "").strip().upper()
        return lv_t == lv_s and tr_t == tr_s

    def set_enrollments_for_template(self, template_id: int, student_ids: list[int]) -> None:
        """Met à jour la liste d'inscrits d'une maquette (ajouts + suppressions)."""
        tid = int(template_id)
        desired = {int(x) for x in student_ids}
        current_rows = self.db.query_all("SELECT student_id FROM enrollments WHERE template_id = ?", (tid,))
        current = {int(r["student_id"]) for r in current_rows}
        to_add = sorted(desired - current)
        to_remove = sorted(current - desired)
        for sid in to_add:
            self.enroll_student(int(sid), tid)
        if to_remove:
            placeholders = ",".join("?" * len(to_remove))
            self.db.execute(
                f"DELETE FROM enrollments WHERE template_id = ? AND student_id IN ({placeholders})",
                (tid, *to_remove),
            )

    # Grades
    def get_grades_for_student_course(self, student_id: int, course_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT a.id AS assessment_id, a.name, a.kind, a.coefficient, a.session,
                   g.grade, g.status, g.locked, g.comment
            FROM assessments a
            LEFT JOIN grades g ON g.assessment_id = a.id AND g.student_id = ?
            WHERE a.course_id = ?
            ORDER BY a.session, a.display_order, a.id
            """,
            (student_id, course_id),
        )
        return [dict(r) for r in rows]

    def upsert_grade(
        self,
        student_id: int,
        assessment_id: int,
        grade: float | None,
        *,
        status: str = "OK",
        locked: int = 0,
        comment: str = "",
        trigger_carry_over: bool = True,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO grades(student_id, assessment_id, grade, status, locked, comment)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, assessment_id)
            DO UPDATE SET
                grade=excluded.grade,
                status=excluded.status,
                locked=excluded.locked,
                comment=excluded.comment
            """,
            (student_id, assessment_id, grade, status, int(locked or 0), comment),
        )

    def compute_course_average(self, student_id: int, course_id: int) -> float | None:
        # NOTE: la note "retenue" dépend uniquement de la décision d'envoi en S2
        # (les notes S2 n'interviennent pas en session 1 si l'étudiant n'a pas été envoyé).
        rows = self.get_grades_for_student_course(student_id, course_id)
        use_s2 = self.is_sent_to_second_session(student_id, self._current_template_for_course_average, course_id) if hasattr(self, "_current_template_for_course_average") else False
        return _compute_course_average_from_rows(rows, mode="final", use_session2=bool(use_s2))

    def compute_course_average_s1(self, student_id: int, course_id: int) -> float | None:
        rows = self.get_grades_for_student_course(student_id, course_id)
        return _compute_course_average_from_rows(rows, mode="s1")

    def compute_course_average_s2(
        self, student_id: int, course_id: int, template_id: int | None = None
    ) -> float | None:
        rows = self.get_grades_for_student_course(student_id, course_id)
        allow = self.is_second_session_carry_allowed(
            int(student_id), int(course_id), template_id
        )
        return _compute_course_average_from_rows(
            rows, mode="s2", allow_s1_reprise_carry=allow
        )

    def is_sent_to_second_session(self, student_id: int, template_id: int, course_id: int) -> bool:
        row = self.db.query_one(
            """
            SELECT sent
            FROM second_session_decisions
            WHERE student_id = ? AND template_id = ? AND course_id = ?
            """,
            (int(student_id), int(template_id), int(course_id)),
        )
        return bool(row and int(row["sent"]) == 1)

    def _template_ids_for_student_course(self, student_id: int, course_id: int) -> list[int]:
        sid, cid = int(student_id), int(course_id)
        out: list[int] = []
        for enr in self.list_enrollments_for_student(sid):
            tid = int(enr["template_id"])
            if any(int(c["course_id"]) == cid for c in self.list_template_courses(tid)):
                out.append(tid)
        return out

    def is_second_session_carry_allowed(
        self, student_id: int, course_id: int, template_id: int | None = None
    ) -> bool:
        """Report CC / reprises S1 → S2 : uniquement si envoi S2 (convocation) sur l'UE."""
        sid, cid = int(student_id), int(course_id)
        if template_id is not None:
            return self.is_sent_to_second_session(sid, int(template_id), cid)
        return any(
            self.is_sent_to_second_session(sid, tid, cid)
            for tid in self._template_ids_for_student_course(sid, cid)
        )

    def course_has_session2_activity(
        self, student_id: int, course_id: int, template_id: int | None = None
    ) -> bool:
        """Au moins une épreuve de session 2 renseignée (hors reprises S1 non convoquées)."""
        sid, cid = int(student_id), int(course_id)
        tids = (
            [int(template_id)]
            if template_id is not None
            else self._template_ids_for_student_course(sid, cid)
        )
        sent = any(self.is_sent_to_second_session(sid, tid, cid) for tid in tids)
        for r in self.get_grades_for_student_course(sid, cid):
            if int(r["session"]) != 2:
                continue
            if _grade_cell_empty(r.get("grade"), r.get("status")):
                continue
            if _is_s2_reprise_assessment_row(r) and not sent:
                continue
            return True
        return False

    def course_retains_session2_grades(
        self, student_id: int, template_id: int, course_id: int
    ) -> bool:
        """Retenir les notes S2 pour une UE : envoi S2 ou au moins une note S2 saisie."""
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if self.course_has_session2_activity(sid, cid, template_id=tid):
            return True
        return self.is_sent_to_second_session(sid, tid, cid)

    def student_has_second_session_presence(
        self, student_id: int, template_id: int
    ) -> bool:
        """Étudiant avec envoi S2 ou note S2 sur au moins une UE de la maquette."""
        sid, tid = int(student_id), int(template_id)
        for c in self.list_template_courses(tid):
            cid = int(c["course_id"])
            if self.is_sent_to_second_session(sid, tid, cid):
                return True
            if self.course_has_session2_activity(sid, cid, template_id=tid):
                return True
        return False

    def second_session_decision_locked(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        s1_jury: bool = False,
    ) -> bool:
        """
        Pas de nouvel envoi S2 si des notes de session 2 existent déjà.

        ``s1_jury=True`` : délibération de session 1 — l'envoi reste modifiable même si
        des notes S2 sont déjà en base (saisie anticipée, tests, reprises automatiques).
        """
        if s1_jury:
            return False
        return self.course_has_session2_activity(int(student_id), int(course_id))

    def can_set_second_session_decision(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        sent: bool,
        s1_jury: bool = False,
    ) -> bool:
        """Nouvel envoi S2 interdit si des notes de session 2 existent déjà (sauf jury S1)."""
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if sent and self.second_session_decision_locked(sid, tid, cid, s1_jury=s1_jury):
            return False
        return True

    def course_uses_session2_grades(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        view_session: str = "s2",
    ) -> bool:
        """
        Retenir les notes S2 en vue session 2 : envoi S2 enregistré ou épreuves S2 disponibles.
        """
        vs = str(view_session or "s1").strip().lower()
        if vs != "s2":
            return False
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if self.is_sent_to_second_session(sid, tid, cid):
            return True
        return self.course_has_session2_activity(sid, cid)

    def can_send_to_second_session(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        s1_jury: bool = False,
    ) -> bool:
        """Envoi en 2ᵉ session : impossible si l’UE a déjà des notes de session 2 (sauf jury S1)."""
        return not self.second_session_decision_locked(
            int(student_id), int(template_id), int(course_id), s1_jury=s1_jury
        )

    def course_session1_has_def(self, student_id: int, course_id: int) -> bool:
        """Vrai si au moins une épreuve de session 1 (hors S2) a le statut DEF pour cet étudiant et cette UE."""
        for r in self.get_grades_for_student_course(int(student_id), int(course_id)):
            if int(r["session"]) == 2:
                continue
            if not _grade_cell_empty(r.get("grade"), r.get("status")):
                if normalize_grade_status(r.get("status")) == STATUS_DEF:
                    return True
        return False

    def course_session2_has_def(self, student_id: int, course_id: int) -> bool:
        """DEF explicite sur une épreuve de session 2 (hors cases vides héritées)."""
        for r in self.get_grades_for_student_course(int(student_id), int(course_id)):
            if int(r["session"]) != 2:
                continue
            if _grade_cell_empty(r.get("grade"), r.get("status")):
                continue
            if normalize_grade_status(r.get("status")) == STATUS_DEF:
                return True
        return False

    def course_session1_has_abj(self, student_id: int, course_id: int) -> bool:
        """Vrai si au moins une épreuve de session 1 a le statut ABJ."""
        for r in self.get_grades_for_student_course(int(student_id), int(course_id)):
            if int(r["session"]) == 2:
                continue
            if not _grade_cell_empty(r.get("grade"), r.get("status")):
                if normalize_grade_status(r.get("status")) == STATUS_ABJ:
                    return True
        return False

    def course_session2_has_abj(self, student_id: int, course_id: int) -> bool:
        """ABJ explicite sur une épreuve de session 2 (hors cases vides héritées)."""
        for r in self.get_grades_for_student_course(int(student_id), int(course_id)):
            if int(r["session"]) != 2:
                continue
            if _grade_cell_empty(r.get("grade"), r.get("status")):
                continue
            if normalize_grade_status(r.get("status")) == STATUS_ABJ:
                return True
        return False

    def _course_session_blocking_status(
        self,
        student_id: int,
        course_id: int,
        *,
        session: int,
    ) -> str | None:
        """DEF ou ABJ explicite sur une épreuve de la session demandée (DEF prioritaire)."""
        sid, cid, target = int(student_id), int(course_id), int(session)
        has_abj = False
        for r in self.get_grades_for_student_course(sid, cid):
            if int(r["session"]) != target:
                continue
            if _grade_cell_empty(r.get("grade"), r.get("status")):
                continue
            st = normalize_grade_status(r.get("status"))
            if st == STATUS_DEF:
                return STATUS_DEF
            if st == STATUS_ABJ:
                has_abj = True
        return STATUS_ABJ if has_abj else None

    def _filled_grade_rows_for_view(
        self,
        rows: list[dict[str, Any]],
        *,
        view_session: str,
        use_s2: bool,
    ) -> list[dict[str, Any]]:
        vs = (view_session or "s1").strip().lower()
        has_s2 = any(int(r["session"]) == 2 for r in rows)
        if vs == "s2" and has_s2 and use_s2:
            pool = [r for r in rows if int(r["session"]) == 2]
        else:
            pool = [r for r in rows if int(r["session"]) != 2]
        return [r for r in pool if not _grade_cell_empty(r.get("grade"), r.get("status"))]

    def course_ue_display_label(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        view_session: str,
        session_average: float | None,
        sent_s2: bool,
        use_s2: bool | None = None,
    ) -> str | None:
        """
        Libellé non numérique pour l’onglet Résultats (DEF, ABJ, NEUT, VAL) ou None → afficher la note.
        """
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if self.has_ue_ects_validation(sid, tid, cid):
            return STATUS_VAL

        rows = self.get_grades_for_student_course(sid, cid)
        vs = (view_session or "s1").strip().lower()
        retain_s2 = (
            use_s2
            if use_s2 is not None
            else self.course_uses_session2_grades(sid, tid, cid, view_session=vs)
        )
        filled = self._filled_grade_rows_for_view(rows, view_session=vs, use_s2=retain_s2)

        if vs == "s1":
            block = self._course_session_blocking_status(sid, cid, session=1)
            if block:
                return block
        elif retain_s2:
            s2_block = self._course_session_blocking_status(sid, cid, session=2)
            if s2_block:
                return s2_block
            s2_avg = (
                session_average
                if session_average is not None
                else self.compute_course_average_s2(sid, cid, template_id=tid)
            )
            if not self.course_has_session2_activity(sid, cid, template_id=tid):
                block = self._course_session_blocking_status(sid, cid, session=1)
                if block:
                    return block
            elif s2_avg is None:
                block = self._course_session_blocking_status(sid, cid, session=1)
                if block:
                    return block
        else:
            # Vue S2 mais moyenne S1 encore affichée (pas de notes S2 retenues).
            if self.course_has_session2_activity(sid, cid, template_id=tid):
                s2_block = self._course_session_blocking_status(sid, cid, session=2)
                if s2_block:
                    return s2_block
            block = self._course_session_blocking_status(sid, cid, session=1)
            if block:
                return block

        if session_average is None and filled:
            statuses = {normalize_grade_status(r.get("status")) for r in filled}
            if statuses == {STATUS_ABJ}:
                return STATUS_ABJ
            if statuses == {STATUS_NEUT}:
                return STATUS_NEUT
            if statuses == {STATUS_VAL}:
                return STATUS_VAL

        return None

    def course_triggers_second_session(self, student_id: int, course_id: int) -> bool:
        """DEF ou ABJ en session 1 sur l'UE → convocation / envoi en 2ᵉ session (règlement FSO)."""
        sid, cid = int(student_id), int(course_id)
        return self.course_session1_has_def(sid, cid) or self.course_session1_has_abj(sid, cid)

    def sync_second_session_obligations(self, template_id: int) -> int:
        """
        Impose l'envoi S2 lorsqu'une UE a **DEF** ou **ABJ** en session 1 (règlement FSO).

        Ne retire pas les envois cochés manuellement par le jury (plusieurs UE possibles).
        Utiliser ``maybe_clear_second_session_without_trigger`` après correction d'un DEF/ABJ.
        """
        tid = int(template_id)
        students = self.list_students_for_template(tid)
        courses = self.list_template_courses(tid)
        n = 0
        for student in students:
            sid = int(student["id"])
            for c in courses:
                cid = int(c["course_id"])
                if not self.course_triggers_second_session(sid, cid):
                    continue
                if self.is_sent_to_second_session(sid, tid, cid):
                    continue
                if self.second_session_decision_locked(sid, tid, cid):
                    continue
                self.set_second_session_decision(sid, tid, cid, sent=True)
                n += 1
        return n

    def maybe_clear_second_session_without_trigger(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
    ) -> bool:
        """
        Retire l'envoi S2 si l'UE n'a plus de DEF/ABJ S1 et aucune note S2 saisie.
        Appelé après correction de notes — ne touche pas aux envois manuels sur d'autres UE.
        """
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if self.course_triggers_second_session(sid, cid):
            return False
        if self.second_session_decision_locked(sid, tid, cid):
            return False
        if not self.is_sent_to_second_session(sid, tid, cid):
            return False
        self.set_second_session_decision(sid, tid, cid, sent=False)
        return True

    def sync_second_session_obligations_for_def(self, template_id: int) -> int:
        """Alias historique — voir ``sync_second_session_obligations``."""
        return self.sync_second_session_obligations(int(template_id))

    def carry_over_reprise_grades_from_session1(
        self,
        student_id: int,
        course_id: int,
        *,
        template_id: int | None = None,
    ) -> int:
        """
        Recopie en base les notes / statuts de session 1 vers les épreuves de session 2 « reprises »
        (nom contenant ``rep`` / ``[Rep]``, ou CC/CCTP), pour les cases S2 encore vides.

        Appelé uniquement lors de l'**envoi en 2ᵉ session** (``set_second_session_decision(sent=True)``).
        Le calcul de moyenne S2 reprend ensuite le CC S1 via repli MCC même si la case S2 est vide.
        """
        sid, cid = int(student_id), int(course_id)
        if not self.is_second_session_carry_allowed(sid, cid, template_id):
            return 0
        rows = self.get_grades_for_student_course(int(student_id), int(course_id))
        s1 = [r for r in rows if int(r["session"]) != 2]
        s2 = [r for r in rows if int(r["session"]) == 2]

        kind_src: dict[str, tuple[float | None, str]] = {}
        for r in s1:
            st = normalize_grade_status(r.get("status"))
            if status_skips_average(st):
                continue
            k = str(r["kind"])
            if k in kind_src:
                continue
            gr = r["grade"]
            kind_src[k] = (float(gr) if gr is not None else None, st)

        tag_src: dict[str, tuple[float | None, str]] = {}
        for r in s1:
            st = normalize_grade_status(r.get("status"))
            if status_skips_average(st):
                continue
            tag = _extract_tag_from_assessment_name(str(r.get("name") or ""))
            if not tag or tag in tag_src:
                continue
            gr = r["grade"]
            tag_src[tag] = (float(gr) if gr is not None else None, st)

        n = 0
        for r in s2:
            if int(r.get("locked") or 0):
                continue
            if not _grade_cell_empty(r.get("grade"), r.get("status")):
                continue
            name = str(r.get("name") or "")
            kind = str(r["kind"])
            src: tuple[float | None, str] | None = None
            if "rep" in name.lower():
                src = kind_src.get(kind)
            elif kind in {"CC", "CCTP"}:
                src = kind_src.get(kind)
            else:
                tag = _extract_tag_from_assessment_name(name)
                if tag and tag in tag_src:
                    src = tag_src[tag]
            if src is None:
                continue
            gv, st = src
            if st == STATUS_DEF:
                # Ne pas matérialiser un DEF S1 en case S2 : affichage vide, moyenne S2 via fallback MCC.
                continue
            self.upsert_grade(
                sid,
                int(r["assessment_id"]),
                gv,
                status=st,
                locked=0,
                comment=str(r.get("comment") or ""),
                trigger_carry_over=False,
            )
            n += 1
        return n

    def purge_carried_s2_reprises_without_send(
        self, student_id: int, course_id: int, *, template_id: int
    ) -> int:
        """Efface les reprises S2 (CC Rep, …) recopiées à tort sans envoi S2 sur l'UE."""
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if self.is_sent_to_second_session(sid, tid, cid):
            return 0
        n = 0
        for r in self.get_grades_for_student_course(sid, cid):
            if int(r["session"]) != 2 or not _is_s2_reprise_assessment_row(r):
                continue
            if _grade_cell_empty(r.get("grade"), r.get("status")):
                continue
            if int(r.get("locked") or 0):
                continue
            self.upsert_grade(
                sid,
                int(r["assessment_id"]),
                None,
                status=STATUS_OK,
                locked=0,
                comment="",
                trigger_carry_over=False,
            )
            n += 1
        return n

    def set_second_session_decision(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        sent: bool,
        comment: str = "",
        s1_jury: bool = False,
        jury_session_id: int | None = None,
    ) -> None:
        if not self.can_set_second_session_decision(
            int(student_id),
            int(template_id),
            int(course_id),
            sent=bool(sent),
            s1_jury=s1_jury,
        ):
            raise ValueError(
                "Envoi en 2ᵉ session impossible : des notes de session 2 existent déjà pour cette UE."
            )
        jsid = int(jury_session_id) if jury_session_id is not None else None
        self.db.execute(
            """
            INSERT INTO second_session_decisions(
                student_id, template_id, course_id, jury_session_id, sent, comment
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, course_id)
            DO UPDATE SET
                sent=excluded.sent,
                comment=excluded.comment,
                jury_session_id=COALESCE(
                    excluded.jury_session_id,
                    second_session_decisions.jury_session_id
                )
            """,
            (
                int(student_id),
                int(template_id),
                int(course_id),
                jsid,
                1 if sent else 0,
                str(comment or "").strip(),
            ),
        )
        if sent:
            self.carry_over_reprise_grades_from_session1(
                int(student_id), int(course_id), template_id=int(template_id)
            )
        else:
            self.purge_carried_s2_reprises_without_send(
                int(student_id), int(course_id), template_id=int(template_id)
            )

    def list_template_blocks_with_courses(self, template_id: int) -> list[tuple[str, list[dict[str, Any]]]]:
        """Blocs dans l’ordre de la maquette, avec la liste des UE (template_courses) de chaque bloc."""
        courses = self.list_template_courses(int(template_id))
        order: list[str] = []
        by_block: dict[str, list[dict[str, Any]]] = {}
        for c in courses:
            bk = _block_key(c)
            if bk not in by_block:
                order.append(bk)
                by_block[bk] = []
            by_block[bk].append(c)
        return [(bk, by_block[bk]) for bk in order]

    def _jury_map_for_template(self, template_id: int) -> dict[int, dict[str, Any]]:
        tid = int(template_id)
        self.repair_jury_decision_session_links(tid)
        rows = self.db.query_all(
            """
            SELECT student_id, scope, course_id, block_name, points
            FROM jury_adjustments
            WHERE template_id = ?
            """,
            (int(template_id),),
        )
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            sid = int(r["student_id"])
            slot = out.setdefault(
                sid,
                {"course": {}, "block": {}, "year": 0.0},
            )
            sc = str(r["scope"] or "").strip().lower()
            pts = float(r["points"] or 0)
            if sc == "course" and r["course_id"] is not None:
                cid = int(r["course_id"])
                slot["course"][cid] = float(slot["course"].get(cid, 0.0)) + pts
            elif sc == "block":
                bk = str(r["block_name"] or "").strip()
                slot["block"][bk] = float(slot["block"].get(bk, 0.0)) + pts
            elif sc == "year":
                slot["year"] = float(slot["year"]) + pts
        return out

    def upsert_jury_adjustment(
        self,
        student_id: int,
        template_id: int,
        scope: str,
        *,
        course_id: int | None = None,
        block_name: str = "",
        points: float = 0.0,
        comment: str = "",
        jury_session_id: int | None = None,
    ) -> None:
        """Enregistre des points de délibération (UE, bloc ou année) pour une réunion."""
        sc = str(scope or "").strip().lower()
        jsid = int(jury_session_id) if jury_session_id is not None else None
        sid, tid = int(student_id), int(template_id)
        if jsid is not None:
            # Évite le double comptage legacy (sans session) + saisie rattachée à une délibération.
            self.db.execute(
                """
                DELETE FROM jury_adjustments
                WHERE student_id = ? AND template_id = ? AND scope = ?
                  AND IFNULL(course_id, -999999) = IFNULL(?, -999999)
                  AND TRIM(IFNULL(block_name, '')) = TRIM(IFNULL(?, ''))
                  AND jury_session_id IS NULL
                """,
                (sid, tid, sc, course_id, block_name),
            )
        self.db.execute(
            """
            DELETE FROM jury_adjustments
            WHERE student_id = ? AND template_id = ? AND scope = ?
              AND IFNULL(course_id, -999999) = IFNULL(?, -999999)
              AND TRIM(IFNULL(block_name, '')) = TRIM(IFNULL(?, ''))
              AND IFNULL(jury_session_id, -1) = IFNULL(?, -1)
            """,
            (sid, tid, sc, course_id, block_name, jsid),
        )
        if abs(float(points)) < 1e-12 and not (comment or "").strip():
            return
        self.db.execute(
            """
            INSERT INTO jury_adjustments(
                student_id, template_id, jury_session_id, scope, course_id, block_name, points, comment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                tid,
                jsid,
                sc,
                course_id,
                str(block_name or "").strip(),
                float(points),
                str(comment or "").strip(),
            ),
        )

    # Équipe pédagogique du master (mention, parcours, secrétariats).

    def list_master_team_members(
        self,
        academic_year: str,
        *,
        role_kind: str = "",
    ) -> list[dict[str, Any]]:
        ay = (academic_year or "").strip()
        rk = (role_kind or "").strip().lower()
        if rk:
            rows = self.db.query_all(
                """
                SELECT * FROM master_team_members
                WHERE academic_year = ? AND role_kind = ?
                ORDER BY display_order, level, track, institution, last_name, id
                """,
                (ay, rk),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT * FROM master_team_members
                WHERE academic_year = ?
                ORDER BY role_kind, display_order, level, track, institution, last_name, id
                """,
                (ay,),
            )
        return [dict(r) for r in rows]

    def get_master_team_member(self, member_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM master_team_members WHERE id = ?", (int(member_id),))
        return dict(row) if row else None

    def add_master_team_member(
        self,
        academic_year: str,
        role_kind: str,
        *,
        level: str = "",
        track: str = "",
        institution: str = "",
        tracks_scope: str = "",
        last_name: str = "",
        first_name: str = "",
        title: str = "",
        affiliation: str = "",
        email: str = "",
        email_work: str = "",
        email_work_2: str = "",
        email_personal: str = "",
        phone: str = "",
        phone_work: str = "",
        phone_work_2: str = "",
        phone_mobile: str = "",
        notes: str = "",
        display_order: int | None = None,
        post_label: str = "",
        student_id: int | None = None,
    ) -> int:
        from ..core.master_team import ROLE_MENTION, ROLE_SECRETARIAT, ROLE_STUDENT_REP, ROLE_TRACK

        ay = (academic_year or "").strip()
        rk = (role_kind or "").strip().lower()
        if rk not in (ROLE_MENTION, ROLE_TRACK, ROLE_SECRETARIAT, ROLE_STUDENT_REP):
            raise ValueError(f"Rôle inconnu : {role_kind!r}")
        if display_order is not None:
            ord_ = int(display_order)
        else:
            mx = self.db.query_one(
                """
                SELECT COALESCE(MAX(display_order), -1) + 1 AS n
                FROM master_team_members WHERE academic_year = ? AND role_kind = ?
                """,
                (ay, rk),
            )
            ord_ = int(mx["n"]) if mx and mx["n"] is not None else 0
        from .contact_emails import email_storage_values
        from .contact_phones import phone_storage_values

        emails = email_storage_values(
            email_work,
            email_work_2,
            email_personal,
            legacy_fallback=email,
        )
        phones = phone_storage_values(
            phone_work,
            phone_work_2,
            phone_mobile or phone,
            legacy_fallback=phone,
        )
        cur = self.db.execute(
            """
            INSERT INTO master_team_members(
                academic_year, role_kind, level, track, institution, tracks_scope,
                last_name, first_name, title, affiliation, email, email_work, email_work_2, email_personal,
                phone, phone_work, phone_work_2, phone_mobile,
                notes, display_order, post_label, student_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ay,
                rk,
                str(level or "").strip().upper(),
                str(track or "").strip().upper(),
                str(institution or "").strip(),
                str(tracks_scope or "").strip(),
                str(last_name or "").strip(),
                str(first_name or "").strip(),
                str(title or "").strip(),
                str(affiliation or "").strip(),
                emails["email"],
                emails["email_work"],
                emails["email_work_2"],
                emails["email_personal"],
                phones["phone"],
                phones["phone_work"],
                phones["phone_work_2"],
                phones["phone_mobile"],
                str(notes or "").strip(),
                ord_,
                str(post_label or "").strip(),
                int(student_id) if student_id else None,
            ),
        )
        return int(cur.lastrowid)

    def update_master_team_member(self, member_id: int, **fields: Any) -> None:
        row = self.get_master_team_member(int(member_id))
        if not row:
            raise ValueError("Membre introuvable.")
        allowed = (
            "level",
            "track",
            "institution",
            "tracks_scope",
            "last_name",
            "first_name",
            "title",
            "affiliation",
            "email",
            "email_work",
            "email_work_2",
            "email_personal",
            "phone",
            "phone_work",
            "phone_work_2",
            "phone_mobile",
            "notes",
            "display_order",
            "post_label",
            "student_id",
        )
        updates: dict[str, Any] = {}
        for key in allowed:
            if key in fields:
                val = fields[key]
                if key in ("level", "track"):
                    val = str(val or "").strip().upper()
                elif key == "display_order":
                    val = int(val)
                elif key == "student_id":
                    val = int(val) if val else None
                else:
                    val = str(val or "").strip()
                updates[key] = val
        if not updates:
            return
        if {"phone", "phone_work", "phone_work_2", "phone_mobile"} & updates.keys():
            from .contact_phones import merge_phone_row

            updates.update(merge_phone_row(row, updates))
        if {"email", "email_work", "email_work_2", "email_personal"} & updates.keys():
            from .contact_emails import merge_email_row

            updates.update(merge_email_row(row, updates))
        sets = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE master_team_members SET {sets} WHERE id = ?",
            (*updates.values(), int(member_id)),
        )

    def delete_master_team_member(self, member_id: int) -> None:
        self.db.execute("DELETE FROM master_team_members WHERE id = ?", (int(member_id),))

    def list_mention_directors(self, academic_year: str) -> list[dict[str, Any]]:
        """Les trois directeurs de la mention (slots 0, 1, 2), dans l'ordre."""
        from ..core.master_team import MENTION_DIRECTOR_COUNT, ROLE_MENTION

        rows = self.list_master_team_members(academic_year, role_kind=ROLE_MENTION)
        by_slot: dict[int, dict[str, Any]] = {}
        overflow: list[dict[str, Any]] = []
        for row in rows:
            slot = int(row.get("display_order") or 0)
            if 0 <= slot < MENTION_DIRECTOR_COUNT and slot not in by_slot:
                by_slot[slot] = row
            else:
                overflow.append(row)
        for idx, row in enumerate(overflow):
            for slot in range(MENTION_DIRECTOR_COUNT):
                if slot not in by_slot:
                    by_slot[slot] = row
                    break
        return [by_slot.get(i, {}) for i in range(MENTION_DIRECTOR_COUNT)]

    def upsert_mention_director(
        self,
        academic_year: str,
        slot: int,
        *,
        last_name: str,
        first_name: str,
        title: str = "",
        affiliation: str = "",
        email: str = "",
        email_work: str = "",
        email_work_2: str = "",
        email_personal: str = "",
        phone: str = "",
        phone_work: str = "",
        phone_work_2: str = "",
        phone_mobile: str = "",
        notes: str = "",
        post_label: str = "",
    ) -> int:
        """Un directeur par slot (0, 1 ou 2) pour le millésime."""
        from ..core.master_team import MENTION_DIRECTOR_COUNT, ROLE_MENTION

        ay = (academic_year or "").strip()
        sl = int(slot)
        if sl < 0 or sl >= MENTION_DIRECTOR_COUNT:
            raise ValueError(f"Slot directeur invalide : {slot!r}")
        existing = self.db.query_one(
            """
            SELECT id FROM master_team_members
            WHERE academic_year = ? AND role_kind = ? AND display_order = ?
            """,
            (ay, ROLE_MENTION, sl),
        )
        if existing:
            mid = int(existing["id"])
            self.update_master_team_member(
                mid,
                last_name=last_name,
                first_name=first_name,
                title=title,
                affiliation=affiliation,
                email=email,
                email_work=email_work,
                email_work_2=email_work_2,
                email_personal=email_personal,
                phone=phone,
                phone_work=phone_work,
                phone_work_2=phone_work_2,
                phone_mobile=phone_mobile,
                notes=notes,
                display_order=sl,
                post_label=post_label,
            )
            return mid
        return self.add_master_team_member(
            ay,
            ROLE_MENTION,
            last_name=last_name,
            first_name=first_name,
            title=title,
            affiliation=affiliation,
            email=email,
            email_work=email_work,
            email_work_2=email_work_2,
            email_personal=email_personal,
            phone=phone,
            phone_work=phone_work,
            phone_work_2=phone_work_2,
            phone_mobile=phone_mobile,
            notes=notes,
            display_order=sl,
            post_label=post_label,
        )

    def upsert_track_director(
        self,
        academic_year: str,
        level: str,
        track: str,
        slot: int,
        *,
        last_name: str,
        first_name: str,
        title: str = "",
        affiliation: str = "",
        email: str = "",
        email_work: str = "",
        email_work_2: str = "",
        email_personal: str = "",
        phone: str = "",
        phone_work: str = "",
        phone_work_2: str = "",
        phone_mobile: str = "",
        notes: str = "",
    ) -> int | None:
        """Un responsable par (millésime, niveau, parcours, slot). Slot 0…1 en M2."""
        from ..core.master_team import ROLE_TRACK, track_director_slot_count

        ay = (academic_year or "").strip()
        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        sl = int(slot)
        if sl < 0 or sl >= track_director_slot_count(lv):
            raise ValueError(f"Slot responsable invalide pour {lv} : {slot!r}")
        ln = str(last_name or "").strip()
        fn = str(first_name or "").strip()
        if (
            not ln
            and not fn
            and not str(title or "").strip()
            and not str(affiliation or "").strip()
            and not any(
                str(v or "").strip()
                for v in (email_work, email_work_2, email_personal, phone_work, phone_work_2, phone_mobile)
            )
        ):
            existing = self.db.query_one(
                """
                SELECT id FROM master_team_members
                WHERE academic_year = ? AND role_kind = ? AND level = ? AND track = ? AND display_order = ?
                """,
                (ay, ROLE_TRACK, lv, tr, sl),
            )
            if existing:
                self.delete_master_team_member(int(existing["id"]))
            return None
        existing = self.db.query_one(
            """
            SELECT id FROM master_team_members
            WHERE academic_year = ? AND role_kind = ? AND level = ? AND track = ? AND display_order = ?
            """,
            (ay, ROLE_TRACK, lv, tr, sl),
        )
        if existing:
            mid = int(existing["id"])
            self.update_master_team_member(
                mid,
                last_name=ln,
                first_name=fn,
                title=title,
                affiliation=affiliation,
                email=email,
                email_work=email_work,
                email_work_2=email_work_2,
                email_personal=email_personal,
                phone=phone,
                phone_work=phone_work,
                phone_work_2=phone_work_2,
                phone_mobile=phone_mobile,
                notes=notes,
                display_order=sl,
            )
            return mid
        return self.add_master_team_member(
            ay,
            ROLE_TRACK,
            level=lv,
            track=tr,
            last_name=ln,
            first_name=fn,
            title=title,
            affiliation=affiliation,
            email=email,
            email_work=email_work,
            email_work_2=email_work_2,
            email_personal=email_personal,
            phone=phone,
            phone_work=phone_work,
            phone_work_2=phone_work_2,
            phone_mobile=phone_mobile,
            notes=notes,
            display_order=sl,
        )

    def list_students_for_track(
        self, *, academic_year: str, level: str, track: str = ""
    ) -> list[dict[str, Any]]:
        """Étudiants actifs du millésime / niveau / parcours (parcours vide = tout le niveau)."""
        ay = str(academic_year or "").strip()
        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        if not ay or not lv:
            return []
        params: list[Any] = [ay, lv]
        track_sql = ""
        if tr:
            track_sql = " AND UPPER(TRIM(track)) = ?"
            params.append(tr)
        rows = self.db.query_all(
            f"""
            SELECT *
            FROM students
            WHERE {sql_student_is_active("students")}
              AND (TRIM(IFNULL(academic_year, '')) = TRIM(?) OR TRIM(IFNULL(academic_year, '')) = '')
              AND UPPER(TRIM(level)) = ?
              {track_sql}
            ORDER BY last_name, first_name
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]

    def list_student_representatives_m1(self, academic_year: str) -> list[dict[str, Any]]:
        """Les 2 représentants M1 (promotion entière), ordre 0 puis 1."""
        from ..core.master_team import ROLE_STUDENT_REP, STUDENT_REP_COUNT_M1

        rows = self.list_master_team_members(academic_year, role_kind=ROLE_STUDENT_REP)
        by_slot: dict[int, dict[str, Any]] = {}
        for row in rows:
            if str(row.get("level") or "").strip().upper() != "M1":
                continue
            if str(row.get("track") or "").strip():
                continue
            slot = int(row.get("display_order") or 0)
            if 0 <= slot < STUDENT_REP_COUNT_M1:
                by_slot[slot] = row
        return [by_slot.get(i, {}) for i in range(STUDENT_REP_COUNT_M1)]

    def list_student_representatives_m2(self, academic_year: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Représentants M2 par (niveau, parcours) → liste ordonnée (2 slots)."""
        from ..core.master_team import ROLE_STUDENT_REP, STUDENT_REP_COUNT_M2_PER_TRACK, m2_track_pairs

        rows = self.list_master_team_members(academic_year, role_kind=ROLE_STUDENT_REP)
        out: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for lv, tr, _lab in m2_track_pairs():
            key = (lv, tr)
            by_slot: dict[int, dict[str, Any]] = {}
            for row in rows:
                if str(row.get("level") or "").strip().upper() != lv:
                    continue
                if str(row.get("track") or "").strip().upper() != tr:
                    continue
                slot = int(row.get("display_order") or 0)
                if 0 <= slot < STUDENT_REP_COUNT_M2_PER_TRACK:
                    by_slot[slot] = row
            out[key] = [by_slot.get(i, {}) for i in range(STUDENT_REP_COUNT_M2_PER_TRACK)]
        return out

    def upsert_student_representative(
        self,
        academic_year: str,
        level: str,
        track: str,
        slot: int,
        *,
        student_id: int | None = None,
        last_name: str = "",
        first_name: str = "",
        email: str = "",
        phone: str = "",
        notes: str = "",
    ) -> int | None:
        """Crée ou met à jour un représentant ; supprime la ligne si aucune info."""
        from ..core.master_team import ROLE_STUDENT_REP

        ay = (academic_year or "").strip()
        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        sl = int(slot)
        sid = int(student_id) if student_id else None
        email_work = ""
        email_personal = ""
        affiliation = ""
        if sid:
            st = self.get_student(sid)
            if st:
                last_name = str(st.get("last_name") or last_name or "")
                first_name = str(st.get("first_name") or first_name or "")
                email_work = str(st.get("email_institutional") or "")
                email_personal = str(st.get("email_personal") or "")
                email = email_work or email_personal or email
                phone = str(st.get("phone") or phone or "")
                affiliation = str(st.get("enrollment_institution") or st.get("origin_institution") or "")
        ln = str(last_name or "").strip()
        fn = str(first_name or "").strip()
        if (
            not sid
            and not ln
            and not fn
            and not str(email or "").strip()
            and not str(phone or "").strip()
        ):
            existing = self.db.query_one(
                """
                SELECT id FROM master_team_members
                WHERE academic_year = ? AND role_kind = ? AND level = ? AND track = ? AND display_order = ?
                """,
                (ay, ROLE_STUDENT_REP, lv, tr, sl),
            )
            if existing:
                self.delete_master_team_member(int(existing["id"]))
            return None
        existing = self.db.query_one(
            """
            SELECT id FROM master_team_members
            WHERE academic_year = ? AND role_kind = ? AND level = ? AND track = ? AND display_order = ?
            """,
            (ay, ROLE_STUDENT_REP, lv, tr, sl),
        )
        fields = dict(
            last_name=ln,
            first_name=fn,
            email=str(email or "").strip(),
            email_work=email_work,
            email_personal=email_personal,
            affiliation=affiliation,
            phone=str(phone or "").strip(),
            notes=str(notes or "").strip(),
            student_id=sid,
            display_order=sl,
        )
        if existing:
            mid = int(existing["id"])
            self.update_master_team_member(mid, **fields)
            return mid
        return self.add_master_team_member(
            ay,
            ROLE_STUDENT_REP,
            level=lv,
            track=tr,
            **fields,
        )

    def secretariats_for_track(
        self, academic_year: str, level: str, track: str
    ) -> list[dict[str, Any]]:
        """Secrétariats pédagogiques couvrant un parcours donné."""
        from ..core.master_team import ROLE_SECRETARIAT, decode_tracks_scope

        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        out: list[dict[str, Any]] = []
        for row in self.list_master_team_members(academic_year, role_kind=ROLE_SECRETARIAT):
            pairs = decode_tracks_scope(str(row.get("tracks_scope") or ""))
            if (lv, tr) in pairs:
                out.append(row)
        return out

    # Délibérations (réunions du jury) — tables techniques ``jury_sessions`` / ``jury_members``.
    # Compositions réutilisables : ``jury_rosters`` / ``jury_roster_members``.

    def list_jury_rosters(self, template_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM jury_rosters WHERE template_id = ?
            ORDER BY display_order, id
            """,
            (int(template_id),),
        )
        return [dict(r) for r in rows]

    def default_roster_name_for_template(self, template_id: int) -> str:
        tpl = self.get_template(int(template_id)) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        tr = str(tpl.get("track") or "").strip().upper()
        return f"Jury {lv}{tr}".strip() or "Composition du jury"

    def _roster_member_count(self, roster_id: int) -> int:
        n = self.db.query_one(
            "SELECT COUNT(*) AS n FROM jury_roster_members WHERE roster_id = ?",
            (int(roster_id),),
        )
        return int(n["n"]) if n and n["n"] is not None else 0

    def consolidate_template_jury_rosters(self, template_id: int) -> int | None:
        """Fusionne les doublons ; ne crée pas de composition vide."""
        tid = int(template_id)
        tpl = self.get_template(tid)
        if not tpl:
            raise ValueError("Maquette introuvable.")
        rosters = self.list_jury_rosters(tid)
        if not rosters:
            return None

        expected = self.default_roster_name_for_template(tid)
        ay = str(tpl.get("academic_year") or "").strip()
        best = max(
            rosters,
            key=lambda r: (self._roster_member_count(int(r["id"])), -int(r["id"])),
        )
        best_id = int(best["id"])

        self.db.execute(
            """
            UPDATE jury_rosters
            SET name = ?, academic_year = CASE WHEN TRIM(academic_year) = '' THEN ? ELSE academic_year END
            WHERE id = ?
            """,
            (expected, ay, best_id),
        )

        for roster in rosters:
            rid = int(roster["id"])
            if rid == best_id:
                continue
            self.db.execute(
                "UPDATE jury_sessions SET roster_id = ? WHERE roster_id = ?",
                (best_id, rid),
            )
            if self._roster_member_count(rid) == 0:
                self.db.execute("DELETE FROM jury_rosters WHERE id = ?", (rid,))

        return best_id

    def get_template_roster(self, template_id: int) -> int | None:
        """Composition enregistrée du parcours, ou None si aucune."""
        return self.consolidate_template_jury_rosters(int(template_id))

    def ensure_template_roster(self, template_id: int) -> int:
        """Crée la composition du parcours au premier enregistrement (import, membre…)."""
        rid = self.get_template_roster(int(template_id))
        if rid is not None:
            return rid
        tid = int(template_id)
        tpl = self.get_template(tid) or {}
        ay = str(tpl.get("academic_year") or "").strip()
        return self.add_jury_roster(
            tid, self.default_roster_name_for_template(tid), academic_year=ay
        )

    def get_or_create_default_roster(self, template_id: int) -> int:
        """Alias historique — préférer ensure_template_roster ou get_template_roster."""
        return self.ensure_template_roster(int(template_id))

    def repair_missing_s1_jury_sessions(self) -> int:
        """Ajoute une délibération S1 si le parcours a déjà d'autres sessions et une composition."""
        added = 0
        for tpl in self.list_templates():
            tid = int(tpl["id"])
            rid = self.get_template_roster(tid)
            if rid is None or self._roster_member_count(rid) == 0:
                continue
            sessions = self.list_jury_sessions(tid)
            if not sessions:
                continue
            kinds = {str(s.get("session_kind") or "") for s in sessions}
            if "S1" in kinds:
                continue
            self.add_jury_session(
                tid,
                "S1",
                label="Délibération bloc 1 — S1",
                scope_text="Bloc 1 — 1ʳᵉ session",
                roster_id=rid,
            )
            added += 1
        return added

    def cleanup_empty_orphan_jury_rosters(self) -> int:
        """Supprime les compositions sans membres ni délibération liée."""
        rows = self.db.query_all(
            """
            SELECT r.id
            FROM jury_rosters r
            LEFT JOIN jury_roster_members m ON m.roster_id = r.id
            LEFT JOIN jury_sessions s ON s.roster_id = r.id
            WHERE m.id IS NULL AND s.id IS NULL
            """
        )
        for row in rows:
            self.db.execute("DELETE FROM jury_rosters WHERE id = ?", (int(row["id"]),))
        return len(rows)

    def list_parcours_rosters(
        self, academic_year: str, level: str
    ) -> list[dict[str, Any]]:
        """Parcours du millésime/niveau avec composition enregistrée (si elle existe)."""
        out: list[dict[str, Any]] = []
        for tpl in self.list_templates_for_year_level(academic_year, level):
            tid = int(tpl["id"])
            rid = self.get_template_roster(tid)
            rec = dict(tpl)
            if rid is None:
                rec["roster_id"] = None
                rec["roster_name"] = ""
                rec["member_count"] = 0
            else:
                roster = self.get_jury_roster(rid) or {}
                rec["roster_id"] = rid
                rec["roster_name"] = str(roster.get("name") or "")
                rec["member_count"] = self._roster_member_count(rid)
            out.append(rec)
        return out

    def get_jury_roster(self, roster_id: int) -> dict[str, Any] | None:
        r = self.db.query_one("SELECT * FROM jury_rosters WHERE id = ?", (int(roster_id),))
        return dict(r) if r else None

    def add_jury_roster(
        self, template_id: int, name: str, *, academic_year: str = "", notes: str = ""
    ) -> int:
        mx = self.db.query_one(
            "SELECT COALESCE(MAX(display_order), -1) + 1 AS n FROM jury_rosters WHERE template_id = ?",
            (int(template_id),),
        )
        ord_ = int(mx["n"]) if mx and mx["n"] is not None else 0
        cur = self.db.execute(
            """
            INSERT INTO jury_rosters(template_id, name, academic_year, notes, display_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(template_id),
                str(name or "").strip() or "Composition du jury",
                str(academic_year or "").strip(),
                str(notes or "").strip(),
                ord_,
            ),
        )
        return int(cur.lastrowid)

    def clone_jury_roster(self, source_roster_id: int, *, new_name: str) -> int:
        src = self.get_jury_roster(int(source_roster_id))
        if not src:
            raise ValueError("Composition source introuvable.")
        new_id = self.add_jury_roster(
            int(src["template_id"]),
            str(new_name).strip() or f"{src.get('name', '')} (copie)",
            academic_year=str(src.get("academic_year") or ""),
            notes=str(src.get("notes") or ""),
        )
        for m in self.list_jury_roster_members(int(source_roster_id)):
            self.add_jury_roster_member(
                int(new_id),
                last_name=str(m.get("last_name") or ""),
                first_name=str(m.get("first_name") or ""),
                title=str(m.get("title") or ""),
                institution=str(m.get("institution") or ""),
                is_president=bool(int(m.get("is_president") or 0)),
            )
        return int(new_id)

    def list_jury_rosters_catalog(
        self,
        *,
        academic_year: str = "",
        level: str = "",
        exclude_roster_id: int | None = None,
        only_with_members: bool = False,
    ) -> list[dict[str, Any]]:
        """Compositions du jury avec maquette (parcours) — pour import inter-filières."""
        rows = self.db.query_all(
            """
            SELECT r.*, t.name AS template_name, t.level, t.track, t.academic_year AS tpl_academic_year
            FROM jury_rosters r
            JOIN templates t ON t.id = r.template_id
            ORDER BY t.academic_year DESC, t.level, t.track, r.display_order, r.id
            """
        )
        ay = (academic_year or "").strip()
        lv = (level or "").strip().upper()
        ex = int(exclude_roster_id) if exclude_roster_id is not None else None
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(row)
            if ay and str(rec.get("tpl_academic_year") or "").strip() != ay:
                continue
            if lv and str(rec.get("level") or "").strip().upper() != lv:
                continue
            if ex is not None and int(rec["id"]) == ex:
                continue
            n = self._roster_member_count(int(rec["id"]))
            rec["member_count"] = n
            if only_with_members and n == 0:
                continue
            out.append(rec)
        return out

    def copy_jury_roster_to_template(
        self,
        source_roster_id: int,
        target_template_id: int,
        *,
        new_name: str = "",
    ) -> int:
        """Copie les membres vers la composition par défaut du parcours cible."""
        src = self.get_jury_roster(int(source_roster_id))
        if not src:
            raise ValueError("Composition source introuvable.")
        tpl = self.get_template(int(target_template_id))
        if not tpl:
            raise ValueError("Maquette cible introuvable.")
        target_id = self.ensure_template_roster(int(target_template_id))
        members = [
            {
                "last_name": str(m.get("last_name") or ""),
                "first_name": str(m.get("first_name") or ""),
                "title": str(m.get("title") or ""),
                "institution": str(m.get("institution") or ""),
            }
            for m in self.list_jury_roster_members(int(source_roster_id))
        ]
        self.replace_jury_roster_members(int(target_id), members)
        if new_name:
            self.db.execute(
                "UPDATE jury_rosters SET name = ? WHERE id = ?",
                (str(new_name).strip(), int(target_id)),
            )
        return int(target_id)

    @staticmethod
    def _member_identity_key(last_name: str, first_name: str) -> str:
        return f"{(last_name or '').strip().upper()}|{(first_name or '').strip().upper()}"

    def append_roster_members_from(
        self,
        source_roster_id: int,
        target_roster_id: int,
        *,
        skip_duplicates: bool = True,
    ) -> int:
        """Ajoute les membres d'une autre composition (sans effacer la cible)."""
        if int(source_roster_id) == int(target_roster_id):
            raise ValueError("La composition source et la cible sont identiques.")
        src_members = self.list_jury_roster_members(int(source_roster_id))
        if not src_members:
            return 0
        existing: set[str] = set()
        if skip_duplicates:
            for m in self.list_jury_roster_members(int(target_roster_id)):
                existing.add(
                    self._member_identity_key(
                        str(m.get("last_name") or ""),
                        str(m.get("first_name") or ""),
                    )
                )
        added = 0
        for m in src_members:
            ln = str(m.get("last_name") or "")
            fn = str(m.get("first_name") or "")
            key = self._member_identity_key(ln, fn)
            if skip_duplicates and key in existing:
                continue
            self.add_jury_roster_member(
                int(target_roster_id),
                last_name=ln,
                first_name=fn,
                title=str(m.get("title") or ""),
                institution=str(m.get("institution") or ""),
            )
            existing.add(key)
            added += 1
        return added

    def delete_jury_roster(self, roster_id: int) -> None:
        self.db.execute("DELETE FROM jury_rosters WHERE id = ?", (int(roster_id),))

    def list_jury_roster_members(self, roster_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM jury_roster_members WHERE roster_id = ?
            ORDER BY is_president DESC, display_order, id
            """,
            (int(roster_id),),
        )
        return [dict(r) for r in rows]

    def set_jury_roster_president(self, roster_id: int, member_id: int) -> None:
        self.db.execute(
            "UPDATE jury_roster_members SET is_president = 0 WHERE roster_id = ?",
            (int(roster_id),),
        )
        self.db.execute(
            """
            UPDATE jury_roster_members
            SET is_president = 1
            WHERE id = ? AND roster_id = ?
            """,
            (int(member_id), int(roster_id)),
        )

    def clear_jury_roster_president(self, roster_id: int) -> None:
        self.db.execute(
            "UPDATE jury_roster_members SET is_president = 0 WHERE roster_id = ?",
            (int(roster_id),),
        )

    def add_jury_roster_member(
        self,
        roster_id: int,
        *,
        last_name: str,
        first_name: str,
        title: str = "",
        institution: str = "",
        is_president: bool = False,
    ) -> int:
        mx = self.db.query_one(
            "SELECT COALESCE(MAX(display_order), -1) + 1 AS n FROM jury_roster_members WHERE roster_id = ?",
            (int(roster_id),),
        )
        ord_ = int(mx["n"]) if mx and mx["n"] is not None else 0
        cur = self.db.execute(
            """
            INSERT INTO jury_roster_members(
                roster_id, last_name, first_name, title, institution, is_president, display_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(roster_id),
                str(last_name or "").strip(),
                str(first_name or "").strip(),
                str(title or "").strip(),
                str(institution or "").strip(),
                1 if is_president else 0,
                ord_,
            ),
        )
        new_id = int(cur.lastrowid)
        if is_president:
            self.set_jury_roster_president(int(roster_id), new_id)
        return new_id

    def update_jury_roster_member(
        self,
        member_id: int,
        *,
        last_name: str | None = None,
        first_name: str | None = None,
        title: str | None = None,
        institution: str | None = None,
    ) -> None:
        row = self.db.query_one("SELECT * FROM jury_roster_members WHERE id = ?", (int(member_id),))
        if not row:
            return
        d = dict(row)
        self.db.execute(
            """
            UPDATE jury_roster_members
            SET last_name = ?, first_name = ?, title = ?, institution = ?
            WHERE id = ?
            """,
            (
                str(last_name).strip() if last_name is not None else d["last_name"],
                str(first_name).strip() if first_name is not None else d["first_name"],
                str(title).strip() if title is not None else d["title"],
                str(institution).strip() if institution is not None else d["institution"],
                int(member_id),
            ),
        )

    def delete_jury_roster_member(self, member_id: int) -> None:
        self.db.execute("DELETE FROM jury_roster_members WHERE id = ?", (int(member_id),))

    def clear_jury_roster_members(self, roster_id: int) -> None:
        self.db.execute("DELETE FROM jury_roster_members WHERE roster_id = ?", (int(roster_id),))

    def replace_jury_roster_members(
        self, roster_id: int, members: list[dict[str, Any]]
    ) -> int:
        """Remplace tous les membres d'une composition. Retourne le nombre importé."""
        self.clear_jury_roster_members(int(roster_id))
        n = 0
        president_id: int | None = None
        for m in members:
            mid = self.add_jury_roster_member(
                int(roster_id),
                last_name=str(m.get("last_name") or ""),
                first_name=str(m.get("first_name") or ""),
                title=str(m.get("title") or ""),
                institution=str(m.get("institution") or ""),
            )
            if int(m.get("is_president") or 0):
                president_id = mid
            n += 1
        if president_id is not None:
            self.set_jury_roster_president(int(roster_id), president_id)
        return n

    def create_jury_roster_with_members(
        self,
        template_id: int,
        name: str,
        members: list[dict[str, Any]],
        *,
        academic_year: str = "",
        notes: str = "",
    ) -> int:
        """Crée une composition et y ajoute tous les membres."""
        rid = self.add_jury_roster(
            int(template_id),
            str(name or "").strip() or "Composition du jury",
            academic_year=academic_year,
            notes=notes,
        )
        self.replace_jury_roster_members(rid, members)
        return rid

    def get_jury_session(self, jury_session_id: int) -> dict[str, Any] | None:
        r = self.db.query_one("SELECT * FROM jury_sessions WHERE id = ?", (int(jury_session_id),))
        return dict(r) if r else None

    def get_deliberation(self, deliberation_id: int) -> dict[str, Any] | None:
        return self.get_jury_session(deliberation_id)

    def list_jury_sessions(self, template_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM jury_sessions WHERE template_id = ?
            ORDER BY session_kind, display_order, id
            """,
            (int(template_id),),
        )
        return [dict(r) for r in rows]

    def list_deliberations(self, template_id: int) -> list[dict[str, Any]]:
        return self.list_jury_sessions(template_id)

    def add_jury_session(
        self,
        template_id: int,
        session_kind: str,
        *,
        label: str = "",
        notes: str = "",
        scope_text: str = "",
        roster_id: int | None = None,
    ) -> int:
        sk = str(session_kind or "S1").strip().upper()
        if sk not in {"S1", "S2", "FINAL"}:
            sk = "S1"
        mx = self.db.query_one(
            """
            SELECT COALESCE(MAX(display_order), -1) + 1 AS n
            FROM jury_sessions WHERE template_id = ?
            """,
            (int(template_id),),
        )
        ord_ = int(mx["n"]) if mx and mx["n"] is not None else 0
        cur = self.db.execute(
            """
            INSERT INTO jury_sessions(
                template_id, roster_id, session_kind, label, scope_text, notes, display_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(template_id),
                int(roster_id) if roster_id is not None else None,
                sk,
                str(label or "").strip(),
                str(scope_text or "").strip(),
                str(notes or "").strip(),
                ord_,
            ),
        )
        return int(cur.lastrowid)

    def add_deliberation(
        self,
        template_id: int,
        session_kind: str,
        *,
        label: str = "",
        notes: str = "",
        scope_text: str = "",
        roster_id: int | None = None,
    ) -> int:
        return self.add_jury_session(
            template_id,
            session_kind,
            label=label,
            notes=notes,
            scope_text=scope_text,
            roster_id=roster_id,
        )

    def update_jury_session(
        self,
        jury_session_id: int,
        *,
        label: str | None = None,
        notes: str | None = None,
        scope_text: str | None = None,
        roster_id: int | None = None,
        clear_roster: bool = False,
    ) -> None:
        if label is not None:
            self.db.execute(
                "UPDATE jury_sessions SET label = ? WHERE id = ?",
                (str(label).strip(), int(jury_session_id)),
            )
        if notes is not None:
            self.db.execute(
                "UPDATE jury_sessions SET notes = ? WHERE id = ?",
                (str(notes).strip(), int(jury_session_id)),
            )
        if scope_text is not None:
            self.db.execute(
                "UPDATE jury_sessions SET scope_text = ? WHERE id = ?",
                (str(scope_text).strip(), int(jury_session_id)),
            )
        if roster_id is not None:
            self.db.execute(
                "UPDATE jury_sessions SET roster_id = ? WHERE id = ?",
                (int(roster_id), int(jury_session_id)),
            )
        elif clear_roster:
            self.db.execute(
                "UPDATE jury_sessions SET roster_id = NULL WHERE id = ?",
                (int(jury_session_id),),
            )

    def delete_jury_session(self, jury_session_id: int) -> None:
        self.db.execute("DELETE FROM jury_sessions WHERE id = ?", (int(jury_session_id),))

    def update_deliberation(
        self,
        deliberation_id: int,
        *,
        label: str | None = None,
        notes: str | None = None,
        scope_text: str | None = None,
        roster_id: int | None = None,
        clear_roster: bool = False,
    ) -> None:
        self.update_jury_session(
            deliberation_id,
            label=label,
            notes=notes,
            scope_text=scope_text,
            roster_id=roster_id,
            clear_roster=clear_roster,
        )

    def delete_deliberation(self, deliberation_id: int) -> None:
        self.delete_jury_session(deliberation_id)

    def list_jury_members(self, jury_session_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM jury_members WHERE jury_session_id = ?
            ORDER BY is_president DESC, display_order, id
            """,
            (int(jury_session_id),),
        )
        return [dict(r) for r in rows]

    def set_jury_session_president(self, jury_session_id: int, member_id: int) -> None:
        self.db.execute(
            "UPDATE jury_members SET is_president = 0 WHERE jury_session_id = ?",
            (int(jury_session_id),),
        )
        self.db.execute(
            """
            UPDATE jury_members
            SET is_president = 1
            WHERE id = ? AND jury_session_id = ?
            """,
            (int(member_id), int(jury_session_id)),
        )

    def clear_jury_session_president(self, jury_session_id: int) -> None:
        self.db.execute(
            "UPDATE jury_members SET is_president = 0 WHERE jury_session_id = ?",
            (int(jury_session_id),),
        )

    def list_jury_members_for_deliberation(self, deliberation_id: int) -> list[dict[str, Any]]:
        sess = self.get_jury_session(int(deliberation_id))
        if sess and sess.get("roster_id"):
            return self.list_jury_roster_members(int(sess["roster_id"]))
        return self.list_jury_members(deliberation_id)

    def add_jury_member(
        self,
        jury_session_id: int,
        *,
        last_name: str,
        first_name: str,
        title: str = "",
        institution: str = "",
        is_president: bool = False,
    ) -> int:
        mx = self.db.query_one(
            """
            SELECT COALESCE(MAX(display_order), -1) + 1 AS n
            FROM jury_members WHERE jury_session_id = ?
            """,
            (int(jury_session_id),),
        )
        ord_ = int(mx["n"]) if mx and mx["n"] is not None else 0
        cur = self.db.execute(
            """
            INSERT INTO jury_members(
                jury_session_id, last_name, first_name, title, institution, is_president, display_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(jury_session_id),
                str(last_name or "").strip(),
                str(first_name or "").strip(),
                str(title or "").strip(),
                str(institution or "").strip(),
                1 if is_president else 0,
                ord_,
            ),
        )
        new_id = int(cur.lastrowid)
        if is_president:
            self.set_jury_session_president(int(jury_session_id), new_id)
        return new_id

    def update_jury_member(
        self,
        member_id: int,
        *,
        last_name: str | None = None,
        first_name: str | None = None,
        title: str | None = None,
        institution: str | None = None,
    ) -> None:
        row = self.db.query_one("SELECT * FROM jury_members WHERE id = ?", (int(member_id),))
        if not row:
            return
        d = dict(row)
        ln = str(last_name).strip() if last_name is not None else d["last_name"]
        fn = str(first_name).strip() if first_name is not None else d["first_name"]
        ti = str(title).strip() if title is not None else d["title"]
        ins = str(institution).strip() if institution is not None else d["institution"]
        self.db.execute(
            """
            UPDATE jury_members
            SET last_name = ?, first_name = ?, title = ?, institution = ?
            WHERE id = ?
            """,
            (ln, fn, ti, ins, int(member_id)),
        )

    def delete_jury_member(self, member_id: int) -> None:
        self.db.execute("DELETE FROM jury_members WHERE id = ?", (int(member_id),))

    def replace_jury_session_members(
        self, jury_session_id: int, members: list[dict[str, Any]]
    ) -> int:
        """Remplace les membres ad hoc d'une délibération (sans composition liée)."""
        jsid = int(jury_session_id)
        self.db.execute("DELETE FROM jury_members WHERE jury_session_id = ?", (jsid,))
        n = 0
        president_id: int | None = None
        for m in members:
            mid = self.add_jury_member(
                jsid,
                last_name=str(m.get("last_name") or ""),
                first_name=str(m.get("first_name") or ""),
                title=str(m.get("title") or ""),
                institution=str(m.get("institution") or ""),
            )
            if int(m.get("is_president") or 0):
                president_id = mid
            n += 1
        if president_id is not None:
            self.set_jury_session_president(jsid, president_id)
        return n

    def list_jury_adjustments_for_export(
        self, template_id: int, *, jury_session_id: int | None = None
    ) -> list[dict[str, Any]]:
        tid = int(template_id)
        if jury_session_id is not None:
            self.repair_jury_decision_session_links(tid)
        rows = self.db.query_all(
            """
            SELECT ja.*, s.last_name AS st_last, s.first_name AS st_first,
                   s.student_number, s.student_number_ine,
                   c.code AS course_code, c.name AS course_name
            FROM jury_adjustments ja
            JOIN students s ON s.id = ja.student_id
            LEFT JOIN courses c ON c.id = ja.course_id
            WHERE ja.template_id = ?
            ORDER BY s.last_name, s.first_name, ja.id
            """,
            (int(template_id),),
        )
        out = [dict(r) for r in rows]
        if jury_session_id is None:
            return out
        return [
            row
            for row in out
            if self._row_belongs_to_jury_pv(
                row, int(template_id), int(jury_session_id), kind="adjustment"
            )
        ]

    def list_templates_for_year_level(
        self, academic_year: str, level: str, *, tracks: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Maquettes pour un millésime et un niveau ; filtre optionnel par codes parcours."""
        ay = (academic_year or "").strip()
        lv = (level or "").strip().upper()
        want = {str(t).strip().upper() for t in (tracks or []) if str(t).strip()}
        out: list[dict[str, Any]] = []
        for t in self.list_templates():
            if ay and str(t.get("academic_year") or "").strip() != ay:
                continue
            if lv and str(t.get("level") or "").strip().upper() != lv:
                continue
            tr = str(t.get("track") or "").strip().upper()
            if want and tr not in want:
                continue
            out.append(t)
        return out

    def upsert_jury_student_outcome(
        self,
        student_id: int,
        template_id: int,
        *,
        jury_session_id: int | None = None,
        outcome: str | None = None,
        mention: str | None = None,
        comment: str | None = None,
        progression_track: str | None = None,
    ) -> None:
        """Décision de jury finale : validate_year, pass_m2, repeat, refuse_repeat + mention."""
        sid, tid = int(student_id), int(template_id)
        jsid = int(jury_session_id) if jury_session_id is not None else None
        existing = self.get_jury_student_outcome(sid, tid, jury_session_id=jsid) or {}
        oc = (
            str(outcome).strip().lower()
            if outcome is not None
            else str(existing.get("outcome") or "").strip().lower()
        )
        mn = (
            str(mention).strip().lower()
            if mention is not None
            else str(existing.get("mention") or "").strip().lower()
        )
        cm = (
            str(comment or "").strip()
            if comment is not None
            else str(existing.get("comment") or "").strip()
        )
        ptr = (
            str(progression_track or "").strip().upper()
            if progression_track is not None
            else str(existing.get("progression_track") or "").strip().upper()
        )
        if not oc and not cm and not mn and not ptr:
            self.db.execute(
                """
                DELETE FROM jury_student_outcomes
                WHERE student_id = ? AND template_id = ?
                  AND IFNULL(jury_session_id, -1) = IFNULL(?, -1)
                """,
                (sid, tid, jsid),
            )
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.db.execute(
            """
            INSERT INTO jury_student_outcomes(
                student_id, template_id, jury_session_id, outcome, mention, comment,
                progression_track, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, jury_session_id)
            DO UPDATE SET
                outcome = excluded.outcome,
                mention = excluded.mention,
                comment = excluded.comment,
                progression_track = excluded.progression_track,
                updated_at = excluded.updated_at
            """,
            (sid, tid, jsid, oc, mn, cm, ptr, now),
        )

    def get_jury_student_outcome(
        self,
        student_id: int,
        template_id: int,
        *,
        jury_session_id: int | None = None,
    ) -> dict[str, Any] | None:
        jsid = int(jury_session_id) if jury_session_id is not None else None
        row = self.db.query_one(
            """
            SELECT * FROM jury_student_outcomes
            WHERE student_id = ? AND template_id = ?
              AND IFNULL(jury_session_id, -1) = IFNULL(?, -1)
            """,
            (int(student_id), int(template_id), jsid),
        )
        return dict(row) if row else None

    def list_jury_student_outcomes_for_export(
        self, template_id: int, *, jury_session_id: int | None = None
    ) -> list[dict[str, Any]]:
        jsid = int(jury_session_id) if jury_session_id is not None else None
        rows = self.db.query_all(
            """
            SELECT o.*, s.last_name AS st_last, s.first_name AS st_first,
                   s.student_number, s.student_number_ine
            FROM jury_student_outcomes o
            JOIN students s ON s.id = o.student_id
            WHERE o.template_id = ?
              AND IFNULL(o.jury_session_id, -1) = IFNULL(?, -1)
              AND TRIM(IFNULL(o.outcome, '')) != ''
            ORDER BY s.last_name, s.first_name
            """,
            (int(template_id), jsid),
        )
        return [dict(r) for r in rows]

    def apply_final_jury_progression(
        self,
        student_id: int,
        template_id: int,
        *,
        jury_session_id: int | None = None,
        new_academic_year: str = "",
        m2_track: str = "",
    ) -> str:
        """Applique passage M2, redoublement ou clôture M2 selon la décision jury finale."""
        from .dates import suggest_next_academic_year

        _OUTCOME_FR = {
            "validate_year": "Année validée",
            "pass_m2": "Admis en M2",
            "repeat": "Redoublement",
            "refuse_repeat": "Refus de redoublement",
        }

        tpl = self.get_template(int(template_id)) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        cur_ay = str(tpl.get("academic_year") or "").strip()
        oc_row = self.get_jury_student_outcome(
            int(student_id), int(template_id), jury_session_id=jury_session_id
        )
        if not oc_row:
            raise ValueError("Aucune décision de jury enregistrée pour cet étudiant.")
        outcome = str(oc_row.get("outcome") or "").strip().lower()

        student = self.get_student(int(student_id)) or {}
        name = f"{student.get('first_name', '')} {student.get('last_name', '')}".strip()

        if outcome == "validate_year":
            if lv != "M2":
                raise ValueError("La clôture de formation s'applique aux maquettes M2.")
            if normalize_student_status(student.get("status")) == STUDENT_STATUS_GRADUATED:
                return f"{name or 'Étudiant'} : formation déjà clôturée (diplômé)"
            self.set_student_graduated(int(student_id), graduated=True)
            return f"{name or 'Étudiant'} : formation terminée — statut diplômé"

        target_ay = str(new_academic_year or "").strip() or suggest_next_academic_year(cur_ay)
        if not target_ay:
            raise ValueError("Millésime cible invalide ou manquant.")

        if outcome == "pass_m2":
            if lv != "M1":
                raise ValueError("Le passage M2 ne s'applique qu'aux maquettes M1.")
            track = (
                str(m2_track or "").strip().upper()
                or str(oc_row.get("progression_track") or "").strip().upper()
            )
            if not track:
                raise ValueError("Parcours M2 requis.")
            if (
                str(student.get("level") or "").strip().upper() == "M2"
                and str(student.get("academic_year") or "").strip() == target_ay
                and str(student.get("track") or "").strip().upper() == track
            ):
                return f"{name or 'Étudiant'} : passage M2 déjà appliqué ({track}, {target_ay})"
            self.promote_student_to_m2(int(student_id), target_ay, track)
            if not str(oc_row.get("progression_track") or "").strip():
                self.upsert_jury_student_outcome(
                    int(student_id),
                    int(template_id),
                    jury_session_id=jury_session_id,
                    progression_track=track,
                )
            return f"{name or 'Étudiant'} : passage en M2 {track} ({target_ay})"

        if outcome == "repeat":
            st_lv = normalize_level(student.get("level"))
            st_tr = normalize_track_acronym(student.get("track"))
            tpl_next = self.find_template_for_year_level_track(
                academic_year=target_ay,
                level=lv,
                track=st_tr or normalize_track_acronym(tpl.get("track")),
            )
            enrolled_next = False
            if tpl_next:
                enrolled_next = bool(
                    self.db.query_one(
                        """
                        SELECT 1 FROM enrollments
                        WHERE student_id = ? AND template_id = ?
                        """,
                        (int(student_id), int(tpl_next["id"])),
                    )
                )
            if (
                st_lv == lv
                and str(student.get("academic_year") or "").strip() == target_ay
                and enrolled_next
            ):
                return f"{name or 'Étudiant'} : redoublement déjà appliqué ({target_ay})"
            retake = self.courses_to_retake_for_student(
                int(student_id), int(template_id), view_session="mixed"
            )
            to_clear = [
                int(c["course_id"])
                for c in (retake.get("mandatory") or [])
                if c.get("course_id") is not None
            ]
            self.repeat_student_same_level(int(student_id), target_ay, to_clear)
            return f"{name or 'Étudiant'} : redoublement {lv} ({target_ay})"

        label = _OUTCOME_FR.get(outcome, outcome)
        raise ValueError(
            f"La décision « {label} » ne déclenche pas de mise à jour automatique de la fiche."
        )

    def apply_all_final_jury_progressions(
        self,
        template_id: int,
        *,
        jury_session_id: int | None = None,
        new_academic_year: str = "",
    ) -> tuple[int, list[str]]:
        from .dates import suggest_next_academic_year

        tpl = self.get_template(int(template_id)) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        target_ay = (
            str(new_academic_year or "").strip()
            or suggest_next_academic_year(str(tpl.get("academic_year") or "").strip())
        )
        outcomes = self.list_jury_student_outcomes_for_export(
            int(template_id), jury_session_id=jury_session_id
        )
        applicable = (
            {"pass_m2", "repeat"}
            if lv == "M1"
            else {"validate_year", "repeat"}
            if lv == "M2"
            else set()
        )
        applied = 0
        errors: list[str] = []
        for row in outcomes:
            outcome = str(row.get("outcome") or "").strip().lower()
            if outcome not in applicable:
                continue
            sid = int(row["student_id"])
            label = f"{row.get('st_first', '')} {row.get('st_last', '')}".strip() or f"id {sid}"
            if outcome == "pass_m2" and not str(row.get("progression_track") or "").strip():
                errors.append(f"{label} : parcours M2 non renseigné")
                continue
            try:
                self.apply_final_jury_progression(
                    sid,
                    int(template_id),
                    jury_session_id=jury_session_id,
                    new_academic_year=target_ay if outcome != "validate_year" else "",
                )
                applied += 1
            except ValueError as exc:
                errors.append(f"{label} : {exc}")
        return applied, errors

    def list_second_session_for_export(
        self, template_id: int, *, jury_session_id: int | None = None
    ) -> list[dict[str, Any]]:
        tid = int(template_id)
        if jury_session_id is not None:
            self.repair_jury_decision_session_links(tid)
        rows = self.db.query_all(
            """
            SELECT d.*, s.last_name AS st_last, s.first_name AS st_first,
                   s.student_number, s.student_number_ine,
                   c.code AS course_code, c.name AS course_name
            FROM second_session_decisions d
            JOIN students s ON s.id = d.student_id
            JOIN courses c ON c.id = d.course_id
            WHERE d.template_id = ? AND d.sent = 1
            ORDER BY c.code, s.last_name, s.first_name
            """,
            (int(template_id),),
        )
        out = [dict(r) for r in rows]
        if jury_session_id is None:
            return out
        return [
            row
            for row in out
            if self._row_belongs_to_jury_pv(
                row, int(template_id), int(jury_session_id), kind="second_session"
            )
        ]

    def _jury_session_sort_key(self, sess: dict[str, Any]) -> tuple[int, int]:
        return (int(sess.get("display_order") or 0), int(sess["id"]))

    def list_prior_jury_sessions(
        self, template_id: int, jury_session_id: int
    ) -> list[dict[str, Any]]:
        current = self.get_jury_session(int(jury_session_id))
        if not current:
            return []
        kind = str(current.get("session_kind") or "")
        cur_key = self._jury_session_sort_key(current)
        out: list[dict[str, Any]] = []
        for sess in self.list_jury_sessions(int(template_id)):
            if str(sess.get("session_kind") or "") != kind:
                continue
            if self._jury_session_sort_key(sess) < cur_key:
                out.append(sess)
        return out

    def prior_jury_pv_block_keys(self, template_id: int, jury_session_id: int) -> set[str]:
        block_keys = self.list_template_block_keys_ordered(int(template_id))
        keys: set[str] = set()
        for sess in self.list_prior_jury_sessions(int(template_id), int(jury_session_id)):
            keys |= scope_text_to_block_keys(str(sess.get("scope_text") or ""), block_keys)
        current = self.get_jury_session(int(jury_session_id)) or {}
        if str(current.get("session_kind") or "").strip().upper() == "FINAL":
            for sess in self.list_jury_sessions(int(template_id)):
                if str(sess.get("session_kind") or "").strip().upper() == "S1":
                    keys |= scope_text_to_block_keys(str(sess.get("scope_text") or ""), block_keys)
        return keys

    def current_jury_pv_block_keys(
        self, template_id: int, jury_session_id: int
    ) -> set[str] | None:
        current = self.get_jury_session(int(jury_session_id)) or {}
        scope = str(current.get("scope_text") or "").strip()
        if not scope:
            return None
        keys = scope_text_to_block_keys(
            scope, self.list_template_block_keys_ordered(int(template_id))
        )
        return keys if keys else None

    def course_id_to_block_key_map(self, template_id: int) -> dict[int, str]:
        return {int(c["course_id"]): _block_key(c) for c in self.list_template_courses(template_id)}

    def jury_sessions_covering_block(
        self,
        template_id: int,
        block_key: str,
        *,
        session_kind: str = "S1",
    ) -> list[int]:
        """Délibérations dont le périmètre textuel couvre ce bloc (ordre chronologique)."""
        bk = str(block_key or "").strip()
        if not bk:
            return []
        block_keys = self.list_template_block_keys_ordered(int(template_id))
        want_kind = str(session_kind or "S1").strip().upper()
        out: list[int] = []
        for sess in self.list_jury_sessions(int(template_id)):
            if str(sess.get("session_kind") or "").strip().upper() != want_kind:
                continue
            covered = scope_text_to_block_keys(str(sess.get("scope_text") or ""), block_keys)
            if bk in covered:
                out.append(int(sess["id"]))
        return out

    def infer_legacy_jury_session_id(
        self,
        template_id: int,
        *,
        block_key: str = "",
        scope: str = "",
        session_kind: str = "S1",
    ) -> int | None:
        """Devine la délibération d'origine d'une décision sans jury_session_id."""
        if str(scope or "").strip().lower() == "year":
            for sess in self.list_jury_sessions(int(template_id)):
                if str(sess.get("session_kind") or "").strip().upper() == "FINAL":
                    return int(sess["id"])
            return None
        bk = str(block_key or "").strip()
        if not bk:
            return None
        matches = self.jury_sessions_covering_block(
            int(template_id), bk, session_kind=session_kind
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return min(
                matches,
                key=lambda sid: self._jury_session_sort_key(self.get_jury_session(int(sid)) or {}),
            )
        blk_n = block_key_bloc_number(bk)
        if blk_n is None:
            return None
        fallback: list[int] = []
        block_keys = self.list_template_block_keys_ordered(int(template_id))
        want_kind = str(session_kind or "S1").strip().upper()
        for sess in self.list_jury_sessions(int(template_id)):
            if str(sess.get("session_kind") or "").strip().upper() != want_kind:
                continue
            nums = extract_bloc_numbers(str(sess.get("scope_text") or ""))
            if blk_n in nums:
                fallback.append(int(sess["id"]))
        if len(fallback) == 1:
            return fallback[0]
        if len(fallback) > 1:
            return min(
                fallback,
                key=lambda sid: self._jury_session_sort_key(self.get_jury_session(int(sid)) or {}),
            )
        return None

    def _infer_jury_adjustment_session_id(
        self,
        template_id: int,
        row: dict[str, Any],
        *,
        course_map: dict[int, str] | None = None,
    ) -> int | None:
        """Délibération d'origine d'un point jury (ligne taguée ou legacy)."""
        jsid = row.get("jury_session_id")
        if jsid is not None:
            return int(jsid)
        tid = int(template_id)
        scope = str(row.get("scope") or "").strip().lower()
        if scope == "year":
            return self.infer_legacy_jury_session_id(tid, scope="year")
        if scope == "block":
            return self.infer_legacy_jury_session_id(
                tid, block_key=str(row.get("block_name") or "").strip(), session_kind="S1"
            )
        if scope == "course":
            cmap = course_map if course_map is not None else self.course_id_to_block_key_map(tid)
            bk = cmap.get(int(row.get("course_id") or 0), "")
            return self.infer_legacy_jury_session_id(tid, block_key=bk, session_kind="S1")
        return None

    def _jury_adjustment_identity_key(
        self,
        template_id: int,
        row: dict[str, Any],
        *,
        course_map: dict[int, str] | None = None,
    ) -> tuple[int, int, str, int | None, str, int | None] | None:
        jsid = self._infer_jury_adjustment_session_id(
            int(template_id), row, course_map=course_map
        )
        if jsid is None:
            return None
        scope = str(row.get("scope") or "").strip().lower()
        cid = row.get("course_id")
        return (
            int(row["student_id"]),
            int(template_id),
            scope,
            int(cid) if cid is not None else None,
            str(row.get("block_name") or "").strip(),
            int(jsid),
        )

    def _jury_adjustment_row_rank(
        self, template_id: int, row: dict[str, Any], *, course_map: dict[int, str] | None = None
    ) -> tuple[int, int, int]:
        jsid = row.get("jury_session_id")
        if jsid is not None:
            sess = self.get_jury_session(int(jsid)) or {}
        else:
            inferred = self._infer_jury_adjustment_session_id(
                int(template_id), row, course_map=course_map
            )
            sess = self.get_jury_session(int(inferred)) or {} if inferred is not None else {}
        sk = self._jury_session_sort_key(sess)
        return (int(sk[0]), int(sk[1]), int(row["id"]))

    def deduplicate_jury_adjustments(self, template_id: int) -> int:
        """
        Supprime les doublons de points jury.

        - même délibération (legacy sans session + ligne taguée) ;
        - plusieurs délibérations pour la même UE / bloc / année → la plus récente l'emporte.
        """
        from collections import defaultdict

        tid = int(template_id)
        course_map = self.course_id_to_block_key_map(tid)
        removed = 0

        def _drop_extras(groups: dict[tuple, list[Any]]) -> None:
            nonlocal removed
            for group in groups.values():
                if len(group) <= 1:
                    continue
                keep = max(
                    group,
                    key=lambda r: self._jury_adjustment_row_rank(
                        tid, dict(r), course_map=course_map
                    ),
                )
                keep_id = int(keep["id"])
                for row in group:
                    rid = int(row["id"])
                    if rid == keep_id:
                        continue
                    self.db.execute("DELETE FROM jury_adjustments WHERE id = ?", (rid,))
                    removed += 1

        def _reload() -> list[Any]:
            return self.db.query_all(
                "SELECT * FROM jury_adjustments WHERE template_id = ? ORDER BY id",
                (tid,),
            )

        rows = _reload()

        by_session: dict[tuple, list[Any]] = defaultdict(list)
        for row in rows:
            key = self._jury_adjustment_identity_key(tid, dict(row), course_map=course_map)
            if key is None:
                continue
            by_session[key].append(row)
        _drop_extras(by_session)

        rows = _reload()
        by_course: dict[tuple[int, int], list[Any]] = defaultdict(list)
        for row in rows:
            if str(row["scope"] or "").strip().lower() != "course" or row["course_id"] is None:
                continue
            by_course[(int(row["student_id"]), int(row["course_id"]))].append(row)
        _drop_extras(by_course)

        rows = _reload()
        by_block: dict[tuple[int, str], list[Any]] = defaultdict(list)
        for row in rows:
            if str(row["scope"] or "").strip().lower() != "block":
                continue
            by_block[
                (int(row["student_id"]), str(row["block_name"] or "").strip())
            ].append(row)
        _drop_extras(by_block)

        rows = _reload()
        by_year: dict[int, list[Any]] = defaultdict(list)
        for row in rows:
            if str(row["scope"] or "").strip().lower() != "year":
                continue
            by_year[int(row["student_id"])].append(row)
        _drop_extras(by_year)

        return removed

    def repair_jury_decision_session_links(self, template_id: int) -> int:
        """
        Rattache les envois S2 / points jury sans session à la délibération
        dont le périmètre couvre le bloc concerné.
        """
        tid = int(template_id)
        course_map = self.course_id_to_block_key_map(tid)
        n = 0

        s2_rows = self.db.query_all(
            """
            SELECT id, course_id
            FROM second_session_decisions
            WHERE template_id = ? AND sent = 1 AND jury_session_id IS NULL
            """,
            (tid,),
        )
        for row in s2_rows:
            cid = int(row["course_id"])
            bk = course_map.get(cid, "")
            jsid = self.infer_legacy_jury_session_id(tid, block_key=bk, session_kind="S1")
            if jsid is None:
                continue
            self.db.execute(
                "UPDATE second_session_decisions SET jury_session_id = ? WHERE id = ?",
                (int(jsid), int(row["id"])),
            )
            n += 1

        adj_rows = self.db.query_all(
            """
            SELECT id, student_id, scope, course_id, block_name
            FROM jury_adjustments
            WHERE template_id = ? AND jury_session_id IS NULL
            """,
            (tid,),
        )
        for row in adj_rows:
            scope = str(row["scope"] or "").strip().lower()
            sid = int(row["student_id"])
            cid = row["course_id"]
            bname = str(row["block_name"] or "").strip()
            if scope == "year":
                jsid = self.infer_legacy_jury_session_id(tid, scope="year")
            elif scope == "block":
                jsid = self.infer_legacy_jury_session_id(
                    tid, block_key=bname, session_kind="S1"
                )
            elif scope == "course":
                bk = course_map.get(int(cid or 0), "")
                jsid = self.infer_legacy_jury_session_id(tid, block_key=bk, session_kind="S1")
            else:
                continue
            if jsid is None:
                continue
            existing = self.db.query_one(
                """
                SELECT id FROM jury_adjustments
                WHERE student_id = ? AND template_id = ? AND scope = ?
                  AND IFNULL(course_id, -999999) = IFNULL(?, -999999)
                  AND TRIM(IFNULL(block_name, '')) = TRIM(IFNULL(?, ''))
                  AND jury_session_id = ?
                  AND id != ?
                """,
                (sid, tid, scope, cid, bname, int(jsid), int(row["id"])),
            )
            if existing:
                self.db.execute(
                    "DELETE FROM jury_adjustments WHERE id = ?",
                    (int(row["id"]),),
                )
            else:
                self.db.execute(
                    "UPDATE jury_adjustments SET jury_session_id = ? WHERE id = ?",
                    (int(jsid), int(row["id"])),
                )
            n += 1
        n += self.deduplicate_jury_adjustments(tid)
        return n

    def _infer_row_owner_jury_session(
        self,
        row: dict[str, Any],
        template_id: int,
        *,
        kind: str,
    ) -> int | None:
        """Délibération qui a produit cette décision (pour PV et rattachement legacy)."""
        if kind == "adjustment":
            scope = str(row.get("scope") or "").strip().lower()
            if scope == "year":
                return self.infer_legacy_jury_session_id(int(template_id), scope="year")
            if scope == "block":
                return self.infer_legacy_jury_session_id(
                    int(template_id),
                    block_key=str(row.get("block_name") or "").strip(),
                    session_kind="S1",
                )
            if scope == "course":
                course_map = self.course_id_to_block_key_map(int(template_id))
                bk = course_map.get(int(row.get("course_id") or 0), "")
                return self.infer_legacy_jury_session_id(
                    int(template_id), block_key=bk, session_kind="S1"
                )
            return None
        course_map = self.course_id_to_block_key_map(int(template_id))
        bk = course_map.get(int(row.get("course_id") or 0), "")
        return self.infer_legacy_jury_session_id(
            int(template_id), block_key=bk, session_kind="S1"
        )

    def _row_belongs_to_jury_pv(
        self,
        row: dict[str, Any],
        template_id: int,
        jury_session_id: int,
        *,
        kind: str,
    ) -> bool:
        """Un PV ne reprend que les décisions de la délibération concernée."""
        row_jsid = row.get("jury_session_id")
        if row_jsid is not None:
            return int(row_jsid) == int(jury_session_id)
        owner = self._infer_row_owner_jury_session(row, template_id, kind=kind)
        return owner is not None and int(owner) == int(jury_session_id)

    def list_template_block_keys_ordered(self, template_id: int) -> list[str]:
        """Noms de blocs (``block_name``) dans l’ordre d’apparition du template, hors cours optionnels."""
        courses = self.list_template_courses(template_id)
        order: list[str] = []
        seen: set[str] = set()
        for c in courses:
            if int(c.get("optional") or 0):
                continue
            bk = _block_key(c)
            if bk not in seen:
                seen.add(bk)
                order.append(bk)
        return order

    def _resolve_course_view_session_for_grades(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        view_session: str,
    ) -> str:
        vs = (view_session or "s1").strip().lower()
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if vs == "mixed":
            return "s2" if self.course_retains_session2_grades(sid, tid, cid) else "s1"
        if vs == "s2":
            return (
                "s2"
                if self.course_uses_session2_grades(sid, tid, cid, view_session="s2")
                else "s1"
            )
        return "s1"

    def student_ue_note_with_jury(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        view_session: str,
        result_row: dict[str, Any] | None = None,
    ) -> float | None:
        """
        Note d'UE de l'étudiant : moyenne MCC de la session affichée + points jury sur l'UE.
        Sert aux règles de validation (seuils 7 et 10) et à la moyenne de bloc.
        """
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        vs_raw = (view_session or "s1").strip().lower()
        if result_row is not None:
            ud = (result_row.get("ue_detail") or {}).get(cid) or (
                result_row.get("ue_detail") or {}
            ).get(str(cid))
            if ud:
                display = str(ud.get("display") or "").strip().upper()
                if display in (STATUS_DEF, STATUS_ABJ, STATUS_NEUT, STATUS_VAL):
                    return None
                total = ud.get("total")
                if total is not None:
                    return float(total)

        vs_eff = self._resolve_course_view_session_for_grades(sid, tid, cid, vs_raw)
        use_s2 = vs_eff == "s2"
        if use_s2:
            base = self.compute_course_average_s2(sid, cid, template_id=tid)
        else:
            base = self.compute_course_average_s1(sid, cid)
        sent_s2 = self.is_sent_to_second_session(sid, tid, cid)
        display = self.course_ue_display_label(
            sid,
            tid,
            cid,
            view_session=vs_eff,
            session_average=base,
            sent_s2=bool(sent_s2),
            use_s2=use_s2,
        )
        if display in (STATUS_DEF, STATUS_ABJ, STATUS_NEUT, STATUS_VAL):
            return None
        jury_m = self._jury_map_for_template(tid).get(
            sid, {"course": {}, "block": {}, "year": 0.0}
        )
        jp = float(jury_m.get("course", {}).get(cid, 0.0))
        return _session_grade_plus_jury(base, jp)

    def get_course_cohort_ue_average(
        self,
        template_id: int,
        course_id: int,
        *,
        view_session: str = "s1",
    ) -> float | None:
        """Moyenne UE (sans jury) sur les inscrits à la maquette — comparaison promo, informative."""
        tid, cid = int(template_id), int(course_id)
        vs = (view_session or "s1").strip().lower()
        student_ids = [
            int(r["student_id"])
            for r in self.db.query_all(
                "SELECT student_id FROM enrollments WHERE template_id = ?", (tid,)
            )
        ]
        vals: list[float] = []
        for sid in student_ids:
            if vs == "mixed":
                vs_eff = self._resolve_course_view_session_for_grades(sid, tid, cid, vs)
            elif vs == "s2":
                vs_eff = (
                    "s2"
                    if self.course_uses_session2_grades(sid, tid, cid, view_session="s2")
                    else "s1"
                )
            else:
                vs_eff = "s1"
            if vs_eff == "s2":
                avg = self.compute_course_average_s2(sid, cid, template_id=tid)
            else:
                avg = self.compute_course_average_s1(sid, cid)
            if avg is not None:
                vals.append(float(avg))
        if not vals:
            return None
        return sum(vals) / len(vals)

    def _course_has_unlocked_grade_below(
        self,
        student_id: int,
        course_id: int,
        *,
        view_session: str,
        floor: float = 7.0,
        template_id: int | None = None,
    ) -> bool:
        """
        True si la note d'UE (MCC + jury) est < ``floor`` sans dérogation « seuil 7 ».
        DEF et ABJ bloquent toujours.
        """
        if template_id is None:
            return False
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if self.has_ue_ects_validation(sid, tid, cid):
            return False
        if self.has_ue_jury_floor_waiver(sid, tid, cid):
            return False
        vs_eff = self._resolve_course_view_session_for_grades(sid, tid, cid, view_session)
        use_s2 = vs_eff == "s2"
        base = (
            self.compute_course_average_s2(sid, cid, template_id=tid)
            if use_s2
            else self.compute_course_average_s1(sid, cid)
        )
        display = self.course_ue_display_label(
            sid,
            tid,
            cid,
            view_session=vs_eff,
            session_average=base,
            sent_s2=bool(self.is_sent_to_second_session(sid, tid, cid)),
            use_s2=use_s2,
        )
        if status_blocks_validation(display):
            return True
        note = self.student_ue_note_with_jury(
            sid, tid, cid, view_session=view_session
        )
        if note is None:
            return False
        return grade_below_threshold(note, float(floor))

    def block_has_unlocked_subthreshold_grade(
        self,
        student_id: int,
        template_id: int,
        block_name: str,
        *,
        view_session: str,
        floor: float = 7.0,
    ) -> bool:
        """
        True si au moins une UE non optionnelle du bloc a une note d'UE (MCC + jury)
        strictement inférieure à ``floor`` (7) sans dérogation « seuil 7 ».
        """
        bn = (block_name or "").strip()
        tid = int(template_id)
        cids = [
            int(c["course_id"])
            for c in self.list_template_courses(tid)
            if int(c.get("optional") or 0) == 0 and _block_key(c) == bn
        ]
        sid = int(student_id)
        for c in self.list_template_courses(tid):
            if int(c.get("optional") or 0) or _block_key(c) != bn:
                continue
            cid = int(c["course_id"])
            if self.has_ue_ects_validation(sid, tid, cid):
                continue
            if self._course_has_unlocked_grade_below(
                sid, cid, view_session=view_session, floor=floor, template_id=tid
            ):
                return True
        return False

    def block_is_validated(
        self,
        student_id: int,
        template_id: int,
        block_name: str,
        *,
        view_session: str,
        block_average: float | None,
        floor: float = 7.0,
    ) -> bool:
        """
        Bloc validé (règles MNE) :

        - moyenne de bloc (notes d'UE pondérées ECTS, avec jury UE + jury bloc) ≥ 10,
          sauf dérogation jury « valider bloc < 10 » ;
        - aucune note d'UE (MCC + jury UE) strictement inférieure à ``floor`` (7)
          sans dérogation jury « seuil 7 ».
        """
        if block_average is None:
            return False
        if grade_below_threshold(block_average, 10.0) and not self.has_block_jury_validation_waiver(
            int(student_id), int(template_id), block_name
        ):
            return False
        return not self.block_has_unlocked_subthreshold_grade(
            student_id, template_id, block_name, view_session=view_session, floor=floor
        )

    def block_has_mandatory_courses(self, template_id: int, block_name: str) -> bool:
        """True si le bloc contient au moins une UE non optionnelle (hors moyennes si optionnelles seules)."""
        bn = (block_name or "").strip()
        return any(
            int(c.get("optional") or 0) == 0
            for c in self.list_template_courses(int(template_id))
            if _block_key(c) == bn
        )

    def block_allows_ue_compensation(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        result_row: dict[str, Any] | None = None,
        view_session: str = "s1",
    ) -> bool:
        return (
            self.block_ue_compensation_status(
                student_id,
                template_id,
                course_id,
                result_row=result_row,
                view_session=view_session,
            )
            == "allowed"
        )

    def _ue_is_eliminatory_for_block_compensation(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        ue_detail: dict[str, Any],
        view_session: str,
    ) -> bool:
        """
        UE éliminatoire pour la compensation des autres UE du bloc :
        DEF/ABJ, ou note d'UE (MCC + jury) < 7 sans dérogation jury.
        """
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        display = str(ue_detail.get("display") or "").strip().upper()
        if display in (STATUS_DEF, STATUS_ABJ):
            return True
        if display in (STATUS_VAL, STATUS_NEUT):
            return False
        if self.has_ue_ects_validation(sid, tid, cid):
            return False
        if self.has_ue_jury_floor_waiver(sid, tid, cid):
            return False
        vs = str(view_session or "s1").strip().lower()
        note = self.student_ue_note_with_jury(sid, tid, cid, view_session=vs)
        if note is None:
            use_s2 = bool(ue_detail.get("use_s2")) or self.course_uses_session2_grades(
                sid, tid, cid, view_session=vs if vs != "mixed" else "s2"
            )
            if vs in {"s2", "mixed"}:
                base = ue_detail.get("s2") if use_s2 else ue_detail.get("s1")
            else:
                base = ue_detail.get("s1")
            if base is None:
                return False
            jp = float(ue_detail.get("jury") or 0.0)
            note = _session_grade_plus_jury(base, jp)
        if note is None:
            return False
        if grade_meets_minimum(note, 10.0):
            return False
        return grade_below_threshold(note, 7.0)

    def block_ue_compensation_status(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        result_row: dict[str, Any] | None = None,
        view_session: str = "s1",
    ) -> str:
        """
        Compensation inter-UE dans un bloc (règles MNE) :

        - ``allowed`` : le bloc est validé (moy. ≥ 10, pas d'UE < 7 non dérogée) ;
        - ``incomplete`` : une autre UE obligatoire n'a pas encore de note ;
        - ``eliminating`` : bloc non validé ou une autre UE du bloc a une note < 7 (DEF/ABJ, …).
        """
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        block_name = ""
        for c in self.list_template_courses(tid):
            if int(c["course_id"]) == cid:
                block_name = _block_key(c)
                break
        if not block_name:
            return "allowed"
        if result_row is None:
            summary = self.get_student_result_summary(tid, view_session=view_session)
            result_row = next((r for r in summary if int(r.get("student_id") or 0) == sid), None)
        if not result_row:
            return "incomplete"
        vs = str(view_session or "s1").strip().lower()
        ud = result_row.get("ue_detail") or {}
        bn = block_name.strip()
        block_avg = (result_row.get("blocks") or {}).get(block_name)
        if block_avg is None:
            for k, v in (result_row.get("blocks") or {}).items():
                if str(k or "").strip() == bn:
                    block_avg = v
                    break
        for c in self.list_template_courses(tid):
            if int(c.get("optional") or 0) or int(c.get("free_ue") or 0):
                continue
            if _block_key(c) != block_name:
                continue
            oc = int(c["course_id"])
            if oc == cid:
                continue
            if self.has_ue_ects_validation(sid, tid, oc):
                continue
            d = ud.get(oc) or ud.get(str(oc)) or {}
            display = str(d.get("display") or "").strip().upper()
            if display in (STATUS_VAL, STATUS_NEUT):
                continue
            if vs == "s2":
                base = d.get("s2") if d.get("use_s2") else d.get("s1")
            else:
                base = d.get("s1")
            if base is None:
                use_s2 = bool(d.get("use_s2")) or self.course_uses_session2_grades(
                    sid, tid, oc, view_session=vs
                )
                base = (
                    self.compute_course_average_s2(sid, oc)
                    if use_s2
                    else self.compute_course_average_s1(sid, oc)
                )
            if base is None and display not in (STATUS_DEF, STATUS_ABJ):
                return "incomplete"
            if self._ue_is_eliminatory_for_block_compensation(
                sid, tid, oc, ue_detail=d, view_session=vs
            ):
                return "eliminating"
        if not self.block_is_validated(
            sid, tid, block_name, view_session=vs, block_average=block_avg
        ):
            return "eliminating"
        return "allowed"

    def count_prior_same_level_years(
        self, student_id: int, level: str, *, before_academic_year: str
    ) -> int:
        """Années universitaires antérieures où l'étudiant était inscrit au même niveau."""
        sid = int(student_id)
        lv = str(level or "").strip().upper()
        before = str(before_academic_year or "").strip()
        years: set[str] = set()
        for enr in self.list_enrollments_for_student(sid):
            if str(enr.get("level") or "").strip().upper() != lv:
                continue
            ay = str(enr.get("academic_year") or "").strip()
            if not ay:
                continue
            if before and ay >= before:
                continue
            years.add(ay)
        return len(years)

    def courses_to_retake_for_student(
        self,
        student_id: int,
        template_id: int,
        *,
        view_session: str = "mixed",
        result_row: dict[str, Any] | None = None,
        threshold: float = 10.0,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        UE à repasser pour un redoublement autorisé.

        - ``mandatory`` : note < ``threshold`` (ou DEF/ABJ) dans un bloc **non validé** ;
        - ``recommended`` : note < ``threshold`` (ou DEF/ABJ) dans un bloc **validé** ;
        - les UE validées (VAL, NEUT, validation ECTS) ne figurent pas.
        """
        from ..core.mne_modules import course_ue_code
        from .timetable_legacy import course_public_code

        sid, tid = int(student_id), int(template_id)
        vs = str(view_session or "mixed").strip().lower()
        if vs not in {"s1", "s2", "mixed"}:
            vs = "mixed"
        tpl = self.get_template(tid) or {}
        tpl_ay = str(tpl.get("academic_year") or "").strip()
        row = result_row
        if row is None:
            rows = self.get_student_result_summary(
                tid, view_session=vs, include_all_students=True
            )
            row = next((r for r in rows if int(r.get("student_id") or 0) == sid), None)
        mandatory: list[dict[str, Any]] = []
        recommended: list[dict[str, Any]] = []
        if row is None:
            return {"mandatory": mandatory, "recommended": recommended}
        block_avgs = row.get("blocks") or {}
        ue_detail = row.get("ue_detail") or {}

        def _block_validated(bk: str) -> bool:
            blk_avg = _lookup_block_average(block_avgs, bk)
            if blk_avg is None:
                return False
            return self.block_is_validated(
                sid, tid, bk, view_session=vs, block_average=blk_avg
            )

        def _display_code(course: dict[str, Any]) -> str:
            pub = course_public_code(course, academic_year=tpl_ay)
            if pub:
                return pub
            mne = course_ue_code(course)
            if mne:
                return mne
            return str(course.get("code") or course.get("name") or "UE").strip()

        for course in self.list_template_courses(tid):
            if int(course.get("optional") or 0) or int(course.get("free_ue") or 0):
                continue
            cid = int(course["course_id"])
            if self.has_ue_ects_validation(sid, tid, cid):
                continue
            bk = _block_key(course)
            detail = ue_detail.get(cid) or ue_detail.get(str(cid)) or {}
            display = str(detail.get("display") or "").strip().upper()
            if display in (STATUS_VAL, STATUS_NEUT):
                continue

            block_ok = _block_validated(bk)
            code = _display_code(course)

            if display in (STATUS_DEF, STATUS_ABJ):
                entry = {
                    "course_id": cid,
                    "code": code,
                    "name": str(course.get("name") or ""),
                    "block_name": bk,
                    "note": None,
                    "status": display,
                }
                (recommended if block_ok else mandatory).append(entry)
                continue

            note = detail.get("total")
            if note is None:
                fin = detail.get("final")
                jp = float(detail.get("jury") or 0.0)
                if fin is not None:
                    note = float(fin) + jp
            if note is None:
                continue
            if grade_meets_minimum(note, float(threshold)):
                continue

            entry = {
                "course_id": cid,
                "code": code,
                "name": str(course.get("name") or ""),
                "block_name": bk,
                "note": float(note),
                "status": "",
            }
            (recommended if block_ok else mandatory).append(entry)
        return {"mandatory": mandatory, "recommended": recommended}

    def format_courses_to_retake_text(
        self,
        retake: dict[str, list[dict[str, Any]]],
        *,
        max_items: int = 12,
    ) -> str:
        """Texte court pour PV / exports : matières obligatoires et recommandées."""
        parts: list[str] = []
        mand = retake.get("mandatory") or []
        rec = retake.get("recommended") or []
        if mand:
            bits = []
            for c in mand[:max_items]:
                code = str(c.get("code") or c.get("name") or "UE").strip()
                st = str(c.get("status") or "").strip()
                n = c.get("note")
                if st:
                    bits.append(f"{code} ({st})")
                elif n is not None:
                    bits.append(f"{code} ({float(n):.2f})")
                else:
                    bits.append(code)
            txt = ", ".join(bits)
            if len(mand) > max_items:
                txt += f" (+{len(mand) - max_items})"
            parts.append(f"Obligatoire : {txt}")
        if rec:
            bits = []
            for c in rec[:max_items]:
                code = str(c.get("code") or c.get("name") or "UE").strip()
                n = c.get("note")
                bits.append(f"{code} ({float(n):.2f})" if n is not None else code)
            txt = ", ".join(bits)
            if len(rec) > max_items:
                txt += f" (+{len(rec) - max_items})"
            parts.append(f"Recommandé : {txt}")
        return " · ".join(parts)

    def evaluate_student_year_validation(
        self,
        student_id: int,
        template_id: int,
        *,
        view_session: str = "s2",
        year_threshold: float = 10.0,
        floor: float = 7.0,
        result_row: dict[str, Any] | None = None,
        auto_sync_s2: bool = True,
    ) -> dict[str, Any]:
        """
        Vérifie les règles de validation d'année (moyenne ≥ 10 avec jury, blocs validés).

        Retourne ``validated``, ``issues``, ``suggested_outcome`` (présélection non enregistrée)
        et ``proposed_outcomes`` (choix proposés au jury final).
        """
        sid, tid = int(student_id), int(template_id)
        tpl = self.get_template(tid) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        cur_ay = str(tpl.get("academic_year") or "").strip()
        vs = str(view_session or "mixed").strip().lower()
        if vs not in {"s1", "s2", "mixed"}:
            vs = "mixed"

        row = result_row
        if row is None:
            rows = self.get_student_result_summary(
                tid, view_session=vs, auto_sync_s2=auto_sync_s2, include_all_students=True
            )
            row = next((r for r in rows if int(r.get("student_id") or 0) == sid), None)
        issues: list[str] = []
        if row is None:
            issues.append("Aucune note calculable pour cet étudiant.")
        else:
            gwj = row.get("global_with_jury")
            if gwj is None or grade_below_threshold(float(gwj), float(year_threshold)):
                issues.append(
                    f"Moyenne année avec jury < {year_threshold:g} "
                    f"({_fmt_validation_num(gwj)})"
                )
            for bk, avg in (row.get("blocks") or {}).items():
                if not self.block_is_validated(
                    sid, tid, str(bk), view_session=vs, block_average=avg, floor=floor
                ):
                    issues.append(
                        f"Bloc « {bk} » non validé (moy. {_fmt_validation_num(avg)})"
                    )
            for cid, detail in (row.get("ue_detail") or {}).items():
                disp = str((detail or {}).get("display") or "").strip()
                if disp in (STATUS_DEF, STATUS_ABJ):
                    course = self.get_course(int(cid)) or {}
                    code = str(course.get("code") or course.get("name") or f"UE #{cid}")
                    issues.append(f"UE {code} : statut {disp}")

        prior_years = self.count_prior_same_level_years(sid, lv, before_academic_year=cur_ay)
        validated = not issues
        if validated:
            suggested = "pass_m2" if lv == "M1" else "validate_year"
            proposed = [suggested]
        else:
            proposed = ["repeat", "refuse_repeat"]
            if prior_years >= 1:
                suggested = "refuse_repeat"
            else:
                suggested = "repeat"
        return {
            "validated": validated,
            "issues": issues,
            "suggested_outcome": suggested,
            "proposed_outcomes": proposed,
            "prior_same_level_years": prior_years,
        }

    def suggest_jury_outcome(
        self,
        student_id: int,
        template_id: int,
        *,
        view_session: str = "s2",
        result_row: dict[str, Any] | None = None,
    ) -> str:
        """Présélection proposée au jury final (le jury choisit et enregistre)."""
        ev = self.evaluate_student_year_validation(
            int(student_id),
            int(template_id),
            view_session=view_session,
            result_row=result_row,
        )
        return str(ev.get("suggested_outcome") or "repeat")

    def persist_suggested_final_jury_outcomes(
        self,
        template_id: int,
        *,
        jury_session_id: int,
        view_session: str = "mixed",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """
        Enregistre outcome + mention suggérés pour tous les inscrits sans décision enregistrée.

        Indispensable pour les cas « non problématiques » jamais ouverts en délibération interactive.
        """
        from .jury_reports import transcript_mention_code_from_grade

        tid, jsid = int(template_id), int(jury_session_id)
        vs = str(view_session or "mixed").strip().lower()
        if vs not in {"s1", "s2", "mixed"}:
            vs = "mixed"
        rows = {
            int(r["student_id"]): r
            for r in self.get_student_result_summary(
                tid, view_session=vs, include_all_students=True, auto_sync_s2=True
            )
        }
        saved = 0
        skipped = 0
        by_outcome: dict[str, int] = {}
        names_saved: list[str] = []
        for stu in self.list_students_for_template(tid):
            sid = int(stu["id"])
            label = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip()
            oc = self.get_jury_student_outcome(sid, tid, jury_session_id=jsid)
            if oc and str(oc.get("outcome") or "").strip() and not overwrite:
                skipped += 1
                continue
            row = rows.get(sid)
            ev = self.evaluate_student_year_validation(
                sid, tid, view_session=vs, result_row=row, auto_sync_s2=True
            )
            outcome = str(ev.get("suggested_outcome") or "repeat")
            mention = ""
            if row and row.get("global_with_jury") is not None:
                mention = transcript_mention_code_from_grade(float(row["global_with_jury"]))
            self.upsert_jury_student_outcome(
                sid,
                tid,
                jury_session_id=jsid,
                outcome=outcome,
                mention=mention,
            )
            saved += 1
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            if label:
                names_saved.append(label)
        return {
            "saved": saved,
            "skipped": skipped,
            "total": len(self.list_students_for_template(tid)),
            "by_outcome": by_outcome,
            "names_saved": names_saved,
        }

    def get_final_jury_closure_status(
        self,
        template_id: int,
        *,
        jury_session_id: int,
        view_session: str = "mixed",
    ) -> dict[str, Any]:
        """Bilan de clôture : décisions, mentions, progressions en attente."""
        from .dates import suggest_next_academic_year

        tid, jsid = int(template_id), int(jury_session_id)
        tpl = self.get_template(tid) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        cur_ay = str(tpl.get("academic_year") or "").strip()
        next_ay = suggest_next_academic_year(cur_ay)
        vs = str(view_session or "mixed").strip().lower()
        if vs not in {"s1", "s2", "mixed"}:
            vs = "mixed"

        missing_outcome: list[str] = []
        missing_mention: list[str] = []
        pass_m2_no_track: list[str] = []
        progression_pending: list[str] = []
        with_outcome = 0

        for stu in self.list_students_for_template(tid):
            sid = int(stu["id"])
            name = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip() or f"id {sid}"
            oc = self.get_jury_student_outcome(sid, tid, jury_session_id=jsid) or {}
            outcome = str(oc.get("outcome") or "").strip().lower()
            if not outcome:
                missing_outcome.append(name)
                continue
            with_outcome += 1
            if not str(oc.get("mention") or "").strip():
                missing_mention.append(name)
            student = self.get_student(sid) or {}
            st_lv = str(student.get("level") or "").strip().upper()
            st_ay = str(student.get("academic_year") or "").strip()
            st_tr = str(student.get("track") or "").strip().upper()
            if outcome == "pass_m2":
                track = str(oc.get("progression_track") or "").strip().upper()
                if not track:
                    pass_m2_no_track.append(name)
                elif st_lv != "M2" or st_ay != next_ay or st_tr != track:
                    progression_pending.append(name)
            elif outcome == "repeat":
                st_lv = normalize_level(student.get("level"))
                st_tr = normalize_track_acronym(student.get("track"))
                tpl_track = st_tr or normalize_track_acronym(tpl.get("track"))
                tpl_next = self.find_template_for_year_level_track(
                    academic_year=next_ay, level=lv, track=tpl_track
                )
                enrolled_next = False
                if tpl_next:
                    enrolled_next = bool(
                        self.db.query_one(
                            """
                            SELECT 1 FROM enrollments
                            WHERE student_id = ? AND template_id = ?
                            """,
                            (sid, int(tpl_next["id"])),
                        )
                    )
                if st_ay != next_ay or st_lv != lv or not enrolled_next:
                    progression_pending.append(name)
            elif outcome == "validate_year" and lv == "M2":
                if normalize_student_status(student.get("status")) != STUDENT_STATUS_GRADUATED:
                    progression_pending.append(name)

        total = len(self.list_students_for_template(tid))
        decisions_complete = not missing_outcome
        ready_for_pv = decisions_complete
        ready_for_transcripts = decisions_complete and self.has_final_jury_session(tid)
        ready_for_progression = (
            decisions_complete and not pass_m2_no_track and not progression_pending
        )

        return {
            "total": total,
            "with_outcome": with_outcome,
            "missing_outcome": missing_outcome,
            "missing_mention": missing_mention,
            "pass_m2_no_track": pass_m2_no_track,
            "progression_pending": progression_pending,
            "decisions_complete": decisions_complete,
            "ready_for_pv": ready_for_pv,
            "ready_for_transcripts": ready_for_transcripts,
            "ready_for_progression": ready_for_progression,
            "next_academic_year": next_ay,
        }

    def get_student_result_summary(
        self,
        template_id: int,
        *,
        view_session: str = "s1",
        include_all_students: bool = False,
        auto_sync_s2: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Synthèse par étudiant pour un template.

        - Moyenne de chaque UE (cours) : depuis les notes / assessments (voir ``compute_course_average``).
        - **Moyenne par bloc** : moyenne pondérée ECTS des UE du bloc, chaque UE étant prise comme
          **moyenne de session + points de jury sur cette UE**. Les points de jury *bloc* / *année*
          restent des colonnes séparées (non additionnés dans ces moyennes).
          Les UE ``optional`` sont exclues.
        - **Moyenne année** : moyenne pondérée ECTS des **moyennes de bloc** (chaque bloc
          incluant jury UE et jury bloc). Les blocs ne se compensent pas entre eux pour la
          validation ; le jury année s'ajoute ensuite pour ``global_with_jury``.

        ``view_session`` :
        - ``s1`` : moyennes de bloc / année basées uniquement sur la session 1 pour chaque UE.
        - ``s2`` : par défaut, étudiants avec envoi S2 ou note S2 sur au moins une UE ;
          avec ``include_all_students=True``, tous les inscrits sont inclus.
          Pour chaque UE, moyenne S2 si l'UE est en seconde session, sinon S1.
        - ``mixed`` : tous les inscrits ; par UE, S2 si notes S2 disponibles (ou envoi S2),
          sinon S1 (transcripts partiels « état actuel »).

        **DEF / ABJ / S2** : avant agrégation, toute UE avec DEF ou ABJ en session 1 entraîne
        ``sync_second_session_obligations`` (envoi en seconde session obligatoire pour cette UE).

        Si une UE du bloc (ou de l’année) n’a pas encore de moyenne calculable, la moyenne du bloc
        (ou l’année) reste vide (``None``) plutôt qu’une valeur partielle trompeuse.
        """
        if auto_sync_s2:
            self.sync_second_session_obligations(int(template_id))
        students = self.list_students_for_template(template_id)
        vs = str(view_session or "s1").strip().lower()
        if vs not in {"s1", "s2", "mixed"}:
            vs = "s1"
        tid = int(template_id)

        courses = self.list_template_courses(template_id)
        t_row = self.db.query_one(
            "SELECT academic_year FROM templates WHERE id = ?", (int(template_id),)
        )
        tpl_ay = str((t_row["academic_year"] if t_row else "") or "").strip()
        jury_by_student = self._jury_map_for_template(int(template_id))
        results = []
        for student in students:
            sid = int(student["id"])
            erasmus_followed: set[int] | None = None
            if is_erasmus_student(student):
                erasmus_followed = set(self.list_student_erasmus_course_ids(sid, tpl_ay))
            if (
                vs in {"s2", "mixed"}
                and not include_all_students
                and vs != "mixed"
                and not self.student_has_second_session_presence(sid, tid)
            ):
                continue
            jury_m = jury_by_student.get(
                sid,
                {"course": {}, "block": {}, "year": 0.0},
            )
            course_values: dict[str, float | None] = {}
            ue_detail: dict[int, dict[str, Any]] = {}
            for course in courses:
                cid = int(course["course_id"])
                if erasmus_followed is not None and cid not in erasmus_followed:
                    continue
                s1 = self.compute_course_average_s1(sid, cid)
                s2 = self.compute_course_average_s2(sid, cid, template_id=tid)
                sent_s2 = self.is_sent_to_second_session(sid, tid, cid)
                allow_carry = self.is_second_session_carry_allowed(sid, cid, tid)
                if vs == "mixed":
                    use_s2 = self.course_retains_session2_grades(sid, tid, cid)
                elif vs == "s1":
                    use_s2 = False
                else:
                    use_s2 = self.course_uses_session2_grades(
                        sid, tid, cid, view_session="s2"
                    )
                fin = _compute_course_average_from_rows(
                    self.get_grades_for_student_course(sid, cid),
                    mode="final",
                    use_session2=use_s2,
                    allow_s1_reprise_carry=allow_carry if use_s2 else False,
                )
                jp = float(jury_m["course"].get(cid, 0.0))
                total_ue = (float(fin) + jp) if fin is not None else None
                if vs == "s1":
                    agg_course = s1
                else:
                    agg_course = s2 if use_s2 else s1
                label_vs = "s2" if use_s2 else "s1"
                display = self.course_ue_display_label(
                    sid,
                    int(template_id),
                    cid,
                    view_session=label_vs if vs == "mixed" else vs,
                    session_average=agg_course,
                    sent_s2=bool(sent_s2),
                    use_s2=use_s2,
                )
                if display in (STATUS_DEF, STATUS_ABJ, STATUS_NEUT, STATUS_VAL):
                    total_ue = None
                course_values[f"c:{cid}"] = agg_course
                ue_detail[cid] = {
                    "s1": s1,
                    "s2": s2,
                    "final": fin,
                    "sent_s2": bool(sent_s2),
                    "use_s2": bool(use_s2),
                    "jury": jp,
                    "total": total_ue,
                    "display": display or "",
                }

            block_items: dict[str, list[tuple[float | None, float]]] = {}
            block_ects: dict[str, float] = {}
            ects_validated_free = 0.0
            for course in courses:
                if int(course.get("optional") or 0):
                    continue
                cid = int(course["course_id"])
                if erasmus_followed is not None and cid not in erasmus_followed:
                    continue
                avg = course_values.get(f"c:{cid}")
                is_free = int(course.get("free_ue") or 0)
                detail = ue_detail.get(cid) or {}
                display = str(detail.get("display") or "").strip()
                if avg is None and self.has_ue_ects_validation(sid, int(template_id), cid):
                    if is_free:
                        ects_validated_free += float(course.get("ects") or 0)
                    ue_detail.setdefault(cid, {})["ects_validated"] = True
                    ue_detail.setdefault(cid, {})["display"] = STATUS_VAL
                    continue
                jp_ue = float(jury_m["course"].get(cid, 0.0))
                if display in (STATUS_DEF, STATUS_ABJ, STATUS_NEUT, STATUS_VAL):
                    eff = None
                else:
                    eff = _session_grade_plus_jury(avg, jp_ue)
                w = _template_course_weight(course)
                bk = _block_key(course)
                block_items.setdefault(bk, []).append((eff, w))
                if eff is not None:
                    block_ects[bk] = block_ects.get(bk, 0.0) + w

            block_avgs: dict[str, float | None] = {}
            for bk, items in block_items.items():
                if not items:
                    block_avgs[bk] = None
                elif any(g is None for g, _ in items):
                    block_avgs[bk] = None
                else:
                    base_blk = weighted_average([(float(g), w) for g, w in items])
                    bp = float(jury_m["block"].get(bk, 0.0))
                    block_avgs[bk] = _session_grade_plus_jury(base_blk, bp)

            global_avg: float | None = None
            year_block_items: list[tuple[float, float]] = []
            for bk, blk_avg in block_avgs.items():
                if blk_avg is None:
                    break
                bw = float(block_ects.get(bk, 0.0))
                if bw > 0:
                    year_block_items.append((float(blk_avg), bw))
            else:
                if year_block_items:
                    global_avg = weighted_average(year_block_items)

            y_j = float(jury_m.get("year") or 0.0)
            global_with_jury = (
                (float(global_avg) + y_j) if global_avg is not None else (y_j if abs(y_j) > 1e-12 else None)
            )

            results.append(
                {
                    "student_id": sid,
                    "student_number": student["student_number"],
                    "student_number_ine": student.get("student_number_ine"),
                    "last_name": student["last_name"],
                    "first_name": student["first_name"],
                    "global_average": global_avg,
                    "global_with_jury": global_with_jury,
                    "courses": course_values,
                    "blocks": block_avgs,
                    "ue_detail": ue_detail,
                    "ects_validated_free": ects_validated_free,
                    "jury": {
                        "course": dict(jury_m["course"]),
                        "block": dict(jury_m["block"]),
                        "year": y_j,
                    },
                }
            )
        return results

    # Demo data
    def seed_demo_data(self, academic_year: str = "") -> str:
        from .demo_seed import run_demo_seed

        return run_demo_seed(self, academic_year=academic_year)

    def seed_cohort_grades(self, academic_year: str = "", *, level: str = "M1", force: bool = False) -> str:
        from .cohort_grades_seed import run_cohort_grades_seed

        return run_cohort_grades_seed(
            self, academic_year=academic_year or "", level=level, force=force
        )

    def set_student_academic_year(self, student_id: int, academic_year: str) -> None:
        self.db.execute(
            "UPDATE students SET academic_year = ? WHERE id = ?",
            (str(academic_year or "").strip(), int(student_id)),
        )

    # --- Emploi du temps (import Excel secrétariat) ---

    def import_timetable(self, result) -> int:
        from .timetable_import import TimetableImportResult

        if not isinstance(result, TimetableImportResult):
            raise TypeError("TimetableImportResult attendu")
        year = (result.academic_year or "").strip()
        level = (result.level or "M1").strip().upper()
        if not year:
            raise ValueError("Millésime académique requis pour l'import emploi du temps.")

        old = self.db.query_all(
            "SELECT id FROM timetable_imports WHERE academic_year = ? AND level = ?",
            (year, level),
        )
        for row in old:
            self.db.execute("DELETE FROM timetable_imports WHERE id = ?", (int(row["id"]),))

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = self.db.execute(
            """
            INSERT INTO timetable_imports (academic_year, level, source_filename, imported_at, notes)
            VALUES (?, ?, ?, ?, '')
            """,
            (year, level, result.source_filename or "", now),
        )
        import_id = int(cur.lastrowid)

        ref_rows = [
            (
                import_id,
                r.period,
                r.block_label,
                r.course_title,
                r.legacy_code,
                r.mne_module_code,
                r.supervisors,
                r.hours_expected,
                r.ects,
            )
            for r in result.reference_courses
        ]
        if ref_rows:
            self.db.executemany(
                """
                INSERT INTO timetable_reference_courses (
                    import_id, period, block_label, course_title, legacy_code,
                    mne_module_code, supervisors, hours_expected, ects
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ref_rows,
            )

        slot_rows = [
            (
                import_id,
                s.level,
                s.track,
                s.period,
                s.week_label,
                s.week_number,
                s.week_start_date,
                s.day_of_week,
                s.time_slot,
                s.raw_text,
                s.legacy_code,
                s.mne_module_code,
                s.teacher_initials,
                s.room,
                s.slot_kind,
                s.fill_color,
            )
            for s in result.slots
        ]
        if slot_rows:
            self.db.executemany(
                """
                INSERT INTO timetable_slots (
                    import_id, level, track, period, week_label, week_number,
                    week_start_date, day_of_week, time_slot, raw_text, legacy_code,
                    mne_module_code, teacher_initials, room, slot_kind, fill_color
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                slot_rows,
            )
        return import_id

    def delete_timetable_import(self, import_id: int) -> None:
        row = self.db.query_one(
            "SELECT id FROM timetable_imports WHERE id = ?",
            (int(import_id),),
        )
        if not row:
            raise ValueError("Import emploi du temps introuvable.")
        self.db.execute("DELETE FROM timetable_imports WHERE id = ?", (int(import_id),))

    def list_timetable_imports(self, *, academic_year: str = "") -> list[dict[str, Any]]:
        year = (academic_year or "").strip()
        if year:
            rows = self.db.query_all(
                """
                SELECT * FROM timetable_imports
                WHERE academic_year = ?
                ORDER BY imported_at DESC
                """,
                (year,),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM timetable_imports ORDER BY academic_year DESC, imported_at DESC"
            )
        return [dict(r) for r in rows]

    def get_latest_timetable_import(
        self, *, academic_year: str, level: str = "M1"
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            """
            SELECT * FROM timetable_imports
            WHERE academic_year = ? AND level = ?
            ORDER BY imported_at DESC
            LIMIT 1
            """,
            ((academic_year or "").strip(), (level or "M1").strip().upper()),
        )
        return dict(row) if row else None

    def list_timetable_weeks(
        self, import_id: int, *, track: str, period: str
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT week_number, week_label, monday_date, friday_date
            FROM timetable_weeks
            WHERE import_id = ? AND track = ? AND period = ?
            ORDER BY week_number
            """,
            (int(import_id), (track or "P").strip().upper(), (period or "S1").strip().upper()),
        )
        if rows:
            return [dict(r) for r in rows]
        rows = self.db.query_all(
            """
            SELECT
                week_number,
                week_label,
                MIN(CASE WHEN day_of_week = 'Monday' THEN week_start_date END) AS monday_date,
                MAX(CASE WHEN day_of_week = 'Friday' THEN week_start_date END) AS friday_date
            FROM timetable_slots
            WHERE import_id = ? AND track = ? AND period = ?
            GROUP BY week_number, week_label
            ORDER BY week_number
            """,
            (int(import_id), (track or "P").strip().upper(), (period or "S1").strip().upper()),
        )
        return [dict(r) for r in rows]

    def list_timetable_slots_for_period(
        self,
        import_id: int,
        *,
        track: str,
        period: str,
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM timetable_slots
            WHERE import_id = ? AND track = ? AND period = ?
            ORDER BY week_number,
                CASE day_of_week
                    WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 9
                END,
                CASE time_slot WHEN '9:00-12:15' THEN 1 WHEN '1:15-4:30' THEN 2 ELSE 3 END
            """,
            (
                int(import_id),
                (track or "P").strip().upper(),
                (period or "S1").strip().upper(),
            ),
        )
        return [dict(r) for r in rows]

    def list_timetable_slots(
        self,
        import_id: int,
        *,
        track: str,
        period: str,
        week_number: int,
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM timetable_slots
            WHERE import_id = ? AND track = ? AND period = ? AND week_number = ?
            ORDER BY
                CASE day_of_week
                    WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 9
                END,
                CASE time_slot WHEN '9:00-12:15' THEN 1 WHEN '1:15-4:30' THEN 2 ELSE 3 END
            """,
            (
                int(import_id),
                (track or "P").strip().upper(),
                (period or "S1").strip().upper(),
                int(week_number),
            ),
        )
        return [dict(r) for r in rows]

    def list_timetable_reference_courses(
        self, import_id: int, *, period: str = ""
    ) -> list[dict[str, Any]]:
        period = (period or "").strip().upper()
        if period:
            rows = self.db.query_all(
                """
                SELECT * FROM timetable_reference_courses
                WHERE import_id = ? AND period = ?
                ORDER BY legacy_code
                """,
                (int(import_id), period),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT * FROM timetable_reference_courses
                WHERE import_id = ?
                ORDER BY period, legacy_code
                """,
                (int(import_id),),
            )
        return [dict(r) for r in rows]

    def summarize_timetable_hours(
        self,
        import_id: int,
        *,
        track: str,
        period: str,
        week_numbers: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        from .timetable_import import slot_hours

        track = (track or "P").strip().upper()
        period = (period or "S1").strip().upper()
        refs_by_legacy: dict[str, dict[str, Any]] = {}
        refs_by_mne: dict[str, dict[str, Any]] = {}
        for r in self.list_timetable_reference_courses(import_id, period=period):
            refs_by_legacy.update(self._index_timetable_ref(r, refs_by_legacy))
        for r in self.list_timetable_reference_courses(import_id, period=""):
            refs_by_legacy.update(self._index_timetable_ref(r, refs_by_legacy))
            refs_by_mne.update(self._index_timetable_ref(r, refs_by_mne, key="mne"))

        slots = self.db.query_all(
            """
            SELECT legacy_code, mne_module_code, time_slot, slot_kind, week_number
            FROM timetable_slots
            WHERE import_id = ? AND track = ? AND period = ?
            """,
            (int(import_id), track, period),
        )
        tallies: dict[str, dict[str, Any]] = {}
        for s in slots:
            if week_numbers is not None and int(s["week_number"] or 0) not in week_numbers:
                continue
            kind = (s["slot_kind"] or "").strip()
            if kind not in ("course", "exam"):
                continue
            legacy = (s["legacy_code"] or "").strip()
            mne = (s["mne_module_code"] or "").strip()
            key = legacy or mne
            if not key:
                continue
            if key not in tallies:
                ref = refs_by_legacy.get(legacy) or refs_by_mne.get(mne) or {}
                tallies[key] = {
                    "legacy_code": legacy,
                    "mne_module_code": mne or ref.get("mne_module_code") or "",
                    "course_title": ref.get("course_title") or "",
                    "hours_expected": float(ref.get("hours_expected") or 0),
                    "slot_count": 0,
                    "hours_scheduled": 0.0,
                }
            tallies[key]["slot_count"] += 1
            tallies[key]["hours_scheduled"] += slot_hours(s["time_slot"] or "")

        out = []
        for item in tallies.values():
            exp = float(item["hours_expected"] or 0)
            sched = float(item["hours_scheduled"] or 0)
            item["hours_delta"] = sched - exp if exp > 0 else None
            out.append(item)
        out.sort(key=lambda x: (x.get("legacy_code") or x.get("mne_module_code") or ""))
        return out

    @staticmethod
    def _index_timetable_ref(
        ref: dict[str, Any],
        existing: dict[str, dict[str, Any]],
        *,
        key: str = "legacy",
    ) -> dict[str, dict[str, Any]]:
        if key == "mne":
            code = str(ref.get("mne_module_code") or "").strip()
        else:
            code = str(ref.get("legacy_code") or "").strip()
        if not code:
            return existing
        existing[code] = ref
        return existing

    def ensure_timetable_scaffold(self, *, academic_year: str, level: str) -> int:
        """Crée un import EdT vide avec la grille calendrier (semaines) si besoin."""
        from .timetable_calendar import generate_weeks_for_level

        year = str(academic_year or "").strip()
        lv = str(level or "M1").strip().upper()
        if not year:
            raise ValueError("Millésime académique requis.")
        imp = self.get_latest_timetable_import(academic_year=year, level=lv)
        if imp:
            import_id = int(imp["id"])
        else:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur = self.db.execute(
                """
                INSERT INTO timetable_imports (
                    academic_year, level, source_filename, imported_at, notes
                ) VALUES (?, ?, '(calendrier vide)', ?, 'Grille éditable')
                """,
                (year, lv, now),
            )
            import_id = int(cur.lastrowid)

        tracks = ("X",) if lv == "M2" else ("P", "C")
        periods = ("S1", "S2", "S3") if lv == "M2" else ("S1", "S2")
        for period in periods:
            weeks = generate_weeks_for_level(year, level=lv, period=period)
            if not weeks:
                continue
            for track in tracks:
                if period == "S3" and lv != "M2":
                    continue
                existing = self.db.query_one(
                    """
                    SELECT COUNT(*) AS n FROM timetable_weeks
                    WHERE import_id = ? AND track = ? AND period = ?
                    """,
                    (import_id, track, period),
                )
                if existing and int(existing["n"] or 0) >= len(weeks):
                    continue
                self.db.execute(
                    """
                    DELETE FROM timetable_weeks
                    WHERE import_id = ? AND track = ? AND period = ?
                    """,
                    (import_id, track, period),
                )
                rows = [
                    (
                        import_id,
                        track,
                        period,
                        int(w["week_number"]),
                        str(w.get("week_label") or ""),
                        str(w.get("monday_date") or ""),
                        str(w.get("friday_date") or ""),
                    )
                    for w in weeks
                ]
                self.db.executemany(
                    """
                    INSERT INTO timetable_weeks (
                        import_id, track, period, week_number, week_label,
                        monday_date, friday_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return import_id

    def list_courses_for_timetable_level(
        self, *, academic_year: str, level: str
    ) -> list[dict[str, Any]]:
        """UE des maquettes du millésime et niveau (pour programmation EdT)."""
        ay = str(academic_year or "").strip()
        lv = str(level or "").strip().upper()
        if not ay or not lv:
            return []
        rows = self.db.query_all(
            """
            SELECT DISTINCT c.*
            FROM courses c
            JOIN template_courses tc ON tc.course_id = c.id
            JOIN templates t ON t.id = tc.template_id
            WHERE TRIM(IFNULL(t.academic_year, '')) = TRIM(?)
              AND UPPER(TRIM(t.level)) = ?
            ORDER BY c.mne_module_code, c.code, c.name
            """,
            (ay, lv),
        )
        return [dict(r) for r in rows]

    def get_timetable_slot(self, slot_id: int) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM timetable_slots WHERE id = ?",
            (int(slot_id),),
        )
        return dict(row) if row else None

    def upsert_timetable_slot(
        self,
        *,
        import_id: int,
        level: str,
        track: str,
        period: str,
        week_number: int,
        day_of_week: str,
        time_slot: str,
        course_id: int | None = None,
        slot_kind: str = "course",
        room: str = "",
        recurrence_weeks: int = 1,
        slot_id: int | None = None,
    ) -> list[int]:
        """
        Crée ou met à jour un créneau. ``recurrence_weeks`` > 1 réplique sur les semaines suivantes.
        Retourne les ids créés/mis à jour.
        """
        from .timetable_colors import fill_color_for_slot
        from .timetable_scheduling import find_slot_conflicts
        from .timetable_legacy import map_mne_to_legacy_timetable_code

        tid = int(import_id)
        tr = str(track or "P").strip().upper()
        per = str(period or "S1").strip().upper()
        lv = str(level or "M1").strip().upper()
        day = str(day_of_week or "").strip()
        ts = str(time_slot or "").strip()
        if not day or not ts:
            raise ValueError("Jour et créneau horaire requis.")

        course = self.get_course(int(course_id)) if course_id else None
        mne = str((course or {}).get("mne_module_code") or "").strip()
        legacy = map_mne_to_legacy_timetable_code(mne) if mne else ""
        fill = fill_color_for_slot(mne_module_code=mne, slot_kind=slot_kind)
        teacher_initials = ""
        if course:
            ln = str(course.get("teacher_last_name") or "").strip()
            fn = str(course.get("teacher_first_name") or "").strip()
            if ln:
                teacher_initials = (fn[:1] + ln[:2]).upper() if fn else ln[:3].upper()

        all_slots = self.list_timetable_slots_for_period(tid, track=tr, period=per)
        recurrence_group = ""
        if int(recurrence_weeks or 1) > 1:
            recurrence_group = f"rec-{datetime.now(timezone.utc).timestamp():.0f}"

        week_row = self.db.query_one(
            """
            SELECT week_label, monday_date, friday_date
            FROM timetable_weeks
            WHERE import_id = ? AND track = ? AND period = ? AND week_number = ?
            """,
            (tid, tr, per, int(week_number)),
        )
        if not week_row:
            week_row = {"week_label": f"Week {week_number}", "monday_date": "", "friday_date": ""}

        target_weeks = [int(week_number)]
        if int(recurrence_weeks or 1) > 1:
            later = self.db.query_all(
                """
                SELECT week_number FROM timetable_weeks
                WHERE import_id = ? AND track = ? AND period = ?
                  AND week_number >= ?
                ORDER BY week_number
                LIMIT ?
                """,
                (tid, tr, per, int(week_number), int(recurrence_weeks)),
            )
            target_weeks = [int(r["week_number"]) for r in later]

        created_ids: list[int] = []
        for i, wn in enumerate(target_weeks):
            wk = self.db.query_one(
                """
                SELECT week_label, monday_date, friday_date
                FROM timetable_weeks
                WHERE import_id = ? AND track = ? AND period = ? AND week_number = ?
                """,
                (tid, tr, per, int(wn)),
            ) or week_row
            candidate = {
                "week_number": int(wn),
                "day_of_week": day,
                "time_slot": ts,
                "mne_module_code": mne,
                "track": tr,
            }
            conflicts = find_slot_conflicts(
                candidate,
                all_slots,
                exclude_slot_id=int(slot_id) if slot_id and i == 0 else None,
            )
            if conflicts:
                labels = [
                    str(c.get("mne_module_code") or c.get("legacy_code") or "créneau")
                    for c in conflicts[:3]
                ]
                raise ValueError(
                    f"Chevauchement sem. {wn} {day} {ts} avec : {', '.join(labels)}"
                )

            if slot_id and i == 0:
                self.db.execute(
                    """
                    UPDATE timetable_slots SET
                        course_id = ?, legacy_code = ?, mne_module_code = ?,
                        teacher_initials = ?, room = ?, slot_kind = ?, fill_color = ?,
                        is_cancelled = 0, is_manual = 1, recurrence_group = ?,
                        raw_text = ?, week_number = ?, day_of_week = ?, time_slot = ?,
                        week_label = ?, week_start_date = ?
                    WHERE id = ?
                    """,
                    (
                        int(course_id) if course_id else None,
                        legacy,
                        mne,
                        teacher_initials,
                        str(room or ""),
                        str(slot_kind or "course"),
                        fill,
                        recurrence_group,
                        str((course or {}).get("name") or ""),
                        int(wn),
                        day,
                        ts,
                        str(wk["week_label"] or ""),
                        str(wk["monday_date"] or ""),
                        int(slot_id),
                    ),
                )
                created_ids.append(int(slot_id))
                continue

            cur = self.db.execute(
                """
                INSERT INTO timetable_slots (
                    import_id, level, track, period, week_label, week_number,
                    week_start_date, day_of_week, time_slot, raw_text, legacy_code,
                    mne_module_code, teacher_initials, room, slot_kind, fill_color,
                    course_id, is_cancelled, recurrence_group, is_manual
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1)
                """,
                (
                    tid,
                    lv,
                    tr,
                    per,
                    str(wk["week_label"] or ""),
                    int(wn),
                    str(wk["monday_date"] or ""),
                    day,
                    ts,
                    str((course or {}).get("name") or ""),
                    legacy,
                    mne,
                    teacher_initials,
                    str(room or ""),
                    str(slot_kind or "course"),
                    fill,
                    int(course_id) if course_id else None,
                    recurrence_group,
                ),
            )
            created_ids.append(int(cur.lastrowid))
        return created_ids

    def cancel_timetable_slot(self, slot_id: int, *, cancel_series: bool = False) -> int:
        slot = self.get_timetable_slot(int(slot_id))
        if not slot:
            raise ValueError("Créneau introuvable.")
        if cancel_series and str(slot.get("recurrence_group") or "").strip():
            grp = str(slot["recurrence_group"]).strip()
            self.db.execute(
                """
                UPDATE timetable_slots SET is_cancelled = 1
                WHERE import_id = ? AND recurrence_group = ? AND is_cancelled = 0
                """,
                (int(slot["import_id"]), grp),
            )
            row = self.db.query_one(
                "SELECT changes() AS n",
            )
            return int(row["n"] or 0) if row else 0
        self.db.execute(
            "UPDATE timetable_slots SET is_cancelled = 1 WHERE id = ?",
            (int(slot_id),),
        )
        return 1
