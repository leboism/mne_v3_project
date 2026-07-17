"""Couleurs des tableaux de notes PDF (alignées onglet Résultats)."""

from mne_grade_manager.services.jury_reports import (
    MNE_RESULT_CELL_FAIL_RGB,
    MNE_RESULT_CELL_PASS_RGB,
    _pdf_grade_cell_kind,
    _pdf_hex_for_grade_cell_kind,
    _pdf_text_hex_from_result_cell_rgb,
    _pdf_ue_cell_color_kind,
)


def test_pdf_grade_cell_kind_thresholds() -> None:
    assert _pdf_grade_cell_kind(10.0) == "warn"
    assert _pdf_grade_cell_kind(10.01) == "pass"
    assert _pdf_grade_cell_kind(7.0) == "warn"
    assert _pdf_grade_cell_kind(6.99) == "fail"
    assert _pdf_grade_cell_kind(None) is None


def test_pdf_text_hex_derived_from_result_cell_rgb() -> None:
    fail_hex = _pdf_text_hex_from_result_cell_rgb(MNE_RESULT_CELL_FAIL_RGB)
    pass_hex = _pdf_text_hex_from_result_cell_rgb(MNE_RESULT_CELL_PASS_RGB)
    assert fail_hex.startswith("#")
    assert pass_hex.startswith("#")
    assert fail_hex != pass_hex


def test_pdf_ue_cell_color_kind_status_and_grade() -> None:
    row_def = {"ue_detail": {1: {"display": "DEF"}}}
    assert _pdf_ue_cell_color_kind(row_def, 1, "s1") == "fail"

    row_low = {"ue_detail": {2: {"display": "", "s1": 6.5, "jury": 0.0}}}
    assert _pdf_ue_cell_color_kind(row_low, 2, "s1") == "fail"

    row_ok = {"ue_detail": {3: {"display": "", "s1": 12.0, "jury": 0.0}}}
    assert _pdf_ue_cell_color_kind(row_ok, 3, "s1") == "pass"

    assert _pdf_hex_for_grade_cell_kind("fail").startswith("#")
