#!/usr/bin/env bash
# Installa pi5_ia come servizio systemd e lo abilita all'avvio.
set -e

SERVICE_NAME="pi5_ia"
SERVICE_FILE="$(cd "$(dirname "$0")" && pwd)/pi5_ia.service"
SYSTEMD_DIR="/etc/systemd/system"
LOG_DIR="/root/.ltsia/logs"

echo "Creazione directory log: $LOG_DIR"
mkdir -p "$LOG_DIR"

echo "Copia $SERVICE_FILE → $SYSTEMD_DIR/$SERVICE_NAME.service"
cp "$SERVICE_FILE" "$SYSTEMD_DIR/$SERVICE_NAME.service"

echo "Ricarica daemon systemd"
systemctl daemon-reload

echo "Abilita servizio all'avvio"
systemctl enable "$SERVICE_NAME"

echo ""
echo "Fatto. Comandi utili:"
echo "  systemctl start $SERVICE_NAME        # avvia ora"
echo "  systemctl stop $SERVICE_NAME         # ferma"
echo "  systemctl status $SERVICE_NAME       # stato"
echo "  journalctl -u $SERVICE_NAME -f       # log in tempo reale (journald)"
echo "  tail -f $LOG_DIR/service.log         # log su file"
