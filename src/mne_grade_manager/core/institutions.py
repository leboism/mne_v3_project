"""Établissements partenaires MNE (porteur de cours)."""

from __future__ import annotations

OTHER_CARRIER_DATA = "__OTHER_CARRIER__"

MNE_CARRIER_PARTNERS: tuple[tuple[str, str], ...] = (
    ("Université Paris-Saclay", "Université Paris-Saclay"),
    ("ENSTA Paris", "ENSTA Paris"),
    ("Chimie ParisTech-PSL", "Chimie ParisTech-PSL"),
    ("CentraleSupélec", "CentraleSupélec"),
    ("CEA / INSTN", "CEA / INSTN"),
    ("École des Ponts ParisTech", "École des Ponts ParisTech"),
    ("Institut Polytechnique de Paris", "Institut Polytechnique de Paris"),
    ("Autre", OTHER_CARRIER_DATA),
)

PEDAGOGICAL_CONTRACT_CATEGORY = "pedagogical_contract"

STUDENT_ATTACHMENT_CATEGORIES: tuple[tuple[str, str], ...] = (
    (PEDAGOGICAL_CONTRACT_CATEGORY, "Contrat pédagogique signé"),
    ("admission_dossier", "Dossier de candidature"),
    ("absence_justification", "Justificatif d'absence"),
    ("other", "Autre document"),
)

INTERNSHIP_STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "— Non renseigné —"),
    ("searching", "En recherche de stage"),
    ("found", "Stage trouvé"),
    ("convention_pending", "Convention en cours"),
    ("convention_signed", "Convention signée"),
)
