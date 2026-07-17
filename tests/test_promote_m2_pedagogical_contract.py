"""Passage M1 → M2 : réinitialisation du contrat pédagogique."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mne_grade_manager.core.database import Database
from mne_grade_manager.core.institutions import PEDAGOGICAL_CONTRACT_CATEGORY
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "promote_m2.sqlite3"))


def test_promote_student_to_m2_resets_pedagogical_contract() -> None:
    repo = _repo()
    tid_m1 = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    tid_m2 = repo.add_template("2026-2027 M2 NPD", "M2", "NPD", "2026-2027", "1")
    sid = repo.add_student(
        "S1",
        "",
        "",
        "Dupont",
        "Alice",
        academic_year="2025-2026",
        level="M1",
        track="P",
    )
    repo.enroll_student(sid, tid_m1)
    repo.set_pedagogical_contract_paper(sid, True)

    pdf = Path(tempfile.mkdtemp()) / "contrat_m1.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    repo.add_student_attachment(sid, PEDAGOGICAL_CONTRACT_CATEGORY, pdf)
    assert repo.has_pedagogical_contract(sid)

    repo.promote_student_to_m2(sid, "2026-2027", "NPD")

    stu = repo.get_student(sid) or {}
    assert stu.get("level") == "M2"
    assert stu.get("track") == "NPD"
    assert not repo.has_pedagogical_contract_paper(sid)
    assert not repo.has_pedagogical_contract_pdf(sid)
    assert not repo.has_pedagogical_contract(sid)
    assert not repo.list_student_attachments(sid, category=PEDAGOGICAL_CONTRACT_CATEGORY)


def test_batch_reset_m2_pedagogical_contracts() -> None:
    repo = _repo()
    sid_m2 = repo.add_student(
        "S2",
        "",
        "",
        "Martin",
        "Paul",
        academic_year="2026-2027",
        level="M2",
        track="NPD",
    )
    sid_m1 = repo.add_student(
        "S1",
        "",
        "",
        "Dupont",
        "Alice",
        academic_year="2025-2026",
        level="M1",
        track="P",
    )
    repo.set_pedagogical_contract_paper(sid_m2, True)
    repo.set_pedagogical_contract_paper(sid_m1, True)

    listed = repo.list_m2_students_with_pedagogical_contract()
    assert len(listed) == 1
    assert int(listed[0]["id"]) == sid_m2

    count, names = repo.reset_pedagogical_contracts_for_m2_students()
    assert count == 1
    assert "Martin" in names[0]
    assert not repo.has_pedagogical_contract(sid_m2)
    assert repo.has_pedagogical_contract_paper(sid_m1)
