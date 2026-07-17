"""Tests réparation parcours étudiants."""

from __future__ import annotations

from pathlib import Path

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository
from mne_grade_manager.services.student_parcours_repair import repair_student_parcours


def test_repair_year_in_level_and_infer_from_enrollment(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite3")
    repo = Repository(db)
    tid = repo.add_template("2025-2026 — M1 P", "M1", "P", "2025-2026", "1")
    sid = repo.add_student(
        "S1",
        "111",
        "A1",
        "Dupont",
        "Alice",
        level="2025-2026",
        track="P",
        academic_year="",
    )
    repo.enroll_student(sid, tid)
    n = repair_student_parcours(db)
    assert n >= 1
    st = repo.get_student(sid) or {}
    assert st.get("level") == "M1"
    assert st.get("track") == "P"
    assert st.get("academic_year") == "2025-2026"


def test_repair_year_in_track_clears_and_reinfers(tmp_path: Path) -> None:
    db = Database(tmp_path / "t2.sqlite3")
    repo = Repository(db)
    tid = repo.add_template("2025-2026 — M1 C", "M1", "C", "2025-2026", "1")
    sid = repo.add_student(
        "S2",
        "222",
        "A2",
        "Martin",
        "Bob",
        level="M1",
        track="2025-2026",
        academic_year="",
    )
    repo.enroll_student(sid, tid)
    repair_student_parcours(db)
    st = repo.get_student(sid) or {}
    assert st.get("level") == "M1"
    assert st.get("track") == "C"
    assert st.get("academic_year") == "2025-2026"


def test_coalesce_preserves_track_when_form_has_year(tmp_path: Path) -> None:
    from mne_grade_manager.services.student_parcours_repair import coalesce_student_parcours_fields

    existing = {"level": "M2", "track": "NPD", "academic_year": "2025-2026"}
    lv, tr, ay = coalesce_student_parcours_fields("M2", "2025-2026", "", existing)
    assert lv == "M2"
    assert tr == "NPD"
    assert ay == "2025-2026"


def test_coalesce_prefers_explicit_form_track(tmp_path: Path) -> None:
    from mne_grade_manager.services.student_parcours_repair import coalesce_student_parcours_fields

    existing = {"level": "M1", "track": "P", "academic_year": "2025-2026"}
    lv, tr, ay = coalesce_student_parcours_fields("M1", "C", "2025-2026", existing)
    assert tr == "C"
    assert lv == "M1"


def test_coalesce_preserves_track_when_form_empty(tmp_path: Path) -> None:
    from mne_grade_manager.services.student_parcours_repair import coalesce_student_parcours_fields

    existing = {"level": "M1", "track": "P", "academic_year": "2025-2026"}
    lv, tr, ay = coalesce_student_parcours_fields("M1", "", "2025-2026", existing)
    assert tr == "P"
