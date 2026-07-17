"""Périmètre textuel des délibérations."""

from mne_grade_manager.services.jury_scope import (
    block_key_bloc_number,
    extract_bloc_numbers,
    extract_jury_date_token,
    scope_text_to_block_keys,
    suggest_pv_pdf_filename,
    suggest_scope_text,
)


def test_block_key_bloc_number() -> None:
    assert block_key_bloc_number("Common courses 1 (block 1)") == 1
    assert block_key_bloc_number("Physics Courses 1 (Block 2)") == 2


def test_extract_bloc_numbers() -> None:
    assert extract_bloc_numbers("Bloc 2 — 1ʳᵉ session") == {2}
    assert extract_bloc_numbers("Blocs 3 et 4") == {3, 4}
    assert extract_bloc_numbers("Blocs 2-3") == {2, 3}
    assert extract_bloc_numbers("Blocs 2 à 4") == {2, 3, 4}
    assert extract_bloc_numbers("Bloc 2,3 & 4 - Session 1") == {2, 3, 4}
    assert extract_bloc_numbers("Bloc 2, 3, 4 Session 1 - 22/05/2026") == {2, 3, 4}


def test_suggest_pv_pdf_filename() -> None:
    sess = {
        "label": "Bloc 2, 3, 4 Session 1 - 22/05/2026",
        "scope_text": "Blocs 2, 3 et 4 — S1",
    }
    name = suggest_pv_pdf_filename(track="C", academic_year="2025-2026", session=sess)
    assert name == "PV jury C - 20260522 - Blocs 2, 3 et 4 - S1.pdf"

    name_draft = suggest_pv_pdf_filename(track="P", academic_year="2025-2026", session=sess, draft=True)
    assert name_draft.endswith("brouillon.pdf")

    no_date = suggest_pv_pdf_filename(
        track="P",
        academic_year="2025-2026",
        session={"scope_text": "Bloc 1 — S1"},
    )
    assert "20252026" in no_date
    assert "Bloc 1 - S1" in no_date


def test_extract_jury_date_token() -> None:
    assert extract_jury_date_token("Bloc 1 - Session 1 - 12/01/2026") == "20260112"
    assert extract_jury_date_token("sans date") == ""


def test_suggest_scope_text() -> None:
    assert suggest_scope_text("S1", ordinal=0) == "Bloc 1 — S1"
    assert suggest_scope_text("S1", ordinal=1) == "Blocs 2, 3 et 4 — S1"
    assert suggest_scope_text("FINAL") == "Année"


def test_scope_text_to_block_keys() -> None:
    blocks = [
        "Common courses 1 (block 1)",
        "Physics Courses 1 (Block 2)",
        "Common Courses 2 (Block 3)",
    ]
    assert scope_text_to_block_keys("Bloc 1 - S1", blocks) == {"Common courses 1 (block 1)"}
    assert scope_text_to_block_keys("Blocs 2 et 3", blocks) == {
        "Physics Courses 1 (Block 2)",
        "Common Courses 2 (Block 3)",
    }
