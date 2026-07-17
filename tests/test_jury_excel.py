"""Export / import Excel des compositions du jury."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.services.jury_excel import (
    parse_jury_members_workbook,
    write_jury_roster_workbook,
)


def test_jury_roster_export_roundtrip() -> None:
    members = [
        {
            "last_name": "Martin",
            "first_name": "Sophie",
            "title": "Professeure",
            "institution": "Université Paris-Saclay",
            "is_president": 1,
        },
        {
            "last_name": "Durand",
            "first_name": "Paul",
            "title": "Représentant professionnel",
            "institution": "CEA",
            "is_president": 0,
        },
    ]
    path = Path(tempfile.mkdtemp()) / "jury_export.xlsx"
    write_jury_roster_workbook(
        members,
        path,
        title="M1 P",
        academic_year="2025-2026",
    )
    parsed, errors = parse_jury_members_workbook(path)
    assert not errors
    assert len(parsed) == 2
    assert parsed[0]["last_name"] == "Martin"
    assert int(parsed[0]["is_president"]) == 1
    assert int(parsed[1]["is_president"]) == 0


def test_split_jury_president_and_members() -> None:
    from mne_grade_manager.services.jury_excel import split_jury_president_and_members

    members = [
        {"id": 1, "last_name": "A", "is_president": 0},
        {"id": 2, "last_name": "B", "is_president": 1},
    ]
    president, others = split_jury_president_and_members(members)
    assert president["last_name"] == "B"
    assert len(others) == 1
    assert others[0]["last_name"] == "A"
