#!/usr/bin/env bash
set -e
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name MNEGradeManager src/mne_grade_manager/main.py
