#!/usr/bin/env bash
# Runs ON the Raspberry Pi (deploy.sh calls it over ssh). Idempotent: run it again to update.
# Installs Node, the oso user, /opt/oso, a self-signed certificate for <hostname>.local and the systemd service.
set -euo pipefail
SRC="${1:-$HOME/oso-deploy}"

need_node() { ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; }
if need_node; then
  sudo apt-get update -y
  sudo apt-get install -y nodejs
fi
command -v openssl >/dev/null || sudo apt-get install -y openssl
command -v rsync >/dev/null || sudo apt-get install -y rsync
command -v avahi-daemon >/dev/null || sudo apt-get install -y avahi-daemon # <hostname>.local on the LAN

id oso >/dev/null 2>&1 || sudo useradd --system --home /var/lib/oso --shell /usr/sbin/nologin oso
sudo mkdir -p /opt/oso /var/lib/oso /etc/oso
sudo rsync -a --delete "$SRC/app/" /opt/oso/app/
sudo chown -R oso:oso /var/lib/oso
sudo chmod 700 /var/lib/oso # holds the Fitbit refresh token

if [ ! -f /etc/oso/cert.pem ]; then
  host="$(hostname).local"
  ip="$(hostname -I | awk '{print $1}')"
  sudo openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=$host" \
    -addext "subjectAltName=DNS:$host,DNS:localhost,IP:${ip:-127.0.0.1}" \
    -keyout /etc/oso/key.pem -out /etc/oso/cert.pem
  sudo chown root:oso /etc/oso/key.pem && sudo chmod 640 /etc/oso/key.pem
  echo "made a self-signed certificate for $host (${ip:-no ip}); trust it on your devices: deploy/pi/README.md"
fi

sudo cp "$SRC/oso.service" /etc/systemd/system/oso.service
sudo systemctl daemon-reload
sudo systemctl enable --now oso
sudo systemctl restart oso
sleep 1
curl -fsS http://localhost:8080/api/health && echo
echo "oso is up: https://$(hostname).local:8443 (browsers)  http://$(hostname).local:8080 (iPhone/Watch apps)"
