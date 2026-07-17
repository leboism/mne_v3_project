"""Photo et documents PDF pour la fiche étudiant."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.institutions import PEDAGOGICAL_CONTRACT_CATEGORY, STUDENT_ATTACHMENT_CATEGORIES
from ..services.attachments import abs_path_from_stored
from .widgets import refresh_students_tab_ancestor

if TYPE_CHECKING:
    from ..services.repository import Repository


class StudentFilesPanel(QWidget):
    def __init__(self, parent=None, *, repo: Repository | None = None, student_id: int | None = None):
        super().__init__(parent)
        self.repo = repo
        self.student_id = student_id
        self._pending_photo: Path | None = None
        self._pending_attachments: list[tuple[str, Path, str]] = []

        root = QVBoxLayout(self)

        photo_box = QGroupBox("Photo (trombinoscope)")
        pb = QVBoxLayout(photo_box)
        self.photo_label = QLabel("Aucune photo")
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setMinimumHeight(140)
        self.photo_label.setStyleSheet("border: 1px solid palette(mid);")
        pb.addWidget(self.photo_label)
        prow = QHBoxLayout()
        self.photo_btn = QPushButton("Choisir une image…")
        self.photo_btn.clicked.connect(self._pick_photo)
        self.photo_clear_btn = QPushButton("Retirer")
        self.photo_clear_btn.clicked.connect(self._clear_photo)
        prow.addWidget(self.photo_btn)
        prow.addWidget(self.photo_clear_btn)
        prow.addStretch()
        pb.addLayout(prow)
        root.addWidget(photo_box)

        docs_box = QGroupBox("Contrat pédagogique & documents")
        db = QVBoxLayout(docs_box)
        self.contract_alarm = QLabel()
        self.contract_alarm.setWordWrap(True)
        self.contract_alarm.hide()
        db.addWidget(self.contract_alarm)
        self.paper_contract_cb = QCheckBox(
            "Version papier archivée (compatible avec un PDF — les deux peuvent coexister)"
        )
        self.paper_contract_cb.toggled.connect(self._on_paper_contract_toggled)
        db.addWidget(self.paper_contract_cb)
        db.addWidget(QLabel("Documents PDF (optionnel si version papier) :"))
        self.docs_list = QListWidget()
        db.addWidget(self.docs_list)
        drow = QHBoxLayout()
        self.doc_category = QComboBox()
        for key, label in STUDENT_ATTACHMENT_CATEGORIES:
            self.doc_category.addItem(label, key)
        idx_contract = self.doc_category.findData(PEDAGOGICAL_CONTRACT_CATEGORY)
        if idx_contract >= 0:
            self.doc_category.setCurrentIndex(idx_contract)
        self.doc_add_btn = QPushButton("Ajouter un PDF…")
        self.doc_add_btn.clicked.connect(self._add_document)
        self.doc_open_btn = QPushButton("Ouvrir")
        self.doc_open_btn.clicked.connect(self._open_document)
        self.doc_del_btn = QPushButton("Supprimer")
        self.doc_del_btn.clicked.connect(self._delete_document)
        drow.addWidget(self.doc_category, 1)
        drow.addWidget(self.doc_add_btn)
        drow.addWidget(self.doc_open_btn)
        drow.addWidget(self.doc_del_btn)
        db.addLayout(drow)
        root.addWidget(docs_box, 1)

        if student_id is not None and repo is not None:
            self._load_existing()
        else:
            self._update_contract_alarm()

    def set_student_context(self, repo: Repository, student_id: int) -> None:
        self.repo = repo
        self.student_id = int(student_id)
        self._pending_photo = None
        self._pending_attachments.clear()
        self._load_existing()

    def _load_existing(self) -> None:
        if self.repo is None or self.student_id is None:
            return
        st = self.repo.get_student(int(self.student_id)) or {}
        self._show_photo_path(str(st.get("photo_path") or ""))
        self.paper_contract_cb.blockSignals(True)
        self.paper_contract_cb.setChecked(bool(int(st.get("pedagogical_contract_paper") or 0)))
        self.paper_contract_cb.blockSignals(False)
        self.docs_list.clear()
        for att in self.repo.list_student_attachments(int(self.student_id)):
            self._add_doc_item(att)
        self._update_contract_alarm()

    def pedagogical_contract_paper(self) -> bool:
        return self.paper_contract_cb.isChecked()

    def _on_paper_contract_toggled(self, checked: bool) -> None:
        self._update_contract_alarm()
        if self.repo is not None and self.student_id is not None:
            try:
                self.repo.set_pedagogical_contract_paper(int(self.student_id), checked)
                refresh_students_tab_ancestor(self)
            except Exception as exc:
                QMessageBox.critical(self, "Contrat pédagogique", str(exc))

    def _has_pedagogical_contract(self) -> bool:
        if self.paper_contract_cb.isChecked():
            return True
        if self.repo is None or self.student_id is None:
            for cat, _src, _name in self._pending_attachments:
                if cat == PEDAGOGICAL_CONTRACT_CATEGORY:
                    return True
            return False
        if self.repo.has_pedagogical_contract(int(self.student_id)):
            return True
        return any(cat == PEDAGOGICAL_CONTRACT_CATEGORY for cat, _s, _n in self._pending_attachments)

    def _update_contract_alarm(self) -> None:
        if self._has_pedagogical_contract():
            self.contract_alarm.hide()
            return
        self.contract_alarm.setText(
            "⚠ Contrat pédagogique signé manquant — document obligatoire. "
            "Cochez « Version papier archivée » si le contrat est en dossier physique, "
            "ou ajoutez le PDF via la catégorie « Contrat pédagogique signé »."
        )
        self.contract_alarm.setStyleSheet(
            "background-color: #ffebee; color: #b71c1c; padding: 8px; "
            "border: 1px solid #ef9a9a; border-radius: 4px; font-weight: bold;"
        )
        self.contract_alarm.show()

    def _show_photo_path(self, stored: str) -> None:
        if not stored:
            self.photo_label.setText("Aucune photo")
            self.photo_label.setPixmap(QPixmap())
            return
        p = abs_path_from_stored(stored)
        if p.is_file():
            pix = QPixmap(str(p))
            if not pix.isNull():
                self.photo_label.setPixmap(
                    pix.scaled(
                        200,
                        200,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.photo_label.setText("")
                return
        self.photo_label.setText("Photo introuvable")

    def _pick_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Photo étudiant",
            str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.gif)",
        )
        if not path:
            return
        src = Path(path)
        if self.repo is not None and self.student_id is not None:
            try:
                self.repo.import_student_photo(int(self.student_id), src)
                st = self.repo.get_student(int(self.student_id)) or {}
                self._show_photo_path(str(st.get("photo_path") or ""))
            except Exception as exc:
                QMessageBox.critical(self, "Photo", str(exc))
        else:
            self._pending_photo = src
            pix = QPixmap(str(src))
            if not pix.isNull():
                self.photo_label.setPixmap(
                    pix.scaled(
                        200,
                        200,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.photo_label.setText("")

    def _clear_photo(self) -> None:
        if self.repo is not None and self.student_id is not None:
            try:
                self.repo.clear_student_photo(int(self.student_id))
            except Exception as exc:
                QMessageBox.critical(self, "Photo", str(exc))
                return
        self._pending_photo = None
        self.photo_label.setText("Aucune photo")
        self.photo_label.setPixmap(QPixmap())

    def _add_doc_item(self, att: dict[str, Any]) -> None:
        cat = str(att.get("category") or "")
        cat_lab = next((l for k, l in STUDENT_ATTACHMENT_CATEGORIES if k == cat), cat)
        name = str(
            att.get("original_filename") or att.get("label") or Path(str(att.get("file_path") or "")).name
        )
        it = QListWidgetItem(f"{cat_lab} — {name}")
        it.setData(Qt.ItemDataRole.UserRole, {"id": att.get("id"), "path": att.get("file_path")})
        self.docs_list.addItem(it)

    def _add_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Document PDF", str(Path.home()), "PDF (*.pdf)")
        if not path:
            return
        cat = str(self.doc_category.currentData() or "other")
        src = Path(path)
        if self.repo is not None and self.student_id is not None:
            try:
                aid = self.repo.add_student_attachment(int(self.student_id), cat, src)
                att = next(
                    (a for a in self.repo.list_student_attachments(int(self.student_id)) if int(a["id"]) == aid),
                    None,
                )
                if att:
                    self._add_doc_item(att)
                self._update_contract_alarm()
            except Exception as exc:
                QMessageBox.critical(self, "Document", str(exc))
        else:
            self._pending_attachments.append((cat, src, src.name))
            cat_lab = self.doc_category.currentText()
            it = QListWidgetItem(f"{cat_lab} — {src.name} (en attente)")
            it.setData(Qt.ItemDataRole.UserRole, {"pending": str(src)})
            self.docs_list.addItem(it)
            self._update_contract_alarm()

    def _open_document(self) -> None:
        it = self.docs_list.currentItem()
        if it is None:
            return
        data = it.data(Qt.ItemDataRole.UserRole) or {}
        if data.get("pending"):
            p = Path(str(data["pending"]))
        else:
            p = abs_path_from_stored(str(data.get("path") or ""))
        if not p.is_file():
            QMessageBox.warning(self, "Document", "Fichier introuvable.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["start", "", str(p)], shell=True, check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
        except Exception as exc:
            QMessageBox.warning(self, "Document", str(exc))

    def _delete_document(self) -> None:
        it = self.docs_list.currentItem()
        if it is None:
            return
        data = it.data(Qt.ItemDataRole.UserRole) or {}
        if data.get("pending"):
            pending = str(data["pending"])
            self._pending_attachments = [x for x in self._pending_attachments if str(x[1]) != pending]
            self.docs_list.takeItem(self.docs_list.row(it))
            self._update_contract_alarm()
            return
        aid = data.get("id")
        if aid is None or self.repo is None:
            return
        if QMessageBox.question(self, "Confirmer", "Supprimer ce document ?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repo.delete_student_attachment(int(aid))
            self.docs_list.takeItem(self.docs_list.row(it))
            self._update_contract_alarm()
        except Exception as exc:
            QMessageBox.critical(self, "Document", str(exc))

    def apply_pending_uploads(self, repo: Repository, student_id: int) -> None:
        sid = int(student_id)
        if self._pending_photo is not None:
            repo.import_student_photo(sid, self._pending_photo)
            self._pending_photo = None
        for cat, src, _label in self._pending_attachments:
            repo.add_student_attachment(sid, cat, src)
        self._pending_attachments.clear()
        repo.set_pedagogical_contract_paper(sid, self.pedagogical_contract_paper())
        self.set_student_context(repo, sid)
