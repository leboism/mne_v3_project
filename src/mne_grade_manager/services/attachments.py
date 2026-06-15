"""Stockage des fichiers joints (photos, PDF) hors base SQLite."""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core.database import APP_DIR

ATTACHMENTS_ROOT = APP_DIR / "attachments"


def _safe_name(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w.\- ]+", "_", base, flags=re.UNICODE).strip()
    return base or "fichier"


def ensure_attachments_root() -> Path:
    ATTACHMENTS_ROOT.mkdir(parents=True, exist_ok=True)
    return ATTACHMENTS_ROOT


def rel_path(abs_path: Path) -> str:
    """Chemin relatif à ``APP_DIR`` pour stockage en base."""
    try:
        return str(abs_path.relative_to(APP_DIR))
    except ValueError:
        return str(abs_path)


def abs_path_from_stored(stored: str) -> Path:
    p = Path(stored or "")
    if p.is_absolute():
        return p
    return APP_DIR / p


def store_file(
    src: str | Path,
    *,
    subdir: str,
    prefix: str = "",
    allowed_suffixes: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    """
    Copie ``src`` vers ``attachments/<subdir>/``.
    Retourne (chemin relatif APP_DIR, nom de fichier d'origine).
    """
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {src_path}")
    suffix = src_path.suffix.lower()
    if allowed_suffixes and suffix not in allowed_suffixes:
        raise ValueError(f"Extension non autorisée : {suffix}")
    ensure_attachments_root()
    dest_dir = ATTACHMENTS_ROOT / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    token = uuid.uuid4().hex[:8]
    safe = _safe_name(src_path.name)
    fname = f"{prefix}{stamp}_{token}_{safe}" if prefix else f"{stamp}_{token}_{safe}"
    dest = dest_dir / fname
    shutil.copy2(src_path, dest)
    return rel_path(dest), src_path.name


def store_student_photo(student_id: int, src: str | Path) -> str:
    rel, _ = store_file(
        src,
        subdir=f"students/{int(student_id)}/photos",
        prefix="photo_",
        allowed_suffixes=(".jpg", ".jpeg", ".png", ".webp", ".gif"),
    )
    return rel


def store_student_document(student_id: int, category: str, src: str | Path) -> tuple[str, str]:
    rel, orig = store_file(
        src,
        subdir=f"students/{int(student_id)}/docs/{category}",
        allowed_suffixes=(".pdf",),
    )
    return rel, orig


COURSE_SYLLABUS_SUFFIXES = (".pdf", ".doc", ".docx")


def store_course_syllabus(course_id: int, src: str | Path) -> tuple[str, str]:
    rel, orig = store_file(
        src,
        subdir=f"courses/{int(course_id)}/syllabus",
        prefix="syllabus_",
        allowed_suffixes=COURSE_SYLLABUS_SUFFIXES,
    )
    return rel, orig


def store_internship_convention(
    student_id: int, course_id: int, template_id: int, src: str | Path
) -> str:
    rel, _ = store_file(
        src,
        subdir=f"internships/{int(student_id)}_{int(course_id)}_{int(template_id)}",
        prefix="convention_",
        allowed_suffixes=(".pdf",),
    )
    return rel


def delete_stored_file(stored_path: str) -> None:
    if not stored_path:
        return
    p = abs_path_from_stored(stored_path)
    if p.is_file():
        p.unlink()
