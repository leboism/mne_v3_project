"""Affichage des points de jury sur les transcripts."""

from mne_grade_manager.services.jury_reports import (
    _transcript_block_jury_points,
    _transcript_block_session_average,
    _transcript_jury_points_display,
    _transcript_row_has_jury_points,
    _transcript_ue_grade,
    _transcript_ue_jury_points,
    _transcript_ue_session_grade,
    _transcript_year_jury_points,
)


def test_transcript_jury_points_display() -> None:
    assert _transcript_jury_points_display(0.0) == ""
    assert _transcript_jury_points_display(None) == ""
    assert _transcript_jury_points_display(0.5) == "+0,50"
    assert _transcript_jury_points_display(-0.25) == "−0,25"


def test_transcript_jury_extractors() -> None:
    row = {
        "ue_detail": {12: {"jury": 0.5}},
        "jury": {
            "course": {12: 0.5},
            "block": {"Bloc 1": -0.25},
            "year": 0.2,
        },
    }
    assert _transcript_ue_jury_points(row, 12) == 0.5
    assert _transcript_block_jury_points(row, "Bloc 1") == -0.25
    assert _transcript_year_jury_points(row) == 0.2
    assert _transcript_row_has_jury_points(row) is True
    assert _transcript_row_has_jury_points({"jury": {"course": {}, "block": {}, "year": 0.0}}) is False


def test_transcript_grade_column_excludes_jury_points() -> None:
    """Colonne Grade = moyenne session ; total (résultat) = moyenne + jury."""
    row = {
        "ue_detail": {
            12: {"s1": 6.92, "s2": None, "use_s2": False, "jury": 0.16, "display": ""},
        },
        "blocks": {"Bloc 1": 10.87},
        "jury": {"course": {12: 0.16}, "block": {"Bloc 1": 0.0}, "year": 0.0},
    }
    assert _transcript_ue_session_grade(row, 12, "mixed") == 6.92
    assert _transcript_ue_grade(row, 12, "mixed") == 7.08
    assert _transcript_block_session_average(row, "Bloc 1", 10.87) == 10.87
