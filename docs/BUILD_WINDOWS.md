# Créer l'exécutable Windows — MNE Grade Manager

Guide pour construire `MNEGradeManager.exe` sur **Windows 10/11** (Python 3.11+, Spyder ou PowerShell).

## 1. Prérequis

| Élément | Détail |
|--------|--------|
| **OS** | Windows 10 ou 11 (64 bits) |
| **Python** | **3.11** ou **3.12** (64 bits) — [python.org](https://www.python.org/downloads/windows/) ou Anaconda/Miniconda |
| **Espace disque** | ~2 Go libres (environnement + build PyInstaller) |
| **Réseau** | Accès Internet pour `pip install` |

Lors de l'installation Python depuis python.org, cocher **« Add python.exe to PATH »**.

### Avec Spyder (Anaconda recommandé)

1. Installer [Miniconda](https://docs.conda.io/en/latest/miniconda.html) ou Anaconda (64 bits).
2. Installer Spyder : `conda install spyder` ou via Anaconda Navigator.
3. Créer un environnement dédié (recommandé) :

```text
conda create -n mne-build python=3.11 spyder -y
conda activate mne-build
```

## 2. Décompresser les sources

1. Extraire l'archive `mne_v3_project-windows-build.zip` (par ex. `C:\Dev\mne_v3_project`).

### Spyder : pas de « Ouvrir un dossier » (comme VS Code)

C'est normal — **Spyder ouvre des fichiers ou des projets**, pas un dossier arbitraire.

**Méthode la plus simple (recommandée)** : ne pas utiliser Spyder pour le build. Utiliser **Anaconda Prompt** ou **PowerShell** (voir §3–4). Spyder sert seulement à consulter le code si besoin.

**Si votre collègue tient à Spyder** :

1. Lancer Spyder depuis l'environnement conda (`conda activate mne-build` puis `spyder`).
2. Définir le répertoire de travail : en haut à droite de la console, icône **dossier** → choisir `C:\Dev\mne_v3_project`  
   *ou* dans la console IPython :
   ```python
   %cd C:\Dev\mne_v3_project
   ```
3. (Optionnel) **File → Open file** → ouvrir `src\mne_grade_manager\main.py` pour parcourir le code.
4. (Optionnel) **Projects → New Project → Existing directory** → pointer vers `C:\Dev\mne_v3_project`.

Pour `pip` et PyInstaller, préférer quand même **Anaconda Prompt** (les scripts shell depuis la console Spyder sont plus fragiles).

## 3. Installer les dépendances

### Option A — Anaconda Prompt (recommandé)

Menu Démarrer → **Anaconda Prompt**, puis :

```text
conda activate mne-build
cd C:\Dev\mne_v3_project
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pyinstaller
```

### Option B — PowerShell

```powershell
cd C:\Dev\mne_v3_project
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pyinstaller
```

### Option C — Console Spyder (dépannage uniquement)

Après `%cd C:\Dev\mne_v3_project` :

```python
!python -m pip install --upgrade pip
!python -m pip install -e .
!python -m pip install pyinstaller
```

(Le `!` exécute une commande système depuis IPython.)

### Vérification rapide

```text
python -c "from mne_grade_manager.app import main; print('OK')"
python -m mne_grade_manager
```

La fenêtre d'accueil MNE doit s'ouvrir. Fermer l'application avant de lancer le build.

## 4. Lancer le build

### Méthode la plus simple — double-clic (cmd)

1. Ouvrir **Anaconda Prompt**, `conda activate mne-build`, puis :
   ```text
   cd C:\Dev\mne_v3_project
   scripts\build_windows.bat
   ```
2. Ou double-clic sur `scripts\build_windows.bat` **après** avoir installé les dépendances (§3).

### Méthode PowerShell

```powershell
cd C:\Dev\mne_v3_project
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build_windows.ps1
```

### Méthode manuelle

```text
cd C:\Dev\mne_v3_project
python -m PyInstaller --noconfirm scripts\MNEGradeManager.spec
```

Durée typique : **3 à 10 minutes** la première fois.

## 5. Résultat

L'exécutable se trouve dans :

```text
dist\MNEGradeManager\MNEGradeManager.exe
```

**Important :** distribuer **tout le dossier** `dist\MNEGradeManager\` (DLL Qt, bibliothèques, assets). Ne pas envoyer uniquement le fichier `.exe`.

Pour tester : double-clic sur `MNEGradeManager.exe`.

## 6. Données utilisateur

L'application **ne stocke pas** la base dans le dossier d'installation. Elle utilise :

```text
%USERPROFILE%\.mne_grade_manager\grade_manager.sqlite3
```

Pour migrer une base existante depuis un Mac : copier ce fichier `.sqlite3` au même emplacement sur le PC Windows.

## 7. Dépannage

### « Python introuvable »

- Vérifier `python --version` dans le terminal Spyder.
- Si besoin : `conda activate mne-build` avant les commandes.

### Erreur PyInstaller / module manquant

```text
python -m pip install -e . --force-reinstall
python -m pip install pyinstaller --upgrade
```

Puis relancer `.\scripts\build_windows.ps1`.

### L'exe ne démarre pas (écran noir / rien)

Lancer depuis PowerShell pour voir les erreurs :

```powershell
cd dist\MNEGradeManager
.\MNEGradeManager.exe
```

Ou rebuild sans mode fenêtré (debug) :

```text
python -m PyInstaller --noconfirm --console scripts\MNEGradeManager.spec
```

### Erreur « attempted relative import with no known parent package »

L'ancien build pointait vers `main.py` (import relatif). Mettre à jour les sources, puis :

```text
python -m pip install -e .
scripts\build_windows.bat
```

Le build utilise maintenant `scripts\pyinstaller_entry.py`.

### Antivirus

Certains antivirus bloquent les exécutables PyInstaller. Ajouter une exception sur `dist\MNEGradeManager\` si besoin.

### Spyder et chemins

- Spyder **ne propose pas** « File → Open folder » : utiliser **Anaconda Prompt** pour le build.
- Pour coder : `%cd C:\Dev\mne_v3_project` dans la console Spyder, ou **Projects → New Project → Existing directory**.

## 8. Livrable à renvoyer

Zipper le dossier complet :

```text
dist\MNEGradeManager\
```

Nom suggéré : `MNEGradeManager-0.3.0-win64.zip`.

Indiquer la version Python utilisée pour le build et la date.

## 9. Distribuer aux collègues (utilisation finale)

**Ne pas envoyer uniquement le fichier `.exe`.** Zipper et envoyer **tout le dossier** `dist\MNEGradeManager\` (DLL Qt, bibliothèques, assets).

**Données (base SQLite, photos, PDF)** : elles ne sont pas dans ce dossier. Chaque utilisateur a sa base dans :

```text
%USERPROFILE%\.mne_grade_manager\
```

Pour partager vos données entre Mac et Windows :

1. Dans l'app : **Fichier → Exporter les données (transfert)…** → archive `.zip`
2. Sur le PC du collègue : **Fichier → Importer des données…** (ou depuis l'écran d'accueil)

Pas besoin d'un « lien » séparé : l'export/import intégré suffit.

## 10. Versions des paquets (référence)

Voir `requirements.lock` à la racine du projet (PySide6 6.10, openpyxl, reportlab, etc.).
