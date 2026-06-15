# MNE Grade Manager V3

Base de projet Python + PySide6 + SQLite pour gérer :
- étudiants
- cours
- modalités d'évaluation
- maquettes configurables
- notes
- calculs de moyenne par cours et synthèse par étudiant

## Lancement rapide

### Avec Rye
```bash
rye sync
rye run mne-grade-manager
```

### Sans Rye
```bash
pip install -e .
python -m mne_grade_manager
```

## Fonctions déjà présentes
- base SQLite auto-créée dans le dossier utilisateur
- onglets Students / Courses / Templates / Grades / Results
- création des étudiants
- création des cours
- création des évaluations d'un cours
- création des maquettes
- ajout de cours à une maquette
- inscription des étudiants dans une maquette
- saisie de notes par étudiant / cours / évaluation
- calcul de moyenne pondérée par cours
- synthèse par étudiant
- données de démonstration

## À développer ensuite
- ABI / ABJ / DEF
- session 2 avancée
- règles de compensation
- ECTS / validation jury
- import Excel / export PDF relevés
