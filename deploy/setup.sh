#!/usr/bin/env bash
# Instala eSoccer Radar como servicio 24/7 en Ubuntu (Oracle o Google Cloud).
# Genera el servicio systemd con el usuario y ruta reales.
# Correr DENTRO del servidor, desde la carpeta del proyecto:
#   bash deploy/setup.sh
set -e

echo "==> Instalando Python..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git

cd "$(dirname "$0")/.."
PROJ="$(pwd)"; RUN_USER="$(whoami)"; PY="$PROJ/.venv/bin/python"
echo "==> Proyecto: $PROJ · usuario: $RUN_USER"

if [ ! -f .env ]; then
  echo "ERROR: falta .env. Crea con: cp .env.example .env && nano .env"; exit 1
fi

python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

echo "==> Creando servicio systemd 'esoccer-radar'..."
sudo tee /etc/systemd/system/esoccer-radar.service >/dev/null <<EOF
[Unit]
Description=eSoccer Radar 24/7
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJ
ExecStart=$PY run_esoccer.py --no-startup
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable esoccer-radar
sudo systemctl restart esoccer-radar
sleep 3
sudo systemctl status esoccer-radar --no-pager || true
echo ""
echo "Listo. Logs en vivo: journalctl -u esoccer-radar -f"
