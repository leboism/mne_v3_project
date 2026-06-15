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
from .calculations import strict_weighted_average, weighted_average
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
from .lookups import suggest_institutional_email


def _template_course_weight(course: dict[str, Any]) -> float:
    """Pondération pour moyennes bloc / année : ECTS si renseigné, sinon `global_coefficient`."""
    e = float(course.get("ects") or 0)
    if e > 0:
        return e
    return float(course.get("global_coefficient") or 0) or 1.0


def _block_key(course: dict[str, Any]) -> str:
    return (course.get("block_name") or "").strip() or "(no block)"


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
        return f"{float(value):.2f}"
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


def _compute_course_average_from_rows(
    rows: list[dict[str, Any]], *, mode: str, use_session2: bool | None = None
) -> float | None:
    """
    Moyenne d'UE à partir des lignes ``get_grades_for_student_course``.

    Règles MCC utilisées :
    - **ABJ / NEUT / VAL** : exclus de la moyenne (pas de pondération).
    - **DEF** : compte comme **0** pour la moyenne pondérée de l’UE.

    ``mode`` :
    - ``s1`` : session 1 uniquement (tout sauf ``session == 2``).
    - ``s2`` : épreuves de session 2 avec reprise S1 (Rep / tags) si besoin ;
      si aucune épreuve S2 n'est définie, équivalent à ``s1``.
    - ``final`` : si ``use_session2`` : même logique que ``s2``, sinon ``s1``.
    """
    if not rows:
        return None

    session2 = [r for r in rows if int(r["session"]) == 2]
    session1 = [r for r in rows if int(r["session"]) != 2]

    def grade_by_kind_session1() -> dict[str, float]:
        out: dict[str, float] = {}
        for r in session1:
            kind = str(r["kind"])
            st = normalize_grade_status(r.get("status"))
            if status_skips_average(st):
                continue
            if status_counts_as_zero(st):
                out.setdefault(kind, 0.0)
                continue
            if r["grade"] is not None:
                out.setdefault(kind, float(r["grade"]))
        return out

    def grade_by_tag_session1() -> dict[str, float]:
        out: dict[str, float] = {}
        for r in session1:
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

    def items_for_session(
        session_rows: list[dict[str, Any]],
        *,
        fallback_by_kind: dict[str, float] | None,
    ) -> list[tuple[float | None, float]]:
        fb = fallback_by_kind or {}
        fb_tag = grade_by_tag_session1() if fallback_by_kind is not None else {}
        items: list[tuple[float | None, float]] = []
        for r in session_rows:
            st = normalize_grade_status(r.get("status"))
            coef = float(r["coefficient"])
            if status_skips_average(st):
                continue
            if status_counts_as_zero(st):
                items.append((0.0, coef))
                continue
            g = r["grade"]
            if g is None:
                name = str(r.get("name") or "")
                if "rep" in name.lower():
                    g = fb.get(str(r["kind"]))
                else:
                    tag = _extract_tag_from_assessment_name(name)
                    if tag and tag in fb_tag:
                        g = fb_tag[tag]
            items.append((float(g) if g is not None else None, coef))
        return items

    if mode == "s1":
        if not session1:
            return None
        return strict_weighted_average(items_for_session(session1, fallback_by_kind=None))

    if mode == "s2":
        fb = grade_by_kind_session1()
        if session2:
            return strict_weighted_average(items_for_session(session2, fallback_by_kind=fb))
        if not session1:
            return None
        return strict_weighted_average(items_for_session(session1, fallback_by_kind=None))

    # mode == "final"
    if use_session2:
        fb = grade_by_kind_session1()
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
    def list_students(self) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM students ORDER BY academic_year DESC, last_name, first_name"
        )
        return [dict(r) for r in rows]

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
        enrollment_institution: str = "",
        application_platform: str = "",
        accommodations: str = "",
        accommodations_other: str = "",
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
                email_personal, email_institutional, enrollment_institution,
                origin_institution, origin_institution_country, highest_diploma, photo_path,
                application_platform, accommodations, accommodations_other,
                notes,
                level, track, academic_year
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                enrollment_institution,
                str(origin_institution or "").strip(),
                str(origin_institution_country or "").strip(),
                str(highest_diploma or "").strip(),
                str(photo_path or "").strip(),
                application_platform,
                accommodations,
                accommodations_other,
                notes,
                level,
                track,
                academic_year,
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
        enrollment_institution: str = "",
        application_platform: str = "",
        accommodations: str = "",
        accommodations_other: str = "",
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
    ) -> None:
        row = self.get_student(int(student_id)) or {}
        photo = row.get("photo_path") if photo_path is None else photo_path
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
                enrollment_institution = ?,
                origin_institution = ?,
                origin_institution_country = ?,
                highest_diploma = ?,
                photo_path = ?,
                application_platform = ?,
                accommodations = ?,
                accommodations_other = ?,
                notes = ?,
                level = ?,
                track = ?,
                academic_year = ?
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
                enrollment_institution,
                str(origin_institution or "").strip(),
                str(origin_institution_country or "").strip(),
                str(highest_diploma or "").strip(),
                str(photo or "").strip(),
                application_platform,
                accommodations,
                accommodations_other,
                notes,
                level,
                track,
                academic_year,
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
        """Passe l'étudiant en M2 : nouvelle année, parcours M2, réinscription maquette, notes conservées."""
        s = self.get_student(student_id)
        if not s:
            raise ValueError("Student not found")
        ay = str(new_academic_year or "").strip()
        tr = str(m2_track or "").strip()
        if not ay or not tr:
            raise ValueError("Academic year and M2 track are required")
        self.update_student(
            student_id,
            str(s.get("student_number") or ""),
            str(s.get("student_number_ine") or ""),
            str(s.get("student_number_local") or ""),
            str(s.get("last_name") or ""),
            str(s.get("first_name") or ""),
            str(s.get("email_personal") or ""),
            str(s.get("email_institutional") or ""),
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
        self.sync_enrollments_for_student(student_id)

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
        s = self.get_student(student_id)
        if not s:
            raise ValueError("Student not found")
        ay = str(new_academic_year or "").strip()
        if not ay:
            raise ValueError("Academic year is required")
        lv = str(s.get("level") or "").strip()
        tr = str(s.get("track") or "").strip()
        if not lv:
            raise ValueError("Student level is empty; set it in the student record first")
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
        self.sync_enrollments_for_student(student_id)

    # Courses
    def list_courses(self) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM courses ORDER BY code")
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
        teacher_phone: str = "",
        teacher_institution: str = "",
        carrier_partner: str = "",
        carrier_partner_other: str = "",
        mne_module_code: str = "",
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO courses(
                code, name, ects, description, active,
                hours_total, hours_cm, hours_td, hours_tp, hours_project, hours_pt, hours_aa,
                code_ip_paris, code_other, mne_module_code, semester, mcc_text, ead_flag,
                course_type, teacher_last_name, teacher_first_name, teacher_email,
                teacher_phone, teacher_institution, carrier_partner, carrier_partner_other
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                str(teacher_email or "").strip(),
                str(teacher_phone or "").strip(),
                str(teacher_institution or "").strip(),
                str(carrier_partner or "").strip(),
                str(carrier_partner_other or "").strip(),
            ),
        )
        return int(cur.lastrowid)

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
        teacher_phone: str = "",
        teacher_institution: str = "",
        carrier_partner: str = "",
        carrier_partner_other: str = "",
        mne_module_code: str = "",
    ) -> None:
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
                teacher_phone = ?,
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
                str(teacher_email or "").strip(),
                str(teacher_phone or "").strip(),
                str(teacher_institution or "").strip(),
                str(carrier_partner or "").strip(),
                str(carrier_partner_other or "").strip(),
                course_id,
            ),
        )

    def is_internship_course(self, course_id: int) -> bool:
        c = self.get_course(int(course_id))
        if not c:
            return False
        if str(c.get("course_type") or "").strip().lower() == "internship":
            return True
        name = str(c.get("name") or "").lower()
        code = str(c.get("code") or "").lower()
        return "stage" in name or "stage" in code

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
    def list_templates(self) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM templates ORDER BY academic_year DESC, level, track, name"
        )
        return [dict(r) for r in rows]

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
        lv = str(level or "").strip()
        tr = str(track or "").strip()
        row = self.db.query_one(
            """
            SELECT * FROM templates
            WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)
              AND TRIM(IFNULL(level, '')) = TRIM(?)
              AND TRIM(IFNULL(track, '')) = TRIM(?)
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
        """Pas de classement si au moins une UE a été envoyée en 2ᵉ session."""
        sid, tid = int(student_id), int(template_id)
        for c in self.list_template_courses(tid):
            if int(c.get("optional") or 0):
                continue
            if self.is_sent_to_second_session(sid, tid, int(c["course_id"])):
                return False
        return True

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
                ptid, view_session="s2", include_all_students=True
            )
            row = next(
                (r for r in data if int(r.get("student_id") or 0) == int(student_id)),
                None,
            )
            if row is None:
                continue
            d = (row.get("ue_detail") or {}).get(cid) or {}
            disp = str(d.get("display") or "").strip()
            grade = d.get("s2") if d.get("sent_s2") else d.get("s1")
            passed = disp == "VAL" or (
                grade is not None and float(grade) >= 10.0 and disp not in ("DEF", "ABJ")
            )
            if not passed:
                continue
            sent = self.is_sent_to_second_session(int(student_id), ptid, cid)
            vs = "s2" if sent else "s1"
            return format_transcript_session_label(vs, ay)

        return format_transcript_session_label(
            str(default_view_session or "s1"),
            str(default_academic_year or cur_ay),
        )

    def get_track_director(
        self, academic_year: str, level: str, track: str
    ) -> dict[str, Any] | None:
        from ..core.master_team import ROLE_TRACK

        ay = str(academic_year or "").strip()
        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        for row in self.list_master_team_members(ay, role_kind=ROLE_TRACK):
            if str(row.get("level") or "").strip().upper() == lv and str(row.get("track") or "").strip().upper() == tr:
                return dict(row)
        return None

    def transcript_header_emails(self, template_id: int) -> list[str]:
        tpl = self.get_template(int(template_id)) or {}
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        tr = str(tpl.get("track") or "").strip().upper()
        emails: list[str] = []
        director = self.get_track_director(ay, lv, tr)
        if director:
            em = str(director.get("email") or "").strip()
            if em:
                emails.append(em)
        for sec in self.secretariats_for_track(ay, lv, tr):
            em = str(sec.get("email") or "").strip()
            if em and em not in emails:
                emails.append(em)
        return emails

    def student_global_rank(
        self, template_id: int, student_id: int, *, view_session: str = "s2"
    ) -> int | None:
        tid, want = int(template_id), int(student_id)
        if not self.student_eligible_for_ranking(want, tid):
            return None
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
        scored.sort(key=lambda item: (-item[1], item[0]))
        for rank, (sid, _) in enumerate(scored, 1):
            if sid == want:
                return rank
        return None

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
            self.add_course_to_template(
                int(new_id),
                int(r["course_id"]),
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
        global_coefficient: float = 1.0,
        display_order: int = 0,
        optional: int = 0,
        free_ue: int = 0,
    ) -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO template_courses(
                template_id, course_id, block_name, global_coefficient, display_order, optional, free_ue
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (template_id, course_id, block_name, global_coefficient, display_order, optional, free_ue),
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
        global_coefficient: float = 1.0,
        display_order: int = 0,
        optional: int = 0,
        free_ue: int = 0,
    ) -> None:
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
            (block_name, global_coefficient, display_order, optional, free_ue, template_id, course_id),
        )

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
        """Dérogation jury : l'UE est validée malgré une note d'épreuve < 7."""
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
            s_ay = str(s.get("academic_year") or "").strip()
            if s_ay and s_ay != ay:
                continue
            lv = str(s.get("level") or "").strip().upper()
            tr = str(s.get("track") or "").strip().upper()
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
        if not s:
            return 0
        s_ay = str(s.get("academic_year") or "").strip()
        if not s_ay:
            return 0
        lv = str(s.get("level") or "").strip().upper()
        tr = str(s.get("track") or "").strip().upper()
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
            """
            SELECT s.*
            FROM enrollments e
            JOIN students s ON s.id = e.student_id
            WHERE e.template_id = ?
            ORDER BY s.last_name, s.first_name
            """,
            (template_id,),
        )
        return [dict(r) for r in rows]

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
            """
            SELECT *
            FROM students
            WHERE (TRIM(IFNULL(academic_year, '')) = TRIM(?) OR TRIM(IFNULL(academic_year, '')) = '')
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
        self, course_id: int, template_ids: list[int]
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
            """
            SELECT *
            FROM students
            WHERE (? = '' OR TRIM(academic_year) = TRIM(?))
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
                    WHERE (
                        TRIM(IFNULL(academic_year, '')) = TRIM(?)
                        OR id IN ({ph})
                    )
                    ORDER BY last_name, first_name
                    """,
                    (ay, *enrolled_ids),
                )
            else:
                rows = self.db.query_all(
                    """
                    SELECT * FROM students
                    WHERE TRIM(IFNULL(academic_year, '')) = TRIM(?)
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
            by_id.values(),
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

    def compute_course_average_s2(self, student_id: int, course_id: int) -> float | None:
        rows = self.get_grades_for_student_course(student_id, course_id)
        return _compute_course_average_from_rows(rows, mode="s2")

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

    def course_has_session2_activity(self, student_id: int, course_id: int) -> bool:
        """Au moins une épreuve de session 2 renseignée pour cette UE."""
        for r in self.get_grades_for_student_course(int(student_id), int(course_id)):
            if int(r["session"]) != 2:
                continue
            if not _grade_cell_empty(r.get("grade"), r.get("status")):
                return True
        return False

    def second_session_decision_locked(
        self, student_id: int, template_id: int, course_id: int
    ) -> bool:
        """Pas de nouvel envoi S2 si des notes de session 2 existent déjà."""
        return self.course_has_session2_activity(int(student_id), int(course_id))

    def can_set_second_session_decision(
        self,
        student_id: int,
        template_id: int,
        course_id: int,
        *,
        sent: bool,
    ) -> bool:
        """Nouvel envoi S2 interdit si des notes de session 2 existent déjà."""
        sid, tid, cid = int(student_id), int(template_id), int(course_id)
        if sent and self.second_session_decision_locked(sid, tid, cid):
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
        self, student_id: int, template_id: int, course_id: int
    ) -> bool:
        """Envoi en 2ᵉ session : impossible si l’UE a déjà des notes de session 2."""
        return not self.second_session_decision_locked(
            int(student_id), int(template_id), int(course_id)
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
            if self.course_session1_has_def(sid, cid):
                return STATUS_DEF
            if self.course_session1_has_abj(sid, cid):
                return STATUS_ABJ
        elif retain_s2:
            if self.course_session2_has_def(sid, cid):
                return STATUS_DEF
            if self.course_session2_has_abj(sid, cid):
                return STATUS_ABJ

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
        Pour chaque inscription : **DEF** ou **ABJ** en session 1 sur une UE impose l'envoi S2.

        Retourne le nombre de décisions S2 passées à « oui » (était « non » ou absente).
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

    def sync_second_session_obligations_for_def(self, template_id: int) -> int:
        """Alias historique — voir ``sync_second_session_obligations``."""
        return self.sync_second_session_obligations(int(template_id))

    def carry_over_reprise_grades_from_session1(self, student_id: int, course_id: int) -> int:
        """
        Recopie en base les notes / statuts de session 1 vers les épreuves de session 2 « reprises »
        (nom contenant ``rep`` / ``[Rep]``, ou même identifiant de tag que la S1), pour les cases S2
        encore vides et non verrouillées — aligné sur ``_compute_course_average_from_rows`` (mode S2).
        """
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
        sid = int(student_id)
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
    ) -> None:
        if not self.can_set_second_session_decision(
            int(student_id), int(template_id), int(course_id), sent=bool(sent)
        ):
            raise ValueError(
                "Envoi en 2ᵉ session impossible : des notes de session 2 existent déjà pour cette UE."
            )
        self.db.execute(
            """
            INSERT INTO second_session_decisions(student_id, template_id, course_id, sent, comment)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, course_id)
            DO UPDATE SET sent=excluded.sent, comment=excluded.comment
            """,
            (int(student_id), int(template_id), int(course_id), 1 if sent else 0, str(comment or "").strip()),
        )
        if sent:
            self.carry_over_reprise_grades_from_session1(int(student_id), int(course_id))

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
                slot["course"][int(r["course_id"])] = pts
            elif sc == "block":
                slot["block"][str(r["block_name"] or "").strip()] = pts
            elif sc == "year":
                slot["year"] = pts
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
    ) -> None:
        """Enregistre des points de délibération (UE, bloc ou année)."""
        sc = str(scope or "").strip().lower()
        self.db.execute(
            """
            DELETE FROM jury_adjustments
            WHERE student_id = ? AND template_id = ? AND scope = ?
              AND IFNULL(course_id, -999999) = IFNULL(?, -999999)
              AND TRIM(IFNULL(block_name, '')) = TRIM(IFNULL(?, ''))
            """,
            (int(student_id), int(template_id), sc, course_id, block_name),
        )
        if abs(float(points)) < 1e-12 and not (comment or "").strip():
            return
        self.db.execute(
            """
            INSERT INTO jury_adjustments(
                student_id, template_id, scope, course_id, block_name, points, comment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(student_id),
                int(template_id),
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
        email: str = "",
        phone: str = "",
        notes: str = "",
        display_order: int | None = None,
    ) -> int:
        from ..core.master_team import ROLE_MENTION, ROLE_SECRETARIAT, ROLE_TRACK

        ay = (academic_year or "").strip()
        rk = (role_kind or "").strip().lower()
        if rk not in (ROLE_MENTION, ROLE_TRACK, ROLE_SECRETARIAT):
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
        cur = self.db.execute(
            """
            INSERT INTO master_team_members(
                academic_year, role_kind, level, track, institution, tracks_scope,
                last_name, first_name, title, email, phone, notes, display_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                str(email or "").strip(),
                str(phone or "").strip(),
                str(notes or "").strip(),
                ord_,
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
            "email",
            "phone",
            "notes",
            "display_order",
        )
        updates: dict[str, Any] = {}
        for key in allowed:
            if key in fields:
                val = fields[key]
                if key in ("level", "track"):
                    val = str(val or "").strip().upper()
                elif key == "display_order":
                    val = int(val)
                else:
                    val = str(val or "").strip()
                updates[key] = val
        if not updates:
            return
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
        institution: str = "",
        email: str = "",
        phone: str = "",
        notes: str = "",
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
                institution=institution,
                email=email,
                phone=phone,
                notes=notes,
                display_order=sl,
            )
            return mid
        return self.add_master_team_member(
            ay,
            ROLE_MENTION,
            last_name=last_name,
            first_name=first_name,
            title=title,
            institution=institution,
            email=email,
            phone=phone,
            notes=notes,
            display_order=sl,
        )

    def upsert_track_director(
        self,
        academic_year: str,
        level: str,
        track: str,
        *,
        last_name: str,
        first_name: str,
        title: str = "",
        email: str = "",
        phone: str = "",
        notes: str = "",
    ) -> int:
        """Un responsable par (millésime, niveau, parcours)."""
        from ..core.master_team import ROLE_TRACK

        ay = (academic_year or "").strip()
        lv = str(level or "").strip().upper()
        tr = str(track or "").strip().upper()
        existing = self.db.query_one(
            """
            SELECT id FROM master_team_members
            WHERE academic_year = ? AND role_kind = ? AND level = ? AND track = ?
            """,
            (ay, ROLE_TRACK, lv, tr),
        )
        if existing:
            mid = int(existing["id"])
            self.update_master_team_member(
                mid,
                last_name=last_name,
                first_name=first_name,
                title=title,
                email=email,
                phone=phone,
                notes=notes,
            )
            return mid
        return self.add_master_team_member(
            ay,
            ROLE_TRACK,
            level=lv,
            track=tr,
            last_name=last_name,
            first_name=first_name,
            title=title,
            email=email,
            phone=phone,
            notes=notes,
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
            ORDER BY display_order, id
            """,
            (int(roster_id),),
        )
        return [dict(r) for r in rows]

    def add_jury_roster_member(
        self,
        roster_id: int,
        *,
        last_name: str,
        first_name: str,
        title: str = "",
        institution: str = "",
    ) -> int:
        mx = self.db.query_one(
            "SELECT COALESCE(MAX(display_order), -1) + 1 AS n FROM jury_roster_members WHERE roster_id = ?",
            (int(roster_id),),
        )
        ord_ = int(mx["n"]) if mx and mx["n"] is not None else 0
        cur = self.db.execute(
            """
            INSERT INTO jury_roster_members(
                roster_id, last_name, first_name, title, institution, display_order
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(roster_id),
                str(last_name or "").strip(),
                str(first_name or "").strip(),
                str(title or "").strip(),
                str(institution or "").strip(),
                ord_,
            ),
        )
        return int(cur.lastrowid)

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
        for m in members:
            self.add_jury_roster_member(
                int(roster_id),
                last_name=str(m.get("last_name") or ""),
                first_name=str(m.get("first_name") or ""),
                title=str(m.get("title") or ""),
                institution=str(m.get("institution") or ""),
            )
            n += 1
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
            ORDER BY display_order, id
            """,
            (int(jury_session_id),),
        )
        return [dict(r) for r in rows]

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
                jury_session_id, last_name, first_name, title, institution, display_order
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(jury_session_id),
                str(last_name or "").strip(),
                str(first_name or "").strip(),
                str(title or "").strip(),
                str(institution or "").strip(),
                ord_,
            ),
        )
        return int(cur.lastrowid)

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
        for m in members:
            self.add_jury_member(
                jsid,
                last_name=str(m.get("last_name") or ""),
                first_name=str(m.get("first_name") or ""),
                title=str(m.get("title") or ""),
                institution=str(m.get("institution") or ""),
            )
            n += 1
        return n

    def list_jury_adjustments_for_export(self, template_id: int) -> list[dict[str, Any]]:
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
        return [dict(r) for r in rows]

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
        if not oc and not cm and not mn:
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
                student_id, template_id, jury_session_id, outcome, mention, comment, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, template_id, jury_session_id)
            DO UPDATE SET
                outcome = excluded.outcome,
                mention = excluded.mention,
                comment = excluded.comment,
                updated_at = excluded.updated_at
            """,
            (sid, tid, jsid, oc, mn, cm, now),
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

    def list_second_session_for_export(self, template_id: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT d.*, s.last_name AS st_last, s.first_name AS st_first,
                   s.student_number, s.student_number_ine,
                   c.code AS course_code, c.name AS course_name
            FROM second_session_decisions d
            JOIN students s ON s.id = d.student_id
            JOIN courses c ON c.id = d.course_id
            WHERE d.template_id = ? AND d.sent = 1
            ORDER BY s.last_name, c.code
            """,
            (int(template_id),),
        )
        return [dict(r) for r in rows]

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
        True s'il existe une épreuve concernée (voir session) avec note strictement inférieure à ``floor``,
        hors lignes ``locked`` (Garder). DEF et ABJ bloquent toujours.
        Une dérogation jury sur l'UE (``ue_jury_floor_waivers``) ne supprime que le seuil ``floor``.
        """
        sid, cid = int(student_id), int(course_id)
        floor_waived = (
            template_id is not None
            and self.has_ue_jury_floor_waiver(sid, int(template_id), cid)
        )
        rows = self.get_grades_for_student_course(int(student_id), int(course_id))
        has_s2 = any(int(r["session"]) == 2 for r in rows)
        kinds_with_s2 = {str(r["kind"]) for r in rows if int(r["session"]) == 2}
        vs = (view_session or "s1").strip().lower()
        if vs not in {"s1", "s2"}:
            vs = "s1"

        for r in rows:
            sess = int(r["session"])
            kind = str(r["kind"])
            if vs == "s1" or not has_s2:
                if sess == 2:
                    continue
            else:
                if sess == 2:
                    pass
                elif kind not in kinds_with_s2:
                    pass
                else:
                    continue

            if int(r.get("locked") or 0):
                continue
            st = normalize_grade_status(r.get("status"))
            if status_blocks_validation(st):
                return True
            if status_skips_average(st):
                continue
            if floor_waived:
                continue
            g = r.get("grade")
            if g is None:
                continue
            if float(g) < float(floor):
                return True
        return False

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
        True si au moins une UE non optionnelle du bloc a une note < ``floor`` non neutralisée par « Garder ».
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
        Bloc validé : moyenne de bloc (comme en résultats, avec jury UE) **strictement supérieure à 10**
        et aucune note d’épreuve < ``floor`` sauf si l’évaluation est cochée « Garder » (``locked``).
        """
        if block_average is not None and float(block_average) > 10.0:
            if not self.block_has_unlocked_subthreshold_grade(
                student_id, template_id, block_name, view_session=view_session, floor=floor
            ):
                return True
        return self._block_satisfied_by_direct_validation(
            student_id, template_id, block_name, view_session=view_session, floor=floor
        )

    def _block_satisfied_by_direct_validation(
        self,
        student_id: int,
        template_id: int,
        block_name: str,
        *,
        view_session: str,
        floor: float = 7.0,
    ) -> bool:
        """Chaque UE du bloc est validée sans note ou avec une moyenne > 10."""
        sid, tid = int(student_id), int(template_id)
        bn = (block_name or "").strip()
        courses = [
            c
            for c in self.list_template_courses(tid)
            if int(c.get("optional") or 0) == 0 and _block_key(c) == bn
        ]
        if not courses:
            return False
        vs = str(view_session or "s1").strip().lower()
        for c in courses:
            cid = int(c["course_id"])
            if self.has_ue_ects_validation(sid, tid, cid):
                continue
            use_s2 = self.course_uses_session2_grades(sid, tid, cid, view_session=vs)
            if vs == "s2" and use_s2:
                avg = self.compute_course_average_s2(sid, cid)
            else:
                avg = self.compute_course_average_s1(sid, cid)
            if avg is None or float(avg) <= 10.0:
                return False
            if self._course_has_unlocked_grade_below(
                sid, cid, view_session=vs, floor=floor, template_id=tid
            ):
                return False
        return True

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
        Vérifie les règles de validation d'année (moyenne > 10 avec jury, blocs validés).

        Retourne ``validated``, ``issues``, ``suggested_outcome`` (présélection non enregistrée)
        et ``proposed_outcomes`` (choix proposés au jury final).
        """
        sid, tid = int(student_id), int(template_id)
        tpl = self.get_template(tid) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        cur_ay = str(tpl.get("academic_year") or "").strip()
        vs = str(view_session or "s2").strip().lower()
        if vs not in {"s1", "s2"}:
            vs = "s2"

        row = result_row
        if row is None:
            rows = self.get_student_result_summary(
                tid, view_session=vs, auto_sync_s2=auto_sync_s2
            )
            row = next((r for r in rows if int(r.get("student_id") or 0) == sid), None)
        issues: list[str] = []
        if row is None:
            issues.append("Aucune note calculable pour cet étudiant.")
        else:
            gwj = row.get("global_with_jury")
            if gwj is None or float(gwj) <= float(year_threshold):
                issues.append(
                    f"Moyenne année avec jury ≤ {year_threshold:g} "
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
        - **Moyenne année** : moyenne pondérée ECTS sur toutes les UE non optionnelles,
          en utilisant pour chaque UE la **moyenne de session affichée + points de jury UE**.

        ``view_session`` :
        - ``s1`` : moyennes de bloc / année basées uniquement sur la session 1 pour chaque UE.
        - ``s2`` : par défaut, ne retourne que les étudiants ayant au moins une UE « envoyé S2 » ;
          avec ``include_all_students=True`` (transcripts finaux), tous les inscrits sont inclus.
          Pour chaque UE, la moyenne agrégée utilise la logique S2 si l’UE est en seconde session,
          sinon la moyenne S1.

        **DEF / ABJ / S2** : avant agrégation, toute UE avec DEF ou ABJ en session 1 entraîne
        ``sync_second_session_obligations`` (envoi en seconde session obligatoire pour cette UE).

        Si une UE du bloc (ou de l’année) n’a pas encore de moyenne calculable, la moyenne du bloc
        (ou l’année) reste vide (``None``) plutôt qu’une valeur partielle trompeuse.
        """
        if auto_sync_s2:
            self.sync_second_session_obligations(int(template_id))
        students = self.list_students_for_template(template_id)
        vs = str(view_session or "s1").strip().lower()
        if vs not in {"s1", "s2"}:
            vs = "s1"

        courses = self.list_template_courses(template_id)
        jury_by_student = self._jury_map_for_template(int(template_id))
        results = []
        for student in students:
            sid = int(student["id"])
            if (
                vs == "s2"
                and not include_all_students
                and not any(
                    self.is_sent_to_second_session(sid, int(template_id), int(c["course_id"]))
                    for c in courses
                )
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
                s1 = self.compute_course_average_s1(sid, cid)
                s2 = self.compute_course_average_s2(sid, cid)
                sent_s2 = self.is_sent_to_second_session(sid, int(template_id), cid)
                use_s2 = self.course_uses_session2_grades(
                    sid, int(template_id), cid, view_session=vs
                )
                fin = _compute_course_average_from_rows(
                    self.get_grades_for_student_course(sid, cid),
                    mode="final",
                    use_session2=use_s2,
                )
                jp = float(jury_m["course"].get(cid, 0.0))
                total_ue = (
                    (float(fin) + jp)
                    if fin is not None
                    else (jp if abs(jp) > 1e-12 else None)
                )
                if vs == "s1":
                    agg_course = s1
                else:
                    agg_course = s2 if use_s2 else s1
                display = self.course_ue_display_label(
                    sid,
                    int(template_id),
                    cid,
                    view_session=vs,
                    session_average=agg_course,
                    sent_s2=bool(sent_s2),
                    use_s2=use_s2,
                )
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
            year_items: list[tuple[float | None, float]] = []
            ects_validated_free = 0.0
            for course in courses:
                if int(course.get("optional") or 0):
                    continue
                cid = int(course["course_id"])
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
                year_items.append((eff, w))

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

            if not year_items or any(g is None for g, _ in year_items):
                global_avg = None
            else:
                global_avg = weighted_average([(float(g), w) for g, w in year_items])

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

    def set_student_academic_year(self, student_id: int, academic_year: str) -> None:
        self.db.execute(
            "UPDATE students SET academic_year = ? WHERE id = ?",
            (str(academic_year or "").strip(), int(student_id)),
        )
