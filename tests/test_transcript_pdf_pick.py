"""Sélection du bon PDF transcript (Final vs Provisional) pour les e-mails."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.services.jury_reports import (
    find_transcript_pdf_in_dir,
    is_final_transcript_pdf,
    is_provisional_transcript_pdf,
    transcript_default_filename,
)


def test_find_transcript_pdf_prefers_final_in_final_mode() -> None:
    stu = {"last_name": "Dupont", "first_name": "Alice"}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        prov = transcript_default_filename(stu, level="M1", track="C", final=False)
        final = transcript_default_filename(stu, level="M1", track="C", final=True)
        (folder / prov).write_bytes(b"%PDF-1.4 prov")
        (folder / final).write_bytes(b"%PDF-1.4 final")

        found = find_transcript_pdf_in_dir(folder, stu, level="M1", track="C", final=True)
        assert found is not None
        assert is_final_transcript_pdf(found)
        assert not is_provisional_transcript_pdf(found)

        missing = find_transcript_pdf_in_dir(folder, stu, level="M1", track="C", final=False)
        assert missing is not None
        assert is_provisional_transcript_pdf(missing)


def test_find_transcript_pdf_final_mode_ignores_provisional_only() -> None:
    stu = {"last_name": "Martin", "first_name": "Bob"}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        prov = transcript_default_filename(stu, level="M1", track="P", final=False)
        (folder / prov).write_bytes(b"%PDF-1.4 prov")

        found = find_transcript_pdf_in_dir(folder, stu, level="M1", track="P", final=True)
        assert found is None
