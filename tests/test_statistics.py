"""Tests agrégations statistiques."""

from __future__ import annotations

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository
from mne_grade_manager.services.statistics import enrollment_overview, filter_students


def _repo_with_students(tmp_path) -> Repository:
    db = Database(tmp_path / "test.sqlite3")
    repo = Repository(db)
    for ay, lv, tr, num in (
        ("2024-2025", "M1", "P", "S24"),
        ("2025-2026", "M1", "X", "S25a"),
        ("2025-2026", "M2", "P", "S25b"),
    ):
        repo.add_student(
            num,
            "",
            "",
            "Dupont",
            "Jean",
            academic_year=ay,
            level=lv,
            track=tr,
        )
    return repo


def test_filter_students_multiple_academic_years(tmp_path):
    repo = _repo_with_students(tmp_path)
    both = filter_students(repo, academic_years=["2024-2025", "2025-2026"])
    assert len(both) == 3
    one = filter_students(repo, academic_years=["2024-2025"])
    assert len(one) == 1
    assert one[0]["academic_year"] == "2024-2025"


def test_enrollment_overview_aggregates_years(tmp_path):
    repo = _repo_with_students(tmp_path)
    ov = enrollment_overview(repo, academic_years=["2024-2025", "2025-2026"])
    assert ov["total"] == 3
    assert ov["by_academic_year"]["2025-2026"] == 2
    assert ov["by_academic_year"]["2024-2025"] == 1


def test_filter_students_by_gender(tmp_path):
    db = Database(tmp_path / "gender.sqlite3")
    repo = Repository(db)
    repo.add_student("M1", "", "", "Martin", "Paul", academic_year="2025-2026", gender="M")
    repo.add_student("F1", "", "", "Durand", "Alice", academic_year="2025-2026", gender="F")
    repo.add_student("O1", "", "", "Lee", "Sam", academic_year="2025-2026", gender="O")

    women = filter_students(repo, academic_years=["2025-2026"], genders=["F"])
    assert len(women) == 1
    assert women[0]["gender"] == "F"

    ov = enrollment_overview(repo, academic_years=["2025-2026"])
    assert ov["by_gender"]["Homme"] == 1
    assert ov["by_gender"]["Femme"] == 1
    assert ov["by_gender"]["Autre"] == 1
