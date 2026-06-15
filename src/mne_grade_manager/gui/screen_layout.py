"""Ajuste taille et position des fenêtres à la zone utile de l'écran."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication, QWidget


def _as_size(value: QSize | tuple[int, int], fallback: QSize | None = None) -> QSize:
    if isinstance(value, tuple):
        return QSize(int(value[0]), int(value[1]))
    return value


def available_geometry(widget: QWidget) -> QRect:
    """Rectangle utilisable (barre de menu / dock exclus) pour l'écran du widget."""
    screen = widget.screen()
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1280, 800)
    return screen.availableGeometry()


def adapt_window_size(
    window: QWidget,
    *,
    preferred: QSize | tuple[int, int] | None = None,
    minimum: QSize | tuple[int, int] | None = None,
    screen_fraction: float = 0.92,
    center: bool = True,
) -> QSize:
    """
    Redimensionne ``window`` pour tenir dans l'écran :
    - taille cible = ``preferred`` (ou taille actuelle), plafonnée à ``screen_fraction`` de l'écran ;
    - ``minimum`` réduit si nécessaire pour rester affichable.
    """
    avail = available_geometry(window)
    frac = max(0.5, min(float(screen_fraction), 1.0))
    max_w = max(320, int(avail.width() * frac))
    max_h = max(240, int(avail.height() * frac))

    pref = _as_size(preferred, window.size()) if preferred is not None else window.size()
    min_sz = _as_size(minimum, QSize(0, 0)) if minimum is not None else QSize(0, 0)

    min_w = min(min_sz.width(), max_w) if min_sz.width() > 0 else 0
    min_h = min(min_sz.height(), max_h) if min_sz.height() > 0 else 0
    if min_w > 0 and min_h > 0:
        window.setMinimumSize(min_w, min_h)

    w = min(max(pref.width(), min_w or 0, 1), max_w)
    h = min(max(pref.height(), min_h or 0, 1), max_h)
    window.resize(w, h)

    if center:
        frame = window.frameGeometry()
        frame.moveCenter(avail.center())
        window.move(frame.topLeft())

    return QSize(w, h)
