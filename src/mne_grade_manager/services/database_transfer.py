"""Export / import de l'ensemble des données (base SQLite + pièces jointes) pour transfert."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.database import APP_DIR, CUSTOM_YEARS_FILE, Database
from .academic_years import HIDDEN_YEARS_FILE
from .attachments import ATTACHMENTS_ROOT, ensure_attachments_root

EXPORT_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DB_ARCHIVE_NAME = "grade_manager.sqlite3"
ATTACHMENTS_ARCHIVE_DIR = "attachments"
CUSTOM_YEARS_ARCHIVE_NAME = "custom_years.json"
HIDDEN_YEARS_ARCHIVE_NAME = "hidden_years.json"
APP_ID = "mne_grade_manager"

ARCHIVE_FILTER = "Archive MNE (*.zip)"
SQLITE_FILTER = "SQLite (*.sqlite3)"


@dataclass(frozen=True)
class TransferSummary:
    path: Path
    attachment_files: int
    sqlite_bytes: int
    zip_bytes: int = 0
    archive_files: int = 0
    verified: bool = False


def default_export_basename(academic_year: str = "") -> str:
    stamp = datetime.now().strftime("%Y-%m-%d")
    if (academic_year or "").strip():
        safe = academic_year.strip().replace("/", "-").replace(" ", "_")
        return f"mne_export_{safe}_{stamp}"
    return f"mne_export_{stamp}"


def _count_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def _write_manifest(
    dest: Path,
    *,
    academic_year: str = "",
    attachment_files: int = 0,
) -> None:
    payload: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "app": APP_ID,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "attachment_files": attachment_files,
    }
    if academic_year.strip():
        payload["academic_year_hint"] = academic_year.strip()
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise ValueError("Archive invalide : manifeste absent.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Archive invalide : manifeste illisible.") from exc
    if not isinstance(data, dict):
        raise ValueError("Archive invalide : manifeste incorrect.")
    if data.get("app") != APP_ID:
        raise ValueError("Ce fichier n'est pas une archive MNE Grade Manager.")
    version = int(data.get("format_version") or 0)
    if version > EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"Archive créée avec une version plus récente du programme (format {version}). "
            "Mettez à jour l'application sur cet ordinateur."
        )
    return data


def _validate_sqlite(path: Path) -> None:
    if not path.is_file():
        raise ValueError("Archive invalide : base SQLite absente.")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(
            "Fichier SQLite vide (0 octet).\n\n"
            "L'enregistrement n'a pas abouti. Réessayez « Enregistrer la base SQLite seule… » "
            "ou utilisez « Exporter les données (transfert)… » pour une archive .zip complète."
        )
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='students'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ValueError("Fichier SQLite invalide ou corrompu.") from exc
    if row is None:
        tables = []
        try:
            conn = sqlite3.connect(str(path))
            try:
                tables = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                ]
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        hint = f"\n\nTables trouvées : {', '.join(tables[:12]) or 'aucune'}."
        raise ValueError(
            "Ce fichier SQLite ne semble pas provenir de MNE Grade Manager."
            f"{hint}"
        )


def export_sqlite_only(dest: Path | str, db: Database) -> TransferSummary:
    """Copie la base ouverte vers un fichier .sqlite3 (avec validation)."""
    dest_path = Path(dest)
    if dest_path.suffix.lower() != ".sqlite3":
        dest_path = dest_path.with_suffix(".sqlite3")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size == 0:
        dest_path.unlink()

    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        db.backup_to(tmp_path)
        _validate_sqlite(tmp_path)
        shutil.copy2(tmp_path, dest_path)
        _validate_sqlite(dest_path)
    except Exception:
        if dest_path.exists() and dest_path.stat().st_size == 0:
            dest_path.unlink(missing_ok=True)
        raise
    finally:
        tmp_path.unlink(missing_ok=True)

    sqlite_bytes = dest_path.stat().st_size
    return TransferSummary(
        path=dest_path,
        attachment_files=0,
        sqlite_bytes=sqlite_bytes,
        zip_bytes=0,
        archive_files=1,
        verified=True,
    )


def verify_export_zip(zip_path: Path | str, *, expected_attachments: int | None = None) -> dict[str, Any]:
    """
    Contrôle l'intégrité d'une archive exportée (zip, manifeste, base SQLite, pièces jointes).
    Lève ValueError si un problème est détecté.
    """
    path = Path(zip_path)
    if not path.is_file():
        raise ValueError(f"Archive introuvable : {path}")
    if path.stat().st_size <= 0:
        raise ValueError("Archive vide.")

    with tempfile.TemporaryDirectory(prefix="mne_verify_") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(path) as zf:
            corrupt = zf.testzip()
            if corrupt:
                raise ValueError(f"Archive corrompue (fichier défectueux : {corrupt}).")
            names = set(zf.namelist())
            for required in (MANIFEST_NAME, DB_ARCHIVE_NAME):
                if required not in names:
                    raise ValueError(f"Archive incomplète : « {required} » manquant.")
            zf.extractall(root)

        manifest = _read_manifest(root)
        db_file = root / DB_ARCHIVE_NAME
        _validate_sqlite(db_file)

        attachments_src = root / ATTACHMENTS_ARCHIVE_DIR
        att_count = _count_files(attachments_src) if attachments_src.is_dir() else 0
        manifest_att = int(manifest.get("attachment_files") or 0)
        if att_count != manifest_att:
            raise ValueError(
                f"Incohérence pièces jointes : {att_count} dans l'archive, "
                f"{manifest_att} indiqué dans le manifeste."
            )
        if expected_attachments is not None and att_count != int(expected_attachments):
            raise ValueError(
                f"Incohérence pièces jointes : {att_count} exporté(s), "
                f"{int(expected_attachments)} attendu(s)."
            )

        return {
            "zip_bytes": path.stat().st_size,
            "archive_files": len(names),
            "attachment_files": att_count,
            "sqlite_bytes": db_file.stat().st_size,
            "manifest": manifest,
        }


def export_data_package(
    dest: Path | str,
    db: Database,
    *,
    academic_year: str = "",
) -> TransferSummary:
    """Crée une archive .zip (base + attachments + années personnalisées)."""
    dest_path = Path(dest)
    if dest_path.suffix.lower() != ".zip":
        dest_path = dest_path.with_suffix(".zip")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    attachment_files = _count_files(ATTACHMENTS_ROOT)

    with tempfile.TemporaryDirectory(prefix="mne_export_") as tmp:
        root = Path(tmp)
        db_tmp = root / DB_ARCHIVE_NAME
        db.backup_to(db_tmp)

        if ATTACHMENTS_ROOT.is_dir():
            shutil.copytree(ATTACHMENTS_ROOT, root / ATTACHMENTS_ARCHIVE_DIR)
        if CUSTOM_YEARS_FILE.is_file():
            shutil.copy2(CUSTOM_YEARS_FILE, root / CUSTOM_YEARS_ARCHIVE_NAME)
        if HIDDEN_YEARS_FILE.is_file():
            shutil.copy2(HIDDEN_YEARS_FILE, root / HIDDEN_YEARS_ARCHIVE_NAME)

        _write_manifest(root / MANIFEST_NAME, academic_year=academic_year, attachment_files=attachment_files)

        archive_files = 0
        with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(root.rglob("*")):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(root).as_posix())
                    archive_files += 1

        sqlite_bytes = db_tmp.stat().st_size

    try:
        verify_export_zip(dest_path, expected_attachments=attachment_files)
    except Exception:
        if dest_path.is_file():
            dest_path.unlink(missing_ok=True)
        raise
    zip_bytes = dest_path.stat().st_size

    return TransferSummary(
        path=dest_path,
        attachment_files=attachment_files,
        sqlite_bytes=sqlite_bytes,
        zip_bytes=zip_bytes,
        archive_files=archive_files,
        verified=True,
    )


def import_sqlite_only(source: Path | str, db: Database) -> None:
    """Importe uniquement la base (compatible « Enregistrer la base sous… »)."""
    src = Path(source)
    _validate_sqlite(src)
    db.replace_from_file(src)


def _replace_attachments(source_dir: Path | None) -> int:
    if ATTACHMENTS_ROOT.exists():
        shutil.rmtree(ATTACHMENTS_ROOT)
    if source_dir is not None and source_dir.is_dir():
        shutil.copytree(source_dir, ATTACHMENTS_ROOT)
        return _count_files(ATTACHMENTS_ROOT)
    ensure_attachments_root()
    return 0


def _restore_custom_years(source_file: Path | None) -> None:
    if source_file is not None and source_file.is_file():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, CUSTOM_YEARS_FILE)


def _restore_hidden_years(source_file: Path | None) -> None:
    if source_file is not None and source_file.is_file():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, HIDDEN_YEARS_FILE)


def import_data_package(source: Path | str, db: Database) -> TransferSummary:
    """Restaure base + pièces jointes depuis une archive .zip ou un .sqlite3 seul."""
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {src}")

    if src.suffix.lower() == ".sqlite3":
        import_sqlite_only(src, db)
        return TransferSummary(path=src, attachment_files=_count_files(ATTACHMENTS_ROOT), sqlite_bytes=src.stat().st_size)

    if src.suffix.lower() != ".zip":
        raise ValueError("Format non reconnu : utilisez une archive .zip ou un fichier .sqlite3.")

    with tempfile.TemporaryDirectory(prefix="mne_import_") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(src) as zf:
            zf.extractall(root)

        _read_manifest(root)
        db_file = root / DB_ARCHIVE_NAME
        _validate_sqlite(db_file)

        attachments_src = root / ATTACHMENTS_ARCHIVE_DIR
        att_src = attachments_src if attachments_src.is_dir() else None
        years_src = root / CUSTOM_YEARS_ARCHIVE_NAME
        years = years_src if years_src.is_file() else None
        hidden_src = root / HIDDEN_YEARS_ARCHIVE_NAME
        hidden = hidden_src if hidden_src.is_file() else None

        db.replace_from_file(db_file)
        attachment_files = _replace_attachments(att_src)
        _restore_custom_years(years)
        _restore_hidden_years(hidden)
        sqlite_bytes = db_file.stat().st_size

    return TransferSummary(
        path=src,
        attachment_files=attachment_files,
        sqlite_bytes=sqlite_bytes,
    )
