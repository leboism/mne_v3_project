"""Tests emploi du temps : calendrier et conflits."""

from mne_grade_manager.services.timetable_calendar import generate_m1_weeks
from mne_grade_manager.services.timetable_scheduling import (
    course_track_scope,
    scopes_conflict,
    slot_conflicts_with,
)


def test_m1_s1_week_count() -> None:
    weeks = generate_m1_weeks("2026-2027", period="S1")
    assert len(weeks) == 17
    assert weeks[0]["week_number"] == 36
    assert weeks[0]["monday_date"] == "2026-08-31"


def test_common_conflicts_with_specialty() -> None:
    assert scopes_conflict({"common"}, {"P"})
    assert scopes_conflict({"P"}, {"common"})
    assert not scopes_conflict({"P"}, {"C"})


def test_slot_parallel_p_c_allowed() -> None:
    p_slot = {
        "week_number": 1,
        "day_of_week": "Monday",
        "time_slot": "9:00-12:15",
        "mne_module_code": "M1B3-P-NEUT",
        "is_cancelled": 0,
    }
    c_slot = {
        "week_number": 1,
        "day_of_week": "Monday",
        "time_slot": "9:00-12:15",
        "mne_module_code": "M1B3-X-SOL",
        "is_cancelled": 0,
    }
    assert not slot_conflicts_with(p_slot, c_slot)


def test_slot_common_blocks_physics() -> None:
    common = {
        "week_number": 1,
        "day_of_week": "Monday",
        "time_slot": "9:00-12:15",
        "mne_module_code": "M1B1-C-THER",
        "is_cancelled": 0,
    }
    phys = {
        "week_number": 1,
        "day_of_week": "Monday",
        "time_slot": "9:00-12:15",
        "mne_module_code": "M1B3-P-NEUT",
        "is_cancelled": 0,
    }
    assert slot_conflicts_with(common, phys)
    assert course_track_scope("M1B1-C-THER") == {"common"}
