"""Champ classement Mon Master (fiche + import Excel)."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository
from mne_grade_manager.services.student_excel import (
    build_import_column_map,
    normalize_mon_master_ranking,
    resolve_field_key_from_header,
)


def test_normalize_mon_master_ranking() -> None:
    assert normalize_mon_master_ranking(None) == ""
    assert normalize_mon_master_ranking(3) == "3"
    assert normalize_mon_master_ranking(10.0) == "10"
    assert normalize_mon_master_ranking("nc") == "NC"
    assert normalize_mon_master_ranking(" 7 ") == "7"


def test_excel_header_resolves_mon_master_ranking() -> None:
    assert resolve_field_key_from_header("Classement Mon Master") == "mon_master_ranking"
    col_map = build_import_column_map(("Nom", "Prénom", "Classement Mon Master"))
    assert col_map.get("mon_master_ranking") == 2


def test_repository_stores_mon_master_ranking() -> None:
    db = Database(Path(tempfile.mkdtemp()) / "mm_rank.sqlite3")
    repo = Repository(db)
    sid = repo.add_student(
        "",
        "",
        "",
        "Test",
        "Student",
        application_platform="MonMaster",
        mon_master_ranking="5",
    )
    row = repo.get_student(sid) or {}
    assert row.get("mon_master_ranking") == "5"
