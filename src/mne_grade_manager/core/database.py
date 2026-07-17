from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

APP_DIR = Path.home() / ".mne_grade_manager"
DB_PATH = APP_DIR / "grade_manager.sqlite3"
CUSTOM_YEARS_FILE = APP_DIR / "custom_years.json"


class Database:
    def __init__(self, path: Path | None = None) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or DB_PATH
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.conn.executescript(schema)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Petites migrations légères pour les bases déjà existantes."""
        cols = {r["name"] for r in self.query_all("PRAGMA table_info(students)")}

        # v0.4: split student email into personal + institutional
        if "email_personal" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN email_personal TEXT DEFAULT ''")
        if "email_institutional" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN email_institutional TEXT DEFAULT ''")
        if "email" in cols:
            # Best-effort: keep existing values as personal if new columns are empty.
            self.conn.execute(
                """
                UPDATE students
                SET email_personal = CASE
                    WHEN (email_personal IS NULL OR email_personal = '') THEN COALESCE(email, '')
                    ELSE email_personal
                END
                WHERE email IS NOT NULL AND email != ''
                """
            )

        if "birth_date" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN birth_date TEXT DEFAULT ''")
        if "nationality" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN nationality TEXT DEFAULT ''")
        if "birth_place" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN birth_place TEXT DEFAULT ''")
        if "gender" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN gender TEXT DEFAULT ''")
        if "enrollment_institution" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN enrollment_institution TEXT DEFAULT ''"
            )
        if "student_number_ine" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN student_number_ine TEXT DEFAULT ''"
            )
        if "student_number_local" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN student_number_local TEXT DEFAULT ''"
            )
        if "application_platform" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN application_platform TEXT DEFAULT ''"
            )
        if "accommodations" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN accommodations TEXT DEFAULT ''"
            )
        if "accommodations_other" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN accommodations_other TEXT DEFAULT ''"
            )
        if "funding" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN funding TEXT DEFAULT ''")
        if "funding_other" not in cols:
            self.conn.execute(
                "ALTER TABLE students ADD COLUMN funding_other TEXT DEFAULT ''"
            )
        if "notes" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN notes TEXT DEFAULT ''")
        if "phone" not in cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN phone TEXT DEFAULT ''")
        cols_after = {r["name"] for r in self.query_all("PRAGMA table_info(students)")}
        if cols_after:
            legacy_phone_expr = "TRIM(COALESCE(phone, ''))"
            if "phone_mobile" in cols_after:
                legacy_phone_expr = (
                    f"CASE WHEN {legacy_phone_expr} != '' THEN phone "
                    f"WHEN TRIM(COALESCE(phone_mobile, '')) != '' THEN phone_mobile "
                    f"WHEN TRIM(COALESCE(phone_work, '')) != '' THEN phone_work "
                    f"WHEN TRIM(COALESCE(phone_work_2, '')) != '' THEN phone_work_2 "
                    f"ELSE '' END"
                )
            self.conn.execute(
                f"""
                UPDATE students
                SET phone = {legacy_phone_expr}
                WHERE TRIM(COALESCE(phone, '')) = ''
                """
            )

        for col, ddl in (
            ("hours_total", "ALTER TABLE courses ADD COLUMN hours_total REAL DEFAULT 0"),
            ("hours_cm", "ALTER TABLE courses ADD COLUMN hours_cm REAL DEFAULT 0"),
            ("hours_td", "ALTER TABLE courses ADD COLUMN hours_td REAL DEFAULT 0"),
            ("hours_tp", "ALTER TABLE courses ADD COLUMN hours_tp REAL DEFAULT 0"),
            ("hours_project", "ALTER TABLE courses ADD COLUMN hours_project REAL DEFAULT 0"),
            ("hours_pt", "ALTER TABLE courses ADD COLUMN hours_pt REAL DEFAULT 0"),
            ("hours_aa", "ALTER TABLE courses ADD COLUMN hours_aa REAL DEFAULT 0"),
            ("code_ip_paris", "ALTER TABLE courses ADD COLUMN code_ip_paris TEXT DEFAULT ''"),
            ("code_other", "ALTER TABLE courses ADD COLUMN code_other TEXT DEFAULT ''"),
            ("semester", "ALTER TABLE courses ADD COLUMN semester TEXT DEFAULT ''"),
            ("mcc_text", "ALTER TABLE courses ADD COLUMN mcc_text TEXT DEFAULT ''"),
            ("ead_flag", "ALTER TABLE courses ADD COLUMN ead_flag TEXT DEFAULT ''"),
        ):
            ccols = {r["name"] for r in self.query_all("PRAGMA table_info(courses)")}
            if col not in ccols:
                self.conn.execute(ddl)
        ccols = {r["name"] for r in self.query_all("PRAGMA table_info(courses)")}
        for col in ("teacher_phone_work", "teacher_phone_work_2", "teacher_phone_mobile"):
            if col not in ccols:
                self.conn.execute(
                    f"ALTER TABLE courses ADD COLUMN {col} TEXT DEFAULT ''"
                )
        ccols = {r["name"] for r in self.query_all("PRAGMA table_info(courses)")}
        if "teacher_phone" in ccols:
            self.conn.execute(
                """
                UPDATE courses
                SET teacher_phone_mobile = teacher_phone
                WHERE (teacher_phone_mobile IS NULL OR TRIM(teacher_phone_mobile) = '')
                  AND teacher_phone IS NOT NULL AND TRIM(teacher_phone) != ''
                """
            )
        for col in ("teacher_email_work", "teacher_email_work_2", "teacher_email_personal"):
            if col not in ccols:
                self.conn.execute(
                    f"ALTER TABLE courses ADD COLUMN {col} TEXT DEFAULT ''"
                )
        ccols = {r["name"] for r in self.query_all("PRAGMA table_info(courses)")}
        if "teacher_email" in ccols:
            self.conn.execute(
                """
                UPDATE courses
                SET teacher_email_work = teacher_email
                WHERE (teacher_email_work IS NULL OR TRIM(teacher_email_work) = '')
                  AND teacher_email IS NOT NULL AND TRIM(teacher_email) != ''
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jury_adjustments'"
        ):
            self.conn.execute(
                """
                CREATE TABLE jury_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'course',
                    course_id INTEGER,
                    block_name TEXT DEFAULT '',
                    points REAL NOT NULL DEFAULT 0,
                    comment TEXT DEFAULT '',
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE SET NULL
                )
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='second_session_decisions'"
        ):
            self.conn.execute(
                """
                CREATE TABLE second_session_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    course_id INTEGER NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0,
                    comment TEXT DEFAULT '',
                    UNIQUE(student_id, template_id, course_id),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
                """
            )

        gcols = {r["name"] for r in self.query_all("PRAGMA table_info(grades)")}
        if "locked" not in gcols:
            self.conn.execute("ALTER TABLE grades ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jury_rosters'"
        ):
            self.conn.execute(
                """
                CREATE TABLE jury_rosters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    academic_year TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    display_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
                )
                """
            )
        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jury_roster_members'"
        ):
            self.conn.execute(
                """
                CREATE TABLE jury_roster_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    roster_id INTEGER NOT NULL,
                    last_name TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    title TEXT DEFAULT '',
                    institution TEXT DEFAULT '',
                    is_president INTEGER NOT NULL DEFAULT 0,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(roster_id) REFERENCES jury_rosters(id) ON DELETE CASCADE
                )
                """
            )

        jscols = {r["name"] for r in self.query_all("PRAGMA table_info(jury_sessions)")}
        if "roster_id" not in jscols:
            self.conn.execute("ALTER TABLE jury_sessions ADD COLUMN roster_id INTEGER")
        if "scope_text" not in jscols:
            self.conn.execute("ALTER TABLE jury_sessions ADD COLUMN scope_text TEXT DEFAULT ''")

        jrmcols = {r["name"] for r in self.query_all("PRAGMA table_info(jury_roster_members)")}
        if "is_president" not in jrmcols:
            self.conn.execute(
                "ALTER TABLE jury_roster_members ADD COLUMN is_president INTEGER NOT NULL DEFAULT 0"
            )
        jmcols = {r["name"] for r in self.query_all("PRAGMA table_info(jury_members)")}
        if "is_president" not in jmcols:
            self.conn.execute(
                "ALTER TABLE jury_members ADD COLUMN is_president INTEGER NOT NULL DEFAULT 0"
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jury_sessions'"
        ):
            self.conn.execute(
                """
                CREATE TABLE jury_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL,
                    session_kind TEXT NOT NULL DEFAULT 'S1',
                    label TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    display_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
                )
                """
            )
        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jury_members'"
        ):
            self.conn.execute(
                """
                CREATE TABLE jury_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jury_session_id INTEGER NOT NULL,
                    last_name TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    title TEXT DEFAULT '',
                    institution TEXT DEFAULT '',
                    is_president INTEGER NOT NULL DEFAULT 0,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(jury_session_id) REFERENCES jury_sessions(id) ON DELETE CASCADE
                )
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_meta'"
        ):
            self.conn.execute(
                """
                CREATE TABLE app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
                """
            )
        # v0.6 (une seule fois) : anciennes saisies « case vide ⇒ DEF » sur reprises S2.
        if not self.query_one(
            "SELECT 1 FROM app_meta WHERE key = 'purged_phantom_s2_def_v06'"
        ):
            self.conn.execute(
                """
                UPDATE grades
                SET status = 'OK'
                WHERE grade IS NULL
                  AND UPPER(TRIM(COALESCE(status, ''))) = 'DEF'
                  AND assessment_id IN (
                      SELECT id FROM assessments
                      WHERE session = 2
                        AND (
                            LOWER(COALESCE(name, '')) LIKE '%rep%'
                            OR kind IN ('CC', 'CCTP')
                        )
                  )
                """
            )
            self.conn.execute(
                "INSERT INTO app_meta(key, value) VALUES ('purged_phantom_s2_def_v06', '1')"
            )

        tcols = {r["name"] for r in self.query_all("PRAGMA table_info(templates)")}
        if "parent_template_id" not in tcols:
            self.conn.execute("ALTER TABLE templates ADD COLUMN parent_template_id INTEGER")
        if "change_note" not in tcols:
            self.conn.execute("ALTER TABLE templates ADD COLUMN change_note TEXT DEFAULT ''")
        if "created_at" not in tcols:
            self.conn.execute("ALTER TABLE templates ADD COLUMN created_at TEXT DEFAULT ''")
            self.conn.execute(
                """
                UPDATE templates
                SET created_at = datetime('now')
                WHERE created_at IS NULL OR TRIM(created_at) = ''
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transcript_exports'"
        ):
            self.conn.execute(
                """
                CREATE TABLE transcript_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER,
                    view_session TEXT NOT NULL DEFAULT 's1',
                    generated_at TEXT NOT NULL,
                    file_path TEXT DEFAULT '',
                    template_snapshot_json TEXT DEFAULT '',
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE SET NULL
                )
                """
            )

        scols = {r["name"] for r in self.query_all("PRAGMA table_info(students)")}
        for col, ddl in (
            ("origin_institution", "ALTER TABLE students ADD COLUMN origin_institution TEXT DEFAULT ''"),
            ("origin_institution_country", "ALTER TABLE students ADD COLUMN origin_institution_country TEXT DEFAULT ''"),
            ("photo_path", "ALTER TABLE students ADD COLUMN photo_path TEXT DEFAULT ''"),
            (
                "pedagogical_contract_paper",
                "ALTER TABLE students ADD COLUMN pedagogical_contract_paper INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "highest_diploma",
                "ALTER TABLE students ADD COLUMN highest_diploma TEXT DEFAULT ''",
            ),
            ("status", "ALTER TABLE students ADD COLUMN status TEXT DEFAULT 'active'"),
            ("mobility_type", "ALTER TABLE students ADD COLUMN mobility_type TEXT DEFAULT 'mne'"),
            (
                "mon_master_ranking",
                "ALTER TABLE students ADD COLUMN mon_master_ranking TEXT DEFAULT ''",
            ),
        ):
            if col not in scols:
                self.conn.execute(ddl)

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='student_course_enrollments'"
        ):
            self.conn.execute(
                """
                CREATE TABLE student_course_enrollments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    course_id INTEGER NOT NULL,
                    academic_year TEXT NOT NULL DEFAULT '',
                    UNIQUE(student_id, course_id, academic_year),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
                """
            )

        # Import PDF candidature (bug paramètres add_student) : niveau/parcours/année permutés.
        self.conn.execute(
            """
            UPDATE students
            SET
                academic_year = track,
                track = level,
                level = CASE
                    WHEN UPPER(TRIM(notes)) IN ('M1', 'M2') THEN UPPER(TRIM(notes))
                    ELSE 'M1'
                END,
                notes = CASE
                    WHEN UPPER(TRIM(notes)) IN ('M1', 'M2')
                         AND TRIM(enrollment_institution) = 'MonMaster'
                    THEN 'Mon Master'
                    ELSE notes
                END,
                application_platform = CASE
                    WHEN TRIM(enrollment_institution) = 'MonMaster'
                         AND TRIM(COALESCE(application_platform, '')) = ''
                    THEN 'MonMaster'
                    ELSE application_platform
                END,
                enrollment_institution = CASE
                    WHEN TRIM(enrollment_institution) = 'MonMaster'
                    THEN 'Université Paris-Saclay'
                    ELSE enrollment_institution
                END
            WHERE UPPER(TRIM(level)) IN ('P', 'C')
              AND TRIM(COALESCE(academic_year, '')) = ''
              AND track GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'
            """
        )
        from ..services.student_parcours_repair import repair_student_parcours

        repair_student_parcours(self)

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='student_attachments'"
        ):
            self.conn.execute(
                """
                CREATE TABLE student_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'other',
                    file_path TEXT NOT NULL,
                    original_filename TEXT DEFAULT '',
                    label TEXT DEFAULT '',
                    uploaded_at TEXT NOT NULL,
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
                )
                """
            )

        ccols = {r["name"] for r in self.query_all("PRAGMA table_info(courses)")}
        for col, ddl in (
            ("course_type", "ALTER TABLE courses ADD COLUMN course_type TEXT DEFAULT 'standard'"),
            ("teacher_last_name", "ALTER TABLE courses ADD COLUMN teacher_last_name TEXT DEFAULT ''"),
            ("teacher_first_name", "ALTER TABLE courses ADD COLUMN teacher_first_name TEXT DEFAULT ''"),
            ("teacher_email", "ALTER TABLE courses ADD COLUMN teacher_email TEXT DEFAULT ''"),
            ("teacher_phone", "ALTER TABLE courses ADD COLUMN teacher_phone TEXT DEFAULT ''"),
            ("teacher_institution", "ALTER TABLE courses ADD COLUMN teacher_institution TEXT DEFAULT ''"),
            ("carrier_partner", "ALTER TABLE courses ADD COLUMN carrier_partner TEXT DEFAULT ''"),
            ("carrier_partner_other", "ALTER TABLE courses ADD COLUMN carrier_partner_other TEXT DEFAULT ''"),
            ("mne_module_code", "ALTER TABLE courses ADD COLUMN mne_module_code TEXT DEFAULT ''"),
            ("syllabus_path", "ALTER TABLE courses ADD COLUMN syllabus_path TEXT DEFAULT ''"),
            ("syllabus_filename", "ALTER TABLE courses ADD COLUMN syllabus_filename TEXT DEFAULT ''"),
        ):
            if col not in ccols:
                self.conn.execute(ddl)

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='internship_records'"
        ):
            self.conn.execute(
                """
                CREATE TABLE internship_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    course_id INTEGER NOT NULL,
                    topic TEXT DEFAULT '',
                    supervisor_last_name TEXT DEFAULT '',
                    supervisor_first_name TEXT DEFAULT '',
                    supervisor_email TEXT DEFAULT '',
                    supervisor_institution TEXT DEFAULT '',
                    supervisor_phone TEXT DEFAULT '',
                    follow_up_status TEXT DEFAULT '',
                    convention_path TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    updated_at TEXT DEFAULT '',
                    UNIQUE(student_id, template_id, course_id),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
                """
            )

        icols = {r["name"] for r in self.query_all("PRAGMA table_info(internship_records)")}
        for col, ddl in (
            (
                "convention_paper",
                "ALTER TABLE internship_records ADD COLUMN convention_paper INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "reporter_last_name",
                "ALTER TABLE internship_records ADD COLUMN reporter_last_name TEXT DEFAULT ''",
            ),
            (
                "reporter_first_name",
                "ALTER TABLE internship_records ADD COLUMN reporter_first_name TEXT DEFAULT ''",
            ),
            (
                "reporter_institution",
                "ALTER TABLE internship_records ADD COLUMN reporter_institution TEXT DEFAULT ''",
            ),
            (
                "defense_date",
                "ALTER TABLE internship_records ADD COLUMN defense_date TEXT DEFAULT ''",
            ),
            (
                "defense_time",
                "ALTER TABLE internship_records ADD COLUMN defense_time TEXT DEFAULT ''",
            ),
        ):
            if col not in icols:
                self.conn.execute(ddl)
                icols.add(col)

        tccols = {r["name"] for r in self.query_all("PRAGMA table_info(template_courses)")}
        if "free_ue" not in tccols:
            self.conn.execute(
                "ALTER TABLE template_courses ADD COLUMN free_ue INTEGER NOT NULL DEFAULT 0"
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ue_ects_validations'"
        ):
            self.conn.execute(
                """
                CREATE TABLE ue_ects_validations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    course_id INTEGER NOT NULL,
                    validated_at TEXT NOT NULL,
                    comment TEXT DEFAULT '',
                    UNIQUE(student_id, template_id, course_id),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ue_jury_floor_waivers'"
        ):
            self.conn.execute(
                """
                CREATE TABLE ue_jury_floor_waivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    course_id INTEGER NOT NULL,
                    waived_at TEXT NOT NULL,
                    comment TEXT DEFAULT '',
                    UNIQUE(student_id, template_id, course_id),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                )
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='block_jury_validation_waivers'"
        ):
            self.conn.execute(
                """
                CREATE TABLE block_jury_validation_waivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    block_name TEXT NOT NULL,
                    waived_at TEXT NOT NULL,
                    comment TEXT DEFAULT '',
                    UNIQUE(student_id, template_id, block_name),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
                )
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jury_student_outcomes'"
        ):
            self.conn.execute(
                """
                CREATE TABLE jury_student_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    jury_session_id INTEGER,
                    outcome TEXT NOT NULL DEFAULT '',
                    mention TEXT NOT NULL DEFAULT '',
                    comment TEXT DEFAULT '',
                    progression_track TEXT DEFAULT '',
                    updated_at TEXT DEFAULT '',
                    UNIQUE(student_id, template_id, jury_session_id),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
                    FOREIGN KEY(jury_session_id) REFERENCES jury_sessions(id) ON DELETE SET NULL
                )
                """
            )

        jso_cols = {r["name"] for r in self.query_all("PRAGMA table_info(jury_student_outcomes)")}
        if "mention" not in jso_cols:
            self.conn.execute(
                "ALTER TABLE jury_student_outcomes ADD COLUMN mention TEXT NOT NULL DEFAULT ''"
            )
        if "progression_track" not in jso_cols:
            self.conn.execute(
                "ALTER TABLE jury_student_outcomes ADD COLUMN progression_track TEXT DEFAULT ''"
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ue_transcript_sessions'"
        ):
            self.conn.execute(
                """
                CREATE TABLE ue_transcript_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    course_id INTEGER NOT NULL,
                    academic_year TEXT NOT NULL DEFAULT '',
                    view_session TEXT NOT NULL DEFAULT 's1',
                    source_template_id INTEGER,
                    recorded_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(student_id, course_id),
                    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_template_id) REFERENCES templates(id) ON DELETE SET NULL
                )
                """
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='master_team_members'"
        ):
            self.conn.execute(
                """
                CREATE TABLE master_team_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    academic_year TEXT NOT NULL DEFAULT '',
                    role_kind TEXT NOT NULL DEFAULT '',
                    level TEXT DEFAULT '',
                    track TEXT DEFAULT '',
                    institution TEXT DEFAULT '',
                    tracks_scope TEXT DEFAULT '',
                    last_name TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    title TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    display_order INTEGER NOT NULL DEFAULT 0,
                    post_label TEXT DEFAULT ''
                )
                """
            )

        mtcols = {r["name"] for r in self.query_all("PRAGMA table_info(master_team_members)")}
        if mtcols and "post_label" not in mtcols:
            self.conn.execute(
                "ALTER TABLE master_team_members ADD COLUMN post_label TEXT DEFAULT ''"
            )
        if mtcols and "student_id" not in mtcols:
            self.conn.execute(
                "ALTER TABLE master_team_members ADD COLUMN student_id INTEGER REFERENCES students(id) ON DELETE SET NULL"
            )
        if mtcols and "affiliation" not in mtcols:
            self.conn.execute(
                "ALTER TABLE master_team_members ADD COLUMN affiliation TEXT DEFAULT ''"
            )
        mtcols = {r["name"] for r in self.query_all("PRAGMA table_info(master_team_members)")}
        if mtcols:
            self.conn.execute(
                """
                UPDATE master_team_members
                SET affiliation = institution
                WHERE role_kind IN ('mention', 'track')
                  AND (affiliation IS NULL OR TRIM(affiliation) = '')
                  AND institution IS NOT NULL AND TRIM(institution) != ''
                """
            )
        for col in ("phone_work", "phone_work_2", "phone_mobile"):
            if mtcols and col not in mtcols:
                self.conn.execute(
                    f"ALTER TABLE master_team_members ADD COLUMN {col} TEXT DEFAULT ''"
                )
        mtcols = {r["name"] for r in self.query_all("PRAGMA table_info(master_team_members)")}
        if mtcols and "phone" in mtcols:
            self.conn.execute(
                """
                UPDATE master_team_members
                SET phone_mobile = phone
                WHERE (phone_mobile IS NULL OR TRIM(phone_mobile) = '')
                  AND phone IS NOT NULL AND TRIM(phone) != ''
                """
            )
        for col in ("email_work", "email_work_2", "email_personal"):
            if mtcols and col not in mtcols:
                self.conn.execute(
                    f"ALTER TABLE master_team_members ADD COLUMN {col} TEXT DEFAULT ''"
                )
        mtcols = {r["name"] for r in self.query_all("PRAGMA table_info(master_team_members)")}
        if mtcols and "email" in mtcols:
            self.conn.execute(
                """
                UPDATE master_team_members
                SET email_work = email
                WHERE (email_work IS NULL OR TRIM(email_work) = '')
                  AND email IS NOT NULL AND TRIM(email) != ''
                """
            )
        if mtcols:
            self.conn.execute(
                """
                UPDATE master_team_members
                SET display_order = 0
                WHERE role_kind = 'track'
                  AND (display_order IS NULL OR display_order < 0)
                """
            )
        for table in ("templates", "students"):
            self.conn.execute(
                f"UPDATE {table} SET track = 'P' WHERE TRIM(track) IN ('M1P', 'm1p')"
            )
            self.conn.execute(
                f"UPDATE {table} SET track = 'C' WHERE TRIM(track) IN ('M1C', 'm1c')"
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='timetable_imports'"
        ):
            self.conn.execute(
                """
                CREATE TABLE timetable_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    academic_year TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'M1',
                    source_filename TEXT DEFAULT '',
                    imported_at TEXT NOT NULL,
                    notes TEXT DEFAULT ''
                )
                """
            )
        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='timetable_reference_courses'"
        ):
            self.conn.execute(
                """
                CREATE TABLE timetable_reference_courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    period TEXT NOT NULL DEFAULT 'S1',
                    block_label TEXT DEFAULT '',
                    course_title TEXT DEFAULT '',
                    legacy_code TEXT DEFAULT '',
                    mne_module_code TEXT DEFAULT '',
                    supervisors TEXT DEFAULT '',
                    hours_expected REAL DEFAULT 0,
                    ects REAL DEFAULT 0,
                    FOREIGN KEY(import_id) REFERENCES timetable_imports(id) ON DELETE CASCADE
                )
                """
            )
        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='timetable_slots'"
        ):
            self.conn.execute(
                """
                CREATE TABLE timetable_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    level TEXT NOT NULL DEFAULT 'M1',
                    track TEXT NOT NULL DEFAULT 'P',
                    period TEXT NOT NULL DEFAULT 'S1',
                    week_label TEXT DEFAULT '',
                    week_number INTEGER NOT NULL DEFAULT 0,
                    week_start_date TEXT DEFAULT '',
                    day_of_week TEXT NOT NULL DEFAULT '',
                    time_slot TEXT NOT NULL DEFAULT '',
                    raw_text TEXT DEFAULT '',
                    legacy_code TEXT DEFAULT '',
                    mne_module_code TEXT DEFAULT '',
                    teacher_initials TEXT DEFAULT '',
                    room TEXT DEFAULT '',
                    slot_kind TEXT NOT NULL DEFAULT 'other',
                    fill_color TEXT DEFAULT '',
                    FOREIGN KEY(import_id) REFERENCES timetable_imports(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX idx_timetable_slots_lookup
                ON timetable_slots(import_id, track, period, week_number)
                """
            )

        slot_cols = {r["name"] for r in self.query_all("PRAGMA table_info(timetable_slots)")}
        if "fill_color" not in slot_cols:
            self.conn.execute(
                "ALTER TABLE timetable_slots ADD COLUMN fill_color TEXT DEFAULT ''"
            )
        if "course_id" not in slot_cols:
            self.conn.execute(
                "ALTER TABLE timetable_slots ADD COLUMN course_id INTEGER "
                "REFERENCES courses(id) ON DELETE SET NULL"
            )
        if "is_cancelled" not in slot_cols:
            self.conn.execute(
                "ALTER TABLE timetable_slots ADD COLUMN is_cancelled INTEGER NOT NULL DEFAULT 0"
            )
        if "recurrence_group" not in slot_cols:
            self.conn.execute(
                "ALTER TABLE timetable_slots ADD COLUMN recurrence_group TEXT DEFAULT ''"
            )
        if "is_manual" not in slot_cols:
            self.conn.execute(
                "ALTER TABLE timetable_slots ADD COLUMN is_manual INTEGER NOT NULL DEFAULT 0"
            )

        if not self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='timetable_weeks'"
        ):
            self.conn.execute(
                """
                CREATE TABLE timetable_weeks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    track TEXT NOT NULL DEFAULT 'P',
                    period TEXT NOT NULL DEFAULT 'S1',
                    week_number INTEGER NOT NULL DEFAULT 0,
                    week_label TEXT DEFAULT '',
                    monday_date TEXT DEFAULT '',
                    friday_date TEXT DEFAULT '',
                    FOREIGN KEY(import_id) REFERENCES timetable_imports(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX idx_timetable_weeks_lookup
                ON timetable_weeks(import_id, track, period, week_number)
                """
            )

        jacols = {r["name"] for r in self.query_all("PRAGMA table_info(jury_adjustments)")}
        if jacols and "jury_session_id" not in jacols:
            self.conn.execute(
                "ALTER TABLE jury_adjustments ADD COLUMN jury_session_id INTEGER "
                "REFERENCES jury_sessions(id) ON DELETE SET NULL"
            )
        s2cols = {r["name"] for r in self.query_all("PRAGMA table_info(second_session_decisions)")}
        if s2cols and "jury_session_id" not in s2cols:
            self.conn.execute(
                "ALTER TABLE second_session_decisions ADD COLUMN jury_session_id INTEGER "
                "REFERENCES jury_sessions(id) ON DELETE SET NULL"
            )

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        cur = self.conn.executemany(sql, seq_of_params)
        self.conn.commit()
        return cur

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cur = self.conn.execute(sql, tuple(params))
        return list(cur.fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cur = self.conn.execute(sql, tuple(params))
        return cur.fetchone()

    def backup_to(self, dest: Path | str) -> None:
        """Copie la base ouverte vers un autre fichier (snapshot cohérent, même si l'app tourne)."""
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_conn = sqlite3.connect(str(dest_path))
        try:
            with dest_conn:
                self.conn.backup(dest_conn)
        finally:
            dest_conn.close()

    def close(self) -> None:
        if getattr(self, "conn", None) is not None:
            self.conn.close()

    def reopen(self) -> None:
        """Rouvre la connexion après remplacement du fichier sur disque."""
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self.conn.commit()

    def replace_from_file(self, source: Path | str) -> None:
        """Remplace le fichier SQLite local par une copie (import / restauration)."""
        import shutil

        src = Path(source)
        if not src.is_file():
            raise FileNotFoundError(f"Base introuvable : {src}")
        self.close()
        shutil.copy2(src, self.path)
        self.reopen()
