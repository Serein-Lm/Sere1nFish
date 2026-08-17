#!/usr/bin/env bash
set -euo pipefail

GPU_HOST=""
GPU_PORT="22"
GPU_USER="root"
GPU_KEY="/root/.ssh/sere1nfish_gpu_ed25519"
PUBLIC_HOST=""
PUBLIC_IP=""
PROXY_TARGET="${GPU_BUILD_PROXY_TARGET:-}"
TUNNEL_HOST="${DEEPFAKE_TUNNEL_HOST:-172.18.0.1}"
REMOTE_ROOT="/opt/sere1nfish/deepfake"
RESTORE_MODELS=1
SKIP_BOOTSTRAP=0
TEMP_TUNNEL_PID=""
CUTOVER_STARTED=0
DEPLOYMENT_COMPLETE=0
CONFIG_UPDATED=0

usage() {
  cat >&2 <<'EOF'
Usage: deploy-from-main.sh --host HOST [options]

Options:
  --port PORT                 SSH port (default: 22)
  --user USER                 SSH user (default: root)
  --identity PATH             SSH private key
  --public-ip IPV4            Public WebRTC address; defaults to --host for IPv4
  --public-host HOSTNAME      Public HTTPS hostname; defaults to IP.sslip.io
  --proxy-target HOST:PORT    Optional HTTP proxy reachable from the main server
  --tunnel-host IPV4          Docker bridge bind address (default: 172.18.0.1)
  --remote-root PATH          Persistent GPU deployment root
  --skip-model-restore        Download public model files on first startup
  --skip-bootstrap            Require Docker and NVIDIA runtime to be preinstalled
EOF
}

while (($#)); do
  case "$1" in
    --host) GPU_HOST="${2:-}"; shift 2 ;;
    --port) GPU_PORT="${2:-}"; shift 2 ;;
    --user) GPU_USER="${2:-}"; shift 2 ;;
    --identity) GPU_KEY="${2:-}"; shift 2 ;;
    --public-ip) PUBLIC_IP="${2:-}"; shift 2 ;;
    --public-host) PUBLIC_HOST="${2:-}"; shift 2 ;;
    --proxy-target) PROXY_TARGET="${2:-}"; shift 2 ;;
    --tunnel-host) TUNNEL_HOST="${2:-}"; shift 2 ;;
    --remote-root) REMOTE_ROOT="${2:-}"; shift 2 ;;
    --skip-model-restore) RESTORE_MODELS=0; shift ;;
    --skip-bootstrap) SKIP_BOOTSTRAP=1; shift ;;
    *) usage; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "deploy-from-main.sh must run as root on the Sere1nFish server" >&2
  exit 1
fi
if [[ ! "${GPU_HOST}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  usage
  exit 2
fi
if [[ ! "${GPU_PORT}" =~ ^[0-9]{1,5}$ ]] || ((GPU_PORT < 1 || GPU_PORT > 65535)); then
  echo "Invalid SSH port" >&2
  exit 2
fi
if [[ ! "${GPU_USER}" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]]; then
  echo "Invalid SSH user" >&2
  exit 2
fi
if [[ ! "${GPU_KEY}" =~ ^/[A-Za-z0-9._/-]+$ ]] || [[ ! -f "${GPU_KEY}" ]]; then
  echo "SSH private key does not exist: ${GPU_KEY}" >&2
  exit 1
fi
if [[ -n "${PROXY_TARGET}" && ! "${PROXY_TARGET}" =~ ^[A-Za-z0-9._-]+:[0-9]{1,5}$ ]]; then
  echo "Invalid proxy target; expected HOST:PORT" >&2
  exit 2
fi
if [[ -n "${PROXY_TARGET}" ]]; then
  proxy_port="${PROXY_TARGET##*:}"
  if ((proxy_port < 1 || proxy_port > 65535)); then
    echo "Invalid proxy target port" >&2
    exit 2
  fi
fi

if [[ -z "${PUBLIC_IP}" && "${GPU_HOST}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  PUBLIC_IP="${GPU_HOST}"
fi
if [[ ! "${PUBLIC_IP}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "--public-ip is required when --host is not an IPv4 address" >&2
  exit 2
fi
python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "${PUBLIC_IP}"
if [[ -z "${PUBLIC_HOST}" ]]; then
  PUBLIC_HOST="${PUBLIC_IP//./-}.sslip.io"
fi
if [[ ! "${PUBLIC_HOST}" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Invalid public hostname" >&2
  exit 2
fi
if [[ ! "${TUNNEL_HOST}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Invalid tunnel host" >&2
  exit 2
fi
python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "${TUNNEL_HOST}"
if [[ ! "${REMOTE_ROOT}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "Invalid remote root" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVER_DIR="$(cd "${GATEWAY_DIR}/.." && pwd)"
REPO_DIR="$(cd "${SERVER_DIR}/.." && pwd)"
MANIFEST_HOST="${GATEWAY_DIR}/model-bundle.manifest.json"
MANIFEST_CONTAINER="/app/deepfake_gateway/model-bundle.manifest.json"
RELEASE_ID="$(git -C "${REPO_DIR}" rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)"
REMOTE_RELEASE="${REMOTE_ROOT}/releases/${RELEASE_ID}"
SAFE_HOST="${GPU_HOST//[^A-Za-z0-9._-]/_}"
STAGING_HOST="${SERVER_DIR}/data/gpu-node-deploy/${SAFE_HOST}/${RELEASE_ID}"
STAGING_CONTAINER="/app/data/gpu-node-deploy/${SAFE_HOST}/${RELEASE_ID}"
BUNDLE_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle_version"])' "${MANIFEST_HOST}")"
BUNDLE_HOST="${SERVER_DIR}/data/gpu-node-bundles/${BUNDLE_VERSION}"
BUNDLE_CONTAINER="/app/data/gpu-node-bundles/${BUNDLE_VERSION}"

install -d -m 0700 "${STAGING_HOST}"
TUNNEL_UNITS=(
  sere1nfish-gpu-tunnel.service
  sere1nfish-media-output-relay.service
  sere1nfish-gpu-proxy-tunnel.service
)
declare -A TUNNEL_WAS_ENABLED=()
declare -A TUNNEL_WAS_ACTIVE=()
declare -A TUNNEL_HAD_FILE=()
for unit in "${TUNNEL_UNITS[@]}"; do
  if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    TUNNEL_WAS_ENABLED["${unit}"]=1
  else
    TUNNEL_WAS_ENABLED["${unit}"]=0
  fi
  if systemctl is-active --quiet "${unit}" 2>/dev/null; then
    TUNNEL_WAS_ACTIVE["${unit}"]=1
  else
    TUNNEL_WAS_ACTIVE["${unit}"]=0
  fi
  if [[ -f "/etc/systemd/system/${unit}" ]]; then
    TUNNEL_HAD_FILE["${unit}"]=1
    cp -a "/etc/systemd/system/${unit}" "${STAGING_HOST}/${unit}.previous"
  else
    TUNNEL_HAD_FILE["${unit}"]=0
  fi
done

cleanup() {
  exit_code=$?
  trap - EXIT
  set +e
  if [[ -n "${TEMP_TUNNEL_PID}" ]]; then
    kill "${TEMP_TUNNEL_PID}" 2>/dev/null
    wait "${TEMP_TUNNEL_PID}" 2>/dev/null
  fi
  if ((CUTOVER_STARTED && !DEPLOYMENT_COMPLETE)); then
    rollback_ready=1
    if ((CONFIG_UPDATED)); then
      if docker compose version >/dev/null 2>&1; then
        rollback_compose=(docker compose)
      else
        rollback_compose=(docker-compose)
      fi
      if ! "${rollback_compose[@]}" -f "${REPO_DIR}/docker-compose.yml" exec -T backend \
        python -m scripts.manage_gpu_node config-restore \
          --input "${STAGING_CONTAINER}/deepfake-config.previous.json"; then
        rollback_ready=0
        echo "Unable to restore the previous Deepfake config; preserving the new tunnel to avoid a credential mismatch" >&2
      fi
    fi
    if ((rollback_ready)); then
      systemctl disable --now "${TUNNEL_UNITS[@]}" >/dev/null 2>&1
      for unit in "${TUNNEL_UNITS[@]}"; do
        if [[ "${TUNNEL_HAD_FILE[${unit}]}" == "1" ]]; then
          install -m 0644 "${STAGING_HOST}/${unit}.previous" "/etc/systemd/system/${unit}"
        else
          rm -f "/etc/systemd/system/${unit}"
        fi
      done
      systemctl daemon-reload
      for unit in "${TUNNEL_UNITS[@]}"; do
        if [[ "${TUNNEL_WAS_ENABLED[${unit}]}" == "1" ]]; then
          systemctl enable "${unit}" >/dev/null 2>&1
        fi
        if [[ "${TUNNEL_WAS_ACTIVE[${unit}]}" == "1" ]]; then
          systemctl start "${unit}" >/dev/null 2>&1
        fi
      done
    fi
  fi
  rm -rf "${STAGING_HOST}"
  exit "${exit_code}"
}
trap cleanup EXIT

SSH_TARGET="${GPU_USER}@${GPU_HOST}"
SSH_OPTS=(
  -i "${GPU_KEY}"
  -p "${GPU_PORT}"
  -o "BatchMode=yes"
  -o "ConnectTimeout=15"
  -o "StrictHostKeyChecking=yes"
)
SCP_OPTS=(
  -i "${GPU_KEY}"
  -P "${GPU_PORT}"
  -o "BatchMode=yes"
  -o "ConnectTimeout=15"
  -o "StrictHostKeyChecking=yes"
)

remote_exec() {
  local remote_command
  printf -v remote_command '%q ' "$@"
  # Arguments are escaped individually above before the remote shell receives them.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "${remote_command}"
}

# Pin a new host key once; subsequent deployment and systemd connections require it.
ssh -i "${GPU_KEY}" -p "${GPU_PORT}" -o BatchMode=yes \
  -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "${SSH_TARGET}" true

GPU_COMPUTE_CAPABILITY="$(ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" \
  "nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n 1 | tr -d ' '")"
if [[ ! "${GPU_COMPUTE_CAPABILITY}" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "Unable to detect GPU compute capability; verify the NVIDIA driver" >&2
  exit 1
fi

openssl rand -hex 48 >"${STAGING_HOST}/api-token"
openssl rand -hex 32 >"${STAGING_HOST}/meanvc-token"
openssl genrsa -out "${STAGING_HOST}/ca.key" 3072 >/dev/null 2>&1
openssl req -x509 -new -sha256 -days 3650 \
  -key "${STAGING_HOST}/ca.key" \
  -subj "/CN=Sere1nFish GPU Private CA" \
  -out "${STAGING_HOST}/ca.crt"
openssl genrsa -out "${STAGING_HOST}/tls.key" 3072 >/dev/null 2>&1
openssl req -new -sha256 \
  -key "${STAGING_HOST}/tls.key" \
  -subj "/CN=${TUNNEL_HOST}" \
  -out "${STAGING_HOST}/tls.csr"
cat >"${STAGING_HOST}/tls.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:${TUNNEL_HOST},IP:${PUBLIC_IP},DNS:${PUBLIC_HOST}
EOF
openssl x509 -req -sha256 -days 825 \
  -in "${STAGING_HOST}/tls.csr" \
  -CA "${STAGING_HOST}/ca.crt" \
  -CAkey "${STAGING_HOST}/ca.key" \
  -CAcreateserial \
  -extfile "${STAGING_HOST}/tls.ext" \
  -out "${STAGING_HOST}/tls.crt" >/dev/null 2>&1
chmod 0600 "${STAGING_HOST}/api-token" "${STAGING_HOST}/meanvc-token" \
  "${STAGING_HOST}/ca.key" "${STAGING_HOST}/tls.key"

proxy_forward=""
proxy_url=""
if [[ -n "${PROXY_TARGET}" ]]; then
  proxy_forward="-R 127.0.0.1:17890:${PROXY_TARGET}"
  proxy_url="http://127.0.0.1:17890"
fi

sed \
  -e "s|__GPU_SSH_PORT__|${GPU_PORT}|g" \
  -e "s|__GPU_SSH_KEY__|${GPU_KEY}|g" \
  -e "s|__LOCAL_TUNNEL_HOST__|${TUNNEL_HOST}|g" \
  -e "s|__PROXY_FORWARD__|${proxy_forward}|g" \
  -e "s|__GPU_SSH_USER__|${GPU_USER}|g" \
  -e "s|__GPU_SSH_HOST__|${GPU_HOST}|g" \
  "${SCRIPT_DIR}/sere1nfish-gpu-tunnel.service.tpl" \
  >"${STAGING_HOST}/sere1nfish-gpu-tunnel.service"

temporary_tunnel_args=(
  ssh "${SSH_OPTS[@]}"
  -N -T
  -o "ExitOnForwardFailure=yes"
  -o "ServerAliveInterval=30"
  -o "ServerAliveCountMax=3"
  -R 127.0.0.1:18443:127.0.0.1:443
)
if [[ -n "${PROXY_TARGET}" ]]; then
  temporary_tunnel_args+=(-R "127.0.0.1:17890:${PROXY_TARGET}")
fi
temporary_tunnel_args+=("${SSH_TARGET}")
"${temporary_tunnel_args[@]}" &
TEMP_TUNNEL_PID=$!
sleep 2
if ! kill -0 "${TEMP_TUNNEL_PID}" 2>/dev/null; then
  wait "${TEMP_TUNNEL_PID}"
  echo "Unable to establish the temporary GPU deployment tunnel" >&2
  exit 1
fi

remote_exec install -d -m 0755 "${REMOTE_RELEASE}"
remote_exec install -d -m 0700 "${REMOTE_ROOT}/secrets"
tar -C "${SERVER_DIR}" \
  --exclude='__pycache__' --exclude='*.pyc' \
  -cf - deepfake_gateway meanvc_runtime \
  | remote_exec tar -xf - -C "${REMOTE_RELEASE}"

cat >"${STAGING_HOST}/.env" <<EOF
DEEPFAKE_PUBLIC_HOST=${PUBLIC_HOST}
DEEPFAKE_LEGACY_HOST=${PUBLIC_IP}
DEEPFAKE_WEBRTC_ADDITIONAL_HOSTS=${PUBLIC_IP}
DEEPFAKE_TUNNEL_HOST=${TUNNEL_HOST}
DEEPFAKE_ROOT=${REMOTE_ROOT}
DEEPFAKE_ASSETS_DIR=${REMOTE_ROOT}/data/assets
DEEPFAKE_CACHE_DIR=${REMOTE_ROOT}/data/cache
MEANVC_MODELS_DIR=${REMOTE_ROOT}/data/meanvc
DEEPFAKE_CADDY_DATA_DIR=${REMOTE_ROOT}/data/caddy
DEEPFAKE_CADDY_CONFIG_DIR=${REMOTE_ROOT}/data/caddy-config
DEEPFAKE_TOKEN_FILE=${REMOTE_ROOT}/secrets/api-token
MEANVC_TOKEN_FILE=${REMOTE_ROOT}/secrets/meanvc-token
DEEPFAKE_TLS_CERT_FILE=${REMOTE_ROOT}/secrets/tls.crt
DEEPFAKE_TLS_KEY_FILE=${REMOTE_ROOT}/secrets/tls.key
DEEPFAKE_FACEFUSION_CONFIG_FILE=${REMOTE_RELEASE}/deepfake_gateway/facefusion.ini
FACEFUSION_COMMIT=3f81a8a78454089d720b8f318a12ae1702c4633b
FACEFUSION_BASE_IMAGE=sere1nfish/facefusion:3.7.1-cuda12.8-py312
DEEPFAKE_GATEWAY_IMAGE=sere1nfish/deepfake-gateway:${RELEASE_ID}
MEANVC_IMAGE=sere1nfish/meanvc-runtime:1.0.0
MEDIAMTX_IMAGE=bluenviron/mediamtx:1.19.3
CADDY_IMAGE=caddy:2.10.2-alpine
HTTP_PROXY=${proxy_url}
HTTPS_PROXY=${proxy_url}
PIP_INDEX_URL=https://pypi.org/simple
PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
DEEPFAKE_MAX_SESSIONS=2
DEEPFAKE_SESSION_TTL_SECONDS=900
DEEPFAKE_DEFAULT_IMAGE_PROFILE=quality
DEEPFAKE_DEFAULT_REALTIME_PROFILE=fast
DEEPFAKE_MEDIA_OUTPUT_FPS=15
EOF
chmod 0600 "${STAGING_HOST}/.env"

scp "${SCP_OPTS[@]}" \
  "${STAGING_HOST}/.env" \
  "${STAGING_HOST}/api-token" \
  "${STAGING_HOST}/meanvc-token" \
  "${STAGING_HOST}/tls.crt" \
  "${STAGING_HOST}/tls.key" \
  "${STAGING_HOST}/ca.crt" \
  "${SSH_TARGET}:${REMOTE_ROOT}/secrets/"
remote_exec mv "${REMOTE_ROOT}/secrets/.env" "${REMOTE_RELEASE}/.env"
remote_exec chmod 0600 "${REMOTE_RELEASE}/.env"
remote_exec chmod 0600 \
  "${REMOTE_ROOT}/secrets/api-token" \
  "${REMOTE_ROOT}/secrets/meanvc-token" \
  "${REMOTE_ROOT}/secrets/tls.key"
remote_exec chmod 0644 \
  "${REMOTE_ROOT}/secrets/ca.crt" \
  "${REMOTE_ROOT}/secrets/tls.crt"

if ((RESTORE_MODELS)); then
  if docker compose version >/dev/null 2>&1; then
    main_compose=(docker compose)
  else
    main_compose=(docker-compose)
  fi
  bundle_download_args=(
    python -m scripts.manage_gpu_node bundle-download
    --manifest "${MANIFEST_CONTAINER}"
    --output-dir "${BUNDLE_CONTAINER}"
  )
  bundle_files=(
    "${BUNDLE_HOST}/facefusion-assets.tar.zst"
    "${BUNDLE_HOST}/meanvc-models.tar.zst"
  )
  if [[ "${GPU_COMPUTE_CAPABILITY}" == "7.5" ]]; then
    bundle_download_args+=(--include-optional)
    bundle_files+=("${BUNDLE_HOST}/tensorrt-sm75.tar.zst")
  fi
  "${main_compose[@]}" -f "${REPO_DIR}/docker-compose.yml" exec -T backend \
    "${bundle_download_args[@]}"
  remote_exec install -d -m 0700 "${REMOTE_ROOT}/bundle-staging/${BUNDLE_VERSION}"
  scp "${SCP_OPTS[@]}" \
    "${BUNDLE_HOST}/SHA256SUMS" \
    "${bundle_files[@]}" \
    "${SSH_TARGET}:${REMOTE_ROOT}/bundle-staging/${BUNDLE_VERSION}/"
fi

if ((!SKIP_BOOTSTRAP)); then
  bootstrap_args=(
    --sysctl-file "${REMOTE_RELEASE}/deepfake_gateway/sysctl-mediamtx.conf"
  )
  if [[ -n "${proxy_url}" ]]; then
    bootstrap_args+=(--proxy-url "${proxy_url}")
  fi
  remote_exec bash \
    "${REMOTE_RELEASE}/deepfake_gateway/deploy/bootstrap-host.sh" \
    "${bootstrap_args[@]}"
fi

remote_deploy_args=(
  --root-dir "${REMOTE_ROOT}"
  --release-dir "${REMOTE_RELEASE}"
  --env-file "${REMOTE_RELEASE}/.env"
)
if ((RESTORE_MODELS)); then
  remote_deploy_args+=(--bundle-dir "${REMOTE_ROOT}/bundle-staging/${BUNDLE_VERSION}")
fi
remote_exec bash \
  "${REMOTE_RELEASE}/deepfake_gateway/deploy/deploy-on-host.sh" \
  "${remote_deploy_args[@]}"

public_origin_ready=0
for _ in $(seq 1 24); do
  if remote_exec curl --fail --silent --max-time 5 \
    --resolve "${PUBLIC_HOST}:443:127.0.0.1" \
    "https://${PUBLIC_HOST}/health" >/dev/null; then
    public_origin_ready=1
    break
  fi
  sleep 5
done
if ((public_origin_ready == 0)); then
  echo "GPU HTTPS origin is not ready. Verify Caddy, DNS and certificate issuance." >&2
  exit 1
fi
if ! curl --fail --silent --max-time 10 --noproxy '*' \
  "https://${PUBLIC_HOST}/health" >/dev/null; then
  echo "Warning: the main server cannot reach public GPU HTTPS; verify the 443/tcp client allowlist before using OBS." >&2
fi

# Cut over only after the new runtime and public endpoint are healthy. The EXIT
# trap restores the previous tunnel units if any later verification fails.
CUTOVER_STARTED=1
kill "${TEMP_TUNNEL_PID}"
wait "${TEMP_TUNNEL_PID}" 2>/dev/null || true
TEMP_TUNNEL_PID=""
systemctl disable --now "${TUNNEL_UNITS[@]}" 2>/dev/null || true
install -m 0644 "${STAGING_HOST}/sere1nfish-gpu-tunnel.service" \
  /etc/systemd/system/sere1nfish-gpu-tunnel.service
systemctl daemon-reload
systemctl enable sere1nfish-gpu-tunnel.service
systemctl restart sere1nfish-gpu-tunnel.service

private_ready=0
for _ in $(seq 1 12); do
  if curl --fail --silent --noproxy '*' --cacert "${STAGING_HOST}/ca.crt" \
    "https://${TUNNEL_HOST}:18443/health" >/dev/null; then
    private_ready=1
    break
  fi
  sleep 2
done
if ((private_ready == 0)); then
  echo "The main-server private GPU tunnel is not ready" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  main_compose=(docker compose)
else
  main_compose=(docker-compose)
fi
"${main_compose[@]}" -f "${REPO_DIR}/docker-compose.yml" exec -T backend \
  python -m scripts.manage_gpu_node config-backup \
    --output "${STAGING_CONTAINER}/deepfake-config.previous.json"
CONFIG_UPDATED=1
"${main_compose[@]}" -f "${REPO_DIR}/docker-compose.yml" exec -T backend \
  python -m scripts.manage_gpu_node configure \
    --base-url "https://${TUNNEL_HOST}:18443" \
    --token-file "${STAGING_CONTAINER}/api-token" \
    --ca-file "${STAGING_CONTAINER}/ca.crt"
"${main_compose[@]}" -f "${REPO_DIR}/docker-compose.yml" exec -T backend \
  python -m scripts.manage_gpu_node status

DEPLOYMENT_COMPLETE=1
echo "GPU deployment complete"
echo "Public endpoint: https://${PUBLIC_HOST}"
echo "Main-server endpoint: https://${TUNNEL_HOST}:18443"
echo "GPU compute capability: ${GPU_COMPUTE_CAPABILITY}"
echo "Security group: 22/tcp from admin IP, 443/tcp, optional 8189/tcp+udp for WHIP"
