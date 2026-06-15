#!/usr/bin/env bash
# Liste les tables et un extrait de schéma d’une base Microsoft Access (.mdb / .accdb).
# Prérequis : mdbtools (macOS : brew install mdbtools)
#
# Usage :
#   ./scripts/access_mdb_tools.sh "/chemin/vers/fichier.mdb"
#
# Note : les fichiers « Standard ACE DB » (Access récent) peuvent ne pas être lus par
# certaines versions de mdbtools ; dans ce cas, exporter les tables depuis Access (CSV)
# ou utiliser Access / UCanAccess sur une machine Windows.

set -euo pipefail
MDB="${1:-}"
if [[ -z "$MDB" ]]; then
  echo "Usage: $0 path/to/database.mdb" >&2
  exit 1
fi
if [[ ! -f "$MDB" ]]; then
  echo "Fichier introuvable: $MDB" >&2
  exit 1
fi
if ! command -v mdb-tables >/dev/null 2>&1; then
  echo "mdb-tables introuvable. Installez mdbtools, ex. : brew install mdbtools" >&2
  exit 1
fi

echo "=== Tables ==="
mdb-tables "$MDB" | tr ' ' '\n' | sed '/^$/d'

echo ""
echo "=== Schéma (aperçu, 400 lignes max) ==="
if mdb-schema --help 2>&1 | grep -q '\-T'; then
  # Certaines versions : mdb-schema -T table file.mdb
  mdb-schema "$MDB" 2>/dev/null | head -400 || true
else
  mdb-schema "$MDB" 2>/dev/null | head -400 || true
fi
