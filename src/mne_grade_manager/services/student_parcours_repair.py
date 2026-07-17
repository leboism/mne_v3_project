"""Réparation niveau / parcours / millésime (imports PDF candidature permutés)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.database import Database

_YEAR_RE = re.compile(r"^\d{4}-\d{4}$")
_M1_TRACKS = frozenset({"P", "C", "M1P", "M1C"})
_M2_TRACKS = frozenset({"NPD", "NPO", "DWM", "NFC", "NRPE"})


def is_academic_year_label(value: str) -> bool:
    return bool(_YEAR_RE.match(str(value or "").strip()))


def _is_year(value: str) -> bool:
    return is_academic_year_label(value)


def coalesce_student_parcours_fields(
    level: str,
    track: str,
    academic_year: str,
    existing: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """
    Fusionne niveau / parcours / millésime issus du formulaire avec la fiche existante.

    Évite d'écraser un parcours valide par une valeur vide ou un millésime (bug import PDF).
    """
    ex = existing or {}
    ex_lv = str(ex.get("level") or "").strip()
    ex_tr = str(ex.get("track") or "").strip()
    ex_ay = str(ex.get("academic_year") or "").strip()

    lv = str(level or "").strip()
    tr = str(track or "").strip()
    ay = str(academic_year or "").strip()

    if _is_year(tr):
        if not ay:
            ay = tr.strip()
        tr = ex_tr
    if _is_year(lv):
        if not ay:
            ay = lv.strip()
        lv = ex_lv

    if not tr:
        tr = ex_tr
    if not lv:
        lv = ex_lv
    if not ay:
        ay = ex_ay

    form_tr = _normalize_track(str(track or "").strip())
    if form_tr in _M1_TRACKS | _M2_TRACKS:
        lv_u = str(level or ex_lv or "").strip().upper()
        if lv_u not in {"M1", "M2"}:
            lv_u = _level_for_track(form_tr)
        return lv_u, form_tr, ay or ex_ay

    tr = _normalize_track(tr)
    lv_u = lv.upper()
    if tr and (not lv_u or lv_u not in {"M1", "M2"}):
        lv = _level_for_track(tr)
    elif lv_u in {"M1", "M2"}:
        lv = lv_u

    return lv, tr, ay


def _normalize_track(code: str) -> str:
    tr = str(code or "").strip().upper()
    if tr in {"M1P"}:
        return "P"
    if tr in {"M1C"}:
        return "C"
    return tr


def _level_for_track(track: str) -> str:
    tr = _normalize_track(track)
    if tr in _M2_TRACKS:
        return "M2"
    return "M1"


def infer_student_parcours(
    db: Database, student_id: int, academic_year: str = ""
) -> tuple[str, str, str] | None:
    """Restaure niveau / parcours / millésime depuis inscriptions ou dossier candidature."""
    inferred = _infer_from_enrollments(db, student_id, academic_year)
    if inferred:
        return inferred
    return _infer_from_admission_attachments(db, student_id, academic_year)


def _infer_from_admission_attachments(
    db: Database, student_id: int, academic_year: str
) -> tuple[str, str, str] | None:
    from ..services.admission_import import infer_track_from_admission_file
    from ..services.attachments import abs_path_from_stored

    rows = db.query_all(
        """
        SELECT file_path
        FROM student_attachments
        WHERE student_id = ?
          AND category = 'admission_dossier'
        ORDER BY id DESC
        """,
        (int(student_id),),
    )
    ay_hint = str(academic_year or "").strip()
    for row in rows:
        path = abs_path_from_stored(str(row["file_path"] or ""))
        if not path.is_file():
            continue
        lv, tr, ay = infer_track_from_admission_file(path)
        tr = _normalize_track(tr)
        if not tr:
            continue
        if not lv:
            lv = _level_for_track(tr)
        return lv, tr, ay or ay_hint
    return None


def repair_student_parcours(db: Database) -> int:
    """
    Corrige les permutations connues et restaure le parcours depuis les inscriptions si besoin.
    Retourne le nombre de fiches modifiées.
    """
    changed = 0
    rows = db.query_all("SELECT * FROM students")
    for row in rows:
        rec = dict(row)
        sid = int(rec["id"])
        level = str(rec.get("level") or "").strip()
        track = str(rec.get("track") or "").strip()
        ay = str(rec.get("academic_year") or "").strip()
        lv_u = level.upper()
        tr_u = track.upper()

        new_level, new_track, new_ay = level, track, ay
        dirty = False

        # Millésime dans level, parcours dans track (ex. level=2025-2026, track=P).
        if _is_year(level) and tr_u in _M1_TRACKS | _M2_TRACKS:
            new_ay = level.strip()
            new_track = _normalize_track(track)
            new_level = _level_for_track(new_track)
            dirty = True

        # Millésime dans track, niveau M1/M2 (parcours perdu ou écrasé).
        elif lv_u in {"M1", "M2"} and _is_year(track):
            if not new_ay:
                new_ay = track.strip()
            new_track = ""
            dirty = True

        # Parcours dans level, millésime dans track (migration historique partielle).
        elif lv_u in {"P", "C"} and _is_year(track) and not ay:
            new_ay = track.strip()
            new_track = _normalize_track(level)
            new_level = "M1"
            dirty = True

        if dirty:
            db.execute(
                "UPDATE students SET level = ?, track = ?, academic_year = ? WHERE id = ?",
                (new_level, new_track, new_ay, sid),
            )
            changed += 1
            level, track, ay = new_level, new_track, new_ay

        if str(track or "").strip():
            continue

        inferred = infer_student_parcours(db, sid, ay)
        if not inferred:
            continue
        inf_lv, inf_tr, inf_ay = inferred
        db.execute(
            """
            UPDATE students
            SET level = ?, track = ?, academic_year = ?
            WHERE id = ?
              AND TRIM(COALESCE(track, '')) = ''
            """,
            (inf_lv, inf_tr, inf_ay or ay, sid),
        )
        ch = db.query_one("SELECT changes() AS c")
        if ch and int(ch["c"]) > 0:
            changed += 1

    return changed


def _infer_from_enrollments(
    db: Database, student_id: int, academic_year: str
) -> tuple[str, str, str] | None:
    rows = db.query_all(
        """
        SELECT t.level, t.track, t.academic_year
        FROM enrollments e
        JOIN templates t ON t.id = e.template_id
        WHERE e.student_id = ?
        ORDER BY
            CASE WHEN TRIM(IFNULL(t.academic_year, '')) = TRIM(?) THEN 0 ELSE 1 END,
            t.id DESC
        """,
        (int(student_id), str(academic_year or "").strip()),
    )
    for row in rows:
        lv = str(row["level"] or "").strip().upper()
        tr = _normalize_track(str(row["track"] or ""))
        ay = str(row["academic_year"] or "").strip()
        if lv and tr:
            return lv, tr, ay
    return None
