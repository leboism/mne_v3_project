"""Établissements partenaires MNE (porteur de cours)."""

from __future__ import annotations

OTHER_CARRIER_DATA = "__OTHER_CARRIER__"

MNE_CARRIER_PARTNERS: tuple[tuple[str, str], ...] = (
    ("CEA/INSTN", "CEA/INSTN"),
    ("UFR Sciences UPSay", "UFR Sciences UPSay"),
    ("ENSTA", "ENSTA"),
    ("ChimieParisTech", "ChimieParisTech"),
    ("Mines-Ponts ParisTech", "Mines-Ponts ParisTech"),
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
