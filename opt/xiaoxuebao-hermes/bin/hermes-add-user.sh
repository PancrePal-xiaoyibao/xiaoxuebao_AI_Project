#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/opt/xiaoxuebao-hermes"
USER_ID="${1:-}"
PORT="${2:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ -z "${USER_ID}" || -z "${PORT}" ]]; then
  echo "Usage: $0 <user_id> <api_port_8651_or_higher>" >&2
  exit 2
fi

if [[ ! "${USER_ID}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "user_id may only contain letters, numbers, underscore, and hyphen." >&2
  exit 2
fi

if [[ "${PORT}" -lt 8651 ]]; then
  echo "Use 8651 or higher for trial users. Port 8650 is reserved for admin." >&2
  exit 2
fi

cd "${ROOT_DIR}"

docker exec hermes hermes profile create "${USER_ID}" || true

PROFILE_ENV="${ROOT_DIR}/profiles/${USER_ID}/.env"
install -d -m 0750 "${ROOT_DIR}/profiles/${USER_ID}"
API_KEY="$(openssl rand -hex 32)"

cat > "${PROFILE_ENV}" <<ENV
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=${PORT}
API_SERVER_KEY=${API_KEY}
API_SERVER_MODEL_NAME=小雪宝-Hermes-${USER_ID}
OPENAI_API_KEY=\${OPENAI_API_KEY}
XIAOXUEBAO_ABILITY_PACK=/ability-pack
ENV
chmod 600 "${PROFILE_ENV}"

docker exec hermes hermes -p "${USER_ID}" gateway start

cat <<OUT
Created Hermes profile: ${USER_ID}
Open WebUI admin connection URL: http://127.0.0.1:${PORT}/v1
API key was written to ${PROFILE_ENV}
Assign this model only to the matching Open WebUI user in Admin Settings.
OUT
