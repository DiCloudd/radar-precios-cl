#!/usr/bin/env bash
# =============================================================================
#  Instalador para una VM "Always Free" de Oracle Cloud (Ubuntu 22.04/24.04, ARM o x86).
#  Uso (dentro de la VM):
#     curl -fsSL https://raw.githubusercontent.com/<USUARIO>/<REPO>/main/deploy/oracle/setup.sh | bash -s -- <USUARIO>/<REPO>
# =============================================================================
set -euo pipefail
REPO="${1:?Uso: setup.sh usuario/repositorio}"
DIR="$HOME/price-monitor"

echo "==> Actualizando sistema e instalando Docker"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git unattended-upgrades
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi
sudo systemctl enable --now docker

echo "==> Zona horaria Chile"
sudo timedatectl set-timezone America/Santiago || true

echo "==> Swap de 2 GB (por si la VM es la micro de 1 GB)"
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Descargando el código"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "https://github.com/$REPO.git" "$DIR"; fi
cd "$DIR"
mkdir -p data

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "!!! Edita $DIR/.env (TELEGRAM_*, GH_TOKEN, GH_REPO, PUBLIC_SITE_URL) y vuelve a ejecutar este script."
  exit 0
fi

echo "==> Construyendo y levantando (se reinicia solo ante caídas y al reiniciar la VM)"
sudo docker compose up -d --build
sudo docker compose ps

echo "==> Actualización automática del código cada noche (04:30)"
( crontab -l 2>/dev/null | grep -v price-monitor-update ; \
  echo "30 4 * * * cd $DIR && git pull --ff-only -q && sudo docker compose up -d --build >/dev/null 2>&1 # price-monitor-update" ) | crontab -

echo
echo "Listo. Logs en vivo:  sudo docker compose -f $DIR/docker-compose.yml logs -f monitor"
