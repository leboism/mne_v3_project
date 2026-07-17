"""Erratum PDF accréditation — maquette M2 DWM."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.maquette_erratum import (
    DWM_TEMPLATE_SPEC,
    apply_m2_dwm_accreditation_erratum,
)
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    return Repository(Database(Path(tempfile.mkdtemp()) / "erratum.sqlite3"))


def _seed_dwm_template(repo: Repository) -> int:
    codes = [c for c, _, _ in DWM_TEMPLATE_SPEC if c != "EN00002180"]
    cids = []
    for code in codes:
        if code == "EN00018956":
            cid = repo.add_course(code, "M2 Nuclear Engineering Internship", 18.0, course_type="internship")
        elif code == "EN00002179":
            cid = repo.add_course(code, "Dismantling: Project Case Study", 3.0, mne_module_code="M2B4-W-DIS")
        elif code == "EN00013743":
            cid = repo.add_course(code, "Risk Management of Dismantling Operations", 3.0, mne_module_code="M2B4-W-RMDO")
        else:
            cid = repo.add_course(code, f"Course {code}", 3.0)
        cids.append(cid)
    tid = repo.add_template("2026-2027 M2 DWM", "M2", "DWM", "2026-2027", "1")
    for cid in cids:
        repo.add_course_to_template(tid, cid)
    return tid


def test_apply_dwm_erratum_restructures_bloc4() -> None:
    repo = _repo()
    _seed_dwm_template(repo)
    summary = apply_m2_dwm_accreditation_erratum(repo)
    assert summary["template_rows"] == len(DWM_TEMPLATE_SPEC)
    assert "EN00002180" in summary["courses_created"]

    tpl = repo.find_template_for_year_level_track(academic_year="2026-2027", level="M2", track="DWM")
    assert tpl
    rows = repo.list_template_courses(int(tpl["id"]))
    by_code = {}
    for r in rows:
        c = repo.get_course(int(r["course_id"])) or {}
        by_code[str(c.get("code"))] = (c, r)

    assert by_code["EN00002179"][0]["name"] == "Risk management of  Dismantling operation"
    assert by_code["EN00013743"][0]["name"] == "Dismantlement case studies"
    assert by_code["EN00002180"][0]["ects"] == 0.0
    assert by_code["EN00018956"][0]["ects"] == 18.0

    total = sum(float(by_code[c][0]["ects"] or 0) for c, _, _ in DWM_TEMPLATE_SPEC)
    assert abs(total - 60.0) < 0.01
