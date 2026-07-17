# Build Windows — MNE Grade Manager
# Exécuter dans PowerShell, à la racine du projet :
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\scripts\build_windows.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "==> Racine projet : $ProjectRoot"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python introuvable. Installez Python 3.11+ (ou activez votre environnement conda)."
}

$pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "==> Python $pyVersion"

python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pyinstaller

Write-Host "==> Test import application..."
python -c "from mne_grade_manager.app import main; print('OK')"

Write-Host "==> PyInstaller..."
python -m PyInstaller --noconfirm (Join-Path $PSScriptRoot "MNEGradeManager.spec")

$dist = Join-Path $ProjectRoot "dist\MNEGradeManager"
if (-not (Test-Path (Join-Path $dist "MNEGradeManager.exe"))) {
    throw "Échec : MNEGradeManager.exe introuvable dans dist\MNEGradeManager"
}

Write-Host ""
Write-Host "SUCCÈS — exécutable : $dist\MNEGradeManager.exe"
Write-Host "Distribuer tout le dossier dist\MNEGradeManager\ (pas seulement le .exe)."
