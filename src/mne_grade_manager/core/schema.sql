CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_number TEXT UNIQUE,
    student_number_ine TEXT DEFAULT '',
    student_number_local TEXT DEFAULT '',
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    birth_date TEXT DEFAULT '',
    nationality TEXT DEFAULT '',
    birth_place TEXT DEFAULT '',
    email_personal TEXT DEFAULT '',
    email_institutional TEXT DEFAULT '',
    enrollment_institution TEXT DEFAULT '',
    origin_institution TEXT DEFAULT '',
    origin_institution_country TEXT DEFAULT '',
    photo_path TEXT DEFAULT '',
    application_platform TEXT DEFAULT '',
    accommodations TEXT DEFAULT '',
    accommodations_other TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    level TEXT DEFAULT '',
    track TEXT DEFAULT '',
    academic_year TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    pedagogical_contract_paper INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS student_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    file_path TEXT NOT NULL,
    original_filename TEXT DEFAULT '',
    label TEXT DEFAULT '',
    uploaded_at TEXT NOT NULL,
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    ects REAL DEFAULT 0,
    description TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    hours_total REAL DEFAULT 0,
    hours_cm REAL DEFAULT 0,
    hours_td REAL DEFAULT 0,
    hours_tp REAL DEFAULT 0,
    hours_project REAL DEFAULT 0,
    hours_pt REAL DEFAULT 0,
    hours_aa REAL DEFAULT 0,
    code_ip_paris TEXT DEFAULT '',
    code_other TEXT DEFAULT '',
    mne_module_code TEXT DEFAULT '',
    semester TEXT DEFAULT '',
    mcc_text TEXT DEFAULT '',
    ead_flag TEXT DEFAULT '',
    course_type TEXT DEFAULT 'standard',
    teacher_last_name TEXT DEFAULT '',
    teacher_first_name TEXT DEFAULT '',
    teacher_email TEXT DEFAULT '',
    teacher_phone TEXT DEFAULT '',
    teacher_institution TEXT DEFAULT '',
    carrier_partner TEXT DEFAULT '',
    carrier_partner_other TEXT DEFAULT '',
    syllabus_path TEXT DEFAULT '',
    syllabus_filename TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS internship_records (
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
    convention_paper INTEGER NOT NULL DEFAULT 0,
    reporter_last_name TEXT DEFAULT '',
    reporter_first_name TEXT DEFAULT '',
    reporter_institution TEXT DEFAULT '',
    defense_date TEXT DEFAULT '',
    defense_time TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',
    UNIQUE(student_id, template_id, course_id),
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'CC',
    coefficient REAL NOT NULL DEFAULT 1,
    session INTEGER NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 0,
    mandatory INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    level TEXT DEFAULT '',
    track TEXT DEFAULT '',
    academic_year TEXT DEFAULT '',
    version TEXT DEFAULT '1',
    active INTEGER DEFAULT 1,
    parent_template_id INTEGER,
    change_note TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    FOREIGN KEY(parent_template_id) REFERENCES templates(id) ON DELETE SET NULL
);

-- Journal des relevés exportés (traçabilité maquette + structure UE au moment de l'export)
CREATE TABLE IF NOT EXISTS transcript_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    template_id INTEGER,
    view_session TEXT NOT NULL DEFAULT 's1',
    generated_at TEXT NOT NULL,
    file_path TEXT DEFAULT '',
    template_snapshot_json TEXT DEFAULT '',
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS template_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    block_name TEXT DEFAULT '',
    global_coefficient REAL NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 0,
    optional INTEGER NOT NULL DEFAULT 0,
    free_ue INTEGER NOT NULL DEFAULT 0,
    UNIQUE(template_id, course_id),
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ue_ects_validations (
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
);

-- Dérogation jury : valider une UE malgré une note d'épreuve < 7 (seuil réglementaire)
CREATE TABLE IF NOT EXISTS ue_jury_floor_waivers (
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
);

CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    UNIQUE(student_id, template_id),
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    assessment_id INTEGER NOT NULL,
    grade REAL,
    status TEXT DEFAULT 'OK',
    locked INTEGER NOT NULL DEFAULT 0,
    comment TEXT DEFAULT '',
    UNIQUE(student_id, assessment_id),
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);

-- Points de délibération (ajustements votés en réunion : UE, bloc ou année)
CREATE TABLE IF NOT EXISTS jury_adjustments (
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
);

-- Décision d'envoi en seconde session (par étudiant, maquette, UE)
CREATE TABLE IF NOT EXISTS second_session_decisions (
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
);

-- Composition du jury (liste de membres réutilisable sur plusieurs délibérations)
CREATE TABLE IF NOT EXISTS jury_rosters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    academic_year TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jury_roster_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_id INTEGER NOT NULL,
    last_name TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    title TEXT DEFAULT '',
    institution TEXT DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(roster_id) REFERENCES jury_rosters(id) ON DELETE CASCADE
);

-- Délibérations (réunions du jury par maquette) : S1 (plusieurs possibles), S2, finale
CREATE TABLE IF NOT EXISTS jury_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    roster_id INTEGER,
    session_kind TEXT NOT NULL DEFAULT 'S1',
    label TEXT DEFAULT '',
    scope_text TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE,
    FOREIGN KEY(roster_id) REFERENCES jury_rosters(id) ON DELETE SET NULL
);

-- Membres du jury (composition pour une délibération donnée)
CREATE TABLE IF NOT EXISTS jury_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jury_session_id INTEGER NOT NULL,
    last_name TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    title TEXT DEFAULT '',
    institution TEXT DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(jury_session_id) REFERENCES jury_sessions(id) ON DELETE CASCADE
);

-- Équipe pédagogique du master (mention, parcours, secrétariats par établissement)
CREATE TABLE IF NOT EXISTS master_team_members (
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
    display_order INTEGER NOT NULL DEFAULT 0
);
