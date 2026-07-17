"""Stage M2 : placement maquette et arborescence."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "m2_stage.sqlite3"))


def test_repair_moves_m2_internship_from_m1_to_m2_templates() -> None:
    repo = _repo()
    cid_m2 = repo.add_course(
        "EN00018956",
        "M2 Nuclear Engineering Internship",
        30.0,
        course_type="internship",
        mne_module_code="S2-C-INTER",
    )
    tid_m1c = repo.add_template("2026-2027 M1 C", "M1", "C", "2026-2027", "1")
    tid_m2_npd = repo.add_template("2026-2027 M2 NPD", "M2", "NPD", "2026-2027", "1")
    tid_m2_npo = repo.add_template("2026-2027 M2 NPO", "M2", "NPO", "2026-2027", "1")
    repo.add_course_to_template(tid_m1c, cid_m2, block_name="Internship (block 5)", display_order=99)

    fixed = repo.repair_m2_internship_maquette_placements("2026-2027")
    assert fixed >= 2

    m1_codes = {int(r["course_id"]) for r in repo.list_template_courses(tid_m1c)}
    assert cid_m2 not in m1_codes
    for tid in (tid_m2_npd, tid_m2_npo):
        codes = {int(r["course_id"]) for r in repo.list_template_courses(tid)}
        assert cid_m2 in codes
