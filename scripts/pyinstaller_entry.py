"""
Point d'entrée PyInstaller.

Ne pas utiliser ``main.py`` du paquet (imports relatifs) : PyInstaller l'exécute
comme script isolé, ce qui provoque « attempted relative import with no known parent package ».
"""

from __future__ import annotations

import sys

from mne_grade_manager.app import main

if __name__ == "__main__":
    raise SystemExit(main())
