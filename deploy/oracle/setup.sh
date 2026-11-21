#!/usr/bin/env bash
# =============================================================================
#  Instalador para una VM "Always Free" de Oracle Cloud (Ubuntu 22.04/24.04, ARM o x86).
#  Uso (dentro de la VM):
#     curl -fsSL https://raw.githubusercontent.com/<USUARIO>/<REPO>/main/deploy/oracle/setup.sh | bash -s -- <USUARIO>/<REPO>
#  Es idempotente: se puede ejecutar de nuevo para actualizar.
# =============================================================================
set -euo pipefail
REPO="${1:?Uso: setup.sh usuario/repositorio}"
OWNER="${REPO%%/*}"; NAME="${REPO##*/}"
DIR="$HOME/price-monitor"

echo "==> Sistema y Docker"
sudo apt-get update -y -q
sudo apt-get install -y -q ca-certificates curl git unattended-upgrades
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

echo "==> Zona horaria Chile"
sudo timedatectl set-timezone America/Santiago || true

echo "==> Swap de 2 GB (colchón de memoria)"
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Código"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only -q; else git clone -q "https://github.com/$REPO.git" "$DIR"; fi
cd "$DIR"
mkdir -p data

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^GH_REPO=.*|GH_REPO=$REPO|; s|^PUBLIC_SITE_URL=.*|PUBLIC_SITE_URL=https://${OWNER,,}.github.io/$NAME|" .env
  echo "   .env creado (GH_REPO=$REPO)"
fi

echo "==> Deploy key (llave SSH con acceso SOLO a $REPO, nunca sale de esta VM)"
if [ ! -f data/deploy_key ]; then
  ssh-keygen -q -t ed25519 -N "" -C "radar-precios-vm" -f data/deploy_key
fi
chmod 600 data/deploy_key

echo "==> Construyendo y levantando (reinicio automático ante caídas y al reiniciar la VM)"
sudo docker compose up -d --build
sudo docker compose ps

echo "==> Auto-actualización nocturna (04:30): git pull + rebuild"
( crontab -l 2>/dev/null | grep -v price-monitor-update ; \
  echo "30 4 * * * cd $DIR && git pull --ff-only -q && sudo docker compose up -d --build >/dev/null 2>&1 # price-monitor-update" ) | crontab -

echo
echo "================ DEPLOY KEY PÚBLICA (agrégala al repo con permiso de escritura) ================"
cat data/deploy_key.pub
echo "================================================================================================="
echo "Logs en vivo:  cd $DIR && sudo docker compose logs -f monitor"
