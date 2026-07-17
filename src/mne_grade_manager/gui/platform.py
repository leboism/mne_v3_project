"""Réglages Qt selon la plateforme (surtout Windows / HiDPI)."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication


def configure_before_qapplication() -> None:
    """À appeler avant ``QApplication(sys.argv)``."""
    if sys.platform != "win32":
        return
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def tune_application(app: QApplication) -> None:
    """Police et style après création de l'application."""
    if sys.platform != "win32":
        return
    app.setStyle("Fusion")
    font = app.font()
    if font.family().lower() in {"", "ms shell dlg 2", "system"}:
        font.setFamily("Segoe UI")
    if font.pointSize() > 0 and font.pointSize() < 9:
        font.setPointSize(9)
    app.setFont(font)
