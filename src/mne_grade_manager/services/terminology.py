"""
Terminologie affichée — levée d’ambiguïté sur le mot « jury ».

En français universitaire, « jury » recouvre deux notions distinctes :

1. **Délibération** — la réunion où le jury se prononce (date, session S1/S2/finale,
   procès-verbal). En base : ``jury_sessions``.
2. **Membres du jury** — les personnes qui composent le jury pour une délibération donnée.
   En base : ``jury_members`` (liés à une délibération).
3. **Points de délibération** — ajustements numériques votés en délibération (UE, bloc, année).
   En base : ``jury_adjustments``. On évite « points de jury » seul dans l’UI pour ne pas
   confondre avec les membres.

Ne pas confondre avec la **session pédagogique** S1/S2 des évaluations (``assessments.session``).
"""

from __future__ import annotations

# Onglet principal
TAB_PV_DELIBERATIONS = "PV & délibérations"

# Délibération (réunion)
DELIBERATIONS = "Délibérations"
DELIBERATION = "Délibération"
DELIBERATION_NEW = "Nouvelle délibération"
DELIBERATION_SELECT = "Sélectionnez une délibération à gauche."
DELIBERATION_LABEL_PLACEHOLDER = "Ex. Délibération janvier, Bloc 1…"
DELIBERATION_LABEL_PROMPT = "Libellé de la délibération (ex. janvier, Bloc 1) :"

# Membres
JURY_MEMBERS = "Membres du jury"
JURY_MEMBER = "Membre du jury"
JURY_MEMBER_ADD = "Ajouter un membre du jury"

# Points issus de la délibération (résultats / PV)
DELIB_POINTS = "Points de délibération"
DELIB_POINTS_SHORT = "Pt délib."
DELIB_POINTS_UE = "Pt délib. (UE)"
DELIB_POINTS_BLOCK = "Pt délib. (bloc)"
DELIB_POINTS_YEAR = "Pt délib. (année)"
YEAR_WITH_DELIB_POINTS = "Année avec pt délib."

# PV
PV_TITLE = "Procès-verbal de délibération"
PV_BUTTON = "Procès-verbal"

# Messages
MSG_SELECT_DELIBERATION = "Sélectionnez une délibération à gauche."
MSG_DELETE_DELIBERATION = "Supprimer cette délibération et tous les membres du jury associés ?"
MSG_SELECT_MEMBER = "Sélectionnez un membre du jury à retirer."
