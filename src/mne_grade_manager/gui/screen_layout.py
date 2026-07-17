"""Ajuste taille et position des fenêtres à la zone utile de l'écran."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QWidget


def _clamp_minimum_to_screen(avail: QRect, min_sz: QSize) -> QSize:
    """Évite des minimums plus grands que l'écran (fréquent sur portable Windows 125 %)."""
    if min_sz.width() <= 0 or min_sz.height() <= 0:
        return min_sz
    cap_w = max(320, int(avail.width() * 0.92))
    cap_h = max(240, int(avail.height() * 0.92))
    return QSize(min(min_sz.width(), cap_w), min(min_sz.height(), cap_h))


def _as_size(value: QSize | tuple[int, int], fallback: QSize | None = None) -> QSize:
    if isinstance(value, tuple):
        return QSize(int(value[0]), int(value[1]))
    return value


def _widget_screen(widget: QWidget) -> QScreen | None:
    wh = widget.windowHandle()
    if wh is not None:
        scr = wh.screen()
        if scr is not None:
            return scr
    parent = widget.parentWidget()
    while parent is not None:
        wh = parent.windowHandle()
        if wh is not None:
            scr = wh.screen()
            if scr is not None:
                return scr
        parent = parent.parentWidget()
    scr = widget.screen()
    if scr is not None:
        return scr
    return QApplication.primaryScreen()


def available_geometry(widget: QWidget) -> QRect:
    """Rectangle utilisable (barre de menu / dock exclus) pour l'écran du widget."""
    screen = _widget_screen(widget)
    if screen is None:
        return QRect(0, 0, 1280, 800)
    return screen.availableGeometry()


def ensure_window_visible(window: QWidget, *, margin: int = 8) -> None:
    """Repositionne la fenêtre si une partie sort de la zone utile de l'écran."""
    avail = available_geometry(window)
    if margin > 0:
        avail = avail.adjusted(margin, margin, -margin, -margin)

    fg = window.frameGeometry()
    x, y = fg.x(), fg.y()
    if fg.width() > avail.width():
        x = avail.left()
    else:
        if fg.right() > avail.right():
            x -= fg.right() - avail.right()
        if fg.left() < avail.left():
            x += avail.left() - fg.left()
    if fg.height() > avail.height():
        y = avail.top()
    else:
        if fg.bottom() > avail.bottom():
            y -= fg.bottom() - avail.bottom()
        if fg.top() < avail.top():
            y += avail.top() - fg.top()
    if x != fg.x() or y != fg.y():
        window.move(x, y)


def adapt_window_size(
    window: QWidget,
    *,
    preferred: QSize | tuple[int, int] | None = None,
    minimum: QSize | tuple[int, int] | None = None,
    screen_fraction: float = 0.88,
    center: bool = True,
) -> QSize:
    """
    Redimensionne ``window`` pour tenir dans l'écran :
    - taille cible = ``preferred`` (ou taille actuelle), plafonnée à ``screen_fraction`` de l'écran ;
    - ``minimum`` réduit si nécessaire pour rester affichable ;
    - repositionnement si la fenêtre déborde (dock, barre de menus, multi-écran).
    """
    avail = available_geometry(window)
    frac = max(0.45, min(float(screen_fraction), 1.0))
    max_w = max(320, int(avail.width() * frac))
    max_h = max(240, int(avail.height() * frac))

    pref = _as_size(preferred, window.size()) if preferred is not None else window.size()
    min_sz = _as_size(minimum, QSize(0, 0)) if minimum is not None else QSize(0, 0)
    if min_sz.width() > 0 or min_sz.height() > 0:
        min_sz = _clamp_minimum_to_screen(avail, min_sz)

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
    else:
        ensure_window_visible(window)

    return QSize(w, h)
