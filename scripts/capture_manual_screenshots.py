#!/usr/bin/env python3
"""Capture d'écrans pour le manuel secrétariat (widgets Qt → PNG)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT = ROOT / "docs" / "images" / "manuel"
OUT.mkdir(parents=True, exist_ok=True)

# Rendu off-screen fiable en CI / sans bureau complet
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _save(widget, name: str) -> Path:
    path = OUT / f"{name}.png"
    pix = widget.grab()
    if pix.isNull():
        raise RuntimeError(f"Capture vide : {name}")
    if not pix.save(str(path), "PNG"):
        raise RuntimeError(f"Échec enregistrement : {path}")
    print("OK", path.relative_to(ROOT))
    return path


def _tab_index(win, label: str) -> int:
    for i in range(win.tabs.count()):
        if win.tabs.tabText(i) == label:
            return i
    raise KeyError(f"Onglet introuvable : {label!r}")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from mne_grade_manager.core.database import Database
    from mne_grade_manager.gui.main_window import MainWindow
    from mne_grade_manager.gui.master_team_dialog import MasterTeamDialog
    from mne_grade_manager.gui.student_profile_dialog import StudentProfileDialog
    from mne_grade_manager.gui.welcome_window import WelcomeWindow
    from mne_grade_manager.services.terminology import TAB_PV_DELIBERATIONS
    from mne_grade_manager.services.repository import Repository

    app = QApplication(sys.argv)

    welcome = WelcomeWindow()
    welcome.resize(520, 620)
    welcome.show()
    app.processEvents()
    _save(welcome, "01-accueil")
    welcome.hide()

    tmp_db = OUT.parent / "_screenshot_tmp.sqlite3"
    if tmp_db.is_file():
        tmp_db.unlink()
    db = Database(path=tmp_db)
    repo = Repository(db)
    try:
        repo.seed_demo_data(academic_year="2025-2026")
    except Exception:
        pass

    win = MainWindow(repo, academic_year="2025-2026")
    win.resize(1360, 860)
    win.show()
    app.processEvents()
    _save(win, "02-fenetre-principale")

    win.tabs.setCurrentIndex(_tab_index(win, "Étudiants"))
    win.students_tab.refresh()
    app.processEvents()
    _save(win, "03-etudiants")

    win.tabs.setCurrentIndex(_tab_index(win, "Maquette"))
    app.processEvents()
    _save(win, "04-maquette")

    win.tabs.setCurrentIndex(_tab_index(win, "Emploi du temps"))
    app.processEvents()
    _save(win, "05-emploi-du-temps")

    win.tabs.setCurrentIndex(_tab_index(win, "Résultats"))
    app.processEvents()
    _save(win, "07-resultats")

    win.tabs.setCurrentIndex(_tab_index(win, TAB_PV_DELIBERATIONS))
    app.processEvents()
    _save(win, "08-pv-deliberations")

    students = repo.list_students_for_level(academic_year="2025-2026", level="M1")
    if students:
        sid = int(students[0]["id"])
        profile = StudentProfileDialog(repo, sid, parent=win, default_academic_year="2025-2026")
        profile.resize(900, 700)
        profile.show()
        app.processEvents()
        _save(profile, "09-fiche-etudiant")
        profile.close()

    dlg = MasterTeamDialog(repo, "2025-2026", parent=win)
    dlg.resize(960, 620)
    dlg.show()
    app.processEvents()
    _save(dlg, "06-equipe-du-master")
    dlg.close()

    db.close()
    if tmp_db.is_file():
        tmp_db.unlink()

    print(f"\nCaptures dans {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
