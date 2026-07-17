# Distribution Windows

## Archive sources (`mne_v3_project-windows-build.zip`)

Archive prête à décompresser sur Windows pour compiler l'application sans Git.

1. Extraire l'archive (par ex. `C:\Dev\mne_v3_project`).
2. Suivre le guide [`docs/BUILD_WINDOWS.md`](../docs/BUILD_WINDOWS.md).
3. Lancer `scripts\build_windows.bat` ou `scripts\build_windows.ps1`.

L'exécutable se trouve dans `dist\MNEGradeManager\MNEGradeManager.exe` — distribuer **tout le dossier** `dist\MNEGradeManager\`.

## Mise à jour de l'archive

Depuis la racine du dépôt (macOS/Linux) :

```bash
./scripts/package_windows_sources.sh
```

Puis committer `release/mne_v3_project-windows-build.zip` sur `main`.
