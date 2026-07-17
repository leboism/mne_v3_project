"""Tests nomenclature secrétariat S1-C/P/X ↔ MNE."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository
from mne_grade_manager.services.timetable_legacy import (
    course_public_code,
    map_legacy_timetable_code,
    map_mne_to_legacy_timetable_code,
)


def test_legacy_roundtrip_samples() -> None:
    pairs = [
        ("S1-C-NUCL", "M1B1-C-NUCL"),
        ("S1-C-THER", "M1B1-C-THER"),
        ("S1-P-NEUT", "M1B3-P-NEUT"),
    ]
    for leg, mne in pairs:
        assert map_legacy_timetable_code(leg) == mne
        assert map_mne_to_legacy_timetable_code(mne) == leg


def test_courses_exclusive_to_academic_year() -> None:
    db = Database(Path(tempfile.mkdtemp()) / "year_iso.sqlite3")
    repo = Repository(db)
    c25 = repo.add_course("S1-C-THER", "Thermo 25", ects=3, mne_module_code="M1B1-C-THER")
    c26 = repo.add_course("M1B1-C-THER", "Thermo 26", ects=3, mne_module_code="M1B1-C-THER")
    t25 = repo.add_template("M1 P 25", "M1", "P", "2025-2026", "1")
    t26 = repo.add_template("M1 P 26", "M1", "P", "2026-2027", "1")
    repo.add_course_to_template(t25, c25, block_name="B1")
    repo.add_course_to_template(t26, c26, block_name="B1")
    shared = repo.add_course("S1-C-NUCL", "Nucl shared", ects=3)
    repo.add_course_to_template(t25, shared, block_name="B1")
    repo.add_course_to_template(t26, shared, block_name="B1")

    only_25 = {int(c["id"]) for c in repo.list_courses_for_academic_year("2025-2026")}
    assert c25 in only_25
    assert c26 not in only_25
    assert shared not in only_25

    only_26 = {int(c["id"]) for c in repo.list_courses_for_academic_year("2026-2027")}
    assert c26 in only_26
    assert c25 not in only_26
    assert shared not in only_26


def test_clone_template_to_new_year_forks_courses() -> None:
    db = Database(Path(tempfile.mkdtemp()) / "clone_year.sqlite3")
    repo = Repository(db)
    c25 = repo.add_course("S1-C-THER", "Thermo 25", ects=3, mne_module_code="M1B1-C-THER")
    t25 = repo.add_template("M1 P 25", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(t25, c25, block_name="B1")
    t26 = repo.clone_template(
        t25,
        name="M1 P 26",
        academic_year="2026-2027",
        version="1",
    )
    c26_ids = {int(r["course_id"]) for r in repo.list_template_courses(t26)}
    assert c25 not in c26_ids
    assert len(c26_ids) == 1
    only_25 = {int(c["id"]) for c in repo.list_courses_for_academic_year("2025-2026")}
    only_26 = {int(c["id"]) for c in repo.list_courses_for_academic_year("2026-2027")}
    assert only_25.isdisjoint(only_26)


def test_course_public_code_internship_mne_millésime() -> None:
    row = {"code": "EN00005934", "mne_module_code": "S2-C-INTER", "name": "Internship"}
    assert course_public_code(row, academic_year="2025-2026") == "S2-C-INTER"
    assert course_public_code(row, academic_year="2026-2027") == "EN00005934"
    row = {"code": "M1B1-C-THER", "mne_module_code": "M1B1-C-THER", "name": "Thermo"}
    assert course_public_code(row, academic_year="2025-2026") == "S1-C-THER"
    assert course_public_code(row, academic_year="2026-2027") == "M1B1-C-THER"
    row2 = {"code": "S1-C-THER", "mne_module_code": "M1B1-C-THER", "name": "Thermo"}
    assert course_public_code(row2, academic_year="2025-2026") == "S1-C-THER"
    assert course_public_code(row2, academic_year="2026-2027") == "M1B1-C-THER"
