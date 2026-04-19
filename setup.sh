#!/usr/bin/env bash
# Setup ambiente Python per LTSIA-py
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creazione virtualenv..."
python3 -m venv .venv

echo "==> Attivazione virtualenv..."
source .venv/bin/activate

echo "==> Installazione dipendenze..."
pip install --upgrade pip -q
pip install -r requirements.txt

echo ""
echo "✓ Setup completato!"
echo ""
echo "Per avviare LTSIA-py:"
echo "  source .venv/bin/activate"
echo "  python main.py"
echo ""
echo "Oppure direttamente:"
echo "  .venv/bin/python main.py"
