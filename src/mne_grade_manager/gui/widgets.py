from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QTableWidget, QTableWidgetItem, QToolButton


@dataclass
class ActionToolbar:
    layout: QHBoxLayout
    primary_buttons: list[QPushButton] = field(default_factory=list)
    menu_actions: dict[str, QAction] = field(default_factory=dict)


def make_actions_toolbar(
    parent,
    *,
    primary: list[tuple[str, Callable[[], None]]],
    menu_sections: list[list[tuple[str, Callable[[], None]]]] | None = None,
) -> ActionToolbar:
    """
    Barre d'outils : quelques boutons principaux + menu « Actions » (comme l'onglet Étudiants).
    ``menu_sections`` : listes d'actions ; une ligne vide ``[]`` insère un séparateur.
    """
    bar = QHBoxLayout()
    primary_buttons: list[QPushButton] = []
    menu_actions: dict[str, QAction] = {}
    for label, slot in primary:
        btn = QPushButton(label, parent)
        btn.clicked.connect(lambda *_args, _slot=slot: _slot())
        bar.addWidget(btn)
        primary_buttons.append(btn)
    if menu_sections:
        more = QToolButton(parent)
        more.setText("Actions")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(parent)
        for section in menu_sections:
            if not section:
                menu.addSeparator()
                continue
            for label, slot in section:
                act = menu.addAction(label, lambda *_args, _slot=slot: _slot())
                menu_actions[label] = act
        more.setMenu(menu)
        bar.addWidget(more)
    bar.addStretch()
    return ActionToolbar(layout=bar, primary_buttons=primary_buttons, menu_actions=menu_actions)


def fill_table(table: QTableWidget, headers: list[str], rows: list[list[str]]) -> None:
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(value)))
    table.resizeColumnsToContents()
