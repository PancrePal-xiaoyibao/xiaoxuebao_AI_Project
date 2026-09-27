#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/opt/xiaoxuebao-hermes"
PRIMARY_REPO="https://github.com/Handsome5201314/xiaoxuebao-ability-pack"
FALLBACK_REPO="https://gitee.com/lishuaishuai1314520/xiaoxuebao-ability-pack.git"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl git gnupg nginx openssl python3 python3-venv ufw

if ! command -v docker >/dev/null 2>&1; then
  if install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc; then
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
    if ! apt-get update || ! apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
      rm -f /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker.io docker-compose-v2
    fi
  else
    apt-get install -y docker.io docker-compose-v2
  fi
fi

install -d -m 0755 /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'DOCKER'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run"
  ]
}
DOCKER
  systemctl restart docker || true
fi

install -d -m 0750 "${ROOT_DIR}"
install -d -m 0750 "${ROOT_DIR}/profiles"

if [[ ! -d "${ROOT_DIR}/ability-pack/.git" ]]; then
  if ! git clone "${PRIMARY_REPO}" "${ROOT_DIR}/ability-pack"; then
    rm -rf "${ROOT_DIR}/ability-pack"
    git clone "${FALLBACK_REPO}" "${ROOT_DIR}/ability-pack"
  fi
else
  git -C "${ROOT_DIR}/ability-pack" pull --ff-only
fi

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
cat > "${ROOT_DIR}/.env" <<'ENV'
# Fill these values on the server. Do not commit or paste them into chat.
OPENAI_API_KEY=replace-with-provider-key
OPENAI_BASE_URL=http://127.0.0.1:8649/v1
HERMES_INFERENCE_PROVIDER=openai-api
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8650
API_SERVER_KEY=replace-with-admin-only-key
API_SERVER_MODEL_NAME=小雪宝-Hermes-管理员
ENV
  chmod 600 "${ROOT_DIR}/.env"
fi

cat > /etc/nginx/sites-available/xiaoxuebao-hermes <<'NGINX'
server {
  listen 80 default_server;
  listen [::]:80 default_server;
  server_name _;

  location = /health {
    add_header Content-Type text/plain;
    return 200 "xiaoxuebao hermes ingress ok\n";
  }

  location / {
    return 404;
  }
}
NGINX

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/xiaoxuebao-hermes /etc/nginx/sites-enabled/xiaoxuebao-hermes
nginx -t
systemctl enable nginx
systemctl restart nginx

cat > "${ROOT_DIR}/docker-compose.yml" <<'YAML'
services:
  hermes:
    image: nousresearch/hermes-agent:v2026.6.5
    container_name: hermes
    restart: unless-stopped
    command: gateway run
    network_mode: host
    env_file:
      - ./.env
    environment:
      XIAOXUEBAO_ABILITY_PACK: "/ability-pack"
    volumes:
      - ./ability-pack:/ability-pack:ro
      - ./.env:/opt/data/.env
      - ./profiles:/opt/data/profiles
YAML

chmod 600 "${ROOT_DIR}/.env"
chmod 640 "${ROOT_DIR}/docker-compose.yml"

python3 "${ROOT_DIR}/ability-pack/scripts/validate_pack.py"

ufw allow 22/tcp
ufw allow 80/tcp
ufw --force enable

cd "${ROOT_DIR}"
docker compose pull
docker compose up -d
docker compose ps

echo "Bootstrap finished. Nginx public health is http://47.95.38.64/health; Hermes stays on 127.0.0.1."
