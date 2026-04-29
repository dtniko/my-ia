#!/usr/bin/env bash
# Installa il binario Qdrant per Raspberry Pi 5 (aarch64)
# Scarica l'ultima release ARM64 da GitHub e la posiziona in ~/.ltsia/qdrant/
set -e

QDRANT_DIR="$HOME/.ltsia/qdrant"
QDRANT_BIN="$QDRANT_DIR/qdrant"
STORAGE_DIR="$QDRANT_DIR/storage"
SNAPSHOTS_DIR="$QDRANT_DIR/snapshots"

echo "==> Qdrant installer per Raspberry Pi 5 (aarch64)"

# Recupera ultima versione disponibile
echo "==> Recupero ultima versione da GitHub..."
LATEST=$(curl -fsSL "https://api.github.com/repos/qdrant/qdrant/releases/latest" \
  | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"v\([^"]*\)".*/\1/')

if [ -z "$LATEST" ]; then
  echo "ERRORE: impossibile recuperare la versione. Controlla la connessione."
  exit 1
fi
echo "    Versione: $LATEST"

DOWNLOAD_URL="https://github.com/qdrant/qdrant/releases/download/v${LATEST}/qdrant-aarch64-unknown-linux-musl.tar.gz"

mkdir -p "$QDRANT_DIR" "$STORAGE_DIR" "$SNAPSHOTS_DIR"

echo "==> Download binario ARM64..."
TMP=$(mktemp -d)
curl -fsSL "$DOWNLOAD_URL" -o "$TMP/qdrant.tar.gz"

echo "==> Estrazione..."
tar -xzf "$TMP/qdrant.tar.gz" -C "$TMP"
rm -f "$TMP/qdrant.tar.gz"

# Il binario potrebbe trovarsi nella root o in una sottocartella
EXTRACTED=$(find "$TMP" -name "qdrant" -type f | head -1)
if [ -z "$EXTRACTED" ]; then
  echo "ERRORE: binario non trovato nell'archivio."
  ls -la "$TMP"
  exit 1
fi

mv "$EXTRACTED" "$QDRANT_BIN"
chmod +x "$QDRANT_BIN"
rm -rf "$TMP"

echo "==> Verifica..."
"$QDRANT_BIN" --version 2>/dev/null || true
echo ""
echo "✓ Qdrant $LATEST installato in $QDRANT_BIN"
echo ""
echo "Avvio automatico: ./pi5_ia lo avvierà automaticamente se non già in esecuzione."
echo "Avvio manuale:    ~/.ltsia/qdrant/qdrant"
