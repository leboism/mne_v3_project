"""Arborescence UE — maquette 2025-2026 vs nomenclature MNE."""

from mne_grade_manager.services.course_tree import course_tree_branch


def _course(
    *,
    mne_module_code: str = "",
    maquette_block: str = "",
    code: str = "EN00000001",
) -> dict:
    return {
        "code": code,
        "mne_module_code": mne_module_code,
        "maquette_block": maquette_block,
    }


def test_secretariat_maquette_common_block_1_ignores_mne_b2():
    branch = course_tree_branch(
        _course(
            mne_module_code="M1B2-C-ENER",
            maquette_block="Common courses 1 (block 1)",
        ),
        academic_year="2025-2026",
    )
    assert branch[:4] == ("M1", "Master 1 (M1)", "B1", "Bloc 1")
    assert branch[4] == "C"


def test_secretariat_maquette_physics_block_2_not_mne_b3():
    branch = course_tree_branch(
        _course(
            mne_module_code="M1B3-P-QUANT",
            maquette_block="Physics Courses 1 (Block 2)",
        ),
        academic_year="2025-2026",
    )
    assert branch[:4] == ("M1", "Master 1 (M1)", "B2", "Bloc 2")
    assert branch[4] == "P"


def test_secretariat_maquette_chemistry_block_4():
    branch = course_tree_branch(
        _course(
            mne_module_code="M1B3-X-CHEM",
            maquette_block="Chemistry Courses 2 (block 4)",
        ),
        academic_year="2025-2026",
    )
    assert branch[:4] == ("M1", "Master 1 (M1)", "B4", "Bloc 4")
    assert branch[4] == "X"


def test_secretariat_maquette_common_block_3():
    branch = course_tree_branch(
        _course(
            mne_module_code="S2-C-DATA",
            maquette_block="Common Courses 2 (Block 3)",
        ),
        academic_year="2025-2026",
    )
    assert branch[:4] == ("M1", "Master 1 (M1)", "B3", "Bloc 3")
    assert branch[4] == "C"


def test_mne_nomenclature_unchanged_from_2026_2027():
    branch = course_tree_branch(
        _course(
            mne_module_code="M1B3-P-QUANT",
            maquette_block="Physics Courses 1 (Block 2)",
        ),
        academic_year="2026-2027",
    )
    assert branch[:4] == ("M1", "Master 1 (M1)", "B3", "Bloc 3")
    assert branch[4] == "P"


def test_m2_internship_under_m2_bloc5_not_m1():
    branch = course_tree_branch(
        {
            "code": "EN00018956",
            "mne_module_code": "S2-C-INTER",
            "maquette_block": "Internship (block 5)",
            "name": "M2 Nuclear Engineering Internship",
            "course_type": "internship",
        },
        academic_year="2026-2027",
    )
    assert branch[:4] == ("M2", "Master 2 (M2)", "B5", "Bloc 5")


def test_m1_stage_stays_under_m1():
    branch = course_tree_branch(
        {
            "code": "EN00005934",
            "mne_module_code": "S2-C-INTER",
            "maquette_block": "Internship (block 4)",
            "name": "Stage",
        },
        academic_year="2026-2027",
    )
    assert branch[0] == "M1"


def test_m2_common_en_without_mne_not_under_m1():
    branch = course_tree_branch(
        {
            "code": "EN00002153",
            "mne_module_code": "",
            "maquette_block": (
                "Bloc 1 (common courses) : Safety & Risk Management "
                "(NDWM/NFC/NPO/NPD/NRPE)"
            ),
            "name": "Radiation Protection (NPD-NDWM-NFC-NPO-NRPE)",
        },
        academic_year="2026-2027",
    )
    assert branch[:4] == ("M2", "Master 2 (M2)", "B1", "Bloc 1")
