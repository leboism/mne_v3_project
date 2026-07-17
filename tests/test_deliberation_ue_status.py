"""Statut UE compensable en délibération (aligné onglet Résultats)."""

from mne_grade_manager.gui.jury_deliberation_dialog import (
    _ue_total_with_jury,
    _ue_validation_status,
)


class _RepoStub:
    def has_ue_ects_validation(self, *_a, **_k) -> bool:
        return False


def test_ue_total_with_jury_from_row() -> None:
    row = {"ue_detail": {5: {"s1": 9.0, "jury": 0.5, "display": ""}}}
    assert _ue_total_with_jury(row, 5, view_session="s1", jury_points=0.5) == 9.5
    assert _ue_total_with_jury(row, 5, view_session="s1", jury_points=1.0) == 10.0


def test_ue_total_with_jury_display_status() -> None:
    row = {"ue_detail": {5: {"s1": 9.0, "display": "DEF"}}}
    assert _ue_total_with_jury(row, 5, view_session="s1", jury_points=0.0) is None


def test_ue_validation_compensable_orange() -> None:
    label, color = _ue_validation_status(
        _RepoStub(),
        1,
        1,
        1,
        display="",
        total_with_jury=9.46,
        waived=False,
        compensation_allowed=True,
    )
    assert label == "Compensable"
    assert color is not None
    assert color.red() > 200 and color.green() > 200


def test_ue_validation_not_compensable_when_block_incomplete() -> None:
    label, color = _ue_validation_status(
        _RepoStub(),
        1,
        1,
        1,
        display="",
        total_with_jury=9.46,
        waived=False,
        compensation_allowed=False,
        compensation_status="incomplete",
    )
    assert label == "Bloc incomplet"
    assert color is not None


def test_ue_validation_not_compensable_when_eliminating_in_block() -> None:
    label, color = _ue_validation_status(
        _RepoStub(),
        1,
        1,
        1,
        display="",
        total_with_jury=9.32,
        waived=False,
        compensation_allowed=False,
        compensation_status="eliminating",
    )
    assert label == "Non compensable"
    assert color is not None


def test_ue_validation_fail_below_7() -> None:
    label, _color = _ue_validation_status(
        _RepoStub(),
        1,
        1,
        1,
        display="",
        total_with_jury=6.5,
        waived=False,
    )
    assert label == "Non validée ✗"


def test_ue_validation_pass() -> None:
    label, _color = _ue_validation_status(
        _RepoStub(),
        1,
        1,
        1,
        display="",
        total_with_jury=12.0,
        waived=False,
    )
    assert label == "Validée ✓"


def test_ue_validation_waiver_below_7_validated() -> None:
    label, _color = _ue_validation_status(
        _RepoStub(),
        1,
        1,
        1,
        display="",
        total_with_jury=6.2,
        waived=True,
        compensation_allowed=True,
    )
    assert label == "Validée ✓"
