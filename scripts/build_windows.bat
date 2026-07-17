@echo off
REM Double-clic ou : scripts\build_windows.bat depuis Anaconda Prompt / cmd
setlocal
cd /d "%~dp0.."
echo Racine projet : %CD%
python --version >nul 2>&1
if errorlevel 1 (
    echo ERREUR : Python introuvable. Ouvrez "Anaconda Prompt" ou installez Python 3.11+.
    pause
    exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pyinstaller
python -c "from mne_grade_manager.app import main; print('Import OK')"
if errorlevel 1 (
    echo ERREUR : import application echoue.
    pause
    exit /b 1
)
python -m PyInstaller --noconfirm scripts\MNEGradeManager.spec
if not exist "dist\MNEGradeManager\MNEGradeManager.exe" (
    echo ERREUR : MNEGradeManager.exe introuvable.
    pause
    exit /b 1
)
echo.
echo SUCCES : dist\MNEGradeManager\MNEGradeManager.exe
echo Zipper tout le dossier dist\MNEGradeManager\ pour distribution.
pause
