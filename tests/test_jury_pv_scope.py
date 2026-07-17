"""PV de jury : une délibération n'affiche que ses propres décisions."""

from __future__ import annotations

from pathlib import Path
import tempfile

from mne_grade_manager.core.database import Database
from mne_grade_manager.services.repository import Repository


def _repo() -> Repository:
    db = Database(Path(tempfile.mkdtemp()) / "jury_pv_scope.sqlite3")
    return Repository(db)


def _setup_template(repo: Repository) -> tuple[int, int, int, int]:
    cid_b2 = repo.add_course("UE-B2", "UE bloc 2", ects=4)
    cid_b3 = repo.add_course("UE-B3", "UE bloc 3", ects=4)
    repo.add_assessment(cid_b2, "EE", "EE", 100.0, session=1)
    repo.add_assessment(cid_b3, "EE", "EE", 100.0, session=1)
    tid = repo.add_template("2025-2026 M1 P", "M1", "P", "2025-2026", "1")
    repo.add_course_to_template(tid, cid_b2, block_name="Common courses 1 (block 2)")
    repo.add_course_to_template(tid, cid_b3, block_name="Common Courses 2 (Block 3)")
    sid = repo.add_student("S1", "", "", "Dupont", "Alice", academic_year="2025-2026")
    repo.enroll_student(sid, tid)
    return sid, tid, cid_b2, cid_b3


def test_pv_export_filters_by_jury_session_id() -> None:
    repo = _repo()
    sid, tid, cid_b2, cid_b3 = _setup_template(repo)
    js1 = repo.add_jury_session(tid, "S1", label="Jury 1", scope_text="Bloc 2")
    js2 = repo.add_jury_session(tid, "S1", label="Jury 2", scope_text="Bloc 3")

    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b2, points=0.2, jury_session_id=js1
    )
    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b3, points=0.3, jury_session_id=js2
    )
    repo.set_second_session_decision(
        sid, tid, cid_b2, sent=True, s1_jury=True, jury_session_id=js1
    )
    repo.set_second_session_decision(
        sid, tid, cid_b3, sent=True, s1_jury=True, jury_session_id=js2
    )

    pv1_adj = repo.list_jury_adjustments_for_export(tid, jury_session_id=js1)
    pv2_adj = repo.list_jury_adjustments_for_export(tid, jury_session_id=js2)
    assert len(pv1_adj) == 1
    assert int(pv1_adj[0]["course_id"]) == cid_b2
    assert len(pv2_adj) == 1
    assert int(pv2_adj[0]["course_id"]) == cid_b3

    pv1_s2 = repo.list_second_session_for_export(tid, jury_session_id=js1)
    pv2_s2 = repo.list_second_session_for_export(tid, jury_session_id=js2)
    assert {int(r["course_id"]) for r in pv1_s2} == {cid_b2}
    assert {int(r["course_id"]) for r in pv2_s2} == {cid_b3}


def test_jury_adjustments_latest_session_wins_for_same_course() -> None:
    """Une UE ne cumule pas les points de plusieurs délibérations — la plus récente l'emporte."""
    repo = _repo()
    sid, tid, cid_b2, cid_b3 = _setup_template(repo)
    js1 = repo.add_jury_session(tid, "S1", scope_text="Bloc 2")
    js2 = repo.add_jury_session(tid, "S1", scope_text="Bloc 3")
    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b2, points=0.1, jury_session_id=js1
    )
    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b2, points=0.2, jury_session_id=js2
    )

    jury_map = repo._jury_map_for_template(tid)
    assert abs(jury_map[sid]["course"][cid_b2] - 0.2) < 1e-9


def test_legacy_rows_filtered_by_prior_scope_text() -> None:
    repo = _repo()
    sid, tid, cid_b2, cid_b3 = _setup_template(repo)
    js1 = repo.add_jury_session(tid, "S1", label="Jury 1", scope_text="Bloc 1")
    js2 = repo.add_jury_session(tid, "S1", label="Jury 2", scope_text="Blocs 2, 3 et 4")

    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid_b2, points=0.2)
    repo.set_second_session_decision(sid, tid, cid_b2, sent=True, s1_jury=True)
    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid_b3, points=0.3)
    repo.set_second_session_decision(sid, tid, cid_b3, sent=True, s1_jury=True)

    repo.repair_jury_decision_session_links(tid)

    pv1_s2 = repo.list_second_session_for_export(tid, jury_session_id=js1)
    pv2_s2 = repo.list_second_session_for_export(tid, jury_session_id=js2)
    assert pv1_s2 == []
    assert {int(r["course_id"]) for r in pv2_s2} == {cid_b2, cid_b3}

    pv2_adj = repo.list_jury_adjustments_for_export(tid, jury_session_id=js2)
    assert len(pv2_adj) == 2
    pv1_adj = repo.list_jury_adjustments_for_export(tid, jury_session_id=js1)
    assert pv1_adj == []


def test_duplicate_jury_points_in_two_deliberations_collapsed() -> None:
    """+0,08 enregistré deux fois (délibérations différentes) → un seul +0,08."""
    repo = _repo()
    sid, tid, cid_b2, _cid_b3 = _setup_template(repo)
    js1 = repo.add_jury_session(tid, "S1", scope_text="Bloc 2")
    js2 = repo.add_jury_session(tid, "S1", scope_text="Blocs 2, 3 et 4")
    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b2, points=0.08, jury_session_id=js1
    )
    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b2, points=0.08, jury_session_id=js2
    )
    jury_map = repo._jury_map_for_template(tid)
    assert abs(jury_map[sid]["course"][cid_b2] - 0.08) < 1e-9


def test_legacy_and_tagged_jury_points_not_doubled() -> None:
    """Legacy (sans session) + saisie rattachée : un seul +0,08, pas +0,16."""
    repo = _repo()
    sid, tid, cid_b2, _cid_b3 = _setup_template(repo)
    js1 = repo.add_jury_session(tid, "S1", label="Jury bloc 2", scope_text="Bloc 2")
    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid_b2, points=0.08)
    repo.upsert_jury_adjustment(
        sid, tid, "course", course_id=cid_b2, points=0.08, jury_session_id=js1
    )
    jury_map = repo._jury_map_for_template(tid)
    assert abs(jury_map[sid]["course"][cid_b2] - 0.08) < 1e-9


def test_repair_drops_legacy_duplicate_after_tagged_save() -> None:
    repo = _repo()
    sid, tid, cid_b2, _cid_b3 = _setup_template(repo)
    js1 = repo.add_jury_session(tid, "S1", scope_text="Bloc 2")
    repo.upsert_jury_adjustment(sid, tid, "course", course_id=cid_b2, points=0.08)
    repo.db.execute(
        """
        INSERT INTO jury_adjustments(
            student_id, template_id, jury_session_id, scope, course_id, block_name, points
        ) VALUES (?, ?, ?, 'course', ?, '', 0.08)
        """,
        (sid, tid, js1, cid_b2),
    )
    repo.repair_jury_decision_session_links(tid)
    jury_map = repo._jury_map_for_template(tid)
    assert abs(jury_map[sid]["course"][cid_b2] - 0.08) < 1e-9


def test_sync_obligations_do_not_erase_jury_session_id() -> None:
    repo = _repo()
    sid, tid, cid_b2, _cid_b3 = _setup_template(repo)
    js2 = repo.add_jury_session(tid, "S1", scope_text="Bloc 2")
    repo.set_second_session_decision(
        sid, tid, cid_b2, sent=True, s1_jury=True, jury_session_id=js2
    )
    repo.sync_second_session_obligations(tid)
    row = repo.db.query_one(
        "SELECT jury_session_id FROM second_session_decisions WHERE student_id=? AND template_id=? AND course_id=?",
        (sid, tid, cid_b2),
    )
    assert int(row["jury_session_id"]) == js2
