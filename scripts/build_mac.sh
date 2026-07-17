#!/usr/bin/env bash
set -e
python -m pip install pyinstaller
pyinstaller --noconfirm scripts/MNEGradeManager.spec
