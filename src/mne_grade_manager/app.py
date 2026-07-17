import sys
from PySide6.QtWidgets import QApplication
from .core.database import Database
from .services.repository import Repository
from .gui.welcome_window import WelcomeWindow
from .gui.main_window import MainWindow
from .gui.platform import configure_before_qapplication, tune_application


def main() -> int:
    configure_before_qapplication()
    app = QApplication(sys.argv)
    tune_application(app)
    app.setApplicationName("MNE Grade Manager")
    db = Database()
    repo = Repository(db)
    for tpl in repo.list_templates():
        try:
            repo.consolidate_template_jury_rosters(int(tpl["id"]))
        except Exception:
            pass
    try:
        repo.cleanup_empty_orphan_jury_rosters()
        repo.repair_missing_s1_jury_sessions()
        for tpl in repo.list_templates():
            try:
                repo.repair_jury_decision_session_links(int(tpl["id"]))
            except Exception:
                pass
    except Exception:
        pass

    welcome = WelcomeWindow(db=db)
    main_window: MainWindow | None = None

    def on_year_selected(academic_year: str) -> None:
        nonlocal main_window
        def back_to_welcome() -> None:
            nonlocal main_window
            if main_window is not None:
                main_window.close()
                main_window = None
            welcome.show()
            welcome.raise_()
            welcome.activateWindow()

        main_window = MainWindow(repo, academic_year=academic_year, back_to_welcome=back_to_welcome)
        main_window.show()
        welcome.hide()

    welcome.year_selected.connect(on_year_selected)
    welcome.show()

    return app.exec()
