"""Sélection d'une composition jury (même millésime / niveau, autre parcours possible)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..core.parcours import track_label


def _roster_label(row: dict[str, Any]) -> str:
    lv = str(row.get("level") or "").strip().upper()
    tr = str(row.get("track") or "").strip().upper()
    parcours = f"{lv} {track_label(lv, tr)}" if lv and tr else str(row.get("template_name") or "")
    name = str(row.get("name") or f"#{row.get('id')}")
    n = int(row.get("member_count") or 0)
    ay = str(row.get("tpl_academic_year") or row.get("academic_year") or "").strip()
    bits = [parcours, name, f"{n} membre(s)"]
    if ay:
        bits.append(ay)
    return " — ".join(bits)


class JuryRosterPickDialog(QDialog):
    """Choisir une composition source dans le catalogue (autres parcours)."""

    def __init__(
        self,
        catalog: list[dict[str, Any]],
        *,
        title: str = "Composition source",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._catalog = list(catalog)
        self.selected_roster_id: int | None = None

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Compositions enregistrées sur les autres parcours du même millésime / niveau."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)

        self.list_widget = QListWidget()
        for row in self._catalog:
            it = QListWidgetItem(_roster_label(row))
            it.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.list_widget.addItem(it)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        lay.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self) -> None:
        it = self.list_widget.currentItem()
        if it is None:
            return
        self.selected_roster_id = int(it.data(Qt.ItemDataRole.UserRole))
        self.accept()


class JuryRosterCopyDialog(QDialog):
    """Copier une composition vers une autre maquette (filière)."""

    def __init__(
        self,
        target_templates: list[dict[str, Any]],
        *,
        default_name: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Copier vers un autre parcours")
        self.target_template_id: int | None = None
        self.roster_name: str = ""

        form = QFormLayout(self)
        self.tpl_combo = QComboBox()
        for t in target_templates:
            lv = str(t.get("level") or "").strip().upper()
            tr = str(t.get("track") or "").strip().upper()
            lab = f"{lv} {track_label(lv, tr)} — {t.get('name', '')}"
            self.tpl_combo.addItem(lab, int(t["id"]))
        form.addRow("Parcours cible :", self.tpl_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Optionnel — nom standard du parcours si vide")
        if default_name:
            self.name_edit.setText(default_name)
        self.tpl_combo.currentIndexChanged.connect(self._on_target_changed)
        form.addRow("Nom (optionnel) :", self.name_edit)
        self._on_target_changed()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_target_changed(self) -> None:
        if self.name_edit.text().strip():
            return
        tid = self.tpl_combo.currentData()
        if tid is None:
            return
        try:
            from ..services.repository import Repository

            parent = self.parent()
            repo = getattr(parent, "repo", None)
            if repo is not None:
                self.name_edit.setPlaceholderText(
                    repo.default_roster_name_for_template(int(tid))
                )
        except Exception:
            pass

    def _accept(self) -> None:
        tid = self.tpl_combo.currentData()
        if tid is None:
            return
        self.target_template_id = int(tid)
        self.roster_name = self.name_edit.text().strip()
        self.accept()
